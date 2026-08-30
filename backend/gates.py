"""
Phase 5a — gates: several access points per location, and a direction on each.

WHAT THIS REPLACES
------------------
Until 5a a location had one nullable access point: the (gate_lat, gate_lon) pair on
`locations`, read by network._waypoint(). That models a site with exactly one gate
used in both directions. Real sites do not work that way — a quarry with a weighbridge
in and a separate exit onto the main road is the normal case, not the exception — and
the consequence is not cosmetic. `_bake_leg()` builds the return leg by swapping the
endpoints:

    a, b = (o, d) if leg == "loaded" else (d, o)

With one symmetric point per location that swap is right. With a one-way gate system it
is wrong in both directions at once: the return leg leaves the destination through its
entry gate and arrives at the origin through its exit. Making the swap direction-aware
is the expensive half of 5a, and it is why this module exists rather than two more
columns on `locations`.

THE RESOLUTION RULE, IN ONE PLACE
---------------------------------
`resolve(location, role, gate_id=None)` is the only thing that decides which coordinate
HERE is given, and every caller goes through network._waypoint(). The order is:

  1. the gate the route explicitly names for that end, IF it serves this role;
  2. otherwise that location's default active gate for the role;
  3. otherwise the lowest-id active gate for the role;
  4. otherwise the legacy (gate_lat, gate_lon) pair on the location;
  5. otherwise the node's own lat/lon.

Steps 4 and 5 are what make this change invisible on the day it ships: a database with
no gate rows resolves exactly as it did before, to the same coordinate, so no baked
geometry becomes stale and no cycle time moves.

⚠️ A route may name a gate whose direction does not cover the role being resolved — an
egress-only gate selected as the origin gate still has to answer "where does the laden
truck arrive to load?". Rather than error, the role it does not serve falls through to
step 2. The route diagnostic reports which gate was actually used at each end and
whether it came from the explicit choice, so this is visible rather than silent.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not compute cycle time. `safety_minutes` and `internal_travel_minutes` are
stored here and read by network._turnaround_parts(), which owns the B4 split and the
B5 precedence rule. Splitting the storage from the arithmetic is deliberate: the 125%
dashboard/backend disagreement recorded at main.py:588 came from two places computing
the same total, and there is exactly one place that computes it now.
"""
import re
from datetime import datetime, timezone

import db

#: A gate's direction of travel. 'access' is entry only, 'egress' exit only.
DIRECTIONS = ("access", "egress", "both")

#: The two roles a bake asks for. 'entry' is "the truck is arriving here", 'exit' is
#: "the truck is leaving here". Named differently from DIRECTIONS on purpose — the
#: question asked of a gate and the property recorded on it are not the same thing,
#: and collapsing the vocabularies is how the leg swap got confusing in the first place.
ROLES = ("entry", "exit")

_ROLE_OK = {
    "entry": ("access", "both"),
    "exit": ("egress", "both"),
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "t", "yes", "y")
    return bool(v)


def _clean_direction(v):
    d = (v or "both").strip().lower()
    return d if d in DIRECTIONS else "both"


def _clean_minutes(v):
    """Minutes, or None. Negative is rejected rather than clamped — a negative
    induction time is a typo, and silently turning it into 0 hides the typo."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f < 0 else f


def _coord(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _next_gate_id():
    # per tenant, for the same reason zones are: G-numbers are unique within a tenant,
    # and scanning every tenant's ids would let one client's gate count push the next
    # client's numbering
    ids = [r["id"] for r in db.query(
        "SELECT id FROM location_gates WHERE tenant_id = ?", (db.current_tenant(),))]
    n = 0
    for i in ids:
        m = re.match(r"^G(\d+)$", (i or "").strip())
        if m:
            n = max(n, int(m.group(1)))
    return f"G{n + 1:03d}"


def _row(g):
    return {
        "id": g["id"],
        "location_id": g["location_id"],
        "name": g.get("name"),
        "direction": _clean_direction(g.get("direction")),
        "lat": g["lat"], "lon": g["lon"],
        "safety_minutes": g.get("safety_minutes"),
        "internal_travel_minutes": g.get("internal_travel_minutes"),
        "is_default": _truthy(g.get("is_default")),
        "active": _truthy(g.get("active")),
        "note": g.get("note"),
        "created_at": g.get("created_at"),
        "updated_at": g.get("updated_at"),
    }


# --------------------------------------------------------------------------- #
#  Reads                                                                       #
# --------------------------------------------------------------------------- #
def list_gates(location_id=None, include_inactive=True):
    if location_id:
        rows = db.query(
            "SELECT * FROM location_gates WHERE tenant_id = ? AND location_id = ? "
            "ORDER BY id", (db.current_tenant(), location_id))
    else:
        rows = db.query("SELECT * FROM location_gates WHERE tenant_id = ? ORDER BY id",
                        (db.current_tenant(),))
    out = [_row(g) for g in rows]
    if not include_inactive:
        out = [g for g in out if g["active"]]
    return out


def get_gate(gate_id):
    rows = db.query("SELECT * FROM location_gates WHERE tenant_id = ? AND id = ?",
                    (db.current_tenant(), gate_id))
    if not rows:
        return {"error": "gate not found", "id": gate_id}
    return _row(rows[0])


def gates_by_location(include_inactive=True):
    """All gates grouped by location id — one query, for the bake loop.

    The batch bake resolves gates for up to 107 routes. Asking per route would be
    214 queries per run for data that fits in one.
    """
    out = {}
    for g in list_gates(include_inactive=include_inactive):
        out.setdefault(g["location_id"], []).append(g)
    return out


# --------------------------------------------------------------------------- #
#  Resolution — the only place that decides where HERE routes to                #
# --------------------------------------------------------------------------- #
def serves(gate, role):
    return _clean_direction(gate.get("direction")) in _ROLE_OK[role]


def resolve(location, role, gate_id=None, gates=None):
    """
    Which point HERE should be given for `location` when a truck is arriving
    (role='entry') or leaving (role='exit'), and where that point came from.

    Returns a dict: lat, lon, gate_id, gate_name, source, and `blocked` — the gate the
    route explicitly names when that gate is deactivated. `blocked` is not an error
    here; the caller decides. network.bake_route() refuses the bake and names it (B2),
    while the diagnostics report it and carry on, because a diagnostic that refuses to
    run is no use for working out why a bake refused.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    loc_id = location.get("id")
    if gates is None:
        # a caller that did not pre-fetch, on a database where location_gates may not
        # exist yet. Falling back to "no gates" is correct rather than lenient: with
        # none, resolve() returns the legacy pair and then the node — the exact
        # coordinate this system used before 5a — so a cold database routes as it did.
        try:
            gates = list_gates(loc_id) if loc_id else []
        except Exception:
            gates = []
    blocked = None

    chosen = None
    if gate_id:
        named = next((g for g in gates if g["id"] == gate_id), None)
        if named is None:
            # a route pointing at a gate that no longer exists. Same shape of problem
            # as a deactivated one and reported the same way, so a deleted gate cannot
            # quietly fall back to the node centre and re-bake to a different road.
            blocked = {"gate_id": gate_id, "reason": "missing"}
        elif not named["active"]:
            blocked = {"gate_id": gate_id, "name": named["name"], "reason": "inactive"}
        elif serves(named, role):
            chosen = named
        # a named gate that is active but does not serve this role falls through to the
        # default below, and `source` records that it did

    if chosen is None and blocked is None:
        usable = [g for g in gates if g["active"] and serves(g, role)]
        chosen = next((g for g in usable if g["is_default"]), None) \
            or (sorted(usable, key=lambda g: g["id"])[0] if usable else None)

    if chosen is not None:
        return {
            "lat": float(chosen["lat"]), "lon": float(chosen["lon"]),
            "gate_id": chosen["id"], "gate_name": chosen["name"],
            "safety_minutes": chosen.get("safety_minutes") or 0.0,
            "internal_travel_minutes": chosen.get("internal_travel_minutes") or 0.0,
            "source": "selected" if (gate_id and chosen["id"] == gate_id) else "default",
            "blocked": blocked,
        }

    # legacy pair, then the node itself. Both keep a pre-5a database routing to the
    # exact coordinate it routed to yesterday.
    g_lat, g_lon = location.get("gate_lat"), location.get("gate_lon")
    if g_lat is not None and g_lon is not None:
        return {"lat": float(g_lat), "lon": float(g_lon), "gate_id": None,
                "gate_name": None, "safety_minutes": 0.0,
                "internal_travel_minutes": 0.0, "source": "legacy_pair",
                "blocked": blocked}
    return {"lat": float(location["lat"]), "lon": float(location["lon"]),
            "gate_id": None, "gate_name": None, "safety_minutes": 0.0,
            "internal_travel_minutes": 0.0, "source": "node", "blocked": blocked}


def bake_blockers(route, origin, dest, gates_by_loc=None):
    """
    B2: the reasons this route cannot be baked, as sentences that name the gate.

    A refusal that does not say which gate is a silent failure with extra steps —
    Phase 4 learned that on promote_alternative(). Both ends and both roles are
    checked, because a gate deactivated at the destination breaks the loaded leg's
    arrival while leaving the return leg's departure fine, and the route list has to
    say which.
    """
    out = []
    gbl = gates_by_loc if gates_by_loc is not None else gates_by_location()
    for loc, gate_id, end in ((origin, route.get("origin_gate_id"), "origin"),
                              (dest, route.get("dest_gate_id"), "destination")):
        if not loc or not gate_id:
            continue
        r = resolve(loc, "entry", gate_id, gates=gbl.get(loc.get("id"), []))
        b = r.get("blocked")
        if not b:
            continue
        if b["reason"] == "inactive":
            out.append(f"{end} gate {b['gate_id']} ({b.get('name')}) at "
                       f"{loc.get('name') or loc.get('id')} is deactivated")
        else:
            out.append(f"{end} gate {b['gate_id']} selected at "
                       f"{loc.get('name') or loc.get('id')} no longer exists")
    return out


# --------------------------------------------------------------------------- #
#  Writes                                                                      #
# --------------------------------------------------------------------------- #
def create_gate(location_id, name, lat, lon, direction="both", safety_minutes=None,
                internal_travel_minutes=None, is_default=False, active=True,
                note=None, gate_id=None):
    if not (location_id or "").strip():
        return {"error": "location_id is required"}
    if not (name or "").strip():
        return {"error": "name is required"}
    if not db.query("SELECT id FROM locations WHERE tenant_id = ? AND id = ?",
                    (db.current_tenant(), location_id)):
        return {"error": f"location {location_id} not found"}
    la, lo = _coord(lat), _coord(lon)
    if la is None or lo is None:
        return {"error": "lat and lon are required and must be numbers"}
    gid = (gate_id or "").strip() or _next_gate_id()
    if db.query("SELECT id FROM location_gates WHERE tenant_id = ? AND id = ?",
                (db.current_tenant(), gid)):
        return {"error": f"gate {gid} already exists"}
    now = _now()
    db.execute(
        "INSERT INTO location_gates (tenant_id, id, location_id, name, direction, "
        "lat, lon, safety_minutes, internal_travel_minutes, is_default, active, note, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (db.current_tenant(), gid, location_id, name.strip(),
         _clean_direction(direction), la, lo, _clean_minutes(safety_minutes),
         _clean_minutes(internal_travel_minutes), bool(is_default), bool(active),
         (note or None), now, now),
    )
    if is_default:
        _clear_other_defaults(location_id, gid)
    return get_gate(gid)


def _clear_other_defaults(location_id, keep_id):
    """At most one default per location. Enforced here rather than by a partial unique
    index, because SQLite and Postgres spell that differently and the rule is cheap."""
    db.execute(
        "UPDATE location_gates SET is_default = ? WHERE tenant_id = ? "
        "AND location_id = ? AND id <> ?",
        (False, db.current_tenant(), location_id, keep_id),
    )


def update_gate(gate_id, **fields):
    """Partial update — only keys actually present are written, so a client that does
    not know about `note` cannot blank one by omitting it. Same trap the Locations PUT
    hit when a node drag started wiping salvaged vendor names."""
    cur = db.query("SELECT * FROM location_gates WHERE tenant_id = ? AND id = ?",
                   (db.current_tenant(), gate_id))
    if not cur:
        return {"error": "gate not found", "id": gate_id}
    cur = cur[0]
    sets, params = [], []
    if "name" in fields:
        if not (fields["name"] or "").strip():
            return {"error": "name cannot be empty"}
        sets.append("name = ?"); params.append(fields["name"].strip())
    if "direction" in fields:
        sets.append("direction = ?"); params.append(_clean_direction(fields["direction"]))
    for key in ("lat", "lon"):
        if key in fields:
            v = _coord(fields[key])
            if v is None:
                return {"error": f"{key} must be a number"}
            sets.append(f"{key} = ?"); params.append(v)
    for key in ("safety_minutes", "internal_travel_minutes"):
        if key in fields:
            sets.append(f"{key} = ?"); params.append(_clean_minutes(fields[key]))
    if "note" in fields:
        sets.append("note = ?"); params.append(fields["note"] or None)
    if "active" in fields:
        sets.append("active = ?"); params.append(bool(fields["active"]))
    if "is_default" in fields:
        sets.append("is_default = ?"); params.append(bool(fields["is_default"]))
    if not sets:
        return get_gate(gate_id)
    sets.append("updated_at = ?"); params.append(_now())
    # tenant_id constrains the WHERE and never appears in the SET — an edit must not be
    # able to move a row into another tenant. Built with .format rather than an f-string
    # so `UPDATE location_gates` and the tenant predicate stay in one string literal:
    # split across an f-string's fragments the write reads as unfiltered to the audit.
    params.append(db.current_tenant())
    params.append(gate_id)
    db.execute("UPDATE location_gates SET {} WHERE tenant_id = ? AND id = ?"
               .format(", ".join(sets)), tuple(params))
    if fields.get("is_default"):
        _clear_other_defaults(cur["location_id"], gate_id)
    return get_gate(gate_id)


def delete_gate(gate_id):
    """
    Deleting a gate a route still names would leave that route resolving to the node
    centre — a different road, a different baked line, and nothing on screen to say why.
    So the routes are reported and the delete is refused unless forced.
    """
    rows = db.query("SELECT * FROM location_gates WHERE tenant_id = ? AND id = ?",
                    (db.current_tenant(), gate_id))
    if not rows:
        return {"error": "gate not found", "id": gate_id}
    used = routes_using(gate_id)
    if used:
        return {"error": f"gate {gate_id} is selected by {len(used)} route(s)",
                "routes": used,
                "hint": "clear the gate on those routes first, or deactivate this gate "
                        "instead of deleting it — a deactivated gate keeps the history "
                        "and refuses the bake by name"}
    db.execute("DELETE FROM location_gates WHERE tenant_id = ? AND id = ?",
               (db.current_tenant(), gate_id))
    return {"deleted": gate_id}


def routes_using(gate_id):
    # the OR is parenthesised so the tenant filter binds to both halves rather than
    # only to the origin_gate_id branch — the Phase 4.5 lesson from routes_touching()
    return [r["id"] for r in db.query(
        "SELECT id FROM routes WHERE tenant_id = ? "
        "AND (origin_gate_id = ? OR dest_gate_id = ?)",
        (db.current_tenant(), gate_id, gate_id))]


def set_route_gates(route_id, origin_gate_id=None, dest_gate_id=None,
                    origin_given=False, dest_given=False):
    """
    Point a route at gates. Each end is only written when the caller says it was sent,
    so a PATCH that sets the destination gate cannot blank the origin one.

    Returns the route row plus `rebake_needed`, which is true whenever a written value
    actually changed. Moving a gate changes where HERE routes to, so the cached geometry
    is stale — the same contract zone and haul-road writes already have. The re-bake is
    NOT performed here; the cost is quoted in the UI first.
    """
    cur = db.query("SELECT * FROM routes WHERE tenant_id = ? AND id = ?",
                   (db.current_tenant(), route_id))
    if not cur:
        return {"error": "route not found", "id": route_id}
    cur = cur[0]
    sets, params, changed = [], [], False
    for key, val, given in (("origin_gate_id", origin_gate_id, origin_given),
                            ("dest_gate_id", dest_gate_id, dest_given)):
        if not given:
            continue
        v = (val or "").strip() or None
        if v and not db.query(
                "SELECT id FROM location_gates WHERE tenant_id = ? AND id = ?",
                (db.current_tenant(), v)):
            return {"error": f"gate {v} not found"}
        if v != cur.get(key):
            changed = True
        sets.append(f"{key} = ?"); params.append(v)
    if sets:
        params.append(db.current_tenant())
        params.append(route_id)
        db.execute("UPDATE routes SET {} WHERE tenant_id = ? AND id = ?"
                   .format(", ".join(sets)), tuple(params))
    row = db.query("SELECT * FROM routes WHERE tenant_id = ? AND id = ?",
                   (db.current_tenant(), route_id))[0]
    return {"route_id": route_id,
            "origin_gate_id": row.get("origin_gate_id"),
            "dest_gate_id": row.get("dest_gate_id"),
            "rebake_needed": changed}


# --------------------------------------------------------------------------- #
#  Migration of the pre-5a single gate                                         #
# --------------------------------------------------------------------------- #
def migrate_legacy_gates():
    """
    Turn each location's (gate_lat, gate_lon) pair into a real gate row.

    Idempotent, and deliberately conservative:

      * direction 'both', because that is exactly what the single pair meant. Nothing
        in the old data says which way traffic went, and guessing would change routing.
      * safety_minutes and internal_travel_minutes left NULL, so the B4 split
        reproduces today's turnaround_hr to the digit. That is the assertion the
        kickoff note insists on before any number moves.
      * the columns on `locations` are NOT dropped. They stay as resolution step 4, so
        a database where this migration has not run still routes correctly, and a
        rollback of 5a does not lose the surveyed coordinates.

    A location that already has a gate row is skipped — re-running this must not mint
    a second 'Main gate'.
    """
    made = []
    have = {g["location_id"] for g in list_gates()}
    for l in db.query("SELECT * FROM locations WHERE tenant_id = ? ORDER BY id",
                      (db.current_tenant(),)):
        if l["id"] in have:
            continue
        if l.get("gate_lat") is None or l.get("gate_lon") is None:
            continue
        res = create_gate(l["id"], "Main gate", l["gate_lat"], l["gate_lon"],
                          direction="both", is_default=True, active=True,
                          note="migrated from locations.gate_lat/gate_lon in Phase 5a")
        if not res.get("error"):
            made.append(res["id"])
    if made:
        print(f"Phase 5a: {len(made)} legacy gate(s) migrated: {', '.join(made)}")
    return made

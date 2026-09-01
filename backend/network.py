"""
Dynamic routing network for RBE Alliance 1 (Phase 0).

Seeds the V2 master (locations + route pairs) into the DB, bakes truck-legal
geometry for a vehicle profile via HERE (cached, route-on-change), and serves
the cached geometry as GeoJSON for the map.
"""
import os
import json
from datetime import datetime, timezone

import db
import conversions
import here_routing
import zones          # Phase 3 — supplies the avoid[areas] the bake path sends to HERE
import haul           # Phase 4 — threads temporary haul roads into the bake path
import gates          # Phase 5a — resolves which gate each leg enters and leaves by

DEFAULT_PROFILE = "Artic Tipper (44t)"
_SEED = os.path.join(os.path.dirname(__file__), "seed_data", "v2_network.json")

# map V2 raw materials -> our categories (locations carry raw V2 strings on first seed)
_V2_TO_CAT = {
    "sand": "Small aggregate", "gravel": "Small aggregate",
    "limestone - rockfill": "Large aggregate / ballast",
    "limestone (shale aggregate)": "Small aggregate",
    "imported goods": "General / imported",
}


def _cat(raw):
    return _V2_TO_CAT.get((raw or "").strip().lower())


_known_cats = None


def _categories():
    """Our material category names, from factors.json (cached)."""
    global _known_cats
    if _known_cats is None:
        try:
            _known_cats = set(conversions.material_names(conversions.load_factors()))
        except Exception:
            _known_cats = set()
    return _known_cats


def _to_cat(raw):
    """
    Normalise a material string to one of our categories. Accepts either a raw V2
    string ('Limestone - rockfill') or an already-correct category name, so it works
    on both seeded routes and routes authored in the UI. Returns None if neither.
    """
    s = (raw or "").strip()
    if not s:
        return None
    return _cat(s) or (s if s in _categories() else None)


_cols_ensured = False


def _ensure_location_columns():
    """
    Belt-and-braces: make sure the newer location columns exist before we write
    them. The canonical migration is in db.init_network_db() (runs on startup),
    but this guards against a live DB whose 'locations' table predates it, so the
    admin endpoints can't hard-500 on a missing column. Idempotent, runs once.
    """
    global _cols_ensured
    if _cols_ensured:
        return
    for col, typ in (("role", "TEXT"), ("materials", "TEXT"), ("supplies", "TEXT"),
                     ("receives", "TEXT"), ("gate_lat", "REAL"), ("gate_lon", "REAL")):
        try:
            if db.IS_PG:
                db.execute(f"ALTER TABLE locations ADD COLUMN IF NOT EXISTS {col} {typ}")
            else:
                db.execute(f"ALTER TABLE locations ADD COLUMN {col} {typ}")
        except Exception:
            pass  # already present (or table not created yet — init_network_db handles that)
    _cols_ensured = True


def _coord_or_none(v):
    """Parse an optional gate coordinate. Blank/absent means 'no gate recorded'."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if -180.0 <= f <= 180.0 else None


def _role_for(loc_type):
    t = (loc_type or "").strip().lower()
    if t in ("quarry", "port"):
        return "origin"
    if t in ("compound", "site"):
        return "destination"
    return "both"


def seed_network():
    """Load the V2 network into locations + routes if not already present."""
    if db.count_locations() > 0:
        return False
    net = json.load(open(_SEED, encoding="utf-8"))
    for l in net["locations"]:
        role = _role_for(l.get("loc_type"))
        mats = [_cat(l.get("material"))] if (role in ("origin", "both") and _cat(l.get("material"))) else []
        db.execute(
            # ON CONFLICT names (tenant_id, id) because that is the primary key since
            # Phase 4.5 — 'C01' is only unique within a tenant, and naming (id) alone
            # would no longer match any constraint.
            "INSERT INTO locations (tenant_id, id, name, loc_type, role, materials, lat, lon, material) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (tenant_id, id) DO NOTHING",
            (db.current_tenant(), l["id"], l["name"], l.get("loc_type"), role, json.dumps(mats),
             l["lat"], l["lon"], l.get("material")),
        )
    for r in net["routes"]:
        db.execute(
            # same reasoning as locations above: the key is (tenant_id, id)
            "INSERT INTO routes (tenant_id, id, origin_id, dest_id, long_route_id, material_category, ipt, origin_temp_km) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (tenant_id, id) DO NOTHING",
            (db.current_tenant(), r["id"], r["origin_id"], r["dest_id"], r.get("long_route_id"),
             r.get("material_category"), r.get("ipt"), r.get("origin_temp_km", 0)),
        )
    return True


def backfill_location_roles():
    """Give any location still missing a role one derived from its type/material."""
    for l in db.query("SELECT * FROM locations WHERE tenant_id = ?", (db.current_tenant(),)):
        if l.get("role"):
            continue
        role = _role_for(l.get("loc_type"))
        mats = [_cat(l.get("material"))] if (role in ("origin", "both") and _cat(l.get("material"))) else []
        db.execute("UPDATE locations SET role = ?, materials = ? WHERE tenant_id = ? AND id = ?",
                   (role, json.dumps(mats), db.current_tenant(), l["id"]))


def backfill_supplies_receives():
    """
    Populate locations.supplies / locations.receives for rows that have never had
    them set.

    The V2 seed only ever wrote 'materials' (and only for origins), which left
    'receives' NULL on every location. Since route validity is
    origin.supplies n dest.receives, an empty 'receives' meant no origin/destination
    pair was ever valid and no route could be authored in the UI.

    We derive the missing values from the route network that already exists: a
    location supplies whatever leaves it (plus its own seeded material) and
    receives whatever arrives at it. Fill-only-if-NULL, so anything edited in the
    Locations panel is never clobbered — including a deliberate empty list.
    """
    _ensure_location_columns()
    locs = db.query("SELECT * FROM locations WHERE tenant_id = ?", (db.current_tenant(),))
    if not locs:
        return {"filled": 0}

    sup, rec = {}, {}
    for l in locs:
        c = _to_cat(l.get("material"))
        if c:
            sup.setdefault(l["id"], set()).add(c)
    for r in db.query("SELECT origin_id, dest_id, material_category FROM routes "
                      "WHERE tenant_id = ?", (db.current_tenant(),)):
        c = _to_cat(r.get("material_category"))
        if not c:
            continue
        if r.get("origin_id"):
            sup.setdefault(r["origin_id"], set()).add(c)
        if r.get("dest_id"):
            rec.setdefault(r["dest_id"], set()).add(c)

    filled = 0
    for l in locs:
        lid = l["id"]
        need_s = l.get("supplies") is None
        need_r = l.get("receives") is None
        if not (need_s or need_r):
            continue
        # 'materials' mirrors 'supplies', matching create_location/update_location
        new_s = sorted(sup.get(lid, set()) | set(_loc_list(l, "materials"))) if need_s else None
        new_r = sorted(rec.get(lid, set())) if need_r else None
        db.execute(
            "UPDATE locations SET supplies = ?, receives = ?, materials = ? "
            "WHERE tenant_id = ? AND id = ?",
            (json.dumps(new_s) if need_s else l.get("supplies"),
             json.dumps(new_r) if need_r else l.get("receives"),
             json.dumps(new_s) if need_s else l.get("materials"),
             db.current_tenant(), lid),
        )
        filled += 1
    return {"filled": filled}


_NODE_META = os.path.join(os.path.dirname(__file__), "seed_data", "node_meta.json")


def apply_node_meta():
    """
    Write the vendor and material detail salvaged from a1_data.js onto locations.

    That static file was the public map's old route geometry and is being retired; 14 of
    its nodes carried a quarry operator that exists nowhere else in the system, and 13 of
    those resolve to a network location. See seed_data/_build_node_meta.py for how the
    match was made and why the work-section tags in the same file were NOT salvaged.

    Additive only: a row whose vendor is already set is left alone, so an edit made in
    the database survives a redeploy.
    """
    try:
        with open(_NODE_META, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return {"applied": 0, "skipped": 0, "note": "node_meta.json not present"}

    current = {r["id"]: r for r in db.query(
        "SELECT id, vendor, detail FROM locations WHERE tenant_id = ?", (db.current_tenant(),))}
    applied = skipped = 0
    for row in payload.get("matched", []):
        lid = row.get("location_id")
        cur = current.get(lid)
        if not cur:
            skipped += 1
            continue
        if cur.get("vendor") or cur.get("detail"):
            skipped += 1
            continue
        db.execute("UPDATE locations SET vendor = ?, detail = ? WHERE tenant_id = ? AND id = ?",
                   (row.get("vendor"), row.get("detail"), db.current_tenant(), lid))
        applied += 1
    return {"applied": applied, "skipped": skipped,
            "unmatched": len(payload.get("unmatched", []))}


# --------------------------------------------------------------------------- #
#  Baking (Phase 1-tail Step B: directed legs x HERE alternatives)              #
# --------------------------------------------------------------------------- #
# A route is now baked as two directed legs. 'loaded' runs origin -> destination at
# laden gross weight; 'return' runs destination -> origin at tare weight, which can
# legally use roads the laden truck may not. Each leg keeps up to ALTERNATIVES options
# from HERE, ranked best-first (alt_index 0 is the one the map draws).
LEGS = ("loaded", "return")
ALTERNATIVES = 3


def _waypoint_full(loc, role="entry", gate_id=None, gate_list=None):
    """
    Which gate answers for this location in one DIRECTION, and why.

    Phase 5a made the access point per-leg and asymmetric. Before it a location had one
    access point used both ways; now it may have several with a direction on each, so
    the question is no longer "where is this site" but "where does a truck ARRIVE here
    (role='entry') / LEAVE here (role='exit')".

    ⚠️ This is the change `_bake_leg` had to absorb. It builds the return leg by
    swapping the endpoints, which is right for one symmetric point per location and
    wrong the moment a gate is one-way: the swap on its own sends the empty truck back
    out through the entry gate. The swap still happens — what flips with it is which
    ROLE each end is asked for.

    The resolution order lives in gates.resolve(), and its last two steps are the legacy
    (gate_lat, gate_lon) pair and the node's own lat/lon. A database with no gate rows
    therefore resolves to exactly the coordinate this returned before 5a, which is what
    keeps every already-baked leg valid on the day this ships.

    `gate_list` is the pre-fetched gate list for this location. The batch bake resolves
    two ends for up to 107 routes; passing it in keeps that one query rather than 214.
    """
    return gates.resolve(loc, role, gate_id, gates=gate_list)


def _waypoint(loc, role="entry", gate_id=None, gate_list=None):
    """The coordinate only. Most call sites want the pair and nothing else."""
    r = _waypoint_full(loc, role, gate_id, gate_list)
    return r["lat"], r["lon"]


def _upsert_geom(route_id, profile, geometry, dist, dur, error, leg="loaded", alt_index=0,
                 zones_applied=None, haul_zones=None, haul_km=None, duration_hr_here=None):
    """
    Cache one baked geometry.

    zones_applied records which zone ids were sent to HERE as avoid[areas] for this
    bake — '' when none were in force, NULL when the row predates Phase 3. That
    distinction is what lets zones.invalidate() be exact about "this leg was routed
    around zone Z" instead of guessing from proximity. Written on every upsert,
    including the error rows, so a failed bake does not leave a stale attribution
    behind from the run before it.

    Phase 4 adds the same idea for haul roads: haul_zones names the roads threaded into
    this leg, and duration_hr_here keeps HERE's own timing next to the adjusted figure in
    duration_hr. Without the raw column an assigned speed would be indistinguishable from
    a road that is genuinely quick, which is exactly the kind of number this codebase has
    been burned by before.
    """
    now = datetime.now(timezone.utc).isoformat()
    # The ON CONFLICT target below has to name the whole primary key, which since
    # Phase 4.5 leads with tenant_id. The DO UPDATE SET deliberately does not touch
    # tenant_id: an upsert re-caches a row, it never moves one between tenants.
    db.execute(
        """
        INSERT INTO route_geometry
            (tenant_id, route_id, vehicle_profile, leg, alt_index, geometry, distance_km,
             duration_hr, computed_at, error, zones_applied, haul_zones, haul_km,
             duration_hr_here)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (tenant_id, route_id, vehicle_profile, leg, alt_index) DO UPDATE SET
            geometry = EXCLUDED.geometry,
            distance_km = EXCLUDED.distance_km,
            duration_hr = EXCLUDED.duration_hr,
            computed_at = EXCLUDED.computed_at,
            error = EXCLUDED.error,
            zones_applied = EXCLUDED.zones_applied,
            haul_zones = EXCLUDED.haul_zones,
            haul_km = EXCLUDED.haul_km,
            duration_hr_here = EXCLUDED.duration_hr_here
        """,
        (db.current_tenant(), route_id, profile, leg, alt_index, geometry, dist, dur,
         now, error, zones_applied, haul_zones, haul_km, duration_hr_here),
    )


def clear_geometry(profile=None, leg=None):
    """Delete cached geometry so it can be recomputed. All profiles if profile is None."""
    clauses, params = [], []
    if profile:
        clauses.append("vehicle_profile = ?"); params.append(profile)
    if leg:
        clauses.append("leg = ?"); params.append(leg)
    # tenant_id is baked into the base statement rather than appended as one more
    # optional clause, so the branch where neither profile nor leg is given cannot
    # produce an unfiltered DELETE. Its param leads the tuple for the same reason.
    sql = "DELETE FROM route_geometry WHERE tenant_id = ?"
    if clauses:
        sql += " AND " + " AND ".join(clauses)
    db.execute(sql, (db.current_tenant(),) + tuple(params))
    return True


def active_avoid():
    """
    (avoid_area_strings, zone_id_tag) for the zones in force right now.

    Resolved once per bake run rather than per leg: it costs two queries, and a run that
    re-read it mid-batch could apply different avoid areas to different routes in the
    same batch and leave the network internally inconsistent.

    Returns '' rather than None for the tag when no zones apply — that is a positive
    record of "baked with nothing avoided", which is not the same as the NULL a
    pre-Phase-3 row carries.
    """
    try:
        zs = zones.routing_zones()
    except Exception:
        return [], ""      # zones table may not exist yet on a cold database
    areas, ids = [], []
    for z in zs:
        bb = zones.bbox_of(z["geometry"])
        if bb:
            areas.append(zones._avoid_str(bb))
            ids.append(z["id"])
    return areas, ",".join(sorted(ids))


def _bake_leg(r, o, d, profile, leg, factors, alternatives=ALTERNATIVES,
              avoid=None, zone_tag=None, gates_by_loc=None):
    """
    Route one direction of one route and cache every alternative HERE offers.

    Returns (n_stored, error_or_None). One HTTP call covers all alternatives. Stale
    higher alt_index rows are cleared first, so a re-bake that comes back with fewer
    options doesn't leave orphans behind from the previous run.

    `avoid` is the Phase 3 zone list as HERE 'bbox:w,s,e,n' strings, and `zone_tag` the
    matching zone ids stamped onto every row written. Callers that pass neither get
    today's zones resolved for them — but a batch should resolve once and pass them in,
    so every leg in the run is baked against the same set.

    ⚠️ Phase 5a: the endpoint swap below is no longer sufficient on its own. `a` and `b`
    still swap for the return leg, but each end is now asked for a different ROLE and a
    different route-selected gate, so a one-way gate system routes out of the exit and
    into the entry in both directions. Getting this wrong is invisible in the numbers —
    the route still bakes, it just uses the wrong side of the site.
    """
    if avoid is None and zone_tag is None:
        avoid, zone_tag = active_avoid()
    o_gate, d_gate = r.get("origin_gate_id"), r.get("dest_gate_id")
    if leg == "loaded":
        a, a_role, a_gate = o, "exit", o_gate      # leaves the origin
        b, b_role, b_gate = d, "entry", d_gate     # arrives at the destination
    else:
        a, a_role, a_gate = d, "exit", d_gate      # leaves the destination
        b, b_role, b_gate = o, "entry", o_gate     # arrives back at the origin
    gbl = gates_by_loc if gates_by_loc is not None else {}
    a_lat, a_lon = _waypoint(a, a_role, a_gate, gbl.get(a.get("id")))
    b_lat, b_lon = _waypoint(b, b_role, b_gate, gbl.get(b.get("id")))

    # Phase 4: which haul roads this leg runs through, in traversal order. Empty for
    # every route nobody has attached one to, and route_with_haul() then reduces to the
    # plain here_routing.routes() call this used to be — so a network with no haul roads
    # behaves exactly as it did before, same call, same cost.
    try:
        steps = haul.plan_leg(r["id"], [a_lon, a_lat], [b_lon, b_lat], leg)
    except Exception:
        steps = []          # route_haul_roads may not exist yet on a cold database
    haul_tag = ",".join([s["zone_id"] for s in steps])

    try:
        opts = haul.route_with_haul(a_lat, a_lon, b_lat, b_lon, profile, steps,
                                    factors=factors, avoid_areas=(avoid or None),
                                    laden=(leg == "loaded"), alternatives=alternatives)
    except Exception as e:
        _upsert_geom(r["id"], profile, None, None, None, str(e)[:300], leg=leg, alt_index=0,
                     zones_applied=zone_tag, haul_zones=haul_tag)
        return 0, str(e)
    db.execute(
        "DELETE FROM route_geometry WHERE tenant_id = ? AND route_id = ? "
        "AND vehicle_profile = ? AND leg = ? AND alt_index >= ?",
        (db.current_tenant(), r["id"], profile, leg, len(opts)),
    )
    for i, res in enumerate(opts):
        _upsert_geom(r["id"], profile, json.dumps(res["geometry"]),
                     res["distance_km"], res["duration_hr"], None, leg=leg, alt_index=i,
                     zones_applied=zone_tag, haul_zones=haul_tag,
                     haul_km=res.get("haul_km"),
                     # None on a leg with no haul road: there was no substitution, so
                     # there is no separate 'before' figure and duration_hr is HERE's own
                     duration_hr_here=res.get("duration_hr_here"))
    return len(opts), None


def bake_batch(profile=DEFAULT_PROFILE, limit=25, legs=LEGS, alternatives=ALTERNATIVES):
    """
    Route up to `limit` not-yet-attempted (route, leg) pairs for a profile and cache
    the geometry. Idempotent — call repeatedly until 'remaining' is 0. To recompute
    existing routes, clear_geometry() first.

    `limit` counts legs, not routes. A full network of 107 routes over both legs is 214
    legs. ⚠️ Since Phase 4 a leg is no longer necessarily ONE HERE call: a leg spliced
    through N haul roads costs N+1, because each drawn stretch breaks the HERE request in
    two. The batch reports `here_calls` alongside `baked` for that reason — on a network
    with no haul roads the two still match, as they always did.

    Every leg in one call is baked against the same set of zone avoid-areas, resolved
    once at the top. Across repeated calls it can change — someone can draw a zone
    halfway through a bake — so the applied set is stamped on each row and reported back
    as `zones_applied`, rather than assumed uniform across the network.
    """
    if not here_routing.configured():
        return {"error": "HERE_API_KEY not set on the server", "baked": 0, "remaining": None}

    all_routes = db.query("SELECT * FROM routes WHERE tenant_id = ? ORDER BY id",
                          (db.current_tenant(),))
    attempted = {(g["route_id"], g["leg"]) for g in db.query(
        "SELECT route_id, leg FROM route_geometry WHERE tenant_id = ? AND vehicle_profile = ?",
        (db.current_tenant(), profile))}
    todo = [(r, leg) for r in all_routes for leg in legs
            if (r["id"], leg) not in attempted]

    locs = {l["id"]: l for l in db.query("SELECT * FROM locations WHERE tenant_id = ?",
                                        (db.current_tenant(),))}
    factors = conversions.load_factors()
    avoid, zone_tag = active_avoid()
    # Phase 5a: one query for every gate in the tenant, resolved per leg below. Asking
    # per route would be 214 queries a run for data that fits in one.
    try:
        gates_by_loc = gates.gates_by_location()
    except Exception:
        gates_by_loc = {}   # location_gates may not exist yet on a cold database
    baked = errors = 0
    haul_legs = here_calls = refused = 0
    samples = []
    blocked_routes = []
    for r, leg in todo[:limit]:
        o = locs.get(r["origin_id"]); d = locs.get(r["dest_id"])
        if not o or not d:
            _upsert_geom(r["id"], profile, None, None, None,
                         "missing origin/destination coordinates", leg=leg, alt_index=0,
                         zones_applied=zone_tag)
            errors += 1
            continue
        # B2: a route whose selected gate has been deactivated is refused, and the
        # refusal names the gate. Written into route_geometry.error rather than merely
        # skipped, so the route list shows it as unbakeable with a reason instead of
        # looking like it was simply never reached in the batch.
        blockers = gates.bake_blockers(r, o, d, gates_by_loc)
        if blockers:
            msg = "gate unavailable: " + "; ".join(blockers)
            _upsert_geom(r["id"], profile, None, None, None, msg[:300], leg=leg,
                         alt_index=0, zones_applied=zone_tag)
            refused += 1
            if r["id"] not in blocked_routes:
                blocked_routes.append(r["id"])
            if len(samples) < 5:
                samples.append(f"{r['id']}/{leg}: {msg[:100]}")
            continue
        here_calls += haul._here_calls_for([(r["id"], profile, leg)])
        n, err = _bake_leg(r, o, d, profile, leg, factors, alternatives,
                           avoid=avoid, zone_tag=zone_tag, gates_by_loc=gates_by_loc)
        if err:
            errors += 1
            if len(samples) < 5:
                samples.append(f"{r['id']}/{leg}: {err[:100]}")
        else:
            baked += 1
            row = db.query("SELECT haul_zones FROM route_geometry WHERE tenant_id = ? AND "
                           "route_id = ? AND vehicle_profile = ? AND leg = ? AND alt_index = 0",
                           (db.current_tenant(), r["id"], profile, leg))
            if row and (row[0].get("haul_zones") or ""):
                haul_legs += 1
    remaining = max(0, len(todo) - min(limit, len(todo)))
    return {"profile": profile, "baked": baked, "errors": errors,
            "remaining": remaining, "legs": list(legs), "error_samples": samples,
            "zones_applied": [z for z in zone_tag.split(",") if z],
            "avoid_areas": avoid,
            # Phase 5a (B2): legs not attempted because a selected gate is deactivated
            # or gone. Counted apart from `errors` — a refusal is a state someone can
            # fix in the UI, a HERE failure is not, and lumping them together loses that.
            "gate_refused": refused,
            "gate_blocked_routes": blocked_routes,
            # Phase 4: `limit` still counts LEGS, but a leg is no longer one HERE call.
            # A spliced haul road costs one extra call per road on that leg, so this
            # batch may have spent more requests than it baked legs. Reported rather
            # than left for someone to discover in the HERE bill.
            "haul_legs": haul_legs,
            "here_calls": here_calls}


def routes_geojson(profile=DEFAULT_PROFILE, leg="loaded", alt_index=0):
    """
    Cached routes for a profile as a GeoJSON FeatureCollection (map source).

    A route now holds up to six geometries per profile (2 legs x 3 alternatives), so the
    map has to be told which one to draw or it would stack them all on top of each
    other. Default is the laden first-choice route, which is the one the network is
    planned around.
    """
    locs = {l["id"]: l for l in db.query("SELECT * FROM locations WHERE tenant_id = ?",
                                        (db.current_tenant(),))}
    geoms = {g["route_id"]: g for g in db.query(
        "SELECT * FROM route_geometry WHERE tenant_id = ? AND vehicle_profile = ? AND leg = ? "
        "AND alt_index = ?", (db.current_tenant(), profile, leg, alt_index))}
    feats = []
    for r in db.query("SELECT * FROM routes WHERE tenant_id = ? ORDER BY id",
                      (db.current_tenant(),)):
        g = geoms.get(r["id"])
        o = locs.get(r["origin_id"], {}); d = locs.get(r["dest_id"], {})
        props = {
            "route_id": r["id"], "long_route_id": r["long_route_id"],
            "origin": o.get("name"), "dest": d.get("name"),
            "origin_id": r["origin_id"], "dest_id": r["dest_id"],
            "material_category": r["material_category"], "ipt": r["ipt"],
            "vehicle_profile": profile, "leg": leg, "alt_index": alt_index,
        }
        if g and g["geometry"]:
            props.update({"distance_km": g["distance_km"], "duration_hr": g["duration_hr"],
                          "failed": False})
            feats.append({"type": "Feature", "properties": props,
                          "geometry": {"type": "LineString",
                                       "coordinates": json.loads(g["geometry"])}})
        elif g and g["error"] and o and d:
            # HERE couldn't route this pair. Previously the route simply vanished from
            # the map, which looks exactly like 'not baked yet' — so a genuinely
            # impossible haul was invisible. Emit a straight origin-to-destination line
            # flagged failed, for the frontend to draw dashed.
            props.update({"distance_km": None, "duration_hr": None,
                          "failed": True, "error": g["error"]})
            # drawn in this leg's own direction, so the failed line starts and ends at
            # the gates the bake would actually have used rather than at a symmetric
            # pair that may be on the wrong side of both sites
            if leg == "loaded":
                a_lat, a_lon = _waypoint(o, "exit", r.get("origin_gate_id"))
                b_lat, b_lon = _waypoint(d, "entry", r.get("dest_gate_id"))
            else:
                a_lat, a_lon = _waypoint(d, "exit", r.get("dest_gate_id"))
                b_lat, b_lon = _waypoint(o, "entry", r.get("origin_gate_id"))
            feats.append({"type": "Feature", "properties": props,
                          "geometry": {"type": "LineString",
                                       "coordinates": [[a_lon, a_lat], [b_lon, b_lat]]}})
    return {"type": "FeatureCollection", "features": feats}


#: what the public map calls each direction. Its layer ids and toggles predate the
#: network's loaded/return vocabulary, so the mapping is done here rather than renaming
#: half the map's controls.
_PUBLIC_LEG_TYPE = {"loaded": "Inbound Highway", "return": "Outbound Highway"}


def public_map_data(profile=None):
    """
    One FeatureCollection for the public map: route lines plus location markers.

    This replaces map/data/a1_data.js, a 4 MB static file keyed to the 69 legacy route
    ids. Measured overlap between those ids and the routing network was 0, so once
    forecasts were re-authored against network routes the map would have painted nothing.

    Vehicle choice is "whichever is baked, labelled": the default profile where it has
    geometry, otherwise any profile that does, with the vehicle named in vehicle_profile
    and in the popup. Different vehicles genuinely route differently, so a map that
    silently mixed them without saying so would be misleading; naming it is the honest
    version of showing as much of the network as possible mid-bake.

    Both legs are emitted. The map's layer ids and toggles are older than the network's
    loaded/return naming, so they are mapped to 'Inbound Highway' / 'Outbound Highway'.

    Temp Haul Track output returned in Phase 4 and is a real drawn polyline: the haul
    road as somebody clicked it onto the map, not a straight line of origin_temp_km
    length. It is emitted as type 'Temp Haul Track' features carrying the assigned speed,
    the drawn length and the routes attached, which is what re-enables the map sidebar
    toggle that has been disabled and labelled '(Phase 4)' since Phase 2 step 2.

    The 97 legacy haul segments went with a1_data.js and are not recoverable — these are
    only the roads drawn since.
    """
    factors = conversions.load_factors()
    plan = factors.get("planning", {}) or {}
    shift_hr = float(plan.get("shift_hours_per_day") or 10)

    locs = {l["id"]: l for l in db.query("SELECT * FROM locations WHERE tenant_id = ?",
                                        (db.current_tenant(),))}
    try:
        map_gates = gates.gates_by_location()
    except Exception:
        map_gates = {}   # location_gates may not exist yet on a cold database

    # every primary-option geometry, in one query rather than per route
    geo = {}
    for g in db.query("SELECT * FROM route_geometry WHERE tenant_id = ? AND alt_index = 0",
                      (db.current_tenant(),)):
        geo.setdefault(g["route_id"], {}).setdefault(g["vehicle_profile"], {})[g["leg"]] = g

    # disciplines actually forecast on each route, for the map's discipline filter.
    # A route can carry several -- that is the point of the widened key -- so this is a
    # list per route and the map filters with an `in` expression, not an equality.
    disc = {}
    try:
        for r in db.query("SELECT DISTINCT route_id, discipline FROM forecasts "
                          "WHERE tenant_id = ? AND status = 'Approved' AND discipline <> ''",
                          (db.current_tenant(),)):
            disc.setdefault(r["route_id"], []).append(r["discipline"])
    except Exception:
        pass   # forecasts table may not exist yet on a cold database

    feats = []
    for r in db.query("SELECT * FROM routes WHERE tenant_id = ? ORDER BY id",
                      (db.current_tenant(),)):
        per_profile = geo.get(r["id"], {})
        if not per_profile:
            continue                      # not baked: nothing honest to draw
        want = profile or DEFAULT_PROFILE
        used = want if want in per_profile else sorted(per_profile)[0]
        legs = per_profile[used]

        o = locs.get(r["origin_id"], {}) or {}
        d = locs.get(r["dest_id"], {}) or {}

        loaded, ret = legs.get("loaded"), legs.get("return")
        load_m, unload_m = _turnaround_minutes(used, factors)
        cycle_hr = trips = None
        if loaded and loaded["duration_hr"]:
            out_hr = loaded["duration_hr"]
            back_hr = (ret["duration_hr"] if ret and ret["duration_hr"] else out_hr)
            # the public map's cycle must be the SAME total route_analysis() computes,
            # from the same function. Two places adding up the same components is the
            # exact shape of the 125% disagreement recorded at main.py:588.
            has_drawn_road = bool((loaded.get("haul_zones") or "")
                                  or (ret is not None and (ret.get("haul_zones") or "")))
            cycle_hr = out_hr + back_hr + _turnaround_parts(
                used, factors, origin=o, dest=d, route=r,
                has_drawn_road=has_drawn_road,
                gates_by_loc=map_gates)["turnaround_hr"]
            trips = int(shift_hr // cycle_hr) if cycle_hr > 0 else 0

        for leg_name, g in (("loaded", loaded), ("return", ret)):
            if not g or not g["geometry"]:
                continue
            feats.append({
                "type": "Feature",
                "properties": {
                    "route_id": r["id"],
                    "type": _PUBLIC_LEG_TYPE[leg_name],
                    "leg": leg_name,
                    "origin": o.get("name"), "dest": d.get("name"),
                    "origin_id": r["origin_id"], "dest_id": r["dest_id"],
                    "ipt": r["ipt"] or "",
                    "material_category": r["material_category"] or "",
                    "vehicle_profile": used,
                    "profile_is_fallback": used != want,
                    "baked_profiles": sorted(per_profile),
                    "distance_km": g["distance_km"],
                    "duration_hr": g["duration_hr"],
                    "cycle_hr": round(cycle_hr, 2) if cycle_hr else None,
                    "trips_per_day": trips,
                    "disciplines": sorted(disc.get(r["id"], [])),
                },
                "geometry": {"type": "LineString",
                             "coordinates": json.loads(g["geometry"])},
            })

    # Phase 4: temporary haul roads, drawn as themselves. Only those in force and
    # affecting routing — a haul road that is inactive, out of date range or flagged
    # advisory is not carrying trucks, and a public map that drew it anyway would say a
    # road is open when it is not.
    try:
        counts = haul.link_counts()
        for z in zones.haul_roads():
            line = zones.as_line(z["geometry"])
            if not line:
                continue
            feats.append({
                "type": "Feature",
                "properties": {
                    "type": "Temp Haul Track",
                    "zone_id": z["id"], "name": z["name"],
                    "speed_kph": z["speed_kph"],
                    "length_km": z["length_km"],
                    "routes_attached": counts.get(z["id"], 0),
                    "starts_on": z["starts_on"], "ends_on": z["ends_on"],
                    "note": z["note"] or "",
                },
                "geometry": {"type": "LineString", "coordinates": line},
            })
    except Exception:
        pass   # zones/route_haul_roads may not exist yet on a cold database

    # location markers. aux_info keeps the shape the map's popup already expects, and is
    # now built from the vendor/detail salvaged out of a1_data.js before it was retired.
    for l in db.query("SELECT * FROM locations WHERE tenant_id = ? ORDER BY id",
                      (db.current_tenant(),)):
        aux = ""
        if l.get("vendor"):
            aux += f"<p style='margin:4px 0;'><b>Vendor:</b> {l['vendor']}</p>"
        if l.get("detail"):
            aux += f"<p style='margin:4px 0;'><b>Detail:</b> {l['detail']}</p>"
        # 🔴 The marker is the SITE, at the site's own coordinate — never a gate.
        #
        # This used to call _waypoint(), which resolves to the access point. That was
        # invisible while almost no location had a gate recorded, and wrong the moment
        # one did: creating a gate visibly moved the quarry on the public map. A site
        # marker that walks to wherever someone last clicked a gate is not a site
        # marker. Gates are their own features below, drawn as their own layer.
        #
        # Consequence, and it is correct rather than a defect: a route line now starts
        # at the gate dot and not at the site marker, because that is where the truck
        # actually joins the road. The two being in different places is the information.
        lat, lon = float(l["lat"]), float(l["lon"])
        feats.append({
            "type": "Feature",
            "properties": {
                "type": "Node",
                "id": l["id"],
                # the map disambiguates nothing itself, and C01/C02 are both called
                # 'Parnu terminal', so the id rides along in the label
                "name": l["name"],
                "node_type": l.get("loc_type") or "Other",
                "material": l.get("material") or "",   # popup calls .toLowerCase() on this
                "ipt": "",
                "vendor": l.get("vendor") or "",
                "aux_info": aux,
            },
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        })

    # Phase 5a — gates as their own features, so the map can draw the access points
    # separately from the sites they belong to. Reference geometry: nothing here
    # changes routing, and a map with the gate layer switched off is the map as it was.
    #
    # Deactivated gates are emitted too, flagged rather than dropped. A gate that has
    # been turned off is why a route refuses to bake, and a planner looking at the map
    # to work out why needs to see it — an absent dot explains nothing.
    try:
        for g in gates.list_gates():
            loc = locs.get(g["location_id"], {}) or {}
            feats.append({
                "type": "Feature",
                "properties": {
                    "type": "Gate",
                    "gate_id": g["id"],
                    "name": g["name"] or g["id"],
                    "location_id": g["location_id"],
                    # NOT called 'origin' or 'dest': populateFilters() on the map sweeps
                    # every feature for those two keys to build its dropdowns, and a gate
                    # is not a routable endpoint
                    "location_name": loc.get("name") or g["location_id"],
                    "direction": g["direction"],
                    "active": bool(g["active"]),
                    "is_default": bool(g["is_default"]),
                    "safety_minutes": g.get("safety_minutes"),
                    "internal_travel_minutes": g.get("internal_travel_minutes"),
                },
                "geometry": {"type": "Point",
                             "coordinates": [float(g["lon"]), float(g["lat"])]},
            })
    except Exception:
        pass   # location_gates may not exist yet on a cold database

    return {"type": "FeatureCollection", "features": feats}


def locations_geojson():
    def _parse(s):
        try:
            return json.loads(s or "[]")
        except Exception:
            return []

    feats = []
    for l in db.query("SELECT * FROM locations WHERE tenant_id = ? ORDER BY id",
                      (db.current_tenant(),)):
        mats = _parse(l.get("materials"))
        supplies = _parse(l.get("supplies")) or mats     # older rows only have materials
        receives = _parse(l.get("receives"))
        feats.append({
            "type": "Feature",
            "properties": {"id": l["id"], "name": l["name"], "loc_type": l["loc_type"],
                           "role": l.get("role") or "both", "materials": mats,
                           "supplies": supplies, "receives": receives,
                           "lat": l["lat"], "lon": l["lon"],
                           # null until an access gate is surveyed; routing falls back
                           # to lat/lon and the panel shows the field empty
                           "gate_lat": l.get("gate_lat"), "gate_lon": l.get("gate_lon")},
            "geometry": {"type": "Point", "coordinates": [l["lon"], l["lat"]]},
        })
    return {"type": "FeatureCollection", "features": feats}


def routes_touching(location_id):
    # the OR is parenthesised so the tenant filter binds to both halves rather than
    # only to the origin_id branch
    return [r["id"] for r in db.query(
        "SELECT id FROM routes WHERE tenant_id = ? AND (origin_id = ? OR dest_id = ?)",
        (db.current_tenant(), location_id, location_id))]


def profiles_for_route(route_id):
    """Vehicle profiles this route currently has cached geometry for."""
    return sorted({g["vehicle_profile"] for g in db.query(
        "SELECT DISTINCT vehicle_profile FROM route_geometry WHERE tenant_id = ? AND route_id = ?",
        (db.current_tenant(), route_id))})


def _next_location_id():
    existing = {l["id"] for l in db.query("SELECT id FROM locations WHERE tenant_id = ?",
                                         (db.current_tenant(),))}
    i = 1
    while f"L{i:03d}" in existing:
        i += 1
    return f"L{i:03d}"


def create_location(name, role, materials=None, lat=None, lon=None, loc_type=None,
                    supplies=None, receives=None, gate_lat=None, gate_lon=None,
                    vendor=None, detail=None):
    _ensure_location_columns()
    lid = _next_location_id()
    supplies = supplies if supplies is not None else (materials or [])
    receives = receives or []
    g_lat, g_lon = _coord_or_none(gate_lat), _coord_or_none(gate_lon)
    if g_lat is None or g_lon is None:      # a gate needs both halves to mean anything
        g_lat = g_lon = None
    # 'materials' mirrors 'supplies' so the existing map popup keeps working.
    db.execute(
        "INSERT INTO locations (tenant_id, id, name, loc_type, role, materials, supplies, receives, "
        "lat, lon, gate_lat, gate_lon, vendor, detail, material) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (db.current_tenant(), lid, name, loc_type or role, role, json.dumps(supplies),
         json.dumps(supplies),
         json.dumps(receives), float(lat), float(lon), g_lat, g_lon,
         (vendor or None), (detail or None), None),
    )
    return {"id": lid}


def update_location(location_id, name=None, role=None, materials=None, lat=None, lon=None,
                    loc_type=None, supplies=None, receives=None,
                    gate_lat=None, gate_lon=None, gate_given=False,
                    vendor=None, detail=None, meta_given=False):
    """
    Update a location. Moving it — or moving/clearing its gate — invalidates the cached
    geometry of every route that touches it, because the coordinate HERE routed to has
    changed.

    `gate_given` distinguishes "the caller didn't mention the gate" (keep whatever is
    stored) from "the caller cleared the gate" (both halves blank). Without it there is
    no way to remove a gate once set. `meta_given` does the same job for the operator
    and detail fields — an older client that does not send them must not blank them.
    """
    _ensure_location_columns()
    cur = db.query("SELECT * FROM locations WHERE tenant_id = ? AND id = ?",
                   (db.current_tenant(), location_id))
    if not cur:
        return {"error": "not found"}
    cur = cur[0]
    moved = (lat is not None and float(lat) != cur["lat"]) or (lon is not None and float(lon) != cur["lon"])

    if gate_given:
        g_lat, g_lon = _coord_or_none(gate_lat), _coord_or_none(gate_lon)
        if g_lat is None or g_lon is None:
            g_lat = g_lon = None
        gate_moved = (g_lat != cur.get("gate_lat")) or (g_lon != cur.get("gate_lon"))
    else:
        g_lat, g_lon = cur.get("gate_lat"), cur.get("gate_lon")
        gate_moved = False

    # when supplies is given, mirror it into materials so the map popup stays in sync
    if supplies is not None:
        supplies_json = json.dumps(supplies)
        materials_json = json.dumps(supplies)
    else:
        supplies_json = cur.get("supplies")
        materials_json = json.dumps(materials) if materials is not None else cur.get("materials")
    receives_json = json.dumps(receives) if receives is not None else cur.get("receives")
    new_vendor = vendor if meta_given else cur.get("vendor")
    new_detail = detail if meta_given else cur.get("detail")
    db.execute(
        "UPDATE locations SET name = ?, loc_type = ?, role = ?, materials = ?, supplies = ?, "
        "receives = ?, lat = ?, lon = ?, gate_lat = ?, gate_lon = ?, vendor = ?, detail = ? "
        "WHERE tenant_id = ? AND id = ?",
        (name if name is not None else cur["name"],
         loc_type if loc_type is not None else cur["loc_type"],
         role if role is not None else cur["role"],
         materials_json, supplies_json, receives_json,
         float(lat) if lat is not None else cur["lat"],
         float(lon) if lon is not None else cur["lon"],
         g_lat, g_lon, new_vendor, new_detail,
         db.current_tenant(), location_id),
    )
    affected = []
    # a gate move changes the routed coordinate just as surely as moving the node does
    if moved or gate_moved:
        affected = routes_touching(location_id)
        # Capture which profiles each route was baked for BEFORE clearing it, so the
        # caller can restore exactly what was there. Without this the profiles are gone
        # with the geometry and a re-bake has to guess.
        affected = [{"id": rid, "profiles": profiles_for_route(rid)} for rid in affected]
        for a in affected:
            db.execute("DELETE FROM route_geometry WHERE tenant_id = ? AND route_id = ?",
                       (db.current_tenant(), a["id"]))
    return {"id": location_id, "moved": moved, "gate_moved": gate_moved,
            "affected_routes": affected}


def delete_location(location_id):
    routes = routes_touching(location_id)
    for rid in routes:
        db.execute("DELETE FROM route_geometry WHERE tenant_id = ? AND route_id = ?",
                   (db.current_tenant(), rid))
        db.execute("DELETE FROM routes WHERE tenant_id = ? AND id = ?",
                   (db.current_tenant(), rid))
    db.execute("DELETE FROM locations WHERE tenant_id = ? AND id = ?",
               (db.current_tenant(), location_id))
    return {"id": location_id, "deleted_routes": routes}


# --------------------------------------------------------------------------- #
#  Route authoring (Phase 1)                                                    #
# --------------------------------------------------------------------------- #
def _loc_list(loc, key):
    try:
        return json.loads(loc.get(key) or "[]")
    except Exception:
        return []


def _next_route_id():
    existing = {r["id"] for r in db.query("SELECT id FROM routes WHERE tenant_id = ?",
                                         (db.current_tenant(),))}
    i = 1
    while f"R{i:03d}" in existing:
        i += 1
    return f"R{i:03d}"


def create_route(origin_id, dest_id, material_category=None, route_id=None, ipt=None):
    """
    Manually pair an origin -> destination for a material category. Validates that
    the origin supplies the category and the destination receives it (server-side
    guard mirroring the UI). Geometry is baked separately (bake_route).
    """
    if not origin_id or not dest_id:
        return {"error": "origin and destination are required"}
    if origin_id == dest_id:
        return {"error": "origin and destination must be different"}
    o = db.query("SELECT * FROM locations WHERE tenant_id = ? AND id = ?",
                 (db.current_tenant(), origin_id))
    d = db.query("SELECT * FROM locations WHERE tenant_id = ? AND id = ?",
                 (db.current_tenant(), dest_id))
    if not o or not d:
        return {"error": "origin or destination not found"}
    o, d = o[0], d[0]

    supplies = _loc_list(o, "supplies") or _loc_list(o, "materials")
    receives = _loc_list(d, "receives")
    shared = [m for m in supplies if m in receives]

    if material_category:
        if supplies and material_category not in supplies:
            return {"error": f"{o['name']} does not supply {material_category}"}
        if receives and material_category not in receives:
            return {"error": f"{d['name']} does not receive {material_category}"}
    elif not shared:
        # Routes no longer carry a material, so validity is the whole intersection.
        # Guard here too, so the API can't be laxer than the UI.
        return {"error": f"{o['name']} and {d['name']} share no material — set what "
                         f"each one supplies/receives on the Locations tab first"}

    rid = (route_id or "").strip() or _next_route_id()
    # scoped to this tenant: 'R001' colliding in another tenant is not a collision here
    if db.query("SELECT id FROM routes WHERE tenant_id = ? AND id = ?",
                (db.current_tenant(), rid)):
        return {"error": f"route id '{rid}' already exists"}

    db.execute(
        "INSERT INTO routes (tenant_id, id, origin_id, dest_id, long_route_id, material_category, ipt, origin_temp_km) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (db.current_tenant(), rid, origin_id, dest_id, None, material_category,
         (ipt or None), 0),
    )
    return {"id": rid, "origin_id": origin_id, "dest_id": dest_id,
            "material_category": material_category, "shared_materials": shared}


def delete_route(route_id):
    db.execute("DELETE FROM route_geometry WHERE tenant_id = ? AND route_id = ?",
               (db.current_tenant(), route_id))
    db.execute("DELETE FROM routes WHERE tenant_id = ? AND id = ?",
               (db.current_tenant(), route_id))
    return {"id": route_id}


def clear_routes():
    """Drop every route + its geometry, keeping locations. For retiring the V2 seed."""
    n = len(db.query("SELECT id FROM routes WHERE tenant_id = ?", (db.current_tenant(),)))
    db.execute("DELETE FROM route_geometry WHERE tenant_id = ?", (db.current_tenant(),))
    db.execute("DELETE FROM routes WHERE tenant_id = ?", (db.current_tenant(),))
    return {"cleared": n}


def bake_route(route_id, profile=DEFAULT_PROFILE, legs=None, alternatives=ALTERNATIVES):
    """
    Route + cache geometry for a single route (used to bake a freshly authored route).

    Bakes both legs by default. Pass legs=("loaded",) to route only the outbound —
    useful when a caller wants a distance quickly and doesn't need the return.
    """
    legs = tuple(legs or LEGS)
    if not here_routing.configured():
        return {"error": "HERE_API_KEY not set on the server", "route_id": route_id}
    r = db.query("SELECT * FROM routes WHERE tenant_id = ? AND id = ?",
                 (db.current_tenant(), route_id))
    if not r:
        return {"error": "route not found", "route_id": route_id}
    r = r[0]
    locs = {l["id"]: l for l in db.query("SELECT * FROM locations WHERE tenant_id = ?",
                                        (db.current_tenant(),))}
    o = locs.get(r["origin_id"]); d = locs.get(r["dest_id"])
    avoid, zone_tag = active_avoid()
    if not o or not d:
        for leg in legs:
            _upsert_geom(route_id, profile, None, None, None,
                         "missing origin/destination coordinates", leg=leg, alt_index=0,
                         zones_applied=zone_tag)
        return {"error": "missing origin/destination", "route_id": route_id}

    # B2: refuse the whole route rather than half of it. A gate deactivated at one end
    # breaks one leg's arrival and the other's departure, so baking the leg that still
    # resolves would leave a route with one fresh and one stale direction — which reads
    # on the map as a working route.
    try:
        gates_by_loc = gates.gates_by_location()
    except Exception:
        gates_by_loc = {}   # location_gates may not exist yet on a cold database
    blockers = gates.bake_blockers(r, o, d, gates_by_loc)
    if blockers:
        msg = "gate unavailable: " + "; ".join(blockers)
        for leg in legs:
            _upsert_geom(route_id, profile, None, None, None, msg[:300], leg=leg,
                         alt_index=0, zones_applied=zone_tag)
        return {"error": msg, "route_id": route_id, "gate_blocked": True,
                "blockers": blockers}

    factors = conversions.load_factors()
    out = {"route_id": route_id, "profile": profile, "legs": {},
           "zones_applied": [z for z in zone_tag.split(",") if z]}
    errs = []
    for leg in legs:
        n, err = _bake_leg(r, o, d, profile, leg, factors, alternatives,
                           avoid=avoid, zone_tag=zone_tag, gates_by_loc=gates_by_loc)
        if err:
            errs.append(f"{leg}: {err[:120]}")
            out["legs"][leg] = {"baked": False, "error": err[:200]}
        else:
            best = db.query(
                "SELECT distance_km, duration_hr FROM route_geometry WHERE tenant_id = ? "
                "AND route_id = ? AND vehicle_profile = ? AND leg = ? AND alt_index = 0",
                (db.current_tenant(), route_id, profile, leg))
            out["legs"][leg] = {
                "baked": True, "alternatives": n,
                "distance_km": best[0]["distance_km"] if best else None,
                "duration_hr": best[0]["duration_hr"] if best else None,
            }
    loaded = out["legs"].get("loaded", {})
    out["baked"] = not errs
    out["distance_km"] = loaded.get("distance_km")   # back-compat with the old shape
    if errs:
        out["error"] = " · ".join(errs)
    return out


def routes_status():
    """
    Per-route metadata + which profiles are baked (for the admin table).

    Each profile entry keeps the flat pre-Step-B fields — 'baked', 'distance_km',
    'duration_hr', 'error', all describing the laden first-choice route — so anything
    reading the old shape still works. Step B's detail hangs off 'legs'.
    """
    locs = {l["id"]: l for l in db.query("SELECT * FROM locations WHERE tenant_id = ?",
                                        (db.current_tenant(),))}
    geoms = {}
    for g in db.query("SELECT * FROM route_geometry WHERE tenant_id = ? "
                      "ORDER BY route_id, vehicle_profile, leg, alt_index",
                      (db.current_tenant(),)):
        prof = geoms.setdefault(g["route_id"], {}).setdefault(
            g["vehicle_profile"], {"baked": False, "distance_km": None, "duration_hr": None,
                                   "error": None, "legs": {}})
        prof["legs"].setdefault(g["leg"], []).append({
            "alt_index": g["alt_index"], "baked": bool(g["geometry"]),
            "distance_km": g["distance_km"], "duration_hr": g["duration_hr"],
            "error": g["error"],
            # Phase 4. haul_zones is '' for a leg baked with no haul road and NULL for
            # one baked before Phase 4 existed; the UI needs to tell those apart to know
            # whether "no haul road" is a fact or an absence of record.
            "haul_zones": [z for z in (g.get("haul_zones") or "").split(",") if z],
            "haul_recorded": g.get("haul_zones") is not None,
            "haul_km": g.get("haul_km"),
            "duration_hr_here": g.get("duration_hr_here"),
        })
        if g["leg"] == "loaded" and g["alt_index"] == 0:
            prof["baked"] = bool(g["geometry"])
            prof["distance_km"] = g["distance_km"]
            prof["duration_hr"] = g["duration_hr"]
            prof["error"] = g["error"]
            prof["haul_km"] = g.get("haul_km")
            prof["duration_hr_here"] = g.get("duration_hr_here")

    # one query for every route's haul-road links rather than one per route
    haul_links = {}
    try:
        for l in db.query("SELECT * FROM route_haul_roads WHERE tenant_id = ? "
                          "ORDER BY route_id, seq, zone_id", (db.current_tenant(),)):
            haul_links.setdefault(l["route_id"], []).append(l)
    except Exception:
        pass          # table not created yet on a database that predates Phase 4

    try:
        gates_by_loc = gates.gates_by_location()
    except Exception:
        gates_by_loc = {}     # location_gates may not exist yet on a cold database

    out = []
    for r in db.query("SELECT * FROM routes WHERE tenant_id = ? ORDER BY id",
                      (db.current_tenant(),)):
        o = locs.get(r["origin_id"], {}); d = locs.get(r["dest_id"], {})
        profs = geoms.get(r["id"], {})
        for p in profs.values():
            legs = p["legs"]
            p["return_baked"] = any(a["baked"] for a in legs.get("return", []))
            p["alt_count"] = max([len(v) for v in legs.values()] or [0])
        out.append({
            "id": r["id"], "origin": o.get("name"), "dest": d.get("name"),
            # ids as well as names, so the Locations table can count routes per
            # location without matching on (non-unique) names
            "origin_id": r["origin_id"], "dest_id": r["dest_id"],
            "material_category": r["material_category"], "ipt": r["ipt"],
            # Phase 4 made this derived. It was seeded from V2 and computed with nowhere;
            # it now means "km of this route's loaded leg on drawn temporary haul road",
            # refreshed by haul.refresh_origin_temp_km() whenever the links or the
            # drawings change. On a route with no haul road attached it is 0.
            "origin_temp_km": r["origin_temp_km"],
            "haul_roads": [l["zone_id"] for l in haul_links.get(r["id"], [])],
            # Phase 5a. B2 says a route whose gate has been deactivated refuses to bake
            # AND is flagged by name — a refusal nobody can see is a silent failure with
            # extra steps, which is what promote_alternative() was fixed for in Phase 4.
            # `gate_blockers` is a list of sentences naming the gate, empty on a healthy
            # route, and it is what makes the route table able to show the reason.
            "origin_gate_id": r.get("origin_gate_id"),
            "dest_gate_id": r.get("dest_gate_id"),
            "gate_blockers": gates.bake_blockers(r, o, d, gates_by_loc),
            "profiles": profs,
        })
    return out


def meta_routes(profile=None):
    """
    The route list /api/meta hands the submission matrix and the dashboard.

    This used to be `json.load(seed_data/routes.json)` -- 69 legacy routes that carry no
    distance_km key at all, so every km, truck-km, CO2 and intensity figure on the
    dashboard read zero and every cycle time was a flat 0.33 h. That is fixed here by
    reading the live network instead.

    Three things the raw `routes` table cannot give the UI, all handled here:

      names       the table stores origin_id / dest_id, not names -- joined from locations
      distance    the table has no distance -- taken from cached geometry, leg 'loaded',
                  alt_index 0, per the decision that a forecast's distance follows the
                  primary route. Which vehicle's geometry is reported is named in
                  distance_profile, because different vehicles legitimately route
                  differently and an unlabelled number would hide that.
      duplicates  two locations are both called 'Parnu terminal' (C01 and C02). The
                  matrix builds its dropdowns from NAMES, so without this those two
                  collapse into one unselectable entry. A name shared by more than one
                  location gets its id appended.

    A route with no baked geometry returns distance_km None -- not 0. Zero is a real
    distance and would quietly pass through every downstream sum; None shows up.
    """
    want = profile or DEFAULT_PROFILE

    locs = db.query("SELECT id, name FROM locations WHERE tenant_id = ?", (db.current_tenant(),))
    name_counts = {}
    for l in locs:
        name_counts[l["name"]] = name_counts.get(l["name"], 0) + 1
    label = {l["id"]: (f"{l['name']} ({l['id']})" if name_counts.get(l["name"], 0) > 1
                       else l["name"]) for l in locs}

    # loaded leg, primary option only -- one row per (route, vehicle)
    dist = {}
    for g in db.query(
        "SELECT route_id, vehicle_profile, distance_km FROM route_geometry "
        "WHERE tenant_id = ? AND leg = 'loaded' AND alt_index = 0 AND distance_km IS NOT NULL",
        (db.current_tenant(),)
    ):
        dist.setdefault(g["route_id"], {})[g["vehicle_profile"]] = g["distance_km"]

    out = []
    for r in db.query("SELECT * FROM routes WHERE tenant_id = ? ORDER BY id",
                      (db.current_tenant(),)):
        per_profile = dist.get(r["id"], {})
        if want in per_profile:
            km, used = per_profile[want], want
        elif per_profile:
            used = sorted(per_profile)[0]
            km = per_profile[used]
        else:
            km, used = None, None
        out.append({
            "route_id": r["id"],
            "origin_id": r["origin_id"], "dest_id": r["dest_id"],
            "origin": label.get(r["origin_id"], r["origin_id"]),
            "dest": label.get(r["dest_id"], r["dest_id"]),
            "ipt": r["ipt"],
            # normalised to a factors.json category, so the UI no longer needs its own
            # free-text guess map
            "material_guess": _to_cat(r["material_category"]) or r["material_category"],
            "distance_km": round(km, 2) if km is not None else None,
            "distance_profile": used,
            "baked_profiles": sorted(per_profile),
        })
    return out


def summary():
    """
    Network totals. 'baked' counts routes whose laden first-choice leg is cached — not
    rows, which after Step B run up to six per route per profile and would read as a
    wildly inflated total against a route count.
    """
    total = len(db.query("SELECT id FROM routes WHERE tenant_id = ?", (db.current_tenant(),)))
    profiles = {}
    for g in db.query("SELECT vehicle_profile, leg, alt_index, geometry, error "
                      "FROM route_geometry WHERE tenant_id = ?", (db.current_tenant(),)):
        p = profiles.setdefault(g["vehicle_profile"],
                                {"baked": 0, "errors": 0, "return_baked": 0, "rows": 0})
        p["rows"] += 1
        if g["alt_index"] != 0:
            continue
        if g["leg"] == "return":
            if g["geometry"]:
                p["return_baked"] += 1
            continue
        if g["geometry"]:
            p["baked"] += 1
        elif g["error"]:
            p["errors"] += 1
    return {"locations": db.count_locations(), "routes": total,
            "here_configured": here_routing.configured(), "profiles": profiles,
            "legs": list(LEGS), "alternatives": ALTERNATIVES}


def route_geometries(route_id, profile=None, leg=None):
    """
    Every cached geometry for one route as a GeoJSON FeatureCollection — each
    (profile, leg, alt_index) its own feature.

    The map's main source carries one geometry per route so the network reads clearly.
    When a single route is selected we want the opposite: all of its options at once,
    so the alternatives can be drawn alongside the chosen line. Scoped to one route,
    so the payload stays small.
    """
    clauses, params = ["route_id = ?", "geometry IS NOT NULL"], [route_id]
    if profile:
        clauses.append("vehicle_profile = ?"); params.append(profile)
    if leg:
        clauses.append("leg = ?"); params.append(leg)
    feats = []
    # tenant_id sits in the fixed head of the statement rather than in `clauses`, so no
    # branch of the optional filters can omit it; its param therefore leads the tuple.
    for g in db.query(
        "SELECT * FROM route_geometry WHERE tenant_id = ? AND " + " AND ".join(clauses) +
        " ORDER BY vehicle_profile, leg, alt_index", (db.current_tenant(),) + tuple(params)
    ):
        try:
            coords = json.loads(g["geometry"])
        except Exception:
            continue
        feats.append({
            "type": "Feature",
            "properties": {
                "route_id": g["route_id"], "vehicle_profile": g["vehicle_profile"],
                "leg": g["leg"], "alt_index": g["alt_index"],
                "distance_km": g["distance_km"], "duration_hr": g["duration_hr"],
                "is_primary": g["alt_index"] == 0,
            },
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    return {"type": "FeatureCollection", "features": feats}


def promote_alternative(route_id, profile, alt_index, leg="loaded"):
    """
    Make one of HERE's alternatives the primary route for a (route, profile, leg).

    HERE ranks the options; this records a human overruling that ranking — the planner
    knows something the router doesn't (a haulier's preference, a residential street to
    avoid, local knowledge of a junction). Implemented as a swap of alt_index with 0,
    so the displaced route stays available rather than being discarded.

    The swap goes via a temporary index because (tenant_id, route_id, vehicle_profile,
    leg, alt_index) is the primary key — two rows cannot briefly share index 0.

    Note this is not durable against a re-bake: routing the pair again re-imports
    HERE's own ordering. Callers should say so.
    """
    alt_index = int(alt_index)
    if alt_index == 0:
        return {"route_id": route_id, "profile": profile, "leg": leg,
                "promoted": 0, "note": "already the primary route"}
    rows = db.query(
        "SELECT alt_index FROM route_geometry WHERE tenant_id = ? AND route_id = ? "
        "AND vehicle_profile = ? AND leg = ?", (db.current_tenant(), route_id, profile, leg))
    have = {r["alt_index"] for r in rows}
    if alt_index not in have:
        # Phase 4 regression, and worth naming rather than reporting as a bare miss: a
        # leg routed through a haul road comes back with ONE option, so there is never
        # an alternative 1 or 2 to promote on it. The generic message would read as a
        # cache miss and send someone re-baking to fix something that is working as
        # designed. See haul.route_with_haul().
        primary = db.query(
            "SELECT haul_zones FROM route_geometry WHERE tenant_id = ? AND route_id = ? AND "
            "vehicle_profile = ? AND leg = ? AND alt_index = 0",
            (db.current_tenant(), route_id, profile, leg))
        tag = (primary[0].get("haul_zones") if primary else None) or ""
        on_haul = [z.strip() for z in tag.split(",") if z.strip()]
        if on_haul:
            return {"error": f"no alternative {alt_index}: this leg runs through haul "
                             f"road(s) {', '.join(on_haul)}, and a leg with a haul road "
                             f"is routed as a single option. Detach the haul road to get "
                             f"alternatives back.",
                    "haul_zones": on_haul, "alternatives_available": sorted(have)}
        return {"error": f"no alternative {alt_index} cached for {profile} / {leg}"}

    TMP = -1
    # tenant_id is a WHERE condition on all three legs of the swap and never appears in
    # a SET clause: the rows are being re-indexed within one tenant, not moved between.
    db.execute(
        "UPDATE route_geometry SET alt_index = ? WHERE tenant_id = ? AND route_id = ? "
        "AND vehicle_profile = ? AND leg = ? AND alt_index = 0",
        (TMP, db.current_tenant(), route_id, profile, leg))
    db.execute(
        "UPDATE route_geometry SET alt_index = 0 WHERE tenant_id = ? AND route_id = ? "
        "AND vehicle_profile = ? AND leg = ? AND alt_index = ?",
        (db.current_tenant(), route_id, profile, leg, alt_index))
    db.execute(
        "UPDATE route_geometry SET alt_index = ? WHERE tenant_id = ? AND route_id = ? "
        "AND vehicle_profile = ? AND leg = ? AND alt_index = ?",
        (alt_index, db.current_tenant(), route_id, profile, leg, TMP))
    return {"route_id": route_id, "profile": profile, "leg": leg,
            "promoted": alt_index, "swapped_with": 0,
            "note": "Re-baking this route will restore HERE's own ranking."}


# --------------------------------------------------------------------------- #
#  Diagnostics                                                                 #
# --------------------------------------------------------------------------- #
def factors_diagnostics():
    """
    Show how each vehicle profile actually resolves against factors.json.

    Several distinct faults present identically in the UI — a profile string in the
    database that no longer matches a key in factors.json, a deployed factors.json
    older than the repo's, or vehicles that genuinely share an emissions figure — and
    all three make the analysis table look like it is ignoring the vehicle. This
    prints the resolved numbers next to the raw keys so they can be told apart.

    'matched' false is the alarm: it means the lookup fell through to _default and
    every such profile will report identical payload and CO2 no matter what.
    """
    factors = conversions.load_factors()
    veh = factors.get("vehicles", {}) or {}
    known = {k for k in veh if not k.startswith("_")}
    default = veh.get("_default", {}) or {}
    plan = factors.get("planning", {}) or {}

    # every profile string that actually appears in cached geometry
    used = sorted({g["vehicle_profile"] for g in
                   db.query("SELECT DISTINCT vehicle_profile FROM route_geometry "
                            "WHERE tenant_id = ?", (db.current_tenant(),))})

    rows = []
    for prof in sorted(known | set(used)):
        v = veh.get(prof)
        matched = v is not None
        v = v or default
        load_m, unload_m = _turnaround_minutes(prof, factors)
        rows.append({
            "profile": prof,
            "in_factors": matched,
            "in_database": prof in used,
            "payload_t": v.get("payload_t", default.get("payload_t")),
            "emissions_kg_co2e_per_km": v.get("emissions_kg_co2e_per_km",
                                              default.get("emissions_kg_co2e_per_km")),
            "gross_weight_kg": (v.get("routing", {}) or {}).get("gross_weight_kg"),
            "tare_weight_kg": here_routing.tare_weight_kg(prof, factors) if matched else None,
            "load_minutes": load_m, "unload_minutes": unload_m,
            # ⚠️ the UNLOADING component only. This diagnostic catalogues vehicles, and
            # a vehicle has no route, so it has no gates and therefore no induction or
            # internal-travel time. Naming it turnaround_hr here would invite exactly
            # the comparison that must never be made — this figure and a route's
            # turnaround are different quantities from Phase 5a onward.
            "unloading_hr": round((load_m + unload_m) / 60.0, 3),
            "truck_params_laden": here_routing._truck_params(prof, factors, laden=True),
            "truck_params_unladen": here_routing._truck_params(prof, factors, laden=False),
        })

    unmatched = [r["profile"] for r in rows if r["in_database"] and not r["in_factors"]]
    emis = [r["emissions_kg_co2e_per_km"] for r in rows if r["in_factors"]]
    pay = [r["payload_t"] for r in rows if r["in_factors"]]
    return {
        "factors_path": conversions._FACTORS_PATH,
        "planning": plan,
        "vehicles": rows,
        "warnings": (
            ([f"{p!r} is stored in route_geometry but is not a key in factors.json — "
              f"it falls back to _default, so its payload and CO2 will match every other "
              f"unmatched profile. Re-bake after fixing the name." for p in unmatched])
            + ([] if len(set(emis)) > 1 else
               ["Every vehicle in factors.json carries the same emissions figure, so CO2 "
                "will be identical across vehicles by definition. Check factors.json is "
                "the version you think it is."])
            + ([] if len(set(pay)) > 1 else
               ["Every vehicle in factors.json carries the same payload."])
        ),
    }


def route_diagnostics(route_id, profile=DEFAULT_PROFILE, probe=False):
    """
    Everything known about one route for one profile: the coordinates actually routed
    to (gate or marker), what is cached, and — with probe=True — a live HERE call
    showing exactly what was sent and what came back.

    probe=True costs two HERE requests (one per leg). It is the only way to see
    whether HERE declined an alternatives request or whether truck parameters reached
    it at all.
    """
    r = db.query("SELECT * FROM routes WHERE tenant_id = ? AND id = ?",
                 (db.current_tenant(), route_id))
    if not r:
        return {"error": "route not found", "route_id": route_id}
    r = r[0]
    locs = {l["id"]: l for l in db.query("SELECT * FROM locations WHERE tenant_id = ?",
                                        (db.current_tenant(),))}
    o, d = locs.get(r["origin_id"]), locs.get(r["dest_id"])
    if not o or not d:
        return {"error": "missing origin/destination", "route_id": route_id}

    # Phase 5a: four resolutions, not two. Each end is entered and left, and with
    # one-way gates those are different points — which is exactly what this diagnostic
    # exists to show, because nothing else in the system makes it visible.
    o_exit = _waypoint_full(o, "exit", r.get("origin_gate_id"))
    o_entry = _waypoint_full(o, "entry", r.get("origin_gate_id"))
    d_entry = _waypoint_full(d, "entry", r.get("dest_gate_id"))
    d_exit = _waypoint_full(d, "exit", r.get("dest_gate_id"))
    o_lat, o_lon = o_exit["lat"], o_exit["lon"]      # the loaded leg starts here
    d_lat, d_lon = d_entry["lat"], d_entry["lon"]    # and ends here

    def _end(loc, sel, leave, arrive):
        return {
            "id": loc["id"], "name": loc["name"],
            "marker": [loc["lat"], loc["lon"]],
            # the pre-5a pair, kept so a mismatch between it and the migrated gate is
            # visible rather than inferred
            "gate": [loc.get("gate_lat"), loc.get("gate_lon")],
            "selected_gate_id": sel,
            "leaves_by": {"gate_id": leave["gate_id"], "name": leave["gate_name"],
                          "point": [leave["lat"], leave["lon"]],
                          "source": leave["source"], "blocked": leave["blocked"]},
            "arrives_by": {"gate_id": arrive["gate_id"], "name": arrive["gate_name"],
                           "point": [arrive["lat"], arrive["lon"]],
                           "source": arrive["source"], "blocked": arrive["blocked"]},
            "asymmetric": (leave["lat"], leave["lon"]) != (arrive["lat"], arrive["lon"]),
            "using_gate": leave["source"] in ("selected", "default")
                          or arrive["source"] in ("selected", "default"),
        }

    out = {
        "route_id": route_id, "profile": profile,
        "origin": dict(_end(o, r.get("origin_gate_id"), o_exit, o_entry),
                       routed_to=[o_lat, o_lon]),
        "destination": dict(_end(d, r.get("dest_gate_id"), d_exit, d_entry),
                            routed_to=[d_lat, d_lon]),
        "gate_blockers": gates.bake_blockers(r, o, d),
        "cached": [
            {"leg": g["leg"], "alt_index": g["alt_index"], "distance_km": g["distance_km"],
             "duration_hr": g["duration_hr"], "has_geometry": bool(g["geometry"]),
             "error": g["error"], "computed_at": g["computed_at"]}
            for g in db.query(
                "SELECT * FROM route_geometry WHERE tenant_id = ? AND route_id = ? "
                "AND vehicle_profile = ? ORDER BY leg, alt_index",
                (db.current_tenant(), route_id, profile))
        ],
        "here_configured": here_routing.configured(),
    }
    _probe_avoid, _zone_tag = active_avoid()
    out["zones_in_force"] = [z for z in _zone_tag.split(",") if z]
    out["avoid_areas"] = _probe_avoid
    if probe:
        factors = conversions.load_factors()
        out["probe"] = {
            # the probe must send the same avoid[areas] a real bake would, or it
            # answers a different question from the one being diagnosed
            "loaded": here_routing.probe(o_lat, o_lon, d_lat, d_lon, profile, factors,
                                         avoid_areas=(_probe_avoid or None), laden=True),
            # the return leg leaves the destination and arrives at the origin, so it is
            # d_exit -> o_entry and NOT the loaded pair reversed. Probing the reversed
            # pair would compare two routes that no bake will ever produce.
            "return": here_routing.probe(d_exit["lat"], d_exit["lon"],
                                         o_entry["lat"], o_entry["lon"], profile, factors,
                                         avoid_areas=(_probe_avoid or None), laden=False),
        }
        L, R = out["probe"]["loaded"], out["probe"]["return"]
        ld = (L.get("summaries") or [{}])[0].get("distance_km")
        rd = (R.get("summaries") or [{}])[0].get("distance_km")
        out["probe"]["legs_differ"] = (ld is not None and rd is not None and ld != rd)
        # ⚠️ with asymmetric gates the legs differ for TWO reasons at once — different
        # roads and different endpoints. Said here so a distance gap is not read as a
        # routing finding when it is a gate placement.
        out["probe"]["endpoints_differ"] = (
            (o_exit["lat"], o_exit["lon"]) != (o_entry["lat"], o_entry["lon"])
            or (d_entry["lat"], d_entry["lon"]) != (d_exit["lat"], d_exit["lon"]))
    return out


def compare_profiles(route_id, profiles=None, probe=False):
    """
    Route the same pair for several vehicles and show whether anything differs.

    Answers the question the UI can't: when every vehicle draws the same line, is that
    because the road network offers no alternative, or because the vehicle's
    dimensions never reached HERE?
    """
    factors = conversions.load_factors()
    profiles = profiles or [p for p in conversions.vehicle_names(factors)]
    r = db.query("SELECT * FROM routes WHERE tenant_id = ? AND id = ?",
                 (db.current_tenant(), route_id))
    if not r:
        return {"error": "route not found", "route_id": route_id}
    r = r[0]
    locs = {l["id"]: l for l in db.query("SELECT * FROM locations WHERE tenant_id = ?",
                                        (db.current_tenant(),))}
    o, d = locs.get(r["origin_id"]), locs.get(r["dest_id"])
    if not o or not d:
        return {"error": "missing origin/destination", "route_id": route_id}
    # the loaded leg's real endpoints: out of the origin, into the destination
    o_lat, o_lon = _waypoint(o, "exit", r.get("origin_gate_id"))
    d_lat, d_lon = _waypoint(d, "entry", r.get("dest_gate_id"))

    _probe_avoid, _zone_tag = active_avoid()
    rows = []
    for p in profiles:
        cached = db.query(
            "SELECT distance_km, duration_hr FROM route_geometry WHERE tenant_id = ? "
            "AND route_id = ? AND vehicle_profile = ? AND leg = 'loaded' AND alt_index = 0",
            (db.current_tenant(), route_id, p))
        entry = {"profile": p,
                 "cached_km": cached[0]["distance_km"] if cached else None,
                 "gross_weight_kg": here_routing._truck_params(p, factors, laden=True)
                                        .get("vehicle[grossWeight]")}
        if probe:
            pr = here_routing.probe(o_lat, o_lon, d_lat, d_lon, p, factors,
                                    avoid_areas=(_probe_avoid or None), laden=True)
            entry["live_km"] = (pr.get("summaries") or [{}])[0].get("distance_km")
            entry["routes_returned"] = pr.get("routes_returned")
            entry["truck_params_present"] = pr.get("truck_params_present")
            entry["error"] = pr.get("error")
        rows.append(entry)

    key = "live_km" if probe else "cached_km"
    vals = [r0[key] for r0 in rows if r0.get(key) is not None]
    return {
        "route_id": route_id, "rows": rows,
        "all_identical": len(set(vals)) <= 1 and len(vals) > 1,
        "note": ("Every vehicle produced the same distance. That is plausible on a sparse "
                 "network with no weight-restricted roads on this pair — check "
                 "truck_params_present is true and gross weights differ before treating "
                 "it as a fault." if len(set(vals)) <= 1 and len(vals) > 1
                 else "Vehicles produced differing distances, so truck parameters are "
                      "reaching HERE."),
        "zones_in_force": [z for z in _zone_tag.split(",") if z],
    }


def zones_diagnostics(route_id=None, profile=DEFAULT_PROFILE, probe=False):
    """
    What the zone layer would actually send to HERE, and — with probe=true — what HERE
    actually does with it.

    This exists because the number of avoid[areas] HERE will accept, and what it does
    when handed a box that encloses a route's own endpoint, are questions this codebase
    has never verified against the live API. Rather than encode a guessed limit, the
    diagnostic sends the current set on a real route and hands back the raw request and
    response, with and without the zones, so a difference is attributable rather than
    inferred from a single number. probe=true spends two HERE requests.

    With no route_id it reports the zone set only and spends nothing.
    """
    avoid, tag = active_avoid()
    try:
        zs = zones.routing_zones()
    except Exception as e:
        return {"error": f"zones unavailable: {e}", "avoid_areas": avoid}

    out = {
        "zones_in_force": [{"id": z["id"], "name": z["name"], "kind": z["kind"],
                            "starts_on": z["starts_on"], "ends_on": z["ends_on"],
                            "bbox": z["bbox"], "avoid_area": z["avoid_area"]} for z in zs],
        "count": len(zs),
        "avoid_areas": avoid,
        "zones_applied_tag": tag,
        "here_configured": here_routing.configured(),
        # how many baked legs carry each provenance, so "is the network baked against the
        # zones I think it is" is answerable without opening the database
        "baked_legs_by_tag": {},
        "note": ("HERE's own limit on the number of avoid[areas] is NOT enforced here and "
                 "has never been verified against the live API. If a bake starts failing "
                 "once several zones exist, run this with probe=true and read the error "
                 "HERE actually returns."),
    }
    try:
        for row in db.query("SELECT zones_applied, COUNT(*) AS n FROM route_geometry "
                            "WHERE tenant_id = ? GROUP BY zones_applied",
                            (db.current_tenant(),)):
            k = row["zones_applied"]
            out["baked_legs_by_tag"]["(pre-Phase-3)" if k is None else (k or "(none)")] = row["n"]
    except Exception:
        pass   # column added by the Phase 3 migration; absent on a database that skipped it

    if route_id and probe:
        r = db.query("SELECT * FROM routes WHERE tenant_id = ? AND id = ?",
                     (db.current_tenant(), route_id))
        if not r:
            out["probe_error"] = f"route {route_id} not found"
            return out
        r = r[0]
        locs = {l["id"]: l for l in db.query("SELECT * FROM locations WHERE tenant_id = ?",
                                            (db.current_tenant(),))}
        o, d = locs.get(r["origin_id"]), locs.get(r["dest_id"])
        if not o or not d:
            out["probe_error"] = "missing origin/destination"
            return out
        o_lat, o_lon = _waypoint(o, "exit", r.get("origin_gate_id"))
        d_lat, d_lon = _waypoint(d, "entry", r.get("dest_gate_id"))
        factors = conversions.load_factors()
        out["probe"] = {
            "route_id": route_id, "profile": profile,
            "with_zones": here_routing.probe(o_lat, o_lon, d_lat, d_lon, profile, factors,
                                             avoid_areas=(avoid or None), laden=True),
            "without_zones": here_routing.probe(o_lat, o_lon, d_lat, d_lon, profile,
                                                factors, laden=True),
        }
    return out


# --------------------------------------------------------------------------- #
#  Route analysis (Phase 1-tail Step C)                                        #
# --------------------------------------------------------------------------- #
def _turnaround_minutes(profile, factors):
    """
    Load and unload minutes for a vehicle.

    These were a single pair of global constants, which made every vehicle's haul cycle
    identical whenever HERE returned the same drive time — a 3.5t rigid and a 29t artic
    do not turn round in the same twenty minutes. Per-vehicle values in factors.json
    take precedence; the global planning figures remain the fallback so an incomplete
    factors.json still works.
    """
    plan = factors.get("planning", {}) or {}
    v = (factors.get("vehicles", {}) or {}).get(profile) or {}
    default = (factors.get("vehicles", {}) or {}).get("_default", {}) or {}
    load = v.get("load_minutes", default.get("load_minutes", plan.get("load_minutes", 0)))
    unload = v.get("unload_minutes", default.get("unload_minutes", plan.get("unload_minutes", 0)))
    return float(load or 0), float(unload or 0)


#: B5, answered 2026-08-30: **(c) — both, drawn road wins.**
#:
#: Internal travel had two homes and they overlap. Phase 4 substitutes a drawn haul
#: road's assigned speed into route_geometry.duration_hr, and route_analysis() reads
#: duration_hr for cycle time — so a route with a drawn internal road ALREADY carries
#: its internal minutes. B4 then asked for an internal-travel component inside
#: turnaround_hr. Adding both counts the same minutes twice, and a cycle time that is
#: too long makes every fleet-size number too big — wrong in the direction that looks
#: cautious, which is the hardest kind to notice.
#:
#: The rule, and it is a rule rather than a default:
#:   * a route with any attached haul road takes its internal travel from the geometry
#:     and gets NO flat figure;
#:   * a route with none takes the flat per-gate figure;
#:   * `internal_travel_source` says which happened, on every row.
#:
#: ⚠️ The precedence is per ROUTE, not per end, because in 5a nothing says which end a
#: haul road belongs to — route_haul_roads records the route and the traversal order,
#: not the site. A route with a drawn road at the origin and a flat figure owed at the
#: destination therefore under-counts. 5b's gate<->area links are what make this
#: per-end, and this note is the reason they have to.
INTERNAL_TRAVEL_SOURCES = ("drawn_road", "flat", "none")


def _turnaround_parts(profile, factors, origin=None, dest=None, route=None,
                      has_drawn_road=False, gates_by_loc=None):
    """
    B4: turnaround as three named components instead of one number.

      unloading       load_minutes + unload_minutes, per vehicle, from factors.json.
                      Unchanged from pre-5a — this IS the old turnaround.
      safety          B3 induction, per GATE, applied to whatever arrives. Charged at
                      the gate the truck ARRIVES by at each end, once per cycle each.
      internal travel the flat per-gate figure, or nothing when a drawn road already
                      carries it. See INTERNAL_TRAVEL_SOURCES above.

    🔴 THE INVARIANT THIS FUNCTION MUST KEEP. On a database with no gate rows — or with
    gates whose two new minute fields are NULL, which is exactly what the legacy
    migration creates — safety and internal travel are both 0.0 and `total_minutes`
    equals load + unload to the digit. That is what makes the split observably not a
    change to anybody's numbers on the day it ships. test_phase5a.py asserts it across
    every vehicle profile in factors.json, and it is the assertion the kickoff note
    demanded before any number moves.
    """
    load_m, unload_m = _turnaround_minutes(profile, factors)
    safety_m = 0.0
    flat_internal_m = 0.0
    gbl = gates_by_loc or {}
    route = route or {}
    for loc, gate_id in ((origin, route.get("origin_gate_id")),
                         (dest, route.get("dest_gate_id"))):
        if not loc:
            continue
        # the ARRIVAL gate at each end: induction happens on the way in, and the flat
        # internal-travel figure is the run from that gate to the working face
        try:
            r = gates.resolve(loc, "entry", gate_id, gates=gbl.get(loc.get("id")))
        except Exception:
            continue     # location_gates may not exist yet on a cold database
        safety_m += float(r.get("safety_minutes") or 0.0)
        flat_internal_m += float(r.get("internal_travel_minutes") or 0.0)

    if has_drawn_road:
        internal_m, source = 0.0, "drawn_road"
    elif flat_internal_m > 0:
        internal_m, source = flat_internal_m, "flat"
    else:
        internal_m, source = 0.0, "none"

    total = load_m + unload_m + safety_m + internal_m
    return {
        "load_minutes": load_m,
        "unload_minutes": unload_m,
        "unloading_minutes": load_m + unload_m,
        "safety_minutes": safety_m,
        "internal_travel_minutes": internal_m,
        "internal_travel_source": source,
        # what the flat figure WOULD have been, kept even when the drawn road won. A
        # number that vanished silently is how someone later concludes the gate field
        # was never wired up.
        "internal_travel_flat_available": flat_internal_m,
        "total_minutes": total,
        "turnaround_hr": total / 60.0,
    }



def route_analysis(route_id, profiles=None):
    """
    Haul-cycle figures for one route, per vehicle profile x alternative.

    A cycle is: drive out laden, unload, drive back empty, load again. Step B gives us
    a separately-routed return leg, so the cycle uses that real duration rather than
    assuming the way home mirrors the way out — they differ whenever the empty truck
    can use a road the laden one can't.

    Where a return leg hasn't been baked, the outbound duration stands in and the row
    is flagged 'return_estimated' rather than silently pretending to be measured.
    """
    r = db.query("SELECT * FROM routes WHERE tenant_id = ? AND id = ?",
                 (db.current_tenant(), route_id))
    if not r:
        return {"error": "route not found", "route_id": route_id}

    factors = conversions.load_factors()
    plan = factors.get("planning", {}) or {}
    shift_hr = float(plan.get("shift_hours_per_day") or 10)
    work_days = float(plan.get("working_days_per_month") or 22)
    veh = factors.get("vehicles", {}) or {}
    default_veh = veh.get("_default", {}) or {}

    # Phase 5a: the two ends and their gates, for the turnaround split. Resolved once
    # per route rather than per (profile x alternative) row — the gates do not vary
    # with the vehicle, and B3 says induction does not either.
    route_row = r[0]
    locs = {l["id"]: l for l in db.query("SELECT * FROM locations WHERE tenant_id = ?",
                                        (db.current_tenant(),))}
    o_loc = locs.get(route_row.get("origin_id"))
    d_loc = locs.get(route_row.get("dest_id"))
    try:
        gates_by_loc = gates.gates_by_location()
    except Exception:
        gates_by_loc = {}   # location_gates may not exist yet on a cold database

    rows_by = {}
    for g in db.query("SELECT * FROM route_geometry WHERE tenant_id = ? AND route_id = ?",
                      (db.current_tenant(), route_id)):
        if profiles and g["vehicle_profile"] not in profiles:
            continue
        rows_by.setdefault(g["vehicle_profile"], {}).setdefault(g["leg"], {})[g["alt_index"]] = g

    out = []
    for prof in sorted(rows_by):
        legs = rows_by[prof]
        loaded, ret = legs.get("loaded", {}), legs.get("return", {})
        v = veh.get(prof, {}) or {}
        payload_t = float(v.get("payload_t") or default_veh.get("payload_t") or 0)
        co2_per_km = float(v.get("emissions_kg_co2e_per_km")
                           or default_veh.get("emissions_kg_co2e_per_km") or 0)
        # turnaround varies by vehicle — a flatbed strapping a load is not a tipper
        load_m, unload_m = _turnaround_minutes(prof, factors)
        for alt in sorted(loaded):
            lg = loaded[alt]
            if not lg["geometry"]:
                continue
            # pair like-for-like where we can; otherwise fall back to the best return
            rg = ret.get(alt) or ret.get(0)
            if rg is not None and not rg["geometry"]:
                rg = None
            l_km = lg["distance_km"] or 0.0
            l_hr = lg["duration_hr"] or 0.0
            r_km = (rg["distance_km"] if rg else l_km) or 0.0
            r_hr = (rg["duration_hr"] if rg else l_hr) or 0.0
            # B5(c): the drawn road wins. `has_drawn_road` is read off the geometry
            # rows actually being costed, not off route_haul_roads — an attachment that
            # has not been baked yet contributes nothing to duration_hr, so charging it
            # as "already counted" would drop the flat figure for minutes nobody has.
            has_drawn_road = bool((lg.get("haul_zones") or "")
                                  or (rg is not None and (rg.get("haul_zones") or "")))
            parts = _turnaround_parts(prof, factors, origin=o_loc, dest=d_loc,
                                      route=route_row, has_drawn_road=has_drawn_road,
                                      gates_by_loc=gates_by_loc)
            turnaround_hr = parts["turnaround_hr"]
            cycle_hr = l_hr + r_hr + turnaround_hr
            trips = int(shift_hr // cycle_hr) if cycle_hr > 0 else 0
            total_km = round(l_km + r_km, 2)
            out.append({
                "profile": prof, "alt_index": alt,
                "loaded_km": round(l_km, 2), "return_km": round(r_km, 2),
                "total_km": total_km,
                "loaded_hr": round(l_hr, 3), "return_hr": round(r_hr, 3),
                "cycle_hr": round(cycle_hr, 3),
                "trips_per_day": trips,
                "tonnes_per_day": round(trips * payload_t, 1),
                "tonnes_per_month": round(trips * payload_t * work_days, 1),
                "co2_kg_per_trip": round(total_km * co2_per_km, 1),
                "co2_kg_per_day": round(total_km * co2_per_km * trips, 1),
                "payload_t": payload_t,
                "co2_kg_per_km": co2_per_km,
                "load_minutes": load_m, "unload_minutes": unload_m,
                # B4: the total AND its three parts. The total is what every surface
                # must display — the dashboard once computed its own and disagreed with
                # this one by 125% (main.py:588). The parts are here so the total can be
                # explained, not so anyone can re-add them.
                "turnaround_hr": round(turnaround_hr, 3),
                "turnaround_parts": {
                    "unloading_minutes": round(parts["unloading_minutes"], 3),
                    "internal_travel_minutes": round(parts["internal_travel_minutes"], 3),
                    "safety_minutes": round(parts["safety_minutes"], 3),
                    "internal_travel_source": parts["internal_travel_source"],
                    "internal_travel_flat_available":
                        round(parts["internal_travel_flat_available"], 3),
                    "total_minutes": round(parts["total_minutes"], 3),
                },
                "return_estimated": rg is None,
                # Phase 4: how much of this cycle is drawn haul road, and what the cycle
                # would have been on HERE's own timing. Surfaced rather than folded in
                # silently — an assigned speed is a planning assumption, and a cycle time
                # that moved because someone typed 25 km/h into a form should say so.
                "haul_km": round((lg.get("haul_km") or 0)
                                 + ((rg.get("haul_km") or 0) if rg else 0), 3),
                "haul_zones": sorted(set(
                    [z for z in (lg.get("haul_zones") or "").split(",") if z]
                    + ([z for z in (rg.get("haul_zones") or "").split(",") if z] if rg else []))),
                "cycle_hr_here": (
                    round((lg.get("duration_hr_here") or l_hr)
                          + ((rg.get("duration_hr_here") or r_hr) if rg else
                             (lg.get("duration_hr_here") or l_hr))
                          + turnaround_hr, 3)),
                "haul_speed_applied": bool(lg.get("duration_hr_here") is not None
                                           or (rg is not None
                                               and rg.get("duration_hr_here") is not None)),
            })
    return {
        "route_id": route_id,
        "planning": {"shift_hours_per_day": shift_hr,
                     "working_days_per_month": work_days},
        "rows": out,
    }

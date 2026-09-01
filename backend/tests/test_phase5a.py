"""
Phase 5a backend assertions — multiple gates per location, the per-leg asymmetric
waypoint, and the turnaround split.

Same harness as test_phase4.py: a scratch SQLite database, no network, and
`fastapi` / `flexpolyline` / `psycopg2` stubbed so main.py imports and its endpoint
functions can be called directly as plain Python.

THE ASSERTION THIS FILE EXISTS FOR
----------------------------------
Section 5 below. B4 splits `turnaround_hr` into unloading, internal travel and safety
check, and the kickoff note is blunt about the risk: the split silently rewrites every
route's cycle time, trips, tonnes and CO2 unless the OLD figure is reproducible from the
NEW parts first. So the file asserts, for every vehicle profile in the real
factors.json, that a database with no gates (and one with migrated gates, whose two new
minute fields are NULL) produces total_minutes == load + unload EXACTLY. Nothing moves
on the day this ships; numbers move only when someone types an induction time into a
gate.

Section 6 is the B5 double-count. Phase 4 already puts internal travel into duration_hr
via a drawn haul road's assigned speed. Rule (c) was chosen: the drawn road wins and the
flat per-gate figure is the fallback. Both branches are asserted, and so is the fact
that the flat figure is still REPORTED when the road won — a number that vanishes
silently is how someone later concludes the field was never wired up.

WHAT THIS DOES NOT PROVE
------------------------
Read this before quoting the pass count.

  * **HERE is never called.** `haul.route_with_haul` and `here_routing.routes` are
    monkeypatched to recorders. Every claim about the bake is a claim about the
    COORDINATES this code hands them, not about what comes back.
  * **No Postgres branch runs.** `location_gates` and the two new `routes` columns are
    created against SQLite only. The Postgres path of init_gates_db() is an
    `ADD COLUMN IF NOT EXISTS` and a plain CREATE, so it needs no key rebuild — but it
    has not been executed.
  * **Nothing in a browser.** The gate editor, the drag-to-move and the route gate
    pickers are asserted at source level in parse_frontend.js, not exercised.
  * **The HTTP layer is stubbed.** Endpoint BODIES run; the transport does not, so
    nothing here proves the admin token gate actually rejects a request.

Run:  python3 backend/tests/test_phase5a.py
"""
import os
import sys
import json
import math
import shutil
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
ROOT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)

# --------------------------------------------------------------------------- #
#  Stubs                                                                       #
# --------------------------------------------------------------------------- #
_fp = types.ModuleType("flexpolyline")
_fp.decode = lambda s: []
_fp.encode = lambda pts: ""
sys.modules.setdefault("flexpolyline", _fp)


def _passthrough_decorator(*a, **k):
    def wrap(fn):
        return fn
    return wrap


class _App:
    def __init__(self, *a, **k):
        pass

    def get(self, *a, **k):
        return _passthrough_decorator()

    post = put = delete = patch = get

    def add_middleware(self, *a, **k):
        pass

    def mount(self, *a, **k):
        pass


class _HTTPException(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


def _Query(default=None, **k):
    return default


_fa = types.ModuleType("fastapi")
_fa.FastAPI = _App
_fa.HTTPException = _HTTPException
_fa.Query = _Query
sys.modules.setdefault("fastapi", _fa)

_mw = types.ModuleType("fastapi.middleware")
_cors = types.ModuleType("fastapi.middleware.cors")
_cors.CORSMiddleware = object
_mw.cors = _cors
sys.modules.setdefault("fastapi.middleware", _mw)
sys.modules.setdefault("fastapi.middleware.cors", _cors)

_resp = types.ModuleType("fastapi.responses")
_resp.FileResponse = lambda *a, **k: None
sys.modules.setdefault("fastapi.responses", _resp)

_static = types.ModuleType("fastapi.staticfiles")
# a CLASS, not a lambda: main.py subclasses this since the /map/ no-cache fix,
# and `class X(lambda)` is a TypeError
class _StaticFiles:
    def __init__(self, *a, **k):
        pass

    async def get_response(self, path, scope):
        return None


_static.StaticFiles = _StaticFiles
sys.modules.setdefault("fastapi.staticfiles", _static)

TMP = tempfile.mkdtemp(prefix="rbe_phase5a_")
os.environ.pop("DATABASE_URL", None)

import db  # noqa: E402
db._SQLITE_PATH = os.path.join(TMP, "scratch.db")

import conversions  # noqa: E402
import zones  # noqa: E402
import haul  # noqa: E402
import network  # noqa: E402
import gates  # noqa: E402
import here_routing  # noqa: E402
import main  # noqa: E402

PASS = 0
FAIL = []


def ok(label, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{label} {extra}".strip())


def reset_db():
    if os.path.exists(db._SQLITE_PATH):
        os.remove(db._SQLITE_PATH)
    db.init_network_db()
    db.init_zones_db()
    db.init_gates_db()     # Phase 5a


def cols(table):
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return {r[1]: r for r in cur.fetchall()}
    finally:
        conn.close()


def tables():
    return {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}



# --------------------------------------------------------------------------- #
#  Fixtures                                                                    #
# --------------------------------------------------------------------------- #
def seed_two_locations():
    """
    O west, D east. Each gets a legacy (gate_lat, gate_lon) so the pre-5a fallback is
    exercised as well as the gate table — the legacy pair is resolution step 4 and it
    is what keeps a database with no gate rows routing exactly as it did before.
    """
    db.execute("INSERT INTO locations (id, name, lat, lon, gate_lat, gate_lon) "
               "VALUES (?, ?, ?, ?, ?, ?)", ("L1", "West pit", 58.5, 24.00, 58.51, 24.01))
    db.execute("INSERT INTO locations (id, name, lat, lon, gate_lat, gate_lon) "
               "VALUES (?, ?, ?, ?, ?, ?)", ("L2", "East site", 58.5, 25.00, 58.52, 25.02))
    db.execute("INSERT INTO routes (id, origin_id, dest_id, origin_temp_km) "
               "VALUES (?, ?, ?, ?)", ("R1", "L1", "L2", 0))


def loc(lid):
    return db.query("SELECT * FROM locations WHERE tenant_id = ? AND id = ?",
                    (db.current_tenant(), lid))[0]


def route(rid="R1"):
    return db.query("SELECT * FROM routes WHERE tenant_id = ? AND id = ?",
                    (db.current_tenant(), rid))[0]


def add_geom(route_id, leg="loaded", profile="P", alt=0, haul_tag=None, dur=1.0):
    db.execute(
        "INSERT INTO route_geometry (route_id, vehicle_profile, leg, alt_index, geometry, "
        "distance_km, duration_hr, zones_applied, haul_zones) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (route_id, profile, leg, alt, json.dumps([[24.0, 58.5], [25.0, 58.5]]),
         70.0, dur, "", haul_tag))


# =========================================================================== #
#  1. Schema                                                                   #
# =========================================================================== #
reset_db()

ok("location_gates table is created", "location_gates" in tables())
gc = cols("location_gates")
for c in ("tenant_id", "id", "location_id", "name", "direction", "lat", "lon",
          "safety_minutes", "internal_travel_minutes", "is_default", "active",
          "note", "created_at", "updated_at"):
    ok(f"location_gates has {c}", c in gc)

# tenant_id FIRST in the key, as Phase 4.5 requires of every tenanted table. Ids are
# client-supplied and human-meaningful, so two tenants will both want a G001; a plain
# column would leave the second tenant's insert dying on a key violation.
ok("location_gates.tenant_id is the FIRST primary key column",
   gc["tenant_id"][5] == 1, f"pk position {gc['tenant_id'][5]}")
ok("location_gates.id is the second key column", gc["id"][5] == 2)

rc = cols("routes")
ok("routes gains origin_gate_id", "origin_gate_id" in rc)
ok("routes gains dest_gate_id", "dest_gate_id" in rc)
ok("routes keeps origin_temp_km through the Phase 5a migration", "origin_temp_km" in rc)

ok("location_gates is registered as tenanted",
   "location_gates" in db.TENANTED_TABLES)
ok("location_gates has a primary key declared for the Postgres path",
   db._TENANT_PK.get("location_gates") == "(tenant_id, id)")
# the two new routes columns must be in _TENANT_DDL as well as ALTERed on, or the
# SQLite tenant rebuild copies only the intersection and drops them
ok("origin_gate_id is in the routes DDL, not only in an ALTER",
   "origin_gate_id" in db._TENANT_DDL["routes"])
ok("dest_gate_id is in the routes DDL, not only in an ALTER",
   "dest_gate_id" in db._TENANT_DDL["routes"])

# Render runs every migration on every boot
db.init_gates_db()
db.init_network_db()
ok("re-running init_gates_db is idempotent",
   "location_gates" in tables() and "origin_gate_id" in cols("routes"))


# =========================================================================== #
#  2. Gate CRUD                                                                #
# =========================================================================== #
reset_db()
seed_two_locations()

g = gates.create_gate("L1", "North gate", 58.60, 24.10, direction="access")
ok("a gate is created", g.get("id") == "G001", str(g))
ok("gate ids run G001, G002, ...",
   gates.create_gate("L1", "South gate", 58.40, 24.10,
                     direction="egress").get("id") == "G002")
ok("a gate on an unknown location is refused",
   "error" in gates.create_gate("NOPE", "x", 58.5, 24.0))
ok("a gate with no name is refused", "error" in gates.create_gate("L1", "", 58.5, 24.0))
ok("a gate with no coordinate is refused",
   "error" in gates.create_gate("L1", "x", None, None))
ok("an unknown direction falls back to 'both' rather than erroring",
   gates.create_gate("L2", "Main", 58.52, 25.02,
                     direction="sideways")["direction"] == "both")

# B3: induction is per gate, and a negative one is a typo rather than a zero
ok("safety_minutes is stored on the gate",
   gates.update_gate("G001", safety_minutes=15)["safety_minutes"] == 15.0)
ok("a negative safety_minutes is rejected, not clamped to 0",
   gates.update_gate("G001", safety_minutes=-5)["safety_minutes"] is None)
gates.update_gate("G001", safety_minutes=15)

# the partial-update trap the Locations PUT already hit
gates.update_gate("G001", note="weighbridge")
gates.update_gate("G001", active=True)
ok("a PATCH that omits a field does not blank it",
   gates.get_gate("G001")["note"] == "weighbridge")

ok("at most one default gate per location",
   [g_["id"] for g_ in gates.list_gates("L1") if g_["is_default"]] == []
   or len([g_ for g_ in gates.list_gates("L1") if g_["is_default"]]) <= 1)
gates.update_gate("G001", is_default=True)
gates.update_gate("G002", is_default=True)
ok("setting a second default clears the first",
   [g_["id"] for g_ in gates.list_gates("L1") if g_["is_default"]] == ["G002"])

ok("list_gates filters by location", {g_["id"] for g_ in gates.list_gates("L1")}
   == {"G001", "G002"})
gates.update_gate("G002", active=False)
ok("include_inactive=False drops a deactivated gate",
   {g_["id"] for g_ in gates.list_gates("L1", include_inactive=False)} == {"G001"})
gates.update_gate("G002", active=True)


# =========================================================================== #
#  3. Resolution — the per-leg asymmetric waypoint                             #
# =========================================================================== #
reset_db()
seed_two_locations()

# no gates at all: the legacy pair, then the node. This is the pre-5a behaviour and it
# is what keeps every already-baked leg valid on the day 5a ships.
r_entry = gates.resolve(loc("L1"), "entry")
ok("with no gates, resolution falls back to the legacy pair",
   (r_entry["lat"], r_entry["lon"]) == (58.51, 24.01))
ok("the legacy fallback is labelled as such", r_entry["source"] == "legacy_pair")
db.execute("UPDATE locations SET gate_lat = NULL, gate_lon = NULL "
           "WHERE tenant_id = ? AND id = ?", (db.current_tenant(), "L1"))
r_node = gates.resolve(loc("L1"), "entry")
ok("with no gates and no legacy pair, resolution falls back to the node",
   (r_node["lat"], r_node["lon"]) == (58.5, 24.00) and r_node["source"] == "node")
db.execute("UPDATE locations SET gate_lat = ?, gate_lon = ? "
           "WHERE tenant_id = ? AND id = ?", (58.51, 24.01, db.current_tenant(), "L1"))

gates.create_gate("L1", "In gate", 58.61, 24.11, direction="access")    # G001
gates.create_gate("L1", "Out gate", 58.41, 24.12, direction="egress")   # G002

ok("an access-only gate answers 'entry'",
   gates.resolve(loc("L1"), "entry")["gate_id"] == "G001")
ok("an access-only gate does NOT answer 'exit'",
   gates.resolve(loc("L1"), "exit")["gate_id"] == "G002")
ok("the two roles resolve to DIFFERENT points — the whole point of 5a",
   gates.resolve(loc("L1"), "entry")["lat"]
   != gates.resolve(loc("L1"), "exit")["lat"])
ok("a gate present but real is preferred over the legacy pair",
   gates.resolve(loc("L1"), "entry")["source"] == "default")
def _bad_role_raises():
    try:
        gates.resolve(loc("L1"), "sideways")
    except ValueError:
        return True
    return False


ok("an unknown role raises rather than silently defaulting to one of them",
   _bad_role_raises())

ok("serves() maps 'both' to either role",
   gates.serves({"direction": "both"}, "entry")
   and gates.serves({"direction": "both"}, "exit"))
ok("serves() maps 'access' to entry only",
   gates.serves({"direction": "access"}, "entry")
   and not gates.serves({"direction": "access"}, "exit"))

# an explicit selection wins over the default
gates.create_gate("L1", "Rail gate", 58.70, 24.20, direction="both")    # G003
ok("an explicitly selected gate wins over the default",
   gates.resolve(loc("L1"), "entry", "G003")["gate_id"] == "G003")
ok("an explicit selection is labelled 'selected'",
   gates.resolve(loc("L1"), "entry", "G003")["source"] == "selected")
# ...but only for the role it serves
ok("a selected gate that does not serve the role falls through to the default",
   gates.resolve(loc("L1"), "exit", "G001")["gate_id"] == "G002")
ok("the fall-through is labelled 'default', not 'selected'",
   gates.resolve(loc("L1"), "exit", "G001")["source"] == "default")

# is_default beats lowest-id
gates.update_gate("G003", is_default=True)
ok("the default gate beats the lowest id",
   gates.resolve(loc("L1"), "entry")["gate_id"] == "G003")


# =========================================================================== #
#  4. The bake path: the swap is direction-aware                               #
# =========================================================================== #
reset_db()
seed_two_locations()
gates.create_gate("L1", "In", 58.61, 24.11, direction="access")     # G001
gates.create_gate("L1", "Out", 58.41, 24.12, direction="egress")    # G002
gates.create_gate("L2", "In", 58.62, 25.11, direction="access")     # G003
gates.create_gate("L2", "Out", 58.42, 25.12, direction="egress")    # G004

CALLS = []


def _recorder(a_lat, a_lon, b_lat, b_lon, profile, steps, **kw):
    CALLS.append((round(a_lat, 5), round(a_lon, 5), round(b_lat, 5), round(b_lon, 5),
                  kw.get("laden")))
    return [{"geometry": [[a_lon, a_lat], [b_lon, b_lat]], "distance_km": 70.0,
             "duration_hr": 1.0}]


_real_rwh = haul.route_with_haul
haul.route_with_haul = _recorder
here_routing.configured = lambda: True

network.bake_route("R1", profile="P")

ok("both legs were routed", len(CALLS) == 2, f"got {len(CALLS)}")
loaded_call = [c for c in CALLS if c[4] is True]
return_call = [c for c in CALLS if c[4] is False]
ok("the loaded leg LEAVES the origin by its egress gate",
   loaded_call and loaded_call[0][0] == 58.41, str(loaded_call))
ok("the loaded leg ARRIVES at the destination by its access gate",
   loaded_call and loaded_call[0][2] == 58.62, str(loaded_call))
ok("the return leg LEAVES the destination by its egress gate",
   return_call and return_call[0][0] == 58.42, str(return_call))
ok("the return leg ARRIVES at the origin by its access gate",
   return_call and return_call[0][2] == 58.61, str(return_call))
# the bug this whole section exists to catch: a plain endpoint swap would make the
# return leg the reverse of the loaded one, sending the empty truck out of the entry
ok("the return leg is NOT the loaded leg reversed",
   loaded_call and return_call
   and (return_call[0][0], return_call[0][1]) != (loaded_call[0][2], loaded_call[0][3]))

# B2: a deactivated gate refuses the bake and NAMES the gate
gates.update_gate("G003", active=False)
db.execute("UPDATE routes SET dest_gate_id = ? WHERE tenant_id = ? AND id = ?",
           ("G003", db.current_tenant(), "R1"))
CALLS.clear()
res = network.bake_route("R1", profile="P")
ok("a route whose selected gate is deactivated refuses to bake", res.get("gate_blocked"))
ok("the refusal spends no HERE call", CALLS == [])
ok("the refusal names the gate", "G003" in (res.get("error") or ""), str(res.get("error")))
ok("the refusal names the location", "East site" in (res.get("error") or ""))
ok("the refusal says the gate is deactivated",
   "deactivated" in (res.get("error") or ""))
ok("the refusal is written onto the route so the list can show it",
   any("G003" in (g_["error"] or "") for g_ in db.query(
       "SELECT * FROM route_geometry WHERE tenant_id = ? AND route_id = ?",
       (db.current_tenant(), "R1"))))
ok("neither leg was left freshly baked while the other refused",
   all(not g_["geometry"] for g_ in db.query(
       "SELECT * FROM route_geometry WHERE tenant_id = ? AND route_id = ?",
       (db.current_tenant(), "R1"))))
ok("routes_status carries the blocker for the UI",
   any("G003" in b for row in network.routes_status()
       for b in row.get("gate_blockers", [])))

# a route pointing at a gate that has been DELETED is the same class of failure
db.execute("UPDATE routes SET dest_gate_id = ? WHERE tenant_id = ? AND id = ?",
           ("G999", db.current_tenant(), "R1"))
res = network.bake_route("R1", profile="P")
ok("a route pointing at a missing gate also refuses",
   res.get("gate_blocked") and "G999" in (res.get("error") or ""))

db.execute("UPDATE routes SET dest_gate_id = NULL WHERE tenant_id = ? AND id = ?",
           (db.current_tenant(), "R1"))
gates.update_gate("G003", active=True)
haul.route_with_haul = _real_rwh


# =========================================================================== #
#  5. 🔴 The turnaround split reproduces the old figure EXACTLY                #
# =========================================================================== #
#
# The kickoff note: "Assert the existing figure is reproducible from the three parts
# before changing any number." This is that assertion, and it is the reason a NULL
# rather than a 0 default was chosen for the two new minute columns.
reset_db()
seed_two_locations()
factors = conversions.load_factors()

profiles = [p for p in (factors.get("vehicles") or {}) if not p.startswith("_")]
ok("factors.json has vehicle profiles to check against", len(profiles) > 0,
   f"got {len(profiles)}")
for prof in profiles:
    load_m, unload_m = network._turnaround_minutes(prof, factors)
    parts = network._turnaround_parts(prof, factors, origin=loc("L1"), dest=loc("L2"),
                                      route=route())
    ok(f"[{prof}] with no gates, total == load + unload exactly",
       parts["total_minutes"] == load_m + unload_m,
       f"{parts['total_minutes']} vs {load_m + unload_m}")
    ok(f"[{prof}] with no gates, safety is 0", parts["safety_minutes"] == 0.0)
    ok(f"[{prof}] with no gates, internal travel is 0",
       parts["internal_travel_minutes"] == 0.0)
    ok(f"[{prof}] the parts sum to the total",
       parts["unloading_minutes"] + parts["safety_minutes"]
       + parts["internal_travel_minutes"] == parts["total_minutes"])
    ok(f"[{prof}] turnaround_hr is the total in hours",
       parts["turnaround_hr"] == parts["total_minutes"] / 60.0)

# the legacy migration must not move a number either: it creates gates with both new
# minute fields NULL, which is the whole reason it does not guess at them
made = gates.migrate_legacy_gates()
ok("the legacy gate migration creates one gate per surveyed pair", len(made) == 2,
   str(made))
ok("a migrated gate is 'both' — the single pair said nothing about direction",
   all(g_["direction"] == "both" for g_ in gates.list_gates()))
ok("a migrated gate is the default for its location",
   all(g_["is_default"] for g_ in gates.list_gates()))
ok("a migrated gate carries NO safety time — guessing one would move every cycle",
   all(g_["safety_minutes"] is None for g_ in gates.list_gates()))
ok("a migrated gate carries NO internal travel time",
   all(g_["internal_travel_minutes"] is None for g_ in gates.list_gates()))
ok("re-running the migration mints no second 'Main gate'",
   gates.migrate_legacy_gates() == [])
ok("the migrated gate sits on the old coordinate",
   gates.resolve(loc("L1"), "entry")["lat"] == 58.51)
ok("locations.gate_lat is NOT dropped — it is resolution step 4 and the rollback path",
   "gate_lat" in cols("locations"))

for prof in profiles:
    load_m, unload_m = network._turnaround_minutes(prof, factors)
    parts = network._turnaround_parts(prof, factors, origin=loc("L1"), dest=loc("L2"),
                                      route=route())
    ok(f"[{prof}] AFTER the legacy migration, total is still load + unload",
       parts["total_minutes"] == load_m + unload_m,
       f"{parts['total_minutes']} vs {load_m + unload_m}")

# and now a real induction time, charged at the ARRIVAL gate of each end, once per cycle
gate_ids = sorted(g_["id"] for g_ in gates.list_gates())
gates.update_gate(gate_ids[0], safety_minutes=10)
gates.update_gate(gate_ids[1], safety_minutes=5)
prof = profiles[0]
load_m, unload_m = network._turnaround_minutes(prof, factors)
parts = network._turnaround_parts(prof, factors, origin=loc("L1"), dest=loc("L2"),
                                  route=route())
ok("induction is charged at BOTH ends of the cycle", parts["safety_minutes"] == 15.0,
   str(parts["safety_minutes"]))
ok("induction lands in the total", parts["total_minutes"] == load_m + unload_m + 15.0)
ok("the unloading component is untouched by induction",
   parts["unloading_minutes"] == load_m + unload_m)


# =========================================================================== #
#  6. 🔴 B5 — internal travel is counted ONCE                                  #
# =========================================================================== #
#
# Phase 4 substitutes a drawn haul road's assigned speed into duration_hr, and
# route_analysis() reads duration_hr for cycle time. So a route with a drawn internal
# road already carries its internal minutes. Rule (c): the drawn road wins.
gates.update_gate(gate_ids[0], safety_minutes=None, internal_travel_minutes=6)
gates.update_gate(gate_ids[1], safety_minutes=None, internal_travel_minutes=4)

flat = network._turnaround_parts(prof, factors, origin=loc("L1"), dest=loc("L2"),
                                 route=route(), has_drawn_road=False)
ok("with no drawn road, the flat figure is used",
   flat["internal_travel_minutes"] == 10.0)
ok("the flat branch is labelled", flat["internal_travel_source"] == "flat")

drawn = network._turnaround_parts(prof, factors, origin=loc("L1"), dest=loc("L2"),
                                  route=route(), has_drawn_road=True)
ok("🔴 with a drawn road, the flat figure is NOT added on top",
   drawn["internal_travel_minutes"] == 0.0)
ok("the drawn-road branch is labelled",
   drawn["internal_travel_source"] == "drawn_road")
ok("the drawn-road total is SMALLER than the flat one — the double count is the bug",
   drawn["total_minutes"] < flat["total_minutes"])
ok("the suppressed flat figure is still reported, not silently dropped",
   drawn["internal_travel_flat_available"] == 10.0)
ok("'none' is used when there is no flat figure and no drawn road",
   network._turnaround_parts(prof, factors, origin=loc("L1"), dest=loc("L2"),
                             route=route())["internal_travel_source"] in
   ("flat", "none"))
ok("the three sources are the declared set",
   set(network.INTERNAL_TRAVEL_SOURCES) == {"drawn_road", "flat", "none"})

# and end to end through route_analysis, which is what the UI reads
add_geom("R1", leg="loaded", profile=prof, haul_tag=None)
add_geom("R1", leg="return", profile=prof, haul_tag=None)
rows = network.route_analysis("R1")["rows"]
ok("route_analysis returns a row", len(rows) == 1, str(len(rows)))
row = rows[0]
ok("route_analysis exposes the three parts", "turnaround_parts" in row)
ok("route_analysis's parts sum to its own total",
   round(row["turnaround_parts"]["unloading_minutes"]
         + row["turnaround_parts"]["internal_travel_minutes"]
         + row["turnaround_parts"]["safety_minutes"], 3)
   == row["turnaround_parts"]["total_minutes"])
ok("route_analysis's turnaround_hr IS the total, in hours",
   row["turnaround_hr"] == round(row["turnaround_parts"]["total_minutes"] / 60.0, 3))
ok("cycle_hr = out + back + turnaround, from the same total",
   row["cycle_hr"] == round(row["loaded_hr"] + row["return_hr"] + row["turnaround_hr"], 3))
ok("with no haul road on the geometry, the flat figure applies",
   row["turnaround_parts"]["internal_travel_source"] == "flat")

db.execute("DELETE FROM route_geometry WHERE tenant_id = ?", (db.current_tenant(),))
add_geom("R1", leg="loaded", profile=prof, haul_tag="Z001")
add_geom("R1", leg="return", profile=prof, haul_tag="Z001")
row = network.route_analysis("R1")["rows"][0]
ok("🔴 a leg baked THROUGH a haul road drops the flat internal figure",
   row["turnaround_parts"]["internal_travel_source"] == "drawn_road"
   and row["turnaround_parts"]["internal_travel_minutes"] == 0.0)

# ⚠️ the precedence reads the GEOMETRY, not the attachment: a haul road attached but
# not yet baked contributes nothing to duration_hr, so charging it as "already counted"
# would drop the flat figure for minutes nobody has
db.execute("DELETE FROM route_geometry WHERE tenant_id = ?", (db.current_tenant(),))
add_geom("R1", leg="loaded", profile=prof, haul_tag="")
add_geom("R1", leg="return", profile=prof, haul_tag="")
row = network.route_analysis("R1")["rows"][0]
ok("an unbaked haul attachment does NOT suppress the flat figure",
   row["turnaround_parts"]["internal_travel_source"] == "flat")


# =========================================================================== #
#  7. Tenant isolation                                                         #
# =========================================================================== #
reset_db()
seed_two_locations()
gates.create_gate("L1", "T1 gate", 58.61, 24.11, direction="both")

tok = db.set_current_tenant("other")
try:
    # tenant_id is explicit here: the column DEFAULTs to 'default', so an insert that
    # omits it lands in tenant one and collides on the key. That collision IS the
    # Phase 4.5 design working — the id is the same, the key is not.
    db.execute("INSERT INTO locations (tenant_id, id, name, lat, lon) "
               "VALUES (?, ?, ?, ?, ?)", ("other", "L1", "Other West", 59.5, 26.00))
    ok("a second tenant may hold the SAME location id", loc("L1")["name"] == "Other West")
    ok("tenant two sees none of tenant one's gates", gates.list_gates() == [])
    g2 = gates.create_gate("L1", "Other gate", 59.61, 26.11)
    ok("gate numbering restarts per tenant — G001 collides deliberately",
       g2["id"] == "G001", str(g2))
    ok("tenant two resolves to ITS OWN gate",
       gates.resolve(loc("L1"), "entry")["lat"] == 59.61)
finally:
    db.reset_current_tenant(tok)

ok("tenant one still sees exactly its own gate",
   [g_["id"] for g_ in gates.list_gates()] == ["G001"])
ok("tenant one's gate is its own coordinate",
   gates.resolve(loc("L1"), "entry")["lat"] == 58.61)
ok("routes_using is tenant-scoped", gates.routes_using("G001") == [])


# =========================================================================== #
#  8. Route gate selection + the re-bake contract                              #
# =========================================================================== #
reset_db()
seed_two_locations()
gates.create_gate("L1", "A", 58.61, 24.11, direction="both")   # G001
gates.create_gate("L2", "B", 58.62, 25.11, direction="both")   # G002

res = gates.set_route_gates("R1", origin_gate_id="G001", origin_given=True)
ok("a route's origin gate can be set", res["origin_gate_id"] == "G001")
ok("setting a gate reports that a re-bake is needed", res["rebake_needed"])
ok("the destination gate was NOT blanked by an origin-only write",
   res["dest_gate_id"] is None)
res = gates.set_route_gates("R1", dest_gate_id="G002", dest_given=True)
ok("an origin gate survives a destination-only write",
   res["origin_gate_id"] == "G001")
ok("writing the same value again reports no re-bake needed",
   not gates.set_route_gates("R1", origin_gate_id="G001",
                             origin_given=True)["rebake_needed"])
ok("pointing a route at a gate that does not exist is refused",
   "error" in gates.set_route_gates("R1", dest_gate_id="G404", dest_given=True))
ok("clearing a gate is allowed and needs a re-bake",
   gates.set_route_gates("R1", origin_gate_id=None,
                         origin_given=True)["rebake_needed"])
gates.set_route_gates("R1", origin_gate_id="G001", origin_given=True)

ok("routes_using finds both ends", sorted(gates.routes_using("G001")) == ["R1"])
d = gates.delete_gate("G001")
ok("deleting a gate a route still names is refused", "error" in d)
ok("the refusal lists the routes", d.get("routes") == ["R1"])
ok("the refusal offers deactivation instead", "deactivat" in (d.get("hint") or ""))
gates.set_route_gates("R1", origin_gate_id=None, origin_given=True)
ok("once no route names it, the gate deletes", gates.delete_gate("G001") ==
   {"deleted": "G001"})


# =========================================================================== #
#  9. Diagnostics                                                              #
# =========================================================================== #
reset_db()
seed_two_locations()
gates.create_gate("L1", "In", 58.61, 24.11, direction="access")    # G001
gates.create_gate("L1", "Out", 58.41, 24.12, direction="egress")   # G002
gates.create_gate("L2", "Both", 58.62, 25.11, direction="both")    # G003
here_routing.configured = lambda: True

diag = network.route_diagnostics("R1", profile="P")
ok("the diagnostic reports how the origin is LEFT",
   diag["origin"]["leaves_by"]["gate_id"] == "G002")
ok("the diagnostic reports how the origin is ARRIVED at",
   diag["origin"]["arrives_by"]["gate_id"] == "G001")
ok("the diagnostic flags an asymmetric end", diag["origin"]["asymmetric"])
ok("a symmetric end is not flagged asymmetric", not diag["destination"]["asymmetric"])
ok("the diagnostic keeps the pre-5a pair visible for comparison",
   diag["origin"]["gate"] == [58.51, 24.01])
ok("routed_to is the loaded leg's start", diag["origin"]["routed_to"] == [58.41, 24.12])
ok("the diagnostic carries the gate blockers", diag.get("gate_blockers") == [])


# =========================================================================== #
#  11. 🔴 The public map: the site marker is the SITE                          #
# =========================================================================== #
#
# Regression, found in the deployment on 2026-08-30. public_map_data() drew the node
# marker at _waypoint(), i.e. the access point. That was invisible while almost no
# location had a gate and wrong the moment one did: creating a gate visibly moved the
# quarry on the client-facing map.
reset_db()
seed_two_locations()
here_routing.configured = lambda: True
add_geom("R1", leg="loaded", profile="P")
add_geom("R1", leg="return", profile="P")

gates.create_gate("L1", "Far gate", 58.99, 24.99, direction="both", is_default=True)

fc = network.public_map_data(profile="P")
nodes = {f["properties"]["id"]: f for f in fc["features"]
         if f["properties"].get("type") == "Node"}
ok("the public map still emits a Node per location", len(nodes) == 2, str(len(nodes)))
ok("🔴 the site marker sits on the SITE, not on its gate",
   nodes["L1"]["geometry"]["coordinates"] == [24.00, 58.5],
   str(nodes["L1"]["geometry"]["coordinates"]))
ok("...and not on the legacy gate pair either",
   nodes["L1"]["geometry"]["coordinates"] != [24.01, 58.51])
ok("a location with no gates is unaffected",
   nodes["L2"]["geometry"]["coordinates"] == [25.00, 58.5])

gate_feats = [f for f in fc["features"] if f["properties"].get("type") == "Gate"]
ok("gates are emitted as their own features", len(gate_feats) == 1, str(len(gate_feats)))
gp = gate_feats[0]["properties"]
ok("the gate feature carries its own coordinate",
   gate_feats[0]["geometry"]["coordinates"] == [24.99, 58.99])
ok("the gate feature names the site it belongs to", gp["location_name"] == "West pit")
ok("the gate feature carries its direction", gp["direction"] == "both")
ok("the gate feature carries its active flag", gp["active"] is True)
# populateFilters() on the map sweeps EVERY feature for 'origin'/'dest' to build its
# dropdowns. A gate is not a routable endpoint and must not appear in them.
ok("a gate feature carries no 'origin' or 'dest' key",
   "origin" not in gp and "dest" not in gp)

gates.update_gate(gate_feats[0]["properties"]["gate_id"], active=False)
gate_feats = [f for f in network.public_map_data(profile="P")["features"]
              if f["properties"].get("type") == "Gate"]
ok("a deactivated gate is still drawn, flagged rather than dropped",
   len(gate_feats) == 1 and gate_feats[0]["properties"]["active"] is False)


# =========================================================================== #
#  12. Route alternatives — a SEPARATE collection, on purpose                  #
# =========================================================================== #
reset_db()
seed_two_locations()
add_geom("R1", leg="loaded", profile="P", alt=0)
add_geom("R1", leg="return", profile="P", alt=0)
add_geom("R1", leg="loaded", profile="P", alt=1)
add_geom("R1", leg="loaded", profile="P", alt=2)
add_geom("R1", leg="return", profile="P", alt=1)

alts = network.route_alternatives_geojson()
ok("alternatives are returned as a FeatureCollection",
   alts.get("type") == "FeatureCollection")
ok("every non-primary option is included, both legs",
   len(alts["features"]) == 3, str(len(alts["features"])))
ok("🔴 the PRIMARY option is NOT in it — that is the other endpoint's job",
   all(f["properties"]["alt_index"] > 0 for f in alts["features"]))
# 🔴 the type string is the whole safety mechanism: 'Inbound Highway' and
# 'Outbound Highway' are what the map's KPI count and leg toggles match on
ok("🔴 alternatives are typed 'Route Alternative', never either Highway string",
   {f["properties"]["type"] for f in alts["features"]} == {"Route Alternative"})
ok("each carries its route, leg and rank so a popup could name it",
   all({"route_id", "leg", "alt_index", "distance_km"} <= set(f["properties"])
       for f in alts["features"]))
ok("filtering by profile works", len(
   network.route_alternatives_geojson(profile="OTHER")["features"]) == 0)

# and the collection the map's five walkers DO read must be unchanged by any of it
main_fc = network.public_map_data(profile="P")
ok("🔴 no alternative leaks into public_map_data",
   not any(f["properties"].get("type") == "Route Alternative"
           for f in main_fc["features"]))
ok("...and it still emits only alt_index 0 geometry",
   len([f for f in main_fc["features"]
        if f["properties"].get("type", "").endswith("Highway")]) == 2,
   str(len([f for f in main_fc["features"]
            if f["properties"].get("type", "").endswith("Highway")])))

# an unbaked alternative (an error row) must not become an empty line
db.execute("INSERT INTO route_geometry (route_id, vehicle_profile, leg, alt_index, "
           "geometry, error) VALUES (?, ?, ?, ?, ?, ?)",
           ("R1", "P", "loaded", 3, None, "boom"))
ok("an alternative with no geometry is skipped, not drawn as nothing",
   len(network.route_alternatives_geojson()["features"]) == 3)


# =========================================================================== #
#  10. Source-level guards                                                     #
# =========================================================================== #
net_src = open(os.path.join(BACKEND, "network.py")).read()
main_src = open(os.path.join(BACKEND, "main.py")).read()
gates_src = open(os.path.join(BACKEND, "gates.py")).read()

import re as _re  # noqa: E402
# every real call passes at least `loc`; an empty match is prose mentioning
# `_waypoint()` in a comment, and the definition itself starts "loc, role"
calls = [m for m in _re.findall(r"_waypoint\(([^)]*)\)", net_src)
         if m.strip() and not m.startswith("loc, role")]
ok("every _waypoint call passes an explicit role",
   all(('"entry"' in c or '"exit"' in c or "role" in c) for c in calls),
   str([c for c in calls if not ('"entry"' in c or '"exit"' in c or "role" in c)]))
ok("_waypoint_full exists for callers that need the gate, not just the point",
   "def _waypoint_full(" in net_src)
ok("the startup creates the gates table",
   "db.init_gates_db()" in main_src)
ok("the gate table is created BEFORE the tenant migration",
   main_src.index("db.init_gates_db()") < main_src.index("db.init_tenant()"))
ok("the legacy gate migration runs AFTER the tenant migration",
   main_src.index("gates.migrate_legacy_gates()") > main_src.index("db.init_tenant()"))
ok("the gate write endpoints are admin-gated",
   main_src.count("_check_admin(token)") >= 4)
# Phase 4.5's design decision: the tenant is resolved by db.current_tenant() on a
# contextvar, so Phase 6 is ONE middleware rather than 139 signature changes. A new
# module that threads a tenant argument quietly undoes that.
ok("gates.py resolves the tenant centrally and never takes it as an argument",
   "db.current_tenant()" in gates_src
   and not _re.search(r"def \w+\([^)]*\btenant(_id)?\b", gates_src),
   str(_re.findall(r"def \w+\([^)]*\btenant(?:_id)?\b[^)]*\)", gates_src)))
ok("the B5 decision is written down next to the code that implements it",
   "B5" in net_src and "drawn road wins" in net_src)
ok("the 125% disagreement is cited where the total is computed",
   "125%" in net_src)
ok("the site marker no longer resolves through _waypoint()",
   'lat, lon = float(l["lat"]), float(l["lon"])' in net_src)
# the deploy that looked like a failed deploy: a cached index.html surviving a hard
# refresh, because FileResponse sends an ETag and no Cache-Control
ok("the app's index.html is served no-cache",
   'headers={"Cache-Control": "no-cache"}' in main_src)
# /map/ had the same exposure and is where it actually bit: a half-upgraded pair,
# new index.html with the old ipt_segments.js still running
ok("the map's static files are served no-cache too",
   "class NoCacheStatic(StaticFiles)" in main_src
   and 'NoCacheStatic(directory=str(ROOT / "map")' in main_src)
ok("...via get_response, not the internal file_response hook that fails silently",
   "async def get_response(self, path, scope)" in main_src
   and "def file_response" not in main_src)


# =========================================================================== #
print()
print(f"{PASS} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAIL:", f)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)

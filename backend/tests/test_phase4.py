"""
Phase 4 backend assertions — temporary haul roads.

Same harness shape as test_phase2.py / test_phase3.py: a scratch SQLite database, no
network, and `fastapi` / `flexpolyline` / `psycopg2` stubbed so main.py imports and its
endpoint functions can be called directly as plain Python.

WHAT THIS DOES NOT PROVE
------------------------
Read this before quoting the pass count. Phase 4's two load-bearing assumptions are both
about live HERE behaviour, and NEITHER is testable here:

  * **that HERE ignores `alternatives` when via-waypoints are present.** Every assertion
    below about via mode is about the query string this code builds, not about what HERE
    does with it. The claim comes from a code comment written during Phase 2 and has
    never been checked against the live service.
  * **that a stopover via splits the response into per-leg sections.** 'via' mode's whole
    speed substitution depends on it: without the split there is no HERE figure for the
    haul stretch alone. The code detects the failure and skips the substitution — that
    detection is tested, the underlying behaviour is not.

  Both are answered by /api/admin/diagnostics/haul-roads?route_id=...&probe=true against
  the real deployment. Until someone runs it, 'splice' is the only mode with a known
  failure mode, which is why it is the default.

Also unexercised, as in every previous phase:
  * HERE is never called. `here_routing.routes` is monkeypatched to a recorder.
  * The HTTP layer is stubbed — endpoint BODIES run, the transport does not.
  * Every Postgres branch of db.py. The Phase 4 DDL runs against SQLite only.
  * Nothing in a browser. The haul-road drawing mode, the route panel and the map layer
    are asserted at source level in parse_frontend.js / parse_map.js.

Run:  python3 backend/tests/test_phase4.py
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

TMP = tempfile.mkdtemp(prefix="rbe_phase4_")
os.environ.pop("DATABASE_URL", None)

import db  # noqa: E402
db._SQLITE_PATH = os.path.join(TMP, "scratch.db")

import conversions  # noqa: E402
import zones  # noqa: E402
import haul  # noqa: E402
import network  # noqa: E402
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
# A haul road running due east at latitude 58.5, from 24.50 to 24.60 -- about 5.8 km.
ROAD = {"type": "LineString", "coordinates": [[24.50, 58.5], [24.55, 58.5], [24.60, 58.5]]}
ROAD2 = {"type": "LineString", "coordinates": [[24.80, 58.5], [24.90, 58.5]]}
BOX = {"type": "Polygon", "coordinates": [[[24.4, 58.4], [24.6, 58.4],
                                           [24.6, 58.6], [24.4, 58.6], [24.4, 58.4]]]}


def seed_two_locations():
    """O at the west end, D at the east end, so the loaded leg meets the road head-on."""
    db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)",
               ("L1", "West pit", 58.5, 24.00))
    db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)",
               ("L2", "East site", 58.5, 25.00))
    db.execute("INSERT INTO routes (id, origin_id, dest_id, origin_temp_km) "
               "VALUES (?, ?, ?, ?)", ("R1", "L1", "L2", 0))


def add_geom(route_id, leg="loaded", profile="P", alt=0, haul_tag=None):
    db.execute(
        "INSERT INTO route_geometry (route_id, vehicle_profile, leg, alt_index, geometry, "
        "distance_km, duration_hr, zones_applied, haul_zones) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (route_id, profile, leg, alt, json.dumps([[24.0, 58.5], [25.0, 58.5]]),
         70.0, 1.0, "", haul_tag))


# =========================================================================== #
#  1. Schema                                                                   #
# =========================================================================== #
reset_db()

ok("route_haul_roads table is created", "route_haul_roads" in tables())
rh = cols("route_haul_roads")
for c in ("route_id", "zone_id", "seq", "created_at"):
    ok(f"route_haul_roads has {c}", c in rh)
ok("route_haul_roads is keyed on (route_id, zone_id)",
   rh["route_id"][5] and rh["zone_id"][5])
ok("seq is not part of the key — reordering must not need a delete",
   not rh["seq"][5])

zc = cols("zones")
ok("zones gains speed_kph", "speed_kph" in zc)
ok("zones gains haul_mode", "haul_mode" in zc)

gc = cols("route_geometry")
for c in ("haul_zones", "haul_km", "duration_hr_here"):
    ok(f"route_geometry gains {c}", c in gc)
ok("zones_applied survived the Phase 4 migration", "zones_applied" in gc)

# the migration must be re-runnable: Render runs it on every boot
db.init_zones_db()
db.init_gates_db()     # Phase 5a
db.init_network_db()
ok("re-running the migrations is idempotent",
   "route_haul_roads" in tables() and "haul_km" in cols("route_geometry"))


# =========================================================================== #
#  2. Geometry maths                                                           #
# =========================================================================== #
# 0.1 degrees of longitude at 58.5N is 111.32 * cos(58.5) * 0.1 = 5.81 km
expect_km = 111.32 * math.cos(math.radians(58.5)) * 0.1
got = zones.line_length_km(ROAD["coordinates"])
ok("line_length_km matches the great-circle figure to 1%",
   abs(got - expect_km) / expect_km < 0.01, f"{got:.3f} vs {expect_km:.3f}")

ok("a two-point line has length", zones.line_length_km([[24.0, 58.5], [24.1, 58.5]]) > 0)
ok("a one-point line has zero length", zones.line_length_km([[24.0, 58.5]]) == 0.0)
ok("an empty line has zero length", zones.line_length_km([]) == 0.0)

ok("as_line accepts a LineString", zones.as_line(ROAD) == ROAD["coordinates"])
ok("as_line REFUSES a Polygon — a ring has no entry or exit",
   zones.as_line(BOX) is None)
ok("as_line takes the first part of a MultiLineString",
   zones.as_line({"type": "MultiLineString",
                  "coordinates": [ROAD["coordinates"], ROAD2["coordinates"]]})
   == ROAD["coordinates"])
ok("as_line rejects a degenerate line",
   zones.as_line({"type": "LineString", "coordinates": [[24.0, 58.5]]}) is None)

line, rev = zones.oriented_line(ROAD, [24.0, 58.5])       # approaching from the west
ok("approaching from the west enters at the west end", line[0] == [24.50, 58.5])
ok("and is not reversed", rev is False)
line, rev = zones.oriented_line(ROAD, [25.0, 58.5])       # approaching from the east
ok("approaching from the east enters at the east end", line[0] == [24.60, 58.5])
ok("and is reported reversed", rev is True)
ok("orientation does not change the length",
   abs(zones.line_length_km(line) - got) < 1e-9)


# =========================================================================== #
#  3. A haul road is never an avoid area                                       #
# =========================================================================== #
# This is the bug Phase 4 had to fix before it could add anything: 'haul_road' was
# already in KINDS with affects_routing defaulting TRUE, so a haul road drawn against
# the Phase 3 build would have gone to HERE as a box to AVOID -- telling the router to
# steer clear of the road it is meant to use.
reset_db()
seed_two_locations()
zones.create_zone("Closure", BOX, kind="closure", affects_routing=True)
zones.create_zone("Haul A", ROAD, kind="haul_road", affects_routing=True, speed_kph=25)

ok("the closure is in the avoid list", len(zones.avoid_areas()) == 1)
ok("the haul road is NOT in the avoid list, despite affects_routing=True",
   all("Z002" not in a for a in zones.avoid_areas()) and len(zones.avoid_areas()) == 1)
ok("routing_zones() excludes haul roads by kind",
   [z["id"] for z in zones.routing_zones()] == ["Z001"])
ok("haul_roads() finds it", [z["id"] for z in zones.haul_roads()] == ["Z002"])
ok("network.active_avoid() therefore never tags a haul road",
   "Z002" not in network.active_avoid()[1])
ok("and the tag still names the closure", network.active_avoid()[1] == "Z001")


# =========================================================================== #
#  4. Haul-road validation                                                     #
# =========================================================================== #
bad = zones.create_zone("Ring road", BOX, kind="haul_road")
ok("a haul road drawn as a Polygon is refused", "error" in bad)
ok("and the message says why", "LineString" in bad.get("error", ""))

ok("a non-haul zone may still be a Polygon",
   "error" not in zones.create_zone("Works", BOX, kind="works"))

z = zones.get_zone("Z002")
ok("speed_kph round-trips", z["speed_kph"] == 25.0)
ok("length_km is computed on read", abs(z["length_km"] - expect_km) < 0.05)
ok("haul_mode is null until set — the default lives in haul.py, not the row",
   z["haul_mode"] is None)

ok("zero speed is stored as null, not zero — dividing by it is the bug",
   zones.update_zone("Z002", speed_kph=0)["speed_kph"] is None)
ok("a negative speed is null too", zones.update_zone("Z002", speed_kph=-5)["speed_kph"] is None)
ok("a non-numeric speed is null", zones.update_zone("Z002", speed_kph="fast")["speed_kph"] is None)
ok("a real speed is kept", zones.update_zone("Z002", speed_kph=22.5)["speed_kph"] == 22.5)
ok("an unknown haul_mode is refused", zones.update_zone("Z002", haul_mode="teleport")["haul_mode"] is None)
ok("'via' is accepted", zones.update_zone("Z002", haul_mode="via")["haul_mode"] == "via")
ok("'splice' is accepted", zones.update_zone("Z002", haul_mode="splice")["haul_mode"] == "splice")

# the case a per-field check waves through: an existing Polygon retyped as a haul road
ok("re-typing an existing Polygon zone to haul_road is refused",
   "error" in zones.update_zone("Z003", kind="haul_road"))
ok("and the polygon zone is unchanged", zones.get_zone("Z003")["kind"] == "works")


# =========================================================================== #
#  5. Attach / detach / order                                                  #
# =========================================================================== #
reset_db()
seed_two_locations()
zones.create_zone("Haul A", ROAD, kind="haul_road", speed_kph=25)      # Z001
zones.create_zone("Haul B", ROAD2, kind="haul_road", speed_kph=15)     # Z002
zones.create_zone("Closure", BOX, kind="closure")                      # Z003

ok("attaching an unknown route fails", "error" in haul.attach("NOPE", "Z001"))
ok("attaching an unknown zone fails", "error" in haul.attach("R1", "Z999"))
ok("attaching a CLOSURE as a haul road is refused — it would mean 'route through the "
   "closed area'", "error" in haul.attach("R1", "Z003"))

ok("attaching works", "attached" in haul.attach("R1", "Z001"))
ok("attaching twice is refused", "error" in haul.attach("R1", "Z001"))
ok("a second road appends after the first", haul.attach("R1", "Z002")["attached"]["seq"] == 1)
ok("links_for_route returns them in order",
   [l["zone_id"] for l in haul.links_for_route("R1")] == ["Z001", "Z002"])
ok("routes_for_zone is the reverse lookup", haul.routes_for_zone("Z001") == ["R1"])
ok("link_counts counts them", haul.link_counts() == {"Z001": 1, "Z002": 1})

ok("reorder swaps them", haul.reorder("R1", ["Z002", "Z001"])["order"] == ["Z002", "Z001"])
ok("and the new order sticks",
   [l["zone_id"] for l in haul.links_for_route("R1")] == ["Z002", "Z001"])
ok("reorder refuses a partial list — a silent drop would change the route",
   "error" in haul.reorder("R1", ["Z001"]))
ok("reorder refuses an unattached road", "error" in haul.reorder("R1", ["Z001", "Z002", "Z003"]))
haul.reorder("R1", ["Z001", "Z002"])

ok("detaching works", "detached" in haul.detach("R1", "Z002"))
ok("detaching twice fails", "error" in haul.detach("R1", "Z002"))
haul.attach("R1", "Z002")


# =========================================================================== #
#  6. Planning a leg                                                           #
# =========================================================================== #
O, D = [24.00, 58.5], [25.00, 58.5]
plan = haul.plan_leg("R1", O, D, "loaded")
ok("both roads are on the loaded plan", [s["zone_id"] for s in plan] == ["Z001", "Z002"])
ok("the first road is entered at its west end", plan[0]["entry"] == (58.5, 24.50))
ok("and left at its east end", plan[0]["exit"] == (58.5, 24.60))
ok("entry/exit are (lat, lon) for HERE, not GeoJSON order",
   plan[0]["entry"][0] == 58.5 and plan[0]["entry"][1] == 24.50)
ok("the drawn length is carried", abs(plan[0]["length_km"] - expect_km) < 0.05)

rplan = haul.plan_leg("R1", D, O, "return")
ok("the return leg meets them in the opposite order",
   [s["zone_id"] for s in rplan] == ["Z002", "Z001"])
ok("and enters the first at its EAST end", rplan[0]["entry"] == (58.5, 24.90))
ok("which is reported as a reversed traversal", rplan[0]["reversed"] is True)

zones.update_zone("Z002", active=False)
ok("an inactive haul road drops out of the plan",
   [s["zone_id"] for s in haul.plan_leg("R1", O, D, "loaded")] == ["Z001"])
zones.update_zone("Z002", active=True)

zones.update_zone("Z002", starts_on="2099-01-01")
ok("a haul road that has not opened yet drops out — the date range IS the 'not built "
   "yet' flag", [s["zone_id"] for s in haul.plan_leg("R1", O, D, "loaded")] == ["Z001"])
zones.update_zone("Z002", starts_on=None)

zones.update_zone("Z002", affects_routing=False)
ok("an advisory haul road is drawn but not routed through",
   [s["zone_id"] for s in haul.plan_leg("R1", O, D, "loaded")] == ["Z001"])
zones.update_zone("Z002", affects_routing=True)

ok("a route with no links has an empty plan", haul.plan_leg("NOPE", O, D, "loaded") == [])


# =========================================================================== #
#  7. origin_temp_km becomes derived                                           #
# =========================================================================== #
# It was seeded from V2, displayed in routes_status(), and computed with NOWHERE
# (confirmed by grep before Phase 4 started). It now means "km of the loaded leg on
# drawn haul road".
temp = db.query("SELECT origin_temp_km FROM routes WHERE id = 'R1'")[0]["origin_temp_km"]
both = zones.line_length_km(ROAD["coordinates"]) + zones.line_length_km(ROAD2["coordinates"])
ok("attach() refreshes it itself — the invariant does not depend on the endpoint "
   "remembering to", abs(temp - both) < 0.05, str(temp))

haul.refresh_origin_temp_km("R1")
ok("and refreshing again is idempotent",
   abs(db.query("SELECT origin_temp_km FROM routes WHERE id = 'R1'")[0]["origin_temp_km"]
       - both) < 0.05)

haul.detach("R1", "Z002")
ok("detaching a road reduces it",
   abs(db.query("SELECT origin_temp_km FROM routes WHERE id = 'R1'")[0]["origin_temp_km"]
       - zones.line_length_km(ROAD["coordinates"])) < 0.05)

db.execute("DELETE FROM route_haul_roads WHERE route_id = 'R1'")
haul.refresh_origin_temp_km()
ok("a route with no haul roads reads 0, not the V2 seed value",
   db.query("SELECT origin_temp_km FROM routes WHERE id = 'R1'")[0]["origin_temp_km"] == 0)
haul.attach("R1", "Z001")


# =========================================================================== #
#  8. Routing a leg — splice mode (HERE is a recorder, never called)            #
# =========================================================================== #
_real_routes = here_routing.routes
_real_conf = here_routing.configured
_calls = []


def _fake_routes(o_lat, o_lon, d_lat, d_lon, profile, factors=None, avoid_areas=None,
                 alternatives=1, laden=True, via=None, pass_through=False):
    _calls.append({"o": (o_lat, o_lon), "d": (d_lat, d_lon), "avoid": avoid_areas,
                   "alternatives": alternatives, "via": via, "laden": laden,
                   "pass_through": pass_through})
    # 10 km / 0.25 h per hop, and one section per via waypoint plus one
    n = len(via or []) + 1
    geom = [[o_lon, o_lat], [d_lon, d_lat]]
    secs = [{"geometry": geom, "distance_km": 10.0 / n, "duration_hr": 0.25 / n}
            for _ in range(n)]
    return [{"geometry": geom, "distance_km": 10.0, "duration_hr": 0.25, "sections": secs}]


here_routing.routes = _fake_routes
here_routing.configured = lambda: True

try:
    _calls.clear()
    plan = haul.plan_leg("R1", O, D, "loaded")
    res = haul.route_with_haul(58.5, 24.0, 58.5, 25.0, "P", plan, alternatives=3)

    ok("splice mode is the default when no haul_mode is set", res[0]["haul_mode"] == "splice")
    ok("one haul road costs TWO HERE calls, not one", len(_calls) == 2)
    ok("and reports that count", res[0]["haul_calls"] == 2)
    ok("the first call runs to the road's entry point", _calls[0]["d"] == (58.5, 24.50))
    ok("the second starts from its exit point", _calls[1]["o"] == (58.5, 24.60))
    ok("each sub-call asks for ONE option — alternatives on a sub-leg are not "
       "alternatives on the route", all(c["alternatives"] == 1 for c in _calls))
    ok("only one option comes back even though three were asked for", len(res) == 1)

    ok("the distance is both HERE hops plus the drawn road",
       abs(res[0]["distance_km"] - (20.0 + expect_km)) < 0.05, str(res[0]["distance_km"]))
    ok("haul_km is the drawn length", abs(res[0]["haul_km"] - expect_km) < 0.05)
    ok("duration_hr_here is HERE's own total, with no haul time in it",
       abs(res[0]["duration_hr_here"] - 0.5) < 1e-6, str(res[0]["duration_hr_here"]))
    ok("duration_hr adds the assigned-speed time on top",
       abs(res[0]["duration_hr"] - (0.5 + expect_km / 25.0)) < 0.001,
       str(res[0]["duration_hr"]))
    ok("the adjusted duration is LONGER than HERE's here — 25 km/h on a haul road is "
       "slow", res[0]["duration_hr"] > res[0]["duration_hr_here"])
    ok("the zone ids applied are recorded", res[0]["haul_zones"] == ["Z001"])
    ok("the geometry runs origin -> road -> destination",
       len(res[0]["geometry"]) >= 5)
    ok("and contains the drawn road's own vertices",
       [24.55, 58.5] in res[0]["geometry"])

    d = res[0]["haul_detail"][0]
    ok("haul_detail names the road", d["zone_id"] == "Z001")
    ok("haul_detail has no HERE duration for a spliced stretch — HERE never saw it",
       d["here_duration_hr"] is None)
    ok("haul_detail carries the applied duration",
       abs(d["applied_duration_hr"] - expect_km / 25.0) < 0.001)

    # a road with no speed
    zones.update_zone("Z001", speed_kph=None)
    _calls.clear()
    plan = haul.plan_leg("R1", O, D, "loaded")
    res = haul.route_with_haul(58.5, 24.0, 58.5, 25.0, "P", plan)
    ok("a haul road with NO speed contributes no time — null is not a default speed",
       abs(res[0]["duration_hr"] - res[0]["duration_hr_here"]) < 1e-9)
    ok("and says so rather than leaving a silent zero",
       "no speed assigned" in (res[0]["haul_detail"][0]["note"] or ""))
    ok("but its distance still counts", res[0]["haul_km"] > 0)
    zones.update_zone("Z001", speed_kph=25)

    # two roads
    haul.attach("R1", "Z002")
    _calls.clear()
    plan = haul.plan_leg("R1", O, D, "loaded")
    res = haul.route_with_haul(58.5, 24.0, 58.5, 25.0, "P", plan)
    ok("two spliced roads cost THREE HERE calls", len(_calls) == 3)
    ok("the hops chain in traversal order",
       _calls[0]["d"] == (58.5, 24.50) and _calls[1]["o"] == (58.5, 24.60)
       and _calls[1]["d"] == (58.5, 24.80) and _calls[2]["o"] == (58.5, 24.90))
    ok("both roads are recorded", res[0]["haul_zones"] == ["Z001", "Z002"])
    ok("haul_km sums both", abs(res[0]["haul_km"] - both) < 0.05)

    # no haul roads at all -> the pre-Phase-4 path, unchanged
    _calls.clear()
    res = haul.route_with_haul(58.5, 24.0, 58.5, 25.0, "P", [], alternatives=3)
    ok("with no haul roads it is a plain single HERE call", len(_calls) == 1)
    ok("and alternatives are passed straight through", _calls[0]["alternatives"] == 3)
    ok("and no via waypoints are sent", _calls[0]["via"] is None)
    ok("and nothing Phase 4 is attached to the result", "haul_km" not in res[0])

    # ------------------------------------------------------------------ via mode
    haul.detach("R1", "Z002")
    zones.update_zone("Z001", haul_mode="via")
    _calls.clear()
    plan = haul.plan_leg("R1", O, D, "loaded")
    res = haul.route_with_haul(58.5, 24.0, 58.5, 25.0, "P", plan)
    ok("via mode is ONE HERE call", len(_calls) == 1)
    ok("and sends the road's two ends as via waypoints",
       _calls[0]["via"] == [(58.5, 24.50), (58.5, 24.60)])
    ok("as STOPOVERS, not pass-through — the section split is what the speed model "
       "reads", _calls[0]["pass_through"] is False)
    ok("the split is detected", res[0]["sections_split_at_vias"] is True)
    ok("via mode substitutes rather than adds: HERE's own section time is removed",
       abs(res[0]["duration_hr"]
           - (0.25 - 0.25 / 3 + (10.0 / 3) / 25.0)) < 0.001, str(res[0]["duration_hr"]))
    ok("and the haul length is HERE's measured section, not the drawn line",
       abs(res[0]["haul_km"] - 10.0 / 3) < 0.001)
    ok("haul_detail keeps HERE's own figure alongside the applied one",
       res[0]["haul_detail"][0]["here_duration_hr"] is not None
       and res[0]["haul_detail"][0]["applied_duration_hr"] is not None)

    # HERE refusing to split is the failure this mode has to survive
    def _no_split(*a, **k):
        _calls.append({"via": k.get("via"), "alternatives": k.get("alternatives", 1)})
        geom = [[24.0, 58.5], [25.0, 58.5]]
        return [{"geometry": geom, "distance_km": 10.0, "duration_hr": 0.25,
                 "sections": [{"geometry": geom, "distance_km": 10.0, "duration_hr": 0.25}]}]

    here_routing.routes = _no_split
    res = haul.route_with_haul(58.5, 24.0, 58.5, 25.0, "P", plan)
    ok("an unsplit response is detected", res[0]["sections_split_at_vias"] is False)
    ok("and NO speed substitution is applied to a section that might be the whole route",
       res[0]["duration_hr"] == res[0]["duration_hr_here"])
    ok("and the response says why in words", "did not split" in (res[0].get("haul_note") or ""))
    ok("the per-road note says it too",
       "did not split" in (res[0]["haul_detail"][0]["note"] or ""))
    here_routing.routes = _fake_routes

    # mixed modes on one leg
    zones.update_zone("Z002", haul_mode="splice")
    haul.attach("R1", "Z002")
    _calls.clear()
    plan = haul.plan_leg("R1", O, D, "loaded")
    res = haul.route_with_haul(58.5, 24.0, 58.5, 25.0, "P", plan)
    ok("mixing modes on one leg falls back to splicing everything",
       res[0]["haul_mode"] == "splice")
    ok("and says so rather than downgrading silently",
       "spliced instead" in (res[0].get("haul_note") or ""))
    haul.detach("R1", "Z002")
    zones.update_zone("Z001", haul_mode="splice")

    # ------------------------------------------------------- through the bake path
    network.clear_geometry()
    _calls.clear()
    out = network.bake_batch(profile="P", limit=10)
    ok("the bake path routes through the haul road", len(_calls) == 4,
       f"{len(_calls)} calls for 2 legs x 2 hops")
    ok("both legs are counted as baked", out["baked"] == 2)
    ok("the batch reports the HERE calls it really spent, not the leg count",
       out["here_calls"] == 4, str(out.get("here_calls")))
    ok("and how many legs went through a haul road", out["haul_legs"] == 2)

    rows = db.query("SELECT * FROM route_geometry ORDER BY leg")
    ok("haul_zones is stamped on the stored rows",
       all(r["haul_zones"] == "Z001" for r in rows), str([r["haul_zones"] for r in rows]))
    ok("haul_km is stored", all(r["haul_km"] and r["haul_km"] > 0 for r in rows))
    ok("duration_hr_here is stored next to the adjusted duration",
       all(r["duration_hr_here"] is not None for r in rows))
    ok("the adjusted duration is what lands in duration_hr",
       all(r["duration_hr"] > r["duration_hr_here"] for r in rows))
    ok("only one alternative is stored for a haul leg",
       len([r for r in rows if r["leg"] == "loaded"]) == 1)

    # a leg with no haul road at all stamps '' -- 'baked with none', not 'unknown'
    db.execute("DELETE FROM route_haul_roads")
    network.clear_geometry()
    network.bake_batch(profile="P", limit=10)
    r0 = db.query("SELECT * FROM route_geometry LIMIT 1")[0]
    ok("a leg baked with no haul road stamps '', not NULL", r0["haul_zones"] == "")
    ok("which is distinguishable from a pre-Phase-4 NULL", r0["haul_zones"] is not None)
    ok("and duration_hr_here stays NULL — nothing was substituted",
       r0["duration_hr_here"] is None)
    haul.attach("R1", "Z001")

finally:
    here_routing.routes = _real_routes
    here_routing.configured = _real_conf


# =========================================================================== #
#  9. Invalidation is exact                                                    #
# =========================================================================== #
reset_db()
seed_two_locations()
zones.create_zone("Haul A", ROAD, kind="haul_road", speed_kph=25)   # Z001
zones.create_zone("Haul B", ROAD2, kind="haul_road", speed_kph=25)  # Z002
db.execute("INSERT INTO routes (id, origin_id, dest_id, origin_temp_km) VALUES (?,?,?,?)",
           ("R2", "L1", "L2", 0))
haul.attach("R1", "Z001")

add_geom("R1", "loaded", haul_tag="Z001")
add_geom("R1", "return", haul_tag="Z001")
add_geom("R2", "loaded", haul_tag="")

inv = haul.invalidate_for_zone("Z001", dry_run=True)
ok("a dry run clears nothing", inv["cleared"] == 0 and inv["dry_run"] is True)
ok("it finds both legs baked through the road", inv["leg_count"] == 2)
ok("and not the route that never used it", inv["route_ids"] == ["R1"])
ok("nothing about it is approximate — attachment is a table lookup, not a proximity "
   "test", inv["approximate"] == 0)
ok("the reason is recorded per leg",
   all("baked through this haul road" in l["reason"] for l in inv["legs"]))
ok("the geometry is still there after a dry run",
   len(db.query("SELECT * FROM route_geometry")) == 3)

ok("here_calls counts the splices, not the legs — 2 legs x (1 road + 1) = 4",
   inv["here_calls"] == 4, str(inv["here_calls"]))

inv = haul.invalidate_for_zone("Z001")
ok("a real run clears them", inv["cleared"] == 2)
ok("R2's geometry is untouched",
   [r["route_id"] for r in db.query("SELECT * FROM route_geometry")] == ["R2"])

# a route that has just been given a road, but whose legs were baked without it
add_geom("R1", "loaded", haul_tag="")
add_geom("R1", "return", haul_tag="")
inv = haul.invalidate_for_zone("Z001", dry_run=True)
ok("legs on an attached route are invalidated even if they were baked without the road",
   inv["leg_count"] == 2)
ok("with the other reason given",
   all("now uses this haul road" in l["reason"] for l in inv["legs"]))

inv = haul.invalidate_for_route("R1", dry_run=True)
ok("invalidate_for_route finds every leg of the route", inv["leg_count"] == 2)
ok("and none of another route's", inv["route_ids"] == ["R1"])

ok("a haul road nobody uses invalidates nothing",
   haul.invalidate_for_zone("Z002", dry_run=True)["leg_count"] == 0)


# =========================================================================== #
#  10. Endpoints                                                               #
# =========================================================================== #
reset_db()
seed_two_locations()


class _Body(dict):
    """Stand-in for a pydantic model: attribute access plus __fields_set__."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    @property
    def __fields_set__(self):
        return set(self.keys())


def zone_body(**kw):
    b = {"name": "Z", "geometry": ROAD, "kind": "haul_road", "affects_routing": True,
         "starts_on": None, "ends_on": None, "note": None, "active": True,
         "speed_kph": None, "haul_mode": None}
    b.update(kw)
    return _Body(b)


created = main.create_zone(zone_body(name="Haul A", speed_kph=25))
ok("POST /api/admin/zones creates a haul road", created["zone"]["kind"] == "haul_road")
ok("with its speed", created["zone"]["speed_kph"] == 25.0)
ok("a NEW haul road invalidates nothing — it is attached to no route yet",
   created["invalidated"]["leg_count"] == 0)
ok("so no re-bake is demanded", created["rebake_required"] is False)

zid = created["zone"]["id"]
add_geom("R1", "loaded", haul_tag="")
add_geom("R1", "return", haul_tag="")

res = main.attach_haul_road("R1", _Body({"zone_id": zid, "seq": None}))
ok("POST attaches the road to the route", res["attached"]["zone_id"] == zid)
ok("attaching clears the route's baked legs", res["invalidated"]["leg_count"] == 2)
ok("and demands a re-bake", res["rebake_required"] is True)
ok("quoting the real HERE cost", res["here_calls"] == 4, str(res["here_calls"]))
ok("attaching refreshes origin_temp_km",
   db.query("SELECT origin_temp_km FROM routes WHERE id='R1'")[0]["origin_temp_km"] > 0)

try:
    main.attach_haul_road("R1", _Body({"zone_id": zid, "seq": None}))
    ok("attaching twice raises", False)
except _HTTPException as e:
    ok("attaching twice raises 400", e.status_code == 400)

got = main.route_haul_roads("R1")
ok("GET /api/routes/{id}/haul-roads returns the plan for both legs",
   set(got["plan"].keys()) == {"loaded", "return"})
ok("with the assigned duration worked out, without calling HERE",
   got["plan"]["loaded"][0]["assigned_duration_hr"] > 0)
ok("and the HERE cost of a full re-bake", got["here_calls_per_bake"] == 4)
ok("the diagnostic names what it has NOT verified", len(got["unverified"]) >= 3)
ok("including the alternatives question",
   any("alternatives" in u for u in got["unverified"]))
ok("and the section-split question",
   any("section" in u for u in got["unverified"]))

imp = main.zone_impact(zid)
ok("/impact on a haul road lists the routes attached", imp["routes_attached"] == ["R1"])
ok("and dry-runs the invalidation", imp["would_invalidate"]["dry_run"] is True)

add_geom("R1", "loaded", haul_tag=zid)
upd = main.update_zone(zid, zone_body(name="Haul A", speed_kph=40))
ok("PUT on a haul road uses the exact haul invalidation",
   upd["invalidated"]["approximate"] == 0)
ok("editing the speed invalidates the legs baked through it",
   upd["invalidated"]["leg_count"] >= 1)

deleted = main.delete_zone(zid)
ok("DELETE reports how many routes it was detached from",
   deleted["detached_from_routes"] == 1)
ok("and the link is gone", haul.link_counts() == {})
ok("and origin_temp_km falls back to 0",
   db.query("SELECT origin_temp_km FROM routes WHERE id='R1'")[0]["origin_temp_km"] == 0)

lst = main.list_haul_roads()
ok("GET /api/haul-roads reports the summary", "summary" in lst)
ok("and the default mode", lst["summary"]["default_mode"] == "splice")

meta = main.list_zones()
ok("/api/zones exposes the haul modes", meta["haul_modes"] == list(haul.MODES))
ok("and names the haul kind so the UI does not hard-code it",
   meta["haul_kind"] == "haul_road")


# =========================================================================== #
#  11. promote_alternative on a haul leg                                       #
# =========================================================================== #
reset_db()
seed_two_locations()
zones.create_zone("Haul A", ROAD, kind="haul_road", speed_kph=25)
haul.attach("R1", "Z001")
add_geom("R1", "loaded", haul_tag="Z001")

res = network.promote_alternative("R1", "P", 1, leg="loaded")
ok("promoting on a haul leg fails", "error" in res)
ok("and explains it is the haul road, not a cache miss",
   "haul road" in res["error"], res.get("error", ""))
ok("naming the road", res.get("haul_zones") == ["Z001"])
ok("and telling the user how to get alternatives back",
   "Detach" in res["error"])

db.execute("DELETE FROM route_geometry")
add_geom("R2" if False else "R1", "loaded", haul_tag="")
res = network.promote_alternative("R1", "P", 1, leg="loaded")
ok("a non-haul leg still gets the plain message",
   "error" in res and "haul road" not in res["error"])


# =========================================================================== #
#  12. Cycle time actually moves                                               #
# =========================================================================== #
reset_db()
seed_two_locations()
db.execute(
    "INSERT INTO route_geometry (route_id, vehicle_profile, leg, alt_index, geometry, "
    "distance_km, duration_hr, haul_zones, haul_km, duration_hr_here) "
    "VALUES (?,?,?,?,?,?,?,?,?,?)",
    ("R1", "Artic Tipper (44t)", "loaded", 0, json.dumps([[24.0, 58.5], [25.0, 58.5]]),
     70.0, 1.50, "Z001", 5.8, 1.20))
db.execute(
    "INSERT INTO route_geometry (route_id, vehicle_profile, leg, alt_index, geometry, "
    "distance_km, duration_hr, haul_zones, haul_km, duration_hr_here) "
    "VALUES (?,?,?,?,?,?,?,?,?,?)",
    ("R1", "Artic Tipper (44t)", "return", 0, json.dumps([[25.0, 58.5], [24.0, 58.5]]),
     70.0, 1.40, "Z001", 5.8, 1.10))

an = network.route_analysis("R1")
row = an["rows"][0]
ok("route_analysis reads the ADJUSTED duration — that is the point of assigning a speed",
   abs(row["cycle_hr"] - (1.50 + 1.40 + row["turnaround_hr"])) < 1e-6)
ok("and reports what the cycle would have been on HERE's own timing",
   abs(row["cycle_hr_here"] - (1.20 + 1.10 + row["turnaround_hr"])) < 1e-6)
ok("the adjusted cycle is longer here", row["cycle_hr"] > row["cycle_hr_here"])
ok("haul_km covers both legs", abs(row["haul_km"] - 11.6) < 1e-6)
ok("the road is named on the row", row["haul_zones"] == ["Z001"])
ok("and the row flags that a speed was applied", row["haul_speed_applied"] is True)
ok("trips per day therefore falls out of the adjusted cycle",
   row["trips_per_day"] == int(an["planning"]["shift_hours_per_day"] // row["cycle_hr"]))


# =========================================================================== #
#  13. The HERE query string (built here, never sent)                          #
# =========================================================================== #
q = here_routing._query({"origin": "58.5,24.0", "destination": "58.5,25.0"},
                        here_routing._via_values([(58.5, 24.5), (58.5, 24.6)]))
ok("two via waypoints produce two via= keys — a dict could not hold them",
   q.count("via=") == 2)
ok("the vias sit between origin and destination, in traversal order",
   q.index("origin") < q.index("via") < q.index("destination"))
ok("24.5 comes before 24.6", q.index("24.5") < q.index("24.6"))
ok("a stopover via carries no passThrough flag", "passThrough" not in q)
ok("pass_through=True adds it",
   "passThrough" in here_routing._query({"origin": "a", "destination": "b"},
                                        here_routing._via_values([(1, 2)], True)))
ok("no vias means no via key at all",
   "via=" not in here_routing._query({"origin": "a", "destination": "b"}, []))


# =========================================================================== #
#  14. Source-level wiring                                                     #
# =========================================================================== #
def code_of(path):
    src = open(path, encoding="utf-8").read()
    return src, "\n".join(l.split("#", 1)[0] for l in src.splitlines())

net_src, net_code = code_of(os.path.join(BACKEND, "network.py"))
main_src, main_code = code_of(os.path.join(BACKEND, "main.py"))
haul_src, haul_code = code_of(os.path.join(BACKEND, "haul.py"))
zones_src, zones_code = code_of(os.path.join(BACKEND, "zones.py"))

ok("the bake path goes through haul.route_with_haul",
   "haul.route_with_haul" in net_code)
ok("and no longer calls here_routing.routes directly in _bake_leg",
   "here_routing.routes" not in net_code.split("def _bake_leg")[1].split("\ndef ")[0])
ok("_upsert_geom takes the Phase 4 columns",
   "haul_zones=None" in net_code and "duration_hr_here=None" in net_code)
ok("the upsert writes them on conflict too",
   "haul_zones = EXCLUDED.haul_zones" in net_src
   and "duration_hr_here = EXCLUDED.duration_hr_here" in net_src)
ok("haul.py never imports network at module level — that would be a cycle",
   "\nimport network" not in haul_code.split("def diagnostics")[0])
ok("zone writes still do not call HERE inline",
   "bake_batch" not in main_src.split("def attach_haul_road")[1].split("\ndef ")[0])
ok("the avoid list excludes haul roads by kind, not by a flag",
   'z["kind"] != HAUL_KIND' in zones_code)

ok("the splice default is stated in code, not just prose",
   'DEFAULT_MODE = "splice"' in haul_code)

# and the docs must not overstate what was verified
ok("haul.py says what has not been checked against HERE",
   "NOT VERIFIED" in haul_src.upper())
ok("the via-alternatives question is named as unverified",
   "alternatives" in haul_src.split("WHAT IS NOT VERIFIED")[1][:600])

# =========================================================================== #
print()
for f in FAIL:
    print("  FAIL:", f)
print(f"\n{PASS} passed, {len(FAIL)} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)

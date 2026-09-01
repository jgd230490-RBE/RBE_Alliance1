"""
Phase 3 backend assertions — zones (geofencing + disruptions).

Same harness shape as test_phase2.py: a scratch SQLite database, no network, and
`fastapi` / `flexpolyline` / `psycopg2` stubbed so main.py imports and its endpoint
functions can be called directly as plain Python.

WHAT THIS DOES NOT PROVE
------------------------
  * HERE is never called. Every assertion about avoid[areas] is about the STRING this
    code builds and hands to here_routing, not about what HERE does with it. Whether
    HERE accepts twelve bounding boxes, or what it returns when a box encloses a route's
    own endpoint, is unknown here and unknowable without the live API — that is what
    /api/admin/diagnostics/zones with probe=true is for.
  * The HTTP layer is stubbed. Routing, status codes, query-parameter coercion and
    response serialisation are unverified; the endpoint BODIES run, the transport does
    not.
  * Every Postgres branch of db.py is unexercised. The zones DDL and the zones_applied
    migration run here against SQLite only.
  * Nothing in a browser. Zone drawing, the map overlay, the popup and the auto-rebake
    loop are asserted at source level in parse_frontend.js / parse_map.js and are not
    executed anywhere.

Run:  python3 backend/tests/test_phase3.py
"""
import os
import sys
import json
import shutil
import sqlite3
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

TMP = tempfile.mkdtemp(prefix="rbe_phase3_")
os.environ.pop("DATABASE_URL", None)

import db  # noqa: E402
db._SQLITE_PATH = os.path.join(TMP, "scratch.db")

import conversions  # noqa: E402
import zones  # noqa: E402
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


# Shapes used throughout. SQ is a 0.2 x 0.2 degree box near Parnu.
SQ = {"type": "Polygon", "coordinates": [[[24.4, 58.4], [24.6, 58.4],
                                          [24.6, 58.6], [24.4, 58.6], [24.4, 58.4]]]}
FAR = {"type": "Polygon", "coordinates": [[[20.0, 50.0], [20.1, 50.0],
                                           [20.1, 50.1], [20.0, 50.1], [20.0, 50.0]]]}
# an L shape: its bounding box covers ground the shape itself does not
L = {"type": "Polygon", "coordinates": [[[24.0, 58.0], [24.1, 58.0], [24.1, 58.9],
                                         [24.2, 58.9], [24.2, 58.0], [24.3, 58.0],
                                         [24.3, 59.0], [24.0, 59.0], [24.0, 58.0]]]}

THROUGH = [[24.0, 58.5], [26.0, 58.5]]        # crosses SQ, no vertex inside it
INSIDE = [[24.45, 58.45], [24.55, 58.55]]     # entirely inside SQ
NEAR = [[24.38, 58.5], [24.37, 58.5]]         # ~1.2-1.7 km west of SQ's west edge
AWAY = [[20.0, 50.0], [20.5, 50.5]]           # nowhere near anything


def add_geom(route_id, coords, leg="loaded", profile="P", alt=0, tag=None):
    db.execute(
        "INSERT INTO route_geometry (route_id, vehicle_profile, leg, alt_index, "
        "geometry, distance_km, duration_hr, zones_applied) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (route_id, profile, leg, alt, json.dumps(coords), 10.0, 0.5, tag))


# =========================================================================== #
#  1. Schema                                                                   #
# =========================================================================== #
reset_db()

ok("zones table is created", "zones" in tables())
zc = cols("zones")
for col in ("id", "name", "kind", "geometry", "affects_routing",
            "starts_on", "ends_on", "note", "active", "created_at", "updated_at"):
    ok(f"zones.{col} exists", col in zc)
ok("zones.name is NOT NULL", "name" in zc and zc["name"][3] == 1)
# ⚠️ Phase 4.5 put tenant_id in front of id in the key. id is still part of it and
# still unique WITHIN a tenant; what changed is that two tenants may each hold a
# 'Z001', which is the entire point of the rebuild.
ok("zones.tenant_id exists", "tenant_id" in zc)
ok("tenant_id is the first primary key column", "tenant_id" in zc and zc["tenant_id"][5] == 1)
ok("zones.id is the second, so the key is (tenant_id, id)",
   "id" in zc and zc["id"][5] == 2)

rg = cols("route_geometry")
ok("route_geometry.zones_applied added by the Phase 3 migration", "zones_applied" in rg)
ok("zones_applied is nullable — NULL means 'baked before zones existed'",
   "zones_applied" in rg and rg["zones_applied"][3] == 0)
ok("the route_geometry key is untouched by Phase 3",
   "leg" in rg and "alt_index" in rg)

# migration must be idempotent: init runs on every boot
db.init_zones_db()
db.init_gates_db()     # Phase 5a
db.init_network_db()
ok("init_zones_db is idempotent", "zones" in tables())
ok("the zones_applied migration is idempotent", "zones_applied" in cols("route_geometry"))

# =========================================================================== #
#  2. Geometry maths                                                           #
# =========================================================================== #
ok("bbox_of returns (w, s, e, n)", zones.bbox_of(SQ) == (24.4, 58.4, 24.6, 58.6))
ok("bbox_of on no coordinates is None", zones.bbox_of({"type": "Polygon", "coordinates": []}) is None)

ok("a line wholly inside a zone is a hit", zones.line_hits_geometry(INSIDE, SQ))
ok("a line crossing with no vertex inside is a hit", zones.line_hits_geometry(THROUGH, SQ))
ok("a line 1.2 km outside is NOT a hit (exact test)", not zones.line_hits_geometry(NEAR, SQ))
ok("a line nowhere near is not a hit", not zones.line_hits_geometry(AWAY, SQ))

pad = zones._pad_bbox(zones.bbox_of(SQ), zones.DETOUR_PAD_KM)
ok("the padded box catches the 1.2 km line", zones.line_in_bbox(NEAR, pad))
ok("the padded box does not catch a line 400 km away", not zones.line_in_bbox(AWAY, pad))
ok("padding widens the box on all four sides",
   pad[0] < 24.4 and pad[1] < 58.4 and pad[2] > 24.6 and pad[3] > 58.6)

LS = {"type": "LineString", "coordinates": [[24.5, 58.0], [24.5, 59.0]]}
ok("a LineString zone is crossable", zones.line_hits_geometry([[24.0, 58.5], [25.0, 58.5]], LS))
ok("a LineString zone that is missed is not a hit",
   not zones.line_hits_geometry([[24.0, 58.5], [24.4, 58.5]], LS))

# the L-shape is the case the bbox reduction gets wrong, on purpose
notch = [[24.13, 58.4], [24.17, 58.4]]        # in the notch: outside the L, inside its bbox
ok("the L shape does not contain a line in its notch", not zones.line_hits_geometry(notch, L))
ok("but the L's bbox does — this is the over-block HERE forces",
   zones.line_in_bbox(notch, zones.bbox_of(L)))

# =========================================================================== #
#  3. Dates                                                                    #
# =========================================================================== #
ok("no dates means always in force", zones.applies_on({}, "2026-06-01"))
ok("before the start date is out", not zones.applies_on({"starts_on": "2026-09-01"}, "2026-06-01"))
ok("after the end date is out", not zones.applies_on({"ends_on": "2026-05-01"}, "2026-06-01"))
ok("the start date itself is in force (inclusive)",
   zones.applies_on({"starts_on": "2026-06-01"}, "2026-06-01"))
ok("the end date itself is in force (inclusive)",
   zones.applies_on({"ends_on": "2026-06-01"}, "2026-06-01"))
ok("an open start is in force", zones.applies_on({"ends_on": "2026-12-31"}, "2026-06-01"))
ok("a malformed date is ignored rather than blocking the zone",
   zones.applies_on({"starts_on": "not-a-date"}, "2026-06-01"))

# =========================================================================== #
#  4. CRUD                                                                     #
# =========================================================================== #
reset_db()
z = zones.create_zone("Tootsi closure", SQ, kind="closure")
ok("create returns the stored zone", z.get("id") == "Z001", str(z)[:120])
ok("ids increment", zones.create_zone("Second", FAR)["id"] == "Z002")
ok("kind is stored", z["kind"] == "closure")
ok("affects_routing defaults to true", z["affects_routing"] is True)
ok("active defaults to true", z["active"] is True)
ok("the bbox is exposed on the row", z["bbox"] == [24.4, 58.4, 24.6, 58.6])
ok("the avoid string is exposed alongside it", z["avoid_area"].startswith("bbox:24.4"))
ok("geometry comes back parsed, not as a string", isinstance(z["geometry"], dict))

ok("a nameless zone is refused", "error" in zones.create_zone("  ", SQ))
ok("a zone with no geometry is refused", "error" in zones.create_zone("X", None))
ok("a Point is refused — it has no extent to avoid",
   "error" in zones.create_zone("X", {"type": "Point", "coordinates": [24.5, 58.5]}))
ok("a garbage geometry type is refused",
   "error" in zones.create_zone("X", {"type": "Circle", "coordinates": [24.5, 58.5]}))

u = zones.update_zone("Z001", name="Renamed")
ok("update renames", u["name"] == "Renamed")
ok("update leaves untouched fields alone", u["kind"] == "closure" and u["affects_routing"] is True)
ok("an empty name is refused on update", "error" in zones.update_zone("Z001", name=" "))
ok("updating a missing zone errors", "error" in zones.update_zone("NOPE", name="x"))
before_note = zones.update_zone("Z001", note="permit 42")["note"]
ok("a note round-trips", before_note == "permit 42")
ok("an update that omits note does not blank it",
   zones.update_zone("Z001", name="Renamed again")["note"] == "permit 42")
ok("deactivating keeps the row", zones.update_zone("Z001", active=False)["active"] is False)
ok("an inactive zone is excluded from the active list",
   "Z001" not in [x["id"] for x in zones.list_zones(include_inactive=False)])
ok("but is still listed by default", "Z001" in [x["id"] for x in zones.list_zones()])

ok("delete removes it", zones.delete_zone("Z002").get("deleted") == "Z002")
ok("deleting twice errors", "error" in zones.delete_zone("Z002"))
ok("get_zone on a missing id is None", zones.get_zone("Z002") is None)

# =========================================================================== #
#  5. What reaches HERE                                                        #
# =========================================================================== #
reset_db()
zones.create_zone("Routing", SQ, affects_routing=True)
zones.create_zone("Advisory", FAR, affects_routing=False)
zones.create_zone("Expired", L, affects_routing=True, ends_on="2000-01-01")
zones.create_zone("Off", L, affects_routing=True, active=False)

av = zones.avoid_areas()
ok("only the routing zone is sent to HERE", len(av) == 1, str(av))
ok("the avoid string is HERE's bbox form", av[0] == "bbox:24.400000,58.400000,24.600000,58.600000", av[0])
ok("an advisory zone is never sent",
   not any("20.0" in a for a in av))
ok("an out-of-window zone is not sent", not any("58.9" in a or "59.0" in a for a in av))
ok("routing_zones agrees with avoid_areas", len(zones.routing_zones()) == 1)

areas, tag = network.active_avoid()
ok("network.active_avoid mirrors zones.avoid_areas", areas == av)
ok("the zone tag names the applied zones", tag == "Z001", tag)
ok("the tag is sorted so it is comparable between bakes",
   network.active_avoid()[1] == tag)

reset_db()
ok("with no zones the avoid list is empty", zones.avoid_areas() == [])
ok("with no zones the tag is '' — a positive 'nothing avoided', not NULL",
   network.active_avoid()[1] == "")

# the parameter must actually reach the query string
_captured = {}


def _fake_urlopen(url, timeout=None):
    _captured["url"] = url
    raise RuntimeError("stopped before the network")


_real_open = here_routing.urllib.request.urlopen
here_routing.urllib.request.urlopen = _fake_urlopen
# routes() refuses to build a URL at all without a key, so give it a fake one. Nothing
# leaves the process: urlopen is replaced above and records the URL instead of opening it.
os.environ["HERE_API_KEY"] = "test-key-not-real"
try:
    try:
        here_routing.routes(58.0, 24.0, 58.5, 24.5, "P", None,
                            avoid_areas=["bbox:1,2,3,4", "bbox:5,6,7,8"])
    except Exception:
        pass
    ok("avoid[areas] reaches the HERE query string",
       "avoid%5Bareas%5D" in _captured.get("url", ""), _captured.get("url", "")[:160])
    ok("several areas are pipe-joined as HERE expects",
       "bbox%3A1%2C2%2C3%2C4%7Cbbox%3A5%2C6%2C7%2C8" in _captured.get("url", ""))
    _captured.clear()
    try:
        here_routing.routes(58.0, 24.0, 58.5, 24.5, "P", None, avoid_areas=None)
    except Exception:
        pass
    ok("with no zones the parameter is omitted entirely, not sent empty",
       "avoid%5Bareas%5D" not in _captured.get("url", ""))
finally:
    here_routing.urllib.request.urlopen = _real_open
    os.environ.pop("HERE_API_KEY", None)

# =========================================================================== #
#  6. Invalidation                                                             #
# =========================================================================== #
reset_db()
add_geom("R_CROSS", THROUGH)                      # crosses SQ
add_geom("R_CROSS", THROUGH, leg="return")
add_geom("R_NEAR", NEAR)                          # 1.2 km away, no bake record
add_geom("R_AWAY", AWAY)                          # far away, no bake record
add_geom("R_TAGGED", AWAY, tag="Z001")            # far away, but baked avoiding Z001
add_geom("R_TAGGED2", AWAY, tag="Z002,Z003")      # baked avoiding others

r = zones.invalidate(new_geometry=SQ, dry_run=True)
ok("a new zone invalidates the route that crosses it", "R_CROSS" in r["route_ids"])
ok("both legs of it are listed", r["leg_count"] == 2, str(r["legs"]))
ok("it does not invalidate a route 1.2 km away", "R_NEAR" not in r["route_ids"])
ok("it does not invalidate an unrelated route", "R_AWAY" not in r["route_ids"])
ok("the reason given is the exact one", r["legs"][0]["reason"] == "crosses the zone")
ok("nothing is approximate in the exact case", r["approximate"] == 0)
ok("a dry run reports here_calls so the cost is visible first", r["here_calls"] == 2)
ok("a dry run clears nothing", r["cleared"] == 0)
ok("a dry run really leaves the table alone",
   db.query("SELECT COUNT(*) AS n FROM route_geometry")[0]["n"] == 6)

r2 = zones.invalidate(zone_id="Z001", dry_run=True)
ok("a leg tagged with the zone is invalidated on any change to it",
   r2["route_ids"] == ["R_TAGGED"], str(r2["route_ids"]))
ok("the tag reason says so", r2["legs"][0]["reason"] == "was baked with this zone avoided")
ok("a leg tagged with OTHER zones is left alone", "R_TAGGED2" not in r2["route_ids"])

r3 = zones.invalidate(zone_id="Z001", old_geometry=SQ, dry_run=True)
ok("the delete case picks up both the tagged leg and the untagged near one",
   set(r3["route_ids"]) == {"R_TAGGED", "R_NEAR"}, str(r3["route_ids"]))
ok("the approximate match is counted and labelled", r3["approximate"] == 1)
ok("the approximate reason names the pad distance",
   any("approximate" in (l["reason"] or "") for l in r3["legs"]))
ok("the padded test never touches a tagged leg — that would burn HERE calls for nothing",
   "R_TAGGED2" not in r3["route_ids"])

r4 = zones.invalidate(new_geometry=SQ)
ok("a real run clears the rows", r4["cleared"] == 2)
ok("the crossing route's geometry is gone",
   db.query("SELECT COUNT(*) AS n FROM route_geometry WHERE route_id = 'R_CROSS'")[0]["n"] == 0)
ok("everything else survives",
   db.query("SELECT COUNT(*) AS n FROM route_geometry")[0]["n"] == 4)
ok("invalidate with nothing to go on is a no-op",
   zones.invalidate()["leg_count"] == 0)

# alternatives must go with the primary, or a promoted alt survives a new closure
reset_db()
add_geom("R_ALT", THROUGH, alt=0)
add_geom("R_ALT", THROUGH, alt=1)
add_geom("R_ALT", INSIDE, alt=2)
r5 = zones.invalidate(new_geometry=SQ)
ok("every alternative on an affected leg is cleared", r5["cleared"] == 3)
ok("one HERE call is reported per LEG, not per alternative", r5["here_calls"] == 1)
ok("no alternative is left behind",
   db.query("SELECT COUNT(*) AS n FROM route_geometry")[0]["n"] == 0)

# =========================================================================== #
#  7. Endpoints (bodies, not HTTP)                                             #
# =========================================================================== #
reset_db()


def zin(**kw):
    base = dict(name="Z", geometry=SQ, kind="closure", affects_routing=True,
                starts_on=None, ends_on=None, note=None, active=True)
    base.update(kw)
    return main.ZoneIn(**base)


add_geom("R_CROSS", THROUGH)
res = main.create_zone(zin(name="Bridge out"))
ok("POST returns the zone", res["zone"]["name"] == "Bridge out")
ok("POST reports what it invalidated", res["invalidated"]["leg_count"] == 1)
ok("POST flags that a re-bake is required", res["rebake_required"] is True)
ok("POST reports the HERE call count up front", res["here_calls"] == 1)
ok("POST actually cleared the geometry",
   db.query("SELECT COUNT(*) AS n FROM route_geometry")[0]["n"] == 0)

add_geom("R2", AWAY)
res2 = main.create_zone(zin(name="Advisory only", geometry=FAR, affects_routing=False))
ok("an advisory zone invalidates nothing", res2["invalidated"]["leg_count"] == 0)
ok("an advisory zone needs no re-bake", res2["rebake_required"] is False)
ok("an advisory zone leaves geometry alone",
   db.query("SELECT COUNT(*) AS n FROM route_geometry")[0]["n"] == 1)

listed = main.list_zones()
ok("GET /api/zones lists both", len(listed["zones"]) == 2)
ok("the listing carries a summary", listed["summary"]["routing"] == 1)
ok("the listing offers the kind vocabulary", "closure" in listed["kinds"])
ok("date filtering is available on the listing",
   isinstance(main.list_zones(on="2026-06-01")["zones"], list))

got = main.get_zone("Z001")
ok("GET one zone works", got["id"] == "Z001")
try:
    main.get_zone("NOPE")
    ok("GET a missing zone 404s", False)
except _HTTPException as e:
    ok("GET a missing zone 404s", e.status_code == 404)

imp = main.zone_impact("Z001")
ok("the impact endpoint is a dry run", imp["would_invalidate"]["dry_run"] is True)
ok("the impact endpoint reports the HERE bill",
   "here_calls" in imp["would_invalidate"])

upd = main.update_zone("Z001", zin(name="Bridge reopened", ends_on="2026-01-01"))
ok("PUT updates", upd["zone"]["name"] == "Bridge reopened")
ok("PUT sets the end date", upd["zone"]["ends_on"] == "2026-01-01")
ok("an out-of-window zone stops being sent to HERE",
   "Z001" not in network.active_avoid()[1])

dl = main.delete_zone("Z002")
ok("DELETE removes the zone", zones.get_zone("Z002") is None)
ok("DELETE reports the old zone for the record", dl["zone"]["was"]["id"] == "Z002")
try:
    main.delete_zone("Z002")
    ok("DELETE a missing zone 404s", False)
except _HTTPException as e:
    ok("DELETE a missing zone 404s", e.status_code == 404)

try:
    main.create_zone(zin(geometry={"type": "Point", "coordinates": [24, 58]}))
    ok("a bad geometry 400s", False)
except _HTTPException as e:
    ok("a bad geometry 400s", e.status_code == 400)

# admin token, when one is set
os.environ["ADMIN_TOKEN"] = "secret"
try:
    main.create_zone(zin(name="Nope"), token="wrong")
    ok("a bad admin token is rejected on zone writes", False)
except _HTTPException as e:
    ok("a bad admin token is rejected on zone writes", e.status_code == 403)
ok("the right token is accepted",
   main.create_zone(zin(name="Allowed", geometry=FAR), token="secret")["zone"]["name"] == "Allowed")
ok("reading zones needs no token — the public map has none",
   isinstance(main.list_zones()["zones"], list))
os.environ.pop("ADMIN_TOKEN")

# =========================================================================== #
#  8. Diagnostics                                                              #
# =========================================================================== #
reset_db()
zones.create_zone("D", SQ)
add_geom("R1", AWAY, tag="Z001")
add_geom("R2", AWAY, tag="")
add_geom("R3", AWAY, tag=None)
d = network.zones_diagnostics()
ok("the zone diagnostic lists what is in force", d["count"] == 1)
ok("it shows the exact avoid string that would be sent",
   d["avoid_areas"] == ["bbox:24.400000,58.400000,24.600000,58.600000"])
ok("it breaks baked legs down by provenance", d["baked_legs_by_tag"].get("Z001") == 1)
ok("legs baked with no zones are distinguishable", d["baked_legs_by_tag"].get("(none)") == 1)
ok("legs predating Phase 3 are distinguishable",
   d["baked_legs_by_tag"].get("(pre-Phase-3)") == 1)
ok("it spends no HERE calls without probe=true", "probe" not in d)
ok("it refuses to invent HERE's avoid-area limit",
   "NOT enforced" in d["note"] and "verified" in d["note"])

# =========================================================================== #
#  9. Bake path wiring (source level — no HERE call is made anywhere here)      #
# =========================================================================== #
net_src = open(os.path.join(BACKEND, "network.py"), encoding="utf-8").read()
net_code = "\n".join(l.split("#", 1)[0] for l in net_src.splitlines())
main_src = open(os.path.join(BACKEND, "main.py"), encoding="utf-8").read()
main_code = "\n".join(l.split("#", 1)[0] for l in main_src.splitlines())

ok("network passes avoid_areas to here_routing.routes",
   "avoid_areas=(avoid or None)" in net_code)
ok("bake_batch resolves the zone set once per run", "avoid, zone_tag = active_avoid()" in net_code)
ok("bake_route resolves it too", net_code.count("active_avoid()") >= 3)
ok("the applied zones are stamped on every stored row",
   net_code.count("zones_applied=zone_tag") >= 4)
ok("_upsert_geom accepts the tag", "def _upsert_geom" in net_code and "zones_applied=None" in net_code)
ok("the upsert writes the column on conflict too",
   "zones_applied = EXCLUDED.zones_applied" in net_src)
ok("the startup creates the zones table", "db.init_zones_db()" in main_code)
ok("zone writes do not call HERE inline — that would time out on Render",
   "bake_batch" not in main_src.split("def create_zone")[1].split("def ")[0])

# the migration must not have disturbed the widened Phase 2 key
reset_db()
sqltxt = db.query("SELECT sql FROM sqlite_master WHERE name = 'route_geometry'")[0]["sql"]
# ⚠️ Phase 4.5 prefixed this key with tenant_id. The Phase 2 four are still there,
# still in order — a tenant key that quietly reordered or dropped one of them would
# break the alternatives model, so both halves are checked.
ok("the route_geometry key is the tenant-widened 5-tuple",
   "PRIMARY KEY (tenant_id, route_id, vehicle_profile, leg, alt_index)" in sqltxt,
   sqltxt[:160])
ok("and the Phase 2 four survive inside it, in order",
   "route_id, vehicle_profile, leg, alt_index)" in sqltxt)

# =========================================================================== #
#  10. End-to-end through the bake path, with HERE replaced by a recorder       #
# =========================================================================== #
# Section 9 asserts the wiring exists in the source. This one runs it: a fake
# here_routing.routes records what it was handed and returns a fixed line, so the whole
# bake_batch -> _bake_leg -> _upsert_geom chain executes. Still no HERE call, and still
# no evidence about what the real API does with an avoid[areas] list.
reset_db()
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES ('L1', 'A', 58.30, 24.30)")
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES ('L2', 'B', 58.70, 24.70)")
db.execute("INSERT INTO routes (id, origin_id, dest_id) VALUES ('RX', 'L1', 'L2')")
zones.create_zone("In the way", SQ, affects_routing=True)
zones.create_zone("Ignored", FAR, affects_routing=False)

_calls = []
_real_routes = here_routing.routes
_real_conf = here_routing.configured
here_routing.configured = lambda: True


def _fake_routes(o_lat, o_lon, d_lat, d_lon, profile, factors=None, avoid_areas=None,
                 alternatives=1, laden=True):
    _calls.append({"avoid": avoid_areas, "laden": laden, "profile": profile})
    return [{"geometry": [[24.30, 58.30], [24.70, 58.70]],
             "distance_km": 55.0, "duration_hr": 1.1}]


here_routing.routes = _fake_routes
try:
    out = network.bake_batch(profile="P", limit=10)
    ok("the bake ran both legs", out["baked"] == 2, str(out))
    ok("the bake reports which zones it applied", out["zones_applied"] == ["Z001"], str(out.get("zones_applied")))
    ok("the bake reports the avoid areas it sent",
       out["avoid_areas"] == ["bbox:24.400000,58.400000,24.600000,58.600000"])
    ok("every HERE call carried the avoid list",
       len(_calls) == 2 and all(c["avoid"] == ["bbox:24.400000,58.400000,24.600000,58.600000"]
                                for c in _calls), str(_calls))
    ok("the advisory zone was never sent",
       not any("20.0" in a for c in _calls for a in (c["avoid"] or [])))
    ok("both legs were routed, one laden and one not",
       sorted(c["laden"] for c in _calls) == [False, True])

    stored = db.query("SELECT leg, zones_applied FROM route_geometry ORDER BY leg")
    ok("the applied zones are stamped on every stored row",
       [r["zones_applied"] for r in stored] == ["Z001", "Z001"], str(stored))

    # and with no zones in force, the tag is '' rather than NULL
    zones.delete_zone("Z001")
    network.clear_geometry()
    _calls.clear()
    out2 = network.bake_batch(profile="P", limit=10)
    ok("with no zones the bake sends no avoid list", _calls and _calls[0]["avoid"] is None)
    ok("and stamps '' — a positive record of 'nothing avoided'",
       db.query("SELECT zones_applied FROM route_geometry LIMIT 1")[0]["zones_applied"] == "")
    ok("which is distinguishable from a pre-Phase-3 NULL",
       db.query("SELECT zones_applied FROM route_geometry LIMIT 1")[0]["zones_applied"] is not None)
finally:
    here_routing.routes = _real_routes
    here_routing.configured = _real_conf

# =========================================================================== #
print()
for f in FAIL:
    print("  FAIL:", f)
print(f"\n{PASS} passed, {len(FAIL)} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)

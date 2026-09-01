"""
Phase 4.5 backend assertions — the tenant key.

Same harness shape as test_phase3.py: a scratch SQLite database, no network, and
`fastapi` / `flexpolyline` / `psycopg2` stubbed so main.py imports and its endpoint
functions can be called directly as plain Python.

WHY THIS EXISTS ALONGSIDE test_tenant_audit.py
----------------------------------------------
The audit is a STATIC check: it proves every SQL statement mentions tenant_id. It
cannot prove the right value is bound to it — a query filtered on a hard-coded
wrong tenant, or one whose `?` placeholders and params tuple have drifted out of
alignment, passes the audit and is still broken.

This file is the runtime half. It puts the SAME ids in two tenants and proves
nothing leaks: reads, writes, updates, deletes and the seed path. If the params
of any statement are misaligned, a query here returns the wrong tenant's row and
these assertions fail.

⚠️ Both halves are needed and neither replaces the other. The audit catches a
   statement nobody remembered to filter, including one added next year. This
   file catches a statement that was filtered wrongly.

WHAT THIS DOES NOT PROVE
------------------------
  * Every Postgres branch is unexercised. The tenant migration has two paths —
    ALTER-in-place for Postgres, rebuild-and-copy for SQLite — and only the
    SQLite one runs here. The live Postgres migration has never been executed.
  * The HTTP layer is stubbed. There is no request-scoped tenant yet: Phase 4.5
    is one tenant, no auth, and db.current_tenant() reads a contextvar with a
    default. Whether a middleware sets it correctly per request is Phase 6.
  * Nothing about authorisation. A tenant key is not a permission boundary. It
    keeps two clients' rows apart; it does not stop anyone asking for either.

Run:  python3 backend/tests/test_phase45.py
"""
import json
import os
import re
import sys
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

TMP = tempfile.mkdtemp(prefix="rbe_phase45_")
os.environ.pop("DATABASE_URL", None)

import db  # noqa: E402
db._SQLITE_PATH = os.path.join(TMP, "scratch.db")

import conversions  # noqa: E402
import zones  # noqa: E402
import haul  # noqa: E402
import network  # noqa: E402
import taxonomy  # noqa: E402
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
    db.init_db()
    db.init_network_db()
    db.init_taxonomy_db()
    db.init_zones_db()
    db.init_gates_db()     # Phase 5a
    db.init_weeks_db()     # Week 1 — forecast_weeks, stockpile_weeks, capacity columns
    db.init_tenant()


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


def table_sql(name):
    r = db.query("SELECT sql FROM sqlite_master WHERE name = ?", (name,))
    return r[0]["sql"] if r else ""


def raw(sql, params=()):
    """A deliberately UNFILTERED read, for checking what is really on disk."""
    return db.query(sql, params)


class as_tenant:
    """Run a block as a given tenant, then put the previous one back."""

    def __init__(self, t):
        self.t = t

    def __enter__(self):
        self.token = db.set_current_tenant(self.t)
        return self

    def __exit__(self, *a):
        db.reset_current_tenant(self.token)
        return False


A, B = "acme", "beta"

# =========================================================================== #
#  1. Schema — every tenanted table has the column, in the key                 #
# =========================================================================== #
reset_db()

# 11 at Phase 4.5, 12 from Phase 5a's location_gates, 14 from Week 1's forecast_weeks
# and stockpile_weeks. Pinned to an exact number on purpose: a table added to db.py
# without being registered here and in test_tenant_audit.py ships with the column
# present and the isolation absent. Both Week 1 tables tripped this line and the audit
# before they were registered.
ok("db.py names the tenanted tables in one place", len(db.TENANTED_TABLES) == 14,
   f"got {len(db.TENANTED_TABLES)}")

for t in sorted(db.TENANTED_TABLES):
    c = cols(t)
    ok(f"{t} exists", bool(c))
    ok(f"{t}.tenant_id exists", "tenant_id" in c)
    ok(f"{t}.tenant_id is NOT NULL", "tenant_id" in c and c["tenant_id"][3] == 1)
    ok(f"⭐ {t}.tenant_id is the FIRST primary key column",
       "tenant_id" in c and c["tenant_id"][5] == 1,
       f"pk position {c.get('tenant_id', (None,) * 6)[5]}")

ok("forecasts keeps its Phase 2 unique key, tenant-widened",
   "UNIQUE (tenant_id, route_id, month_index, discipline, section_id)" in table_sql("forecasts"))
ok("route_geometry keeps its Phase 2 alternatives key, tenant-widened",
   "PRIMARY KEY (tenant_id, route_id, vehicle_profile, leg, alt_index)"
   in table_sql("route_geometry"))
ok("route_haul_roads keeps its Phase 4 key, tenant-widened",
   "PRIMARY KEY (tenant_id, route_id, zone_id)" in table_sql("route_haul_roads"))

ok("a fresh database is born tenanted and needs no migration",
   all(v == "already" for v in db.init_tenant().values()))

ok("there is one default, not a bare string repeated",
   isinstance(db.TENANT_DEFAULT, str) and db.TENANT_DEFAULT)
ok("current_tenant falls back to it", db.current_tenant() == db.TENANT_DEFAULT)

tok = db.set_current_tenant("zzz")
ok("set_current_tenant changes it", db.current_tenant() == "zzz")
db.reset_current_tenant(tok)
ok("and reset puts it back", db.current_tenant() == db.TENANT_DEFAULT)
ok("an empty tenant falls back rather than writing ''",
   (lambda: (db.set_current_tenant(""), db.current_tenant() == db.TENANT_DEFAULT)[1])())
db.set_current_tenant(db.TENANT_DEFAULT)

# =========================================================================== #
#  2. ⭐ The same ids in two tenants — what the key rebuild was FOR            #
# =========================================================================== #
# The strongest available proof, because it is the real seed path: both tenants
# load the SAME v2_network.json, so every one of the 27 location ids and 107
# route ids collides. Before Phase 4.5 the second seed would have hit a primary
# key violation on 'C01'.
reset_db()

with as_tenant(A):
    seeded_a = network.seed_network()
    n_loc_a = db.count_locations()
with as_tenant(B):
    seeded_b = network.seed_network()
    n_loc_b = db.count_locations()

ok("tenant A seeds the network", seeded_a is True)
ok("⭐ tenant B seeds it too — the guard is per tenant, not global", seeded_b is True)
ok("A has the full network", n_loc_a == 27, f"got {n_loc_a}")
ok("B has the full network", n_loc_b == 27, f"got {n_loc_b}")
ok("⭐ and both hold the SAME ids — 54 rows, 27 distinct ids",
   len(raw("SELECT * FROM locations")) == 54 and
   len({r["id"] for r in raw("SELECT id FROM locations")}) == 27)
ok("both tenants hold the same 107 route ids",
   len(raw("SELECT * FROM routes")) == 214 and
   len({r["id"] for r in raw("SELECT id FROM routes")}) == 107)
ok("the rows are stamped with the two tenants",
   {r["tenant_id"] for r in raw("SELECT tenant_id FROM locations")} == {A, B})

with as_tenant(A):
    ok("re-seeding an already-seeded tenant is still a no-op",
       network.seed_network() is False)

# =========================================================================== #
#  3. ⭐ Writes do not cross — the failure mode of a missing WHERE             #
# =========================================================================== #
# An UPDATE with no tenant predicate rewrites every tenant's row. That is the
# most damaging shape a missed filter can take, because it succeeds silently.
with as_tenant(A):
    network.update_location("C01", name="Acme renamed")

names = {r["tenant_id"]: r["name"]
         for r in raw("SELECT tenant_id, name FROM locations WHERE id = 'C01'")}
ok("⭐ an update under A changes A's row", names.get(A) == "Acme renamed", str(names))
ok("⭐ and leaves B's row alone", names.get(B) != "Acme renamed", str(names))

with as_tenant(A):
    network.delete_location("C01")
left = raw("SELECT tenant_id FROM locations WHERE id = 'C01'")
ok("⭐ a delete under A removes A's row", len(left) == 1)
ok("⭐ and B's row of the same id survives it", left[0]["tenant_id"] == B)
with as_tenant(B):
    ok("B still counts the full network", db.count_locations() == 27)

# =========================================================================== #
#  4. Geometry and haul links                                                  #
# =========================================================================== #
reset_db()
for t in (A, B):
    with as_tenant(t):
        network.seed_network()

RID = raw("SELECT id FROM routes LIMIT 1")[0]["id"]

for t, km in ((A, 11.0), (B, 22.0)):
    db.execute(
        "INSERT INTO route_geometry (tenant_id, route_id, vehicle_profile, leg, alt_index, "
        "geometry, distance_km, duration_hr) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (t, RID, "P", "loaded", 0, json.dumps([[24.4, 58.3], [24.8, 58.6]]), km, 0.5))

ok("⭐ the same geometry key exists in both tenants — the old PK forbade it",
   len(raw("SELECT * FROM route_geometry")) == 2)
with as_tenant(A):
    g = network.route_geometries(RID)["features"]
ok("a read under A gets exactly one feature", len(g) == 1, f"got {len(g)}")
ok("⭐ and it carries A's distance, not B's",
   g and abs(float(g[0]["properties"]["distance_km"]) - 11.0) < 1e-6,
   str(g[0]["properties"]["distance_km"] if g else None))

with as_tenant(A):
    network.clear_geometry()
ok("⭐ clearing geometry under A leaves B's row on disk",
   len(raw("SELECT * FROM route_geometry")) == 1)
ok("and the survivor is B's", raw("SELECT * FROM route_geometry")[0]["tenant_id"] == B)

HAUL_LINE = {"type": "LineString", "coordinates": [[24.4, 58.3], [24.5, 58.4]]}
for t in (A, B):
    with as_tenant(t):
        z = zones.create_zone(f"{t} haul", HAUL_LINE, kind="haul_road", speed_kph=30)
        haul.attach(RID, z["id"])

ok("both tenants hold a haul link on the same route", len(raw("SELECT * FROM route_haul_roads")) == 2)
with as_tenant(A):
    ok("each tenant sees one link", len(haul.links_for_route(RID)) == 1)
    ok("and the count agrees", db.count_haul_links() == 1)
    a_zone = zones.list_zones()[0]["id"]
    haul.clear_links_for_zone(a_zone)
ok("⭐ clearing A's links leaves B's link", len(raw("SELECT * FROM route_haul_roads")) == 1)
ok("and the survivor belongs to B",
   raw("SELECT * FROM route_haul_roads")[0]["tenant_id"] == B)

# =========================================================================== #
#  5. Zones — including the list that actually reaches HERE                    #
# =========================================================================== #
reset_db()

SQ = {"type": "Polygon", "coordinates": [[[24.4, 58.4], [24.6, 58.4],
                                          [24.6, 58.6], [24.4, 58.6], [24.4, 58.4]]]}
FAR = {"type": "Polygon", "coordinates": [[[20.0, 50.0], [20.1, 50.0],
                                           [20.1, 50.1], [20.0, 50.1], [20.0, 50.0]]]}

with as_tenant(A):
    za = zones.create_zone("Acme closure", SQ, kind="closure")
with as_tenant(B):
    zb = zones.create_zone("Beta closure", FAR, kind="closure")

ok("⭐ zone ids restart per tenant — one client's count cannot push another's",
   za["id"] == zb["id"] == "Z001", f"{za['id']} vs {zb['id']}")
with as_tenant(A):
    ok("each tenant lists one zone", len(zones.list_zones()) == 1)
    ok("and gets its own by id", zones.get_zone(za["id"])["name"] == "Acme closure")
    ok("the count agrees", db.count_zones() == 1)
    zones.delete_zone(za["id"])
with as_tenant(B):
    ok("⭐ deleting A's Z001 leaves B's Z001", zones.get_zone(zb["id"]) is not None)

# ⭐ avoid_areas() is what is handed to HERE. A leak here would steer one client's
#    trucks around another client's road closures — wrong routes, real money.
with as_tenant(B):
    boxes_b = zones.avoid_areas()
with as_tenant(A):
    boxes_a = zones.avoid_areas()
ok("⭐ the avoid[areas] list HERE receives is per tenant",
   len(boxes_b) == 1 and len(boxes_a) == 0, f"B={len(boxes_b)} A={len(boxes_a)}")

# =========================================================================== #
#  6. Forecasts, and the ON CONFLICT upsert                                    #
# =========================================================================== #
reset_db()
for t in (A, B):
    with as_tenant(t):
        network.seed_network()

FID = raw("SELECT id FROM routes LIMIT 1")[0]["id"]


def save(tenant, qty):
    with as_tenant(tenant):
        return main.save_matrix_row(main.MatrixRow(
            route_id=FID, discipline="substructure", section_id="WS1",
            material_type="Small aggregate", vehicle_type="Artic Tipper (44t)",
            unit="t", cells=[main.Cell(month_index=3, quantity=qty)],
            submitted_by="tester"))


save(A, 10.0)
save(B, 99.0)
ok("⭐ both tenants hold a forecast on the same (route, month, discipline, section)",
   len(raw("SELECT * FROM forecasts")) == 2)

save(A, 25.0)
rows = raw("SELECT * FROM forecasts ORDER BY tenant_id")
ok("⭐ re-saving upserts rather than duplicating — the ON CONFLICT target matches",
   len(rows) == 2, f"got {len(rows)} rows")
ok("A's quantity was updated",
   any(r["tenant_id"] == A and abs(r["quantity"] - 25.0) < 1e-6 for r in rows))
ok("⭐ and B's was not touched",
   any(r["tenant_id"] == B and abs(r["quantity"] - 99.0) < 1e-6 for r in rows))

with as_tenant(A):
    ok("each tenant counts its own", db.count_forecasts() == 1)
    ok("and lists its own", len(main.list_forecasts()) == 1)
    main.set_route_status(FID, main.StatusUpdate(status="Approved"))

after = {r["tenant_id"]: r["status"] for r in raw("SELECT * FROM forecasts")}
ok("⭐ approving under A approves A's line", after.get(A) == "Approved")
ok("⭐ and leaves B's line Pending", after.get(B) == "Pending", str(after))

with as_tenant(A):
    main.withdraw_route(FID)
left = raw("SELECT * FROM forecasts")
ok("⭐ withdrawing under A removes only A's rows", len(left) == 1, f"got {len(left)}")
ok("and B's row is the one left", left and left[0]["tenant_id"] == B)

# =========================================================================== #
#  7. Seeding gives each tenant its own reference data                         #
# =========================================================================== #
reset_db()

with as_tenant(A):
    ca = taxonomy.seed_taxonomy()
with as_tenant(B):
    cb = taxonomy.seed_taxonomy()

ok("seeding tenant A inserts rows", any(ca.values()), str(ca))
ok("⭐ seeding tenant B inserts its OWN rows rather than finding A's",
   any(cb.values()), str(cb))
ok("the two seeds are the same size", ca == cb, f"{ca} vs {cb}")

with as_tenant(A):
    again = taxonomy.seed_taxonomy()
ok("re-seeding the same tenant is still idempotent", not any(again.values()), str(again))

n_disc = len(raw("SELECT * FROM disciplines"))
with as_tenant(A):
    mine = len(taxonomy.list_disciplines(include_out_of_scope=True))
ok("⭐ each tenant reads only its own taxonomy", mine * 2 == n_disc, f"{mine} of {n_disc}")

# =========================================================================== #
#  8. The migration off a pre-4.5 database                                     #
# =========================================================================== #
# The live Postgres predates Phase 4.5, so this path is not hypothetical. Here it
# is exercised on SQLite only — the Postgres branch is unrun. See the header.
if os.path.exists(db._SQLITE_PATH):
    os.remove(db._SQLITE_PATH)

conn = db.get_conn()
cur = conn.cursor()
cur.execute("""
    CREATE TABLE locations (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, loc_type TEXT, role TEXT,
        materials TEXT, supplies TEXT, receives TEXT,
        lat REAL NOT NULL, lon REAL NOT NULL, material TEXT
    )
""")
cur.execute("INSERT INTO locations (id, name, loc_type, lat, lon) VALUES "
            "('C01', 'legacy yard', 'Compound', 58.4, 24.5)")
cur.execute("""
    CREATE TABLE route_geometry (
        route_id TEXT NOT NULL, vehicle_profile TEXT NOT NULL,
        leg TEXT NOT NULL DEFAULT 'loaded', alt_index INTEGER NOT NULL DEFAULT 0,
        geometry TEXT, distance_km REAL, duration_hr REAL, computed_at TEXT, error TEXT,
        PRIMARY KEY (route_id, vehicle_profile, leg, alt_index)
    )
""")
cur.execute("INSERT INTO route_geometry (route_id, vehicle_profile, leg, alt_index, "
            "distance_km) VALUES ('R001', 'P', 'loaded', 0, 42.0)")
conn.commit()
conn.close()

pre = cols("locations")
ok("the pre-4.5 fixture really has no tenant_id", "tenant_id" not in pre)

db.init_network_db()      # adds the later ALTER columns, as it does on a real boot
mid = cols("locations")
ok("init_network_db adds the later columns first", "gate_lat" in mid and "vendor" in mid)

result = db.init_tenant()
post = cols("locations")

ok("the migration reports what it did", isinstance(result, dict) and
   result.get("locations") == "migrated", str(result.get("locations")))
ok("tenant_id is added", "tenant_id" in post)
ok("⭐ and it is the first primary key column", post["tenant_id"][5] == 1)
ok("⭐ the existing row survives", len(raw("SELECT * FROM locations")) == 1)
ok("⭐ stamped with the default tenant",
   raw("SELECT * FROM locations")[0]["tenant_id"] == db.TENANT_DEFAULT)
ok("its data is intact", raw("SELECT * FROM locations")[0]["name"] == "legacy yard")
ok("⭐ columns added by the earlier ALTERs are NOT lost in the rebuild",
   "gate_lat" in post and "vendor" in post and "detail" in post and "default_section_id" in post)
ok("the scratch table is cleaned up", "locations_pre45" not in tables())

rg = raw("SELECT * FROM route_geometry")
ok("route_geometry's row survives too", len(rg) == 1 and abs(rg[0]["distance_km"] - 42.0) < 1e-6)
ok("with the default tenant", rg[0]["tenant_id"] == db.TENANT_DEFAULT)
ok("and the Phase 4 columns are present after the rebuild",
   "haul_km" in cols("route_geometry") and "duration_hr_here" in cols("route_geometry"))

second = db.init_tenant()
# This fixture only created two of the eleven tables, so the other nine report
# 'missing' — which is the point of that status: init_tenant() must not crash on a
# table that does not exist yet, because init_* and init_tenant() can legitimately
# run against a half-built database during a partial migration.
ok("⭐ the migration is idempotent — a second boot migrates nothing",
   not any(v in ("migrated", "failed") for v in second.values()), str(second))
ok("the tables it did migrate report 'already'",
   second["locations"] == "already" and second["route_geometry"] == "already")
ok("and a table that does not exist is reported, not crashed on",
   second["zones"] == "missing")
ok("and it did not duplicate the row", len(raw("SELECT * FROM locations")) == 1)

# the default tenant is what the app reads, so a migrated database looks unchanged
ok("⭐ after migrating, the app sees the same row it saw before",
   db.count_locations() == 1)
ok("and reads it through the normal filtered path",
   len(network.locations_geojson()["features"]) == 1)

# =========================================================================== #
#  9. Source-level: the promise cannot be quietly withdrawn                    #
# =========================================================================== #
db_src = open(os.path.join(BACKEND, "db.py"), encoding="utf-8").read()
called = set(re.findall(r'_create_tenanted\(cur,\s*"(\w+)"\)', db_src))
ok("⭐ the tenant DDL is the single source for every tenanted table",
   called == db.TENANTED_TABLES, f"missing: {sorted(db.TENANTED_TABLES - called)}")
ok("no second untenanted copy of the forecasts DDL survives",
   "FORECASTS_DDL = " not in db_src)
main_src = open(os.path.join(BACKEND, "main.py"), encoding="utf-8").read()
ok("init_tenant runs at startup", "db.init_tenant()" in main_src)
ok("⭐ and AFTER the other init_* calls, whose ALTERs it copies",
   main_src.index("db.init_zones_db()") < main_src.index("db.init_tenant()"))
# Week 1 adds three columns to `locations` by ALTER. Run init_weeks_db() after the
# tenant rebuild and SQLite copies only the intersection, silently dropping them —
# the same trap Phase 5a's two route gate columns had to avoid.
ok("⭐ init_weeks_db too, for the three capacity columns it ALTERs onto locations",
   main_src.index("db.init_weeks_db()") < main_src.index("db.init_tenant()")
   and main_src.index("db.init_network_db()") < main_src.index("db.init_weeks_db()"))
ok("⭐ and BEFORE the seeds, which must write into the tenanted table",
   main_src.index("db.init_tenant()") < main_src.index("network.seed_network()"))
ok("the tenant is resolved in one place, not passed through 139 signatures",
   "def current_tenant" in db_src and "contextvars" in db_src)

print()
for f in FAIL:
    print("  FAIL:", f)
print(f"\n{PASS} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)

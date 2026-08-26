"""
Phase 2 backend assertions.

Runs against a scratch SQLite database with no network access. FastAPI and psycopg2 are
not installable in the build sandbox, so `fastapi` is stubbed just enough to import
main.py and call its endpoint functions directly as plain Python. That means the real
endpoint bodies are exercised -- the actual ON CONFLICT SQL, the actual delete clause --
but NOT the HTTP layer: routing, status codes, query-parameter coercion and response
serialisation are unverified here and can only be checked in the deployment.

The Postgres branches of db.py are likewise unexercised. Every statement they run is a
sibling of the SQLite one in the same function, but "the SQLite path passes" is not
evidence about Postgres, which is what Render actually runs.

Run:  python3 backend/tests/test_phase2.py
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
#  Stubs: fastapi (absent) and flexpolyline (absent, needed by here_routing)   #
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
    # Real Query() is a marker object; returning the default makes direct calls behave
    # like an unsupplied query parameter, which is what these tests want.
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
_static.StaticFiles = lambda *a, **k: None
sys.modules.setdefault("fastapi.staticfiles", _static)

# --------------------------------------------------------------------------- #
#  Scratch DB                                                                  #
# --------------------------------------------------------------------------- #
TMP = tempfile.mkdtemp(prefix="rbe_phase2_")
os.environ.pop("DATABASE_URL", None)

import db  # noqa: E402
db._SQLITE_PATH = os.path.join(TMP, "scratch.db")

import conversions  # noqa: E402
import taxonomy  # noqa: E402
import network  # noqa: E402
import seed  # noqa: E402
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


def cols(table):
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return {r[1]: r for r in cur.fetchall()}
    finally:
        conn.close()


def table_sql(table):
    r = db.query("SELECT sql FROM sqlite_master WHERE name = ?", (table,))
    return r[0]["sql"] if r else ""


def tables():
    return {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}


def cell(m, q):
    return main.Cell(month_index=m, quantity=q)


# =========================================================================== #
#  1. Forecasts schema                                                         #
# =========================================================================== #
reset_db()
db.init_db()
c = cols("forecasts")

ok("forecasts.discipline exists", "discipline" in c)
ok("forecasts.section_id exists", "section_id" in c)
ok("forecasts.vehicle_type_2 removed", "vehicle_type_2" not in c)
ok("forecasts.split_pct removed", "split_pct" not in c)
ok("discipline is NOT NULL", "discipline" in c and c["discipline"][3] == 1)
ok("section_id is NOT NULL", "section_id" in c and c["section_id"][3] == 1)
ok("discipline defaults to ''", "discipline" in c and str(c["discipline"][4]).strip("'\"") == "")
ok("section_id defaults to ''", "section_id" in c and str(c["section_id"][4]).strip("'\"") == "")

sql = table_sql("forecasts")
ok("UNIQUE key is the 4-tuple",
   "UNIQUE (route_id, month_index, discipline, section_id)" in sql, sql[:120])
ok("old 2-part UNIQUE key is gone", "UNIQUE (route_id, month_index)\n" not in sql)

# =========================================================================== #
#  2. Clean-rebuild migration off the pre-Phase-2 table                        #
# =========================================================================== #
reset_db()
conn = db.get_conn()
cur = conn.cursor()
cur.execute("""
    CREATE TABLE forecasts (
        id TEXT PRIMARY KEY, route_id TEXT NOT NULL, month_index INTEGER NOT NULL,
        quantity REAL NOT NULL, unit TEXT NOT NULL, material_type TEXT,
        material_description TEXT, vehicle_type TEXT, vehicle_type_2 TEXT,
        split_pct INTEGER DEFAULT 100, submitted_by TEXT,
        status TEXT NOT NULL DEFAULT 'Pending', reject_reason TEXT,
        UNIQUE (route_id, month_index))
""")
for i in (1, 2, 3):
    cur.execute("INSERT INTO forecasts (id, route_id, month_index, quantity, unit, "
                "material_type, vehicle_type, status) VALUES (?,?,?,?,?,?,?,?)",
                (f"HR-EW-LEGACY::{i}", "HR-EW-LEGACY", i, 100.0, "t",
                 "Small aggregate", "Artic Tipper (44t)", "Approved"))
conn.commit()
conn.close()

db.init_db()
ok("migration: legacy rows are out of forecasts",
   db.query("SELECT COUNT(*) AS n FROM forecasts")[0]["n"] == 0)
ok("migration: forecasts_legacy retained", "forecasts_legacy" in tables())
ok("migration: legacy rows preserved in forecasts_legacy",
   db.query("SELECT COUNT(*) AS n FROM forecasts_legacy")[0]["n"] == 3)
ok("migration: new table has discipline", "discipline" in cols("forecasts"))

before = tables()
db.init_db()          # idempotent
ok("migration is idempotent (no second rename)", tables() == before)
ok("migration idempotent: forecasts still empty",
   db.query("SELECT COUNT(*) AS n FROM forecasts")[0]["n"] == 0)

# =========================================================================== #
#  3. Taxonomy                                                                 #
# =========================================================================== #
reset_db()
db.init_db()
db.init_network_db()
db.init_taxonomy_db()

for t in ("disciplines", "discipline_materials", "ipts", "design_sections", "work_sections"):
    ok(f"table {t} created", t in tables())
ok("design_section_disciplines NOT created", "design_section_disciplines" not in tables())

counts = taxonomy.seed_taxonomy()
ok("10 disciplines seeded", counts["disciplines"] == 10, str(counts))
ok("6 IPTs seeded", counts["ipts"] == 6, str(counts))
ok("3 design sections seeded", counts["design_sections"] == 3, str(counts))

again = taxonomy.seed_taxonomy()
ok("seed_taxonomy is idempotent", all(v == 0 for v in again.values()), str(again))

in_scope = taxonomy.list_disciplines()
ok("7 disciplines in scope", len(in_scope) == 7, str([d["id"] for d in in_scope]))
ok("disciplines come back in programme order",
   [d["sort_order"] for d in in_scope] == sorted(d["sort_order"] for d in in_scope))
all_disc = taxonomy.list_disciplines(include_out_of_scope=True)
ok("reserved disciplines seeded out of scope",
   {d["id"] for d in all_disc} - {d["id"] for d in in_scope} == {"mep", "ene", "ccs"})

ok("ballast serves substructure AND superstructure",
   "Large aggregate / ballast" in taxonomy.materials_for("substructure")
   and "Large aggregate / ballast" in taxonomy.materials_for("superstructure"))
ok("earthworks does not claim ballast",
   "Large aggregate / ballast" not in taxonomy.materials_for("earthworks"))

recv = taxonomy.derive_receives(["substructure", "superstructure"])
ok("derive_receives unions without duplicating", len(recv) == len(set(recv)))
ok("derive_receives covers both disciplines",
   set(taxonomy.materials_for("superstructure")).issubset(set(recv)))

# every seeded material category must exist in factors.json, or receives silently
# derives categories no location can ever match
known = set(conversions.material_names(conversions.load_factors()))
bad = {c for cats in taxonomy.DISCIPLINE_MATERIALS.values() for c in cats} - known
ok("every discipline material is a real factors.json category", not bad, str(bad))

ok("work_sections deliberately left unseeded (awaiting Appendix E)",
   len(taxonomy.list_work_sections()) == 0)
ok("disciplines carry their material categories for the picker",
   all("materials" in d for d in in_scope))
sub = [d for d in in_scope if d["id"] == "substructure"][0]
ok("a discipline's materials match its seed",
   sorted(sub["materials"]) == sorted(taxonomy.DISCIPLINE_MATERIALS["substructure"]),
   str(sub["materials"]))
ok("ipts.manager left NULL", all(i["manager"] is None for i in taxonomy.list_ipts()))
ok("IPTs seeded pre-merge, no merge modelling",
   all(i["merged_into"] is None for i in taxonomy.list_ipts()))

# =========================================================================== #
#  4. The widened key: upsert, coexist, scoped delete                          #
# =========================================================================== #
def save(route, disc, sect, qty, month=3, veh="Artic Tipper (44t)", mat="Small aggregate"):
    return main.save_matrix_row(main.MatrixRow(
        route_id=route, discipline=disc, section_id=sect,
        material_type=mat, vehicle_type=veh, unit="t",
        cells=[cell(month, qty)], submitted_by="tester"))


save("R-Q01-C01", "substructure", "WS1", 100.0)
save("R-Q01-C01", "substructure", "WS1", 250.0)
rows = db.query("SELECT * FROM forecasts WHERE route_id = 'R-Q01-C01'")
ok("re-saving a line UPDATES rather than duplicating", len(rows) == 1, f"got {len(rows)}")
ok("re-saved quantity is the new one", rows and rows[0]["quantity"] == 250.0)

save("R-Q01-C01", "superstructure", "WS7.3", 400.0)
rows = db.query("SELECT * FROM forecasts WHERE route_id = 'R-Q01-C01' AND month_index = 3")
ok("two disciplines coexist on one route-month", len(rows) == 2, f"got {len(rows)}")
ok("both disciplines kept their own quantity",
   sorted(r["quantity"] for r in rows) == [250.0, 400.0])

# the motivating failure: zeroing one line must not delete the other
save("R-Q01-C01", "substructure", "WS1", 0.0)
rows = db.query("SELECT * FROM forecasts WHERE route_id = 'R-Q01-C01' AND month_index = 3")
ok("zeroing one discipline deletes only that line", len(rows) == 1, f"got {len(rows)}")
ok("the surviving line is the other discipline",
   rows and rows[0]["discipline"] == "superstructure", str(rows))

ids = [r["id"] for r in db.query("SELECT id FROM forecasts")]
ok("no 'None' in any composed id", not any("None" in i for i in ids), str(ids))
ok("id carries all four key parts",
   all(len(i.split("::")) == 4 for i in ids), str(ids))

# unassigned sentinel must be '' and still constrain
save("R-Q02-C01", "", "", 50.0)
save("R-Q02-C01", "", "", 75.0)
rows = db.query("SELECT * FROM forecasts WHERE route_id = 'R-Q02-C01'")
ok("unassigned ('') lines still upsert rather than duplicate", len(rows) == 1, f"got {len(rows)}")
ok("unassigned discipline stored as '' not NULL", rows and rows[0]["discipline"] == "")

# =========================================================================== #
#  5. The double-counting caution                                              #
# =========================================================================== #
res = save("R-Q03-C01", "earthworks", "WS3", 10.0)
ok("no caution when a line is the only one on its route", res.get("caution") is None)
res = save("R-Q03-C01", "substructure", "WS3", 20.0)
ok("caution raised when another discipline is already on the route",
   res.get("caution") is not None)
ok("caution names the other line",
   res.get("caution") and res["caution"]["lines"][0]["discipline"] == "earthworks",
   str(res.get("caution")))
ok("save still reports success alongside the caution", res["status"] == "success")

# =========================================================================== #
#  6. Ledger and public matrix group per LINE, not per route                   #
# =========================================================================== #
ledger = main.forecasts_summary()
r01 = [g for g in ledger if g["route_id"] == "R-Q03-C01"]
ok("ledger shows one row per discipline line", len(r01) == 2, str(r01))
ok("ledger rows carry their discipline",
   {g["discipline"] for g in r01} == {"earthworks", "substructure"})

main.set_route_status("R-Q03-C01", main.StatusUpdate(status="Approved"),
                      discipline="earthworks", section_id="WS3")
st = {(r["discipline"], r["status"]) for r in
      db.query("SELECT discipline, status FROM forecasts WHERE route_id='R-Q03-C01'")}
ok("scoped approval touches only its own line",
   ("earthworks", "Approved") in st and ("substructure", "Pending") in st, str(st))

# withdrawal must be line-scoped for the same reason the zero-cell delete is
save("R-Q04-C01", "earthworks", "WS3", 11.0, month=7)
save("R-Q04-C01", "structures", "WS4", 22.0, month=7)
res = main.withdraw_route("R-Q04-C01", submitted_by="tester", from_=1, to=60,
                          discipline="earthworks", section_id="WS3")
left = db.query("SELECT discipline FROM forecasts WHERE route_id = 'R-Q04-C01'")
ok("scoped withdrawal removes only its own line", len(left) == 1, str(left))
ok("scoped withdrawal leaves the other discipline", left and left[0]["discipline"] == "structures")
ok("withdrawal reports what it deleted", res["deleted"] == 1 and res["scoped_to_line"], str(res))

unscoped = main.withdraw_route("R-Q04-C01", submitted_by="tester", from_=1, to=60)
ok("an unscoped withdrawal still clears the route", unscoped["scoped_to_line"] is False)
ok("route is empty after the unscoped withdrawal",
   db.query("SELECT COUNT(*) AS n FROM forecasts WHERE route_id='R-Q04-C01'")[0]["n"] == 0)

pm = main.public_forecast_matrix(from_=1, to=60, unit="t")
rows = [r for r in pm["routes"] if r["route_id"] == "R-Q03-C01"]
ok("public matrix keeps disciplines as separate rows", len(rows) == 1, str(rows))
ok("public matrix row carries discipline", rows and "discipline" in rows[0])
ok("public matrix no longer emits split_pct", rows and "split_pct" not in rows[0])

# =========================================================================== #
#  7. /api/meta routes off the live network                                    #
# =========================================================================== #
reset_db()
db.init_db()
db.init_network_db()
db.init_taxonomy_db()
network.seed_network()
network.backfill_location_roles()
network.backfill_supplies_receives()
taxonomy.seed_taxonomy()

# two locations really are both called 'Parnu terminal' in the seed
dupe = db.query("SELECT name, COUNT(*) AS n FROM locations GROUP BY name HAVING COUNT(*) > 1")
ok("the duplicate location name is present in the seed data",
   any(d["name"] == "Parnu terminal" for d in dupe), str(dupe))

PROF = "Artic Tipper (44t)"
GEOM = json.dumps([[24.0, 58.0], [24.1, 58.1]])       # stored serialised, as bake does
# 41.5 km in 0.60 h out and 0.55 h back is about 69 / 75 km/h — an ordinary Estonian
# trunk-road run for a truck, and nowhere near the dashboard's flat 45 km/h assumption.
OUT_HR, BACK_HR = 0.60, 0.55
network._upsert_geom("R-Q01-C01", PROF, GEOM, 41.5, OUT_HR, None, leg="loaded", alt_index=0)
network._upsert_geom("R-Q01-C02", PROF, GEOM, 12.25, 0.3, None, leg="loaded", alt_index=0)
network._upsert_geom("R-Q01-C01", PROF, GEOM, 99.0, 1.9, None,
                     leg="loaded", alt_index=1)      # an alternative, must be ignored
network._upsert_geom("R-Q01-C01", PROF, GEOM, 44.0, BACK_HR, None,
                     leg="return", alt_index=0)      # return leg: counts for cycle, not distance

mr = {r["route_id"]: r for r in network.meta_routes()}
ok("meta_routes returns every network route", len(mr) == 107, str(len(mr)))
ok("baked route reports its real distance", mr["R-Q01-C01"]["distance_km"] == 41.5,
   str(mr["R-Q01-C01"]["distance_km"]))
ok("distance ignores alternatives (alt_index 0 only)", mr["R-Q01-C01"]["distance_km"] != 99.0)
ok("distance ignores the return leg", mr["R-Q01-C01"]["distance_km"] != 44.0)
ok("distance names the vehicle it belongs to", mr["R-Q01-C01"]["distance_profile"] == PROF)
ok("unbaked route reports None, not 0", mr["R-Q05-C01"]["distance_km"] is None,
   str(mr["R-Q05-C01"]["distance_km"]))
ok("routes carry origin and dest names", bool(mr["R-Q01-C01"]["origin"])
   and bool(mr["R-Q01-C01"]["dest"]))
ok("routes still carry origin_id / dest_id",
   mr["R-Q01-C01"]["origin_id"] == "Q01" and mr["R-Q01-C01"]["dest_id"] == "C01")

labels = {r["origin"] for r in mr.values()} | {r["dest"] for r in mr.values()}
ok("duplicated location names are disambiguated by id",
   "Parnu terminal (C01)" in labels and "Parnu terminal (C02)" in labels, str(
       sorted(l for l in labels if "Parnu terminal" in l)))
ok("unique location names are left alone", "Muuga Harbour" in labels)

ok("material_guess normalised to a factors.json category",
   mr["R-P01-C03"]["material_guess"] in known, str(mr["R-P01-C03"]["material_guess"]))

# The regression this fixes: the retired legacy file had no distance key at all.
# seed_data/routes.json is now dead weight that nothing loads, so it may legitimately
# have been deleted from the repo — this records the finding when it is still there and
# says so plainly when it is not, rather than failing over a file that is allowed to go.
_legacy_path = os.path.join(BACKEND, "seed_data", "routes.json")
if os.path.exists(_legacy_path):
    legacy = json.load(open(_legacy_path, encoding="utf-8"))
    ok("the retired legacy file genuinely carried no distance",
       all("distance_km" not in r for r in legacy))
else:
    print("  note: seed_data/routes.json has been removed — "
          "skipping the historical no-distance check")
ok("meta now yields at least one non-zero distance",
   any((r["distance_km"] or 0) > 0 for r in mr.values()))

# the seaports and Kivimae, which an earlier note claimed were missing
names = {l["name"] for l in db.query("SELECT name FROM locations")}
ok("Muuga Harbour exists in the network", "Muuga Harbour" in names)
ok("Port of Parnu exists in the network", "Port of Parnu" in names)
ok("Kivimae III gravel exists in the network", "Kivimae III gravel" in names)
port_routes = db.query("SELECT COUNT(*) AS n FROM routes WHERE origin_id IN ('P01','P02')")
ok("both seaports are already routed", port_routes[0]["n"] == 16, str(port_routes))

# =========================================================================== #
#  8. Salvaged vendor / detail                                                 #
# =========================================================================== #
res = network.apply_node_meta()
ok("vendor salvage applied to 13 locations", res["applied"] == 13, str(res))
q07 = db.query("SELECT vendor, detail FROM locations WHERE id = 'Q07'")[0]
ok("Anelema limestone got its operator", q07["vendor"] == "OÜ Eesti Killustik", str(q07))
ok("material detail salvaged too", q07["detail"] == "Limestone - rockfill", str(q07))
again = network.apply_node_meta()
ok("salvage is additive — a second run changes nothing", again["applied"] == 0, str(again))
ok("the unmatched vendor row is retained, not silently dropped", res["unmatched"] == 1, str(res))

ok("locations.default_section_id exists but is left NULL",
   "default_section_id" in cols("locations")
   and all(r["default_section_id"] is None
           for r in db.query("SELECT default_section_id FROM locations")))

# =========================================================================== #
#  8b. Public map data — the replacement for a1_data.js                        #
# =========================================================================== #
_V = conversions.load_factors()["vehicles"]
tip = _V["Artic Tipper (44t)"]["load_minutes"] + _V["Artic Tipper (44t)"]["unload_minutes"]

# give one route geometry under a NON-default profile only, to exercise the fallback
network._upsert_geom("R-Q02-C01", "Rigid 7.5t", GEOM, 8.0, 0.2, None,
                     leg="loaded", alt_index=0)

fc = network.public_map_data()
lines = [f for f in fc["features"] if f["geometry"]["type"] == "LineString"]
nodes = [f for f in fc["features"] if f["geometry"]["type"] == "Point"]
by_route = {}
for f in lines:
    by_route.setdefault(f["properties"]["route_id"], []).append(f["properties"])

ok("public map emits only baked routes",
   set(by_route) == {"R-Q01-C01", "R-Q01-C02", "R-Q02-C01"}, str(sorted(by_route)))
ok("an unbaked route is absent, not drawn as a zero-length line",
   "R-Q05-C01" not in by_route)

types = {p["type"] for p in by_route["R-Q01-C01"]}
ok("both directions are emitted", types == {"Inbound Highway", "Outbound Highway"}, str(types))
ok("only the outbound leg exists where no return is baked",
   {p["type"] for p in by_route["R-Q01-C02"]} == {"Inbound Highway"})
ok("no Temp Haul Track features are produced",
   not any(p["type"] == "Temp Haul Track" for ps in by_route.values() for p in ps))

inb = [p for p in by_route["R-Q01-C01"] if p["type"] == "Inbound Highway"][0]
ok("the drawn vehicle is named on every feature", inb["vehicle_profile"] == PROF)
ok("the default profile is not flagged as a fallback", inb["profile_is_fallback"] is False)
ok("cycle time uses both legs plus per-vehicle turnaround",
   abs(inb["cycle_hr"] - round(OUT_HR + BACK_HR + tip / 60.0, 2)) < 1e-9, str(inb["cycle_hr"]))
ok("trips per day is derived from that cycle",
   inb["trips_per_day"] == int(10 // (OUT_HR + BACK_HR + tip / 60.0)), str(inb["trips_per_day"]))
ok("routes carry a disciplines list for the map filter",
   isinstance(inb["disciplines"], list))

fb = [p for p in by_route["R-Q02-C01"] if p["type"] == "Inbound Highway"][0]
ok("a route baked only under another vehicle still appears",
   fb["vehicle_profile"] == "Rigid 7.5t", str(fb["vehicle_profile"]))
ok("and it is flagged as a fallback rather than passed off as the default",
   fb["profile_is_fallback"] is True)
ok("the fallback route names every profile it has", fb["baked_profiles"] == ["Rigid 7.5t"])

ok("every location becomes a marker", len(nodes) == 27, str(len(nodes)))
q07n = [f["properties"] for f in nodes if f["properties"]["id"] == "Q07"][0]
ok("the salvaged vendor reaches the popup", "Eesti Killustik" in q07n["aux_info"], q07n["aux_info"])
ok("the salvaged detail reaches the popup too", "Limestone - rockfill" in q07n["aux_info"])
# the map popup calls props.material.toLowerCase() unguarded — a null would throw there
ok("material is always a string, never null",
   all(isinstance(f["properties"]["material"], str) for f in nodes))
ok("node_type is always a string", all(isinstance(f["properties"]["node_type"], str) for f in nodes))
ok("ports are typed so the map can treat them as origins",
   {f["properties"]["node_type"] for f in nodes if f["properties"]["id"] in ("P01", "P02")} == {"Port"})

# =========================================================================== #
#  9. Batch analysis feed for the dashboard                                    #
# =========================================================================== #
batch = main.routes_analysis_batch(route_ids="R-Q01-C01,R-Q01-C02,R-Q05-C01")
ok("batch returns the baked routes", set(batch["analysis"]) == {"R-Q01-C01", "R-Q01-C02"},
   str(set(batch["analysis"])))
ok("batch reports unbaked routes separately", batch["not_baked"] == ["R-Q05-C01"],
   str(batch["not_baked"]))
row = batch["analysis"]["R-Q01-C01"][PROF]
ok("batch returns only the primary option", row["alt_index"] == 0)
ok("batch cycle time uses real leg durations plus per-vehicle turnaround",
   abs(row["cycle_hr"] - (OUT_HR + BACK_HR + 24 / 60.0)) < 1e-6, str(row["cycle_hr"]))
ok("batch carries the planning constants the dashboard needs",
   "shift_hours_per_day" in batch["planning"])

# Why the dashboard had to stop computing its own. Two independent sources of divergence.
plan = conversions.load_factors()["planning"]


def dash_cycle(one_way_km, veh=None):
    """The formula the dashboard used: flat speed, one global turnaround for every vehicle."""
    return (one_way_km * 2) / plan["avg_haul_speed_kmh"] + \
           (plan["load_minutes"] + plan["unload_minutes"]) / 60.0


# a) speed — HERE's real durations against a flat 45 km/h
d = dash_cycle(41.5)
ok("dashboard formula diverges from real HERE durations",
   abs(d - row["cycle_hr"]) > 0.25, f"dash={d:.3f} backend={row['cycle_hr']:.3f}")
ok("and it diverges the wrong way — overstating the cycle on a fast road",
   d > row["cycle_hr"])

# b) turnaround — independent of speed, and worst on the Flatbed
veh = conversions.load_factors()["vehicles"]
glob = plan["load_minutes"] + plan["unload_minutes"]
flat = veh["Artic Flatbed (44t)"]["load_minutes"] + veh["Artic Flatbed (44t)"]["unload_minutes"]
tip = veh["Artic Tipper (44t)"]["load_minutes"] + veh["Artic Tipper (44t)"]["unload_minutes"]
ok("the global turnaround understates an Artic Flatbed by more than double",
   flat > 2 * glob, f"flatbed={flat}m global={glob}m")
ok("no single global turnaround can serve both artics",
   flat != tip, f"flatbed={flat}m tipper={tip}m")
ok("route_analysis resolves turnaround per vehicle, not globally",
   abs(row["turnaround_hr"] - tip / 60.0) < 1e-6, str(row["turnaround_hr"]))

# =========================================================================== #
#  9b. Quick fixes (2026-08-22)                                                #
# =========================================================================== #
# vendor / detail round-trip through create and update, and survive a body that
# does not mention them (an older client must not blank an operator name)
nid = network.create_location("Test Pit", "origin", materials=["Small aggregate"],
                              lat=58.5, lon=24.7, loc_type="Quarry",
                              vendor="OU Testija", detail="Sand")["id"]
row = db.query("SELECT vendor, detail FROM locations WHERE id = ?", (nid,))[0]
ok("create_location stores the operator", row["vendor"] == "OU Testija", str(row))
ok("create_location stores the detail", row["detail"] == "Sand")

network.update_location(nid, name="Test Pit", lat=58.6, lon=24.7)      # meta_given False
row = db.query("SELECT vendor FROM locations WHERE id = ?", (nid,))[0]
ok("an update that omits vendor does not blank it", row["vendor"] == "OU Testija", str(row))

network.update_location(nid, name="Test Pit", lat=58.6, lon=24.7,
                        vendor="OU Uus", detail=None, meta_given=True)
row = db.query("SELECT vendor, detail FROM locations WHERE id = ?", (nid,))[0]
ok("an update that names vendor does change it", row["vendor"] == "OU Uus", str(row))
ok("and it can clear the detail", row["detail"] is None, str(row))
network.delete_location(nid)

# the rejection reason has to reach the people who need to read it
save("R-Q06-C01", "earthworks", "WS3", 5.0, month=2)
main.set_route_status("R-Q06-C01", main.StatusUpdate(status="Rejected",
                      reject_reason="Volumes exceed the approved haul allowance"),
                      discipline="earthworks", section_id="WS3")
led = [g for g in main.forecasts_summary() if g["route_id"] == "R-Q06-C01"][0]
ok("the ledger carries the rejection reason",
   led["reject_reason"] == "Volumes exceed the approved haul allowance", str(led.get("reject_reason")))
ok("and the row reads as Rejected", led["status"] == "Rejected")
rows = main.list_forecasts(route_id="R-Q06-C01")
ok("the reason is on the raw rows the submitter's view reads",
   all(r["reject_reason"] for r in rows), str(rows[:1]))

# every material category must name at least one vehicle, or the restricted dropdown
# would leave a submitter unable to pick anything at all
_cats = conversions.load_factors()["material_categories"]
_bad = [k for k, v in _cats.items() if not k.startswith("_") and not (v.get("vehicles") or [])]
ok("every material category lists usable vehicles", not _bad, str(_bad))
_vnames = set(conversions.vehicle_names(conversions.load_factors()))
_unknown = {v for k, c in _cats.items() if not k.startswith("_")
            for v in (c.get("vehicles") or []) if v not in _vnames}
ok("every vehicle named by a category actually exists", not _unknown, str(_unknown))
ok("aggregates do not permit a flatbed",
   "Artic Flatbed (44t)" not in (_cats["Large aggregate / ballast"].get("vehicles") or []))

# =========================================================================== #
#  10. Retired paths are actually gone (source-level)                          #
# =========================================================================== #
main_src = open(os.path.join(BACKEND, "main.py"), encoding="utf-8").read()
conv_src = open(os.path.join(BACKEND, "conversions.py"), encoding="utf-8").read()

ok("_load_routes() is gone from main.py", "def _load_routes" not in main_src)
ok("nothing loads seed_data/routes.json any more",
   'SEED_DIR / "routes.json"' not in main_src)
ok("the forecast insert no longer writes vehicle_type_2",
   "vehicle_type_2, split_pct, submitted_by" not in main_src)
ok("ON CONFLICT targets the widened key",
   "ON CONFLICT (route_id, month_index, discipline, section_id)" in main_src)
ok("no narrow ON CONFLICT survives",
   "ON CONFLICT (route_id, month_index)" not in main_src)
ok("the zero-cell delete is scoped to the line",
   "AND discipline = ? AND section_id = ?" in main_src)
# strip comments first — main.py explains in prose why these calls were removed, and a
# raw substring check would match the explanation and pass for the wrong reason
main_code = "\n".join(l.split("#", 1)[0] for l in main_src.splitlines())
ok("the startup no longer calls the legacy forecast seeder",
   "seed.seed_if_empty()" not in main_code and "seed.reseed()" not in main_code)
ok("the removal is explained in main.py, not silent", "seed.seed_if_empty()" in main_src)
ok("seed_if_empty() is a no-op", seed.seed_if_empty() is False)
ok("reseed() is a no-op", seed.reseed() is False)
ok("effective_payload is marked deprecated, not silently kept",
   "DEPRECATED in Phase 2" in conv_src)

# =========================================================================== #
print()
for f in FAIL:
    print("  FAIL:", f)
print(f"\n{PASS} passed, {len(FAIL)} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)

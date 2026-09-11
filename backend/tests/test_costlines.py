"""
Cost lines, 2026-09-11 — /api/costing/lines, the one read behind the Dashboard and the Forecasts page.

What is asserted: the endpoint's shape; that every figure equals what the Look-ahead's
own functions give for the same line (trips rounded up, km only when baked, CO2 = km x
the vehicle's factor); the three prices in four units and their arithmetic; totals that
exclude unbaked lines and say so; the access filter (an IPT code sees only its lines);
the month range and status filters; the statement budget; and — at source level — that
the Dashboard and the Forecasts page READ this and derive nothing.

WHAT THIS DOES NOT PROVE
------------------------
  * The HTTP layer is stubbed; the query-string parsing of ?from=&to=&status= is not run.
  * Nothing in a browser; Chart.js never draws here (render_frontend.js renders the page
    around empty canvases).

Run:  python3 backend/tests/test_costlines.py
"""
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
ROOT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)

# ---- stubs, verbatim from test_lookahead.py --------------------------------------
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


class _StaticFiles:
    def __init__(self, *a, **k):
        pass

    async def get_response(self, path, scope):
        return None


_static.StaticFiles = _StaticFiles
sys.modules.setdefault("fastapi.staticfiles", _static)

TMP = tempfile.mkdtemp(prefix="rbe_costlines_")
os.environ.pop("DATABASE_URL", None)
os.environ.pop("ADMIN_TOKEN", None)

import db  # noqa: E402
db._SQLITE_PATH = os.path.join(TMP, "scratch.db")
import conversions  # noqa: E402
import network  # noqa: E402
import weeks  # noqa: E402
import main  # noqa: E402
import access  # noqa: E402
import days  # noqa: E402
import derived  # noqa: E402
import costing  # noqa: E402
import fuel  # noqa: E402
import lookahead  # noqa: E402
import export  # noqa: E402
import fairprice  # noqa: E402
import config  # noqa: E402
import costlines  # noqa: E402
for _v in ("IPT1_CODE", "IPT2_CODE", "IPT3_CODE", "IPT4_CODE", "IPT5_CODE", "IPT6_CODE",
           "PLANNER_CODE", "ADMIN_CODE"):
    os.environ.pop(_v, None)
access.set_current("planner123")
days.START_YEAR = main.START_YEAR

PASS = 0
FAIL = []


def ok(label, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{label} {extra}".strip())


def _raises(fn):
    try:
        fn()
    except Exception as e:
        return e
    return None


def reset_db():
    if os.path.exists(db._SQLITE_PATH):
        os.remove(db._SQLITE_PATH)
    db.init_db()
    db.init_network_db()
    db.init_taxonomy_db()
    db.init_zones_db()
    db.init_gates_db()
    db.init_weeks_db()
    db.init_lookahead_db()
    db.init_config_db()
    db.init_costing_db()
    db.init_tenant()
    import config as _cfg
    _cfg.invalidate()
    costing.invalidate()




def _code_tokens(src):
    """NAME and NUMBER tokens only — docstrings and comments may NAME a thing the code must not hold."""
    import io as _io
    import tokenize
    names, nums = set(), set()
    for t in tokenize.generate_tokens(_io.StringIO(src).readline):
        if t.type == tokenize.NAME:
            names.add(t.string.lower())
        elif t.type == tokenize.NUMBER:
            try:
                nums.add(float(t.string))
            except ValueError:
                pass
    return names, nums



# =========================================================================== #
#  0. A database with four lines: rigid baked · artic baked · unbaked · another IPT   #
# =========================================================================== #
reset_db()
access.set_current("planner123")
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)", ("L1", "Pit", 58.5, 24.0))
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)", ("L2", "Site", 58.6, 24.4))
for _rid in ("R1", "R2", "R3", "R4"):
    db.execute("INSERT INTO routes (id, origin_id, dest_id, ipt) VALUES (?, ?, ?, ?)", (_rid, "L1", "L2", "IPT 1"))
V8 = "Rigid 8-wheeler (32t)"
V12 = "N3 tractor (BC) + O4 tipping semi (DA) GCW 40/44 t"


def _geom(rid, prof, leg, km, hr):
    db.execute("INSERT INTO route_geometry (tenant_id, route_id, vehicle_profile, leg, alt_index, "
               "geometry, distance_km, duration_hr) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               ("default", rid, prof, leg, 0, "[[24,58.5],[24.4,58.6]]", km, hr))


for _rid, _v in (("R1", V8), ("R2", V12), ("R4", V8)):
    _geom(_rid, _v, "loaded", 30.0, 0.75); _geom(_rid, _v, "return", 30.0, 0.65)
MONTHS = (9, 10, 11)


def _line(rid, sect, qty, veh, ipt, status="Approved"):
    main.save_matrix_row(main.MatrixRow(
        route_id=rid, discipline="earthworks", section_id=sect, material_type="Small aggregate",
        material_description=None, vehicle_type=veh, submitted_by="tester", unit="t",
        status="Pending", cells=[main.Cell(month_index=m, quantity=qty) for m in MONTHS], ipt=ipt))
    if status == "Approved":
        main.set_route_status(rid, main.StatusUpdate(status="Approved"), discipline="earthworks", section_id=sect)


_line("R1", "WS1", 4010.0, V8, "IPT1")            # rigid, baked; 4010 t does NOT divide by 20 t (ceil matters)
_line("R2", "WS2", 2600.0, V12, "IPT1")           # artic, baked
_line("R3", "WS3", 900.0, V8, "IPT1")             # UNBAKED
_line("R4", "WS4", 1000.0, V8, "IPT2", status="Pending")   # another IPT, still Pending
fuel.set_manual(1.922, "2026-09-07", by="admin")
network.set_route_planning("R2", {"rate_eur_per_t"}, rate_eur_per_t=3.0)
costing.set_target({"rate_eur_per_load": 45.0, "rate_eur_per_km": 1.5}, by="admin")

res = main.costing_lines()
L = res["lines"]
ok("the endpoint returns one row per line-month with totals and the costing block",
   len(L) == 12 and isinstance(res.get("totals"), dict) and isinstance(res.get("costing"), dict)
   and res.get("working_days_per_month") == 22)
byk = {(l["route_id"], l["month_index"]): l for l in L}
r1 = byk[("R1", 9)]
ok("⭐ trips are ROUNDED UP per line-month: 4010 t / 20 t = 200.5 → 201, not 200.5",
   r1["trips"] == 201 and r1["tonnes"] == 4010.0)
ok("...the same figure derived.py gives for that quantity (one source)",
   r1["trips"] == derived.day_figures(4010.0, derived.line_context(
       {"route_id": "R1", "unit": "t", "material_type": "Small aggregate", "vehicle_type": V8, "month_index": 9,
        "week": {"planned_qty": 4010.0}}, conversions.load_factors()), conversions.load_factors())["trips"])
ok("km = trips × km/trip (60 km round trip) on a baked line; t·km on the route basis",
   r1["baked"] is True and r1["km_trip"] == 60.0 and abs(r1["km"] - 201 * 60.0) < 0.01 and abs(r1["tonne_km"] - 4010.0 * 60.0) < 0.1)
ok("CO₂ = km × the vehicle's kg CO₂e/km ÷ 1000 (0.95 for the 32 t rigid)",
   abs(r1["co2_t"] - round(201 * 60.0 * 0.95 / 1000.0, 3)) < 0.002)
ok("vehicles = trips ÷ (working days × cycles per vehicle-day), rounded up: 201 ÷ (22 × 5) → 2",
   r1["cycles_per_vehicle_day"] == 5 and r1["vehicles"] == 2)
ok("🔴 the UNBAKED line has no km, t·km, CO₂, vehicles or fair € — and is flagged, not estimated",
   byk[("R3", 9)]["baked"] is False and byk[("R3", 9)]["km"] is None and byk[("R3", 9)]["co2_t"] is None
   and byk[("R3", 9)]["vehicles"] is None and byk[("R3", 9)]["fair"]["eur"] is None and "UNBAKED" in byk[("R3", 9)]["flags"]
   and byk[("R3", 9)]["trips"] == 45)
# the three prices in four units
p1, f1 = r1["planned"], r1["fair"]
ok("R1 (no route rate) is planned at the TARGET: trips × 45 + trips × 60 km × 1.5, source 'target'",
   p1["source"] == "target" and abs(p1["eur"] - round(201 * 45.0 + 201 * 60.0 * 1.5, 2)) < 0.011)
ok("⭐ per_t = € / tonnes · per_trip = € / trips · per_km = € / (trips × basis km)",
   abs(p1["per_t"] - round(p1["eur"] / 4010.0, 2)) < 0.011 and abs(p1["per_trip"] - round(p1["eur"] / 201, 2)) < 0.011
   and abs(p1["per_km"] - round(p1["eur"] / (201 * 60.0), 3)) < 0.0011)
ok("...per_trip of the target on this route is exactly 45 + 60 × 1.5 = € 135", p1["per_trip"] == 135.0)
ok("the fair price is there too, in the same four units, with its flags and fuel share",
   f1["eur"] is not None and f1["per_t"] is not None and f1["per_trip"] is not None and f1["per_km"] is not None
   and abs(f1["per_trip"] - round(f1["eur"] / 201, 2)) < 0.011 and isinstance(f1["flags"], list) and f1["fuel_share_pct"] is not None)
ok("...and equals trips × the model's € / trip for that vehicle, distance and month",
   abs(f1["eur"] - round(201 * fairprice.trip_cost({"baked": True, "distance_km": 30.0, "return_km": 30.0, "cycle_min": r1["cycle_min"],
                                                     "payload_t": 20.0, "vehicle_class": "rigid"}, 9, 1.922,
                                                    fairprice.params(conversions.load_factors()))["eur_trip"], 2)) < 0.02)
ok("November is a winter month: R1's fair € in month 11 is higher than in month 9 and carries WINTER",
   byk[("R1", 11)]["fair"]["eur"] > byk[("R1", 9)]["fair"]["eur"] and "WINTER" in byk[("R1", 11)]["fair"]["flags"])
r2 = byk[("R2", 9)]
ok("R2 prices from its OWN €/t (source 'route'), never mixed with the target: € = 2600 × 3",
   r2["planned"]["source"] == "route" and abs(r2["planned"]["eur"] - 7800.0) < 0.011 and r2["planned"]["per_t"] == 3.0
   and r2["vehicle_class"] == "artic")
ok("a line-month with no rate and no target prices nothing; the unbaked one still gets the target's per-load term, marked partial",
   byk[("R3", 9)]["planned"]["source"] == "target" and byk[("R3", 9)]["planned"]["partial"] is True
   and byk[("R3", 9)]["planned"]["per_km"] is None)

# totals
T = res["totals"]
ok("totals: 12 line-months, 9 baked; excludes_unbaked true; tonnes and trips over ALL lines",
   T["lines"] == 12 and T["baked_lines"] == 9 and T["excludes_unbaked"] is True
   and abs(T["tonnes"] - 3 * (4010 + 2600 + 900 + 1000)) < 0.01 and T["trips"] == sum(l["trips"] for l in L))
ok("...km, t·km and CO₂ over the BAKED lines only", abs(T["km"] - sum(l["km"] for l in L if l["baked"])) < 0.01
   and abs(T["co2_t"] - sum(l["co2_t"] for l in L if l["baked"])) < 0.01)
ok("...kg CO₂e / t is over the baked tonnes, not all tonnes",
   abs(T["co2_kg_per_t"] - round(T["co2_t"] * 1000.0 / sum(l["tonnes"] for l in L if l["baked"]), 2)) < 0.02)
ok("...planned and fair totals in four units, weighted over the lines that have them",
   T["planned"]["eur"] is not None and T["planned_lines"] == 12 and T["planned_target_lines"] == 9
   and abs(T["planned"]["per_t"] - round(T["planned"]["eur"] / T["tonnes"], 2)) < 0.011
   and T["fair"]["eur"] is not None and T["fair_lines"] == 9
   and abs(T["fair"]["per_t"] - round(T["fair"]["eur"] / sum(l["tonnes"] for l in L if l["baked"]), 2)) < 0.011)

# filters
ok("?from/?to narrow the months; ?status narrows the status",
   len(main.costing_lines(from_=10, to=10)["lines"]) == 4 and len(main.costing_lines(status="Approved")["lines"]) == 9
   and len(main.costing_lines(status="Pending")["lines"]) == 3 and len(main.costing_lines(status="All")["lines"]) == 12)
# access
_codes = access.codes() if hasattr(access, "codes") else None
access.set_current("submitter123")
sub = main.costing_lines()
ok("a submitter (no IPT scope in demo mode) sees every line — same rule as /api/forecasts", len(sub["lines"]) == 12)
access.set_current("planner123")
# the IPT filter: an IPT code sees ONLY its IPT's lines. Demo mode has no IPT codes; set one.
os.environ["IPT2_CODE"] = "ipt2-secret"
try:
    access.set_current("ipt2-secret")
    i2 = main.costing_lines()
    ok("🔴 an IPT code sees only its own IPT's line-months (IPT2: R4 × 3 months), and the totals are of those",
       len(i2["lines"]) == 3 and all(l["route_id"] == "R4" for l in i2["lines"]) and i2["totals"]["lines"] == 3, str(len(i2["lines"])))
finally:
    os.environ.pop("IPT2_CODE", None)
    access.set_current("planner123")

# statement budget: one read per (route, vehicle) cycle, not per row
import collections as _co
_orig_q, _orig_x = db.query, db.execute
_cnt = _co.Counter()
def _q(sql, *a, **k):
    _cnt["query"] += 1
    return _orig_q(sql, *a, **k)
def _x(sql, *a, **k):
    _cnt["execute"] += 1
    return _orig_x(sql, *a, **k)
db.query, db.execute = _q, _x
try:
    costing.invalidate(); _cnt.clear(); main.costing_lines(); c = dict(_cnt)
    ok(f"the read writes nothing and stays under a statement budget for 12 line-months on 4 routes ({c.get('query', 0)} reads)",
       c.get("execute", 0) == 0 and c.get("query", 0) <= 40, str(c))
finally:
    db.query, db.execute = _orig_q, _orig_x

# the fixture the render harness reads
_fix_dir = os.path.join(HERE, "fixtures")
os.makedirs(_fix_dir, exist_ok=True)
def _scrub(o):
    if isinstance(o, dict):
        return {k: ("<ts>" if k.endswith("_at") and isinstance(v, str) else _scrub(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [_scrub(x) for x in o]
    return o
with open(os.path.join(_fix_dir, "cost_lines.json"), "w", encoding="utf-8") as _fh:
    _fh.write(json.dumps(_scrub(res), indent=1, ensure_ascii=False))
ok("the cost-lines fixture is written (12 rows, planned + fair, an unbaked line)", os.path.exists(os.path.join(_fix_dir, "cost_lines.json")))

# =========================================================================== #
#  1. Source level — the pages READ, they do not derive                        #
# =========================================================================== #
_fe = open(os.path.join(ROOT, "frontend", "index.html"), encoding="utf-8").read()
_db = _fe[_fe.index("function Dashboard("):_fe.index("function DashboardStock(")]
ok("🔴 the Dashboard reads /costing/lines and NOT /forecasts or /routes/analysis-batch",
   "fetch(`${API}/costing/lines?status=All`)" in _db and "/routes/analysis-batch" not in _db and "`${API}/forecasts`" not in _db)
ok("...and derives nothing: no tonnes ÷ payload, no CO₂ factor, no cycle arithmetic in the browser",
   "/ payload(" not in _db and "emisFactor" not in _db and "toTonnes(" not in _db and "avg_haul_speed" not in _db
   and "emisFactor = " not in _fe)
ok("...its fleet figure is the server's `vehicles`", "r.vehicles" in _db and "capacityPerDay)" not in _db.replace("capacityPerDay: r.cycles_per_vehicle_day", ""))
ok("the Dashboard has IPT and discipline filters, a cost group, a cost-over-time chart, and planned / fair / fair €/t columns",
   'value={ipt}' in _db and 'value={discipline}' in _db and 'title="Cost"' in _db and "Cost over time" in _db
   and 'setSort("planned")' in _db and 'setSort("fair")' in _db and 'setSort("fairPerT")' in _db)
_fc = _fe[_fe.index("function Forecasts("):_fe.index("function CostingTab(")]
ok("the Forecasts page reads /costing/lines (its own fetch, so a failure never hides the list) and joins per line",
   "fetch(`${API}/costing/lines?status=All`)" in _fc and "costByLine[g.key]" in _fc and "if(!c) return" in _fc)
ok("...with a Cost column (planned + fair, €/t beside fair) and nine cost columns in the CSV",
   'data-cost-cell="1"' in _fc and '"Fair € (model)", "Fair €/t", "Fair €/trip", "Fair €/km"' in _fc)
ok("the Look-ahead prints the unit triple (€/t · €/trip · €/km) under planned and fair on Account and in the expanded row",
   "unitTriple(r.planned_units)" in _fe and "unitTriple(r.fair_units)" in _fe and "unitTriple(wd.fair_units)" in _fe and "unitTriple(wd.eur_units)" in _fe)
ok("🔴 no cost on the public map", "costing/lines" not in open(os.path.join(ROOT, "map", "index.html"), encoding="utf-8").read())

print()
for f in FAIL:
    print("  FAIL:", f)
print(f"\n{PASS} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)

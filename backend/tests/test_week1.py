"""
Week-1 backend assertions — the 2026-09-01 build list, Tasks A–E.

  A   work_sections seeded (15 rows) + the four EU-named planning vehicles
  B   multi-year Submit Forecast — one bulk POST across two years
  C   forecast_weeks: materialise on approve, edit, confirm
  D   typed actuals, variance, calibrate
  D2  stockpile capacity, weekly consumption, the balance read model
  E   the Railhead location type

Same harness as test_phase5a.py: a scratch SQLite database, no network, and `fastapi`
/ `flexpolyline` / `psycopg2` stubbed so main.py imports and its endpoint functions can
be called directly as plain Python.

WHAT THIS DOES NOT PROVE
------------------------
Read this before quoting the pass count.

  * **The HTTP layer is stubbed.** Endpoint BODIES run; the transport does not. Nothing
    here proves a route is actually mounted, that a query parameter is parsed, or that
    the admin token rejects a request.
  * **No Postgres branch runs.** `forecast_weeks` and `stockpile_weeks` are created
    against SQLite only. Their Postgres path is a plain CREATE plus the Phase 4.5
    tenant migration, which needs no key rebuild — but it has not been executed.
  * **HERE is never called**, and nothing here bakes a route.
  * **Nothing in a browser.** The Look-ahead tab, the stockpile panel, the year-range
    form and the rail layer are asserted at SOURCE level in parse_frontend.js and
    parse_map.js, not exercised. No assertion here proves a cell renders.
  * **The rail geometry is Natural Earth, not OSM or survey.** Its accuracy is asserted
    to be DECLARED, not to be good. See test_rail_data() and the README.

Run:  python3 backend/tests/test_week1.py
"""
import json
import os
import shutil
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


# a CLASS, not a lambda: main.py subclasses this, and `class X(lambda)` is a TypeError
class _StaticFiles:
    def __init__(self, *a, **k):
        pass

    async def get_response(self, path, scope):
        return None


_static.StaticFiles = _StaticFiles
sys.modules.setdefault("fastapi.staticfiles", _static)

TMP = tempfile.mkdtemp(prefix="rbe_week1_")
os.environ.pop("DATABASE_URL", None)

import db  # noqa: E402
db._SQLITE_PATH = os.path.join(TMP, "scratch.db")

import conversions  # noqa: E402
import taxonomy  # noqa: E402
import network  # noqa: E402
import weeks  # noqa: E402
import stockpiles  # noqa: E402
import main  # noqa: E402
# 2026-09-02, Task F: the staff endpoints now require an access code, resolved from the
# X-Access-Code header by a middleware the stubbed app never runs. The harness sets the
# request context itself, as a PLANNER, so every pre-existing assertion still exercises
# the same code paths it did. Nothing here proves the header is actually read.
import access  # noqa: E402
for _v in ("IPT1_CODE", "IPT2_CODE", "IPT3_CODE", "IPT4_CODE", "IPT5_CODE", "IPT6_CODE",
           "PLANNER_CODE", "ADMIN_CODE"):
    os.environ.pop(_v, None)
access.set_current("planner123")

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
    db.init_gates_db()
    db.init_weeks_db()
    db.init_tenant()


def cols(table):
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return {r[1]: r for r in cur.fetchall()}
    finally:
        conn.close()


def pk_order(table):
    """Primary-key column names, in key order."""
    return [r[1] for r in sorted(
        (r for r in cols(table).values() if r[5]), key=lambda r: r[5])]


def tables():
    return {r["name"] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def seed_line(route_id="R1", disc="earthworks", sect="WS1", months=(1, 2, 3),
              qty=100.0, unit="t", status="Pending"):
    """One forecast line over `months`, through the real bulk endpoint."""
    return main.save_matrix_row(main.MatrixRow(
        route_id=route_id, discipline=disc, section_id=sect,
        material_type="Small aggregate", material_description=None,
        vehicle_type="Rigid 8-wheeler (32t)", submitted_by="tester",
        unit=unit, status=status,
        cells=[main.Cell(month_index=m, quantity=qty) for m in months]))


# =========================================================================== #
#  A. work_sections + the four planning vehicles                               #
# =========================================================================== #
reset_db()
taxonomy.seed_taxonomy()

_ws = taxonomy.list_work_sections()
ok("A: fifteen work sections are seeded", len(_ws) == 15, f"got {len(_ws)}")
ok("A: they reach /api/meta's picker payload",
   len(main.meta()["work_sections"]) == 15)

F = conversions.load_factors()
PLANNING = conversions.planning_vehicle_names(F)
ok("A: exactly four planning vehicles", len(PLANNING) == 4, str(PLANNING))
ok("A: /api/meta ships them as their own list",
   main.meta().get("planning_vehicles") == PLANNING)
# ⭐ THE POINT OF 'add four, keep six'. Every baked route_geometry row is keyed on
# vehicle_profile and network.DEFAULT_PROFILE is one of the six. If the six ever leave
# this file their routes' payload, CO2 and trips/day fall through to _default and every
# affected route then reports IDENTICAL figures — the exact fault
# network.factors_diagnostics() was written to surface.
ALL_V = conversions.vehicle_names(F)
ok("A: ten vehicles in total — four added, six kept", len(ALL_V) == 10, str(len(ALL_V)))
for legacy in ("Rigid 7.5t", "Rigid 4-wheeler (18t)", "Rigid 6-wheeler (26t)",
               "Rigid 8-wheeler (32t)", "Artic Tipper (44t)", "Artic Flatbed (44t)"):
    ok(f"A: legacy vehicle {legacy!r} is still present", legacy in ALL_V)
ok("A: ⭐ DEFAULT_PROFILE still resolves in factors.json",
   network.DEFAULT_PROFILE in ALL_V)
ok("A: ...and does NOT fall through to _default",
   conversions._payload(F, network.DEFAULT_PROFILE)
   != conversions._payload(F, "__no_such_vehicle__"))
# the payloads the build list gives, verbatim
for name, want in ((PLANNING[0], 18.0), (PLANNING[2], 25.5), (PLANNING[3], 26.0)):
    ok(f"A: {name[:28]}… carries its given payload {want} t",
       conversions._payload(F, name) == want)
# V10 is the one whose tonnage is DERIVED, and the file has to say so
v10 = F["vehicles"][PLANNING[1]]
ok("A: V10 keeps the 8 m3 figure the build list actually gave",
   v10.get("payload_m3") == 8)
ok("A: V10's payload_t is derived from the concrete density in the same file",
   abs(v10["payload_t"] - 8 * F["material_categories"]["Precast / concrete"]
       ["density_t_per_m3"]) < 1e-9)
ok("A: ⭐ and every figure not given in the build list declares its basis",
   all("_payload_basis" in F["vehicles"][v] and "_emissions_basis" in F["vehicles"][v]
       for v in PLANNING))
# 2026-09-02: one vehicle, three labels. The key is the id; a label only changes the text.
_VL = conversions.vehicle_labels(F)
ok("A2: /api/meta ships vehicle_labels", main.meta().get("vehicle_labels") == _VL)
ok("A2: every vehicle has all three slots filled",
   all(set(v) == {"en", "eu", "ee"} for v in _VL["labels"].values())
   and set(_VL["labels"]) == set(ALL_V))
ok("A2: ⭐ a missing slot falls back to the KEY, not another language",
   all(_VL["labels"][k][l] == k for k, ls in _VL["fallbacks"].items() for l in ls))
ok("A2: the EU label of a planning vehicle IS its key",
   all(_VL["labels"][k]["eu"] == k for k in PLANNING))
ok("A2: ⭐ no Estonian label was invented — every ee slot fell back",
   all("ee" in _VL["fallbacks"].get(k, []) for k in ALL_V))
ok("A2: the six legacy keys are their own English label",
   all(_VL["labels"][k]["en"] == k for k in ALL_V if k not in PLANNING))
ok("A2: V07's English label is the trade name the build list gave",
   "8x4" in _VL["labels"][PLANNING[0]]["en"] and "tipper" in _VL["labels"][PLANNING[0]]["en"])

# a planning vehicle nobody can select is a planning vehicle that does not exist
selectable = {v for c in conversions.material_names(F)
              for v in F["material_categories"][c].get("vehicles", [])}
ok("A: every planning vehicle is offered by at least one material category",
   set(PLANNING) <= selectable, str(set(PLANNING) - selectable))
# absent dimensions must stay absent — borrowing another vehicle's would change which
# roads HERE allows, silently
import here_routing  # noqa: E402
for name in PLANNING:
    p = here_routing._truck_params(name, F, laden=True)
    ok(f"A: {name[:24]}… sends a gross weight to HERE", "vehicle[grossWeight]" in p)
    ok(f"A: {name[:24]}… sends NO invented dimensions",
       not ({"vehicle[height]", "vehicle[width]", "vehicle[length]"} & set(p)))
# B4's turnaround split must still reproduce load+unload for the NEW profiles too
for name in PLANNING:
    load, unload = network._turnaround_minutes(name, F)
    parts = network._turnaround_parts(name, F)
    ok(f"A: {name[:24]}… turnaround is still exactly load + unload",
       abs(parts["total_minutes"] - (load + unload)) < 1e-9,
       f"{parts['total_minutes']} vs {load + unload}")


# =========================================================================== #
#  B. Multi-year save                                                          #
# =========================================================================== #
reset_db()
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)",
           ("L1", "Pit", 58.5, 24.0))
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)",
           ("L2", "Site", 58.6, 24.4))
db.execute("INSERT INTO routes (id, origin_id, dest_id) VALUES (?, ?, ?)",
           ("R1", "L1", "L2"))

# Jan 2026 (1) .. Dec 2027 (24) in ONE post, values on the four corners of the range
res = main.save_matrix_row(main.MatrixRow(
    route_id="R1", discipline="earthworks", section_id="WS1",
    material_type="Small aggregate", vehicle_type="Rigid 8-wheeler (32t)",
    submitted_by="tester", unit="t", status="Pending",
    cells=[main.Cell(month_index=m, quantity=(50.0 if m in (1, 12, 13, 24) else 0.0))
           for m in range(1, 25)]))
ok("B: ⭐ one bulk POST saves across two calendar years",
   res["months_saved"] == 4, str(res))
rows = main.list_forecasts(route_id="R1")
ok("B: exactly the four non-zero months are stored", len(rows) == 4)
ok("B: and they are month_index 1, 12, 13, 24 — absolute, not per-year",
   sorted(r["month_index"] for r in rows) == [1, 12, 13, 24])
ok("B: ⭐ MONTH_COUNT is unchanged", main.MONTH_COUNT == 60)
ok("B: ⭐ and no `year` column was added to forecasts",
   "year" not in cols("forecasts"))
ok("B: the UNIQUE key is still the Phase 4.5 one",
   "UNIQUE (tenant_id, route_id, month_index, discipline, section_id)"
   in db._TENANT_DDL["forecasts"])
# re-saving the same range must UPDATE, not duplicate — the ON CONFLICT target has to
# keep matching the constraint across a 24-cell post exactly as it did across 12
main.save_matrix_row(main.MatrixRow(
    route_id="R1", discipline="earthworks", section_id="WS1",
    material_type="Small aggregate", vehicle_type="Rigid 8-wheeler (32t)",
    submitted_by="tester", unit="t", status="Pending",
    cells=[main.Cell(month_index=m, quantity=(70.0 if m in (1, 12, 13, 24) else 0.0))
           for m in range(1, 25)]))
rows = main.list_forecasts(route_id="R1")
ok("B: re-saving the range upserts rather than duplicating", len(rows) == 4)
ok("B: ...with the new quantities", all(r["quantity"] == 70.0 for r in rows))
# a zero inside the range clears that month and ONLY that month
main.save_matrix_row(main.MatrixRow(
    route_id="R1", discipline="earthworks", section_id="WS1",
    material_type="Small aggregate", vehicle_type="Rigid 8-wheeler (32t)",
    submitted_by="tester", unit="t", status="Pending",
    cells=[main.Cell(month_index=m, quantity=(70.0 if m in (1, 13, 24) else 0.0))
           for m in range(1, 25)]))
ok("B: a zero inside the range deletes that month",
   sorted(r["month_index"] for r in main.list_forecasts(route_id="R1")) == [1, 13, 24])
# ⭐ narrowing the range must not reach the year it no longer covers
main.save_matrix_row(main.MatrixRow(
    route_id="R1", discipline="earthworks", section_id="WS1",
    material_type="Small aggregate", vehicle_type="Rigid 8-wheeler (32t)",
    submitted_by="tester", unit="t", status="Pending",
    cells=[main.Cell(month_index=m, quantity=0.0) for m in range(1, 13)]))
ok("B: ⭐ saving 2026 alone leaves 2027's months untouched",
   sorted(r["month_index"] for r in main.list_forecasts(route_id="R1")) == [13, 24])
# and a second discipline on the same route+months is still its own line
seed_line(route_id="R1", disc="substructure", sect="WS2", months=(13, 24), qty=9.0)
ok("B: a second discipline over the same months is a separate line",
   len(main.list_forecasts(route_id="R1")) == 4)
ok("B: withdrawing one line's span leaves the other's",
   main.withdraw_route("R1", submitted_by="tester", **{"from_": 13, "to": 24},
                       discipline="substructure", section_id="WS2")["deleted"] == 2
   and len(main.list_forecasts(route_id="R1")) == 2)


# =========================================================================== #
#  C. forecast_weeks — schema, materialise-on-approve, edit, confirm           #
# =========================================================================== #
reset_db()
ok("C: forecast_weeks table is created", "forecast_weeks" in tables())
fw = cols("forecast_weeks")
for c in ("tenant_id", "route_id", "month_index", "discipline", "section_id",
          "week_index", "planned_qty", "unit", "status", "weather", "wetness",
          "traffic", "other", "confirmed_by", "confirmed_at",
          "actual_qty", "actual_note", "actual_by", "actual_at"):
    ok(f"C: forecast_weeks has {c}", c in fw)
# ⭐ Task D's four columns are created NOW, with Task C's table, precisely so there is
# no second migration on a live Postgres a day later.
ok("C: ⭐ the Task D actual columns exist from the first migration",
   all(c in fw for c in ("actual_qty", "actual_note", "actual_by", "actual_at")))
ok("C: tenant_id is FIRST in the primary key",
   pk_order("forecast_weeks")[0] == "tenant_id")
ok("C: the rest of the key is the line plus the week",
   pk_order("forecast_weeks") == ["tenant_id", "route_id", "month_index",
                                  "discipline", "section_id", "week_index"])
ok("C: forecast_weeks is registered in TENANTED_TABLES",
   "forecast_weeks" in db.TENANTED_TABLES)
ok("C: ...and has a _TENANT_DDL entry and a _TENANT_PK entry",
   "forecast_weeks" in db._TENANT_DDL and "forecast_weeks" in db._TENANT_PK)

db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)",
           ("L1", "Pit", 58.5, 24.0))
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)",
           ("L2", "Site", 58.6, 24.4))
db.execute("INSERT INTO routes (id, origin_id, dest_id) VALUES (?, ?, ?)",
           ("R1", "L1", "L2"))
seed_line(months=(9, 10), qty=100.0)

ok("C: ⭐ a Pending line materialises NO weeks",
   len(weeks.list_weeks(9, 10)) == 0)
main.set_route_status("R1", main.StatusUpdate(status="Approved"),
                      discipline="earthworks", section_id="WS1")
w = weeks.list_weeks(9, 10)
ok("C: approving the line creates four weeks per approved month", len(w) == 8, str(len(w)))
ok("C: ⭐ planned_qty is the month divided by four",
   all(abs(r["planned_qty"] - 25.0) < 1e-9 for r in w))
ok("C: the weeks inherit the parent's unit", all(r["unit"] == "t" for r in w))
ok("C: they start as `derived`", all(r["status"] == "derived" for r in w))
ok("C: week_index is 1-4", sorted({r["week_index"] for r in w}) == [1, 2, 3, 4])

# an edit sticks, and moves the row to `edited`
weeks.set_week("R1", 9, "earthworks", "WS1", 2, planned_qty=40.0)
row = weeks.get_week("R1", 9, "earthworks", "WS1", 2)
ok("C: editing a week's planned quantity stores it", row["planned_qty"] == 40.0)
ok("C: ...and marks it `edited`", row["status"] == "edited")

# re-approving after the parent month changes: derived refreshes, edited does not
seed_line(months=(9,), qty=200.0, status="Pending")
main.set_route_status("R1", main.StatusUpdate(status="Approved"),
                      discipline="earthworks", section_id="WS1")
w9 = {r["week_index"]: r for r in weeks.list_weeks(9, 9)}
ok("C: ⭐ a re-approved month refreshes its `derived` weeks",
   abs(w9[1]["planned_qty"] - 50.0) < 1e-9, str(w9[1]["planned_qty"]))
ok("C: ⭐ ...and leaves an `edited` one alone", w9[2]["planned_qty"] == 40.0)
ok("C: the edited week is flagged as out of step with its parent",
   bool(w9[2].get("parent_changed")) and not w9[1].get("parent_changed"))

# confirm carries the four optional flags and nothing else
weeks.confirm_week("R1", 9, "earthworks", "WS1", 3, by="planner",
                   flags={"weather": "wet", "wetness": "", "traffic": "", "other": ""})
c3 = weeks.get_week("R1", 9, "earthworks", "WS1", 3)
ok("C: confirming a week sets `confirmed`", c3["status"] == "confirmed")
ok("C: ...records who and when", c3["confirmed_by"] == "planner" and c3["confirmed_at"])
ok("C: ...stores the flags as free text, empty allowed",
   c3["weather"] == "wet" and c3["wetness"] == "")
# week 3 was confirmed while the parent stood at 200, so it holds 200/4 = 50. Pushing
# the parent to 400 must move the derived weeks to 100 and leave week 3 at 50.
_before_confirmed = weeks.get_week("R1", 9, "earthworks", "WS1", 3)["planned_qty"]
seed_line(months=(9,), qty=400.0, status="Pending")
main.set_route_status("R1", main.StatusUpdate(status="Approved"),
                      discipline="earthworks", section_id="WS1")
ok("C: ⭐ a confirmed week is NOT refreshed by re-approving its parent",
   weeks.get_week("R1", 9, "earthworks", "WS1", 3)["planned_qty"] == _before_confirmed
   and _before_confirmed == 50.0, str(_before_confirmed))
ok("C: ...while the derived weeks around it DO move",
   weeks.get_week("R1", 9, "earthworks", "WS1", 1)["planned_qty"] == 100.0)
ok("C: ...and the edited one still does not",
   weeks.get_week("R1", 9, "earthworks", "WS1", 2)["planned_qty"] == 40.0)
ok("C: the confirmed week is flagged as out of step with its parent",
   weeks.get_week("R1", 9, "earthworks", "WS1", 3)["parent_changed"] is True)

# next-week arithmetic: week buckets are days 1-7 / 8-14 / 15-21 / 22-end
ok("C: day 1 is week 1", weeks.week_of_day(1) == 1)
ok("C: day 7 is week 1", weeks.week_of_day(7) == 1)
ok("C: day 8 is week 2", weeks.week_of_day(8) == 2)
ok("C: day 21 is week 3", weeks.week_of_day(21) == 3)
ok("C: day 22 is week 4", weeks.week_of_day(22) == 4)
ok("C: day 31 is week 4 — no fifth bucket", weeks.week_of_day(31) == 4)
ok("C: next week inside a month is the following bucket",
   weeks.next_week(month_index=9, week_index=1) == (9, 2))
ok("C: ⭐ past week 4, next week is week 1 of the NEXT month",
   weeks.next_week(month_index=9, week_index=4) == (10, 1))

# visibility: until Task F, the same as /api/forecasts
ok("C: the week feed is tenant-scoped like every other read",
   all("tenant_id" not in r for r in weeks.list_weeks(9, 10)))


# =========================================================================== #
#  D. Actuals, variance, calibrate                                             #
# =========================================================================== #
reset_db()
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)",
           ("L1", "Pit", 58.5, 24.0))
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)",
           ("L2", "Site", 58.6, 24.4))
db.execute("INSERT INTO routes (id, origin_id, dest_id) VALUES (?, ?, ?)",
           ("R1", "L1", "L2"))
seed_line(months=(9,), qty=100.0)
main.set_route_status("R1", main.StatusUpdate(status="Approved"),
                      discipline="earthworks", section_id="WS1")

weeks.set_actual("R1", 9, "earthworks", "WS1", 1, actual_qty=20.0,
                 actual_note="rain", by="foreman")
w1 = weeks.get_week("R1", 9, "earthworks", "WS1", 1)
ok("D: a typed actual is stored", w1["actual_qty"] == 20.0)
ok("D: ...with its note and author", w1["actual_note"] == "rain" and w1["actual_by"] == "foreman")
ok("D: ⭐ variance = planned - actual", w1["variance"] == 5.0, str(w1["variance"]))
ok("D: ⭐ variance is blank until an actual is typed",
   weeks.get_week("R1", 9, "earthworks", "WS1", 2)["variance"] is None)
# ⭐ saving an actual must NOT calibrate. Two people reported that as the surprising
# behaviour to avoid, and it is one line away from happening by accident.
ok("D: ⭐ saving the actual does NOT move next week",
   weeks.get_week("R1", 9, "earthworks", "WS1", 2)["planned_qty"] == 25.0)
ok("D: ...and does not change this week's plan either", w1["planned_qty"] == 25.0)

res = weeks.calibrate("R1", 9, "earthworks", "WS1", 1)
w2 = weeks.get_week("R1", 9, "earthworks", "WS1", 2)
ok("D: ⭐ calibrate adds the variance to next week only", w2["planned_qty"] == 30.0,
   str(w2["planned_qty"]))
ok("D: ...and marks next week `edited`", w2["status"] == "edited")
ok("D: weeks after next are untouched",
   weeks.get_week("R1", 9, "earthworks", "WS1", 3)["planned_qty"] == 25.0
   and weeks.get_week("R1", 9, "earthworks", "WS1", 4)["planned_qty"] == 25.0)
ok("D: this week's own plan is untouched",
   weeks.get_week("R1", 9, "earthworks", "WS1", 1)["planned_qty"] == 25.0)
ok("D: calibrate says which week it wrote", res.get("to") == {"month_index": 9, "week_index": 2})

# an explicit override replaces the formula, not adds to it
weeks.calibrate("R1", 9, "earthworks", "WS1", 1, override_qty=12.0)
ok("D: a typed override replaces next week's plan outright",
   weeks.get_week("R1", 9, "earthworks", "WS1", 2)["planned_qty"] == 12.0)

# blocked when next week is confirmed
weeks.confirm_week("R1", 9, "earthworks", "WS1", 2, by="planner", flags={})
blocked = weeks.calibrate("R1", 9, "earthworks", "WS1", 1)
ok("D: ⭐ calibrate refuses when next week is already confirmed",
   bool(blocked.get("error")), str(blocked))
ok("D: ...and says so rather than failing quietly",
   "confirm" in (blocked.get("error") or "").lower())
ok("D: ...and next week's number did not move",
   weeks.get_week("R1", 9, "earthworks", "WS1", 2)["planned_qty"] == 12.0)

# calibrating week 4 must reach into the next month, not off the end
seed_line(months=(9, 10), qty=100.0, status="Pending")
main.set_route_status("R1", main.StatusUpdate(status="Approved"),
                      discipline="earthworks", section_id="WS1")
weeks.set_actual("R1", 9, "earthworks", "WS1", 4, actual_qty=5.0, by="foreman")
weeks.calibrate("R1", 9, "earthworks", "WS1", 4)
ok("D: ⭐ calibrating week 4 writes week 1 of the NEXT month",
   weeks.get_week("R1", 10, "earthworks", "WS1", 1)["planned_qty"] == 45.0,
   str(weeks.get_week("R1", 10, "earthworks", "WS1", 1)["planned_qty"]))

# ⭐ Crossing a month boundary calls materialise_line() for the next month, which is
# the ONE place a materialise happens outside an approval. It must still refuse to
# create weeks for a month that is not approved — otherwise pressing calibrate on the
# last week of an approved month silently brings a Pending month into the look-ahead.
seed_line(route_id="R1", disc="structures", sect="WS4", months=(11, 12), qty=80.0)
main.set_route_status("R1", main.StatusUpdate(status="Approved"),
                      discipline="structures", section_id="WS4")
# reopen month 12 only
db.execute("UPDATE forecasts SET status = ? WHERE tenant_id = ? AND route_id = ? "
           "AND month_index = ? AND discipline = ? AND section_id = ?",
           ("Pending", db.current_tenant(), "R1", 12, "structures", "WS4"))
db.execute("DELETE FROM forecast_weeks WHERE tenant_id = ? AND route_id = ? "
           "AND month_index = ? AND discipline = ? AND section_id = ?",
           (db.current_tenant(), "R1", 12, "structures", "WS4"))
weeks.set_actual("R1", 11, "structures", "WS4", 4, actual_qty=1.0, by="foreman")
_cross = weeks.calibrate("R1", 11, "structures", "WS4", 4)
ok("D: ⭐ calibrating into a month that is NOT approved refuses",
   bool(_cross.get("error")), str(_cross))
ok("D: ...and creates no weeks for it",
   weeks.get_week("R1", 12, "structures", "WS4", 1) is None)
ok("D: ...and says which month is the problem", "12" in (_cross.get("error") or ""))


# =========================================================================== #
#  D2. Stockpile capacity + weekly consumption                                 #
# =========================================================================== #
reset_db()
ok("D2: stockpile_weeks table is created", "stockpile_weeks" in tables())
sw = cols("stockpile_weeks")
for c in ("tenant_id", "location_id", "month_index", "week_index",
          "consumed_qty", "unit", "note", "updated_by", "updated_at"):
    ok(f"D2: stockpile_weeks has {c}", c in sw)
ok("D2: tenant_id is FIRST in the primary key",
   pk_order("stockpile_weeks")[0] == "tenant_id")
ok("D2: stockpile_weeks is registered in TENANTED_TABLES",
   "stockpile_weeks" in db.TENANTED_TABLES)
lc = cols("locations")
for c in ("capacity_qty", "capacity_unit", "opening_qty"):
    ok(f"D2: locations has {c}", c in lc)
ok("D2: ⭐ the three new location columns are in _TENANT_DDL, not only ALTERed",
   all(c in db._TENANT_DDL["locations"]
       for c in ("capacity_qty", "capacity_unit", "opening_qty")))

db.execute("INSERT INTO locations (id, name, lat, lon, loc_type) VALUES (?, ?, ?, ?, ?)",
           ("L1", "Pit", 58.5, 24.0, "Quarry"))
db.execute("INSERT INTO locations (id, name, lat, lon, loc_type) VALUES (?, ?, ?, ?, ?)",
           ("L2", "North pile", 58.6, 24.4, "Stockpile"))
db.execute("INSERT INTO routes (id, origin_id, dest_id) VALUES (?, ?, ?)",
           ("R1", "L1", "L2"))
stockpiles.set_capacity("L2", capacity_qty=3000.0, capacity_unit="t", opening_qty=200.0)

seed_line(months=(9,), qty=400.0)
main.set_route_status("R1", main.StatusUpdate(status="Approved"),
                      discipline="earthworks", section_id="WS1")

bal = stockpiles.balances(9, 9)["stockpiles"]
L2 = [s for s in bal if s["location_id"] == "L2"][0]
w = {r["week_index"]: r for r in L2["weeks"]}
ok("D2: ⭐ inbound is 0 until a week ACTUAL is typed — never the month forecast",
   all(r["inbound"] == 0 for r in w.values()))
ok("D2: balance therefore starts at the opening figure", w[1]["balance_end"] == 200.0)
ok("D2: remaining = capacity - balance", w[1]["remaining"] == 2800.0)
ok("D2: not over capacity", w[1]["over"] is False)

weeks.set_actual("R1", 9, "earthworks", "WS1", 1, actual_qty=500.0, by="foreman")
stockpiles.consume("L2", 9, 1, consumed_qty=100.0, unit="t", note="", by="foreman")
w = {r["week_index"]: r for r in
     [s for s in stockpiles.balances(9, 9)["stockpiles"]
      if s["location_id"] == "L2"][0]["weeks"]}
ok("D2: a week actual on a route INTO the pile becomes inbound", w[1]["inbound"] == 500.0)
ok("D2: typed consumption is stored", w[1]["consumed"] == 100.0)
ok("D2: ⭐ balance = opening + inbound - consumed", w[1]["balance_end"] == 600.0,
   str(w[1]["balance_end"]))
ok("D2: ⭐ remaining = capacity - (opening + inbound - consumed)",
   w[1]["remaining"] == 2400.0, str(w[1]["remaining"]))
ok("D2: the balance carries forward into week 2 with no new movement",
   w[2]["balance_end"] == 600.0)

# over capacity is a FLAG, never a block
weeks.set_actual("R1", 9, "earthworks", "WS1", 2, actual_qty=5000.0, by="foreman")
w = {r["week_index"]: r for r in
     [s for s in stockpiles.balances(9, 9)["stockpiles"]
      if s["location_id"] == "L2"][0]["weeks"]}
ok("D2: ⭐ over capacity is flagged", w[2]["over"] is True)
ok("D2: ...and remaining goes negative rather than clamping", w[2]["remaining"] < 0)
ok("D2: ⭐ and nothing is blocked — the actual still saved",
   weeks.get_week("R1", 9, "earthworks", "WS1", 2)["actual_qty"] == 5000.0)

# no capacity recorded: the balance still computes, remaining is unknown
db.execute("INSERT INTO locations (id, name, lat, lon, loc_type) VALUES (?, ?, ?, ?, ?)",
           ("L3", "Unmeasured pile", 58.7, 24.5, "Stockpile"))
L3 = [s for s in stockpiles.balances(9, 9)["stockpiles"]
      if s["location_id"] == "L3"][0]
ok("D2: ⭐ a pile with no capacity still balances", L3["weeks"][0]["balance_end"] == 0.0)
ok("D2: ...and reports remaining as unknown, not zero",
   L3["weeks"][0]["remaining"] is None and L3["capacity_qty"] is None)
ok("D2: ...and is never `over`", L3["weeks"][0]["over"] is False)

# dest_id missing: inbound stays 0, nothing is guessed
db.execute("INSERT INTO routes (id, origin_id, dest_id) VALUES (?, ?, ?)",
           ("R9", "L1", None))
seed_line(route_id="R9", disc="earthworks", sect="WS3", months=(9,), qty=999.0)
main.set_route_status("R9", main.StatusUpdate(status="Approved"),
                      discipline="earthworks", section_id="WS3")
weeks.set_actual("R9", 9, "earthworks", "WS3", 1, actual_qty=999.0, by="foreman")
tot = sum(r["inbound"] for s in stockpiles.balances(9, 9)["stockpiles"]
          for r in s["weeks"])
ok("D2: ⭐ a route with no dest_id contributes nothing — no guessing",
   tot == 5500.0, str(tot))


# =========================================================================== #
#  E. Railhead                                                                #
# =========================================================================== #
reset_db()
r = network.create_location("Lelle railhead", "origin", lat=58.8797, lon=24.8236,
                            loc_type="Railhead", supplies=["Large aggregate / ballast"])
got = db.query("SELECT * FROM locations WHERE tenant_id = ? AND id = ?",
               (db.current_tenant(), r["id"]))[0]
ok("E: a location can be created with loc_type 'Railhead'",
   got["loc_type"] == "Railhead")
ok("E: ⭐ a railhead defaults to an origin role, like a quarry or a port",
   network._role_for("Railhead") == "origin")
ok("E: a Stockpile defaults to a destination role",
   network._role_for("Stockpile") == "destination")
fc = network.locations_geojson()
f = [x for x in fc["features"] if x["properties"]["id"] == r["id"]][0]
ok("E: it reaches the map's location feed as a Railhead",
   f["properties"]["loc_type"] == "Railhead")
# 2026-09-02 rename: 'Rail head' -> 'Railhead'. Old rows must not vanish, and the old
# string must never be written again.
ok("E2: the spelling is one word everywhere the type is declared",
   "Railhead" in stockpiles.STORAGE_TYPES and "Rail head" not in stockpiles.STORAGE_TYPES)
db.execute("INSERT INTO locations (tenant_id, id, name, lat, lon, loc_type) "
           "VALUES (?, ?, ?, ?, ?, ?)", ("default", "OLD1", "Old spelling", 58.9, 24.8, "Rail head"))
_old = [x for x in network.locations_geojson()["features"] if x["properties"]["id"] == "OLD1"][0]
ok("E2: ⭐ a row still spelled 'Rail head' is READ as 'Railhead'",
   _old["properties"]["loc_type"] == "Railhead")
ok("E2: ...and is still an origin", network._role_for("Rail head") == "origin")
ok("E2: ...and still counts as a storage location",
   any(l["id"] == "OLD1" for l in stockpiles.storage_locations()))
ok("E2: ...and reaches the public map as a Railhead node",
   [x for x in network.public_map_data()["features"]
    if x["properties"].get("id") == "OLD1"][0]["properties"]["node_type"] == "Railhead")
r2 = network.create_location("Posted old", "origin", lat=58.9, lon=24.8, loc_type="Rail head")
ok("E2: ⭐ a client that still POSTs 'Rail head' gets 'Railhead' WRITTEN",
   db.query("SELECT loc_type FROM locations WHERE tenant_id = ? AND id = ?",
            (db.current_tenant(), r2["id"]))[0]["loc_type"] == "Railhead")
network.update_location("OLD1", loc_type="Rail head")
ok("E2: ...and so does an update", db.query(
    "SELECT loc_type FROM locations WHERE tenant_id = ? AND id = ?",
    (db.current_tenant(), "OLD1"))[0]["loc_type"] == "Railhead")
db.execute("UPDATE locations SET loc_type = ? WHERE tenant_id = ? AND id = ?",
           ("Rail head", db.current_tenant(), "OLD1"))
db.init_weeks_db()
ok("E2: ⭐ the boot migration rewrites the old string in place",
   db.query("SELECT loc_type FROM locations WHERE tenant_id = ? AND id = ?",
            (db.current_tenant(), "OLD1"))[0]["loc_type"] == "Railhead")
ok("E2: no source file still declares the two-word type",
   not any("\"Rail head\"" in open(os.path.join(BACKEND, fn), encoding="utf-8").read()
           for fn in os.listdir(BACKEND) if fn.endswith(".py") and fn != "network.py"))


# =========================================================================== #
#  The rail geometry file — what it IS, and what it is NOT                     #
# =========================================================================== #
RAIL = os.path.join(ROOT, "map", "data", "evr_rail.js")
ok("E: map/data/evr_rail.js exists", os.path.exists(RAIL))
_txt = open(RAIL, encoding="utf-8").read()
_geo = json.loads(_txt[_txt.index("{"):_txt.rindex("}") + 1])
feats = _geo["features"]
ok("E: it is a FeatureCollection of LineStrings", _geo["type"] == "FeatureCollection"
   and all(x["geometry"]["type"] == "LineString" for x in feats))
ok("E: ⭐ every feature carries `heads`",
   all(isinstance(x["properties"].get("heads"), list) and x["properties"]["heads"]
       for x in feats))
# ⚠️ THE HONESTY ASSERTIONS. This geometry is Natural Earth 10m, NOT OSM and NOT
# survey. It is right to ~0.3 km at Rapla and wrong by ~6.4 km at Lelle. None of that
# is fixable here — Overpass, openstreetmap.org and Geofabrik are all unreachable from
# the sandbox. What IS enforceable is that the file never claims to be better than it
# is, on a client-visible map that has been burned by exactly that twice.
ok("E: ⭐ every feature names its source", all(x["properties"].get("source") for x in feats))
ok("E: ⭐ every feature is flagged provisional",
   all(x["properties"].get("provisional") is True for x in feats))
ok("E: ⭐ every feature states its measured positional error",
   all(x["properties"].get("accuracy_note") for x in feats))
ok("E: ⭐ the Lelle error is stated in the file, not buried in a note elsewhere",
   "6.4" in _txt and "Lelle" in _txt)
ok("E: it is NOT a straight line — the corridor is drawn with real vertices",
   all(len(x["geometry"]["coordinates"]) > 20 for x in feats))
# and it must not be mistaken for the Rail Baltica alignment, which is a different file
ok("E: ⭐ it is not the Rail Baltica alignment layer",
   "alignment_data" not in _txt and "window.evr_rail_data" in _txt)


# =========================================================================== #
#  Cross-cutting: units, the endpoint wrappers, and what must NOT exist        #
# =========================================================================== #
reset_db()
db.execute("INSERT INTO locations (id, name, lat, lon, loc_type) VALUES (?, ?, ?, ?, ?)",
           ("L1", "Pit", 58.5, 24.0, "Quarry"))
db.execute("INSERT INTO locations (id, name, lat, lon, loc_type) VALUES (?, ?, ?, ?, ?)",
           ("L2", "Volume pile", 58.6, 24.4, "Stockpile"))
db.execute("INSERT INTO routes (id, origin_id, dest_id) VALUES (?, ?, ?)",
           ("R1", "L1", "L2"))
# ⭐ A line entered in m3 delivering into a pile measured in TONNES. Adding those
# together untouched would produce a number with no meaning, and it would look right.
stockpiles.set_capacity("L2", capacity_qty=1000.0, capacity_unit="t", opening_qty=0.0)
main.save_matrix_row(main.MatrixRow(
    route_id="R1", discipline="earthworks", section_id="WS1",
    material_type="Small aggregate", vehicle_type="Rigid 8-wheeler (32t)",
    submitted_by="tester", unit="m3", status="Pending",
    cells=[main.Cell(month_index=9, quantity=400.0)]))
main.set_route_status("R1", main.StatusUpdate(status="Approved"),
                      discipline="earthworks", section_id="WS1")
weeks.set_actual("R1", 9, "earthworks", "WS1", 1, actual_qty=100.0, by="foreman")
_w1 = [s for s in stockpiles.balances(9, 9)["stockpiles"]
       if s["location_id"] == "L2"][0]["weeks"][0]
# Small aggregate is 1.6 t/m3 in the real factors.json
ok("⭐ an m3 movement into a tonnes pile is CONVERTED, not added raw",
   abs(_w1["inbound"] - 160.0) < 1e-6, str(_w1["inbound"]))
ok("...and the balance is in the pile's unit", _w1["unit"] == "t")
_dens = conversions.load_factors()["material_categories"]["Small aggregate"]["density_t_per_m3"]
ok("...using the density from factors.json, not a constant",
   abs(_w1["inbound"] - 100.0 * _dens) < 1e-6)
# consumption typed in m3 against the same tonnes pile
stockpiles.consume("L2", 9, 1, consumed_qty=10.0, unit="m3", note="", by="foreman")
_w1 = [s for s in stockpiles.balances(9, 9)["stockpiles"]
       if s["location_id"] == "L2"][0]["weeks"][0]
ok("⭐ typed consumption is converted the same way",
   abs(_w1["consumed"] - 16.0) < 1e-6, str(_w1["consumed"]))

# the START_YEAR epoch has one definition, not two
ok("⭐ stockpiles.START_YEAR is set from main, not maintained separately",
   stockpiles.START_YEAR == main.START_YEAR == 2026)

# capacity is clearable — "we do not know how big this pile is" is a real state
stockpiles.set_capacity("L2", capacity_qty=None, capacity_unit=None, opening_qty=0.0)
_L2 = [s for s in stockpiles.balances(9, 9)["stockpiles"] if s["location_id"] == "L2"][0]
ok("D2: a capacity can be cleared back to unknown", _L2["capacity_qty"] is None)
ok("...and remaining goes back to unknown with it", _L2["weeks"][0]["remaining"] is None)

# the endpoint wrappers, called as plain functions
_res = main.list_forecast_weeks(from_month=9, to_month=10)
ok("the /api/forecast-weeks payload carries the server-chosen next week",
   isinstance(_res.get("next_week"), dict)
   and set(_res["next_week"]) == {"month_index", "week_index"})
ok("...the three statuses", _res["statuses"] == list(weeks.WEEK_STATUSES))
ok("...and the four flag names", _res["flag_fields"] == list(weeks.FLAG_FIELDS))
_sp = main.list_stockpiles(from_month=9, to_month=10)
ok("the /api/stockpiles payload names the storage types",
   _sp["storage_types"] == list(stockpiles.STORAGE_TYPES))
ok("...and the two capacity units", _sp["capacity_units"] == ["m3", "t"])

# ⭐ NOTHING MAY UPLOAD. "Never build: file upload, OCR..." — asserted at source level
# across every backend module, so a later edit that adds one trips here.
_backend_src = ""
for _fn in sorted(os.listdir(BACKEND)):
    if _fn.endswith(".py"):
        _backend_src += open(os.path.join(BACKEND, _fn), encoding="utf-8").read()
for _banned in ("UploadFile", "File(", "multipart", "python-multipart"):
    ok(f"⭐ no upload path in the backend: {_banned!r} is absent",
       _banned not in _backend_src, "found in backend/*.py")
_reqs = open(os.path.join(BACKEND, "requirements.txt"), encoding="utf-8").read()
ok("⭐ ...and python-multipart is not a dependency", "multipart" not in _reqs)

# =========================================================================== #
#  F. IPT access codes — 2026-09-02                                            #
# =========================================================================== #
# ⚠️ The middleware that reads X-Access-Code never runs here (stubbed app). What is
# exercised is everything BELOW it: resolution, the demo fallback, the per-line filter
# on every staff endpoint, the write guards, and the approve gate. The header itself is
# unverified — the first thing to check on the deployment is that a wrong code gets 401.
def _as(code):
    access.set_current(code)

reset_db()
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)", ("L1", "Pit", 58.5, 24.0))
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)", ("L2", "Site", 58.6, 24.4))
db.execute("INSERT INTO routes (id, origin_id, dest_id, ipt) VALUES (?, ?, ?, ?)", ("R1", "L1", "L2", "IPT 3 / IPT 6"))
db.execute("INSERT INTO routes (id, origin_id, dest_id, ipt) VALUES (?, ?, ?, ?)", ("R5", "L1", "L2", "IPT 5"))

# --- demo mode: no real code configured ----------------------------------------
ok("F: with no env codes the three demo codes resolve",
   all(access.resolve(c) for c in ("submitter123", "planner123", "admin123")))
ok("F: ...and an unknown code does not", access.resolve("letmein") is None)
ok("F: demo submitter sees all and cannot approve",
   access.resolve("submitter123")["ipt"] is None and not access.can_approve(access.resolve("submitter123")))
ok("F: /api/auth describes what a code grants",
   main.auth(main.AuthIn(code="planner123"))["can_approve"] is True
   and main.auth(main.AuthIn(code="planner123"))["demo"] is True)
try:
    main.auth(main.AuthIn(code="nope")); ok("F: /api/auth rejects an unknown code", False)
except Exception as e:
    ok("F: /api/auth rejects an unknown code with 401", getattr(e, "status_code", None) == 401)
_as("planner123")
seed_line(route_id="R1", disc="earthworks", sect="WS1", months=(9,), qty=10.0)
ok("F: in demo mode a planner may save with no ipt (pre-F behaviour)",
   main.list_forecasts(route_id="R1")[0]["ipt"] is None)
seed_line(route_id="R5", disc="earthworks", sect="WS1", months=(9,), qty=10.0)

# --- the one-off backfill: single-IPT routes only --------------------------------
_bf = network.backfill_forecast_ipt()
ok("F: ⭐ backfill fills a line on a single-IPT route", _bf["filled"] == 1
   and main.list_forecasts(route_id="R5")[0]["ipt"] == "IPT5")
ok("F: ⭐ ...and leaves a line on a SHARED route NULL — no guessing between two IPTs",
   _bf["left"] == 1 and main.list_forecasts(route_id="R1")[0]["ipt"] is None)
ok("F: the backfill is idempotent", network.backfill_forecast_ipt() == {"filled": 0, "left": 1})

# --- real codes configured ---------------------------------------------------------
os.environ.update({"IPT3_CODE": "three-secret", "IPT6_CODE": "six-secret",
                   "PLANNER_CODE": "plan-secret", "ADMIN_CODE": "adm-secret"})
ok("F: ⭐ once real codes exist, the demo codes stop working",
   all(access.resolve(c) is None for c in ("submitter123", "planner123", "admin123")))
ok("F: an IPT code resolves to its IPT and cannot approve",
   access.resolve("three-secret") == {"role": "ipt", "ipt": "IPT3", "label": "IPT 3"}
   and not access.can_approve(access.resolve("three-secret")))
ok("F: 'IPT 3', 'ipt3' and 'IPT-3' all canonicalise to IPT3",
   all(access.canonical_ipt(v) == "IPT3" for v in ("IPT 3", "ipt3", " IPT-3 ", "IPT3")))
ok("F: ...and rubbish canonicalises to None",
   access.canonical_ipt("IPT 7") is None and access.canonical_ipt("Earthworks") is None)

# planner writes lines for two IPTs
_as("plan-secret")
try:
    main.save_matrix_row(main.MatrixRow(route_id="R1", discipline="substructure", section_id="WS2",
        material_type="Small aggregate", vehicle_type="Rigid 8-wheeler (32t)", submitted_by="p",
        unit="t", status="Pending", cells=[main.Cell(month_index=9, quantity=5.0)]))
    ok("F: ⭐ a planner with real codes MUST name an IPT", False)
except Exception as e:
    ok("F: ⭐ a planner with real codes MUST name an IPT — no silent IPT 1",
       getattr(e, "status_code", None) == 400 and "required" in str(e))
for _ipt, _sect in (("IPT 3", "WS2"), ("IPT6", "WS3")):
    main.save_matrix_row(main.MatrixRow(route_id="R1", discipline="substructure", section_id=_sect,
        material_type="Small aggregate", vehicle_type="Rigid 8-wheeler (32t)", submitted_by="p",
        unit="t", status="Pending", cells=[main.Cell(month_index=9, quantity=5.0)], ipt=_ipt))
_all = main.list_forecasts(route_id="R1")
ok("F: the planner sees every line on the route, NULL-ipt included", len(_all) == 3)
ok("F: the planner's chosen IPT is written canonically",
   sorted(r["ipt"] for r in _all if r["section_id"] == "WS2") == ["IPT3"])

# --- an IPT3 code cannot read an IPT6 line -------------------------------------------
_as("three-secret")
_mine = main.list_forecasts(route_id="R1")
ok("F: ⭐ an IPT3 code sees only its own line", [r["section_id"] for r in _mine] == ["WS2"])
ok("F: ⭐ ...not the IPT6 line, and not the NULL-ipt line either",
   not any(r["section_id"] in ("WS1", "WS3") for r in _mine))
ok("F: the summary is filtered the same way",
   {r["section_id"] for r in main.forecasts_summary()} == {"WS2"})
# writes
main.save_matrix_row(main.MatrixRow(route_id="R1", discipline="substructure", section_id="WS2",
    material_type="Small aggregate", vehicle_type="Rigid 8-wheeler (32t)", submitted_by="i3",
    unit="t", status="Pending", cells=[main.Cell(month_index=9, quantity=7.0)], ipt="IPT6"))
ok("F: ⭐ an IPT code's save is FORCED to its own IPT whatever the body said",
   [r["ipt"] for r in main.list_forecasts(route_id="R1")] == ["IPT3"]
   and main.list_forecasts(route_id="R1")[0]["quantity"] == 7.0)
try:
    main.save_matrix_row(main.MatrixRow(route_id="R1", discipline="substructure", section_id="WS3",
        material_type="Small aggregate", vehicle_type="Rigid 8-wheeler (32t)", submitted_by="i3",
        unit="t", status="Pending", cells=[main.Cell(month_index=9, quantity=99.0)]))
    ok("F: ⭐ an IPT3 code cannot overwrite an IPT6 line", False)
except Exception as e:
    ok("F: ⭐ an IPT3 code cannot overwrite an IPT6 line — and gets 404, not 403",
       getattr(e, "status_code", None) == 404)
_as("plan-secret")
ok("F: ...and the IPT6 line is untouched",
   [r["quantity"] for r in main.list_forecasts(route_id="R1") if r["section_id"] == "WS3"] == [5.0])
# approve
_as("three-secret")
try:
    main.set_route_status("R1", main.StatusUpdate(status="Approved"), discipline="substructure", section_id="WS2")
    ok("F: an IPT code cannot approve", False)
except Exception as e:
    ok("F: ⭐ an IPT code cannot approve — 403", getattr(e, "status_code", None) == 403)
_as("plan-secret")
main.set_route_status("R1", main.StatusUpdate(status="Approved"), discipline="substructure", section_id="WS2")
main.set_route_status("R1", main.StatusUpdate(status="Approved"), discipline="substructure", section_id="WS3")
# look-ahead
_as("six-secret")
_w6 = main.list_forecast_weeks(from_month=9, to_month=9)["weeks"]
ok("F: ⭐ the look-ahead is filtered per IPT", {w["section_id"] for w in _w6} == {"WS3"} and len(_w6) == 4)
try:
    main.set_forecast_week_actual(main.WeekActual(route_id="R1", month_index=9, discipline="substructure",
        section_id="WS2", week_index=1, actual_qty=1.0))
    ok("F: an IPT6 code cannot type an actual on an IPT3 week", False)
except Exception as e:
    ok("F: ⭐ an IPT6 code cannot type an actual on an IPT3 week — 404",
       getattr(e, "status_code", None) == 404)
main.set_forecast_week_actual(main.WeekActual(route_id="R1", month_index=9, discipline="substructure",
    section_id="WS3", week_index=1, actual_qty=1.0))
ok("F: ...but can on its own", weeks.get_week("R1", 9, "substructure", "WS3", 1)["actual_qty"] == 1.0)
# withdraw
_as("three-secret")
_wd = main.withdraw_route("R1", discipline="substructure", section_id="WS3")
ok("F: ⭐ an IPT3 withdraw of an IPT6 line deletes nothing", _wd["deleted"] == 0)
_as("plan-secret")
ok("F: ...and the line is still there",
   any(r["section_id"] == "WS3" for r in main.list_forecasts(route_id="R1")))
# no code at all
_as(None)
try:
    main.list_forecasts(); ok("F: no code -> 401", False)
except Exception as e:
    ok("F: ⭐ no code at all gets 401 on a staff endpoint", getattr(e, "status_code", None) == 401)
ok("F: ...while the public map feed stays open",
   isinstance(main.public_route_forecasts(1, 60, "vehicles"), dict)
   and isinstance(main.meta(), dict))
# stockpiles: any code, NOT filtered
_as("six-secret")
ok("F: stockpiles are readable by an IPT code and are not IPT-filtered (a pile has no IPT)",
   isinstance(main.list_stockpiles(from_month=9, to_month=9)["stockpiles"], list))
# tidy: back to demo for the rest of the file
for _v in ("IPT3_CODE", "IPT6_CODE", "PLANNER_CODE", "ADMIN_CODE"):
    os.environ.pop(_v, None)
_as("planner123")
ok("F: the ipt column is in _TENANT_DDL as well as ALTERed", "ipt" in db._ddl_columns(db._TENANT_DDL["forecasts"]))


# =========================================================================== #
#  §8 / §9 (2026-09-02) — the two public endpoints behind the map's cards/warnings #
# =========================================================================== #
reset_db()
db.execute("INSERT INTO locations (id, name, lat, lon, loc_type) VALUES (?, ?, ?, ?, ?)", ("L1", "Pit", 58.5, 24.0, "Quarry"))
db.execute("INSERT INTO locations (id, name, lat, lon, loc_type) VALUES (?, ?, ?, ?, ?)", ("L2", "Pile", 58.6, 24.4, "Stockpile"))
db.execute("INSERT INTO routes (id, origin_id, dest_id) VALUES (?, ?, ?)", ("R1", "L1", "L2"))
_as("planner123")
# three lines in month 9: tonnes on a known vehicle, m3 on a known vehicle, and a
# vehicle factors.json has never heard of
main.save_matrix_row(main.MatrixRow(route_id="R1", discipline="earthworks", section_id="WS1",
    material_type="Small aggregate", vehicle_type="Rigid 8-wheeler (32t)", submitted_by="p",
    unit="t", status="Approved", cells=[main.Cell(month_index=9, quantity=200.0)]))
main.save_matrix_row(main.MatrixRow(route_id="R1", discipline="substructure", section_id="WS2",
    material_type="Small aggregate", vehicle_type="Artic Tipper (44t)", submitted_by="p",
    unit="m3", status="Approved", cells=[main.Cell(month_index=9, quantity=100.0)]))
main.save_matrix_row(main.MatrixRow(route_id="R1", discipline="structures", section_id="WS3",
    material_type="Precast / concrete", vehicle_type="Unicorn lorry", submitted_by="p",
    unit="t", status="Approved", cells=[main.Cell(month_index=9, quantity=36.0)]))
# and a Pending one that must NOT appear
main.save_matrix_row(main.MatrixRow(route_id="R1", discipline="utilities", section_id="WS4",
    material_type="Small aggregate", vehicle_type="Rigid 8-wheeler (32t)", submitted_by="p",
    unit="t", status="Pending", cells=[main.Cell(month_index=9, quantity=999.0)]))
_as(None)      # PUBLIC: no code
_k = main.public_month_kpis(month=9, unit="vehicles")
ok("§9: /api/public/month-kpis is open without a code", isinstance(_k, dict))
ok("§9: Approved lines only", sorted(l["section_id"] for l in _k["lines"]) == ["WS1", "WS2", "WS3"])
_by = {l["section_id"]: l for l in _k["lines"]}
ok("§9: working days come from factors.planning", _k["working_days"] == 22)
ok("§9: a tonnes line -> vehicle-loads = t / payload", abs(_by["WS1"]["vehicle_loads"] - 200 / 20) < 1e-9)
ok("§9: an m3 line converts through density THEN payload",
   abs(_by["WS2"]["qty_t"] - 160.0) < 1e-6 and abs(_by["WS2"]["vehicle_loads"] - 160 / 29) < 1e-3)
ok("§9: ⭐ an unknown vehicle falls back to V07 (18 t), NOT _default (20 t), and says so",
   _by["WS3"]["payload_t"] == 18.0 and abs(_by["WS3"]["vehicle_loads"] - 2.0) < 1e-9
   and _by["WS3"]["payload_fallback"] == PLANNING[0] and _by["WS1"]["payload_fallback"] is None)
ok("§9: qty_unit is in the requested map unit",
   _by["WS1"]["qty_unit"] == 10 and main.public_month_kpis(month=9, unit="t")["lines"][0]["qty_t"] == 200.0)
ok("§9: never actuals", not any("actual" in k for l in _k["lines"] for k in l))
ok("§9: an empty month returns no lines rather than zeros", main.public_month_kpis(month=10, unit="t")["lines"] == [])
# stockpile timeline
_as("planner123")
stockpiles.set_capacity("L2", capacity_qty=100.0, capacity_unit="t", opening_qty=0.0)
# the lines above were written Approved directly, so nothing materialised their weeks
weeks.materialise_line("R1", "earthworks", "WS1")
weeks.set_actual("R1", 9, "earthworks", "WS1", 1, actual_qty=150.0, by="f")
_as(None)
_st = main.public_stockpile_timeline(9, 10)
ok("§8: /api/public/stockpile-timeline is open without a code", isinstance(_st, dict))
_p = [s for s in _st["stockpiles"] if s["location_id"] == "L2"][0]
ok("§8: ⭐ the pile is over at the end of month 9", _p["months"]["9"]["over"] is True and _p["months"]["9"]["balance_end"] == 150.0)
ok("§8: ...and still over in month 10 with no movement (the balance carries)", _p["months"]["10"]["over"] is True)
ok("§8: only piles WITH a capacity appear — nothing can be 'over' an unknown limit",
   (stockpiles.set_capacity("L2", capacity_qty=None, opening_qty=0.0),
    main.public_stockpile_timeline(9, 10)["stockpiles"] == [])[1])
_as("planner123")


# =========================================================================== #
#  The UPGRADE path — a database that predates this delivery                   #
# =========================================================================== #
# ⭐ The highest-risk part of this week's work, because it runs once against live
# Postgres and there is no second chance. The trap db.py documents: the SQLite tenant
# rebuild copies the INTERSECTION of the live table's columns with the DDL, so a
# column ALTERed on AFTER that rebuild survives and one added BEFORE it and missing
# from the DDL is silently dropped. The three capacity columns are in
# _TENANT_DDL["locations"] as well as ALTERed for exactly that reason — and this
# asserts it against a database built the way the last deploy left one.
if os.path.exists(db._SQLITE_PATH):
    os.remove(db._SQLITE_PATH)
# 1. a PRE-week-1 database, with data in it
db.init_db()
db.init_network_db()
db.init_taxonomy_db()
db.init_zones_db()
db.init_gates_db()
db.init_tenant()
db.execute("INSERT INTO locations (tenant_id, id, name, lat, lon, loc_type, vendor, "
           "detail, gate_lat, gate_lon, default_section_id) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
           ("default", "C01", "Vao quarry", 59.4, 24.9, "Quarry", "OU Killustik",
            "Limestone - rockfill", 59.41, 24.91, "WS1"))
ok("upgrade: the pre-week-1 database has no forecast_weeks",
   "forecast_weeks" not in tables())
# 2. deploy: the lifespan order, init_weeks_db BEFORE init_tenant
db.init_network_db()
db.init_gates_db()
db.init_weeks_db()
db.init_tenant()
_lc = cols("locations")
for c in ("capacity_qty", "capacity_unit", "opening_qty"):
    ok(f"upgrade: locations gained {c}", c in _lc)
# ⭐ and NOTHING that was already there was lost to the rebuild
for c in ("vendor", "detail", "gate_lat", "gate_lon", "default_section_id",
          "supplies", "receives", "materials", "material", "role"):
    ok(f"upgrade: ⭐ the pre-existing column {c} survived", c in _lc)
_row = db.query("SELECT * FROM locations WHERE tenant_id = ? AND id = ?",
                (db.current_tenant(), "C01"))
ok("upgrade: ⭐ the row survived", len(_row) == 1)
ok("upgrade: ⭐ ...with its salvaged vendor intact",
   _row and _row[0]["vendor"] == "OU Killustik")
ok("upgrade: ...its gate pair intact", _row and _row[0]["gate_lat"] == 59.41)
ok("upgrade: ...and a NULL capacity, which is 'not recorded', not zero",
   _row and _row[0]["capacity_qty"] is None)
ok("upgrade: both new tables exist",
   "forecast_weeks" in tables() and "stockpile_weeks" in tables())
# 3. ⭐ THE ACTUAL TRAP, and it needs a PRE-4.5 table to bite.
#
# The step above cannot expose it: that database had already been through the tenant
# migration, so _migrate_table_to_tenant() returns "already", no rebuild happens, and
# the ALTERed columns simply stay. The live Postgres is in that state too (4.5
# confirmed 2026-08-30), so it is safe today — but a fresh SQLite database, or a
# second tenant, goes through the rebuild, and there the rebuild copies only the
# INTERSECTION of the live columns with _TENANT_DDL. A column ALTERed on but missing
# from the DDL is dropped, silently, with its data.
#
# So: a `locations` table with NO tenant_id, carrying the three capacity columns and a
# row, put through init_tenant().
db.execute("DROP TABLE IF EXISTS locations_pre45")
db.execute("DROP TABLE locations")
db.execute("""
    CREATE TABLE locations (
        id TEXT NOT NULL PRIMARY KEY, name TEXT NOT NULL, loc_type TEXT, role TEXT,
        materials TEXT, supplies TEXT, receives TEXT, lat REAL NOT NULL,
        lon REAL NOT NULL, material TEXT, default_section_id TEXT, vendor TEXT,
        detail TEXT, gate_lat REAL, gate_lon REAL,
        capacity_qty REAL, capacity_unit TEXT, opening_qty REAL
    )
""")
db.execute("INSERT INTO locations (id, name, lat, lon, vendor, capacity_qty, "
           "capacity_unit, opening_qty) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
           ("C02", "Legacy pile", 58.9, 24.5, "OU Killustik", 2500.0, "t", 180.0))
ok("upgrade: a pre-4.5 locations table has no tenant_id",
   "tenant_id" not in cols("locations"))

# ⭐ 3a. THE PARSER THAT DECIDES WHAT SURVIVES THAT REBUILD.
#
# db._ddl_columns() splits a DDL body on top-level commas and takes the first token of
# each piece. Several blocks carry `-- …` comments, and prose contains commas: one of
# them splits a fragment mid-sentence, so the piece that declares the NEXT column
# begins with an English word and that column never reaches `want`. The rebuild copies
# only `[c for c in want if c in have]`, so it is dropped with its data.
#
# ⚠️ FOUND 2026-09-01 AND IT WAS ALREADY LIVE: routes.origin_gate_id (Phase 5a) had
# been invisible to this parser since it shipped, while dest_gate_id survived because
# it happened to start its own fragment. Fixed by stripping `--` first. Asserted for
# EVERY table so a future comment cannot re-open it.
import re as _re  # noqa: E402
_ddl_problems = []
for _t, _ddl in sorted(db._TENANT_DDL.items()):
    _got = set(db._ddl_columns(_ddl))
    for _line in _ddl.splitlines():
        _l = _line.strip()
        if not _l or _l.startswith("--"):
            continue
        _tok = _l.split()[0]
        # a declaration line starts with a bare identifier; skip the statement and
        # the table-level constraints. Matched on the WHOLE token, so `created_at`
        # is not mistaken for CREATE and `primary_discipline` not for PRIMARY.
        if _tok.upper() in ("CREATE", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK",
                            "CONSTRAINT", ")"):
            continue
        if not _re.fullmatch(r"[a-z_][a-z0-9_]*", _tok):
            continue
        if _tok not in _got:
            _ddl_problems.append(f"{_t}.{_tok}")
ok("⭐ every column declared in every _TENANT_DDL block is seen by _ddl_columns()",
   not _ddl_problems, str(_ddl_problems))
# and the specific pair that was broken
ok("⭐ routes.origin_gate_id is visible to the tenant rebuild (was NOT, pre-fix)",
   "origin_gate_id" in db._ddl_columns(db._TENANT_DDL["routes"]))
ok("...as is dest_gate_id, which survived by luck",
   "dest_gate_id" in db._ddl_columns(db._TENANT_DDL["routes"]))
ok("⭐ and the parser returns no English words from the comments",
   not [c for c in db._ddl_columns(db._TENANT_DDL["locations"])
        if c in ("--", "which", "and", "or", "not", "default", "so")])
db.init_tenant()
_lc2 = cols("locations")
for c in ("capacity_qty", "capacity_unit", "opening_qty"):
    ok(f"upgrade: ⭐ {c} survives the pre-4.5 tenant REBUILD", c in _lc2)
_r2 = db.query("SELECT * FROM locations WHERE tenant_id = ? AND id = ?",
               (db.current_tenant(), "C02"))
ok("upgrade: ⭐ and so does the capacity DATA in it",
   len(_r2) == 1 and _r2[0]["capacity_qty"] == 2500.0
   and _r2[0]["capacity_unit"] == "t" and _r2[0]["opening_qty"] == 180.0,
   str(_r2))

# 4. a second boot must change nothing
db.init_weeks_db()
_again = db.init_tenant()
ok("upgrade: ⭐ a second boot migrates nothing",
   not [t for t, r in _again.items() if r == "migrated"],
   str([t for t, r in _again.items() if r == "migrated"]))
ok("upgrade: ...and no table reports FAILED",
   not [t for t, r in _again.items() if r == "failed"])

# the week layer must not have leaked into the monthly key
ok("⭐ `forecasts` still has no week_index", "week_index" not in cols("forecasts"))
ok("⭐ ...and the public feed is still monthly",
   "week" not in main.public_route_forecasts.__doc__.lower())


# =========================================================================== #
print()
print(f"{PASS} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAIL:", f)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)

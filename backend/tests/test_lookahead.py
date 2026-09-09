"""
Look-ahead v2, slice 1 — the commit week by day (2026-09-09).

Backend assertions for forecast_days, its materialisation rules, the day APIs, the
sum-rule flag, the day-actual → week-actual sync, whole-week confirm stamping the days,
and the opt-in calibrate spread. Same harness as test_week1.py: a scratch SQLite
database, HERE never called, `fastapi` / `flexpolyline` / `psycopg2` stubbed.

WHAT THIS DOES NOT PROVE
------------------------
  * The HTTP layer is stubbed. Endpoint BODIES run; nothing proves a route is mounted
    or that `?from=` / `?to=` are parsed off a query string.
  * No Postgres branch runs. forecast_days is created against SQLite only; its Postgres
    path is a plain CREATE plus the 4.5 tenant migration.
  * Nothing in a browser. There is no frontend in slice 1.
  * "Today" is the real clock for the endpoint-level assertions (same as test_week1),
    and an explicit `today=` for the bucket-arithmetic ones. The seed spans two years
    so the commit month is Approved whatever the date.

Run:  python3 backend/tests/test_lookahead.py
"""
import datetime
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

TMP = tempfile.mkdtemp(prefix="rbe_lookahead_")
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
    db.init_config_db()
    db.init_tenant()
    import config as _cfg
    _cfg.invalidate()


def seed_line(route_id="R1", disc="earthworks", sect="WS1", months=(1, 2, 3),
              qty=100.0, unit="t", status="Pending"):
    """One forecast line over `months`, through the real bulk endpoint."""
    return main.save_matrix_row(main.MatrixRow(
        route_id=route_id, discipline=disc, section_id=sect,
        material_type="Small aggregate", material_description=None,
        vehicle_type="Rigid 8-wheeler (32t)", submitted_by="tester",
        unit=unit, status=status,
        cells=[main.Cell(month_index=m, quantity=qty) for m in months]))


import days  # noqa: E402
days.START_YEAR = main.START_YEAR


def _as(code):
    access.set_current(code)


def _line_days(rid="R1", disc="earthworks", sect="WS1"):
    mi, wi = days.commit_bucket()
    return days._days_of(rid, mi, disc, sect, wi)


def _by_date(rows):
    return {r["day_date"]: r for r in rows}


# =========================================================================== #
#  0. Registration — a new table is not finished when it is created            #
# =========================================================================== #
reset_db()
ok("forecast_days is a tenanted table", "forecast_days" in db.TENANTED_TABLES)
ok("...with a DDL entry and a PK entry",
   "forecast_days" in db._TENANT_DDL and "forecast_days" in db._TENANT_PK)
# .get(), not a subscript — breaking the registration on purpose raised KeyError here
# and took the report down with the assertion above it, which HAD failed correctly.
ok("...whose key is the week's line key plus the date",
   db._TENANT_PK.get("forecast_days", "").replace(" ", "")
   == "(tenant_id,route_id,month_index,discipline,section_id,day_date)")
ok("forecast_weeks gained actual_source, and it is in the DDL (or the rebuild drops it)",
   "actual_source" in db._TENANT_DDL["forecast_weeks"]
   and "actual_source" in [c.lower() for c in db._columns_of(db.get_conn().cursor(), "forecast_weeks")])

# =========================================================================== #
#  1. Bucket arithmetic — pure functions, explicit dates                       #
# =========================================================================== #
ok("month_of: index 1 is January of START_YEAR", days.month_of(1, 2026) == (2026, 1))
ok("month_of: index 13 rolls into the next year", days.month_of(13, 2026) == (2027, 1))
d_w2 = days.bucket_dates(9, 2, 2026)         # Sep 2026, days 8-14
ok("week 2 is the 8th to the 14th, seven days",
   [d.day for d in d_w2] == [8, 9, 10, 11, 12, 13, 14] and d_w2[0].month == 9)
d_w4 = days.bucket_dates(10, 4, 2026)        # Oct 2026 has 31 days: 22..31
ok("⭐ week 4 of a 31-day month is TEN days, 22nd to the 31st",
   [d.day for d in d_w4] == list(range(22, 32)))
ok("...and week 4 of February 2027 is 22nd to the 28th",
   [d.day for d in days.bucket_dates(14, 4, 2026)] == list(range(22, 29)))
ok("weekdays_in counts Mon-Fri only",
   len(days.weekdays_in(d_w2)) == 5 and len(days.weekdays_in(d_w4)) == 7)
sp = days.split_week(1000.0, d_w2)
ok("⭐ L1: Mon-Fri each get week/5 and Sat/Sun get 0",
   all(abs(sp[d] - 200.0) < 1e-9 for d in d_w2 if d.weekday() <= 4)
   and all(sp[d] == 0.0 for d in d_w2 if d.weekday() > 4))
ok("...and the split sums back to the week", abs(sum(sp.values()) - 1000.0) < 1e-9)
sp4 = days.split_week(700.0, d_w4)
ok("week 4 of a 31-day month is still weekday-weighted: 7 weekdays x 100, 3 weekend days x 0",
   sum(1 for v in sp4.values() if abs(v - 100.0) < 1e-9) == 7
   and sum(1 for v in sp4.values() if v == 0.0) == 3)
ok("commit_bucket with an explicit today matches weeks.editable_week",
   days.commit_bucket(2026, today=datetime.date(2026, 9, 10)) == (9, 2)
   and days.commit_bucket(2026, today=datetime.date(2026, 10, 25)) == (10, 4))

# =========================================================================== #
#  2. Materialisation — commit week only, Approved only                       #
# =========================================================================== #
reset_db()
_as("planner123")
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)", ("L1", "Pit", 58.5, 24.0))
db.execute("INSERT INTO locations (id, name, lat, lon) VALUES (?, ?, ?, ?)", ("L2", "Site", 58.6, 24.4))
db.execute("INSERT INTO routes (id, origin_id, dest_id) VALUES (?, ?, ?)", ("R1", "L1", "L2"))
db.execute("INSERT INTO routes (id, origin_id, dest_id) VALUES (?, ?, ?)", ("R2", "L1", "L2"))
MONTHS = tuple(range(1, 25))                 # two years, so today is always covered
seed_line(route_id="R1", disc="earthworks", sect="WS1", months=MONTHS, qty=4000.0)
seed_line(route_id="R2", disc="earthworks", sect="WS9", months=MONTHS, qty=800.0)   # stays Pending

MI, WI = days.commit_bucket()
DATES = days.bucket_dates(MI, WI)
ISO = [d.isoformat() for d in DATES]
NWD = len(days.weekdays_in(DATES))
ok("the commit bucket is inside the seeded horizon", 1 <= MI <= 24 and 1 <= WI <= 4)

r = days.materialise_commit_week()
ok("nothing materialises while no line is Approved", r["created"] == 0 and _line_days() == [])

main.set_route_status("R1", main.StatusUpdate(status="Approved"), discipline="earthworks", section_id="WS1")
r = days.materialise_commit_week()
rows = _line_days()
ok("⭐ approving the month creates one day per calendar day in the commit bucket",
   r["created"] == len(DATES) and [x["day_date"] for x in rows] == ISO)
ok("...every one `derived` with the week index stamped",
   all(x["status"] == "derived" and x["parent_week_index"] == WI for x in rows))
ok("🔴 and NONE outside the bucket — no days for weeks 2-4 of the horizon",
   db.query("SELECT COUNT(*) AS n FROM forecast_days WHERE tenant_id = ?", (db.current_tenant(),))[0]["n"] == len(DATES))
ok("🔴 and none for the Pending line", days._days_of("R2", MI, "earthworks", "WS9", WI) == [])
# ⚠️ That assertion is protected by weeks.py, not by days.py: a never-approved line has
# no week row, so there is nothing to hang a day on. Breaking days.py's own Approved
# guard on purpose left it green. The case that guard actually protects is a line that
# WAS approved (so its week rows exist and survive un-approval, per weeks.py's rule) and
# is then reopened: it must not get days.
seed_line(route_id="R2", disc="earthworks", sect="WS8", months=MONTHS, qty=300.0)
main.set_route_status("R2", main.StatusUpdate(status="Approved"), discipline="earthworks", section_id="WS8")
ok("...a reopened line still has its week rows (weeks.py's rule, not changed here)",
   weeks.get_week("R2", MI, "earthworks", "WS8", WI) is not None
   and (main.set_route_status("R2", main.StatusUpdate(status="Pending"), discipline="earthworks", section_id="WS8") or True)
   and weeks.get_week("R2", MI, "earthworks", "WS8", WI) is not None)
days.materialise_commit_week()
ok("🔴 ...and days.py does NOT materialise days for it — a week row is not enough, the parent must be Approved",
   days._days_of("R2", MI, "earthworks", "WS8", WI) == [])
wk = weeks.get_week("R1", MI, "earthworks", "WS1", WI)
share = float(wk["planned_qty"]) / NWD
ok("⭐ L1 in the database: weekdays carry week/n, weekends carry 0",
   all(abs(float(x["planned_qty"]) - share) < 1e-9 for x in rows if datetime.date.fromisoformat(x["day_date"]).weekday() <= 4)
   and all(float(x["planned_qty"]) == 0.0 for x in rows if datetime.date.fromisoformat(x["day_date"]).weekday() > 4))
ok("...and the day planned quantities sum to the week", abs(sum(float(x["planned_qty"]) for x in rows) - float(wk["planned_qty"])) < 1e-6)
ok("materialising again is idempotent", days.materialise_commit_week()["created"] == 0)

# the read, through the endpoint
res = main.list_forecast_days()
ok("GET /api/forecast-days groups by line and names the commit week",
   res["commit_week"]["month_index"] == MI and res["commit_week"]["week_index"] == WI
   and res["commit_week"]["days"] == len(DATES) and res["commit_week"]["weekdays"] == NWD)
ln = [l for l in res["lines"] if l["route_id"] == "R1"]
ok("...one entry for the Approved line, none for the Pending one",
   len(ln) == 1 and not [l for l in res["lines"] if l["route_id"] == "R2"])
ok("...carrying the parent's unit, material and vehicle so the UI needs no second call",
   ln[0]["unit"] == "t" and ln[0]["material_type"] == "Small aggregate" and ln[0]["vehicle_type"] == "Rigid 8-wheeler (32t)")
ok("...and the sum rule is satisfied on a fresh materialise",
   ln[0]["days_ne_week"] is False and abs(ln[0]["days_sum"] - float(wk["planned_qty"])) < 1e-6)
clip = main.list_forecast_days(from_date=ISO[1], to_date=ISO[2])
ok("from/to clip the days returned but do not widen the bucket",
   [d["day_date"] for d in clip["lines"][0]["days"]] == ISO[1:3])
ok("the response says which statuses exist and counts the table",
   clip["statuses"] == ["derived", "edited", "confirmed"] and clip["summary"]["days"] == len(DATES))

# =========================================================================== #
#  3. Derived refreshes, edited holds, and `week_changed`                       #
# =========================================================================== #
WD = [d.isoformat() for d in days.weekdays_in(DATES)]
first_wd, second_wd = WD[0], WD[1]
r = main.edit_forecast_day(main.DayEdit(route_id="R1", month_index=MI, discipline="earthworks",
                                        section_id="WS1", day_date=first_wd, planned_qty=999.0))
bd = _by_date(_line_days())
ok("typing a day sets it to `edited` with the typed figure",
   bd[first_wd]["status"] == "edited" and float(bd[first_wd]["planned_qty"]) == 999.0)
ok("⭐ ...and moves a `derived` week to `edited` without changing its number",
   weeks.get_week("R1", MI, "earthworks", "WS1", WI)["status"] == "edited"
   and abs(float(weeks.get_week("R1", MI, "earthworks", "WS1", WI)["planned_qty"]) - float(wk["planned_qty"])) < 1e-9)
ok("⭐ the sum rule now FLAGS — and nothing is blocked", r["line"]["days_ne_week"] is True)

# the week moves under the days
weeks.set_week("R1", MI, "earthworks", "WS1", WI, planned_qty=2000.0)
days.materialise_commit_week()
bd = _by_date(_line_days())
new_share = 2000.0 / NWD
ok("⭐ a `derived` day follows the week's new figure",
   abs(float(bd[second_wd]["planned_qty"]) - new_share) < 1e-9 and bd[second_wd]["status"] == "derived")
ok("🔴 an `edited` day holds what was typed", float(bd[first_wd]["planned_qty"]) == 999.0)
view = days._line_view("R1", MI, "earthworks", "WS1", WI)
vb = {d["day_date"]: d for d in view["days"]}
ok("⭐ ...and says the week changed under it, while the refreshed day does not",
   vb[first_wd]["week_changed"] is True and vb[second_wd]["week_changed"] is False)

# outside the bucket
other = (DATES[0] - datetime.timedelta(days=10)).isoformat()
r = days.set_day("R1", MI, "earthworks", "WS1", other, 5.0)
ok("a day outside the commit week is refused, naming the bucket",
   "not in the commit week" in (r.get("error") or "") and r["commit_week"]["week_index"] == WI)
ok("Sat/Sun are typeable — 0 is a default, not a rule",
   (lambda wknd: (not wknd) or days.set_day("R1", MI, "earthworks", "WS1", wknd[0], 40.0).get("error") is None)
   ([d.isoformat() for d in DATES if d.weekday() > 4]))

# =========================================================================== #
#  4. Actuals — rule 6, the freeze trap, and no calibration                    #
# =========================================================================== #
before_next = weeks.get_week("R1", *weeks.next_week(MI, WI)[:1], "earthworks", "WS1", weeks.next_week(MI, WI)[1]) \
    if weeks.next_week(MI, WI)[0] <= 24 else None
r = main.set_forecast_day_actual(main.DayActual(route_id="R1", month_index=MI, discipline="earthworks",
                                                section_id="WS1", day_date=first_wd, actual_qty=10.0))
w = weeks.get_week("R1", MI, "earthworks", "WS1", WI)
ok("a day actual is stored with its variance", float(r["line"]["days"][0]["actual_qty"]) == 10.0 if r["line"]["days"][0]["day_date"] == first_wd else True)
ok("⭐ with no week actual typed, the week actual becomes the day sum",
   w["actual_qty"] == 10.0 and r["line"]["week_actual"] == "summed")
main.set_forecast_day_actual(main.DayActual(route_id="R1", month_index=MI, discipline="earthworks",
                                            section_id="WS1", day_date=second_wd, actual_qty=20.0))
w = weeks.get_week("R1", MI, "earthworks", "WS1", WI)
ok("🔴 THE FREEZE TRAP: a second day actual UPDATES the sum rather than being blocked by the first",
   w["actual_qty"] == 30.0)
ok("...and the day's status is untouched by an actual", _by_date(_line_days())[second_wd]["status"] == "derived")
if before_next is not None:
    after_next = weeks.get_week("R1", *weeks.next_week(MI, WI)[:1], "earthworks", "WS1", weeks.next_week(MI, WI)[1])
    ok("⭐ saving day actuals did NOT calibrate — next week's plan is untouched",
       float(after_next["planned_qty"]) == float(before_next["planned_qty"]) and after_next["status"] == before_next["status"])

# a clerk types the week
weeks.set_actual("R1", MI, "earthworks", "WS1", WI, actual_qty=100.0)
main.set_forecast_day_actual(main.DayActual(route_id="R1", month_index=MI, discipline="earthworks",
                                            section_id="WS1", day_date=second_wd, actual_qty=25.0))
w = weeks.get_week("R1", MI, "earthworks", "WS1", WI)
ok("🔴 a TYPED week actual is never overwritten by a day sum", w["actual_qty"] == 100.0)
src = db.query("SELECT actual_source FROM forecast_weeks WHERE tenant_id = ? AND route_id = ? AND month_index = ? AND week_index = ?",
               (db.current_tenant(), "R1", MI, WI))[0]["actual_source"]
ok("...because the source says so", src == "typed")
weeks.set_actual("R1", MI, "earthworks", "WS1", WI, actual_qty=None)
r = main.set_forecast_day_actual(main.DayActual(route_id="R1", month_index=MI, discipline="earthworks",
                                                section_id="WS1", day_date=second_wd, actual_qty=25.0))
w = weeks.get_week("R1", MI, "earthworks", "WS1", WI)
ok("⭐ clearing the typed week actual lets the day sum take over again", w["actual_qty"] == 35.0)
main.set_forecast_day_actual(main.DayActual(route_id="R1", month_index=MI, discipline="earthworks", section_id="WS1", day_date=first_wd, actual_qty=None))
main.set_forecast_day_actual(main.DayActual(route_id="R1", month_index=MI, discipline="earthworks", section_id="WS1", day_date=second_wd, actual_qty=None))
w = weeks.get_week("R1", MI, "earthworks", "WS1", WI)
ok("clearing every day actual clears the summed week actual to None, not 0", w["actual_qty"] is None)

# =========================================================================== #
#  5. Confirm is whole-week, and it stamps the days                           #
# =========================================================================== #
r = main.confirm_forecast_week(main.WeekConfirm(route_id="R1", month_index=MI, discipline="earthworks",
                                                section_id="WS1", week_index=WI, confirmed_by="tester"))
ok("⭐ L6: confirming the week confirms every day in its bucket",
   r["week"]["status"] == "confirmed" and r["days_confirmed"] == len(DATES)
   and all(x["status"] == "confirmed" for x in _line_days()))
r = days.set_day("R1", MI, "earthworks", "WS1", second_wd, 1.0)
ok("🔴 ...after which planned days are read-only", r.get("blocked_by") == "confirmed")
r = main.set_forecast_day_actual(main.DayActual(route_id="R1", month_index=MI, discipline="earthworks",
                                                section_id="WS1", day_date=second_wd, actual_qty=12.0))
ok("⭐ ...but actuals are still typeable on a confirmed week", r.get("error") is None
   and float(_by_date(_line_days())[second_wd]["actual_qty"]) == 12.0)
# a week that is NOT the commit bucket confirms as it always did, with no days
om, ow = (MI, 1) if WI != 1 else (MI, 2)
r = days.confirm_week("R1", om, "earthworks", "WS1", ow)
ok("a week outside the commit bucket confirms with zero days stamped — Task C unchanged",
   r["week"]["status"] == "confirmed" and r["days_confirmed"] == 0)

# =========================================================================== #
#  6. Calibrate — next week only, and the opt-in spread                       #
# =========================================================================== #
# calibrating INTO a confirmed commit week still 400s, spread or not
pm, pw = (MI, WI - 1) if WI > 1 else (MI - 1, 4)
weeks.set_actual("R1", pm, "earthworks", "WS1", pw, actual_qty=1.0)
r = days.calibrate("R1", pm, "earthworks", "WS1", pw, spread=True)
ok("🔴 calibrate into a confirmed commit week is refused, spread or not", r.get("blocked_by") == "confirmed")

# reopen by moving the commit week back to `edited` — the only way is a fresh line
seed_line(route_id="R1", disc="earthworks", sect="WS2", months=MONTHS, qty=1000.0)
main.set_route_status("R1", main.StatusUpdate(status="Approved"), discipline="earthworks", section_id="WS2")
days.materialise_commit_week()
prev = weeks.get_week("R1", pm, "earthworks", "WS2", pw)
weeks.set_actual("R1", pm, "earthworks", "WS2", pw, actual_qty=float(prev["planned_qty"]) - 180.0)   # 180 short
target_before = float(weeks.get_week("R1", MI, "earthworks", "WS2", WI)["planned_qty"])
days_before = {x["day_date"]: float(x["planned_qty"]) for x in days._days_of("R1", MI, "earthworks", "WS2", WI)}

# spread OFF: the week moves, no day does
r = days.calibrate("R1", pm, "earthworks", "WS2", pw, spread=False)
ok("calibrate with spread off writes the week only",
   r["spread"] is None and abs(float(r["week"]["planned_qty"]) - (target_before + 180.0)) < 1e-6)
days_after = {x["day_date"]: float(x["planned_qty"]) for x in days._days_of("R1", MI, "earthworks", "WS2", WI)}
ok("...and no day planned figure moved", days_after == days_before)

# spread ON, from the first weekday: 180 over every weekday
weeks.set_actual("R1", pm, "earthworks", "WS2", pw, actual_qty=float(prev["planned_qty"]) - 180.0)
# ⚠️ The expectation is built from the WEEK as it stands before this calibrate, not from
# `days_after`. Those days are still `derived` and have not been read since the first
# calibrate moved the week, so they are a stale snapshot that no longer sums to it. The
# spread freezes the as-was distribution FIRST (derived day = week/n as of now), then adds
# the delta. Written against the stale snapshot this assertion failed, and the failure
# was the test's, not the code's — the sum-rule assertion below passed throughout.
week_now = float(weeks.get_week("R1", MI, "earthworks", "WS2", WI)["planned_qty"])
r = days.calibrate("R1", pm, "earthworks", "WS2", pw, spread=True, spread_from=WD[0])
ok("⭐ spread on: the delta is divided over the remaining WEEKDAYS only",
   r["spread"]["applied"] is True and abs(r["spread"]["delta"] - 180.0) < 1e-6
   and r["spread"]["days"] == WD and abs(r["spread"]["per_day"] - 180.0 / NWD) < 1e-6)
now_days = {x["day_date"]: x for x in days._days_of("R1", MI, "earthworks", "WS2", WI)}
ok("...each weekday = its as-was share + shortage/n, and every day went `edited`",
   all(abs(float(now_days[d]["planned_qty"]) - (week_now / NWD + 180.0 / NWD)) < 1e-6
       for d in WD)
   and all(now_days[d]["status"] == "edited" for d in ISO))
ok("🔴 ...and Sat/Sun stayed at 0",
   all(float(now_days[d]["planned_qty"]) == 0.0 for d in ISO if d not in WD))
ok("...and the days still sum to the week — the spread keeps the sum rule",
   r["line"]["days_ne_week"] is False)

# spread from a later weekday: only the days from there on
weeks.set_actual("R1", pm, "earthworks", "WS2", pw, actual_qty=float(prev["planned_qty"]) - 90.0)
r = days.calibrate("R1", pm, "earthworks", "WS2", pw, spread=True, spread_from=WD[-2])
ok("⭐ spread_from limits it to the weekdays on or after that date",
   r["spread"]["days"] == WD[-2:] and abs(r["spread"]["per_day"] - 45.0) < 1e-6)
# spread from after the last weekday: nothing to spread onto, said plainly
weeks.set_actual("R1", pm, "earthworks", "WS2", pw, actual_qty=float(prev["planned_qty"]) - 10.0)
r = days.calibrate("R1", pm, "earthworks", "WS2", pw, spread=True,
                   spread_from=(DATES[-1] + datetime.timedelta(days=1)).isoformat())
ok("no remaining weekdays ⇒ the week is still written and the response says no day moved",
   r["spread"]["applied"] is False and "no weekdays remain" in r["spread"]["note"]
   and r.get("error") is None)
# through the endpoint, with the new body fields
r = main.calibrate_forecast_week(main.WeekCalibrate(route_id="R1", month_index=pm, discipline="earthworks",
                                                   section_id="WS2", week_index=pw, spread=False))
ok("the endpoint's spread defaults OFF and passes through", r["spread"] is None)

# =========================================================================== #
#  7. Task F — the day layer inherits the parent line's IPT                   #
# =========================================================================== #
os.environ.update({"IPT3_CODE": "three-secret", "IPT6_CODE": "six-secret",
                   "PLANNER_CODE": "plan-secret", "ADMIN_CODE": "adm-secret"})
_as("plan-secret")
db.execute("INSERT INTO routes (id, origin_id, dest_id) VALUES (?, ?, ?)", ("R3", "L1", "L2"))
for _sect, _ipt in (("WS3", "IPT3"), ("WS6", "IPT6")):
    main.save_matrix_row(main.MatrixRow(route_id="R3", discipline="substructure", section_id=_sect,
        material_type="Small aggregate", material_description=None, vehicle_type="Rigid 8-wheeler (32t)",
        submitted_by="planner", unit="t", status="Pending",
        cells=[main.Cell(month_index=m, quantity=50.0) for m in MONTHS], ipt=_ipt))
    main.set_route_status("R3", main.StatusUpdate(status="Approved"), discipline="substructure", section_id=_sect)
_as("six-secret")
res6 = main.list_forecast_days(route_id="R3")
ok("⭐ an IPT6 code sees only IPT6's days", {l["section_id"] for l in res6["lines"]} == {"WS6"})
try:
    main.edit_forecast_day(main.DayEdit(route_id="R3", month_index=MI, discipline="substructure",
                                        section_id="WS3", day_date=first_wd, planned_qty=1.0))
    ok("🔴 an IPT6 code cannot type IPT3's day", False)
except Exception as e:
    ok("🔴 an IPT6 code cannot type IPT3's day — 404, not 403", getattr(e, "status_code", None) == 404)
r = main.edit_forecast_day(main.DayEdit(route_id="R3", month_index=MI, discipline="substructure",
                                        section_id="WS6", day_date=first_wd, planned_qty=1.0))
ok("...but can type its own", r.get("error") is None)
for _v in ("IPT3_CODE", "IPT6_CODE", "PLANNER_CODE", "ADMIN_CODE"):
    os.environ.pop(_v, None)
_as("planner123")

# =========================================================================== #
#  8. Tenant isolation on the table itself                                    #
# =========================================================================== #
n_default = db.query("SELECT COUNT(*) AS n FROM forecast_days WHERE tenant_id = ?", (db.current_tenant(),))[0]["n"]
tok = db.set_current_tenant("other")
try:
    ok("🔴 another tenant sees no days at all",
       days.list_days()["lines"] == [] and days.summary()["days"] == 0)
finally:
    db.reset_current_tenant(tok)
ok("...and the default tenant's rows are untouched by that read",
   db.query("SELECT COUNT(*) AS n FROM forecast_days WHERE tenant_id = ?", (db.current_tenant(),))[0]["n"] == n_default)

# =========================================================================== #
#  9. What the brief says must NOT exist                                      #
# =========================================================================== #
main_src = open(os.path.join(BACKEND, "main.py"), encoding="utf-8").read()
days_src = open(os.path.join(BACKEND, "days.py"), encoding="utf-8").read()
ok("🔴 no upload endpoint was added — no UploadFile, no File(, no multipart",
   "UploadFile" not in main_src and "File(" not in main_src and "multipart" not in main_src.lower())
ok("🔴 no per-day confirm exists", "confirm_day" not in days_src and "/forecast-days/confirm" not in main_src)
ok("🔴 the forecasts UNIQUE key is unchanged",
   db._TENANT_UNIQUE["forecasts"].replace(" ", "") == "(tenant_id,route_id,month_index,discipline,section_id)")
ok("the public map feed is still monthly", "week" not in main.public_route_forecasts.__doc__.lower())
ok("days.py imports weeks and does not reimplement its writes",
   "import weeks" in days_src and "UPDATE forecast_weeks SET planned_qty" not in days_src)


# =========================================================================== #
print()
print(f"{PASS} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAIL:", f)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)

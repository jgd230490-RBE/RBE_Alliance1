rbe-lookahead-slices3-6-0909.zip
================================
Delivered 2026-09-09 (late evening). Extract over the repo root; the paths
already match. Nothing is deleted. factors.json is not included and nothing
touches it.

REQUIRES slice 2 applied (it is — the live /api/forecast-days returned
`totals` at 14:xx). SUPERSEDES nothing; sits on top.

⚠️ SCHEMA CHANGE: eight new COLUMNS, no new table.
⚠️ TWO NEW PYTHON DEPENDENCIES: openpyxl, reportlab (requirements.txt).
   Render installs them on the next deploy from requirements.txt. If the
   build log does not show them, the export endpoints return 503 with the
   import error and everything else still works.

FILES
-----
  backend/clashes.py              NEW. The clash rail (brief §6), flag only.
  backend/lookahead.py            NEW. GET /api/lookahead — the page in one read.
  backend/export.py               NEW. XLSX (openpyxl) + PDF one-pager (reportlab).
  backend/days.py                 bucket=next (the Thursday process); reopen.
  backend/weeks.py                actual_cost_eur; calibrate carries the DELTA;
                                  reopen_week.
  backend/derived.py              €, km_basis, rates in context.
  backend/db.py                   the eight columns + init_lookahead_db().
  backend/network.py              set_route_planning(); routes_status carries
                                  the planning fields.
  backend/main.py                 endpoints below; init_lookahead_db() in lifespan.
  backend/requirements.txt        + openpyxl==3.1.5, reportlab==4.4.10
  backend/tests/test_lookahead.py 125 -> 186
  backend/tests/parse_frontend.js 270 -> 299
  backend/tests/render_frontend.js 28 -> 49
  backend/tests/fixtures/lookahead_page.json  NEW. Written by test_lookahead.py,
                                  read by render_frontend.js. Regenerated every run.
  frontend/index.html             LookAhead rewritten (three views); RoutePlanning
                                  block on the route edit form.
  README.txt                      this file

DEPLOY — what to watch
----------------------
1. Build log: openpyxl and reportlab install.
2. First boot: init_lookahead_db() ALTERs routes (+5) and forecast_weeks (+3).
   On Postgres these are ADD COLUMN IF NOT EXISTS — silent when already there.
   Nothing to migrate; every new column is nullable and starts empty.
3. Open Look-ahead. It lands on Commit · this week. With no Approved September
   line it shows the empty state — approve one on a baked route to see it work.

NEW ENDPOINTS
-------------
  GET  /api/lookahead?bucket=commit|next&tark_tee=1   the page's read model
  GET  /api/forecast-weeks/clashes?bucket=            the rail alone
  GET  /api/forecast-weeks/export?format=xlsx|pdf&bucket=   browser download
  POST /api/forecast-weeks/reopen                     confirmed -> edited, days follow
  PUT  /api/admin/routes/{id}/planning                cap + rates + km basis (token)
  PUT  /api/forecast-weeks/actual                     gains actual_cost_eur
  GET  /api/forecast-days                             gains ?bucket=next

DECISIONS YOU MADE THIS SESSION, AS BUILT
-----------------------------------------
* Everything in one pass: page + rail + rates + export.
* Cost model: target €/t, €/km (and €/load) typed on the ROUTE (route edit form,
  "Planning · Look-ahead" block). Actual confirmed € typed per LINE per WEEK on
  the Account view beside the actual tonnes. € variance = planned € − actual €.
  The three rates are SUMMED where filled (Estonian quotes are "base per load
  + €/km"); the brief's "first non-null" could not hold that.
* km basis (C20): per route, default round trip — €/km and t·km both use it.
* Spread (C19): remaining weekdays from today. When the target week has not
  started (the Thursday case), that is all of Mon–Fri.
* Thursday process: a "this week / next week" switch on the page. bucket=next
  creates next week's days on demand; the default read is unchanged. Account
  is then THIS week (partial). And calibrate now carries only the DELTA of the
  variance — a Thursday spread with a partial actual followed by Friday's real
  figure adds only the difference, never the whole variance twice
  (forecast_weeks.calibrated_qty records what was carried).
* C18: the mock's button bar (Export XLSX + green Confirm week; PDF as a small
  second button), the per-line "spread on this week" button (no dialog), ONE
  clash rail with "+N more". Pills sit under PageHeader; the left rail stays.
* Re-open week: exists now (the mocks' footer promised it). Confirmed -> edited,
  days follow, nothing deleted.
* Approval is NOT gated on baking (your process rule, not enforced in code —
  say if you want a refusal at approval).

WHAT THE UI DOES
----------------
Commit: KPI strip (planned t, trips, veh/day peak, t·km, planned €, last-week
shortage, last-week delivered), the rail, the day grid (qty typeable while the
week is not confirmed; "17 tr · 3 veh" under each; unbaked lines show trips and
"veh —" with a NOT BAKED chip), ▶ expands km/trip · km/week · veh peak · t·km ·
cycle · €, Stock held with the forecast balance. Confirm week = every line, one
dialog, four notes. Re-open when all confirmed.
Account: 4 KPI cards, table with actual qty / actual € inputs, variance, € var,
note, and the action state (held / spread on this week / applied · n / waiting).
Horizon: 8 week columns (commit month + next) with ACCOUNT / COMMIT / MAKE-READY /
EARLY WARNING labels, commit column washed, status line per cell, no Confirm
button; the Stockpiles panel (capacity + consumption) sits under it.
Route form: "Planning · Look-ahead" block — cap, km basis, €/load, €/t, €/km.

WHAT WAS NOT TESTED
-------------------
* HTTP layer stubbed (endpoint bodies run; the export endpoint's Response
  object is only exercised on Render). No Postgres branch. HERE never called.
* Tark Tee: unreachable from the sandbox, so the rail's TARK_TEE path only ran
  its "unavailable" branch. The page SAYS when Tark Tee could not be reached.
* The page was rendered with react-dom/server from a fixture the backend
  wrote (49 render assertions, three views + empty states). It has never been
  opened in a browser. Column widths, the sticky Line column and the day grid
  at week 4 (ten columns) are unseen.
* The XLSX was re-opened with openpyxl and checked (sheets, rows, €). The PDF's
  bytes were grepped for its text. Neither has been looked at.
* The XLSX/PDF download uses fetch + blob so the access-code header rides
  along; a plain link would 401. Untested in a browser.

SUITE
-----
Thirteen runnable files, 2,499 passed / 0 failed, every count watched print,
and verified again on a fresh clone with this zip extracted over it:
  test_lookahead.py 186 (was 125)   parse_frontend.js 299 (was 270)
  render_frontend.js 49 (was 28)    test_tenant_audit.py 29 (was 28 — clashes.py)
  unchanged: parse_map 484, test_week1 315, test_phase4 220, test_phase5a 215,
  test_phase2 160, test_phase3 154, test_phase45 144, test_ipt_overlay 140,
  test_phase25a 104.

Seventeen deliberate regressions, seventeen caught — four only after fixing the
test: a cap fixture that was already exceeded (≥ vs > indistinguishable), no
line without an IPT, a cost mutant equivalent for the "absent field" case (an
explicit null was the case that told them apart), and one sheet assertion that
CRASHED instead of failing (lesson 13, again). Plus one frontend: a `break`
after the first confirm still contained the loop the assertion looked for.

LANDING GREPS (each run, not reasoned)
--------------------------------------
  backend/clashes.py backend/lookahead.py backend/export.py exist
  grep -c "def init_lookahead_db" backend/db.py        -> 1
  grep -c "calibrated_qty" backend/weeks.py            -> 4
  grep -c "/api/lookahead" backend/main.py             -> 1
  grep -c "function RoutePlanning" frontend/index.html -> 1
  grep -c '"rbe_la_view"' frontend/index.html          -> 2
  grep -c "openpyxl" backend/requirements.txt          -> 1

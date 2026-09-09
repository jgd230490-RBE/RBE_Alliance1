rbe-lookahead-slice1-0909.zip
=============================
Delivered 2026-09-09. Extract over the repo root; the paths already match.

THIS ZIP SUPERSEDES rbe-departure-probe-0909.zip
------------------------------------------------
It contains every file that zip contained, at the same or a later state, plus
the Look-ahead slice. So:

  * if you have NOT applied the departure-probe zip: don't. Apply this one.
  * if you HAVE applied it: extract this over the top. Same result.
  * either way, rbe-route-vehicles-0909.zip is already on the repo (verified
    at HEAD earlier today) and nothing here undoes it.

There is no ordering trap this time -- that is why the departure files are
carried again rather than left to a second zip.

FILES
-----
  backend/days.py                 NEW. The commit week, day by day.
  backend/db.py                   forecast_days registered (four places);
                                  forecast_weeks.actual_source added.
  backend/main.py                 GET/PUT /api/forecast-days, PUT .../actual;
                                  confirm and calibrate routed via days.py;
                                  calibrate takes spread + spread_from;
                                  weeks GET also returns commit_week.
                                  (Also carries the departure endpoint.)
  backend/weeks.py                set_actual stamps actual_source='typed'.
                                  The ONE touch to Task C/D code.
  backend/here_routing.py         carried from the departure zip, unchanged.
  backend/network.py              carried from the departure zip, unchanged.
  backend/tests/test_lookahead.py NEW. 72 assertions.
  backend/tests/test_phase4.py    carried from the departure zip, unchanged.
  backend/tests/test_phase45.py   tenanted-table count pin 15 -> 16.
  backend/tests/test_tenant_audit.py  forecast_days added to its registry.
  env.example                     the ADMIN_TOKEN comment no longer reads as
                                  if "openssl rand -hex 24" were the value.

Nothing is deleted. factors.json is not included and nothing touches it.
There IS a schema change: one new table and one new column. See DEPLOY.

DEPLOY
------
Boot creates forecast_days and ALTERs actual_source onto forecast_weeks in
init_weeks_db(), both idempotent. On Postgres the ALTER is ADD COLUMN IF NOT
EXISTS. On SQLite it is a plain ADD COLUMN inside try/except, same pattern as
the locations capacity columns from Week 1.

I have NOT run this against Postgres. The tenant migration path for a new
table is the same one that ran for forecast_weeks on 2026-09-02 and has not
changed since. Check the Render deploy log for the line
"Phase 4.5: tenant key added to 1 table(s): forecast_days" on first boot, or
its absence with no error, which means the CREATE already carried the key.

WHAT IT DOES
------------
Sits ON forecast_weeks exactly as forecast_weeks sits on the forecasts line.
Tasks C/D/D2 are not rebuilt. Rules, each one locked in your brief:

  L1   Days exist only for the COMMIT week (the bucket that contains today,
       weeks.editable_week) and only for Approved lines. Mon-Fri each get
       week / n_weekdays. Sat and Sun get 0. Week 4 of a 31-day month is ten
       days long and still weekday-weighted.

  L6   Confirm is whole-week, one act. POST /api/forecast-weeks/confirm now
       confirms the week and stamps every day in its bucket confirmed. After
       that, planned days are read-only (a PUT returns blocked_by: confirmed)
       and actuals are still typeable. No per-day confirm exists.

  derived / edited / confirmed on the day, same three as the week, same
       meaning. A derived day follows the week's figure; an edited one holds.
       parent_week_qty stamps the week-as-at-last-write, so "week changed" on
       a day is an exact test -- the argument weeks.py makes for parent_qty,
       one level down.

  Sum rule is a FLAG. sum(day planned) should equal the week. When it does not,
       the read says days_ne_week: true. Confirm is still allowed.

  Actuals, two paths, and the second is the one that needed a column:
         a) clerk types a week actual  -> actual_source = 'typed'. Day sums
            never touch it.
         b) clerk types day actuals    -> week actual = running sum of them,
            actual_source = 'days', updated on every day save.
       Without actual_source the FIRST partial day sum would have been
       indistinguishable from a typed figure and would have frozen the week
       there. Clearing a typed week actual lets the day sums take over again.
       Clearing every day actual clears the week to None, not 0.

  Calibrate spread is OPT-IN, default off. spread=true divides the delta that
       was just applied to the target week over that week's WEEKDAYS on or
       after spread_from (today if omitted), and stamps every day in the
       bucket edited. Only the commit week has days, so calibrating into any
       other week writes the week total only and says so. Saving an actual
       still never calibrates.

ONE THING I HAD TO DECIDE, AND WHERE YOU CAN OVERRULE IT
--------------------------------------------------------
"Spread across remaining commit-week days" (your brief) and "splits across
Mon-Fri of the commit week (36 t/day)" (the Account mock's footer) are the
same thing on a Monday and different things on a Thursday. I followed the
brief's word: from today onward. spread_from is an explicit parameter so the
UI can send Monday if you want the mock's behaviour. Tell me which.

THE THING THE FIRST TEST RUN CAUGHT
-----------------------------------
The spread refreshed derived days to the NEW week total and then added the
delta on top -- double counted. The fix is an ordering: freeze the as-was
distribution BEFORE the week moves, then calibrate, then spread, then stamp
every day edited (because a planner has just chosen a non-uniform split and
the only thing that stops a derived day re-uniforming it on the next read is
not being derived any more). It is the whole function; the docstring says so.

API
---
  GET  /api/forecast-days?from=&to=&route_id=
       -> { commit_week: {month_index, week_index, from, to, weekdays, days},
            lines: [ {route_id, month_index, discipline, section_id, week_index,
                      ipt, unit, material_type, vehicle_type,
                      week: {...the forecast_weeks row...},
                      days: [ {day_date, planned_qty, actual_qty, status,
                               variance, week_changed, ...} ],
                      days_sum, days_ne_week} ],
            statuses, summary }
       Reading materialises, like the week endpoint. from/to clip; they never
       widen. Filtered by the parent line's IPT exactly as weeks are.
  PUT  /api/forecast-days          { line key..., day_date, planned_qty }
  PUT  /api/forecast-days/actual   { line key..., day_date, actual_qty, actual_note }
  POST /api/forecast-weeks/calibrate  now also accepts { spread: bool, spread_from: "YYYY-MM-DD" }
  GET  /api/forecast-weeks            now also returns commit_week (same value as next_week)

No CSV import. No file body. No upload endpoint -- asserted at source level.

NOT IN THIS SLICE
-----------------
No frontend. The Commit view, the day grid, the KPI strip, the clash rail, the
XLSX/PDF export and the derived km/trips/vehicles columns are slices 2-6.
Nothing you see in the app changes until those land. What changes is that the
data and the rules now exist and are tested.

TESTS
-----
Full suite: 2,333 passed, 0 failed (2,256 -> 2,333, +77).

  test_lookahead.py   72  NEW
  test_phase45.py    140 -> 144   its per-table loop picked the new table up
  test_tenant_audit   26 ->  27   likewise -- and it read every query in days.py
                                  and found a tenant predicate on all of them
  test_phase4.py     220          (carried, unchanged)

Twenty-six regressions applied on purpose across today's three deliveries;
all twenty-six caught by the assertion meant to catch them. Two of the nine
new ones needed a second pass, and both were defects in MY tests:

  * the PK-registration assertion subscripted a dict directly, so removing
    the registration raised KeyError and killed the report. Lesson 13, again.
  * "no days for the Pending line" stayed green with days.py's own Approved
    guard DELETED -- because weeks.py never creates a week row for a
    never-approved line, so there was nothing to hang a day on either way.
    The case that guard actually protects is a line approved, then reopened:
    its week rows survive (weeks.py's rule) and it must still get no days.
    Added. Now caught.

Both are the same lesson as this morning: the regression that does not fail
is the informative one.

WHAT IS NOT TESTED
------------------
  * The HTTP layer is stubbed. Endpoint bodies run; nothing proves ?from= and
    ?to= are parsed off a query string or that the routes are mounted.
  * No Postgres branch ran. See DEPLOY.
  * No browser. There is no UI in this slice.
  * "Today" is the real clock for the endpoint-level assertions (as in
    test_week1.py) and an explicit date for the bucket arithmetic. The seed
    spans 24 months so the commit month is always Approved.

PLEASE CHECK ON THE LIVE SITE
-----------------------------
  1. The Render deploy log on first boot -- see DEPLOY.
  2. GET /api/forecast-days with a valid access code. You should see one entry
     per Approved line for this week, days_ne_week false on every line, and
     Sat/Sun at 0. If lines is empty, that is E16's blocker again: no Approved
     lines this month.
  3. The existing Look-ahead tab still works exactly as before. It does not
     read the new endpoint yet.

STILL OPEN
----------
  * Rotate ADMIN_TOKEN. It is still the literal string "openssl rand -hex 24"
    until you change it on Render.
  * Confirm one of your REAL access codes signs in. The demo codes are dead
    (all 401 -- E10's success criterion) but I cannot prove a real one works.
  * Run the departure probe once this is deployed:
    /api/admin/diagnostics/departure/R001?profile=Artic%20Tipper%20(44t)&token=...
  * Push still blocked. Ninth delivery.

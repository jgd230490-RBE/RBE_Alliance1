rbe-dashboard-bigscreen-0911.zip
=================================
Delivered 2026-09-11 (afternoon). Extract over the repo root. Cut against HEAD as of
11 Sep 13:00 UK (your costlines zip applied — the screenshot you sent is that build).
Seven files. NO new dependency, NO schema change, factors.json NOT in this zip.

WHAT YOU ASKED FOR, AND WHAT WAS DONE
-------------------------------------
1. "Remove the stock held table, replace with a stockpile capacity bar chart."
   DONE. The table is gone. One horizontal bar per stockpile = how full it is at the
   end of the chosen week, as a % of its RECORDED capacity: green, amber from 90 %,
   red past 100 %, a dashed line at capacity. Chips pick the week (this month + next;
   "now" marks the week that holds today). Hover a bar for the absolute figures
   (held / max / in / out / room). A stockpile with NO capacity recorded cannot be a
   % — it is listed beside the chart with its balance and "max —", never drawn as
   0 % or as full (your rule: no capacity recorded is not zero capacity). Same
   /api/stockpiles read model as before; nothing typed here.

2. "The cost KPIs need info on hover."
   DONE — for EVERY KPI figure on the page, not only Cost (16 on the four cards, 4
   on the Today card). Each hover gives the exact figure and how it is made, e.g.
   planned EUR: "EUR 111 234 = EUR 4.92/t · EUR 135/trip · EUR 2.25/km — the route's
   typed rates summed, or the Config target where the route has none (6 line-months
   at target)". Asserted at source and in the render: a figure without a hover fails.

3. "Scaling isn't correct on my screen, numbers are cut off."
   Reproduced in a real browser at 1440 px (see NOT TESTED for how). Two causes:
   a) each KPI card packed FOUR figures in one row with `truncate` — 90 px per
      figure at 1440 px, so "1 017 425" became "1 017 …". Now two figures per row,
      no truncate anywhere, the value sized with clamp() so it scales with the
      screen, millions printed compact ("1.02 M") with the exact figure on hover.
   b) the filter row sat in the page header and the vehicle <select> is as wide as
      its longest option (a 50-character vehicle name) — it pushed the month pickers
      off the right edge. The filters are now their own wrapping toolbar under the
      header, each select capped at 13 rem.

4. "Further detail and interactiveness; a big screen; a look-ahead / today's
   deliveries section with a small map; research similar dashboards; positive,
   exciting data."
   BUILT (it is additive — nothing you had is lost, the table and filters are still
   there in the working view):
   - TODAY strip at the top: tonnes, trips, vehicles on the road and lines reported
     for today, from the Look-ahead's own read (/api/lookahead, commit bucket, Tark
     Tee off); "this week to date" and "last week" bars (planned vs reported, the
     account's hold count and shortfall); today's deliveries listed (line, material,
     t, trips, veh, confirmed / edited / planned / reported); and the Look-ahead's
     week map beside it (same CommitMap component, so it draws exactly what the
     Look-ahead draws; cost never reaches the map — asserted).
   - DELIVERED TO DATE card (first card): tonnes moved, % of plan, actual EUR typed,
     and delivered ÷ planned on the reported months. This is the "positive" number —
     it is the sum of the week actuals typed on Look-ahead → Account (a day actual
     rolls into its week). Nothing is assumed: until an actual is typed it says
     "nothing reported yet", and an unreported month counts as not yet delivered.
   - "Delivered against plan" chart (planned t as outlined bars, reported t filled)
     and a Delivered % column in the route table.
   - INTERACTIVE: click any row of the route table (or a bar in "Top routes") and
     every chart and KPI narrows to that line; the table stays whole so you can pick
     another; the chip in the header (or clicking the row again) clears it. The
     subtitle names the focus.
   - BIG SCREEN button (header): fullscreen, the rail folded, filters and the route
     table hidden, larger type, both reads refreshed every 5 minutes with an
     "updated hh:mm" stamp. Esc (or leaving fullscreen) returns to the working view.
   Research notes: the wall-display guidance (Klipfolio; DataCamp/Toptal dashboard
   principles; TransportWorks on logistics KPI dashboards) says: readable in three
   seconds, high contrast, suffixes on big numbers ("34.2 M"), few defended KPIs,
   no interaction expected on the wall, refresh automatically, tie each figure to an
   action. Rail Baltica's own "Progress today" page presents progress as km /
   percentage counters plus a map per country. That shaped the build: the Today
   strip and Delivered-to-date lead; the map is beside them; big-screen mode hides
   what needs a mouse. What the guidance recommends and the product does NOT hold
   is on-time / OTIF and live vehicle positions — see NOT BUILT.

WHAT THIS CHANGES ON THE SERVER (small)
---------------------------------------
GET /api/costing/lines now also returns, per line-month: actual_qty, actual_tonnes,
actual_eur, weeks_reported; totals gain actual_tonnes, actual_eur, reported_lines,
delivered_pct. One extra statement (forecast_weeks, tenant-scoped, no materialise).
Existing fields unchanged; the Forecasts page ignores the new ones.

ACTION ON YOUR SIDE
-------------------
- Apply, deploy. Open the Dashboard on your screen first, then press "Big screen".
- If a stockpile is missing from the chart, it has no capacity recorded: set one on
  the Locations page.
- If "Delivered to date" says nothing reported: that is true until a week actual is
  typed on Look-ahead → Account.

NOT BUILT / OPEN
----------------
- On-time / in-full and live truck positions: the product has no arrival times and
  no tracking. Today's rows show the planned figures and the day's status only.
- The Today strip shows the commit week (the week that holds today). On a Saturday
  or Sunday with no planned quantity it says "no deliveries planned for today".
- The route table is hidden in big-screen mode (13 columns do not read at 6 m).
- The header (brand bar) stays in big-screen mode.

FILES (7)
---------
CHANGED backend/costlines.py (actuals per line-month, totals) ·
        backend/tests/test_costlines.py (32 → 38) ·
        backend/tests/fixtures/cost_lines.json (regenerated; carries a reported line) ·
        backend/tests/parse_frontend.js (340 → 356) · render_frontend.js (85 → 95) ·
        frontend/index.html (Dashboard rebuilt: DashboardToday, DashboardStockChart
        replace DashboardStock; CommitMap gains title/height/flush props and a guarded
        start; Portal folds the rail on big screen) · README.txt

SUITE — every figure watched print, on a FRESH clone with this zip applied
--------------------------------------------------------------------------
test_costing 106 · test_costlines 38 · test_fairprice 47 · test_lookahead 242 ·
test_phase2 160 · test_phase25a 104 · test_phase3 154 · test_phase4 220 ·
test_phase45 144 · test_phase5a 215 · test_tenant_audit 32 · test_week1 317 = 1,779 py
parse_frontend 356 · parse_map 484 · render_frontend 95 · test_ipt_overlay 140 = 1,075 js
TOTAL 2,854 / 0 (+ test_help 16 / 1, still the placeholder.pl). Was 2,822 / 0.
Run the .py files before the .js ones — three fixtures are written by them.

Four deliberate regressions run and caught: the tenant predicate dropped from the
actuals read (tenant audit + costlines fail); an unreported month reading as 0
delivered (3 fail); EUR added to the map's hover (1 fail); a KPI without a hover and
a truncated KPI (4 fail).

NOT TESTED / HOW THE BROWSER CHECK WAS DONE
-------------------------------------------
- This time the page WAS opened in a real browser (headless Chromium in the sandbox)
  against a stub that serves the frontend and canned JSON from the test database,
  with the CDN libraries served locally — so the layout, Chart.js drawing, the row
  click, the focus chip, hover titles and big-screen mode were all seen at 1440 px
  and 1920 px. Screenshots are in the chat.
- NOT seen: Mapbox tiles (no network from the sandbox — the map frame rendered, the
  tiles did not); the 5-minute refresh (not waited for); fullscreen (headless has no
  fullscreen); the live site's real data volumes (the stub had 4 routes, 5 invented
  stockpiles for the chart).
- The HTTP layer is stubbed as always; the query-string parsing is not run.

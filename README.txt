rbe-costlines-0911.zip
======================
Delivered 2026-09-11. Extract over the repo root. Cut against HEAD as of 11 Sep morning
(your fairprice zip applied — thank you; README.txt at HEAD is still the help page's).
Twelve files. NO new dependency, NO schema change, factors.json NOT in this zip.

WHAT YOU ASKED FOR
------------------
1. Every price in EUR/t, EUR/km and EUR/trip as well as the total — Quote, Target and
   Fair alike — so comparisons are like for like.
2. The cost figures on the Forecasts page and the Dashboard.
3. A review of the Dashboard and a general upgrade.

WHAT CHANGED, AND ONE THING YOU WILL NOTICE
-------------------------------------------
THE DASHBOARD NO LONGER DOES ITS OWN ARITHMETIC. It used to compute trips as tonnes /
payload (fractional), km from analysis-batch, CO2 from the emissions factor and a fleet
size from a monthly peak — a second source for numbers the Look-ahead computes on the
server. Now ONE backend read, GET /api/costing/lines, returns every forecast line x
month with volume, haul, carbon and cost from the Look-ahead's own functions; the
Dashboard and the Forecasts page sum what it returns and derive nothing.
  * Trips are ROUNDED UP per line-month (4,010 t / 20 t = 201, not 200.5). Totals
    move up a little. That is the Look-ahead's figure and the right one.
  * An UNBAKED line has NO km, CO2, vehicles or fair EUR on the Dashboard any more —
    the old fallback to the route's headline distance x 2 is gone (your 09 Sep rule).
    The header says "n of m line-months on a baked route · km, CO2e and fair EUR
    exclude the k unbaked". On the live site most routes are unbaked, so expect the
    km and CO2 totals to DROP until routes are baked. That is not a fault.

THE UNITS
---------
Planned (route rates or the Config target) and Fair (the model) each come as
EUR total · EUR/t · EUR/trip · EUR/km. EUR/km is over the km the trips actually run on
the route's basis (round trip unless the route says loaded). Where they show:
  Look-ahead -> Account: under Planned EUR and under Fair EUR, a small line
    "EUR 5.69/t · EUR 114/trip · EUR 1.90/km".
  Look-ahead -> Commit, expanded row (▶): "fair EUR … (model) = EUR …/t · …/trip · …/km
    · quote = …" (or "target = …").
  Dashboard: the Cost group (planned EUR, fair EUR, fair EUR/t, fair EUR/km with
    EUR/trip under it); hover any EUR in the route table for the triple.
  Forecasts page: a Cost column per line — planned (with a 'target' chip where it is
    the Config rate) and fair with EUR/t beside it; the triple on hover. The CSV
    export gains nine cost columns.

THE DASHBOARD REVIEW — what was changed and why
-----------------------------------------------
- KPI strip regrouped into three cards: VOLUME (tonnes, trips, truck-km, peak fleet),
  COST (planned EUR, fair EUR, fair EUR/t, fair EUR/km), CARBON (tCO2e, kg/t, t·km,
  peak month). Peak fleet is new at the top — it was only in the table before.
- IPT and discipline filters (every other surface had them; the Dashboard did not).
- A coverage line in the header ("n of m line-months baked …") replaces the amber
  box at the bottom of the page, and the diesel index with its bulletin date sits
  beside it.
- Charts: "Cost over time" (planned vs fair by month) and "Vehicles needed over
  time" are new; the others are unchanged in content.
- Route table: km/trip (was "Dist km" — it is the trip distance on the route basis),
  Peak veh (the server's figure), Planned EUR with a quote/target chip, Fair EUR with
  winter/thaw marks, Fair EUR/t. Origin -> destination and the vehicle under the route
  id. "Peak/day" and "Trucks" (client arithmetic) are gone; "kg/t" moved to the KPI.
- The footnote states where every figure comes from and how it is made.
NOT changed: the stock panel, the material charts, the Top routes chart.

ACTION ON YOUR SIDE
-------------------
- Apply, deploy. Open the Dashboard and read the coverage line first.
- Nothing to configure; nothing to seed.

NOT BUILT / OPEN
----------------
- The Submit-forecast matrix does not show cost while you type (it would need a per-
  cell read; say if you want it).
- The Dashboard has not been seen in a browser (Chart.js does not draw in the
  harness; the page renders around empty canvases). First look: the three KPI cards,
  the two new charts, the route table at laptop width with 13 columns.
- test_help.py still fails its one "orphaned media" assertion until
  frontend/help/media/placeholder.pl is deleted in the web UI.

FILES (12)
----------
NEW     backend/costlines.py · backend/tests/test_costlines.py (32) ·
        backend/tests/fixtures/cost_lines.json (written by that test)
CHANGED backend/derived.py (unit_prices; eur_units / fair_units on week figures) ·
        backend/lookahead.py (planned_units / fair_units on Account rows) ·
        backend/main.py (GET /api/costing/lines) ·
        backend/tests/parse_frontend.js (340) · render_frontend.js (85) ·
        fixtures/lookahead_page.json · fixtures/lookahead_page_fuel.json (regenerated) ·
        frontend/index.html (Dashboard rewritten; Forecasts cost column; Look-ahead units)
        README.txt

SUITE — every figure watched print, on a FRESH clone with this zip applied
--------------------------------------------------------------------------
test_costing 106 · test_costlines 32 · test_fairprice 47 · test_lookahead 242 ·
test_phase2 160 · test_phase25a 104 · test_phase3 154 · test_phase4 220 ·
test_phase45 144 · test_phase5a 215 · test_tenant_audit 32 · test_week1 317 = 1,773 py
parse_frontend 340 · parse_map 484 · render_frontend 85 · test_ipt_overlay 140 = 1,049 js
TOTAL 2,822 / 0 (+ test_help 16 / 1, the placeholder). Was 2,777 / 0.
Run the .py files before the .js ones — three fixtures are written by them.

NOT TESTED
----------
- The Dashboard's charts (Chart.js never draws here) and its layout.
- The HTTP parsing of ?from=&to=&status= (stubbed layer; the bodies run).
- Two deliberate regressions run and caught: the access filter removed from the
  cost-lines read (the IPT assertion fails); CO2 on half the km (the CO2 assertion fails).

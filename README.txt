rbe-costing-fuel-0910.zip
=========================
Delivered 2026-09-10 (evening). Extract over the repo root, ON TOP of
rbe-lookahead-calendar-weeks-0910.zip (which HEAD already has — README.txt at
HEAD began with that name when this was cut). Seventeen files. NO new pip
dependency. ONE new table (fuel_index — global, see point 4). No route or
week column changes. factors.json is NOT in this zip.

WHAT YOU ASKED FOR, AND WHAT THIS IS
------------------------------------
You asked for a planned / fair delivery price on the Look-ahead that could later
drive the whole forecast, and attached Grok's fuel-index note. You chose "both,
target rate then fuel". So, in that order:

1. TARGET RATE (the planned price where a route has no rate)
   Config -> "Costing · fuel" tab -> € per load / € per tonne / € per km, saved
   with the admin token. Every route WITHOUT a rate of its own now prices at
   this on the Look-ahead (Commit days, the week, the KPI, the Account rows,
   both exports), and every such figure is marked "target". A route with ANY
   rate typed on its route form prices from that ALONE — the target is never
   mixed in (a typed €/t contract must not gain the target's €/km). Nothing is
   seeded: until you type a target, everything reads "rate not set" exactly
   as before.

2. FUEL INDEX + BAF (Grok's note, "price + BAF on an existing quote")
   - The server fetches https://eurooilwatch.com/api/v1/prices (EU Weekly Oil
     Bulletin, Estonia, automotive diesel with taxes; verified 10 Sep: 1.922,
     bulletin 2026-09-07) and stores ONE row. The browser never calls it.
   - A widget (one component, three mounts: Commit KPI strip right of t·km,
     top of the Account view, Config) shows "Diesel EE €1.922 / L · bulletin
     7 Sep 2026", a "stale" chip when the bulletin is older than 8 days, and
     "Index unavailable — type a price" when there is no row.
   - Two typed settings on the widget (planner or admin): Yard €/L (optional,
     what the haulier pays — for cost-plus LATER; never replaces the index) and
     Fuel share % (empty = no BAF).
   - The BAF base is LOCKED by the first "Confirm week" that finds an index
     row and no base. Later confirms never move it. Admin resets it on Config.
   - BAF % = (index now ÷ base − 1) × share. Every quote then gets a SECOND
     figure, "+ BAF € …", beside it — on days, weeks, the KPI, the Account
     rows, the XLSX ("€ + BAF" column) and the PDF (under the quote). The quote
     itself is never replaced, and € var on Account still compares the actual
     against the QUOTE.
   - No quote (no route rate, no target) ⇒ diesel only, no € invented.

ACTION ON YOUR SIDE
-------------------
a. Deploy; the boot creates fuel_index (CREATE TABLE IF NOT EXISTS, no
   migration). Nothing to seed.
b. Open Config -> Costing · fuel. Press "Fetch the bulletin now" with the
   admin token, or just open the Look-ahead — the widget's first read starts
   a background fetch when the row is older than 12 h. ⚠️ UNVERIFIED: whether
   Render's outbound can reach eurooilwatch.com. The sandbox could (through
   its proxy); Render should. If the widget stays on "Index unavailable",
   read the Render log for "fuel-index-refresh" or GET /api/fuel-index and
   look at index.last_error. Fallback that always works: type the index on
   Config ("Use this index") — it is stored as source 'manual'.
c. Type a target rate if you want every unpriced line to show a planned €.
   Type a fuel share (and, optionally, the yard price) on the widget.
d. Confirm a week: that locks the BAF base. From the NEXT bulletin on,
   "+ BAF" figures appear beside the quotes.

THINGS TO KNOW
--------------
- One BAF base per tenant, not per haulier or per route (Grok's lock K1/K6).
  Hauliers who quoted on different dates share one base. If that bites, the
  base becomes a route column — say so and it is a small slice.
- The feed does not say "with taxes". €1.922 can only be the taxed figure
  (ex-tax diesel is ~€1.0), so the widget's attribution says which series it
  is taken to be. If the Commission's own XLSX ever disagrees, type it over.
- The supplier's XLSX gains a "Fuel" sheet (index, bulletin date, base, share,
  BAF %) and the PDF a footer line naming the index and the formula. The yard
  price and the target rates are NOT on the supplier's sheet — they are yours.
- The widget has NOT been seen in a browser (the sandbox cannot load the CDNs;
  it renders under the harness with a populated fixture and Babel-compiles).
  First look: Look-ahead -> Commit -> the fifth KPI card; Account -> the strip
  above the KPIs; Config -> Costing · fuel.
- GET /api/lookahead never waits for the feed: it reads the stored row only
  (two extra statements per page read, asserted). The widget's own GET
  /api/fuel-index returns the stored row at once and refreshes in a thread.
- Admin endpoints are still open while ADMIN_TOKEN is unset (C11, unchanged).

FILES (17)
----------
backend/costing.py                 NEW — target rates, fuel settings, BAF formula (config key 'costing')
backend/fuel.py                    NEW — the feed, the global row, refresh in a thread, manual index
backend/db.py                      init_costing_db(): fuel_index (untenanted, by decision)
backend/main.py                    7 endpoints: GET /api/costing · PUT /api/admin/costing/target ·
                                   GET /api/fuel-index · POST /api/admin/fuel-index/refresh ·
                                   PUT /api/fuel-index/settings · PUT /api/admin/fuel-index/manual ·
                                   POST /api/admin/fuel-index/reset-base; init order; imports
backend/weeks.py                   confirm_week() locks the BAF base once (and ONLY confirm — see tests)
backend/derived.py                 rate_source / route_rates / baf_pct on context; eur_adj on days,
                                   weeks, totals; eur_target_lines; response.costing
backend/lookahead.py               account rows: rate_source, planned_eur_adj; page.costing
backend/export.py                  XLSX: "€ source", "€ + BAF" columns + a Fuel sheet; PDF: target
                                   mark, +BAF under the quote, a diesel footer line, footer wording
backend/tests/test_costing.py      NEW — 106 assertions (see below)
backend/tests/test_lookahead.py    reset_db() creates fuel_index; the sheet list gained "Fuel"
backend/tests/test_tenant_audit.py fuel_index registered as untenanted with its reason (30 -> 31)
backend/tests/parse_frontend.js    +16 (314 -> 330)
backend/tests/render_frontend.js   +16 (51 -> 67)
backend/tests/fixtures/lookahead_page_fuel.json  NEW — written by test_costing.py, read by render
backend/tests/fixtures/lookahead_page.json       rewritten by test_lookahead.py (adds costing block)
frontend/index.html                FuelWidget (×3), CostingTab, Quote + BAF on Commit/Account, copy
README.txt                         this file

SUITE — every figure watched print, on a FRESH clone with this zip applied
--------------------------------------------------------------------------
test_costing 106 · test_lookahead 242 · test_phase2 160 · test_phase25a 104 ·
test_phase3 154 · test_phase4 220 · test_phase45 144 · test_phase5a 215 ·
test_tenant_audit 31 · test_week1 317 = 1,693 py
parse_frontend 330 · parse_map 484 · render_frontend 67 · test_ipt_overlay 140 = 1,021 js
TOTAL 2,714 / 0 (was 2,575 at HEAD).

Run the .py files before the .js ones — two fixtures are written by them.

NOT TESTED (say it plainly)
---------------------------
- eurooilwatch.com from Render (the fetch is exercised against a stub; the
  parser against a payload captured on 10 Sep).
- The widget in a real browser (harness-rendered only).
- Postgres: fuel_index is CREATE TABLE IF NOT EXISTS with TEXT/REAL columns —
  the same shapes every other table uses — but no Postgres branch runs here.
- Two deliberate regressions were run and caught by the right assertions
  (mixing the target into a typed route; BAF replacing the quote). And one
  REAL bug was caught while building: the base-lock hook first landed in the
  week EDIT function (its tail is identical to confirm's) — an assertion now
  pins that editing a week never locks the base.

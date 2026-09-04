rbe-2.5b.zip — 2026-09-04
Roadmap item 2.5b (pulled forward), plus the movements/vehicles correction.

Extract over the repo root. Paths mirror the repo (backend/…, frontend/…, map/…).
Nothing has to be renamed. A zip only ADDS and REPLACES — see "Nothing to delete"
below.


================================================================================
1. WHAT YOU HAVE TO DO ON YOUR SIDE
================================================================================

a) NOTHING TO DELETE. No file is retired by this delivery.

b) THE ACCESS CODES, on Render (this answers the question you asked).
   Render → your service → Environment → Add Environment Variable, one row per
   code, then Save. The service redeploys itself.

       IPT1_CODE … IPT6_CODE   one per IPT. Sees only that IPT's forecast lines.
       PLANNER_CODE            all IPTs, can approve.
       ADMIN_CODE              planner + the admin surface.

   Pick the strings yourself; they are shared secrets typed into a browser.
   ⚠️ The moment ANY of those eight is set, the three demo codes
   (submitter123 / planner123 / admin123) stop working — everywhere, for
   everyone. Set them all in one go, or you will lock a role out.
   Leave them all unset and the demo codes keep working, which is the right
   state for a local checkout and the wrong one for the live deployment.

   My recommendation stands: set the codes now, and treat proper per-person
   login as Phase 6, the next slice. The codes are an access filter, not
   authentication; skipping them until Phase 6 leaves every line visible to
   anyone with the URL in the meantime.

c) factors.json IS NOW ONLY A SEED. Read this even if you read nothing else.
   On the first boot after this deploys, backend/factors.json is copied into a
   new `config` table row, and from then on THAT ROW is what every payload,
   density and cycle time reads. Editing backend/factors.json in the repo will
   change nothing on Render until somebody presses "Reset to file" on the new
   Config page.
   The file in this zip is UNCHANGED from what is in the repo, so there is
   nothing to merge — but if you have edited it since, edit it before you
   deploy, because after the first boot the file stops being the live copy.
   The Config page says all of this at the top and shows "differs from the file
   in the repo" when the two have drifted.

d) ADMIN_TOKEN is still optional and still unset on Render, so the Config page's
   Save and Reset are open to anyone who reaches them, exactly like every other
   admin endpoint. That is unchanged by this delivery and it is not fixed until
   Phase 5's security work. Do not describe the deployment as secure.


================================================================================
2. WHAT CHANGED
================================================================================

MOVEMENTS AND VEHICLES ARE DIFFERENT NUMBERS (your correction — I had them wrong)
  A trip is one loaded run. A vehicle makes several trips a day. So:

      movements / day  = the month's loads ÷ working days
      vehicles needed  = ceil(movements ÷ trips one vehicle can make per day)

  Trips per vehicle per day comes from that route's BAKED cycle time (laden out
  + empty back + that vehicle's own load and unload), not from an assumption. A
  route with no baked geometry for the vehicle reads "not baked" rather than
  showing a made-up number. Both figures now appear on the submission strip, in
  /api/public/month-kpis, on the map's KPI cards and on the dashboard.

2.5b, ALL FOUR PARTS

  1. ONE FORECASTS PAGE. "My submissions" and "Approvals" were the same list
     twice; they are now one page. What you see is filtered on the server by
     your access code; what you can DO differs — approve and reject only appear
     for a planner or admin. Lines are grouped per route + discipline + section
     across every year they cover, so a five-year submission is one row.
     Filters for status, IPT, discipline, "mine only" and a search box, plus a
     CSV export of whatever is on screen. Withdraw is still scoped to exactly
     the months the row shows, never the whole horizon, and a rejection still
     refuses to save without a reason.

  2. A LEFT RAIL INSTEAD OF THE TAB ROW. Seven tabs was already tight and this
     slice needed nine. Four groups:

         Plan    Dashboard · Submit forecast · Forecasts
         Track   Look-ahead
         Data    Locations · Routes · Zones · Config     (planner/admin only)
         Map     Public route map

     The page you were on is remembered between visits, the rail collapses to
     icons, and Forecasts carries a badge with the number of lines waiting for
     approval. Locations / Routes / Zones were sub-tabs inside Data Management;
     they are top-level pages now, but still ONE component underneath, so the
     map is not rebuilt when you move between them.
     Every page now uses the same header block — title, one line of purpose,
     actions on the right. No page draws its own heading any more.

  3. EDIT A ROUTE IN PLACE. A route row has an Edit button. You can change the
     origin, destination, material category and IPT without deleting and
     recreating it, so the forecasts, week rows and haul-road links on that
     route are kept. Before it writes, it fetches and shows the impact —
     how many forecast lines and weeks hang off the route, and that moving an
     end clears the cached geometry and that end's gate until it is re-baked.
     The route ID itself is not editable: baked geometry is keyed on it.

  4. A CONFIG PAGE (Data → Config). Vehicles (labels, payload, GVW, emissions,
     load/unload minutes, which are planning vehicles), materials (density,
     default unit, which vehicles carry them), the planning constants, the
     seasonal windows, and a raw JSON tab for everything the forms do not cover.
     Save sends the whole document; the server validates it and refuses with a
     list of problems rather than storing a bad one. It will not let you delete
     a vehicle that has baked route geometry, or the default routing profile,
     or set a payload that is not a positive number.


================================================================================
3. TESTS — WHAT RAN, AND WHAT THE NUMBERS MEAN
================================================================================

All green:

    backend/tests/test_week1.py        303
    backend/tests/test_phase5a.py      215
    backend/tests/test_phase4.py       202
    backend/tests/test_phase3.py       154
    backend/tests/test_phase2.py       160
    backend/tests/test_phase25a.py     104
    backend/tests/test_phase45.py      140
    backend/tests/test_tenant_audit.py  26
    backend/tests/parse_map.js         406
    backend/tests/test_ipt_overlay.js  140
    backend/tests/parse_frontend.js    259   (was 225)
    backend/tests/render_frontend.js    28   NEW
    backend/tests/browser_check.js      —    NEW, not in the default suite

TWO NEW HARNESSES, and the reason for each:

  render_frontend.js — evaluates the whole script block with the real React and
  renders every page of the rail with react-dom/server. Source-level greps
  cannot catch a page that names a component which no longer exists; this can,
  and it did (I typo'd ConfigPage while regression-testing and it failed).
  Runs anywhere: it uses only the globally installed typescript and react.

  browser_check.js — the real page in a real browser with the API stubbed. It is
  NOT in the default suite and cannot be: index.html loads React, Babel,
  Tailwind, Chart.js and Mapbox from CDNs the sandbox blocks, so the harness
  substitutes local copies and needs `npm i` first. The header of the file has
  the exact commands. This is the only harness that runs useEffect, lays
  anything out and clicks anything, and it is where I actually verified:
    - every rail page loads with data and throws nothing;
    - ⭐ ONE Mapbox instance is constructed across Locations → Routes → Zones →
      Locations (the single reason the three pages share a component);
    - the Forecasts table fits at 1440px with its actions reachable — which is
      why "submitted by" ended up folded under the discipline rather than
      keeping a column of its own;
    - the collapsed rail still separates its groups.

DELIBERATE REGRESSIONS RUN (each broke the build, then was reverted):
  rendering DataManagement in three branches instead of one; dropping the call
  that tells the rail about an internal page change; widening withdraw back to
  1..60; removing the "a rejection needs a reason" guard; renaming ConfigPage at
  one call site. Every one was caught.

⚠️ WHAT IS STILL NOT TESTED
  - Nothing has run against Postgres. SQLite locally, as always.
  - FastAPI still cannot boot in the sandbox; the endpoints are exercised
    through the stub harnesses, not through a real request.
  - No HERE call was made. Route editing clears geometry and re-bakes through
    the existing path, which is stubbed here.
  - Mapbox and Chart.js are stubs in browser_check.js, so no map, no chart and
    no geometry was actually drawn anywhere in this session.
  - The Config page's Save and Reset were never sent to a live server. The
    validation is unit-tested; the round trip is not.
  - Nothing was checked on a phone or a narrow window. The rail collapses and
    the tables scroll, but I have only looked at 1440px.


================================================================================
4. WHAT I DID NOT DO, AND WHAT IS NEXT
================================================================================

Not built, deliberately: no user table, no email, no SSO — Phase 6 owns that.
The open questions C8 / C9 / D5 / A2 are still with you and I have not guessed
at any of them.

Next slice, as agreed: Phase 6 proper login. A user table with hashed passwords
you set as admin, per-user IPT and role, and a users page — replacing the shared
codes. No email and no SSO.

RBE Alliance 1 — PHASE 3: ZONES (geofencing + disruptions, merged)
================================================================================
Extract over your repo root, then commit.
THERE ARE THREE MANUAL STEPS AT THE BOTTOM. A zip cannot delete files.

  backend/zones.py                     ->  backend/          (NEW)
  backend/db.py                        ->  backend/
  backend/network.py                   ->  backend/
  backend/main.py                      ->  backend/
  backend/tests/test_phase3.py         ->  backend/tests/    (NEW)
  backend/tests/parse_frontend.js      ->  backend/tests/    (see manual step 1)
  backend/tests/parse_map.js           ->  backend/tests/
  frontend/index.html                  ->  frontend/
  map/index.html                       ->  map/
  README.txt                           ->  repo root

factors.json is NOT included — nothing in it changed.

Tests:
    python3 backend/tests/test_phase2.py     -> 139 passed   (unchanged, no regressions)
    python3 backend/tests/test_phase3.py     -> 151 passed   (new)
    node   backend/tests/parse_frontend.js   ->  59 passed   (was 37)
    node   backend/tests/parse_map.js        ->  49 passed   (was 35)


================================================================================
READ FIRST — FOUR THINGS STILL OUTSTANDING THAT NOW MATTER MORE
================================================================================

1. YOUR POSTGRES IS STILL ON THE RENDER FREE PLAN.

   Free Postgres expires 30 days after creation, gets a 14-day grace period, then
   is DELETED. No backups, ever. Phase 3 works by re-routing through HERE and
   writing the result into that database. You are now spending API calls into
   something with a deletion date. Check the creation date of rbe-a1-db today.
   Cheapest paid tier is $6/month.

2. THE NETWORK IS NOT BAKED (as far as I know).

   Zones invalidate baked geometry and re-route it. With nothing baked there is
   nothing to invalidate and a zone will appear to do nothing at all. That is
   correct behaviour, not a bug. Run the bulk bake on Data Management -> Routes
   first, then draw a zone and watch legs get re-routed.

3. ADMIN_TOKEN IS STILL UNSET ON RENDER.

   The new zone endpoints are admin endpoints, and _check_admin() is a no-op when
   ADMIN_TOKEN is unset. Anyone who finds the URL can create a zone that invalidates
   dozens of baked legs, and the UI will then fire dozens of HERE calls to re-route
   them. This is the first admin endpoint that costs money per click by design
   rather than by accident.

     openssl rand -hex 24
     -> Render -> Environment -> ADMIN_TOKEN
     -> paste it into the Admin token box in the Data Management header

4. NAVIGATION WAS NOT RESTRUCTURED.

   Phase 2.5 wanted the nav reorganised once (Forecasts as a parent, Submit and My
   submissions beneath it). Zones went in as a third sub-tab under Data Management
   alongside Locations and Routes, so no top-level nav change was needed — but if you
   still want that restructure, it is now one more screen to move.


================================================================================
WHAT PHASE 3 IS
================================================================================

Phases 3 and 3.5 were planned as separate things: geofencing that steers HERE away
from an area, and curated disruptions with date ranges drawn on the map. You asked
for them merged, which is what the roadmap already recommended. They are one table.

A zone is a drawn area, a kind, a date range and one flag:

  AFFECTS ROUTING = ON    the area is sent to HERE as avoid[areas]. Every baked leg
                          crossing it is cleared and re-routed. Red on the maps.
  AFFECTS ROUTING = OFF   the area is drawn on both maps for information. The router
                          never hears about it. Grey on the maps.

Built separately you would have got two polygon editors, two tables, and no answer
to "this closure shuts a road — does it change my routing?". One table answers it.


================================================================================
THE ONE THING TO UNDERSTAND BEFORE YOU DRAW ANYTHING
================================================================================

HERE's avoid[areas] TAKES BOUNDING BOXES, NOT POLYGONS.

Whatever shape you draw is sent to the router as the rectangle that contains it.
Draw a long diagonal closure and the router avoids a large rectangle you did not
intend. Draw an L and it avoids the notch too.

This is a HERE limitation, not a shortcut taken here. The full polygon IS stored and
IS what both maps draw — only the router gets the box. So the map and the routing
deliberately disagree, and the Zones panel prints the exact bbox string it will send
so the difference is visible before you save, not after.

WHERE THAT MATTERS, DRAW SEVERAL SMALL ZONES INSTEAD OF ONE LARGE ONE. Each one
contributes its own box and the union approximates the real shape far better.


================================================================================
HOW A ZONE CHANGE DECIDES WHAT TO RE-ROUTE
================================================================================

You chose automatic re-baking. A zone save clears the affected geometry and the UI
immediately re-routes it. Three tests decide what is affected, and only two of them
are exact:

  EXACT   Routes whose baked line crosses the new shape. Real segment-level
          intersection against the drawn polygon, not a bbox test.

  EXACT   Legs that were baked WHILE this zone was being avoided. Phase 3 adds a
          zones_applied column to route_geometry recording which zone ids were sent
          to HERE for that bake. So "this leg detoured around Z003" is a record, not
          a guess, and moving or deleting Z003 re-routes exactly those legs.

  APPROXIMATE  Legs baked BEFORE this release, which carry no such record. For those
          — and only those — a zone edit or delete falls back to "does this line pass
          within 3 km of where the zone was". That over-invalidates (nearby routes get
          re-baked for nothing, costing HERE calls) and under-invalidates for any
          detour that swung wider than 3 km.

The UI reports how many of the legs it re-routed were matched approximately. Once the
network has been re-baked under this release that number goes to zero and stays there.
The 3 km figure is my judgement, not a measured value — it is DETOUR_PAD_KM at the top
of backend/zones.py if you want it different.


================================================================================
WHAT YOU CAN DO NOW
================================================================================

Data Management -> Zones

  - "+ Draw zone", then click the map to place corners. Three or more makes a shape.
  - Name it, pick a kind, set From/To dates (blank = open-ended, both ends inclusive).
  - Tick or untick "Affects routing".
  - Save. If it affects routing, the invalidated legs are cleared and re-routed
    immediately, with a running count.
  - Click any row to edit it. Editing a shape means redrawing it — corners cannot be
    dragged individually (see LIMITATIONS).
  - "Active" can be unticked to retire a zone without deleting it. Prefer that to
    deleting: a deleted zone takes with it the record of why a past bake went the way
    it did.
  - Delete asks first and tells you how many HERE calls it is about to spend.

Public map (/map/)

  - "Disruption zones" toggle under Overlays. Red = avoided by routing, grey = advisory.
  - Click a zone for its name, kind, dates, note and whether routing avoids it.
  - With the forecast timeline open the overlay FOLLOWS THE MONTH ON SCREEN. A closure
    that ends in March is gone by April. Close the timeline and every active zone comes
    back.

New endpoints

  GET    /api/zones                       public — the maps read this, no token
  GET    /api/zones/{id}
  POST   /api/admin/zones
  PUT    /api/admin/zones/{id}
  DELETE /api/admin/zones/{id}
  GET    /api/admin/zones/{id}/impact     dry run: what a change would cost
  GET    /api/admin/diagnostics/zones     what is sent to HERE (probe=true to ask HERE)

A zone write does NOT call HERE inline. It clears geometry and reports what needs
re-routing; the browser then drives /api/admin/bake-routes in batches, the same loop
the bulk-bake button uses. Doing the HERE calls inside the write request would block
one HTTP call on dozens of round trips and time out on Render long before finishing.


================================================================================
SCHEMA CHANGES
================================================================================

NEW TABLE  zones
  id, name, kind, geometry (GeoJSON text), affects_routing, starts_on, ends_on,
  note, active, created_at, updated_at

NEW COLUMN  route_geometry.zones_applied  TEXT NULL
  Comma-separated zone ids avoided when this leg was baked.
    ''    = baked with no zones in force
    NULL  = baked before Phase 3, provenance unknown
  Plain ADD COLUMN on both backends — no primary-key change, so SQLite needs no
  rebuild. Runs on startup and is idempotent.

Nothing existing was altered. The Phase 2 forecasts key and the widened
route_geometry key are untouched, and test_phase2.py still passes 139/139 to prove it.


================================================================================
WHAT IS NOT TESTED
================================================================================

Same gaps as before, plus new ones specific to this work. Do not read 398 passing
assertions as more coverage than this.

  * HERE IS NEVER CALLED ANYWHERE IN THESE TESTS. Every assertion about avoid[areas]
    is about the string this code builds, not what HERE does with it. Specifically
    UNKNOWN and unknowable in the sandbox:
      - how many avoid[areas] boxes HERE will accept. No limit is enforced here,
        deliberately — inventing one would silently drop zones you drew.
      - what HERE returns when a box encloses a route's own origin or destination.
      - whether HERE honours avoid[areas] the way we assume for truck profiles.
    Run  /api/admin/diagnostics/zones?route_id=R001&probe=true  against the live
    deployment. It sends the same route with and without the zones and hands back both
    raw responses, so any difference is attributable. That costs two HERE calls.

  * The HTTP layer is still stubbed. Endpoint bodies run; routing, status codes,
    query-parameter coercion and serialisation do not.

  * Every Postgres branch of db.py is still unexercised, including the new zones DDL
    and the zones_applied migration. They run against SQLite only.

  * Nothing in a browser. Zone drawing, the map overlay, the popup, the timeline filter
    and the automatic re-bake loop are asserted at source level only. Nobody has watched
    a polygon get drawn.

  * The automatic re-bake loop has never run against a real bake. It reuses the
    bulk-bake batching that does work, but the specific sequence
    "save zone -> geometry cleared -> loop until remaining is 0" is untested end to end.
    test_phase3.py does run the full bake chain with HERE replaced by a recorder, which
    proves the avoid list reaches the call and the zone tag is stamped on every stored
    row — but that is the backend half, not the browser loop.


================================================================================
LIMITATIONS I CHOSE, SO YOU CAN OVERRULE THEM
================================================================================

  * NO VERTEX DRAGGING. Drawing is hand-rolled: clicks place corners, Save closes the
    ring, changing a shape means redrawing it. That keeps the "one Mapbox instance, no
    build step, no extra CDN dependency" architecture intact. mapbox-gl-draw is the
    upgrade if nudging single corners turns out to matter, and it drops into the same
    place. Say the word.

  * ZONES ARE POLYGONS ONLY IN THE UI. The backend accepts LineString zones (a closed
    road drawn as a line) and the intersection maths handles them, but nothing in the UI
    draws one yet. That path is where Phase 4 haul roads plug in — the kind 'haul_road'
    is already in the vocabulary and does nothing so far.

  * ZONES DO NOT AFFECT FORECASTS. A closure changes route geometry, which changes
    distance and cycle time, which changes trucks-needed. All of that flows through once
    the legs are re-baked. But nothing warns you that an approved forecast now assumes a
    route that got 12 km longer. If you want that, it is real work and needs its own pass.

  * DELETING A ZONE DOES NOT RESTORE THE OLD ROUTE. It clears the affected geometry and
    re-routes from scratch. HERE may or may not return exactly what it did before.

  * NO TENANT KEY. You chose to defer multi-tenancy, so zones has no tenant column. That
    was the cheap moment to add one; retrofitting later means a key on every table and a
    filter on every query.


================================================================================
MANUAL STEPS — A ZIP CANNOT DELETE FILES
================================================================================

1. DELETE  parse_frontend.js  FROM THE REPO ROOT.

   It is still sitting there. The 2026-08-22 cleanup zip asked for this and it was
   never done, so backend/tests/parse_frontend.js did not exist at HEAD at all — this
   zip creates it. The harness resolves paths as __dirname/../.. and only works from
   backend/tests/. After extracting you will briefly have both copies; delete the root
   one.

2. DELETE  test_phase2.py  FROM THE REPO ROOT.

   Same story. The real one is backend/tests/test_phase2.py and is untouched here.

3. RENAME  download  TO  .gitignore  (still not done).

   Its contents are a correct .gitignore (__pycache__/, *.pyc, *.db ...) but git has
   never read it under that name, which is why .pyc files got committed once.

Also: two files that claude/code-snapshot.md records as DELETED are still at HEAD. I
have corrected the note rather than the repo:

     backend/routes.json                          nothing reads it, safe to delete
     backend/seed_data/wp3_timeline_forecast.csv  input to the retired seeder, safe to delete

Neither breaks anything by staying. Your call.


================================================================================
GIT
================================================================================
Committed locally in the session so the diff and message exist. THE PUSH FAILED, as
always — the sandbox git proxy blocks it. Nothing has reached GitHub. Extract this zip
and commit through the web UI as usual, and check the PATHS after uploading, not just
that the files arrived: dragging loose files puts them at the repo root, which is how
manual steps 1 and 2 came to exist in the first place.

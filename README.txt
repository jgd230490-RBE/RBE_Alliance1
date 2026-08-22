RBE Alliance 1 — Phase 2, step 1
Discipline taxonomy · widened forecasts key · /api/meta off the live network
================================================================================
Extract over your repo root (structure matches), then commit.
factors.json is NOT included — nothing in it changed, so there is nothing to merge.

Tests included under backend/tests/. Run them before you deploy:

    python3 backend/tests/test_phase2.py      -> 109 passed
    node   backend/tests/parse_frontend.js    ->  37 passed


READ THIS FIRST — one destructive change and one thing to check
================================================================================

1. YOUR FORECASTS TABLE IS REBUILT ON FIRST BOOT.

   db.init_db() detects the pre-Phase-2 table and replaces it. Legacy rows are NOT
   translated — the clean rebuild decision. They are also NOT destroyed: the old
   table is renamed to forecasts_legacy and left in the database, invisible to every
   endpoint. Startup prints how many rows moved.

   Drop it yourself when you are satisfied:   DROP TABLE forecasts_legacy;

   I kept the rename rather than dropping because "gone from the app" and "gone from
   the disk" are different promises and only one of them is reversible. If you would
   rather it were a real DROP, say so and I will change it.

2. CHECK YOUR DASHBOARD BEFORE AND AFTER.

   Before this change, every distance figure on the dashboard was ZERO. Not wrong —
   zero. Dist km 0.0, Truck-km 0, tCO2e 0.0, Intensity 0.0, and Cycle h a flat 0.33
   for every route.

   Cause: /api/meta read backend/seed_data/routes.json, and that file has no
   distance_km key on any of its 69 rows. (backend/routes.json, the unused copy
   beside it, does have real distances — 9.0 to 185.9 km. It is not the one being
   read.) The frontend then did `route.distance_km || 0`.

   Worth confirming on the live deployment before you deploy this, so you can see
   the fix land rather than take my word for it.


WHAT CHANGED
================================================================================

SCHEMA (backend/db.py)

  forecasts   UNIQUE (route_id, month_index, discipline, section_id)

              discipline and section_id are NOT NULL DEFAULT '', not nullable.
              This matters: NULLs compare distinct inside a UNIQUE constraint in
              both Postgres and SQLite, so a nullable column leaves the row
              unconstrained, ON CONFLICT never fires, and re-saving a matrix row
              inserts a duplicate instead of updating it. '' is the unassigned
              sentinel.

              vehicle_type_2 and split_pct are gone — see below.

  new tables  disciplines, discipline_materials, ipts, design_sections,
              work_sections.

              work_sections is CREATED EMPTY and deliberately not seeded — see
              "What is still blocked" below.

              There is no design_section_disciplines table and there must not be:
              "superstructure only" is a mainline rule, not a design-section rule,
              and seeding it would fire false out-of-scope warnings on A1's own DS2
              facility sections on day one.

  locations   gains default_section_id (left NULL), vendor, detail.


ONE VEHICLE PER FORECAST LINE

  vehicle_type_2 and split_pct are retired. A haul split across two vehicle types is
  now two forecast lines, which the widened key makes storable.

  This is not just simplification. The blended payload averaged two haul cycles that
  are nowhere near each other — an Artic Flatbed turns round in 45 minutes against an
  Artic Tipper's 24 — and hid that inside one number. Two lines keep each cycle
  honest, and they sum correctly.

  conversions.effective_payload() is kept but marked deprecated so an older caller
  does not crash.


/api/meta NOW COMES FROM THE LIVE NETWORK (network.meta_routes)

  Three things the raw routes table could not give the UI, all handled server-side:

    names       the table stores origin_id/dest_id — joined from locations
    distance    the table has no distance — taken from cached geometry, leg
                'loaded', alt_index 0, per your decision that a forecast's distance
                follows the primary route. Which vehicle's geometry was used is
                reported in distance_profile, because different vehicles legitimately
                route differently and an unlabelled number would hide that.
    duplicates  C01 and C02 are BOTH named 'Parnu terminal'. The matrix builds its
                dropdowns from names, so without this those two collapse into one
                entry a submitter cannot tell apart. A name shared by more than one
                location now gets its id appended: "Parnu terminal (C01)".

  An unbaked route reports distance_km = null, NOT 0. Zero is a real distance and
  would sum silently into every total.


CYCLE TIME — THE TWO MODELS ARE NOW ONE

  The dashboard computed its own: round trip over a flat 45 km/h, plus one global
  12+8 minute turnaround for every vehicle. The route analysis panel used HERE's real
  routed durations and each vehicle's own turnaround. Same route, two answers.

  The dashboard now calls /api/routes/analysis-batch — one request for every route,
  not one per row — and uses the backend figures.

  Size of the disagreement it removes:
    turnaround   Rigid 7.5t -35%, 4-wheeler -20%, 6-wheeler -10%, 8-wheeler 0%,
                 Artic Tipper +20%, Artic Flatbed +125% (45 min actual vs 20 assumed)
    speed        on a 50 km haul where HERE's effective speed is 65 km/h, the old
                 formula overstated the cycle by 32% and read 3 trips/day where the
                 backend reads 5

  NEW COLUMN: Trucks. The old screen showed only demand-driven trips (tonnage /
  payload); the backend produces capacity-driven trips (shift hours / cycle). Fleet
  size needs both — trucks = peak demand per day / capacity per truck per day.
  Converging the cycle models alone would not have given you this.

  Routes with no baked geometry now read "not baked" rather than a number. You chose
  the backend model with no flat-speed fallback, so that is the honest display.


DOUBLE-COUNTING CAUTION

  Saving a line returns a `caution` when another discipline already forecasts on that
  route. Not an error — two disciplines on one route is the case the taxonomy exists
  for — but two people filling the matrix independently can count the same lorries
  twice without ever seeing each other's line. The matrix also lists existing lines on
  the route before you save.


DISCIPLINE AND SECTION PICKERS

  On the forecast line, not the route or the location. The section picker shows
  "Not configured yet" until work_sections is seeded.

  A discipline/material mismatch shows an advisory note, not a block. The
  discipline-to-category join is many-to-many and deliberately permissive.


PER-LINE EVERYWHERE ELSE

  Ledger, approvals, my-submissions, public matrix and the dashboard route table all
  group per (route, discipline, section). Approve and withdraw take optional
  discipline/section_id parameters and the UI sends them — without that, withdrawing
  a substructure forecast would have taken a superstructure one with it. The same
  class of bug as the zero-cell delete.


LEGACY FORECAST SEEDING REMOVED

  seed.seed_if_empty() fired whenever the forecasts table was empty and re-inserted
  the 69 legacy rows. Phase 2 empties that table on purpose. Left in place it would
  have silently undone the rebuild on your next Render restart. seed.py is now a
  no-op shell; the seed data files are untouched on disk.


NODE METADATA SALVAGED FROM a1_data.js

  backend/seed_data/node_meta.json — vendor and material detail for 13 network
  locations, applied on boot, additive (never overwrites an edit you made).

  The WORK SECTION tags in the same file were deliberately NOT salvaged, against the
  plan in phase2-decisions.md. Two measured reasons:

    - matching those 16 nodes to network locations by name recovers 2 of 16. The
      network merged and renamed the rest.
    - the merged pairs DISAGREE. Tootsi Station (EW) is WS1 and Tootsi Station (TM)
      is WS7, and both fold to one network location (C07). Kivisilla viadukt is WS2
      and WS7. locations.default_section_id is a single column, so seeding from these
      would have been a coin flip.

  That is your own overlap principle showing up in the data. default_section_id is
  left NULL; it is a pre-fill, and a wrong pre-fill is worse than none.

  One vendor did not match (Parnu Construction Base / OU Enefit Industry). It is kept
  in node_meta.json under "unmatched" rather than dropped.


WHAT I DID NOT DO, AND WHY
================================================================================

THE PUBLIC MAP IS NOT MIGRATED YET, AND a1_data.js IS STILL IN THE REPO.

  Deleting it now would break the map immediately — map/index.html loads it at
  line 12 and reads it in six more places, for routes AND for its 32 node markers.
  The migration to /api/routes/geojson is the next slice; a1_data.js comes out in the
  same change, not before.

  Consequence you should expect meanwhile: after this deploys, the public map paints
  nothing. It joins /api/public/route-forecasts to a1_data.js on legacy route ids, and
  there are no forecasts at all until you re-author them. It is not erroring, it has
  nothing to draw. That was the accepted cost of the clean rebuild.

WORK SECTIONS ARE NOT SEEDED.

  Blocked on you checking Appendix E. Three questions, all from scope-diagram.md:

    1. Are WS 10/11 and WS 14 & 15 one row each or two? The scope document says
       "fifteen work sections", which implies two, and the only prose source splits
       WS 14 (Temporary Works) from WS 15 ("Superstructure ONLY: rails, sleepers,
       ballast, fencing, earthing"). A single row mis-files one of them.
    2. Which IPT owns WS 14 & 15? No band is drawn beneath them.
    3. Is WS 6 genuinely design-only? The only source is ipt-matrix.md, which is
       unverified AI output and already got WS 13's IPT wrong.

  Until these are settled the table stays empty and the section picker says so.

THE SECTION-TO-DISCIPLINE MAPPING IS NOT SEEDED EITHER.

  Same reason. It is still my inference, and phase2-prep.md flags three caveats on it.

disciplines.sort_order IS MINE.

  "In programme order" was a requirement with no values behind it. I seeded a
  construction-sequence reading — utilities, temporary works, earthworks,
  substructure, structures, superstructure, stations — with gaps of ten so rows can be
  slotted between without renumbering. It is a judgement, not a sourced fact. Check it
  before anyone reports off it.

ipts.manager IS NULL AND STAYS NULL. IPTs ARE SEEDED PRE-MERGE.

  Per your decision: store the original IPT, no merge modelling. The consequence you
  accepted is that the UI will show work sections owned by IPT 2, which no longer
  exists as a team. Adding ipts.merged_into later is additive and needs no migration.

CHAINAGE VALUES ARE NULL.

  The scope diagram carries two chainage notations that do not reconcile, and stated
  lengths that do not match their own spans.


WHAT IS NOT TESTED
================================================================================

  - THE HTTP LAYER. FastAPI cannot be installed in my sandbox, so the test harness
    stubs it and calls the endpoint functions directly as plain Python. The real
    SQL and logic run; routing, status codes, query-parameter coercion and response
    serialisation do not. Those can only be checked in the deployment.

  - POSTGRES. Everything ran against SQLite. Every Postgres branch in db.py is a
    sibling of the SQLite one in the same function, but "the SQLite path passes" is
    not evidence about Postgres, and Postgres is what Render runs. The forecasts
    rebuild in particular takes a different path on each.

  - ANYTHING NEEDING A BROWSER. The frontend checks parse the JSX with the TypeScript
    compiler and then assert at source level (useEffect does not run under
    renderToStaticMarkup). Mapbox, Chart.js rendering and actual fetch behaviour are
    unverified.

  - HERE. No live calls, as always.

  A note on the source-level assertions: they strip comments first. Without that they
  matched the comments explaining why a thing was removed and passed for the wrong
  reason — which is exactly what happened on the first run, twice.


STILL OUTSTANDING FROM BEFORE
================================================================================

  SET ADMIN_TOKEN ON RENDER. _check_admin() is `if admin_token and token !=
  admin_token: raise 403`. Unset, every admin endpoint is open — create and delete
  locations, clear-routes, clear-geometry. openssl rand -hex 24, set it in Render,
  paste it into the admin token field in the Data Management header.

  BAKING. You said you would re-bake manually through the Data Management page. The
  bulk endpoint /api/admin/bake-routes still exists and only its UI was removed —
  it takes a profile, batches by leg, and you call it until "remaining" is 0. Manual
  is 107 clicks per vehicle profile, and the per-route Bake button only uses the
  "Show" selector's vehicle on a route that has no geometry yet, so a cold sweep
  silently bakes one profile unless you change the selector between clicks. Say the
  word and I will put the bulk-bake UI back.

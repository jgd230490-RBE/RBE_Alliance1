RBE Alliance 1 — Phase 2, step 2
Public map onto the routing network · bulk-bake UI · a1_data.js deleted
================================================================================
Extract over your repo root, then commit.

⚠️ THIS DELIVERY DELETES A FILE. See "The one deletion" below — you have to remove
   map/data/a1_data.js on GitHub yourself, because extracting a zip cannot delete.

factors.json is NOT included — nothing in it changed.

Tests:
    python3 backend/tests/test_phase2.py      -> 128 passed
    node   backend/tests/parse_frontend.js    ->  37 passed
    node   backend/tests/parse_map.js         ->  35 passed


WHERE EACH FILE GOES
================================================================================

  README.txt                        ->  repo root
  main.py, network.py               ->  backend/
  tests/parse_map.js                ->  backend/tests/
  frontend/index.html               ->  frontend/
  map/index.html                    ->  map/

  Five files. Everything else from step 1 is unchanged and does not need re-uploading.

  THEN DELETE, on github.com:        map/data/a1_data.js


THE ONE DELETION
================================================================================

map/data/a1_data.js — 3.95 MB — is now dead and must be removed.

It was the public map's route geometry: 364 line segments and 32 node markers keyed
to the 69 LEGACY route ids. Measured overlap with the routing network was zero, so
once forecasts are re-authored against network routes it could never have matched
anything again.

To delete it on GitHub: open map/data/a1_data.js, click the ⋯ menu top right,
choose Delete file, commit.

Do this AFTER uploading the new map/index.html, not before — the old map/index.html
loads that file at line 12 and would break the moment it disappears. The new one
does not reference it at all.

DO NOT delete map/data/alignment.js or map/data/chainage.js. Those are surveyed
reference geometry, nothing to do with haul routes, and both still load statically.

Nothing is lost. The vendor and material detail that lived only in that file was
salvaged into backend/seed_data/node_meta.json in step 1 and is already applied to
your locations — the deploy log confirmed "Applied salvaged vendor/detail to 13
location(s)."


THE PUBLIC MAP NOW READS THE NETWORK
================================================================================

New endpoint /api/public/map-data returns route lines and location markers built
from the live routing network.

WHICH VEHICLE IT DRAWS. You chose "whichever is baked, labelled". The backend uses
the default profile where that route has geometry, falls back to any profile that
does, and names the vehicle on every feature with a profile_is_fallback flag. A map
that quietly mixed vehicles without saying so would be misleading — different
vehicles genuinely route differently, which is the whole reason the network bakes
per profile.

BOTH DIRECTIONS are drawn. The map's layer ids and toggles predate the network's
loaded/return vocabulary, so they are mapped rather than renamed: 'Inbound' is the
laden leg out, 'Outbound' the empty leg back. The sidebar labels now say so.

UNBAKED ROUTES ARE OMITTED, not drawn as a straight line. On the Data Management map
a dashed straight line usefully means "HERE could not route this pair". On a public
map it would just look like a road that isn't there.


THREE THINGS THAT CHANGED BECAUSE THE NETWORK HAS NO EQUIVALENT
================================================================================

1. TEMPORARY HAUL LAYER — REMOVED.

   The legacy file carried 97 such segments across 47 routes. The network holds only
   a scalar origin_temp_km per route and no geometry at all. Drawing a straight line
   of that length would be a fabricated shape on a map where every other line is
   surveyed road.

   The toggle is still in the sidebar, disabled and labelled "(Phase 4)", so its
   absence reads as a known gap rather than something that broke.

   You said this matters for the future and should be available when editing a route.
   Noted in claude/roadmap.md against Phase 4 — that phase is already scoped to draw a
   polyline and assign a speed, which is the right shape for it.

2. 'LEG' FILTER -> 'DISCIPLINE' FILTER.

   The legacy values (Primary/Direct, Hub Distribution, Primary Freight Hub) have no
   network equivalent. Discipline replaces them.

   Note it filters with a MEMBERSHIP test, not an equality — a route can carry several
   disciplines at once, which is the entire point of the widened forecast key.

   It is populated from APPROVED forecasts, so until you re-author some it will show
   "No approved forecasts yet" and disable itself rather than sit there looking broken.

3. 'CAPACITY' KPI -> TRIPS/DAY FROM THE NETWORK.

   It used to sum max_loads: a hand-entered integer 1–6 per route from the legacy file,
   provenance unconfirmed, no network equivalent. It now sums what the baked geometry
   actually supports — shift hours divided by the real HERE cycle time, the same figure
   the forecasting dashboard uses.

   It reads 0 until you bake. That is honest rather than broken.


A BUG FOUND WHILE MIGRATING
================================================================================

The white "casing" layers under each route filtered on ['get', 'route_leg'] — a
property that does not exist anywhere in the data. Not in the legacy file, not in the
network. Those layers therefore matched nothing and never drew; only the core lines
appeared, and only after applyFilters() overwrote their filters with a different
property name.

Fixed — they filter on `type` like everything else, so the halos work now. Pre-existing,
nothing to do with Phase 2, but worth knowing it was never working rather than assuming
the map looks different because of this change.


BULK BAKE IS BACK
================================================================================

Data Management -> Routes tab -> "Bake all · N vehicle(s)", next to Clear all routes.

/api/admin/bake-routes never went away; only its UI did. That was fine while the
network was already baked. A cold network is 107 routes x 2 legs per vehicle, and the
per-route Bake button only uses the "Show" selector's vehicle on a route that has no
geometry yet — so a manual sweep was 107 clicks AND silently did one profile.

  WHICH VEHICLES: the ones ticked on the "Create route" panel on the same tab. One
  vehicle list per tab rather than two that can disagree. Nothing ticked falls back to
  whatever "Show" is set to.

  BOTH LEGS or LADEN ONLY: the dropdown beside the button. Both legs is what you want —
  laden only halves the HERE calls but leaves every cycle time estimated rather than
  measured.

  It batches and loops on the endpoint's own "remaining" count, reporting progress,
  rather than one request that would time out. Interrupt it and already-baked routes
  are kept, so re-running picks up where it stopped.

  ⚠️ Re-baking a route resets a promoted alternative to HERE's own ranking. If you have
  promoted anything by hand, it goes. The confirmation dialog says so.

  ⚠️ It calls HERE once per route per leg per vehicle. Two profiles, both legs, 107
  routes = 428 calls. Check your HERE quota before running it wide.

Bake before you judge the public map — with nothing baked it correctly draws nothing,
and tells you so.


ALSO IN THIS DELIVERY
================================================================================

/api/forecasts now takes an optional route_id. Your deploy log showed nine identical
GET /api/forecasts in a row: the submission matrix reloads its twelve month-cells
whenever the route, year, discipline OR section changes, and step 1 took that from two
triggers to four while it was still pulling the entire forecasts table each time. It
now asks for one route.


WHAT IS STILL NOT DONE
================================================================================

WORK SECTIONS ARE STILL NOT SEEDED. Blocked on your Appendix E check. Three questions:

  1. Are WS 10/11 and WS 14 & 15 one row each or two? The scope document says "fifteen
     work sections", implying two, and the only prose source splits WS 14 (Temporary
     Works) from WS 15 ("Superstructure ONLY: rails, sleepers, ballast, fencing,
     earthing"). One row mis-files one of them.
  2. Which IPT owns WS 14 & 15? No band is drawn beneath them in the diagram.
  3. Is WS 6 genuinely design-only? The only source is claude/ipt-matrix.md, unverified
     AI output that already got WS 13's IPT wrong.

The section picker in the matrix says "Not configured yet" until these are answered.

THE SECTION -> DISCIPLINE MAPPING is still Claude's inference and still unseeded.

disciplines.sort_order is still my judgement, not a sourced fact.

RE-AUTHORING FORECASTS is yours. Nothing on the dashboard or the public map's
discipline filter has anything to show until some exist.


WHAT IS NOT TESTED
================================================================================

  - THE HTTP LAYER. FastAPI is not installable in my sandbox, so the harness stubs it
    and calls endpoint functions directly. Real SQL and logic run; routing, status
    codes and serialisation do not.
  - POSTGRES. Everything ran against SQLite.
  - ANYTHING NEEDING A BROWSER. The map assertions extract its inline scripts, check
    they are valid JavaScript, and then assert at source level. Whether Mapbox actually
    renders the layers, whether the fetch succeeds against your deployment, and how it
    looks are all unverified. The map is the part of this delivery you should eyeball
    yourself.
  - HERE. No live calls, as always. The bulk-bake loop has never been run against the
    real endpoint — only its shape is verified.


STILL OUTSTANDING
================================================================================

  SET ADMIN_TOKEN ON RENDER. Unset, every admin endpoint is open — including the
  bulk-bake button this delivery just added, and clear-routes next to it.
  openssl rand -hex 24, set it in Render, paste it into the admin token field in the
  Data Management header.

  DROP TABLE forecasts_legacy; when you are satisfied the 90 rows it holds are not
  wanted.

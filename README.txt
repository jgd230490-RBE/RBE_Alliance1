RBE Alliance 1 — Phase 2, quick fixes from the improvement review
================================================================================
Extract over your repo root, then commit. No deletions, no renames this time.
factors.json is NOT included — nothing in it changed.

  backend/main.py                  ->  backend/
  backend/network.py               ->  backend/
  backend/tests/test_phase2.py     ->  backend/tests/
  frontend/index.html              ->  frontend/
  map/index.html                   ->  map/
  README.txt                       ->  repo root

Tests:
    python3 backend/tests/test_phase2.py     -> 139 passed
    node   backend/tests/parse_frontend.js   ->  37 passed
    node   backend/tests/parse_map.js        ->  35 passed


⚠️ READ THE DATABASE WARNING FIRST
================================================================================
Your Render Postgres is on the free plan. Free Postgres EXPIRES 30 DAYS AFTER
CREATION, gets a 14-day grace period, then is DELETED — and free Postgres has no
backups at all. Everything is in there: baked geometry, edited locations, the
salvaged operator data, forecasts, and forecasts_legacy.

Check the creation date of rbe-a1-db in the Render dashboard today. Cheapest paid
Postgres is $6/month and it is the difference between having backups and not.

Do this before you spend an afternoon baking 428 HERE calls into it.


WHAT IS FIXED
================================================================================

1. ARTIC FLATBED CAN NO LONGER BE PICKED FOR AGGREGATES.

   factors.json has carried a `vehicles` list per material category all along.
   It was only ever used to choose a DEFAULT — the dropdown still offered every
   vehicle, so nothing stopped a flatbed being selected for ballast.

   Vehicles outside the category's list are now shown greyed and disabled,
   labelled "not used for this material", rather than hidden. If a saved row
   already has a wrong vehicle you can still see it, instead of it being
   silently swapped for something else behind your back.

2. A REJECTION NOW REQUIRES A REASON, AND THE REASON IS ACTUALLY SHOWN.

   This was worse than you described. The reason was optional AND it was never
   displayed anywhere — not in Approvals, not in My submissions. It was written
   to the database and read by nothing. A submitter saw "Rejected" and had no
   way to find out why.

   Rejecting now refuses to proceed without a reason, and the text appears under
   the status badge in both Approvals and My submissions.

   Approve and reject are also now scoped to the forecast LINE rather than the
   whole route — approving a substructure forecast was approving a
   superstructure one on the same route as a side effect.

3. THE MAP KEY NOW MATCHES THE MAP.

   You said green/orange was fine but the key didn't reflect it. The cause: the
   base layers were navy and purple, and the forecast overlay REPAINTED them to
   green and amber the moment it was switched on, while the sidebar key kept
   showing navy and purple. Two colour schemes, one key, wrong half the time.

   Green and amber are now the base scheme, the key matches, and the overlay's
   repaint no longer changes anything — it only restores opacity after a
   highlight.

4. DIRECTIONAL ARROWS, from zoom 12 in. Offset above the laden line and below
   the empty one, coloured to match, so the two directions read as opposite
   flows rather than two parallel lines.

5. PREV / NEXT MONTH BUTTONS on the timeline, either side of play. Scrubbing a
   60-wide slider by hand cannot reliably land on one month.

6. CSV EXPORT from My submissions. Built in the browser, no server round trip.
   Includes the rejection reason column.

7. AVG AND PEAK PER WORKING DAY in the Submit Forecast totals box, in both
   vehicles and tonnes. Averaged over the months you actually entered, not all
   twelve. The peak is the number that sizes a fleet and a site gate; an annual
   total hides it completely.

8. "Forecast Matrix" is now "SUBMIT FORECAST" throughout.

9. OPERATOR / COMPANY AND DETAIL are editable on the Locations form.

   Half of this already existed and you may not have noticed — the columns
   arrived with the a1_data.js salvage in step 1, which is where the quarry
   operators came from. They were being stored and displayed but not editable.
   Now they are, and they survive a node drag (that PUT is a full replace, so
   without care a nudge would have wiped an operator name).


WHAT I DID NOT DO, AND WHY
================================================================================

Everything else from your list is triaged in claude/roadmap.md under "Phase 2.5
— the improvement backlog". Short version of the ones with a catch:

CONFIG PAGES (discipline matrix, vehicle matrix, etc) — bigger than it reads.
A vehicle matrix means editing factors.json through the UI, and that file is the
single source of truth AS A FILE, read from disk per request. Making it editable
needs either a config table in the database or writing to disk — and Render free
has no persistent storage. Storage decision first, UI second.

MERGE MY SUBMISSIONS + APPROVALS with role scoping — happy to build it, but be
clear what it is. LOGINS is a client-side dict with plaintext passwords visible
in view-source. "Submitters see only their own" built on that is a UI
convention, not a permission boundary. It stops honest people making mistakes;
it stops nobody else. Real access control is Phase 5.

RETURN-LEG ALTERNATIVES — you are right and it is a genuine gap.
route_analysis() iterates the LOADED leg's alternatives and pairs each with the
matching return, so any return alternative beyond that pairing is never listed
and cannot be promoted. promote_alternative() already accepts a `leg` argument,
so the data and the backend are there; only the UI is missing.

STREET VIEW — Mapbox does not have one. That is Google Street View: separate
API, separate key, separate billing. Technically easy either as a link or an
embedded panorama. It is a commercial decision, not a technical one.

WEIGHT / HEIGHT RESTRICTION LAYER — two different things. HERE already returns
per-route section notices for low bridges and weight limits, and probe() already
collects them; surfacing those per route is real work and worth doing. A LAYER
showing all restrictions across the map needs a restrictions dataset, which HERE
Routing does not provide.

AI UPLOAD / AUTOFILL — feasible, but the scope depends entirely on what actually
arrives. A parser for a known spreadsheet layout is about a week. "Understand
any document someone emails us" is a different product. Send me three real
example files before I design anything.


⚠️ ONE REQUEST I CANNOT DELIVER AS WRITTEN
================================================================================

"Haul routes need more accuracy to within 1m on the correct size of the road."

Two separate things, and the first is already true:

  - COORDINATE PRECISION IS NOT THE PROBLEM. here_routing.decode_polyline()
    rounds to 6 decimal places — about 0.11 m at this latitude. The geometry is
    already sub-metre.

  - WHAT YOU ARE SEEING IS A DELIBERATE OFFSET. map/index.html draws each
    direction with a line-offset of ±1 to ±4 pixels so laden-out and empty-back
    do not sit exactly on top of each other. Remove it and the two directions
    collapse into one line.

  - "THE CORRECT SIZE OF THE ROAD" IS NOT SOMETHING HERE RETURNS. Routing v8
    gives the road CENTRELINE. Road width, lane count and lane position need HD
    map data — HERE HD Live Map or equivalent. Different product, different
    licensing, and a rendering problem rather than a routing one.

WHAT I CAN DO, if you want it: reduce or drop the offset at high zoom so lines
sit on the centreline when you are close in, and scale line width with zoom so
it stops looking like a fat ribbon over a narrow road. Both are styling changes,
both small. Say the word.


WHAT IS NOT TESTED
================================================================================
  - The HTTP layer. FastAPI is stubbed; endpoint functions are called directly.
  - Postgres. Everything ran against SQLite.
  - Anything needing a browser — including whether the new arrows render, and
    whether the CSV download works in your browser. Please check both.


STILL OUTSTANDING
================================================================================
  - Check the database plan. Top of this file.
  - Set ADMIN_TOKEN on Render. Unset, every admin endpoint is open — including
    the bulk-bake button, which can burn your HERE quota.
  - Answer the three Appendix E questions so work_sections can be seeded.
  - Bake the network. Nothing downstream shows anything until you do.
  - DROP TABLE forecasts_legacy; when you are satisfied.

RBE Alliance 1 — PHASE 2.5a
Road restrictions (Tark Tee) · Street View · map polish
================================================================================
Extract over your repo root, then commit.
NO DELETIONS. Nothing needs removing by hand.

  backend/projection.py                ->  backend/          (NEW)
  backend/restrictions.py              ->  backend/          (NEW)
  backend/streetview.py                ->  backend/          (NEW)
  backend/main.py                      ->  backend/
  backend/tests/test_phase25a.py       ->  backend/tests/    (NEW)
  backend/tests/test_phase2.py         ->  backend/tests/    (RESTORED — see 1)
  backend/tests/parse_frontend.js      ->  backend/tests/
  backend/tests/parse_map.js           ->  backend/tests/
  frontend/index.html                  ->  frontend/
  map/index.html                       ->  map/
  render.yaml                          ->  repo root         (⚠️ READ SECTION 2)
  env.example                          ->  repo root
  README.txt                           ->  repo root

factors.json is NOT included — nothing in it changed. Do not overwrite yours.
zones.py, haul.py, network.py, db.py and here_routing.py are unchanged since
Phase 4 and are not included.

Tests, all run in this session:
    python3 backend/tests/test_phase2.py      -> 139 passed  (unchanged)
    python3 backend/tests/test_phase3.py      -> 151 passed  (unchanged)
    python3 backend/tests/test_phase4.py      -> 202 passed  (unchanged)
    python3 backend/tests/test_phase25a.py    ->  77 passed  (new)
    node     backend/tests/parse_frontend.js  ->  95 passed  (84 + 11 new)
    node     backend/tests/parse_map.js       ->  81 passed  (57 + 24 new)
                                                 ---
                                                 745 assertions

Committed locally. The push failed, as always — the git proxy blocks it.


--------------------------------------------------------------------------------
1. test_phase2.py WAS MISSING FROM THE REPO AGAIN
--------------------------------------------------------------------------------
I cloned HEAD before starting. backend/haul.py is there, README.txt reads
"PHASE 4", and every Phase 4 file matches my build byte for byte — so Phase 4
landed cleanly. But backend/tests/ held only FOUR files: test_phase2.py was
missing again.

That is the fourth time this one file has gone astray. It is in this zip.
After extracting, `ls backend/tests/` must show SIX:

    parse_frontend.js  parse_map.js  test_phase2.py  test_phase3.py
    test_phase4.py     test_phase25a.py

Nothing in the running app depends on it. The cost is only that a future phase
cannot prove it has not broken Phase 2. Worth a glance each time you commit.


--------------------------------------------------------------------------------
2. ⚠️ render.yaml — CHECK THIS BEFORE ANY BLUEPRINT SYNC
--------------------------------------------------------------------------------
You upgraded the Postgres and web service in the Render dashboard. render.yaml
in the repo still said:

    plan: free      (web service)
    plan: free      (database)

so the blueprint now contradicts your deployment. If Render ever re-syncs the
blueprint it tries to apply the file — and for the DATABASE that is the
destructive direction.

I have updated it to the plans I quoted when I recommended the upgrade:

    plan: starter          web service   ($7)
    plan: basic-256mb      database      ($6)

**If you actually bought something else, fix those two lines before you commit,
or at the very least before any blueprint sync.** I cannot see your dashboard
from here, and guessing wrong in the file is worse than leaving it obviously
wrong. The comments in the file say the same thing.

Your API keys are now listed in render.yaml with `sync: false`, which tells
Render they are dashboard-managed secrets and must not be wiped by a sync. Their
VALUES are not in the file and must never be.

While you are in there: **ADMIN_TOKEN is still unset.** Every admin endpoint is
open, and this delivery adds one more that spends money — the Street View proxy.
`openssl rand -hex 24` → Render → Environment → paste into the Data Management
header field.


--------------------------------------------------------------------------------
3. TARK TEE — you do not need to upload anything
--------------------------------------------------------------------------------
You asked whether you could "upload a data layer with all restrictions taken
from tarktee". You do not have to. Tark Tee runs a live ArcGIS REST server and
it carries exactly what you want:

    weak bridges          mass restrictions       height restrictions
    width restrictions    traffic restrictions    diversions

(and, unused for now: truck_roads_60t / _80t / _25m, ice roads, and a
restrictions_tallinn layer that will matter for the urban-site work in Phase 5.)

It is fetched live, server-side, and cached for 30 minutes. Nothing to upload,
nothing to keep up to date, and it cannot go stale — which is exactly the failure
mode copying it into your own zones table would have had.

WHAT YOU GET
  * Six toggleable layers on the public map under "Road restrictions".
    All OFF by default — it is a busy overlay on a map whose job is haul routes,
    so you turn on the one you are asking about.
  * Click any of them for a popup.
  * **Every baked route is checked against every layer**, and the result appears
    in that route's expanded panel on the Routes tab, next to the haul roads.
    That is the part worth having: not a layer you look at, but "this route
    passes a weak bridge".

⚠️ TWO THINGS I HAD TO FIX OR REFUSE, AND YOU SHOULD KNOW ABOUT BOTH

(a) THEIR COORDINATES ARE MISLABELLED AT SOURCE.
    The service returns L-EST97 projected metres while declaring
    "spatialReference": {"wkid": 4326}, and it ignores outSR. I checked both
    f=geojson and f=json&outSR=4326 against the live service — same lie both
    times. Anything that believed it would draw Estonian roads in the Indian
    Ocean.

    So backend/projection.py converts them. pyproj would be the normal tool and
    my sandbox cannot install it, so it is the Lambert Conformal Conic maths
    written out and verified two ways: the false origin is exact by definition,
    and a round trip through the forward projection is accurate to 5.6 mm across
    the whole country. Two real bridges land in the right counties for their road
    numbers (13xxx = Ida-Viru, 14xxx = Jõgeva).

    It converts only if the value actually looks projected, so the day Tark Tee
    starts telling the truth this keeps working instead of double-converting.

(b) I WILL NOT TELL YOU WHETHER A BRIDGE CAN TAKE YOUR TRUCK. ⚠️
    Real nominal_load values from the live service look like this:

        "N-8/NG-30"     "N-13/NG-60"     "N-18/NG-60"     ""

    That is an Estonian bridge load CLASSIFICATION, not a tonnage. I do not know
    what N-13/NG-60 permits, nobody has told me, and so nothing in this code
    converts it to tonnes or compares it against a 44 t artic. The route panel
    shows the class verbatim, the vehicle's laden weight beside it, and says
    plainly that the comparison is not one the app can make.

    **This is a question for you.** If N-x/NG-x maps to a tonne limit — or to a
    rule like "N is the single-vehicle class and NG the tracked-vehicle class,
    both in tonnes" — tell me and I will make it a real check with a real verdict.
    Until then a confident green tick would be the worst possible output.

    Note one of the five bridges I sampled has an EMPTY nominal_load. That is
    handled, and reported as "no load class recorded" rather than as safe.

ADVISORY ONLY, DELIBERATELY. None of this is fed to HERE as avoid-areas. HERE has
its own truck restriction model and returns its own per-section notices, and two
systems arguing about the same road would be worse than one. A "hit" means the
restriction sits within 30 m of the baked line — not that it applies to your
vehicle. That 30 m is judgement, the same status as the 3 km zone detour pad.

DIAGNOSTIC:  /api/admin/diagnostics/restrictions?probe=true
It shows the raw coordinate next to the converted one, so the mislabelling is
visible rather than something you take on trust.


--------------------------------------------------------------------------------
4. STREET VIEW — added, and wired so it costs almost nothing
--------------------------------------------------------------------------------
Mapbox has no street-level imagery, so this is Google Maps Platform: a separate
product, a separate key and a separate bill from HERE.

SET UP:
  1. Google Cloud console → enable **Street View Static API** only.
  2. Create an API key and restrict it to your Render server's IP.
  3. Render → Environment → GOOGLE_MAPS_API_KEY
  4. Optional but recommended: also set GOOGLE_MAPS_URL_SIGNING_SECRET.

With no key set nothing breaks — popups simply show no photo.

THE COST TRICK, WHICH IS THE WHOLE DESIGN:
Google's metadata endpoint is FREE and consumes no quota. Their words: "Street
View Static API metadata requests are available at no charge. No quota is
consumed when you request metadata." Only images are billable. So the app
**always** asks metadata first and only ever requests a picture for a location
Google actually has imagery for. A quarry down a private haul track — most of
this network — costs nothing and shows nothing, instead of spending a paid
request to receive a grey tile.

On top of that: imagery is only looked up when you actually open a popup (not for
27 locations on page load), metadata answers are cached for a week, and the key
never reaches the browser because everything is proxied.

The popup also tells you how far away the nearest panorama is when that is more
than 25 m, because on a long approach road the photo can be of somewhere else
entirely.

⚠️ I have NOT quoted a price anywhere in the code or here. Google's rates have
changed twice recently — the universal $200 monthly credit is gone, replaced by
per-SKU free tiers — and a stale figure in a comment is worse than none. Read the
current rate off Google's pricing page.
/api/admin/diagnostics/streetview reports how many billable requests this process
has actually made, which is the number worth watching.

Nothing is stored. Google's terms restrict caching Maps content, so the proxy
streams and forgets, and there is an assertion that the module never opens a file
or touches the database.


--------------------------------------------------------------------------------
5. MAP POLISH — and one item that was not what the backlog said
--------------------------------------------------------------------------------
⚠️ THE KPI CARDS DID NOT EXIST.
The backlog read "KPI cards on the map synced to timeline + filters — KPIs recalc
on filter but not on timeline scrub". Going to fix it, I found calculateKPIs()
was writing to element ids (kpi-routes, kpi-capacity) that are not in the page at
all. They had been removed at some point and the function was left behind,
running on every filter change and updating nothing.

So I built the cards rather than "syncing" cards that were not there. They sit in
the sidebar under "At a glance" and follow the timeline as well as the filters:
with the timeline open they count the routes carrying volume in the month on
screen, and the label changes to say so — otherwise a month figure reads as a
network figure that has mysteriously dropped.

ANIMATION ONLY IN PLAY MODE. The ant-march dashes now move only while the
timeline is playing, and the line is solid otherwise. The old loop also scheduled
itself every frame for ever whether or not there was anything to animate; it now
stops dead when playback stops. That matters on a phone.

OFFSET COLLAPSES AT HIGH ZOOM. The ±1–4 px direction offset is what separates
laden-out from empty-back at zoom 12. Past about zoom 14 the road is wider than
the offset, so it stops separating anything and starts drawing the route in the
verge — which is what the "1 m accuracy" complaint was really about. It now ramps
up to zoom 12 and back to zero by zoom 17. Line width keeps growing instead, so a
route at zoom 16 no longer looks like a scratch on a 40 px road.


--------------------------------------------------------------------------------
6. MOBILE — analysed, not built. Full version in claude/mobile-analysis.md
--------------------------------------------------------------------------------
Short version: **do not make the app responsive.** Build one new read-only phone
screen and leave the other five tabs desktop-only.

The finding is who is actually holding the phone. Submitters, planners and admins
are all at a desk — nobody types a 60-month forecast matrix or draws a haul road
on a phone. The person who genuinely needs this on a phone is a gate marshal,
driver or site engineer asking "where does this load go and what should I watch
for" — and that person **has no login and no screen** in the app today.

Making the existing five tabs responsive would mean card-ifying 91 table columns
to serve people who are sitting at a computer anyway.

WORTH BUILDING, in order:
  1. A `/m/` route-lookup screen: one route, the line on a map, cycle time, and —
     the part that only exists as of today — its Tark Tee warnings and any haul
     road on it. Every API it needs already exists.
  2. Public map touch fixes: full-width sidebar under ~700 px, the timeline bar
     is positioned assuming a 300 px sidebar is there, and tap targets are 7 px
     where they want ~44. Half a day, and it is what you would show a client on a
     phone.
  3. A "what's on today" list. Cheap once (1) exists.

DEFER: Approvals on mobile. It is the only genuinely mobile *write*, and the role
gate is decorative — LOGINS is a client-side dict with plaintext passwords in
view-source. Putting an approve button on the device most likely to be handed to
someone or left unlocked makes that materially worse. Wait for real auth.

BEFORE BUILDING ANY OF IT: ask whoever will actually hold the phone what they
open it for. The whole analysis is inference from the code. It is a reasonable
inference and it is still an inference — the likeliest way to waste the work is to
build the route screen and discover the real need was "photograph a delivery note
and attach it to a load".


--------------------------------------------------------------------------------
7. STILL OPEN FROM THE 2.5 BACKLOG
--------------------------------------------------------------------------------
AI-ASSISTED UPLOAD / AUTOFILL — parked, as you said. Still needs three real
example files before it can be scoped. A parser for a known column layout is a
week; "understand any document" is a different product.

CONFIG PAGES — you chose a config table in Postgres with factors.json as the
seed, now that the paid plan makes persistent storage a real option. NOT built
here; it is a data-model change and belongs with 2.5b.

STILL IN 2.5b (the next delivery): multi-year forecast entry, merged My
submissions + Approvals, nav restructure, route editing, config pages.

STILL BLOCKED ON FORECASTS EXISTING: origin/destination icons highlighting when
in a forecast, and forecast route colours by discipline. Neither can be built
until forecasts are re-authored — they would have nothing to colour.

DRAG-TO-EDIT DRAWN SHAPES — you said not yet, revisit at Phase 5a. Agreed: gates
are points and node dragging already works, so 5a gets it free for gates without
adding mapbox-gl-draw and coupling to a plugin version.


--------------------------------------------------------------------------------
8. WHAT IS NOT TESTED
--------------------------------------------------------------------------------
Neither external service is ever called from my sandbox — the same rule HERE has
followed since Phase 1. Every Tark Tee assertion is about parsing, reprojection
and matching maths against a recorded fixture; every Street View assertion is
about which endpoint is called and in what order. Tark Tee could rename a field
tomorrow and the suite would still pass.

The projection is the exception and IS properly tested, because it is pure maths
with no dependency.

Also unexercised, as in every phase: the HTTP layer (FastAPI cannot boot here),
every Postgres branch of db.py, and anything needing a browser — the restriction
overlay, the Street View thumbnails and the KPI cards are asserted at source
level only.

Run both diagnostics against the deployment once it is up:
    /api/admin/diagnostics/restrictions?probe=true
    /api/admin/diagnostics/streetview?probe=true&lat=…&lon=…

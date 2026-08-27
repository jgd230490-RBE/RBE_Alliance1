RBE Alliance 1 — PHASE 2.5a-fix
Restriction values · colours · route z-order · KPI HUD
================================================================================
Extract over your repo root, then commit. NO DELETIONS.

  backend/restrictions.py              ->  backend/
  backend/main.py                      ->  backend/
  backend/tests/test_phase25a.py       ->  backend/tests/
  backend/tests/parse_frontend.js      ->  backend/tests/
  backend/tests/parse_map.js           ->  backend/tests/
  frontend/index.html                  ->  frontend/
  map/index.html                       ->  map/
  README.txt                           ->  repo root

projection.py and streetview.py are UNCHANGED from the 2.5a zip and are not
included. If you have not applied that zip yet, apply it first — this one
replaces four of its files and assumes the other two are there.

    python3 backend/tests/test_phase2.py      -> 139 passed  (unchanged)
    python3 backend/tests/test_phase3.py      -> 151 passed  (unchanged)
    python3 backend/tests/test_phase4.py      -> 202 passed  (unchanged)
    python3 backend/tests/test_phase25a.py    -> 104 passed  (was 77)
    node     backend/tests/parse_frontend.js  ->  99 passed  (was 95)
    node     backend/tests/parse_map.js       ->  96 passed  (was 81)
                                                 ---
                                                 791 assertions

Committed locally; push blocked by the proxy as always.


--------------------------------------------------------------------------------
1. THE RESTRICTION VALUE — my mistake, and it went further than you asked
--------------------------------------------------------------------------------
You were right and the cause was sloppy: I read the real field names off the
bridges_weak layer and then GUESSED at the field names for the others, with a
hopeful list like ['restriction','piirang','value','height','width','mass'].
None of those exist. So the layer told you a height restriction was near your
route and never told you it was 4 m — the only part that matters.

The real schema, read off the live service:

    restriction_limit   a NUMBER.  8 = eight tonnes.  3.2 = 3.2 metres.
                        (arrives as float32 noise: 4.1500000953674316)
    type                e.g. GROSS_VEHICLE_WEIGHT_LIMIT
    cause / effect      e.g. CULVERT_REPAIRS / COMPLETE_CLOSURE
    extra_info          Estonian free text, often the useful detail
    km_from / km_to     chainage along the numbered road
    date_from/date_to   epoch MILLISECONDS
    links               reference URL

Now shown in three places: the map popup leads with it, the map labels the
value beside the dot from zoom 11 so you can read it without clicking, and the
route panel has it in its own column.

⭐ AND BECAUSE IT IS A REAL NUMBER, YOU NOW GET A REAL VERDICT.
restriction_limit is in the same unit as a figure already in factors.json, so
mass, height and width are now genuinely compared:

    3.2 m height limit  ·  limit 3.2 m  ·  vehicle 4.0 m  ·  EXCEEDS by 0.8 m
    8 t mass limit      ·  limit 8 t    ·  vehicle 44 t   ·  EXCEEDS by 36 t
    8 t mass limit      ·  limit 8 t    ·  Rigid 7.5t     ·  within, 0.5 t spare

Weak bridges still get NO verdict, for the reason in the last README:
"N-13/NG-60" is a load class, not tonnes. That question is still open and is
the one thing here I still cannot answer for you.


--------------------------------------------------------------------------------
2. ⚠️ FOUND WHILE FIXING THAT: THE LAYERS CONTAIN EXPIRED RECORDS
--------------------------------------------------------------------------------
Once I could read date_from / date_to I checked them. Every record in the first
sample I pulled from the live service was from **June–August 2017**. Nine years
stale.

That is worse than a missing feature. Warning you that your route is closed for
culvert repairs, when those repairs finished in 2017, would poison the whole
layer — and the two working restrictions next to it.

So: dates are parsed, and **current-only is the default everywhere** — map,
route checks, all of it. The count of what was excluded is reported rather than
hidden, so a near-empty layer reads as "nothing in force today" instead of
looking broken. `/api/restrictions?include_expired=true` shows everything if you
ever want the history.

A record with no dates at all is treated as open-ended and always applies, the
same rule zones already use.


--------------------------------------------------------------------------------
3. TRAFFIC RESTRICTION COLOUR
--------------------------------------------------------------------------------
It was a muddy brown shared by everything I had classed as a "note", which said
nothing. Each layer now has its own colour, chosen to mean something:

    Weak bridges          #7F1D1D  deep maroon   structural
    Mass limits           #92400E  bronze        weight
    Height limits         #7C3AED  violet     ]  a dimension your vehicle
    Width limits          #DB2777  magenta    ]  either fits or does not
    Traffic restrictions  #DC2626  strong red    the road is shut — stop colour
    Diversions            #EA580C  orange        universal diversion colour

Checked against the existing palette so nothing collides: inbound #039E86,
outbound #f59e0b, haul roads #C2790B, zones #BF2E55. There is an assertion for
that, and another that all six are distinct.

The colours are served from the backend catalogue and stamped onto every
feature, so the sidebar legend dot and the thing it points at read the same
value and cannot drift apart. That has had to be fixed on this map once before.


--------------------------------------------------------------------------------
4. ROUTE Z-ORDER
--------------------------------------------------------------------------------
Fixed. In Mapbox, layer ADD ORDER is z-order, and outbound was being added last
so it painted over inbound. Outbound is now added first and inbound (laden)
draws on top — it is the leg the network is planned around and the one a
forecast's distance follows, so it should win where they overlap.

Each casing still sits under its own core line. There are assertions on both
orderings and a comment at the site saying why, because this is exactly the kind
of thing that gets silently reversed by a tidy-up.


--------------------------------------------------------------------------------
5. KPIs ON THE MAP
--------------------------------------------------------------------------------
Moved out of the sidebar onto the map itself, top-right under the navigation
control. That means they stay visible with the sidebar collapsed and while the
timeline is playing, which is when the numbers are actually moving.

Still driven by both the filters and the timeline, and the label still changes
to "Routes in Mar 2027" when the timeline is open so a month figure cannot be
mistaken for a network figure. On a narrow screen it drops the explanatory note
and moves to the bottom of the map — the small start on the mobile work.


--------------------------------------------------------------------------------
6. STILL OPEN — the one question I need from you
--------------------------------------------------------------------------------
**What does an Estonian bridge nominal_load class permit?**

    "N-8/NG-30"    "N-13/NG-60"    "N-18/NG-60"

If N-x/NG-x maps to a tonne limit — or if the rule is something like "N is the
single-vehicle class and NG the tracked-vehicle class, both in tonnes" — tell me
and weak bridges get the same real verdict mass, height and width now have.
Until then the class is shown verbatim next to your vehicle's laden weight and
labelled "not comparable", which is honest but is doing less for you than it
could.


--------------------------------------------------------------------------------
7. ON THE TESTS
--------------------------------------------------------------------------------
Five assertions were NARROWED rather than deleted. The old suite asserted "no
pass/fail verdict is rendered anywhere", which was right when the only thing I
could read was a bridge load class. Now that mass, height and width are real
numbers, that assertion became "no verdict on a bridge LOAD CLASS" — which was
always the actual point. Reversing an assertion and saying so is fine; quietly
deleting one is not.

Unchanged from last time: tarktee.ee is never called from my sandbox. Every
assertion here is against parsing, projection and matching maths using fixtures
copied from real responses. The field names and value formats in those fixtures
are verbatim from the live service on 2026-08-27 — but if Tark Tee renames a
field tomorrow, this suite will still pass and the app will still break. Run
/api/admin/diagnostics/restrictions?probe=true against the deployment.

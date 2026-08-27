RBE Alliance 1 — PHASE 4: TEMPORARY HAUL ROADS
================================================================================
Extract over your repo root, then commit.
THERE ARE NO DELETIONS THIS TIME. Nothing needs removing by hand.

  backend/haul.py                      ->  backend/          (NEW)
  backend/zones.py                     ->  backend/
  backend/here_routing.py              ->  backend/
  backend/network.py                   ->  backend/
  backend/db.py                        ->  backend/
  backend/main.py                      ->  backend/
  backend/tests/test_phase4.py         ->  backend/tests/    (NEW)
  backend/tests/test_phase2.py         ->  backend/tests/    (RESTORED — see 1)
  backend/tests/parse_frontend.js      ->  backend/tests/    (RESTORED — see 1)
  backend/tests/parse_map.js           ->  backend/tests/
  frontend/index.html                  ->  frontend/
  map/index.html                       ->  map/
  README.txt                           ->  repo root

factors.json is NOT included — nothing in it changed. Do not overwrite yours.

Tests, all run in this session:
    python3 backend/tests/test_phase2.py     -> 139 passed   (unchanged)
    python3 backend/tests/test_phase3.py     -> 151 passed   (unchanged)
    python3 backend/tests/test_phase4.py     -> 202 passed   (new)
    node     backend/tests/parse_frontend.js ->  84 passed   (59 + 25 new)
    node     backend/tests/parse_map.js      ->  57 passed   (49 + 8 new)
                                                ---
                                                633 assertions

Committed locally in the session so the diff and message exist. The push failed,
as always — the git proxy blocks it. Nothing reached GitHub.


--------------------------------------------------------------------------------
1. THE TWO DELETED TEST HARNESSES ARE BACK IN THIS ZIP
--------------------------------------------------------------------------------
I checked the repo at HEAD before starting anything. backend/tests/ still held
only parse_map.js and test_phase3.py — commits bd540cb and 087bd07 were still the
tip — so rbe-restore-test-harnesses.zip was never applied.

test_phase2.py and parse_frontend.js are in this zip, recovered from the commit
before their deletion and then updated for Phase 4. After extracting,
`ls backend/tests/` should show FIVE files:

    parse_frontend.js  parse_map.js  test_phase2.py  test_phase3.py  test_phase4.py

You can discard the old restore zip.


--------------------------------------------------------------------------------
2. READ THIS BEFORE DRAWING A HAUL ROAD
--------------------------------------------------------------------------------
HERE routes on HERE's map. A haul road just cut across a field is not in that
map, and no parameter makes it appear. That single fact shaped the whole design,
and it is why there are two modes:

  SPLICE (the default)  Assumes HERE has never heard of the road.
      Routes origin -> the road's near end with HERE, uses YOUR DRAWN LINE for
      the road itself, then routes from the far end to the destination. Two HERE
      calls per leg instead of one. Always works, because it never asks HERE
      about a road it does not know. The geometry along the road is exactly what
      you drew — no better and no worse.

  VIA                   Assumes HERE already maps the road.
      One call with the road's two ends as via-waypoints; HERE finds its own way
      between them. Cheaper, and the line comes back snapped to real roads.
      Correct ONLY if the road is genuinely in HERE's data. If it is not, HERE
      routes between those two points along whatever public roads it does know,
      returns a plausible-looking route that is wrong, and nothing in the
      response says so.

Splice is the default because its failure mode is visible on the map and via's
is not. Use via only for a haul road that is an existing forest or gravel track
HERE already knows — and confirm that with the probe in section 5 first.


--------------------------------------------------------------------------------
3. WHAT WAS BUILT
--------------------------------------------------------------------------------
DRAWING. Data Management -> Zones has a second button, "+ Haul road". It uses the
same click-to-place vertex collector as zones but saves an OPEN LINE instead of
closing the ring, and the panel shows the drawn length and what the assigned
speed makes of it in minutes as you draw.

ATTACHING. A haul road does NOTHING until it is put on a route. Expand any route
on the Routes tab and there is a panel above the analysis table: pick a road,
Attach. Reorder with the arrows, Detach with the link. That is the "editable on
the route itself" requirement from 2026-08-22 — no second drawing tool and no new
top-level page.

Explicit attachment is not only simpler. Because nothing is inferred from
proximity, "which legs does this haul road change" is a table lookup rather than
a guess: Phase 3's zone invalidation has an approximate half for legs baked
before it existed, and Phase 4's has none at all. It reports approximate: 0 every
time, and that is a fact about the design rather than luck.

SPEED. HERE will not accept a custom speed, so an assigned one is applied
afterwards. In splice mode the haul stretch is timed at drawn_length / speed
outright — HERE never sees that stretch, so there is nothing to substitute, only
to supply. In via mode HERE's own section time for the stretch is REPLACED by
section_length / speed. Either way the adjusted figure lands in
route_geometry.duration_hr, which is what route_analysis() reads, so cycle time,
trips/day, tonnes/day, tonnes/month and CO2 all move.

That was your choice ("full: recompute duration + feed cycle time"), and it is
the useful version, but be clear about what it means: a number somebody types
into a form now drives fleet sizing. So HERE's own figure is kept alongside it in
duration_hr_here, the route analysis marks any affected cycle with a dagger, and
hovering it shows what the cycle would have been on HERE's timing.

A haul road with NO speed set is routed through and its duration left exactly as
HERE gave it. Blank means "not set", not zero — zero and negatives are rejected
on the way in, because dividing by them is the actual bug. The route panel flags
roads with no speed in red: a spliced road with no speed adds distance to the
cycle and no time at all.

origin_temp_km. Now derived, as you chose. It was seeded from V2, shown in
routes_status(), and computed with nowhere — confirmed by grep. It now means "km
of this route's loaded leg on drawn temporary haul road" and is recomputed on
every attach, detach, reorder and haul-road edit. A route with no haul road
attached reads 0, so the old V2 seed values will be gone from that column the
first time anything refreshes it.

  >>> If those V2 numbers were real survey data you wanted to keep, tell me
  >>> BEFORE you apply this and I will move them to a separate column first.
  >>> Once the refresh runs they are overwritten in the database.

PUBLIC MAP. The "Temporary haul" toggle in the map sidebar is enabled again after
being disabled and labelled "(Phase 4)" since Phase 2 step 2. It draws the real
drawn polylines — dashed and amber, so they read as temporary works rather than
surveyed network — with a popup giving the length, the assigned speed, the
implied minutes and how many routes use it. The 97 legacy haul segments went with
a1_data.js and are still not recoverable; these are only the roads drawn from now
on.


--------------------------------------------------------------------------------
4. A BUG THAT WAS ALREADY THERE, NOW FIXED
--------------------------------------------------------------------------------
'haul_road' has been in zones.KINDS since Phase 3, doing nothing, with
affects_routing defaulting to TRUE. Any zone of that kind drawn against the
deployed Phase 3 build would have had its bounding box sent to HERE as an
avoid[areas] — telling the router to steer AWAY from the road it is meant to use.
Silently: the only symptom would be routes that got inexplicably longer.

Haul roads are now excluded from the avoid list by KIND, not by a flag someone
has to remember to untick. If you drew any haul-road zone since Phase 3 shipped,
check it after applying this: its Routing column should read "Routed through",
not "Avoided", and any route baked while it existed is worth re-baking.


--------------------------------------------------------------------------------
5. THE TWO THINGS I COULD NOT TEST, AND THE PROBE THAT ANSWERS THEM
--------------------------------------------------------------------------------
HERE is never called from my sandbox. Both of these are inherited from a code
comment written during Phase 2 and NEITHER has ever been checked against the live
service:

  a) that HERE ignores `alternatives` when via-waypoints are present
  b) that a stopover via splits the response into one section per leg

(b) is load-bearing: via mode's speed substitution only works if HERE isolates
the haul stretch as its own section. The code detects that failure — if the split
does not happen it applies NO speed adjustment rather than applying one to a
section that might be the whole route, and says so in the response and in the
per-road note. That detection is tested. The underlying behaviour is not.

Run this against the live deployment, on a route you have attached a haul road to:

    /api/admin/diagnostics/haul-roads?route_id=R001&probe=true

It spends up to three HERE calls and reports:

    probe.reading.here_drops_alternatives_with_via   -> answers (a)
    probe.reading.sections_split_at_vias             -> answers (b)
    probe.reading.via_mode_usable                    -> true if via mode is safe

If via_mode_usable is false, leave every haul road on splice. Send me what it
returns and I will fold the answer into the notes so nobody re-derives it.

Also unexercised, as in every phase: the HTTP layer (FastAPI cannot boot here),
every Postgres branch of db.py, and anything needing a browser — the drawing
mode, the route panel and the map layer are asserted at source level only.


--------------------------------------------------------------------------------
6. THINGS TO EXPECT THAT MIGHT LOOK LIKE BUGS
--------------------------------------------------------------------------------
ALTERNATIVES DISAPPEAR ON A ROUTE WITH A HAUL ROAD. A leg routed through one
comes back as a single option. In via mode that is HERE's own behaviour (probably
— see 5a). In splice mode it is my decision: alternatives across a spliced route
would be the cross product of the alternatives on each sub-call, ranked by
durations that are partly HERE's and partly assigned, which is a worse answer
than one honest option. "Make primary" therefore has nothing to promote on those
routes. The route panel warns about it up front, and promote_alternative returns
a message naming the haul road instead of a bare cache miss. Detach the road and
alternatives come back on the next bake.

A BAKE NOW COSTS MORE HERE CALLS THAN LEGS. `limit` on /api/admin/bake-routes
still counts legs, but a leg spliced through N haul roads is N+1 calls. The batch
response has a new `here_calls` field with the real number, and the attach/detach
panel quotes the cost before you spend it. On a network with no haul roads
nothing changes — the same one call per leg as before.

A HAUL ROAD THAT SEEMS TO DO NOTHING is almost always one of three things, and
the UI now names all three: it is attached to no route (the Zones table says "no
routes" in amber), it is inactive / outside its date window / not ticked "open to
traffic" (the route panel says "not in force today"), or the route has never been
baked. You said the network is only partly baked, so expect the third.

DRAWING ORDER IS NOT DIRECTION. Which end of a road a truck enters is worked out
per leg from where it is coming from, so the loaded leg and the return leg
traverse the same drawn line in opposite directions. Clicking a road left to
right says nothing about traffic.

THE DRAWN LENGTH IS ONLY AS GOOD AS THE DRAWING. A curving road sketched with
four clicks reads short, and the assigned speed is divided into that short
figure. Click more points on a bendy road.


--------------------------------------------------------------------------------
7. STILL OUTSTANDING, AND NOW MORE URGENT
--------------------------------------------------------------------------------
RENDER POSTGRES IS STILL ON THE FREE PLAN. 30 days from creation, 14-day grace,
then deleted. No backups, ever. Raised every session since 2026-08-21. Phase 4
makes it worse again: haul roads invalidate and re-bake routes, and a spliced
re-bake is more HERE calls than it used to be, all landing in a database with a
deletion date. $6/month.

ADMIN_TOKEN IS STILL UNSET ON RENDER, so every admin endpoint is open. Phase 4
adds four more that spend HERE calls per click: attach, detach, reorder, and the
haul diagnostic with probe=true. `openssl rand -hex 24` -> Render -> Environment
-> paste into the Data Management header field.


--------------------------------------------------------------------------------
8. NOT BUILT, DELIBERATELY
--------------------------------------------------------------------------------
  * No vertex dragging. Editing a haul road still means redrawing it, exactly as
    for zones. mapbox-gl-draw remains the upgrade path and drops into the same
    place. You have asked for drag-to-edit routes; that is the same mechanism and
    still unbuilt.
  * No warning that an approved forecast now assumes a different route. A haul
    road changes cycle time and therefore trucks needed, and nothing tells a
    planner that an already-approved forecast moved under them. Phase 3 has the
    identical gap. It wants its own pass across both, not a patch on this one.
  * Gates vs haul roads: I checked, and they do not collide. A gate moves an
    endpoint via _waypoint(); a haul road inserts a middle. A site can have both
    and they compose — the gate decides where the leg starts, the haul road what
    it passes through. No change was needed.
  * Mixed splice and via roads on ONE leg fall back to splicing everything, and
    say so in the response rather than downgrading silently. Interleaving them
    would break the section indices the via speed model reads.

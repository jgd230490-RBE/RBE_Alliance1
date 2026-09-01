RBE Alliance 1 — routes 20% thinner + alternative routes as a grey underlay
===========================================================================
Delivered 2026-09-01.  Zip: rbe-map-alts.zip

⚠️ SUPERSEDES `rbe-map-hotfix.zip`. Apply this instead if you have not applied
   that one; it contains the same route-visibility fix plus these two changes.

Five files. Extract over the repo root.

    map/index.html
    backend/main.py            one new endpoint
    backend/network.py         one new function
    backend/tests/parse_map.js
    backend/tests/test_phase5a.py

No migration, no backup, no version bump — `ipt_segments.js` is untouched so the
handshake stays v9 and `?v=9` is still correct.

🔴 THIS IS A BACKEND CHANGE AS WELL AS A MAP ONE. Render will redeploy the
service, not just serve a new static file. Watch the boot log completes normally.


-----------------------------------------------------------------------------------
1. ANOTHER 20% OFF THE ROUTE LINES
-----------------------------------------------------------------------------------
                  before        now
    zoom 7         1.7          1.36
    zoom 12        5.0          4.0
    zoom 16        8.5          6.8

Casing keeps the fixed 1.25× ratio (1.7 / 5 / 8.5). That ratio is not decoration —
it is what makes the core and casing dash arrays draw the same period on screen.

⚠️ **THE GAP RATIO HAD TO MOVE WITH IT, 0.45 → 0.55.** `line-dasharray` is in
multiples of the line's own width, so a 20% thinner line is a 20% smaller gap. At
the new 1.36 px zoom-7 width, 0.45 would have given a **0.61 px** gap — back into
the range where a dashed line aliases to solid. 0.55 holds it at 0.75 px, about
what it was before this thinning.

The cost is at close zoom: at zoom 12 the gap is now 2.2 px against a 4 px dot, so
the dots are spaced a little wider than the agreed 3:1. If they read too loose,
that is one number and I will bring it back.

⚠️ Still a plain constant, not a zoom expression. The reason is in the source and
in the last README: a bare array inside a `step` expression is what made every
route vanish, and `line-dasharray` is a cross-faded property whose expression
support has been patchy across GL JS versions.


-----------------------------------------------------------------------------------
2. ALTERNATIVE ROUTES
-----------------------------------------------------------------------------------
Every non-primary option HERE returned is now available as a thin translucent grey
line under both carriageways.

    colour     #94a3b8 at 0.45 opacity — the same muted slate 'Outside A1' uses,
               which is this map's established colour for "real, and not the subject"
    width      0.8 px at zoom 7, 2.2 at 12, 3.6 at 16
    style      SOLID, not dotted. The dots are what say "this is a haul route";
               an alternative drawn dotted would read as another one
    casing     none. A halo is what lifts a line off the basemap, and lifting these
               off the basemap is the opposite of what they are for
    z-order    added BEFORE both carriageways, so it draws underneath
    control    "Alternative routes", **OFF by default**

The sidebar note under the checkbox reports how many alternative legs were found,
or says why there are none.

### 🔴 The design decision that matters — its own source and its own endpoint

Alternatives are **not** added to `/api/public/map-data`. They come from a new
`GET /api/public/route-alternatives` into a separate Mapbox source.

That is not tidiness. **Five separate things walk `routes-source` per feature,
matching "a LineString with a route_id":**

  * `calculateKPIs()` — the route count and trips/day in the HUD;
  * the leg filters in `applyFilters()`;
  * `buildRouteInfo()` — which takes the LONGEST line per route id as that route's
    distance;
  * the forecast timeline, which stamps `is_forecast` onto every matching line;
  * the route-highlight lookup.

Putting alternatives in that collection would have made each route's reported
distance jump to whichever option happened to be longest, and painted alternatives
as forecast routes when the timeline ran. Zones were deliberately kept out of that
same endpoint in Phase 3 for exactly this reason, and the comment saying so is
still in `map/index.html`.

Both halves are asserted: alternatives carry `type: 'Route Alternative'` (never
either Highway string, which is what the KPI count and the leg toggles match on),
and `public_map_data()` is asserted to still emit only `alt_index 0`.

### What is NOT there, and why

  * **Routes with a temporary haul road have no alternatives at all.** That is a
    known Phase 4 consequence — a spliced leg returns one option — not a gap here.
    Those routes will show nothing when you tick the box.
  * An alternative with no geometry (an error row) is skipped rather than drawn as
    an empty line. Asserted.
  * No click popup on the grey lines. They are context; adding a popup would invite
    treating them as selectable, and "promote this alternative" already exists in
    the admin app where it belongs.

⚠️ **Payload.** This is a second fetch of real geometry — up to two extra options
per leg per route. It runs OFF the critical path and is not awaited, so if it is
slow or fails the map is exactly as it was minus the grey underlay. But it is not
free, and if the map feels slower to settle this is the first thing to turn off.


-----------------------------------------------------------------------------------
3. TESTS — 1,510, 0 failed
-----------------------------------------------------------------------------------
    parse_map.js      309  (was 297)      test_phase5a.py   191  (was 182)
    everything else unchanged

Four deliberate regressions confirmed the new guards bite: typing an alternative
as 'Inbound Highway' (1 fail), including the primary option (3), and the two from
the previous zip.

⚠️ One correction worth naming: I wrote an assertion in `parse_map.js` reading
`ok("the backend types them as 'Route Alternative'…", true)` — a literal `true`,
which passes regardless. It is replaced by a real assertion in `test_phase5a.py`
where the function can actually be run. That is the "an assertion that passes for
the wrong reason" failure the project has already paid for twice.

🔴 **Still not seen rendered on a real basemap.** No Mapbox in this sandbox.


-----------------------------------------------------------------------------------
4. AFTER DEPLOYING
-----------------------------------------------------------------------------------
  1. Routes present and dotted, now noticeably thinner.
  2. Tick "Alternative routes". Grey lines should appear UNDER the coloured ones,
     and the note should say how many legs were found.
  3. Check the HUD route count and trips/day do NOT change when you tick it. If
     they do, an alternative has leaked into the main collection and I need to
     know immediately.
  4. Zoom 7–9: are the dots still dots at the new width?
  5. Zoom 12–14: are the dots now too far apart? That is the 0.55 compromise.

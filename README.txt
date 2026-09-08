rbe-map-slice-0908.zip — 2026-09-08
The public map slice: A–E in full, plus ride-alongs F1 and F4.

Extract over the repo root. Three files, all map-side:

    map/index.html
    backend/tests/parse_map.js
    backend/tests/map_browser_check.js

Nothing to delete, nothing to merge by hand, no backend change, no data file changed.


================================================================================
0. FIRST — YOUR REPO IS CLEAN, AND THAT IS NEW
================================================================================

I ran the whole suite against plain HEAD (`529416e`) before starting, which is the
check that caught the missing 2.5b backend files on 04 September. This time everything
landed: fourteen test files present, twelve runnable suites green, `backend/config.py`
there, `map/index.html` carrying the 0907 work. So the map slice was built on top of
0907 rather than around it, and nothing in this zip is a re-delivery.

⚠️ Still outstanding and nothing to do with this slice: THE EIGHT ACCESS CODES ARE
STILL NOT SET ON RENDER. `submitter123` / `planner123` / `admin123` still work on the
live site and the per-IPT filtering built on 02 September does nothing until they are.


================================================================================
A. LOCATION ICONS — one type, one map-layer symbol
================================================================================

All three bugs were the same bug in three costumes: a location's mark was decided in
more than one place.

  1. A railhead was drawn by the `railheads` SYMBOL LAYER, which `toggleRail()` switched
     off with the corridor — so unchecking "Rail network" hid the site.
  2. It was ALSO drawn by the DOM marker loop, which knew nothing about railheads and
     fell through to the cyan default — two marks on one point, wrong one on top.
  3. `Port` was never tested for at all (only the legacy `Hub`), so every port drew as
     that same cyan default and read as a compound.

Fixed structurally, not with three patches. `loc_type` resolves to exactly ONE icon key,
stamped on the feature as the data loads, and exactly ONE `locations` symbol layer draws
it. There is no second layer and no DOM marker anywhere on the map.

  Quarry     diamond      — the EXISTING material map, unchanged: Sand gold, Limestone
                            white, everything else the sienna default. Not flattened.
  Port       anchor       #0369A1
  Compound   rounded sq   see the warning below
  Site       circle       #6D28D9
  Railhead   twin-rail sq #0F766E
  Stockpile  hexagon      #78350F
  Other      small dot    #94a3b8

Shape discriminates, not colour — a colour-blind reader, a greyscale print and a
satellite basemap all lose colour before they lose shape.

⚠️ ONE COLOUR I HAD TO CHOOSE. The brief says Compound is "existing compound sand".
There is no compound sand in this codebase. The existing compound colour is `#00ffff` —
the cyan DEFAULT that ports were also falling through to, i.e. the bug itself. So I
picked `#CA8A04`, an ochre, and I am telling you rather than calling it the colour that
was already there. Say the word and it is one constant.

⚠️ A RAILHEAD LOOKS DIFFERENT NOW. The 02 September mark was a WHITE rounded square
edged in teal. Your table says Railhead fill `#0F766E`, so it is a solid teal square
with the rail and sleepers reversed to white. Deliberate, and it follows the signed
table; flag it if the mockup meant the white one.

⭐ THE LEGEND IS THE FILTER. Seven checkboxes, each hiding its own type and nothing
else, with the swatch drawn from the same colour table and the same geometry as the
mark on the map — a hand-written swatch is how the sidebar's alignment gradient ended
up two palettes out of date. "Sites & assets" is still the master switch. "Rail network"
no longer touches any location.

⭐ A SIDE EFFECT WORTH KNOWING: the site marks are inside the WebGL canvas for the first
time. That is the thing that has always blocked PNG export — see the roadmap's export
note. Export is not built, but the blocker is gone.

🔴 A LATENT CRASH FIXED IN PASSING. `quarryFill()` read `props.material.toLowerCase()`
unguarded. ONE quarry with no material would have thrown inside the marker loop and
taken every remaining marker on the map with it. Guarded, and a quarry with no material
now gets the default fill and is still a diamond, as you asked.


================================================================================
B. EVR LINE
================================================================================

`RAIL_BOLD` is deleted. It painted the corridor in the reserved rail colour whenever a
railhead was in play, which made the least trustworthy line on the map — provisional
Natural Earth, ~6.4 km out at Lelle — the loudest. Never green, never bold, never teal,
as instructed.

One style: thin mid-grey `#64748B`, dash `[2, 18]`, dark slate casing `#334155` at 35%.

⚠️ THE TICKING RULE, AND ONE PLACE I READ YOU NARROWLY. It is now (Play is running) AND
(the EVR checkbox is on) AND (a movement in the month on the playhead starts or ends at
a Railhead). Your line was "dash-offset animates only in months where an Approved route
uses a Railhead"; I kept Play as a condition too. An animation on a map nobody is
scrubbing is a requestAnimationFrame wakeup every frame for as long as the tab is open,
which is why the march was tied to playback in the first place. "Months where a railhead
is used" narrows WHICH months tick; it does not turn a static map into a moving one. If
you want it ticking while paused, that is one condition to remove.

The geometry is untouched, the popup caveat is untouched, and the sidebar note now says
which state the corridor is in, since the colour no longer does.


================================================================================
C. ORIGIN / DESTINATION ON FORECAST PLAY
================================================================================

The 07 September name plates are deleted — the plates, the cap of six, the rectangle
collision skip and the moveend re-placement. That collision skip was already saying in
code what you said in review: six name plates is a list lying on top of a map.

The ends of Approved routes carrying volume in the month on screen get `is_end` stamped,
and the same ONE layer swaps to a bigger image: same mark, 2 px larger, 1 px halo, baked
into the image. No second layer, no second component, no callout. The name is one click
away in the popup that was always there.


================================================================================
D. KPI PANEL
================================================================================

One compact card, top-left, month title, Approved only, 22 working days (read from the
server's factors, which already says 22 — not hardcoded, so the Config page still owns
it). Four always-visible figures, then ONE chip row with a switcher:

    Discipline | IPT | Work section          (Discipline by default)

Each chip carries THREE numbers: `Earthworks 5 veh/d · 16.9 trips/d · 406 t/d`.

⚠️ The third figure is TONNES, not the map unit. The map unit is "vehicles" by default,
and in that unit "material / day" and "movements / day" are the SAME NUMBER — printing
both would put the 04 September correction back on screen in a new place.

⚠️ Vehicles in a chip are summed as ceil() PER LINE, exactly as the headline figure is.
A vehicle working one route cannot also be working another. Ceiling the group total
would under-count, and the chips would then not add up to the card above them — the
browser check asserts that they do.

A group is divided by the months it actually appeared in, not by the window length: a
discipline that ran in one of three months is not doing a third of its rate every month.

IPT comes from the ROUTE, discipline and work section from the LINE. The endpoint
already sends all three, so this needed no backend change. Empty month hides the chips
AND the switcher, and shows "No approved forecast in this month." No warning text
anywhere in the card.


================================================================================
E. WARNINGS — and one source that does not exist
================================================================================

Three named levels, the word on the row, not just a stripe:

    CLASH    a stockpile past its capacity
    WARNING  a Tark Tee mass / height / width exceed on a visible baked route
    CAUTION  a seasonal window from Config matching that month and vehicle

⚠️ A Tark Tee 'breach' is a WARNING, not a Clash. It is a vehicle-versus-limit verdict
on a drivable road, not an impossibility — and it cannot judge a weak-bridge class at
all (§A2 is still open), so it must never be the loudest thing on screen.

🔴 THE SECOND CLASH SOURCE YOU NAME DOES NOT EXIST AND I HAVE NOT INVENTED IT.
"Two Approved movements the product already flags as conflicting on a shared gate/head"
— nothing in this product flags that. There is no conflict, contention or booking
concept anywhere in the backend; grepping for conflict/clash/contention finds two
unrelated comments. It needs a TIME model, which is Phase 7. `gate_blockers` is a
deactivated gate refusing to bake, not two movements colliding. Building it would mean
inventing what "conflicting" means — how close in time, same gate or same head, whether
two IPTs on one railhead is a clash or just a Tuesday — and that is your decision.

Tell me the rule and it is a small amount of work. Until then, Clash has one source.

🔴 A REAL DEFECT FOUND WHILE TESTING THIS. On 07 September I fixed the phone overlap
between the KPI bar and the warning stack with a fixed `bottom:190px`. That cannot work:
the stack is as tall as its contents, so a month with three warnings landed straight
back on the bar. It only showed up when the browser check was finally given a month that
HAS warnings. It measures the bar now, and re-measures when the card changes height or
the window resizes.


================================================================================
F. RIDE-ALONGS — two done, two NOT, and one needs your decision
================================================================================

DONE:

F1. The forecast route label read "412 vehicles". That is the same confusion the
    04 September correction was about — 412 is not 412 lorries, it is 412 loads carried
    out and brought back. It says "Two-way" now. The FIGURE is unchanged; only the words
    on the line moved.

F4. Mapbox `fill-extrusion` buildings, from zoom 14, DEFAULT OFF. Not an ortho mesh.
    ⚠️ Guarded on the `composite` source: the Maa-amet orthophoto basemap is raster and
    has no composite source at all, and `addLayer` would fire an error and return having
    added nothing — the exact silent failure that dropped four route layers on
    2026-09-01. The sidebar says "not available on this basemap" instead.

NOT DONE, and I am not going to half-do them:

F2. Sorting on the five staff tables. This is `frontend/index.html`, not map work — a
    different file, a different test harness, and five tables with different cell shapes
    (the Forecasts table alone has grouped rows with a status chip and a year span). It
    is a straightforward slice, roughly the size of §D on its own, and it belongs in a
    frontend zip where `parse_frontend.js`, `render_frontend.js` and `browser_check.js`
    can all be extended together. Say the word and it is the next thing.

F3. 🔴 VEHICLE PICKERS RESTRICTED TO THE SIX LEGACY PROFILES — THIS NEEDS A DECISION,
    AND IT REVERSES ONE YOU ALREADY MADE.

    On 01 September you chose "add four, keep six", and C9 in open-questions has been
    sitting on "should the six be hidden" ever since. This is the opposite: hide the
    FOUR. That is fine — but three consequences you should see first.

    a) It does not touch existing lines. A forecast already saved on V07/V10/V11/V12
       keeps that vehicle, and the picker will still show it (a currently-selected
       vehicle is never disabled), so nothing becomes unsavable. Good.

    b) "Public vehicle-count" is a BACKEND change. `/api/public/month-kpis` falls back to
       `planning[0]` — V07, 18 t — for a vehicle `factors.json` does not know. If the
       four EU ids stop being selectable, that fallback should stop being one of them,
       which is `main.py`, not the map. Small, but it is backend, and your brief said
       don't rebuild 2.5b/0907, so I have left it alone rather than guess how far you
       meant this to reach.

    c) "Stops the double-fleet count" — I want to make sure I understand what you are
       seeing before I act on it. Nothing in the code counts a vehicle twice: the fleet
       is summed per LINE, and a line names exactly one vehicle. If the double count is
       that two people forecast the SAME physical trucks, one on V07 and one on
       "Rigid 8-wheeler (32t)", then hiding the four stops it happening AGAIN but does
       not fix the lines already written that way. Those need re-saving. If that is what
       you are seeing, say so and I will add a diagnostic that lists which routes carry
       both an EU and a legacy profile in the same month — that is a probe, not a guess.


================================================================================
TESTS
================================================================================

All green:

    backend/tests/parse_map.js           465   (was 443)
    backend/tests/map_browser_check.js    47   (was 31)
    backend/tests/test_week1.py          303
    backend/tests/test_phase5a.py        215
    backend/tests/test_phase4.py         202
    backend/tests/test_phase2.py         160
    backend/tests/test_phase3.py         154
    backend/tests/test_phase45.py        140
    backend/tests/test_ipt_overlay.js    140
    backend/tests/test_phase25a.py       104
    backend/tests/parse_frontend.js      259
    backend/tests/render_frontend.js      28
    backend/tests/test_tenant_audit.py    26

EVERY TEST YOU LISTED IS IN THERE, and two of them only a browser can answer:

  ✅ loc_type=Railhead still visible when the rail layer is off
  ✅ one symbol at a point that is both a former compound paint and a Railhead
  ✅ Port does not use the compound image              ← browser only, see below
  ✅ Quarry diamonds keep distinct fills when two materials exist
  ✅ EVR paint is grey; no #0F766E / #039E86 on the rail layer   ← read off the LIVE layer
  ✅ animation only when the playhead month has a Railhead movement
  ✅ KPI card contains no Warning / Caution / Clash string
  ✅ warning-stack month matches #tl-range

TWELVE DELIBERATE REGRESSIONS, each reverted after. Seven at source level, and the
split is the interesting part:

  caught by parse_map.js  — railheads back on the rail toggle; the bold rail state back;
                            the flow ticking on Play alone; a Tark Tee exceed promoted to
                            Clash; the phone stack back to a fixed offset; the flow layer
                            rejoining the toggle list.
  caught ONLY in Chromium — 🔴 PORT FALLING BACK TO THE COMPOUND MARK, which is your own
                            test and has NO textual signature at all; the chips no longer
                            adding up to the card; the phone stack overlapping the bar.

`map_browser_check.js` is still NOT in the default suite and cannot be — `map/index.html`
loads Mapbox GL JS from a CDN the sandbox blocks, so it substitutes a local copy and
needs `npm i mapbox-gl@2.14.1` first. The header of the file has the commands.

⚠️ WHAT IS STILL NOT TESTED
  - No real basemap. The harness answers the style URL with a blank offline style, so
    nothing about legibility over map tiles — or over satellite — has been checked, and
    that matters more now that seven marks have to be told apart on top of one.
  - No glyphs offline, so no map label has ever been drawn in a test.
  - `buildings-3d` has never been ADDED, only refused: the offline style has no
    `composite` source, which is exactly why that guard exists. The layer itself is
    unexercised.
  - The API is fixtures. The route geometry is four invented lines, not 107.
  - Nothing has been near Postgres, HERE, Tark Tee or Google.


================================================================================
WHAT I WOULD LOOK AT FIRST ON THE DEPLOYMENT
================================================================================

1. Add a Railhead in Data Management, then uncheck "Rail network". The pin stays.
2. A port and a compound side by side — anchor and rounded square, never the same mark.
3. Two quarries with different materials — two fills, both diamonds.
4. Press Play on a month that uses a railhead, then one that does not. Ticks, then none.
   The corridor stays grey in both.
5. Switch the KPI chips through Discipline / IPT / Work section and check the vehicle
   numbers still add up to the card above them.
6. A month with a stockpile over capacity: CLASH in the stack under the map, nothing in
   the KPI card.
7. The seven marks over the SATELLITE basemap, which is the one thing the tests cannot
   see at all.

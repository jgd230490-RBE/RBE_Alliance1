rbe-map-0907.zip — 2026-09-07
The public map's forecast-timeline pass, PLUS the five 2.5b backend files that did
not land on the repo last time.

Extract over the repo root. Paths mirror the repo. Nothing to delete, nothing to
rename, nothing to merge by hand.


================================================================================
A. FIRST — FIVE FILES FROM `rbe-2.5b.zip` NEVER MADE IT ONTO THE REPO
================================================================================

I checked your HEAD (`dc3bf31`) before starting this work. `frontend/index.html`,
`map/index.html` and all four test files from that delivery are byte-identical to
what I sent. These five are not:

    backend/config.py       MISSING ENTIRELY
    backend/main.py         still the 02 Sep version
    backend/db.py           still the 02 Sep version
    backend/conversions.py  still the 02 Sep version
    backend/network.py      still the 02 Sep version

Reproduced on a fresh clone of your HEAD:

    test_week1.py     AttributeError: module 'db' has no attribute 'init_config_db'
    test_phase45.py   AttributeError: module 'db' has no attribute 'init_config_db'
    test_tenant_audit.py                       23 passed, 2 failed

What it means on the live site right now:
  - the Config page loads and every call it makes 404s;
  - Edit on a route 404s (`PATCH /api/admin/routes/{id}` does not exist);
  - `/api/public/month-kpis` does not carry `trips_per_vehicle_day`, so the map's
    "Vehicles needed" card has nothing to divide by.

⚠️ THOSE FIVE FILES ARE IN THIS ZIP, unchanged from `rbe-2.5b.zip`. Extracting this
zip fixes all of the above as a side effect. They are listed separately here so you
know exactly what is being replaced and why — I have not touched them in this
session.

⚠️ Still true, and still the oldest item outstanding: THE EIGHT ACCESS CODES ARE NOT
SET ON RENDER. Until they are, `submitter123` / `planner123` / `admin123` work on the
live site and the per-IPT filtering built on 02 Sep does nothing. Render → service →
Environment → `IPT1_CODE`…`IPT6_CODE`, `PLANNER_CODE`, `ADMIN_CODE` → Save. Set them
all in one go; the first one you set stops the demo codes for everybody.

⚠️ And the one from last time that has not changed: on the first boot after this
deploys, `backend/factors.json` is copied into a `config` table row and the FILE
STOPS BEING THE LIVE COPY. Edit the file before deploying, or edit on the Config page
afterwards.


================================================================================
B. THE MAP WORK
================================================================================

Only `map/index.html` changed. No backend endpoint changed, no data file changed, and
the Rail Baltica alignment, the roads, Tark Tee and the zones are untouched.

1. THE EVR CORRIDOR MARCHES WHILE THE TIMELINE PLAYS
   A new `evr-rail-flow` overlay on the same source as the corridor: white ticks on
   the SAME dash index as the hauls' `forecast-flow`, so the two read as one movement
   rather than two animations at the same speed but a different phase.

   The rule is narrow on purpose: visible only when Play is on AND the EVR checkbox
   is on. Pause, scrub or close and it goes away, leaving the corridor exactly as the
   dim/bold rule drew it.

   It is its OWN layer rather than an animation of `evr-rail-line`, because that line
   carries the dim/bold state — colour, width, dash. Marching the same layer would put
   two things on one `line-dasharray` and the last write would win.

   The bold rule is unchanged: a railhead origin, or a railhead movement on screen.
   EVR is still not in `routes-source` and is still counted nowhere.

   ⭐ ONE THING I CHANGED THAT YOU DID NOT ASK FOR, and I think it is a correction:
   the "provisional geometry" caveat under the EVR checkbox used to appear ONLY in the
   un-bolded state. So the moment somebody highlighted a railhead — exactly when they
   were reading that line most closely — the caveat vanished. It is permanent now, and
   the highlight state sits under it. The click popup is unchanged and still carries
   the source, the accuracy note and the Lelle error.

2. THE KPI PANEL MOVED, AND SAYS WHAT THE MONTH IS DOING
   From `top:112px right:10px` — tucked under Mapbox's zoom and compass stack — to the
   top-left of the map pane, beside the sidebar. It reads first now instead of last,
   the whole right-hand column is left to the map controls, and there is room for:

     - a title: "Jul 2026 · what is moving";
     - the same four cards (movements/day, vehicles needed, material/day, routes);
     - the disciplines of THAT month as chips, e.g. `Earthworks 17 loads/d`, with
       material chips in a different colour when the month mixes materials.

   Sidebar closed, it moves to `left:14px top:58px` — BELOW the menu button, not beside
   it. The brief said `left: ~58px`; at that x it sits on top of "☰ Control Panel",
   which is 14px from the left and about 150px wide. I measured it in a browser rather
   than guessing, and put it underneath.

   On a phone it is a compact bar above the timeline: the four cards in ONE row, the
   note hidden, chips scrolling sideways. ⚠️ It and the warning stack were both at
   `bottom:74px` and drew on top of each other whenever a month carried a warning —
   pre-existing, not caused by this change. The warnings now sit at `bottom:190px`.

   An empty month shows dashes, not `0` routes. A zero read as a measured zero rather
   than as nothing approved.

3. ORIGIN / DESTINATION CALLOUTS ON THE ACTIVE MONTH
   Small labels on the two ends of every route carrying volume in the month on the
   playhead — role, site name, and its disciplines. They follow the playhead, they
   follow the filters, they clear when the timeline closes, and they are capped at six,
   busiest end first. Origins lift above the marker, destinations sit below it.

   They are SEPARATE popups added straight to the map. Nothing calls `setPopup()`, so
   the node markers' own click popups are untouched — a timeline tick cannot replace
   something you had open.


================================================================================
C. TWO DEFECTS FOUND BY RUNNING THE PAGE, NOT BY READING IT
================================================================================

Both of these passed every one of the 443 source-level assertions. They are the
argument for the new browser harness, so they are worth stating plainly.

1. THE CALLOUTS DREW ON TOP OF EACH OTHER. Kuusiku and Rapla are about 60 px apart at
   the default zoom, and their two boxes covered each other so NEITHER name could be
   read. A cap does not fix that — six boxes in one place is still six boxes.

   Fixed with a collision skip that tests the BOX, not the marker: comparing marker
   distance was the first attempt and was not enough, because two markers 108 px apart
   still overlap when the box is 200 px wide and one of them hangs downwards. The test
   is in screen space, so the callouts are re-placed on `moveend` — what collides at
   zoom 8 separates at zoom 11 and comes back.

   ⚠️ CONSEQUENCE, and you should know it: at a wide zoom a busy month will show FEWER
   than six callouts, and which ones survive is "the end most routes touch". That is
   deliberate. The alternative is boxes nobody can read.

2. "(1 LINE UNBAKED)" SURVIVED THE TIMELINE CLOSING. The vehicles label carries that
   caveat for the month it was computed for, and neither the no-month branch nor the
   empty-month branch reset it — so closing the timeline left the caveat sitting under
   a dash, about a month that was no longer on screen. Both branches reset it now.
   Pre-existing since 02 Sep; visible only because somebody looked at the page.


================================================================================
D. TESTS
================================================================================

All green:

    backend/tests/parse_map.js          443   (was 406)
    backend/tests/test_week1.py         303
    backend/tests/test_phase5a.py       215
    backend/tests/test_phase4.py        202
    backend/tests/test_phase2.py        160
    backend/tests/test_phase3.py        154
    backend/tests/test_phase45.py       140
    backend/tests/test_ipt_overlay.js   140
    backend/tests/test_phase25a.py      104
    backend/tests/parse_frontend.js     259
    backend/tests/render_frontend.js     28
    backend/tests/test_tenant_audit.py   26
    backend/tests/map_browser_check.js   31   NEW, and NOT in the default suite

⚠️ Two of those numbers only reach green with section A applied. `test_week1.py`,
`test_phase45.py` and `test_tenant_audit.py` fail on your current HEAD for the reason
in section A, not for anything to do with the map.

NEW: `backend/tests/map_browser_check.js`
  The public map in real Chromium with the REAL Mapbox GL JS. It cannot be in the
  default suite: `map/index.html` loads GL JS from a CDN the sandbox blocks, so the
  harness substitutes a local copy and needs `npm i mapbox-gl@2.14.1` first. The exact
  commands are in the file's header.

  The style URL is answered with a minimal offline style — version 8, one background
  layer, no sources, no sprite, no glyphs. So the real GL JS runs, layers really are
  added, filters really are validated, and `mapboxgl.Popup` really renders, which is
  what makes the callouts testable at all.

  It is the only thing in the project that can see WHERE something is. It measures the
  panel against the sidebar and against the zoom controls, measures the rendered
  callout rectangles against each other, and checks the phone layout by RELOADING at
  390px rather than resizing (the page closes the sidebar at load time, so a resized
  desktop page shows a layout no phone user would ever see).

  ⚠️ What it still does not prove: the basemap is blank, so nothing about legibility
  over real tiles is tested; the API is fixtures; the geometry is four invented routes,
  not the 107-route network; and no month has ever been played against a real backend.

REGRESSIONS RUN (each broke the build, then was reverted):
  ten against the source assertions — the flow layer joining the checkbox trio, the
  Play-AND-checkbox rule loosened to Play alone, the empty month printing a zero again,
  the callouts losing their own-popup class, something inserted between
  `applyZoneMonth(null)` and `applyFilters()`, the rail marching after Play stopped,
  the rail given its own out-of-phase dash cycle, the caveat going back to
  dim-state-only, the callouts not cleared before a rebuild, and the overlay losing the
  railhead narrowing. All ten failed the assertions they should and no others.

  Two against the browser harness. The first (panel back in the old corner) failed
  correctly. 🔴 The second — removing the collision skip — failed NOTHING, which is how
  I found that no assertion actually checked the callouts do not overlap. There is one
  now, and it measures the rendered rectangles rather than the code that places them.

THE ASSERTIONS THE BRIEF ASKED ME TO KEEP, all still passing:
  - `toggleRail` still contains the exact trio
    `'evr-rail-casing', 'evr-rail-line', 'railheads'` — the flow layer is toggled on the
    next line instead, because joining that list would let the checkbox alone switch it
    to visible and it would march on a paused map;
  - exactly FIVE `'line-cap': 'butt'` strings. The flow layer sets no line-cap at all:
    Mapbox's default is already butt and `forecast-flow` relies on the same default, so
    a sixth explicit string would have moved that count without anything changing about
    the dotted routes;
  - `applyZoneMonth(null);` immediately followed by `applyFilters();`
  - `applyFilters();` immediately followed by `renderTimelineWarnings(m);`

The one assertion that was REVERSED rather than deleted: `#kpi-hud` pinned at
`top:112px right:10px`. That was exactly the position this pass exists to leave, so it
now pins the new corner and refuses the old one.

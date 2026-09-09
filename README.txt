rbe-map-review-0908pm.zip — 2026-09-08 (afternoon)
Your review of the morning's map slice. Three files. Extract over the repo root;
paths mirror the repo. Nothing to delete, nothing to rename, nothing to merge.

    map/index.html
    backend/tests/parse_map.js
    backend/tests/map_browser_check.js

No backend file changed. No endpoint changed. No data file changed. The Rail
Baltica alignment, the roads, Tark Tee, the zones and the staff app are untouched.

⚠️ STILL OUTSTANDING AND STILL THE OLDEST ITEM: THE EIGHT ACCESS CODES ARE NOT SET
ON RENDER. Until they are, submitter123 / planner123 / admin123 work on the live
site and the per-IPT filtering does nothing. Render -> service -> Environment ->
IPT1_CODE…IPT6_CODE, PLANNER_CODE, ADMIN_CODE -> Save. Set all eight in one go;
the first one you set stops the demo codes for everybody. Six days now.


================================================================================
1. THE THING YOU SPOTTED WAS A REGRESSION I INTRODUCED YESTERDAY MORNING
================================================================================

"I would like all icons to sit on top of the alignments and routes."

You were right, and it is worse than it looks from the sidebar. I measured the
layer stack in Chromium. NINE layers were drawing over the location marks:

    rail-alignment-survey, rail-alignment-underlay, rail-alignment,
    chainage-global, sel-glow, sel-core,
    forecast-casing, forecast-layer, forecast-flow

A railhead with two routes converging on it was about 70% covered by the 6 px
forecast casing — the mark was a sliver of teal above a blue line. The routes
converge exactly ON the nodes, so the marks were most hidden precisely where they
matter most.

WHY IT HAPPENED, because it is the interesting part. Until yesterday the sites
were DOM mapboxgl.Markers. A DOM marker is an HTML sibling of the WebGL canvas,
so it is above every layer unconditionally and for free — nobody ever had to
think about it. Moving them into the canvas as a symbol layer bought the seven-
type taxonomy and unblocked PNG export, and silently cost that guarantee. Layer
order in Mapbox is ADD order, and several of the layers above are added LAZILY
(the forecast layers when you tick the box, the zone and restriction layers when
you open them), so there is no single place to insert before.

Fixed with raiseMarks(), called from every lazy adder and from applyFilters(),
which runs after every user action. It is guarded: if the mark layers are already
last, in order, it returns without touching the style, because moveLayer forces a
style recalculation and applyFilters runs on every keystroke in the filter row.

⚠️ The assertion that would have caught this did not exist. Draw order has no
textual signature, so parse_map.js cannot see it — it is in the browser harness
now, and it names the specific layers, so a future reorder says which one moved.


================================================================================
2. THE MARKS
================================================================================

QUARRY — BACK TO A CIRCLE. The pick is drawn as vector rather than the old 9 px
  emoji character, so it is identical on every machine and sharp at every zoom.
  The three material fills are unchanged: sand gold, limestone white, everything
  else sienna.

  ⚠️ THIS REVERSES A RULE I WROTE YESTERDAY, deliberately and at your request.
  The morning slice said "shape discriminates, not colour — two types never share
  a mark", because a colour-blind reader, a greyscale print and a satellite
  basemap all lose colour before shape. Five of the seven types are circles now
  and the INNER SYMBOL discriminates instead: a pick, a dot, a pile, an anchor,
  nothing. Railhead and Compound stay squares, so the two types that sit ON the
  corridor are still separable by silhouette alone. Your call, recorded as yours.

  ⚠️ It took three attempts and the two failures are worth knowing about, because
  both rendered without error and both were wrong at a glance: v1 (handle
  overshooting a shallow arc) read as a figure 2; v2 (a near-flat arc over a
  near-vertical handle) read as a letter T. The head is now built FROM the handle
  vector so the two cannot drift apart again.

STOCKPILE — A GAUGE, as you asked: a circle, and the pile inside it in a creamy
  grey, filled to its recorded stock level.

  The level comes from stock_balance / capacity_qty — the same two numbers the
  popup already states in words, so the picture and the text can never disagree
  about one pile. Over capacity, the pile goes the same red the Clash warning
  uses. No new endpoint, no new data, nothing stored.

  Two decisions inside it you should know about:

  - The level is QUANTISED into six steps (0/20/40/60/80/100) plus over plus
    "unmeasured". Each distinct mark is a separate image in the map's atlas, and
    an image per exact percentage is an unbounded set.

  - ⚠️ A PILE WITH NO RECORDED CAPACITY IS DRAWN WITH A DASHED OUTLINE. Without
    that, an unmeasured pile and a 90-100% full pile are the same picture — both
    solid to the top — and the map would be claiming a reading it does not have.
    A dashed pile means "nobody has told this system how big this pile is",
    which is a real state, not a zero.

  ⚠️ ONE THING I HAD TO GET WRONG FIRST: the boundary between the filled and empty
  parts is an INK RULE, not just a change of tone. Creamy grey on cream is a few
  percent of luminance apart, and at map size the first version had no visible
  fill line at all. The rule is what makes the level readable.

COMPOUND — a solid site cabin instead of the thin outline square with a gap for
  the gate. That outline vanished below about 22 px, so at corridor zoom a
  compound was an empty amber square. Solid white shapes survive small sizes;
  thin white lines do not.

  ⚠️ The compound's amber (#CA8A04) is still a colour I CHOSE, not one I inherited
  — there is no "compound sand" anywhere in this codebase, and the colour that was
  there before was the cyan default that ports were also falling through to, i.e.
  the bug itself. Say the word and it changes in one place.

PORT and SITE are untouched, as you asked. RAILHEAD and OTHER are untouched.


================================================================================
3. "CAN WE GET THE ICONS TO HIGHLIGHT WHEN IN USE ON THE FORECAST TIMELINE?"
================================================================================

They already did, and you could not see it. That is the finding, not the feature.

Yesterday's treatment was "+2 px with a 1 px halo". Measured in the browser, that
is 26 -> 34 CSS px across, and it only reads if an UNUSED mark happens to sit
right beside a used one. In a busy month almost every mark is in use, so there is
nothing to compare against and the emphasis is invisible. I built exactly what
the brief asked for and it did not do the job.

Replaced with two things:

  1. A WHITE RING with a dark rim, drawn OUTSIDE the mark. It reads on the Light
     basemap and on satellite, and it lives in the image's margin so the mark
     itself does not change size — nothing jumps when a month is scrubbed.

  2. EVERYTHING NOT IN USE DROPS TO 34% while a month is on screen. This is the
     half that actually works in a busy month: contrast, not size. It goes back to
     solid the moment the timeline is closed.

The dimming is an icon-opacity expression on the layer rather than a second set
of images, because "is this month on screen" is a property of the SCREEN, not of
the mark — baking it in would double the atlas for every type.

⚠️ Both halves are driven by the same flag the ring is, so they cannot disagree
about whether a month is being shown.


================================================================================
4. TESTS
================================================================================

    backend/tests/parse_map.js          484   (was 465)
    backend/tests/map_browser_check.js   58   (was 47)   NOT in the default suite
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

THREE ASSERTIONS REVERSED RATHER THAN DELETED, all three pinning behaviour that
turned out to be the thing you were objecting to:
  - "+2 px with a halo, baked into the -on image" now pins the ring AND the
    dimming, and REFUSES the size-only treatment coming back;
  - the quarry diamond now asserts the circle and refuses the diamond path;
  - the compound outline now asserts the solid cabin and refuses strokeRect.

⚠️ TWO OF MY OWN NEW ASSERTIONS WERE MIS-TARGETED, and one of them could never
have failed. parse_map.js strips // comments out of `code` before matching, so
`!/\/\/ diamond/.test(code)` was vacuously true no matter what the file said. It
tests the PATH now, and the comment marker is grepped in `src`. Worth recording
because a green assertion that cannot fail is worse than no assertion.

FOURTEEN DELIBERATE REGRESSIONS RUN (each broke the build, then was reverted).
The split is the point:

  TWELVE were caught by the source assertions — the marks back under the routes,
  the no-op guard inverted, the +2 px treatment restored, the dimming applied
  always, an unmeasured pile drawn as a measured one, the stock rule removed, the
  backend's `over` ignored, the level dropped from the image key, the quarry back
  to a diamond, the pick's head angle hard-coded, the compound back to an outline,
  and the ring's radius changed.

  🔴 TWO WERE CAUGHT ONLY BY THE BROWSER, and neither has any textual signature:
    - the image canvas shrunk from 72 to 62 px, so the ring runs off the edge and
      renders as a broken arc. Every source regex still passed;
    - the bucket arithmetic wrong by a factor of ten, so a 42%-full pile draws as
      empty. Every source regex still passed.

⚠️ AND A PROCESS FAILURE ON MY SIDE, because it nearly put a wrong number in this
file. My regression harness held the original file in memory and restored it at
the end. Two runs were killed by a timeout between applying a regression and
reverting it, and both left the working tree broken — so the NEXT run measured
every case against an already-regressed baseline and reported plausible-looking
numbers. Caught it by grepping the tree afterwards. The harness restores with
`git checkout` now and refuses to start on a dirty tree.

⚠️ WHAT IS STILL NOT TESTED
  - THE BASEMAP IS BLANK. The harness answers the style URL with an offline stub,
    so nothing here has been seen over real satellite or real Light tiles. That
    matters more than it did yesterday: the stockpile's cream, the quarry's
    limestone white and the ring's white all have to hold against real imagery,
    and I cannot see that from here. Please look at the deployment.
  - The API is fixtures. The stock gauge has been exercised against three invented
    piles (over / part-full / unmeasured), never against your real capacities.
  - The geometry is four invented routes, not the 107-route network. I have not
    seen what the ring and the dimming look like when 40 marks are on screen.
  - buildings-3d has still only ever been REFUSED, never added: the offline style
    has no `composite` source, which is exactly what its guard checks.

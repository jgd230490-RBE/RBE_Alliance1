rbe-ipt-overlay-v4.zip — palette rebuilt on measurement
2026-08-28. Extract over the repo root.

SUPERSEDES rbe-ipt-polish.zip AND rbe-ipt-overlay-v3.zip. Same four files, later
versions. Apply AFTER rbe-ipt-overlay.zip. No overlap with rbe-phase45-tenant.zip.

ONLY the palette changed from v3. The band splitter, gap bridges, IPT 6 underlay,
per-IPT checkboxes, survey layer and chainage ladder are all as delivered in v3.

FILES (4, all replacements)
  map/ipt_segments.js
  map/index.html                     (untouched from v3 - included so the zip is whole)
  backend/tests/parse_map.js         206 -> 212 assertions
  backend/tests/test_ipt_overlay.js   93 assertions

=====================================================================
WHY THE PALETTE WAS REBUILT
=====================================================================
You said the colours were too similar, especially the ones next to each other.
Measured with CIE dE2000 (~1 = a just-noticeable difference; two 2.5px lines on a
light basemap need roughly 20+ to read as different colours):

                              as specified     rebuilt
  weakest adjacent pair             6.3          22.1
  weakest pair anywhere             5.7          21.0
  underlay vs the colour on it      0.0          34.5
  weakest adjacent, colour-blind    2.0          21.5

That last row decided it. Simulated for deuteranopia and protanopia, three
CONSECUTIVE bands of the first palette were dE 2.0 apart - the same colour, not
a similar one. And the underlay was dE 0.0 from IPT 6 because they were the same
hex, so it could never show beneath the band it was supposed to sit under.

I also under-reported this the first time. My earlier note said dE 10.7 and 17.2
using CIE76; dE2000 is the better model and gives 6.3 and 6.4. Five of the seven
adjacent pairs were too close, not two.

THE REBUILT PALETTE

  IPT 6       #86198F  plum          L* 33
  IPT 1       #7F1D1D  oxblood       L* 28
  IPT 2       #78716C  stone         L* 48
  IPT 3       #854D0E  bronze        L* 38
  IPT 4       #155E75  teal blue     L* 37
  IPT 5       #166534  pine green    L* 37
  Outside A1  #94a3b8  slate         L* 66   (unchanged)
  underlay    #C4B5FD  lavender      L* 77   (a light wash, not a 7th deep colour)

Six different hue families, one per band, so the LEGEND reads as six things
rather than two purples and two greens. The underlay separates by LIGHTNESS
rather than competing on hue - that is what lets it show beneath a dark band.

Muted engineering tones throughout: no neon, nothing brighter than the route
layers, and it still reads as an infrastructure map rather than a chart.

** 21.0 IS THE CEILING, NOT A COMPROMISE NOBODY TRIED TO BEAT. **
Seven hexes are already spent on inbound, outbound, temporary haul, forecast,
selection, brand navy and brand dark. Searched exhaustively over a bank of 28
corporate tones against all four constraints at once - every pair apart, every
adjacent pair further apart, colour-blind safe, and clear of every line the map
already draws. Nothing scores higher. If a band ever needs more separation than
this, the way to get it is to free up a reserved colour, not to re-shuffle these.

ALSO FIXED: the #6D28D9 collision is gone, not just documented. No band colour
now matches anything the codebase uses for anything else.

CLOSEST REMAINING APPROACHES - acceptable, but worth knowing
  Outside A1 vs forecast          dE 15.4   both muted; Outside A1 predates this
  IPT 1      vs selection/zone    dE 17.1
  IPT 4      vs brand navy        dE 17.8   navy draws node markers, not lines
  IPT 3      vs temporary haul    dE 19.5   temp haul is DASHED, which separates
                                            it independently of colour

TO ACTION
  Nothing by hand. No data file touched.

UNVERIFIED
  No browser in this sandbox. The delivered PNG is built from the real GeoJSON
  without Mapbox, showing chainage 93-146 km where the corridor changes package
  six times - old palette left, rebuilt right. Judge it on the real basemap;
  every colour is one line in window.IPT_SEGMENTS.

TESTS
  node backend/tests/parse_map.js         212 passed
  node backend/tests/test_ipt_overlay.js   93 passed
  Full suite 1,150 assertions, 0 failed.

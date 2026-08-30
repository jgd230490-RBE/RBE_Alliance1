rbe-ipt-overlay-v6.zip — the three reported faults, and a guard so they cannot recur
2026-08-30. Extract over the repo root.

SUPERSEDES the polish zip, v3, v4 and v5.

*** EXTRACT BOTH FILES. That is the whole of fault (1) and (3). ***

FILES (4, all replacements)
  map/ipt_segments.js                version stamp, no_surveyed_track flag
  map/index.html                     version handshake, bridges removed, font fix
  backend/tests/parse_map.js         250 assertions
  backend/tests/test_ipt_overlay.js  140 assertions
  Full suite 1,235 assertions, 0 failed.

=====================================================================
DIAGNOSIS - your three symptoms are ONE cause
=====================================================================
  "IPT 6 green underlay still exists"
  "the transparent line is still there"
  "work section ticks/labels haven't worked, only the filter appears"

map/index.html was at v5 and map/ipt_segments.js was still the POLISH build.

Every delivery ships both files and they are a matched pair. Update one without
the other and the map does not error - it half-works, silently:

  - IPT 6 green      -> the polish build still had the ORIGINAL palette. v4
                        made IPT 6 plum #86198F and the underlay lavender.
  - transparent line -> gap bridges came in the polish build. v5 still had them.
  - WS checkbox with -> v5's index.html draws the tick layers and adds the
    nothing behind it    checkbox, but they read window.buildWsBoundaries, which
                         only exists from v5's ipt_segments.js. Missing function
                         -> empty FeatureCollection -> a toggle over nothing.

VERIFY IN ONE LINE. Open /map/, then in the browser console:

    window.IPT_SEGMENTS_VERSION

  "v6"       -> the data file is current
  "v5"/older -> that file did not get replaced
  undefined  -> it is older than v5

From v6 the two halves check each other on load. A mismatch prints a console
error AND puts a red line in the sidebar naming which file is stale. This class
of confusion should not cost you a debugging session again.

=====================================================================
(2) THE TRANSPARENT LINE IS GONE - and what replaced the message
=====================================================================
The 30%-opacity gap bridges are removed.

** CONSEQUENCE, and I argued the opposite two days ago, so it is only fair to
   say it plainly: the corridor now reads DASHED again. ** 31 stretches over
300 m have no Main Track geometry in alignment.js - about 60 km in total, the
largest 7.0 km. Every one is now a visible break in the line.

What was KEPT is the information, which is what the bridges were really for:

  - The three package boundaries that fall in a hole now say so ON THEIR LABEL:
    "WS1 | WS2 / 117+278 / no surveyed alignment here".
  - Those ticks are drawn in a lighter grey, so the caveat is visible before you
    read anything.
  - Clicking a tick gives the full explanation and the measured distance to the
    nearest drawn track (1,377 m at 117+278; 708 m at 142+000; 448 m at 135+400).
  - The sidebar note now reads "Breaks in the line = no surveyed Main Track in
    the alignment file, not a rendering fault."

The flag is MEASURED at build time from the real geometry, not hard-coded, so a
better alignment file changes the answer instead of leaving a stale warning.

** THE GEOMETRY CUT IS UNCHANGED. ** GAP_SPLIT still removes the 239.5 km of
phantom straight lines. That is a separate and older decision - do not undo it to
make the line look continuous again, or the map goes back to drawing 45 km of
railway that does not exist.

=====================================================================
(3) A SECOND REASON THE TICKS MIGHT NOT HAVE DRAWN - now removed
=====================================================================
The v5 tick layer specified 'text-font': ['Open Sans Bold', 'Arial Unicode MS
Bold']. It was the ONLY symbol layer on this map with an explicit font, and a
fontstack the style cannot serve makes a symbol layer render NOTHING, silently.
Removed - it now inherits the style default like every other label here.

So even on a correctly matched pair, v5's ticks may not have drawn. Both causes
are fixed; if they still do not appear, tell me and I will take the next step
rather than guess again.

TO ACTION
  Nothing by hand. No data file touched.

STILL UNVERIFIED
  No browser here. Check: ticks perpendicular and not clipped; labels not
  colliding at zoom 13-14; the three lighter ticks legible; no red version
  warning in the sidebar.

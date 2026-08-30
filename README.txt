rbe-ipt-overlay-v5.zip — WS boundary ticks + work-section popup
2026-08-30. Extract over the repo root.

SUPERSEDES rbe-ipt-polish.zip, v3 and v4. Same four files, later versions.
Apply AFTER rbe-ipt-overlay.zip. No overlap with rbe-phase45-tenant.zip.

FILES (4, all replacements - nothing new, nothing to delete)
  map/ipt_segments.js                WS_NAMES, ws_primary, WS_BOUNDARIES, chainText
  map/index.html                     tick layers, toggle, rebuilt popup
  backend/tests/parse_map.js         212 -> 250 assertions
  backend/tests/test_ipt_overlay.js   93 -> 135 assertions
  Full suite 1,230 assertions, 0 failed.

=====================================================================
READ FIRST - the WS table in the spec reproduces a known error
=====================================================================
The names came from the IPT Matrix. claude/roadmap.md marks that document
"second-hand AI output, superseded", and claude/scope-diagram.md - derived from
the real Appendix E - corrects it. Two of those corrections matter here:

1. ** WS13 (Urge halt) is IPT 2, not IPT 1. **
   ipt-matrix.md lists "WS 13 (Urge) -> IPT 1" in its OWN table of known errors,
   and the scope diagram shows the ITP 2 band directly beneath Urge. The spec
   table repeats the error.

   Worse: the FIRST overlay delivery inherited it. IPT 1's legend row has been
   reading "WS1, WS12, WS13" since 2026-08-28. Fixed: IPT 1 is WS1, WS12 and
   IPT 2 is WS2, WS13. That is my bug, not the spec's, and it was shipped.

2. ** WS14 / WS15 ownership is NOT settled, so it is not asserted. **
   The spec says IPT 6 for both. The scope diagram draws NO IPT band beneath
   them, and "Which IPT owns WS 14 & 15?" is open question A3.2 - one of the
   three Appendix E questions still blocking the rest of Phase 2. Both rows
   carry ipt: null and provisional: true rather than a guess.

Also recorded, not resolved:
  - WS3's official name "Timmermanni to Papiniidu" overlaps WS2's extent
    ("Timmermanni to Orasselja"). The scope diagram labels that stretch
    "Rääma bog". Both are on the row; the popup shows the official name with
    the diagram's label as a footnote.
  - The diagram subdivides WS7 into WS 7.1-7.4 along the alignment. This overlay
    carries a single WS7, so the popup says WS7 for the whole northern band.
    A simplification, not a finding.

=====================================================================
WHAT WAS BUILT
=====================================================================
1. POPUP on the civil IPT alignment
   IPT, the work section that OWNS that chainage band, its official name, and
   the chainage range as an engineer writes it - "105+480 - 117+278", not
   "105.48 km". Point assets on the same ground (a station, a halt) are listed
   separately as "also on this ground" rather than presented as owners.

   The underlay line appears ONLY when the IPT 6 underlay is actually on and the
   stretch is inside A1 scope. A provisional band says so in the popup. A
   disputed WS name carries its dispute.

   Handler wired ONCE, as before - a basemap switch cannot stack popups.

   NOT DONE, deliberately: the "emphasise the clicked segment" option. Mapbox
   feature-state needs a feature id, and this source has none; adding ids means
   touching the builder, and the spec marked it optional. Say the word.

2. SEVEN BOUNDARY TICKS at 105480, 117278, 125000, 130036, 135400, 137685,
   142000. Neutral slate #334155 - not a route colour and not a package colour,
   because a tick is neither. Ticks from zoom 11, labels from zoom 13, one
   toggle for both, on by default.

   They are a SYMBOL layer with a rotated glyph, not a line. A fixed ground
   length would be one pixel at corridor zoom and half the viewport at zoom 16;
   a rotated glyph turns with the map and stays a constant size on screen.

   ** A BUG WORTH KNOWING ABOUT, since I wrote and then caught it. **
   The first version took each tick's bearing from the two chainage markers
   bracketing the boundary. Four of the seven boundaries land exactly on a 100 m
   marker, which makes that span zero - and the code then silently reused the
   PREVIOUS tick's bearing. Three of the seven ticks were rotated to a different
   stretch of railway. Now the bearing is measured from the markers either side,
   widened by one on an exact hit, and the test asserts all seven bearings are
   distinct.

3. ** THREE OF THE SEVEN TICKS SIT ON A GAP BRIDGE, NOT ON SOLID TRACK. **
   Measured: 117+278 is 1,377 m from the nearest solid Main Track, 142+000 is
   708 m, 135+400 is 448 m. All seven are within 68 m of the corridor once the
   faint bridges are counted.

   The ticks are right. chainage.js covers the corridor continuously (2,180
   markers, all Main Track); alignment.js does not - it has ~60 km of holes,
   which is open question H2. So those three boundaries fall where there is no
   surveyed line to draw and their ticks land on a 30%-opacity bridge.

   Flagging it because on the live map it will look like a rendering fault.

TO ACTION
  Nothing by hand. No data file touched.

UNVERIFIED
  No browser in this sandbox. The tick glyph is 'Open Sans Bold' '|' rotated
  with text-rotation-alignment 'map'; the rotation maths is asserted but how the
  glyph SITS on the line is not - check the ticks look perpendicular and are not
  clipped, and that the labels do not collide at zoom 13-14.

STILL OPEN (unchanged)
  - WS2/WS3 at 125+000 is approximate - now said in the popup as well
  - ~60 km of Main Track missing from alignment.js - see item 3 above
  - Two multi-km single-edge Main Track features - real, or placeholders?
  - WS14/WS15 IPT ownership - open question A3.2

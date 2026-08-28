rbe-ipt-polish.zip — IPT overlay visual polish
2026-08-28. Extract over the repo root.

APPLY AFTER rbe-ipt-overlay.zip. It replaces two of that zip's four files.
No file overlap with rbe-phase45-tenant.zip; order between the two does not matter.

FILES (4, all replacements — nothing new, nothing to delete)
  map/ipt_segments.js               gap bridges + chainage step tiers
  map/index.html                    three alignment layers, zoom-synced chainage
  backend/tests/parse_map.js        126 -> 165 assertions
  backend/tests/test_ipt_overlay.js  57 -> 81 assertions

WHAT CHANGED
  1. Gap bridges. Every edge the gap splitter cuts is now KEPT as a separate
     feature, flagged is_bridge, drawn solid at 30% opacity in the band's own
     colour. The geometry cut is unchanged - GAP_SPLIT still removes the phantom
     straights from the solid track. Solid 407.2 km + bridge 239.5 km = 646.7 km,
     which is the source file exactly. Nothing invented, nothing lost.

     A bridge can be 45 km long, longer than five of the seven IPT bands, so
     bridges are densified every 200 m and band-split like everything else. 272
     cut edges produce 279 bridge features because seven of them cross a band
     boundary and change colour.

  2. IPT layer is Main Track ONLY, at ONE width (2.5 px). Side tracks,
     crossovers and 1520 mm were 471 of the 531 source features and clustered at
     stations, turning every node into a blob of package colour. They are still
     in the data and the survey layer draws them.

  3. New layer: "Alignment (survey, continuous)". The original presentation -
     raw alignment_data, every track type, black 1.5 px, nothing cut. Its own
     source so the IPT cuts cannot reach it, its own checkbox, OFF by default.

     ADD ORDER IS Z-ORDER: survey, then bridges, then solid IPT track.

  4. Chainage markers are zoom-synced. Each point carries a step_m tier and the
     map draws 24 markers at corridor zoom instead of 2,180 - a 99% reduction -
     densifying to 45 at zoom 10, 218 at zoom 12, all 2,180 at 13.5. Labels are
     off entirely below zoom 10.

     Mapbox GL cannot use ['zoom'] inside a layer filter, so the tiering is in
     the paint/layout expressions instead. Both layer ids are unchanged, so the
     existing checkbox still works.

  5. Clicking a bridge explains itself: "No surveyed track here." Someone
     looking at a fainter line needs an answer more than someone looking at a
     solid one.

TO ACTION
  Nothing by hand. No data file is touched; factors.json is not included.

UNVERIFIED
  No browser in this sandbox, so Mapbox rendering, the legend's appearance and
  the zoom transitions are unrun. A PNG rendered from the built GeoJSON without
  Mapbox was delivered with this zip; it is as close as the sandbox gets.

  Worth a look on the live map: IPT 1 navy (#003787) and IPT 3 slate (#475569)
  are close in tone, as are IPT 6 (#039E86) and IPT 2 (#0E7490). They are never
  adjacent along the corridor so it reads fine in the preview, but if they are
  hard to tell apart on the real basemap the colours are yours to change - one
  line each in window.IPT_SEGMENTS.

STILL OPEN (unchanged)
  - WS2/WS3 global bound at 125000 is approximate. Refine after the visual check.
  - ~60 km of Main Track missing from alignment.js - whose file is it?
  - Two multi-km single-edge Main Track features - real, or placeholders?

TESTS
  node backend/tests/parse_map.js          165 passed
  node backend/tests/test_ipt_overlay.js    81 passed
  Full suite 1,091 assertions, 0 failed.

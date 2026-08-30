rbe-ipt-overlay-v7.zip — solid line everywhere, and the work-section popup fixed
2026-08-30. Extract over the repo root. SUPERSEDES every earlier overlay zip
except rbe-ipt-overlay.zip itself, which you still apply first.

*** BOTH FILES. Console check after loading /map/:  window.IPT_SEGMENTS_VERSION
    should return "v7". Anything else means one file did not get replaced. ***

FILES (4, all replacements)
  map/ipt_segments.js
  map/index.html
  backend/tests/parse_map.js         257 assertions
  backend/tests/test_ipt_overlay.js  140 assertions
  Full suite 1,242 assertions, 0 failed.

=====================================================================
1. THE LINE IS SOLID EVERYWHERE NOW
=====================================================================
The connectors across the unsurveyed stretches are back, drawn at FULL opacity,
same width and same band colour as real track. The corridor reads as one
continuous line whether or not the survey covers it.

The notice stayed where you asked for it: click any stretch and the popup says
whether there is surveyed alignment beneath it, and if not, that the line is
reference only and nothing is measured or routed on it.

** The trade, stated once so it is on the record: the map now draws railway on
   ground the survey does not cover, and nothing on the line itself
   distinguishes the two. ** That is a deliberate choice - the alternative was
   60 km of visible breaks that read as a rendering fault. It does mean the
   popup is the ONLY place the distinction survives, so it must not be removed
   from there.

The underlying geometry cut is UNCHANGED. GAP_SPLIT still strips 239.5 km of
phantom straight lines out of the data; these connectors are separate features
flagged is_bridge, and they are presentation only. Nothing measures them.

The IPT 6 underlay now bridges the gaps too - otherwise it would have shown as
a dashed wash beneath a solid line, which looks like a fault.

=====================================================================
2. THE WORK SECTION POPUP - a real bug, and I wrote it
=====================================================================
You saw, on IPT 6:

    Work section: none - outside A1 mainline scope
    also on this ground: WS7, WS8, WS9, WS10, WS11

That is self-contradicting and it was two faults meeting:

  (a) The data file on the deployment was older than the one index.html
      expected, so ws_primary was never stamped on the features. The version
      banner added in v6 catches that now.

  (b) ** My popup read "no ws_primary" as "outside A1". It is not. ** Outside A1
      is one specific band at the far end of the corridor; every other empty
      value means the stamp is missing, which is a completely different
      sentence. That was my error, not a consequence of (a).

Both fixed:

  - The popup now DERIVES the owning work section from the band table when the
    feature does not carry it. The band table is in the same file as the popup
    code, so this works even against an older data file - it no longer depends
    on the two being in step.
  - "none - outside A1 mainline scope" is now said ONLY for the Outside A1 band.
    A genuinely missing stamp says "not recorded" in red and points at the
    version banner, rather than inventing a confident wrong answer.

On IPT 6 you should now see:
    Work section: WS7 - Superstructure
    also on this ground: WS8, WS9, WS10, WS11

TO ACTION
  Nothing by hand. No data file touched.

STILL UNVERIFIED
  No browser here. Worth checking: the corridor reads continuous end to end;
  clicking an unsurveyed stretch gives the amber notice; IPT 6 reports WS7; the
  seven boundary ticks appear from zoom 11 with labels from 13; no red version
  banner in the sidebar.

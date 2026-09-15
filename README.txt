RBE Alliance 1 — rbe-help-screenshots-0915.zip
==============================================
Cut 2026-09-15 against repo HEAD cf53c81. Twenty-one files, all of them images.
No code. No schema. No dependency. Nothing in backend/, frontend/index.html or map/.

WHAT IT DOES
============
Replaces 21 of the 22 placeholder figures in frontend/help/media/ with your real
screenshots. E32 — "the help page ships 22 placeholder images" — is all but closed.

Same filenames, so nothing in frontend/help/index.html changes and no caption moves.
Extract over the repo root and the guide is illustrated.

  M01  the corridor with the control panel open
  M02  the alignment popup — IPT 5 / WS5, chainage 135+800–137+000
  M03  the stockpile gauge — Stock Pile 2, 40 000 / 88 154 t
  M05  a road restriction popup — 10 t mass limit, Väljataguse tänav
  M06  the timeline playing — Sep 2026, 101 movements/day
  M07  the Estonia orthophoto under Soodevahe CB
  S01  the sign-in screen  ** post-deploy: no demo codes on it **
  S02  the Dashboard, big screen — KPI tiles and the week map
  S03  the Dashboard, lower half — charts and the stockpile capacity chart
  S04  Submit forecast
  S05  the Forecasts ledger
  S06  Commit view — KPI strip, clash rail, Mon–Fri grid
  S07  the same with a row expanded and the week map under it
  S08  Account — planned, actual, variance, cost, Stockpiles
  S09  Horizon — W1–W5 with the band labels
  S10  the Confirm week dialog
  S11  Locations — editing C11 Stock Pile 1, capacity 124 118 t
  S12  Routes — R001 expanded
  S13  Zones — the haul road HR01
  S14  Config — the Vehicles tab
  S15  the supplier PDF

M08 (Street View) IS NOT IN THIS ZIP and is still a placeholder. There is nothing to
photograph until GOOGLE_MAPS_API_KEY is set on Render. Shoot it after that and it is a
one-file follow-up.

WHAT WAS DONE TO THE IMAGES
===========================
Resized from ~1915 px to 1440 px wide — the width the placeholders were drawn at, so the
page layout is unchanged — and reduced to a 256-colour palette with Floyd–Steinberg
dithering. That is 9.9 MB of raw captures down to 3.3 MB.

Checked, not assumed: a text-dense crop of S14 (the Vehicles table) and a crop of the
M07 aerial were rasterised and looked at. Table text is crisp; the orthophoto shows no
banding. The page loads all 22 at once, so the size mattered.

VERIFIED
========
- 3,063 passed / 1 failed — unchanged from HEAD. No assertion moved, because no code did.
- 32 passed / 0 failed in REAL headless Chromium (help_browser_check.js).
- Every one of the 27 <img> on the guide decodes: forced eager, awaited decode(), all 27
  report naturalWidth 1440. Zero broken. Then the rendered page was screenshotted and
  looked at — Figure 10 shows the real Commit view.

ACTIONS FOR YOU
===============
[ ] Extract over the repo root. Nothing renames; nothing needs deleting from this zip.
[ ] STILL OUTSTANDING, AND THIS IS THE LAST RED ASSERTION IN THE SUITE:
    delete frontend/help/media/placeholder.pl in the GitHub web UI. A zip cannot delete
    a file. test_help goes 39/1 -> 40/0 the moment it is gone. It has been the one
    failing assertion since 10 September.
[ ] Set GOOGLE_MAPS_API_KEY on Render, then shoot M08.

THREE CAPTIONS STILL DO NOT MATCH THEIR PICTURE — LEFT ALONE ON YOUR INSTRUCTION
================================================================================
You said to leave S03 and S13 for now and keep them in open questions. M05 turned out to
be the same class of problem and is left alone for consistency. All three are recorded in
claude/open-questions-0915.md (Q1). Each is a one-line edit to frontend/help/index.html
whenever you want them:

  S03  caption says "The Stock held panel at the foot of the Dashboard". That panel was
       deleted on 10 Sep (C22). The picture correctly shows the capacity chart that
       replaced it.
  M05  caption says "a road restriction AND ITS VERDICT FOR THE VEHICLE". The public map
       cannot show a verdict — restrictionPopupHTML() prints the limit, road, km range,
       dates and source, and the public map has no vehicle selected. The verdict lives on
       the Look-ahead's clash rail, already visible in S06 and S07.
  S13  caption says "a disruption zone AND a haul road". There is only the haul road;
       the page reads "1 zones". Draw a disruption zone, or reword.

Two more, minor, not raised as open questions: S11 and S12 are correct captures whose
right-hand panel runs below the fold. S11 shows the stock capacity but not the gate block;
S12 shows R001's vehicles, gates and haul roads but not the analysis, alternatives,
planning block or Tark Tee rows its caption lists. Usable. A taller browser window would
match the captions exactly.

NOT TESTED
==========
- The guide was rendered from the local file, not from the deployment. The /help/ route is
  behind the staff sign-in, so confirm it there after uploading — one page load.
- Nobody has read the guide end to end with the real pictures in place. The assertions
  check that images decode and that the text is present; they cannot tell you a figure is
  under the wrong heading. Worth one scroll-through before anyone outside sees it.

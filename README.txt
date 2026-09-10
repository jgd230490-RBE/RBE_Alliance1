rbe-help-page-0910.zip
======================
Delivered 2026-09-10 (evening). Extract over the repo root. No schema change,
no new dependency, no change to the staff app or the public map. 30 files:
one new test and the help page with its 27 images — PLUS ONE MANUAL EDIT to
backend/main.py (below). main.py is deliberately NOT in this zip: a parallel
session delivered rbe-costing-fuel-0910.zip the same evening, which also
carries main.py; two zips touching one file are not interchangeable, so the
four-line mount is left for you to add by hand AFTER whichever zips you apply.

THE MANUAL EDIT (backend/main.py, after the /map mount, before @app.get("/"))
-----------------------------------------------------------------------------
# User guide at /help/ (frontend/help/index.html + media/). Same no-cache static class
# as the map. Mounted before the catch-all "/". Not linked from the rail yet.
app.mount("/help", NoCacheStatic(directory=str(ROOT / "frontend" / "help"), html=True), name="help")

Until that line is in, test_help.py fails its first three assertions and
says so ("main.py mounts /help"); everything else in it passes.

WHAT THIS IS
------------
The User Guide, served inside the app at /help/ so it can be linked from the
rail later. It is generated from the same content as the Word User Guide
(RBE_A1_User_Guide_v1.0.docx), so the two cannot drift unless one is edited
by hand. Sections: Welcome, Getting started, Dashboard, Submitting a
forecast, Forecasts, the Look-ahead (weeks, rhythm, Commit / Account /
Horizon, exports), Data pages, the public map, Understanding the numbers,
Flags and warnings, Troubleshooting, Methods and assumptions, Glossary.

FILES
-----
backend/main.py            NOT in the zip — see THE MANUAL EDIT above.
backend/tests/test_help.py NEW. 17 assertions: the mount exists once and sits between
                             /map and "/"; the page parses, has a title, >= 10 h1 and
                             >= 20 h2, an id on every h1; every <img> points at an
                             existing file under frontend/help/media/ and no media file
                             is orphaned; alt text on every image; the wording rule
                             ("stockpile", never a bare "pile"); no internal names
                             (table / function names) leak; the only external URL is
                             the Inter font; no <script>. Broken on purpose twice
                             (renamed mount -> 3 fail; removed an image -> 2 fail).
frontend/help/index.html   NEW. 66 KB, self-contained apart from the Inter font, with a
                             fixed left navigation built from the headings; prints
                             cleanly; collapses to one column under 900 px.
frontend/help/media/*.png  NEW. 27 images, 1.9 MB: 10 drawn figures (architecture,
                             lifecycle, haul cycle, calendar weeks, access, delivery,
                             data model, app map, weekly rhythm, numbers) and 17
                             screenshot SLOTS (see below).

WHAT YOU NEED TO KNOW
---------------------
1. The screenshot images are placeholders. The desktop link dropped before
   the capture pass could run, so every "Screenshot Sxx / Mxx" image is a
   framed slot naming what it should show and the state to capture it in.
   When the captures exist they replace the files under the same names
   (S01..S15, M01..M08) and the page is rebuilt — nothing else changes.
   The Word guides carry the same slots. The list is Appendix C of both.
2. Not linked from the rail. Deliberate: you said "for a later link". The
   page is reachable at /help/ once deployed.
3. The suite at HEAD + this zip + the manual edit: every existing file
   unchanged (1,586 py + 989 js = 2,575) plus test_help.py 17 = 2,592 / 0.
   With rbe-costing-fuel-0910.zip applied as well, expect its 2,714 + 17.
   Run test_help.py from the repo root like the others.
4. Order with the costing-fuel zip: either order, then the manual edit last.

UNVERIFIED
----------
- That Starlette serves /help/ on Render. The HTTP layer is stubbed in the
  sandbox, as for every other harness. Check: open /help/ after deploy; a
  404 means the mount did not land (partial upload) — compare main.py by
  content.
- How the page looks in a real browser. It was rendered from the same
  content as the .docx (which was rasterised and looked at); the HTML
  itself has not been opened in Chromium here.

NOTHING TO DELETE. NOTHING TO MERGE.

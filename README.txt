rbe-guide-roles-0914.zip — the user guide becomes role-aware, gains troubleshooting,
                           and an admin technical appendix
================================================================================
Delivered 2026-09-14 (evening). Extract over the repo root. Cut against HEAD e88b58a.

*** THIS INCLUDES rbe-map-gate-0914-v2.zip IN FULL. ***
If you have not applied v2 yet, apply this instead — it is a strict superset and it is
the only one you need. If you HAVE applied v2, this adds to it cleanly.

  backend/gate.py                        (from v2) the /map/ and /help/ gate
  backend/main.py                        (from v2) middleware, refusal pages, /api/map-auth
  backend/tests/test_gate.py             (from v2) 111 assertions
  backend/tests/test_lookahead.py        (from v2) the date-bomb fix
  env.example                            (from v2) MAP_PASSWORD / GATE_SECRET / MAP_GATE
  frontend/index.html               NEW  User guide link + hash routing (#dashboard etc.)
  frontend/help/index.html          NEW  role switcher, troubleshooting, Appendix D
  frontend/help/media/*.png         NEW  22 placeholder figures REGENERATED
  backend/tests/test_help.py        NEW  39 assertions (was 17)
  backend/tests/parse_frontend.js   NEW  +11 assertions
  backend/tests/help_browser_check.js NEW  32 assertions in real Chromium

NO schema change. NO new runtime dependency. factors.json NOT in this zip.


STILL THE ONLY THING YOU MUST DO ON RENDER
------------------------------------------
    MAP_PASSWORD = <the string you hand to stakeholders>

Without it the map is closed to everyone outside the alliance. Staff are unaffected.


WHAT CHANGED IN THE GUIDE
-------------------------
1. IT ADAPTS TO WHO IS READING IT. A switcher at the top — IPT submitter / Planner /
   Admin. The link in the app's rail passes your role, so it opens on the right view and
   says "signed in as planner". You can still look at another role's view; useful when
   you are talking someone through a screen you can see and they cannot.

   ⚠️ THE ROLE IS NOT A PERMISSION, AND IS NOT TREATED AS ONE. It arrives in the query
   string and anyone can type ?role=admin. All that gets them is the technical appendix,
   which is documentation. The sign-in gate decides who reads the page at all. Making it
   tamper-proof would mean putting the role in the signed cookie and adding an endpoint —
   a bigger change for no security gain, so it was not done. Say if you disagree.

   Planner and above: the Data pages (Locations, Routes, Zones, Config) and the
   approve/reject section. Admin only: Appendix D.

2. TROUBLESHOOTING ASKS WHAT YOU ARE SEEING. Twelve symptoms, each with a verdict on
   whether it is a fault at all — because most of them are not. It leads with the one
   that caught ME today: an empty map is almost always no month selected and forecast
   routes switched off, not missing data.

3. APPENDIX D — TECHNICAL, ADMINS ONLY. How a haulage number is made; which figures are
   measured, which are planning assumptions and which are unaudited (the carbon factor
   is unaudited and says so); the live sources and how each fails; what leaves the
   platform to third parties; and that the deployment must not be called secure.

   ⚠️ IT IS A SUMMARY, NOT YOUR 53-PAGE TECHNICAL GUIDE. That document is not in the
   Claude project and I have never seen it — this is written from the working notes.
   Upload the .docx and I will fold the real thing in; until then the Word document is
   the one that has been reviewed and Appendix D says so itself.

4. THE 22 PLACEHOLDER FIGURES WERE REGENERATED. The old ones carried the line "Captured
   from the live deployment", which was not true of the image — a dashed empty box. The
   new ones say "Screenshot not yet captured" and name the screen. FILENAMES ARE
   UNCHANGED on purpose: drop a real capture over S02.png and nothing else needs to
   change. The five fig-*.png diagrams are genuine artwork and were not touched.

5. EVERY FIGURE WITH A SCREEN BEHIND IT NOW LINKS INTO THE LIVE APP — 21 of the 27.
   That needed the app to name its page in the URL, so #dashboard, #lookahead and the
   rest now work: pages are bookmarkable, and the guide can point at one. The hash wins
   over the remembered page on first load; an unknown hash is ignored. It REPLACES
   rather than pushes, so Back still leaves the app instead of walking your click history.

6. TWO STALE CLAIMS FIXED. The guide said the public map needs no sign-in, in two
   places. It has not since this morning.


FIRST LOOK
----------
1. Open the app, sign in, click "? User guide" at the foot of the rail. It should open
   on YOUR role and say so at the top right.
2. Switch to Admin. Appendix D appears in the body AND in the left nav. Switch back to
   IPT submitter — the Data pages disappear from both.
3. Click "Something looks wrong →", then any symptom. One answer opens at a time and
   says whether it is a fault.
4. Click "open it in the app →" under any figure — a new tab on that screen.
5. Sign out, open /help/ — you should be told to sign in, not shown the guide.


ASSERTIONS
----------
  3,037 passed, 1 failed in the default suite (18 files).
  PLUS 32 in real headless Chromium — backend/tests/help_browser_check.js.

  The 1 failure is the same one as always: test_help.py's "no media file is orphaned",
  which is frontend/help/media/placeholder.pl. A ZIP CANNOT DELETE IT — delete that one
  file in the GitHub web UI and test_help goes 40 / 0. Outstanding since 10 Sep.

  Baseline: plain HEAD was 2,892 / 2.


⚠️ WHAT IS NOT PROVEN
---------------------
  * THE GUIDE IS NOW VERIFIED IN A REAL BROWSER — role filtering, the nav, the
    troubleshooting picker, the deep links and the ?role= handover all run in Chromium
    and are asserted, not inferred. That is new and it is the strongest coverage any
    page in this repo has.
  * THE APP IS NOT. Hash routing is asserted at SOURCE level only. React cannot be
    rendered in the harness the way the static guide can. Check #2 and #4 above.
  * THE BABEL CHECK STILL CANNOT RUN. npm answers 403 for @babel/standalone. The app
    was transpiled and server-rendered through TypeScript, and scanned for duplicate
    top-level declarations (104 declarations, none duplicated). TypeScript accepts some
    things Babel refuses. If the app goes blank, it is one of the two frontend changes.
  * NO FRESH-CLONE CHECK. The repo is private and the sandbox has no credentials. This
    was verified against the 07:39 clone reset to HEAD with `git clean -fdx` and the zip
    extracted over it. If you uploaded anything after 07:39 today, diff before extracting.


ONE MISTAKE WORTH KNOWING ABOUT
-------------------------------
While fixing the "map needs no sign-in" sentence in section 8, I replaced the whole
paragraph instead of the sentence, and silently deleted the Control Panel description
with it. I caught it and restored it from HEAD. There is now an assertion that the
Control Panel text survives — correcting a stale sentence must not cost the paragraph
around it.


STILL OPEN
----------
  * The 22 real screenshots. I cannot take them: the browser pane returns images to me,
    not files I can put in a zip, so my earlier offer to capture them was wrong. Either
    you capture them (the filenames and the figure list tell you exactly what each one
    is), or they stay as honest placeholders, which is far better than where they were.
  * Per-person logins (Phase 6) still replace the shared codes and the shared map
    password. Do not describe the deployment as secure.
  * The real technical guide (.docx) to fold into Appendix D.

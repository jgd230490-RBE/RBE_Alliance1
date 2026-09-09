rbe-lookahead-hotfix2-tarktee-0909.zip
======================================
Delivered 2026-09-09 (night). Extract over the repo root. Apply ON TOP of
slices 3-6 and hotfix 1 (the blank page). Six files, no schema change.

THE BUG
-------
The Look-ahead froze on "Loading..." the moment a forecast was approved on a
baked route. Diagnosis (structural -- I cannot reach Render or Tark Tee from the
sandbox, so it is not reproduced live): the page read /api/lookahead called the
Tark Tee road-restriction check INLINE. That is six live ArcGIS services (a
metadata call plus one per layer, 20 s timeout each, uncached on first use) and
then a pure-Python point-to-segment loop over every route x every feature x
every vertex. With no baked line it was skipped, which is why the empty page
loaded instantly. I put a live external fetch on the page's critical path. My
design error.

THE FIX
-------
* /api/lookahead no longer consults Tark Tee (tark_tee defaults to 0). The rail
  reports the source as "pending".
* New GET /api/forecast-weeks/tark-tee?bucket= returns the TARK_TEE flags alone,
  for the routes on the page ONLY (not all 107), with status ok | unavailable |
  skipped. Never a silent clean.
* The page fetches it AFTER rendering, shows "checking road restrictions (Tark
  Tee)..." meanwhile, merges the flags into the rail when they arrive, and says
  "unavailable" if they cannot.
* The XLSX/PDF export still checks Tark Tee inline (a download can wait; a sheet
  handed to a haulier should carry the flags). If the export is slow, that is why.

FILES
-----
  backend/clashes.py       tark_tee() checks only the page's routes; compute()
                           defaults Tark Tee off and reports 'pending'.
  backend/lookahead.py     page() defaults off; new tark_tee_flags().
  backend/main.py          /api/lookahead default tark_tee=0; new
                           /api/forecast-weeks/tark-tee; clashes default 0.
  frontend/index.html      the second fetch, the "checking" line, the merge.
  backend/tests/test_lookahead.py   186 -> 191 (patched restrictions: the page
                           read never touches it; the tark-tee read fetches once
                           and checks only R1, R3; outage reads unavailable).
  backend/tests/parse_frontend.js   301 -> 303.
  README.txt

IF IT STILL FREEZES
-------------------
Then something else in the page read is slow on your data, and I need to see
which. DevTools -> Network -> the /api/lookahead request: its time, and its
status. And the Render log for that request. Send both.

SUITE
-----
2,508 / 0 on a fresh clone with slices 3-6 + hotfix 1 + this zip.
Four deliberate regressions, four caught: page() default on, endpoint default
on, checking every route instead of the page's, the page fetch asking for
tark_tee=1.

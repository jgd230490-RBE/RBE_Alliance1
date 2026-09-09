rbe-lookahead-hotfix-blank-page-0909.zip
========================================
Delivered 2026-09-09 (night), minutes after rbe-lookahead-slices3-6-0909.zip.
Extract over the repo root. Two files. Apply ON TOP of slices 3-6.

THE BUG
-------
The app rendered a blank white page after slices 3-6. Cause: frontend/index.html
declared `function Kpi` twice -- an old one (the Forecasts page, line 92) and a
new one in the Look-ahead. The browser compiles the page with Babel, which
refuses a duplicate top-level declaration and stops before React mounts. Nothing
else was wrong; the backend was not involved.

Why 2,499 assertions passed a page that cannot compile: the harness transpiles
with TypeScript and evaluates with `new Function()`, and BOTH accept a redeclared
function in sloppy mode. Babel does not. My fault, and a gap in the harness.

FILES
-----
  frontend/index.html             the Look-ahead's Kpi renamed LaKpi (11 usages).
                                  No other change.
  backend/tests/parse_frontend.js two assertions: no top-level name declared
                                  twice; and, when @babel/standalone is present,
                                  compile the page exactly as the browser does.
                                  Both FAIL on the shipped file and pass now.
  README.txt

AFTER APPLYING
--------------
Hard reload (incognito + Empty Cache and Hard Reload -- the browser-cache trap).
If the page is STILL blank: DevTools -> Console, and send me the first red line.
That would mean a second problem this fix did not cover.

SUITE
-----
parse_frontend.js 299 -> 301. Everything else unchanged: 2,501 / 0.

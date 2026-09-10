rbe-lookahead-feedback-0910.zip
===============================
Delivered 2026-09-10. Extract over the repo root, ON TOP of everything through
hotfix 3. Ten files. ⚠️ THREE new nullable columns on routes (no new table),
added by the same init_lookahead_db() ALTER on first boot.

YOUR THREE POINTS, AS BUILT
---------------------------
1. "Tark Tee takes too long to load."
   The live check no longer runs on any page read. It runs ON DEMAND, in a
   background thread, and the result is STORED on each route
   (routes.restrictions_hits / _checked_at / _status). The Look-ahead and the
   export read the stored result instantly and print its date. Under the rail:
     "Road restrictions (Tark Tee): checked 2026-09-10 08:41 UTC · re-check"
   or, before any check: "NOT checked for these routes — that is not the same
   as 'no restrictions' · check now". Pressing it starts the check; the page
   polls and re-reads itself when it finishes (a minute on a cold cache).
   A re-baked route loses its stored check (named as "not checked since
   baking") until the next re-check. A failed check writes nothing.
   New: POST /api/forecast-weeks/tark-tee/refresh (any staff code).

2. "Export takes too long; no stockpile list on it; routes map instead."
   The export reads the stored Tark Tee result, so it is now as fast as the
   page. The Stock sheet and the "Stock at week end" section are gone from
   both files. The PDF carries a MAP of the week's routes in its place, drawn
   from the baked geometry: a Mapbox static image when MAPBOX_TOKEN is set on
   Render and api.mapbox.com answers, otherwise a schematic (lines + labelled
   ends) — the PDF says which. ⚠️ The Mapbox path has NOT run anywhere: the
   sandbox has no token and no network. The first PDF you download tells us;
   if it says "schematic", send me the Render log line for that request.
   Stock stays on the Commit view (Stock held card) for the planner; a
   Look-ahead dashboard for it is noted as a follow-up, not built.

3. "Keep it as Monday to Friday only."
   The Commit grid shows five day columns; the XLSX and PDF print weekday
   rows only. Sat/Sun still exist at 0 in the database (the sum rule and the
   brief's L1 are unchanged) — they are simply not shown or printed.

FILES
-----
  backend/db.py            +3 routes columns (DDL + ALTER)
  backend/restrictions.py  store_checks(), stored_checks(), refresh_async(),
                           refresh_state()
  backend/network.py       a bake clears the route's stored check
  backend/clashes.py       TARK_TEE from the store; sources carry its age
  backend/lookahead.py     tark_tee_status(); page carries the store
  backend/main.py          GET tark-tee = status; POST tark-tee/refresh
  backend/export.py        Mon–Fri rows; no stock; the route map
  frontend/index.html      Mon–Fri grid; the status line + check/re-check
  backend/tests/test_lookahead.py 205 -> 214
  backend/tests/parse_frontend.js 303 -> 305 · render_frontend.js unchanged 49
  README.txt

WHAT WAS NOT TESTED
-------------------
The Mapbox static map (no token/network here) — the schematic fallback is what
the tests see. The background thread against Postgres (the pool is thread-safe
by design; psycopg2 is absent in the sandbox). The live Tark Tee fetch itself,
as ever.

SUITE
-----
2,534 / 0 on a fresh clone with every zip applied (1,556 py + 978 js). Six deliberate regressions,
six caught — one after adding the fixture it needed (a fetch that returns an
errors dict rather than raising).

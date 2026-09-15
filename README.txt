RBE Alliance 1 — rbe-tighten-0915.zip
=====================================
Cut 2026-09-15 against repo HEAD ca6ee38 ("Add files via upload", 14 Sep 13:50).
Fifteen files. No schema change. No new pip or CDN dependency. factors.json NOT included.

VERIFIED: extracted over a FRESH clone of ca6ee38 and the suite run there —
3,063 passed / 1 failed. Plain HEAD before this zip was 3,037 / 1. The one failure is
the same one it has been since 10 Sep: frontend/help/media/placeholder.pl. See ACTIONS.


WHAT IS IN IT — five of your seven items
========================================

2. THE DEMO CODES ARE GONE FROM THE SIGN-IN BOX — AND FROM THE SERVER'S DEFAULT
   You chose "remove the hint and the fallback".

   Before: with none of the eight *_CODE variables set, submitter123 / planner123 /
   admin123 worked, and the sign-in box printed all three. One missed Render variable
   silently reopened the whole app to anyone with the URL.

   Now: backend/access.py honours them only when ALLOW_DEMO_CODES is truthy AND no real
   code is configured. With neither, resolve() returns None and nobody signs in — it
   fails closed, like MAP_PASSWORD, unlike ADMIN_TOKEN.

   >> DO NOT SET ALLOW_DEMO_CODES ON RENDER. <<
   Your eight access codes are already set there, so this changes nothing for your team.
   A blank variable counts as off, deliberately.

   The seven Python harnesses that sign in as planner123 now set the variable themselves
   (one line each, at the head, beside the existing env-var pop loop).

   Also updated: env.example (new section), README.md (its "Who can do what" table still
   listed the three codes as the way in).

4. THE ROUTE LABEL IS A DAILY AVERAGE
   It read "2221 Two-way" — one month's movements — while the KPI card beside it read
   "101 MOVEMENTS / DAY". Two rates on one screen, two orders of magnitude apart.

   The division is the SERVER's, not the map's:
     /api/public/route-forecasts  each route gains  per_day, peak_per_day, working_days
     /api/public/forecast-matrix  the response gains  working_days
   Both from main._working_days(), which is now the ONLY reader of
   factors.planning.working_days_per_month — public_month_kpis was refactored onto it.
   A zero or non-numeric value in a hand-edited factors.json falls back to 22 rather
   than dividing a public endpoint by zero.

   The label now reads "101 Two-way / day". The month figure is NOT thrown away: it
   rides on the feature as f_month_total (timeline) / f_month_avg (window) so the popup
   and the Forecast detail table go on showing a monthly number.

6. THE TIMELINE BAR
   (a) "vehicles" -> "trips". NOT a blind string swap: the word comes from the API and
       can be t or m3, and "4444 trips" over a tonnage would be a lie. Only the
       vehicle-loads unit is renamed, via TL_UNIT_WORD, and it is renamed to the word
       the rest of the product already uses (the Dashboard's TRIPS card, the map KPI
       card's "101 trips/d").
   (b) The overlap. #tl-label had min-width:190px and white-space:nowrap but no flex
       sizing, so as a flex item it shrank to exactly 190px while its text kept its
       natural width and painted straight over the select. flex:0 0 auto fixes it: the
       box is max(content, 190px) and can never be squeezed under its own text.
   (c) The speed select is half the width it was (50px, centred, smaller type) — five
       two-character options did not need what it had.
   The range slider is now the only item in the bar allowed to give ground.

3. THE BIG SCREEN SPLITS INTO FOUR
   Sections, in the page's own order so nothing moves relative to the working view:
     1 Today       the today strip + this week's lines on the map
     2 Figures     Delivered to date / Volume / Cost / Carbon
     3 Charts      demand, delivered, cost, trips, fleet, carbon, split, top routes
     4 Stockpiles  the capacity chart

   Three ways to show them, from a new bar that appears only in big-screen mode:
     All four    every section on one screen, divided and labelled (what it did before)
     Rotate      one at a time, advancing on a timer (10 s … 2 min, remembered)
     One section chosen here — or pinned per display by URL

   >> FOUR SCREENS: open #dashboard/1 on the first, #dashboard/2 on the second, and so
      on. Each browser shows that section and nothing else. Nothing to configure on the
      machines themselves, and a pinned URL wins over whatever that browser remembered.
      A number outside 1–4 is ignored and you get the whole board, not a silent guess.

   HIDDEN, NEVER UNMOUNTED — and this is the part worth knowing. Section 1 holds a
   CommitMap, which is a real mapboxgl.Map. Rotating by unmounting would destroy and
   rebuild a WebGL context every few seconds on a display left running all day; browsers
   cap those at about sixteen and the tiles would be re-fetched each time. So a hidden
   section keeps its DOM and its instances, and fires one window resize when it comes
   back into view, because Mapbox and Chart.js both measure zero inside display:none.
   The working view always shows all four whatever the big screen remembered.

5. STREET VIEW — NOT A BUG. THE KEY IS NOT SET ON RENDER.
   Nothing in this zip. Measured on the live deployment, 15 Sep:

     GET /api/streetview/meta?lat=59.4207&lon=24.8890
     -> 200 {"available":false,"status":"NO_KEY",
             "error":"GOOGLE_MAPS_API_KEY is not set on the server"}

   Set GOOGLE_MAPS_API_KEY on Render (Google Maps Platform — a separate key and separate
   billing from HERE) and Street View starts working with no code change.
   GOOGLE_MAPS_URL_SIGNING_SECRET is optional and recommended.

   Same shape as E23, where MAPBOX_TOKEN was never on Render either.

   >> A SECOND, SEPARATE DEFECT, NOT FIXED HERE: the map asks /streetview/meta first and,
      when available is false, renders NOTHING and says nothing. So a missing key, a
      Google outage and "there is genuinely no imagery down this quarry track" all look
      identical to the reader — which is exactly the Tark Tee complaint already logged as
      E16. Say the word and the popup gets one line naming which of the three it is.


WHAT IS NOT IN IT
=================
1. THE HELP-GUIDE SCREENSHOTS. Eleven arrived; the guide has 23 slots. Nothing has been
   put into frontend/help/media/ yet — the catalogue and the list of what is still
   missing is in the chat and in claude/help-screenshots-0915.md.

7. SORTING AND COLUMN RESIZE ON ALL TABLES. Deliberately left out. There are seventeen
   tables; four of them (the Look-ahead day grid and the month matrices) are grids, not
   row lists, where sorting rows means nothing. Doing it properly is one shared
   component applied to twelve call sites, not seventeen one-off patches, and it is a
   slice rather than a tighten-up. Scoped in the chat with the one decision it needs.


ACTIONS FOR YOU
===============
[ ] Extract over the repo root. Nothing is renamed; nothing needs deleting.
[ ] Do NOT add ALLOW_DEMO_CODES to Render. Check your eight access codes are still set
    there BEFORE deploying this — with them unset and no opt-in, nobody can sign in.
    That is the intended behaviour, but find out now rather than at the sign-in screen.
[ ] Set GOOGLE_MAPS_API_KEY on Render to turn Street View on.
[ ] Still outstanding from 10 Sep: delete frontend/help/media/placeholder.pl in the
    GitHub web UI. A zip cannot delete a file. test_help goes 39/1 -> 40/0.

FIRST LOOK, once deployed
  1. Sign in. The box under the button must no longer name any code.
  2. Public map -> Play the timeline. The bar reads "... N routes · N trips", the month
     label no longer sits under the speed box, and the label on each route reads a
     two-or-three-figure daily rate, not a four-figure monthly one.
  3. Dashboard -> Big screen. The new bar appears. Try Rotate; watch the map in section 1
     survive a full cycle and still draw. Then open #dashboard/4 in a second window.
  4. Leave the working view and come back: all four sections are there.


WHAT IS NOT TESTED
==================
- The Babel compile check still cannot be run (npm returns 403 for @babel/standalone).
  The substitute is render_frontend.js plus an AST duplicate-declaration scan. TypeScript
  accepts what Babel refuses — that is how a duplicate function blanked the page on
  9 Sep. This delivery adds one new top-level component (WallSection) and three new
  top-level functions (hashParts, pageFromHash, screenFromHash); the scan sees no
  duplicate, but a real Babel compile has not run.
- Nothing in this zip has been seen in a browser. The staff app cannot be rendered in the
  sandbox at all — the CDNs it loads (Tailwind, React, Babel, Chart.js, Mapbox) are all
  blocked by the egress proxy. Every frontend claim above is source-level plus the
  TypeScript render harness.
- The rotate timer, the resize nudge, and Mapbox surviving a hide/show cycle: asserted at
  source level only. Item 3 of FIRST LOOK is the one that actually proves them.
- /api/public/route-forecasts and /forecast-matrix were exercised against SQLite with the
  test scaffold's data, not against Postgres with your 8 routes.

26 new assertions. Each was broken on purpose and the right one was seen to fail
(11 mutations: per_day back to avg, the demo fallback reopened, the label back to the
month total, the flex fix removed, the unit word un-mapped, the codes back in the box,
unmount instead of hide, the resize nudge dropped, the working view inheriting the wall
layout, a clamped screen number, and the rotate timer left running).

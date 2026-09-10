rbe-lookahead-calendar-weeks-0910.zip
=====================================
Delivered 2026-09-10 (afternoon). Extract over the repo root, ON TOP of
rbe-lookahead-feedback-0910.zip. Seventeen files. ⚠️ ONE new pip dependency
(pillow — reportlab already pulls it in; now pinned). NO schema change.
⚠️ The MEANING of a week changes (point 1) — read that before deploying.

YOUR SIX POINTS, AS BUILT
-------------------------
1. "Mon–Fri shows but starts from Tuesday; should always start from Monday."
   Not a display bug: forecast weeks were days-of-month buckets (1–7, 8–14,
   15–21, 22–end — the Week 1 build list's rule). 1 Sep 2026 is a Tuesday, so
   this week's bucket was Tue 8 – Mon 14, and Mon–Fri-only showed Tue, Wed,
   Thu, Fri, then NEXT week's Monday. You chose calendar weeks:
     • A week is Monday–Sunday and belongs to the month that holds its
       THURSDAY (ISO 8601 — the week numbers on Estonian calendars).
     • A month has FOUR or FIVE weeks. Sep 2026: 4 (31 Aug–6, 7–13, 14–20,
       21–27). Oct 2026: 5 (28 Sep–4 Oct … 26 Oct–1 Nov). Dec 2026: 5.
     • A month's forecast splits over ITS weeks: ÷4 or ÷5. Derived weeks
       refresh to the new share on the next read; edited/confirmed hold.
     • week_index keeps its meaning (k-th week of the month), so every
       existing forecast_weeks row keeps its identity — only its DATES move,
       by at most six days. Your confirmed September week and typed actuals
       stay attached to "week 2", which is now 7–13 Sep (was 8–14).
     • Day rows are keyed by date, so the days you typed keep their figures.
       A day that changed bucket (Mon 14 Sep: week 2 → week 3) is re-stamped
       on the next read. If you had EDITED that day, week 3's DAYS ≠ WEEK
       flag may fire — that is the truth, not a fault.
     • The Horizon grid now has 4 or 5 columns per month, headed with each
       week's Mon–Sun dates; the server says how many (nothing in the
       browser assumes four).
   Nothing to migrate. First read after deploy does the re-stamping.

2. "A full actual map would be preferred."
   The schematic you got was MY bug: the PDF read only MAPBOX_TOKEN, which
   Render does not have, while the app itself falls back to the public token
   in main.py. One source now — config.mapbox_token() — for the browser map,
   the new Commit map and the PDF's Mapbox Static image. ⚠️ Still not run
   against api.mapbox.com from here (the sandbox cannot reach it); the fetch
   is exercised with a stub, the token/URL are asserted, and the caption
   still says which it drew. Open the first PDF: "map © Mapbox" or
   "schematic … Mapbox could not be reached". If the latter, send the Render
   log line for that request.

3. "The table: Mon–Fri individual, origin and destination coordinates, clear
    columns and rows, landscape, more pages if needed."
   The PDF is now landscape A4, the map full width on page 1, then ONE ROW
   PER LINE: Route · Origin (name + lat, lon) · Destination (name + lat, lon)
   · IPT/WS · Material · Vehicle · Mon · Tue · Wed · Thu · Fri · Week ·
   km/trip · € (when any rate is typed). Each day cell = qty / trips · veh;
   today's column tinted; † marks a typed day; a TOTAL row closes the table.
   The header row repeats on every page; flags and the footer follow. The
   collapse rule ("MON–FRI EACH DAY") is gone. Coordinates are the location's
   own WGS84 lat, lon from the Locations page.

4. "Stock held with consumption input → Account page, simpler."
   Account view now has a Stockpiles block for the ACCOUNT week only — one
   row per stockpile: opening · in · OUT [one box] · closing · capacity ·
   remaining. That is the only place consumption is typed. The W1–W4 × two
   months grid at the bottom of Horizon is gone (its component deleted).
   Consequence you agreed: no planned consumption for future weeks any more —
   the STOCKPILE OVER forecast is opening + planned inbound.

5. "Stock held on Commit → Dashboard; replace with this week's movements map."
   Dashboard gets a read-only "Stock held" panel (this month + next, balance
   at each week's end, over-capacity in red). The Commit view's card is
   replaced by a small LIVE Mapbox map of this week's lines — the baked
   loaded leg per line, coloured by IPT (palette C), origin/destination dots,
   hover for the week's qty and trips. It is NOT the public map in a frame
   (that loads zones, restrictions and months of matrix): it makes one small
   read, GET /api/lookahead/geometry?ids=…, and never touches the page read,
   so the grid is no slower. Created once, kept while Look-ahead is open.
   Unbaked routes are listed under it, never drawn as straight lines.

6. "Always stockpile, not pile."
   Every user-visible string, the flag code (PILE_OVER → STOCKPILE_OVER, rail
   label "STOCKPILE OVER"), comments and tests. A source-level assertion now
   fails if "pile" appears on its own anywhere in index.html.

FILES
-----
  backend/weeks.py         calendar weeks: iso_weeks_of_month, weeks_in_month,
                           week_span, week_of_date, prev_week; ÷n split
  backend/days.py          bucket_dates from week_span; bucket re-stamp
  backend/stockpiles.py    week_index bound = the month's own count
  backend/clashes.py       STOCKPILE_OVER; prev_week from weeks.py
  backend/lookahead.py     account_stock(), week_geometry(), horizon week spans
  backend/main.py          GET /api/lookahead/geometry; token via config
  backend/config.py        MAPBOX_TOKEN_DEFAULT + mapbox_token()
  backend/export.py        the landscape PDF; shared geometry read; token
  backend/network.py       wording only
  backend/requirements.txt + pillow==12.2.0
  frontend/index.html      CommitMap, DashboardStock, Account stockpile block,
                           Horizon columns from the server, Stockpiles grid
                           removed, wording
  backend/tests/test_week1.py     315 -> 317 (calendar-week arithmetic)
  backend/tests/test_lookahead.py 214 -> 242 (section 13; the PDF; Mapbox stub)
  backend/tests/parse_frontend.js 305 -> 314 · render_frontend.js 49 -> 51
  backend/tests/fixtures/lookahead_page.json (regenerated)
  README.txt

WHAT WAS NOT TESTED
-------------------
The Mapbox Static fetch against the real API (stubbed here — the sandbox has
no path to api.mapbox.com). The Commit map in a real browser (mapbox-gl is
not loadable here; parsed and Babel-compiled only). The migration against
YOUR Postgres rows — exercised on SQLite with a stale bucket number.

SUITE
-----
2,575 / 0 on a fresh clone with every zip applied (1,586 py + 989 js).
Eleven deliberate regressions, eleven caught (one by the defensive
"unreachable" assertion in week_of_date rather than a named test; one
exposed a crashable assertion, since fixed).

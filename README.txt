rbe-map-gate-0914.zip  v2  —  /map/ password, /help/ behind sign-in, + a way IN to /help/
================================================================================
Delivered 2026-09-14 (second cut). Extract over the repo root. Cut against HEAD e88b58a
(2026-09-11 11:49). SEVEN files.

*** THIS REPLACES THE FIRST CUT. DISCARD IT. ***  The first cut was never applied (I
checked the live site: /map/ and /help/ both still served to an anonymous request from
outside). This is a strict superset, so nothing is lost by binning the first one.

  backend/gate.py                  NEW — the whole policy, pure, no framework, no DB
  backend/main.py                  the middleware, the refusal pages, /api/map-auth
  frontend/index.html              ⭐ NEW IN v2 — a "User guide" link in the rail
  backend/tests/test_gate.py       NEW — 111 assertions
  backend/tests/parse_frontend.js  ⭐ NEW IN v2 — 4 assertions on that link
  backend/tests/test_lookahead.py  a date-bomb assertion fixed (pre-existing failure)
  env.example                      MAP_PASSWORD / GATE_SECRET / MAP_GATE documented


⭐ WHY v2 EXISTS: THE GUIDE HAD NO WAY IN
-----------------------------------------
You asked how to open the help page. The answer was: you could not. It has been served
at /help/ since 10 September and NOTHING in the app ever linked to it — zero occurrences
of "/help/" anywhere in frontend/index.html. The only way in was to type the URL.

That is a separate fault from the gate, and it would have survived a perfect deployment.
v2 adds a "User guide" link at the foot of the left rail, above Collapse. It is an
ANCHOR opening in a new tab, not a NAV entry: /help/ is a separate document, not a React
view, and a 'help' page id would fail the allowed-page check and bounce you to the
Dashboard.

NO schema change. NO new dependency. factors.json NOT in this zip.


WHAT YOU MUST DO AFTER UPLOADING
--------------------------------
Set ONE environment variable on Render, or the map stays shut to everyone outside
the alliance:

    MAP_PASSWORD = <the string you hand to stakeholders>

Nothing else is required. Then restart and check the three things under "FIRST LOOK".

⚠️ THIS FAILS CLOSED, AND THAT IS DELIBERATE. If you forget MAP_PASSWORD, an outside
visitor to /map/ gets "The map is closed" — not the map. That is the opposite of how
ADMIN_TOKEN behaves (unset = every admin endpoint open, which is open question C11 and
is not a pattern worth copying). Your own team is never affected: a valid access code,
or the cookie a staff sign-in sets, opens the map whether or not a password exists.


HOW IT WORKS, IN FOUR LINES
---------------------------
  /map/   opens for: a valid access code, OR the staff cookie, OR the map password.
  /help/  opens for: a valid access code, OR the staff cookie. The map password does
          NOT open the guide — it is staff documentation.
  One cookie, rbe_gate, carrying a level (staff | map) and an expiry, signed with
  HMAC-SHA256 so it can be neither forged nor extended.
  Staff cookie lasts 12 hours; the map password cookie lasts 30 days.

⭐ THE BIT THAT MATTERS FOR THE APP: a successful /api/auth now also sets the staff
cookie. That is what keeps the map working INSIDE the staff dashboard. The app iframes
/map/, and an iframe's document request carries cookies but NOT the X-Access-Code
header — so without this, a signed-in planner would have been asked for a second
credential to see the map on their own page. It is asserted, but only at source level
(see WHAT IS NOT PROVEN).

The map's DATA is gated too, not just its page — /api/public/*, /api/zones,
/api/restrictions/*, /api/routes/restrictions, /api/streetview. Gating the page alone
would have been theatre; every figure on it is readable straight off those endpoints.

TWO ENDPOINTS ARE DELIBERATELY LEFT OPEN:
  /api/meta    the staff app fetches it on mount, BEFORE anyone signs in, to build its
               dropdowns. Gating it empties the login screen. It carries units,
               material names and vehicle labels out of factors.json — no location, no
               route, no forecast, nothing about the project.
  /api/health  Render polls it.


ROTATING THE PASSWORD SIGNS EVERYONE OUT
----------------------------------------
The signing key is derived from MAP_PASSWORD, so changing it invalidates every cookie
already issued. That is the feature, not a bug: it is how you take the map away from
someone who should no longer have it. If you would rather rotate the password WITHOUT
signing everyone out, set GATE_SECRET to any fixed random string and the cookie
survives rotation.


FIRST LOOK (in this order)
--------------------------
0. FIRST, CONFIRM IT LANDED: /map/ in a private window must NOT show the map. As of
   14 Sep 10:30 it still did — the first cut was never applied.
1. Open /map/ in a private window. You should get a navy password page, not the map.
   Type the wrong password — a red line. Type the right one — the map loads.
2. Open /help/ in that same private window. You should be told to sign in, NOT given
   a password box.
3. Sign in to the app normally, then look at the Dashboard. ⭐ THE MAP MUST STILL DRAW
   INSIDE THE APP with no second prompt. If it does not, that is the iframe/cookie path
   and it is the single most likely thing to be wrong — tell me and do not fight it.
4. Still signed in, look at the FOOT OF THE LEFT RAIL: a "? User guide" link above
   Collapse. Click it — the guide opens in a new tab. That link did not exist before v2,
   which is why you could not find the guide.
5. Sign out (or use a private window) and open /help/ directly: you should be told to
   sign in, NOT shown the guide.


WHAT IS NOT PROVEN
------------------
  * THE HTTP LAYER IS STUBBED IN THE SANDBOX. The middleware never executed here. No
    real request was routed, response.set_cookie() was never called, and no Set-Cookie
    header was ever produced. The policy (gate.decide) is exercised exhaustively as a
    pure function; the WIRING is asserted by reading main.py as text. Step 3 above is
    the only thing that proves the wiring.
  * Nothing in a browser. The password page's fetch() has never run.
  * secure=True on the cookie is asserted as a value in a dict, not observed on a wire.
  * ⚠️ I COULD NOT DO THE FRESH-CLONE CHECK. The standing rule is to extract the zip
    over a clean clone and re-run the suite before sending. The repo went private this
    morning and the sandbox has no credentials, so it was verified against the clone I
    took at 07:39 today (HEAD e88b58a) and not against a fresh one. If you have uploaded
    anything since that time, diff before extracting.


ASSERTIONS
----------
  3,008 passed, 1 failed, across 18 files (14 python + 4 node).
  The 1 failure is test_help.py's "no media file is orphaned" — it is
  frontend/help/media/placeholder.pl, which a zip cannot delete. DELETE THAT FILE in
  the GitHub web UI and test_help goes 17 / 0. It has been outstanding since 10 Sep.

  Baseline for comparison: plain HEAD was 2,892 passed, 2 failed.
  This zip adds 111 (test_gate.py) + 4 (parse_frontend.js, the guide link) and turns one
  pre-existing failure into a pass.

⚠️ THE BABEL CHECK COULD NOT BE RUN TODAY. The standing rule is to compile the frontend
with the browser's own compiler, because TypeScript accepts things Babel refuses — that
is how a duplicate `function Kpi` blanked the page on 9 September. npm now answers 403
("forbidden by your security policy") for @babel/standalone in this sandbox, so it was
not installable. What WAS run instead: render_frontend.js (which transpiles and
server-renders the whole block through TypeScript, so a JSX syntax error would fail), and
an explicit AST scan for duplicate top-level declarations — 103 declarations, no
duplicates, 0 syntactic diagnostics. The v2 change adds no new function, only markup
inside an existing component, so the 9 Sep failure mode is not in play. The residual risk
is a Babel-specific rejection of something TypeScript accepts. IF THE APP GOES BLANK
AFTER APPLYING THIS, that is the cause and the rail link is the only frontend change.


ONE THING I FIXED THAT YOU DID NOT ASK FOR
------------------------------------------
test_lookahead.py had an assertion that hard-coded the PDF's five day headings as
"MON 7 SEP ... FRI 11 SEP". It is built from the CURRENT commit week, so it passed
only during the week of 7 September and started failing on Monday 14th — today — when
the week rolled over. It is a stale test, not a code regression, and the PDF is fine.

I derived the five headings from the same week the PDF was built from (via the XLSX's
own date column, which the test had already parsed) instead of hard-coding them. The
assertion is not weakened: it still requires five distinct Mon-Fri columns, all present
in the PDF text, and no collapse rule.

It is one self-contained hunk. If you would rather I had left it alone, revert that
file and the count goes to 3,003 / 2.


STILL OPEN AFTER THIS
---------------------
  * The help page ships 22 PLACEHOLDER images under captions reading "Captured from
    the live deployment" (open question E32). The gate stops strangers seeing them; it
    does not make the captions true. Fix before you send anyone the link.
  * Per-person logins (Phase 6) still replace all of this properly. A shared code per
    role and a shared map password are shared secrets, with no identity and no audit
    trail. Do not describe the deployment as secure on the strength of this zip.
  * There is no rate limit on /api/map-auth. Someone who wants to brute-force a short
    password can. Use a long one.

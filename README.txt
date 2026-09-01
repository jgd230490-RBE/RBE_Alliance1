RBE Alliance 1 — WEEK 1: Tasks A, B, C, D, D2, E
================================================================================
Delivered 2026-09-01.  Zip: rbe-week1.zip

Extract over the REPO ROOT. Paths already mirror the repo (backend/…, frontend/…,
map/…), so nothing needs renaming.

A zip can only ADD or REPLACE. Nothing here needs deleting, so there is no manual
removal step this time.

🔴 BACKEND CHANGE AND A MIGRATION. Render will redeploy the service and create
   two new tables plus three columns on `locations`. Watch the boot log — §5.

SEVEN commits exist in the session with real diffs and messages: one per task,
one for the cross-cutting tests, and one for the Phase 5a fix in §2.0.
THE PUSH FAILED, as it has every session. The proxy's message is now specific
and actionable:

    "jgd230490-RBE/RBE_Alliance1 is not in this session's authorized repository
     set … To fix, add the repository to the session's sources."

That is the backlog item roadmap.md calls "get the repo into the session's
authorised sources". If you can add it, deliveries stop being zips.


--------------------------------------------------------------------------------
1. WHAT SHIPPED
--------------------------------------------------------------------------------

  A   15 work sections seeded; four EU-named planning vehicles added
  B   Submit Forecast saves a YEAR RANGE in one POST
  C   Four-week look-ahead on each approved month line (new staff tab)
  D   Typed actuals, variance, and a calibrate button
  D2  Stockpile max capacity, opening stock, typed weekly consumption
  E   Rail head location type + the EVR rail corridor on the public map

NOT built, as instructed: Task F (IPT access codes) and Task G (KPI HUD). The
week went on A–E. Their specs in your build list are untouched.

Never-build list respected: no upload UI, no upload endpoint, no OCR, no
weather/traffic API, no simulation, no SSO, no user table, no week scrubber on
/map/, no actuals painted on the public map. Muuga / Rapla / Lelle are NOT seeded
as locations — you add those in Data Management.


--------------------------------------------------------------------------------
2. THINGS YOU MUST ACTION OR DECIDE
--------------------------------------------------------------------------------

🔴 2.0  A LIVE PHASE 5A BUG WAS FOUND AND FIXED IN db.py. I was not looking for
        it; the week-1 upgrade-path test tripped over it.

        db._ddl_columns() splits a _TENANT_DDL body on top-level commas and takes
        the first token of each piece. Several of those blocks carry "-- …"
        comments, and prose contains commas. One comma inside a comment splits a
        fragment mid-sentence, so the piece that declares the NEXT column begins
        with an English word — and that column never reaches the wanted-columns
        list. The SQLite tenant rebuild copies only the intersection of that list
        with the live table, so the column is DROPPED, with its data.

        ⚠️ routes.origin_gate_id has been invisible to that parser since Phase 5a
        shipped. dest_gate_id survived, purely because it happened to start its
        own fragment. A SQLite database going through the 4.5 migration would
        have lost a route's ORIGIN gate selection and kept its DESTINATION one —
        and nothing would have errored, because gates.resolve() falls back to the
        location's default gate and then to the legacy lat/lon pair. The route
        would simply have started routing from the wrong side of the site.
        "A fallback chain is what makes a schema change invisible", exactly as
        roadmap.md warns.

        ✅ THE LIVE POSTGRES WAS NEVER EXPOSED. Its branch of
        _migrate_table_to_tenant() uses ADD COLUMN and constraint swaps and never
        calls this parser, and 4.5 ran there on 2026-08-30 — before 5a added
        those columns. The exposure was local SQLite, and any database that still
        needs the migration. Nothing to repair on Render; nothing to check.

        My own locations.capacity_qty and forecast_weeks.parent_qty had the same
        fault for the same reason, which is how it surfaced.

        Fix: strip "--" to end of line before looking for commas. One line.
        Asserted for EVERY column of EVERY table so a future comment cannot
        re-open it, plus a pre-4.5 rebuild test that carries real data through
        and checks it arrives.

🔴 2.1  factors.json — MERGE, DO NOT OVERWRITE, if you have edited it.
        Three kinds of change: four new vehicle entries; a new top-level
        "planning_vehicles" array; and the four new names added to the FRONT of
        each material category's "vehicles" list. Nothing else in the file was
        touched — but a straight overwrite loses your own edits to densities,
        payloads or planning constants.

🔴 2.2  V10's payload is DERIVED and is probably too high.
        The build list gives V10 as "8 m³", not a tonnage, and conversions.py
        reads payload_t only. Stored as 8 m³ × 2.4 t/m³ (the Precast/concrete
        density already in the file) = 19.2 t, with payload_m3: 8 kept verbatim
        beside it. 19.2 t of concrete plus a mixer drum is more than a 32 t GVW
        4-axle can legally carry, so the real MASS payload is lower. If you have
        a mass figure, change payload_t — the reasoning is in the entry's
        _payload_basis field.

🔴 2.3  The rail geometry is PROVISIONAL and wrong by ~6.4 km at Lelle.
        map/data/evr_rail.js is Natural Earth 10m (public domain, ~1:10 000 000)
        — NOT OpenStreetMap and NOT survey. Overpass, openstreetmap.org,
        Geofabrik and every Estonian portal are unreachable from the build
        sandbox; GitHub raw is the only vector source there. Natural Earth was
        the best available and its error was MEASURED, not assumed:

            Rapla 0.3 km · Saku 0.5 km · Nõmme 2.2 km · LELLE ~6.4 km

        Lelle is one of the rail heads this feature exists to serve. Natural
        Earth puts that junction at about 24.98°E; Lelle station is at 24.8236°E.

        Every feature carries provisional:true, its source and its own
        accuracy_note; the click popup renders all three, and parse_map.js
        EXECUTES the popup builder to prove the caveat appears rather than merely
        existing in the file.

        REPLACING IT IS ONE FILE AND NO CODE. Run this at overpass-turbo.eu
        (paste, Run, Export → GeoJSON):

            [out:json][timeout:60];
            area["ISO3166-1"="EE"][admin_level=2]->.ee;
            way(area.ee)["railway"="rail"]["usage"="main"];
            out geom;

        then per feature you keep set properties.heads (an array of location
        NAMES), properties.source, properties.provisional=false and
        properties.accuracy_note, and drop it in as window.evr_rail_data. The map
        reads nothing else. Full instructions are in the file's own header.

        The Muuga branch is NOT included: Natural Earth's nearest line comes no
        closer than 4.1 km to Muuga and could not be identified as the branch
        with any confidence.

⚠️ 2.4  Emissions for the four new vehicles were not given, so they were COPIED
        from the structurally identical existing entries — 0.95 for the two 32 t
        4-axle rigids, 0.89 for the two artic combinations, same DEFRA/BEIS 2025
        basis as the rest of the file. Each says so in _emissions_basis.

⚠️ 2.5  Vehicle DIMENSIONS were not given and were NOT invented. The four send
        HERE a gross weight, and an axle count only for the two the build list
        calls 4-axle. No height, width or length; no axle count for the two artic
        combinations, because "tractor + semi" is 5 or 6 axles depending on
        configuration and guessing changes which roads HERE allows.

⚠️ 2.6  Which material categories list the four new vehicles is MY inference:
        V07/V12 tippers on soil and aggregates, V10 mixer on concrete, V11 flat
        semi on concrete/steel/general. It had to be decided — the Submit
        Forecast picker DISABLES a vehicle its category does not list, so an
        unlisted planning vehicle would be unselectable. Advisory in the UI, not
        enforced; correct any line freely. Recorded as _materials_vehicles_note.

⚠️ 2.7  Two aliases per new vehicle are restatements, not roster trade names.
        Only "8x4 tipper" (V07) came from your build list. "8x4 mixer",
        "40 t artic", "tractor + semi-trailer", "artic tipper" and "tipping semi"
        are mine, marked as such in each _aliases_note. Nothing reads `aliases`
        yet — the field exists so the roster's own wording survives.

⚠️ 2.8  WS15's ownership is split three ways and one ipt_id column cannot hold
        it. IPT 6 is stored because that is what the build list says; "track =
        IPT 6, ditch and gas = IPT 3, the rest RBE PTO" is recorded verbatim in
        its scope_note so the loss is visible rather than silent.

⚠️ 2.9  "in_scope=true, active=true except where noted" — NOTHING in the fifteen
        notes actually withdraws a section from scope, so all fifteen are in
        scope. WS 6 is full scope in v1.2 and only its Phase-1 CONSTRUCT status
        is open (no phasing column exists, so that lives in scope_note). WS 9 and
        WS 11 are "partial", which is a scope quantity, not in_scope=false. If
        any should be out of scope it is one UPDATE.

⚠️ 2.10 primary_discipline is NULL on all fifteen rows. open-questions §A4 — "is
        the section → discipline mapping right?" — is still unanswered and your
        build list carries no discipline column. WS 7 is literally named
        "Superstructure" and it would have been easy to write that in; that is
        reading a label, not a sourced fact, and this table is what the pickers
        and any future report read.

⚠️ 2.11 WS3's official name begins "Timmermanni" while its band begins at the
        Orasselja Viaduct (24.546), where WS2 ends. Seeded verbatim from Scope
        v1.2 as instructed and flagged in its scope_note — not a transcription
        error on my side, but worth a second look at the source document.


--------------------------------------------------------------------------------
3. DECISIONS I MADE THAT YOU MIGHT REVERSE
--------------------------------------------------------------------------------

 3.1  VEHICLES: added four, KEPT the six — you chose this when asked.
      Every baked route_geometry row is keyed on vehicle_profile,
      network.DEFAULT_PROFILE is "Artic Tipper (44t)", and every material
      category names the old entries. Removing one makes its routes' payload, CO₂
      and trips/day fall through to _default (20 t, 0.90 kg/km), and every
      affected route then reports IDENTICAL figures — the exact fault
      /api/admin/diagnostics/factors exists to surface. The four appear FIRST in
      the picker under "Planning vehicles"; the six sit under "Other vehicles".
      Say the word and I will cut the six from the picker while leaving them in
      factors.json so old rows still resolve.

 3.2  MY SUBMISSIONS now groups per LINE, across every year it covers, and shows
      "2026–2027". CONSEQUENCE: Withdraw removes the whole line, not one year. It
      is scoped to that row's own first..last month rather than 1..60, so it
      cannot reach past what is on screen, and the confirmation names the span
      and the month count. Say so if you want per-year withdrawal back.

 3.3  forecast_weeks carries an EXTRA column, parent_qty, not in your table spec.
      Without it "parent month changed" cannot be told truthfully: an `edited`
      week ALWAYS differs from parent/4 — that is what editing means — so
      comparing the two would flag every edited week forever. parent_qty holds
      the parent quantity as at the row's last write, so the flag is an exact
      equality test that clears when somebody edits or confirms again.

 3.4  Reopening or rejecting an approved month does NOT delete its weeks. A
      confirmed week with an actual typed against it is a record of what happened
      on site. The rows stop being refreshed and the Look-ahead shows "month is
      now Pending" on that line instead.

 3.5  The rail highlight, with NO feature naming the selected head, bolds the
      layer UNFILTERED rather than filtering to nothing. Taken literally your
      rule ("filter to those whose heads contains the selected name") makes the
      whole corridor vanish at the moment somebody asks to see it. Unfiltered
      says "here is the network, we cannot tell you which branch". Revisit when
      the geometry is real.

 3.6  The Look-ahead tab is visible to SUBMITTERS as well as planners. Until Task
      F, /api/forecast-weeks has exactly the same visibility as /api/forecasts —
      everyone on staff sees every line — and hiding the tab would imply a
      permission boundary that does not exist behind it. Do not describe
      confirm / actual / calibrate as access-controlled until F ships.

 3.7  Work sections come back in NUMERIC order. `ORDER BY section_id` is
      lexicographic on both backends and with fifteen rows put WS1, WS10, WS11 …
      WS15, WS2 in the picker, which reads as a bug and buries WS2.

 3.8  NODE_COLORS on the ADMIN map: Rail head #0F766E (your reserved rail colour,
      same concept), Stockpile #78350F. The stockpile colour is MINE — clear of
      every reserved route, brand and IPT value. One line to change, and the
      legend is generated from the same object.


--------------------------------------------------------------------------------
4. WHAT IS AND IS NOT TESTED
--------------------------------------------------------------------------------

ELEVEN test files, 1,887 assertions, all green (was TEN / 1,510):

    test_phase2.py        160   (was 142)
    test_phase25a.py      104
    test_phase3.py        154
    test_phase4.py        202
    test_phase45.py       136   (was 127)
    test_phase5a.py       215   (was 191)
    test_tenant_audit.py   24   (was  22)
    test_week1.py         209   NEW
    parse_frontend.js     185   (was 119)
    parse_map.js          358   (was 309)
    test_ipt_overlay.js   140

Run them:  for f in backend/tests/test_*.py; do python3 "$f"; done
           for f in backend/tests/*.js;      do node    "$f"; done

⭐ THE TENANT AUDIT PROVED ITSELF AGAIN. Both new tables were written, registered
   in db.TENANTED_TABLES and given _TENANT_DDL and _TENANT_PK entries — and the
   suite still failed: twelve assertions across test_tenant_audit.py and
   test_phase45.py, naming both tables, because the registry INSIDE the audit had
   not been updated. Four registrations per table, and the fourth is the test's
   own set.

⭐ TWO ASSERTIONS I WROTE WERE VACUOUS AND THE REGRESSION PASS CAUGHT THEM.
   - parse_frontend: a window-sized regex meant to prove saveActual never
     calibrates matched doCalibrate() further down the file, and passed on a
     clean tree AND on a deliberately broken one. Rewritten to slice the
     function's own body.
   - parse_map: grepping for "Provisional geometry" passed with the caveat
     branched out behind `if (false)` — the string was still in the file and
     unreachable. The popup builder is now extracted and EXECUTED against sample
     properties, in the provisional and the non-provisional case.
   Two of the seven backend regressions also failed to fail, for good reasons (a
   second gate upstream, and a None-check that already covered it); the paths
   they exposed are now covered instead.

THIRTEEN deliberate regressions in total; every one failed only what it should.
The last of them — reverting the comment-stripping fix in §2.0 — fails four
assertions naming routes.origin_gate_id, locations.capacity_qty and
forecast_weeks.parent_qty by name.

⚠️ WHAT NONE OF THIS PROVES — read before quoting the number:
   * The HTTP layer is stubbed. Endpoint BODIES run; the transport does not.
     Nothing proves a route is mounted, a query parameter is parsed, or that
     ADMIN_TOKEN rejects anything.
   * No Postgres branch runs. forecast_weeks, stockpile_weeks and the three
     locations columns are created against SQLite only. Their Postgres path is a
     plain CREATE plus ADD COLUMN IF NOT EXISTS and needs no key rebuild — but it
     has NOT been executed. Watch the boot log on the first deploy.
   * Nothing in a browser. The Look-ahead tab, the stockpile panel, the year-
     range form and the rail layer are asserted at SOURCE level only. No
     assertion proves a cell renders or a fetch succeeds.
   * HERE, Tark Tee and Google are never called.


--------------------------------------------------------------------------------
5. FIRST-DEPLOY CHECKLIST
--------------------------------------------------------------------------------

 [ ] Nothing to do about §2.0 on Render — Postgres never took that path. The fix
     matters for local SQLite and for any future tenant.
 [ ] Boot log: init_weeks_db() runs BEFORE init_tenant(). Expect
     "Phase 4.5: tenant key added to 2 table(s): forecast_weeks, stockpile_weeks"
     and NO "FAILED" line. If it says FAILED, stop and send me the log.
 [ ] Boot log: "Seeded taxonomy: {… 'work_sections': 15 …}".
     The seed is ADDITIVE — it skips a section that already exists, so a row you
     correct in the database survives a redeploy, and correcting a row in CODE
     does NOT reach a database that already has it.
 [ ] Submit Forecast → the Work section picker is populated (it said "Not
     configured yet" until now) and lists WS1…WS15 in that order.
 [ ] Submit Forecast → From year 2026, To year 2027, a figure in Dec 2026 and
     Jan 2027, Save. My submissions shows ONE row reading "2026–2027".
 [ ] Approvals → approve that line. Look-ahead → four week cells appear at a
     quarter of each month.
 [ ] Look-ahead → the blue cell is next week. Edit it; Confirm it with the four
     notes blank. Type an actual in week 1: Variance appears and next week's
     Planned MUST NOT MOVE. Then press "→ next week": it moves, weeks 3 and 4 do
     not, and pressing it again into a confirmed week is refused by name.
 [ ] Data Management → new location, type "Stockpile", give it a max capacity and
     an opening figure. It appears in the Stock held panel under the Look-ahead.
 [ ] Data Management → new location, type "Rail head". On /map/, pick it in the
     origin filter: the teal rail line goes bold AND the road hauls from it still
     isolate. Set the origin back to ALL and it dims again.
 [ ] /map/ → click the rail line. The popup must say "Provisional geometry" and
     quote the accuracy note. If it does not, you have a stale cached
     evr_rail.js — hard refresh and check DevTools → Network.


--------------------------------------------------------------------------------
6. FILES IN THIS ZIP
--------------------------------------------------------------------------------

 NEW
   backend/weeks.py                   look-ahead: materialise, edit, confirm,
                                      actual, calibrate
   backend/stockpiles.py              capacity, typed consumption, the balance
                                      read model
   backend/tests/test_week1.py        179 assertions for A–E
   map/data/evr_rail.js               PROVISIONAL EVR corridor — see §2.3

 CHANGED
   backend/db.py                      forecast_weeks + stockpile_weeks DDL, the
                                      three locations capacity columns,
                                      init_weeks_db(), TENANTED_TABLES 12 → 14,
                                      AND the _ddl_columns() comment fix — §2.0
   backend/main.py                    init_weeks_db() in the lifespan;
                                      materialise on approve; five look-ahead
                                      endpoints; three stockpile endpoints;
                                      planning_vehicles in /api/meta
   backend/network.py                 Rail head / Stockpile default roles; the
                                      three capacity columns in
                                      _ensure_location_columns and the location
                                      feed; stock figures on public-map nodes
   backend/conversions.py             planning_vehicle_names()
   backend/taxonomy.py                WORK_SECTIONS (15), seeding, numeric order
   backend/factors.json               ⚠️ MERGE — see §2.1
   frontend/index.html                year range; Look-ahead tab; Stock held
                                      panel; Rail head / Stockpile types;
                                      capacity fields; grouped vehicle picker
   map/index.html                     evr-rail source + layer, rail-heads layer,
                                      Rail network checkbox, applyRailHighlight(),
                                      filterByNode Rail head rule, rail popup,
                                      stock line in the node popup
   backend/tests/test_phase2.py       the "work_sections deliberately left
                                      unseeded" assertion is INVERTED, not
                                      deleted
   backend/tests/test_phase45.py      init_weeks_db in reset_db; 12 → 14
   backend/tests/test_tenant_audit.py the two new tables registered
   backend/tests/parse_frontend.js    119 → 185
   backend/tests/parse_map.js         309 → 358

 NOT in this zip, deliberately: map/ipt_segments.js. Nothing in it changed, so
 IPT_INDEX_VERSION stays v9 on both sides and the pair still matches.
 map/index.html was touched but its version constant was NOT bumped — bumping it
 would claim a change to the segments file that did not happen.

 No .pyc files and no __pycache__ are included.


--------------------------------------------------------------------------------
7. NOTES UPDATED IN THE CLAUDE PROJECT
--------------------------------------------------------------------------------

   claude/week1-decisions.md   NEW — this week as built, in full
   claude/roadmap.md           Week 1 in the sequence; §A3 closed
   claude/open-questions.md    §A3 answered; new §D5 (V10's tonnage), §E8 (the
                               rail geometry), §C9 (the six legacy vehicles)
   claude/code-snapshot.md     eleven files, 1,857 assertions
   claude/for-grok.md          dated entry for the slice

================================================================================

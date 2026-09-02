RBE Alliance 1 — FEEDBACK BUILD, 2026-09-02 (§0–§9 of CLAUDE_FEEDBACK_20260902)
================================================================================
Delivered 2026-09-02.  Zip: rbe-feedback-0902.zip

Extract over the REPO ROOT. Paths mirror the repo. Nothing needs deleting.

🔴 BACKEND CHANGE + ONE NEW COLUMN + ONE UPDATE. Render redeploys the service.
   Boot adds `forecasts.ipt`, runs `UPDATE locations SET loc_type='Railhead'
   WHERE loc_type='Rail head'`, and fills `ipt` on existing lines where the route
   names exactly one IPT. Watch the boot log — §5 below.

🔴 SET THE CODES ON RENDER BEFORE ANYONE ELSE SIGNS IN — §2.1. Until you do, the
   three demo codes still work. The moment any real code is set, they stop.

Ten commits in the session, one per section. Push refused as before.


--------------------------------------------------------------------------------
1. WHAT SHIPPED (all nine must-ships)
--------------------------------------------------------------------------------

 §0  Gate field copy is your wording; the maths is untouched, both fields
     still NULL on every migrated gate.
 §1  Vehicle picker: EN / EU / EE toggle in the header. LABEL ONLY — the stored
     vehicle_type, the payload and the emissions factor are the canonical id.
     Default European.
 §2  "Rail head" → "Railhead" everywhere. Old rows are read as the new string,
     the new string is the only one written, and boot runs your one-line UPDATE.
 §3  Submit Forecast: six per-working-day cards — avg + peak of vehicles, trips,
     material — across the selected year range, in the navy strip.
 §4  Street View: "No Street View here" on ZERO_RESULTS. No extra request.
 §5  Railhead is its own map-layer mark: a rail-over-two-sleepers glyph on a
     white pin edged #0F766E, generated on a canvas, plus a legend entry.
 §6a EVR line: casing 5 px #334155 at 35% under a 2 px #64748B dash [2, 18];
     highlighted casing 7 px / dash [2, 12] in #0F766E. No IPT or reserved
     route colour in either style (asserted).
 §6b Bold when the origin filter is a Railhead OR (Show forecast on / timeline
     open) and a route carrying volume in the month on screen, inside the
     current filter, starts or ends at a Railhead. Dim otherwise.
 §6c Geometry unchanged — still Natural Earth, still ~6.4 km off at Lelle, still
     labelled provisional in the popup. Drop in a replacement file as before.
 §7  IPT access codes, checked SERVER-SIDE. `ipt` on the forecast line is the
     source of truth. IPT field on Submit Forecast: locked for an IPT code,
     empty-until-chosen for a planner. The LOGINS dict is gone.
 §8  Timeline warnings: Tark Tee hits, piles over capacity, seasonal windows —
     for the month on the playhead only, max three + "N more", dismissible,
     rebuilt every tick, gone when the timeline closes.
 §9  KPI cards: vehicles/day · trips/day · material/day · active routes for the
     month on the playhead, plus by-discipline and (when mixed) by-material
     lines. Recomputed on every tick, on the forecast toggle, on every filter.
     Approved only, never actuals.

NOT built, as instructed: SSO, vehicle photos, a new EVR survey, truck
animation, week scrubber, N-class hard-block, the C8 hex, hiding legacy
vehicles, the V10 mass rewrite. C8 / C9 / D5 / A2 are still with you.


--------------------------------------------------------------------------------
2. THINGS YOU MUST ACTION OR DECIDE
--------------------------------------------------------------------------------

🔴 2.1  SET THE CODES. Render → Environment:
            IPT1_CODE … IPT6_CODE    PLANNER_CODE    ADMIN_CODE
        Rules, all enforced on the server:
          - an IPT code sees ONLY lines whose `ipt` is that IPT — not other
            IPTs' lines, and not lines with no IPT at all. Cannot approve.
          - PLANNER_CODE sees everything and approves. Must pick an IPT on the
            form; there is no silent default.
          - ADMIN_CODE = planner + the admin surface, which STILL needs
            ADMIN_TOKEN for /api/admin/*. That boundary is unchanged.
          - while NONE of the eight is set, submitter123 / planner123 / admin123
            work (demo mode). Set one and all three stop.
        ⚠️ Everyone signed in when you set the codes will get 401 on their next
        click and need to sign in again with a real code.

🔴 2.2  FIRST CHECK AFTER DEPLOY: that a WRONG code actually gets 401.
        The middleware that reads X-Access-Code cannot run in the sandbox (the
        app class is stubbed). Everything BELOW it — resolution, the per-line
        filter on every staff endpoint, the write guards, the approve gate — is
        exercised by 40 assertions. The header itself is not. Sign in with a
        made-up code: it must be refused. Then sign in with an IPT code and
        confirm Approvals is not offered and another IPT's line is not shown.

🔴 2.3  LINES WITH NO IPT. Every forecast written before today has `ipt` NULL.
        Boot fills it from the route ONLY where the route names exactly one IPT
        ("IPT 5"). Most routes read "IPT 3 / IPT 6" — shared — and nothing on
        the line says which, so those stay NULL. NULL lines are visible to
        planners and admins only and show "no IPT" in Approvals. To hand one to
        an IPT: open it from My submissions as a planner, pick the IPT, save.
        The boot log tells you how many were filled and how many were left.

🔴 2.4  factors.json — MERGE, DO NOT OVERWRITE, if you have edited it. This pass
        adds `labels.{en,eu,ee}` to every vehicle and a `_labels_note`. Nothing
        else in the file changed.

⚠️ 2.5  NO ESTONIAN LABEL WAS INVENTED. Your note names "veoauto, sadulveduk,
        kallur" as EE official aliases but not which vehicle each belongs to,
        and the EU matrix that carries them is not in this repo. Every EE slot
        falls back to the EU string and shows a " *" in the picker. Fill
        `labels.ee` in factors.json per vehicle and the toggle picks it up — no
        code change. The English labels for the four planning vehicles are the
        aliases from last week (only "8x4 tipper" came from you; the rest are
        mine, marked as such). The six legacy keys are their own English label
        and fall back for EU.

⚠️ 2.6  STOCKPILES ARE NOT IPT-FILTERED. Your table says an IPT code can do
        "stockpile consume for that IPT". A pile has no IPT and inferring one
        from the routes that feed it would be a guess, so any valid code can
        see and consume every pile. Say the word if piles should carry an IPT.

⚠️ 2.7  TRIPS/DAY == VEHICLES/DAY, on both the form strip and the map cards.
        On one line with one vehicle a trip IS a vehicle-load, and a line typed
        in t or m³ converts through the payload either way — exactly your
        formula. Both cards are shown as asked, and each place says they are
        equal rather than hiding it. They would only diverge under a line
        model that does not exist.

⚠️ 2.8  THE KPI CARDS WITH THE TIMELINE CLOSED BUT SHOW FORECAST ON use the
        Show-forecast window, averaged over its months that carry volume, and
        the label says so. With neither on, the three rate cards read "—" and
        Active routes is the baked-route count in the filter — there is no
        forecast month to rate.

⚠️ 2.9  STILL NOT SECURE. A code per role is a shared secret typed into a
        browser: no rate limit, no per-person identity beyond the name typed,
        code in sessionStorage for the tab. Better than plaintext codes in
        view-source; not Phase 6.


--------------------------------------------------------------------------------
3. DECISIONS I MADE THAT YOU MIGHT REVERSE
--------------------------------------------------------------------------------

 3.1  Demo mode keeps a planner's IPT OPTIONAL (pre-F behaviour) so nothing
      locks out locally or in the sandbox. With real codes set it is required.

 3.2  An IPT code that tries to touch another IPT's line gets 404, not 403 — it
      must not learn the line exists.

 3.3  Withdraw for an IPT code carries `ipt = ?` as a DELETE predicate, so the
      statement itself cannot reach another IPT's rows.

 3.4  The Rail-head rename UPDATE is deliberately unscoped by tenant: a spelling
      fix to a type value, applied to every tenant alike.

 3.5  Unknown vehicle on a line → V07's 18 t payload for the cards (your
      fallback), flagged in the card note. The line's own qty is unaffected.

 3.6  Warnings use the FIRST (most severe) Tark Tee hit per route with "(+N)".

 3.7  §6b with no forecast on: an explicit Railhead origin still bolds and still
      filters to that head's features; a movement highlight shows the whole
      corridor.


--------------------------------------------------------------------------------
4. TESTS
--------------------------------------------------------------------------------

ELEVEN files, 2,027 assertions, all green (was 1,887):

    test_week1.py         270   (was 209)   §2, §1, §7 (40), §8/§9 endpoints
    parse_map.js          401   (was 358)   §5, §6, §8, §9, §4
    parse_frontend.js     220   (was 185)   §0, §1, §2, §3, §7
    test_tenant_audit.py   25   (was  24)
    test_phase2.py 160 · test_phase45.py 136 · test_phase5a.py 215 · test_phase4.py 202
    test_phase3.py 154 · test_phase25a.py 104 · test_ipt_overlay.js 140

Run:  for f in backend/tests/test_*.py; do python3 "$f"; done
      for f in backend/tests/*.js;      do node    "$f"; done

Every Python harness now signs in as planner123 (demo mode) before running.

Fifteen deliberate regressions; each failed only what it should. One of them —
"an IPT code trusts the body's ipt" — was caught not by the assertion written
for it but by the ownership guard one layer down, which raised and stopped the
run. The protection held; the harness could be more graceful about it.

⚠️ WHAT NONE OF THIS PROVES
   * The HTTP layer, and specifically the X-Access-Code middleware — §2.2.
   * No Postgres branch. `forecasts.ipt` is ADD COLUMN IF NOT EXISTS; the
     rename is a plain UPDATE. Low risk, not executed.
   * Nothing in a browser. The header toggle, the IPT field, the glyph, the
     dashed rail style, the warning stack and the cards are asserted at source
     level; the glyph has never been drawn.
   * HERE, Tark Tee and Google are never called. The warning stack's Tark Tee
     source calls /api/routes/restrictions, which does — on Render, first open
     of the timeline may take a few seconds while that fetches, once.


--------------------------------------------------------------------------------
5. FIRST-DEPLOY CHECKLIST
--------------------------------------------------------------------------------

 [ ] Boot log: no FAILED line. Expect "Task F: ipt filled on N line(s) from
     single-IPT routes; M left NULL".
 [ ] Before setting codes: sign in with planner123, open My submissions — your
     test forecast is there. Note whether it shows an IPT chip.
 [ ] Set the eight codes on Render. Redeploy.
 [ ] Sign in with a MADE-UP code → refused. With planner → Approvals visible,
     IPT dropdown on Submit Forecast, empty by default.
 [ ] Sign in with an IPT code → no Approvals tab, IPT field locked, only that
     IPT's lines in My submissions / Dashboard / Look-ahead. A line with no
     IPT is invisible to it.
 [ ] Header: EN / EU / EE toggle changes vehicle names everywhere; save a line
     and confirm the stored vehicle_type is the EU string.
 [ ] Data Management: a location typed "Rail head" before today reads
     "Railhead" now.
 [ ] /map/: the Railhead pin is a glyph, not a dot. The EVR line is grey ticks
     with big gaps. Pick the railhead as origin → teal and bold. Set ALL, turn
     on Show forecast with a railhead route approved in the window → bold
     again. Off → grey.
 [ ] /map/: open the timeline, scrub. The four cards change per month; an
     empty month says "No approved forecast in this month". A warning stack
     appears above the bar only when that month has a hit; scrub past it and
     it goes.
 [ ] Location popup with no imagery says "No Street View here".


--------------------------------------------------------------------------------
6. FILES IN THIS ZIP
--------------------------------------------------------------------------------

 NEW      backend/access.py            codes, per-request context, the filters
 CHANGED  backend/main.py              middleware, /api/auth, filters on every
                                       staff endpoint, ipt on save, the two new
                                       public endpoints (month-kpis,
                                       stockpile-timeline)
          backend/db.py                forecasts.ipt; the Railhead UPDATE
          backend/network.py           canonical_loc_type(), backfill_forecast_ipt()
          backend/stockpiles.py        Railhead in STORAGE_TYPES, canonical read
          backend/weeks.py             parent ipt rides on week rows
          backend/conversions.py       vehicle_labels()
          backend/factors.json         ⚠️ MERGE — labels per vehicle
          frontend/index.html          §0 §1 §2 §3 §7
          map/index.html               §4 §5 §6 §8 §9
          map/data/evr_rail.js         comments only (Railhead)
          env.example                  the eight new vars, commented out
          backend/tests/*              as above
 UNCHANGED, not in the zip: map/ipt_segments.js — pair still v9.
 No .pyc, no __pycache__.

================================================================================

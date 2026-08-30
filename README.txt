RBE Alliance 1 — Phase 5a: gates, per-leg asymmetric routing, and the turnaround split
=====================================================================================
Delivered 2026-08-30.  Zip: rbe-phase5a.zip
Extract over the repo root. Every path mirrors the repo; nothing needs renaming.

This file has to stand alone — you will read it after the chat has scrolled away.


-----------------------------------------------------------------------------------
0. BEFORE YOU APPLY THIS — ORDER MATTERS
-----------------------------------------------------------------------------------
Phase 5a adds a tenanted table and two columns on `routes`. Both depend on Phase 4.5
having run.

  1. BACK UP THE RENDER POSTGRES.  Two migrations now run on the next boot: 4.5's
     eleven-table tenant rebuild (still never executed against Postgres) and 5a's
     new table. Take the backup first; the paid plan has them since 2026-08-27.

  2. Apply rbe-phase45-tenant.zip if you have not already, then this zip.  They can
     go in together — main.py's lifespan runs them in the right order — but 4.5 must
     be in the tree.

  3. Watch the boot log for THREE lines:
       Phase 4.5: tenant key added to 11 table(s): ...      (4.5 worked)
       Phase 5a: N legacy gate(s) migrated: G001, ...       (5a's gate migration ran)
       ⚠️  Phase 4.5: tenant migration FAILED on: ...        (it did NOT — stop here)

     If no "Phase 5a: ... legacy gate(s) migrated" line appears at all, that is fine
     and expected when no location has a surveyed gate_lat/gate_lon. It is only wrong
     if you know some do.

  4. Confirm the app still shows the same locations, routes and forecasts. With one
     tenant every filter is a no-op, so ANYTHING that changed is a bug, not a feature.

NOTHING IN THIS DELIVERY HAS BEEN CONFIRMED IN THE DEPLOYMENT OR IN A BROWSER.


-----------------------------------------------------------------------------------
1. WHAT CHANGED
-----------------------------------------------------------------------------------
NEW FILE
  backend/gates.py        The gate model and, more importantly, the ONE place that
                          decides which coordinate HERE is given for a location in a
                          given direction. Every caller goes through it.

CHANGED
  backend/db.py           location_gates in _TENANT_DDL (tenant_id first in the
                          primary key) + TENANTED_TABLES + _TENANT_PK; origin_gate_id
                          and dest_gate_id added to the routes DDL AND to a new
                          init_gates_db(); count_gates().
  backend/network.py      _waypoint is per-leg and asymmetric; _waypoint_full added;
                          _turnaround_parts added; bake refusal on a blocked gate;
                          the route diagnostic now reports four resolutions, not two.
  backend/main.py         init_gates_db() + gates.migrate_legacy_gates() in the
                          lifespan; /api/gates and four admin gate endpoints.
  frontend/index.html     Gate editor with click-to-place on Locations; gate pickers
                          and a "gate blocked" badge on Routes; turnaround breakdown.
  backend/tests/*         New test_phase5a.py (167). The five existing python suites
                          now call db.init_gates_db() in their harnesses.
                          parse_frontend.js gains 20 Phase 5a assertions.
                          test_phase45.py's table count moves 11 -> 12.

NO FILE NEEDS DELETING.  Nothing was removed in this delivery, so there is no manual
step of that kind. (A zip can never delete a file — worth remembering next time.)

NO factors.json IN THIS ZIP.  Phase 5a reads it and does not change it, so your copy
is untouched and there is nothing to merge.


-----------------------------------------------------------------------------------
2. THE THREE THINGS TO ACTUALLY LOOK AT
-----------------------------------------------------------------------------------

(a) THE RETURN LEG IS NO LONGER THE OUTBOUND REVERSED.

_bake_leg() used to build the return leg by swapping the endpoints, which is exactly
right when a location has one symmetric access point. With a one-way gate it is wrong
in both directions at once — the empty truck leaves through the entry gate. The swap
still happens; what flips with it is which ROLE each end is asked for:

    loaded:  origin EXIT  -> destination ENTRY
    return:  destination EXIT -> origin ENTRY

All 11 _waypoint call sites now pass a role. A test asserts that none is left
direction-blind, and a separate one asserts the return leg is NOT the loaded leg
reversed — reverting the roles alone fails three assertions.

(b) NO NUMBER MOVES ON THE DAY THIS SHIPS.

B4 splits turnaround_hr into unloading + internal travel + safety check. The kickoff
note was blunt that this can silently rewrite every route's cycle time, trips, tonnes
and CO2. So:

  * the two new gate columns default to NULL, not 0;
  * gates.migrate_legacy_gates() does NOT guess an induction time for a migrated gate;
  * test_phase5a.py asserts, for EVERY vehicle profile in your real factors.json, that
    total_minutes == load + unload exactly, both before and after the migration.

Deliberately giving a migrated gate a 10-minute induction fails 7 assertions. Numbers
move only when you type one into a gate.

(c) B5 — YOU ANSWERED (c): THE DRAWN ROAD WINS.

Phase 4 already puts internal travel into route_geometry.duration_hr via a haul road's
assigned speed. Adding a flat gate-to-face allowance on top counts the same minutes
twice, and every fleet-size number would then be too big — wrong in the direction that
looks cautious. Implemented as:

  * a route whose baked geometry ran through a haul road gets internal travel from the
    geometry and NO flat figure   -> internal_travel_source: "drawn_road"
  * a route with none gets the flat per-gate figure -> "flat"
  * neither -> "none"
  * the suppressed flat figure is still REPORTED as internal_travel_flat_available. A
    number that vanishes silently is how someone later concludes the field was never
    wired up.

⚠️ THE PRECEDENCE IS PER ROUTE, NOT PER END, and this is a real limitation, not an
oversight. route_haul_roads records the route and the traversal order, not which SITE
the road belongs to. A route with a drawn road at the origin and a flat allowance owed
at the destination therefore UNDER-counts. 5b's gate<->area links are what make it
per-end. Written into network.py next to the code and into claude/phase5a-decisions.md.

⚠️ Also deliberate: the precedence reads the BAKED GEOMETRY, not the attachment. A haul
road attached but not yet baked contributes nothing to duration_hr, so treating it as
"already counted" would drop the flat figure for minutes nobody has. Asserted.


-----------------------------------------------------------------------------------
3. HOW RESOLUTION WORKS (the thing to check first if a route moves)
-----------------------------------------------------------------------------------
gates.resolve(location, role, gate_id) picks, in order:

  1. the gate the route explicitly names for that end, IF it serves this role;
  2. otherwise that location's default active gate for the role;
  3. otherwise its lowest-id active gate for the role;
  4. otherwise the legacy locations.gate_lat / gate_lon pair;
  5. otherwise the node's own lat/lon.

Steps 4 and 5 are why a database with no gate rows routes to exactly the coordinate it
routed to before 5a. locations.gate_lat/gate_lon is NOT dropped: it is step 4 and it is
the rollback path if 5a has to come out.

⚠️ A route may name a gate that does not serve the role being resolved — an egress-only
gate selected as the origin gate still has to answer "where does the truck arrive to
load?". That role falls through to step 2 rather than erroring. Visible, not silent:
/api/admin/diagnostics/route/{id} reports leaves_by and arrives_by per end with a
`source` of "selected" or "default".


-----------------------------------------------------------------------------------
4. B2 — A DEACTIVATED GATE
-----------------------------------------------------------------------------------
You asked for "refuse to bake, flag in UI". Both legs of the route are refused, not
one — baking the leg that still resolves would leave a route with one fresh and one
stale direction, which reads on the map as a working route. The refusal:

  * spends no HERE call;
  * is written into route_geometry.error naming the gate id, its name and the
    location, so the route list shows a reason rather than looking un-baked;
  * appears as `gate_blockers` on /api/routes/status and as a red "gate blocked"
    badge on the route row;
  * counts separately from `errors` in the batch result (`gate_refused`,
    `gate_blocked_routes`) — a refusal is something you can fix in the UI, a HERE
    failure is not, and lumping them together loses that.

A route pointing at a DELETED gate is treated the same way. Deleting a gate a route
still names is refused outright, with the routes listed and deactivation offered.


-----------------------------------------------------------------------------------
5. TESTS
-----------------------------------------------------------------------------------
    python3 backend/tests/test_phase5a.py       167 passed
    python3 backend/tests/test_phase2.py        142
    python3 backend/tests/test_phase3.py        154
    python3 backend/tests/test_phase4.py        202
    python3 backend/tests/test_phase25a.py      104
    python3 backend/tests/test_phase45.py       127
    python3 backend/tests/test_tenant_audit.py   22
    node     backend/tests/parse_frontend.js    119
    node     backend/tests/parse_map.js         250
    node     backend/tests/test_ipt_overlay.js  140
                                              -----
                                              1,427 passed, 0 failed

`ls backend/tests/` must show TEN files. It was nine. test_phase2.py has gone missing
from the repo four times — check it is there.

Four regressions were deliberately introduced and each failed the right assertions:
reverting the leg roles (3 fails), re-adding the flat figure on top of a drawn road
(3), letting a deactivated gate fall through silently (8), and giving a migrated gate
a guessed induction time (7).

WHAT IS *NOT* TESTED. Read this before quoting 1,427.
  * HERE is never called. haul.route_with_haul is a recorder. Every claim about the
    bake is about the COORDINATES handed to it, not what comes back.
  * No Postgres branch runs. location_gates and the two routes columns are created
    against SQLite only. 5a's Postgres path is an ADD COLUMN IF NOT EXISTS and a plain
    CREATE — no key rebuild — but it has not been executed.
  * Nothing in a browser. The gate editor, click-to-place and the route pickers are
    asserted at SOURCE level only.
  * The HTTP layer is stubbed. Endpoint bodies run; nothing proves the admin token
    actually rejects a request.


-----------------------------------------------------------------------------------
6. WHAT TO CHECK IN THE BROWSER
-----------------------------------------------------------------------------------
  1. Data Management -> Locations -> open a location. A "Gates" block appears under
     the old access-gate fields. On a location with a surveyed gate_lat/gate_lon it
     should already list ONE gate called "Main gate", direction "in + out", default.
  2. + Add gate -> "Place on map" -> click. The cursor goes to a crosshair and the
     click fills lat/lon WITHOUT selecting the node underneath.
  3. Set that gate to "Exit only" and add a second one "Entry only" somewhere else on
     the site. Then Routes -> expand a route using that location -> the Gates block.
  4. Bake the route, then GET /api/admin/diagnostics/route/{id}. `origin.asymmetric`
     should be true and leaves_by / arrives_by should name the two different gates.
  5. Deactivate a gate a route has selected. The route row should show a red "gate
     blocked" badge, and Bake should refuse by name without spending a HERE call.
  6. Put 10 minutes of induction on a gate. The Turn column gains a ‡ and the cycle
     time increases by exactly that. Before you do it, note the Turn figure — it must
     be unchanged from before this delivery.


-----------------------------------------------------------------------------------
7. OPEN / DELIBERATELY LEFT OUT
-----------------------------------------------------------------------------------
  * GATES ARE NOT DRAGGABLE ON THE MAP. Click-to-place is built; drag-to-move is not.
    Gates are not drawn as a map layer at all yet — you place one by clicking and edit
    the coordinates by re-placing it. The roadmap expected 5a to give drag "for free"
    via mapbox-gl-draw; that would be a new CDN dependency and the standing rule is to
    decide that deliberately rather than smuggle it in. Said plainly rather than
    implied as done.
  * NO GATE LAYER ON EITHER MAP. Existing gates are listed in the panel, not drawn.
  * B5's per-route (not per-end) precedence — section 2(c) above.
  * E1, the HERE haul-road probe, is STILL OWED and still blocks 5b. Not needed for
    5a. Run it before starting 5b:
        GET /api/admin/diagnostics/haul-roads?route_id=...&probe=true
  * Security is unchanged. LOGINS is still a client-side dict with plaintext passwords
    in view-source. The gate endpoints are admin-token-gated, which is an API boundary
    and not a user-permission one. Do not describe this deployment as secure.

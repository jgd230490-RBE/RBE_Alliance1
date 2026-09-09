rbe-route-vehicles-0909.zip
===========================
Delivered 2026-09-09. Extract over the repo root; the paths already match.

WHAT THIS IS
------------
"When selecting edit route I am unable to change vehicle."

You were right about the symptom. The cause was deliberate -- the panel hid the
vehicle tick-boxes whenever a route was being edited, and the label said so
("Vehicle(s) - baked on create"). But the capability was missing from the WHOLE
page, not just the edit form, and that is the part worth knowing:

  * A route has no vehicle field. Its vehicles are simply whichever profiles
    have a row in route_geometry.
  * The per-row "Bake" button re-bakes ONLY the profiles a route already has.
    It falls back to the "Show" selector's vehicle only when the route has none
    at all. So adding an 11th profile to a route that already has 7 was not
    possible except by baking the whole network.
  * clear_geometry() had no route filter -- it was network-wide by
    construction. So removing a profile from ONE route was not possible at all,
    from the UI or the API.

Both gaps are closed.

FILES IN THIS ZIP
-----------------
  backend/main.py                  clear-geometry endpoint takes route_id
  backend/network.py               clear_geometry(route_id=...), and
                                   route_edit_impact() reports forecast use by vehicle
  backend/tests/test_week1.py      +12 assertions (303 -> 315)
  backend/tests/parse_frontend.js  +11 assertions (259 -> 270)
  frontend/index.html              the vehicle tick-boxes while editing

Nothing is deleted by this delivery, so there is no manual removal step.
factors.json is NOT included -- nothing here touches it.

WHAT YOU WILL SEE
-----------------
Press Edit on a route. The vehicle list is now there, pre-ticked with whatever
that route is actually baked for, and every row says which way it is going:

    Artic Tipper (44t)        . baked          (green - nothing happens)
    Rigid 8-wheeler (32t)     . will be baked  (navy  - costs HERE calls)
    Artic Flatbed (44t)       . will be removed(red   - geometry is deleted)

The Save button counts it before you press it -- "Save changes . +1 -1
vehicle(s)" -- and the confirm dialog names the consequences:

  * removing a vehicle deletes BOTH legs and EVERY alternative for it on that
    route, so restoring it later is real HERE calls;
  * how many forecast lines on that route name the vehicle you are removing,
    and how many of those are Approved. forecasts.vehicle_type is the key the
    Forecasts page looks route analysis up by, so those lines will read
    "not baked" where their km, cycle time and vehicle count were;
  * a separate warning if you are removing the LAST vehicle, because the route
    then reads "not baked" everywhere including on the map.

Adding a vehicle is not gated by a dialog -- it only spends HERE calls -- but
it is reported in the status line afterwards.

TWO THINGS THAT COULD HAVE GONE WRONG QUIETLY, AND DID NOT
----------------------------------------------------------
1. The edit list is its OWN state, not the Create panel's. Sharing it would
   have been one line shorter and would have silently re-pointed the
   network-wide "Bake all . N vehicle(s)" button at whatever route you happened
   to have open -- while its own tooltip still said the list came from the
   Create panel. There is an assertion that bulk bake never reads the edit set.

2. Moving a route's endpoints already clears every profile server-side. The old
   code then re-baked "the profiles it had". If you move the endpoints AND
   untick a vehicle in the same save, that would have brought the unticked one
   straight back, and the route list would have shown it as though you had
   never touched it. A move now re-bakes the NEWLY TICKED set.

TESTS
-----
Full suite: 2,238 passed, 0 failed (was 2,215 -- +23).

  test_week1.py     303 -> 315   parse_frontend.js  259 -> 270
  test_phase5a 215 . test_phase4 202 . test_phase2 160 . test_phase3 154
  test_phase45 140 . test_ipt_overlay 140 . test_phase25a 104
  parse_map 484 . render_frontend 28 . test_tenant_audit 26   (all unchanged)

Ten regressions were applied on purpose and all ten were caught by the
assertion meant to catch them -- including that another TENANT's identical row
survives a route-scoped delete, and that the no-filter branch still means
"everything for this tenant", which is what "Clear all routes" depends on.

Two of the ten did not fail cleanly on the first pass, and both were defects in
MY test code rather than gaps in coverage:

  * one assertion subscripted a dict key directly instead of using .get(), so
    breaking that key raised KeyError and killed the run before the report
    printed. A caught regression read as an uncaught one. An assertion that can
    CRASH is worse than one that can fail: it hides every assertion after it.
    Fixed, and committed separately so the reason is in the history.
  * the first regression harness counted "FAIL:" lines and nothing else, so a
    run that died before reaching the new assertions reported zero failures --
    indistinguishable from a regression nobody caught. It now checks the run
    finished at all.

WHAT IS NOT TESTED
------------------
  * HERE is never called from the build sandbox. Nothing here proves a bake
    actually succeeds -- only that the right profile is asked for.
  * No browser ran. The tick-boxes, the labels and the confirm dialog are
    asserted at SOURCE level only. Nothing has rendered them.
  * The HTTP layer is stubbed, so clear-geometry's new route_id parameter is
    proved to reach network.clear_geometry() by calling the endpoint function
    directly. Nothing proves FastAPI parses it off the query string.
  * No Postgres branch ran. The delete is a plain DELETE with one more WHERE
    term, but it has only been executed against SQLite.

PLEASE CHECK ON THE LIVE SITE
-----------------------------
  1. Edit a route, tick a vehicle it does not have, save. The Vehicles column
     should gain a chip. Watch for the chip being HOLLOW -- that means the
     laden leg baked and the return did not, which is not enough for a cycle
     time.
  2. Edit it again, untick that vehicle, save. The chip should go, and only
     that route should lose it. Check a second route that also has that
     vehicle still has it.
  3. Move a route's endpoints and change its vehicles in the SAME save. The
     result should be exactly what you ticked -- nothing you unticked should
     come back.

STILL OPEN, AND UNCHANGED BY THIS DELIVERY
------------------------------------------
  * THE DETOUR ON R001. Diagnosed but NOT fixed -- see claude/route-management-0909.md.
    It is the laden leg into gate G001 at Soodevahe, and it is not the haul
    road (no route has one attached). The stored geometry has a 147 m jump
    between two consecutive vertices where its neighbours are 6-38 m apart.
    Settling whether that came from HERE or from our own section-joining code
    needs ONE call, and it needs your ADMIN_TOKEN:

      /api/admin/diagnostics/route/R001?profile=Artic%20Tipper%20(44t)&probe=true

    Send me that JSON. If it comes back with more than one section, every baked
    route in the network is suspect at its section joins.

  * The eight IPT access codes are STILL not set on Render. Oldest open item.

  * The push is still blocked: "jgd230490-RBE/RBE_Alliance1 is not in this
    session's authorized repository set." Seventh delivery. Adding the repo to
    the session's sources would end the zip chain and the partial-upload
    failure mode with it.

  * code-snapshot.md says the suite total should be 2,222. Its own twelve
    per-file counts sum to 2,215, which is what actually printed at HEAD before
    this delivery. The per-file numbers are right; the total is wrong. Check by
    the counts, not the total. Corrected in the notes, not in this zip.

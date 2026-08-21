RBE Alliance 1 — diagnostics, per-vehicle turnaround, visible failed routes
===========================================================================
Extract over your repo root (structure already matches), then commit.

NOTE: factors.json is included this time — the per-vehicle load/unload times
live in it. If you have edited factors.json on your side since, merge rather
than overwrite: the only additions are load_minutes / unload_minutes on each
vehicle, plus two updated _note strings.


WHAT THIS ANSWERS FROM YOUR TESTING
-----------------------------------

1. "Cycle times the same for all vehicles"
   CONFIRMED ISSUE, FIXED. Cycle = drive out + drive back + turnaround, and
   turnaround was a single global constant (12 min load + 8 min unload) applied
   to every vehicle. So whenever HERE returned the same drive time for two
   vehicles, their cycle times were identical by construction.

   Turnaround now lives per vehicle in factors.json:

       Rigid 7.5t              13 min
       Rigid 4-wheeler (18t)   16 min
       Rigid 6-wheeler (26t)   18 min
       Rigid 8-wheeler (32t)   20 min
       Artic Tipper (44t)      24 min
       Artic Flatbed (44t)     45 min   (strapping and unstrapping dominates)

   These are EDITABLE ESTIMATES, not measured figures — change them to match
   your operation. The old planning.load_minutes/unload_minutes remain as the
   fallback for any vehicle without its own values.

2. "CO2/kg the same for all vehicles"
   PARTLY EXPECTED. Artic Tipper (44t) and Artic Flatbed (44t) BOTH carry
   0.89 kg CO2e/km in factors.json, so identical CO2 between those two is
   correct data, not a bug. A Rigid 7.5t (0.55) should differ clearly.

   You said you saw identical figures including a lighter vehicle. If that is
   still true after this update, the new "Check factors.json" button will say
   why — see below.

3. "All routes the same for every vehicle" / "can't see any outbound"
   NOT YET ANSWERED — needs real data. Two very different causes produce
   identical output: the vehicle's dimensions never reaching HERE, or the road
   network genuinely routing every truck the same way (entirely plausible in
   Estonia if no route on the pair crosses a weight-restricted road).
   The new Probe button distinguishes them.

4. "No alternatives returned"
   LIKELY NORMAL, now visible. HERE returns fewer options than asked when it
   can't find meaningfully different ones, which is common for point-to-point
   truck routes on a sparse network. The Probe button reports how many routes
   HERE actually returned.


NEW: DIAGNOSTICS PANEL
----------------------
A "Diagnostics" card now sits in the right-hand column of Data Management,
below the bake panel. Two buttons:

  "Check factors.json"
      Shows how every vehicle profile resolves: payload, CO2/km, laden and tare
      weights, and turnaround. Warns loudly if a profile stored in the database
      is NOT a key in factors.json — that case falls back to _default, and every
      such profile then reports identical payload and CO2 no matter what. This
      is the first thing to click if vehicles still look interchangeable.

  "Probe HERE · <route>"
      Select a route on the Routes tab first. Routes that pair for EVERY vehicle
      with a real HERE call and shows, per vehicle: gross weight sent, distance
      returned, how many options came back, and whether any vehicle[...]
      parameters were present at all.

      A red X under "Truck params" means HERE routed it as a generic truck and
      the vehicle profile is not being applied — that would be the bug.
      All green ticks with identical distances means the road network genuinely
      offers one answer, which is not a bug.

      This spends real HERE requests (one per vehicle). Use sparingly.

Both endpoints sit behind ADMIN_TOKEN. Paste your token into the bake panel
box first — the diagnostics panel reads the same value.


NEW: FAILED ROUTES ARE VISIBLE
------------------------------
Answering "what happens if a route can't be found": previously the error was
stored and the vehicle chip went red, but the map filtered the route out
entirely — so an impossible haul looked exactly like one that hadn't been baked
yet. Unroutable pairs now draw as a DASHED RED STRAIGHT LINE between origin and
destination, are clickable, and appear in the legend as "No route found".


STILL OPEN
----------
Route constraints (low bridges, weight limits) — HERE reports these as section
"notices", and the Probe already collects them, so any that occur will show in
the probe response. Surfacing them per-route in the UI is a further piece of
work, not included here.

Manual route editing by dragging — feasible but it is Phase 4's mechanism:
dragging produces via-waypoints. Note HERE ignores the alternatives parameter
when a request carries via-waypoints, so a hand-edited route would drop to a
single option.

Colour-coding routes by vehicle — the map draws one profile at a time. Colouring
by vehicle means drawing all profiles at once, and if vehicles really do route
identically the lines overlap exactly and only the topmost colour shows. Worth
settling question 3 above first: the answer decides whether this feature would
be informative or misleading.


VERIFICATION
------------
83 backend assertions and 48 frontend assertions, all passing. Includes a test
that reproduces the shared-0.89 CO2 case explicitly, and one that simulates a
profile name mismatch to confirm the warning fires.

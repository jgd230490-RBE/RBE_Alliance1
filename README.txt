RBE Alliance 1 — alternatives on the map, promote to primary, per-route baking
==============================================================================
Extract over your repo root (structure matches), then commit.
factors.json is included — merge rather than overwrite if you have edited it.


1. ALTERNATIVES SHOWN IN GREY
-----------------------------
Select a route (click the row or the line) and the map now draws every option
cached for that vehicle and direction: the chosen line RED, the others GREY
underneath. Grey lines sit below the red one so the primary always reads on top.

Only the focused vehicle's options are drawn. Showing every vehicle's
alternatives at once would be a thicket rather than information.


2. CLICK A VEHICLE ROW TO HIGHLIGHT IT
--------------------------------------
Open a route's analysis (the ▶ arrow) and click any row. That option becomes the
red line on the map and the row is marked with a red dot. This works per vehicle
AND per alternative, so you can step through and compare actual roads without
touching the map's own Show/leg/alt selectors.

Clicking a row on a route that isn't selected selects it first.


3. MAKE AN ALTERNATIVE THE PRIMARY ROUTE
----------------------------------------
Every non-primary row in the analysis has a "Make primary" button. It swaps that
alternative into first place for that vehicle and direction, which means:

  - the map draws it as the route
  - it becomes what the analysis treats as the main option
  - the displaced route is NOT lost — it takes the promoted one's old slot

IMPORTANT: this is not durable against a re-bake. Routing the pair again
re-imports HERE's own ranking and your choice is lost. The UI says so after each
promotion. If you need promotions to survive re-baking, that needs a stored
preference — tell me and I'll add it.

The "Alt 1 / Alt 2" labels are HERE's ranking. "Primary" is whatever currently
sits at index 0, whether that is HERE's choice or yours.


4. BAKE PANEL REMOVED
---------------------
The "Vehicle profiles to bake" card is gone from both sub-tabs as requested. It
was doing three jobs, so each has been rehomed rather than dropped:

  ADMIN TOKEN -> now a single field in the header, next to the Show selectors.
      It saves as you type, and the create-route and diagnostics panels read it
      from there. One field for the whole page.

  BULK BAKE -> now a "Bake" action on each Routes row. It re-routes that route
      for the vehicles it already has geometry for. If it has none, it uses
      whatever the "Show" selector is set to.

  AUTO RE-BAKE AFTER MOVING A NODE OR GATE -> this used to re-bake against
      whichever profiles happened to be ticked, which could silently add or drop
      vehicles. It now re-bakes each affected route for the profiles THAT ROUTE
      already had. Nothing global to set, and it restores exactly what was there.
      This is better than the old behaviour, not a workaround for losing it.

The bulk endpoint /api/admin/bake-routes still exists on the backend and is
reachable directly if you ever need a full sweep — only the UI is gone.


NOTE ON RE-BAKING AND YOUR CHOICES
----------------------------------
Two things are reset by re-baking a route: a promoted alternative (above), and
nothing else. Gates, supplies/receives, IPT and route ids all survive.


VERIFICATION
------------
104 backend assertions, 76 frontend. New coverage includes the alt_index swap
round-tripping, the displaced route surviving, other vehicles being unaffected
by a promotion, and a node move returning each route's profiles so the re-bake
can restore them.

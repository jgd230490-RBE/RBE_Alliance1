RBE Alliance 1 — Phase 1-tail, Steps B & C
==========================================

HOW TO APPLY
------------
Extract this zip over your repo root. The folder structure already matches, so
every file lands where it belongs and overwrites the old version:

    RBE_Alliance1/
      .gitignore            <- new
      backend/
        db.py               <- replaced
        network.py          <- replaced
        here_routing.py     <- replaced
        main.py             <- replaced
      frontend/
        index.html          <- replaced

On macOS/Linux:   unzip -o rbe-step-b-c.zip -d /path/to/RBE_Alliance1
On Windows:       extract, then drag the backend/ and frontend/ folders onto
                  the repo root and confirm "replace files in destination"

Then commit:

    git add -A
    git commit -m "Phase 1-tail Steps B & C: directed legs, HERE alternatives, gates, analysis"
    git push

The .gitignore is new — it stops __pycache__ and local .db files being committed.


WHAT CHANGED
------------
Step B — every route is now baked in BOTH directions, each with up to three
HERE alternatives, and can route to a site's access gate instead of its marker.

  * route_geometry key widened to (route_id, vehicle_profile, leg, alt_index).
    Migration is automatic on first startup and idempotent. Existing rows become
    leg='loaded', alt_index=0 and stay valid — nothing needs re-baking.
  * The return leg routes at TARE weight (gross minus payload). An empty 44t
    artic weighs ~15t and may legally use roads the loaded one cannot, so the
    way home is a genuinely different line.
  * locations.gate_lat / gate_lon, both optional. Routing falls back to the
    marker until a gate is filled in. Changing a gate re-routes everything
    touching that location.

Step C — expanding row on the Routes table (click the little arrow) showing
haul-cycle figures per vehicle and alternative: cycle time, trips/day, tonnes
per day and month, CO2 per trip.


TWO THINGS TO KNOW
------------------
1. BAKING NOW COSTS TWICE AS MUCH.
   214 HERE calls for 107 routes, not 107 — alternatives are free (one response
   carries all three) but the two legs are not. Check your HERE free-tier limit
   before re-baking across several vehicle profiles. The bake panel's counter
   now says "legs" for this reason.

2. THE HERE CLIENT'S LIVE HTTP PATH IS UNTESTED.
   PyPI was blocked in the build sandbox, so the routing client only ever ran
   against a stub. On your first real bake, watch for:
     - whether HERE actually returns 3 alternatives on short hauls (often fewer)
     - whether multi-section responses appear
   Both are handled defensively, but neither has been seen for real.


SEPARATE — PLEASE ACTION
------------------------
ADMIN_TOKEN is not set on Render (or you weren't sure). Until it is, every admin
endpoint is open to anyone who finds the URL, including POST /api/admin/clear-routes
which would wipe all 107 routes.

  1. Render dashboard -> your service -> Environment
  2. Add ADMIN_TOKEN = a long random string   (openssl rand -hex 24)
  3. Save; Render redeploys
  4. Paste the same string into the "Admin token" box in the Data Management
     bake panel (it is remembered in your browser)

This is unrelated to Steps B and C — it applies to the code as it stands today.


VERIFICATION
------------
61 backend assertions against a scratch SQLite database with a stubbed HERE:
migration from a genuine pre-Step-B table, idempotence, leg/alternative
round-trip, laden vs unladen routing params, gate fallback and invalidation,
stale-alternative cleanup, error persistence, and the analysis maths.

36 frontend assertions: JSX parse plus server-side renders of the new
components and a full Data Management render.

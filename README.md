# RBE Alliance 1 Logistics

Forecasting app + public route map for the Rail Baltica Alliance 1 haul programme,
running as **one service**. Engineers enter monthly volumes (in m³, tonnes, or
vehicle loads); planners approve them; approved forecasts appear on the public
Mapbox route map. One repo, one Render URL — no CSV files to keep in sync.

```
your-render-url.onrender.com/         → forecasting app (staff)
your-render-url.onrender.com/map/     → public route map (embedded in the app too)
your-render-url.onrender.com/api/...  → forecast API (feeds the map)
```

---

## Deploy (browser only, ~10 minutes)

You never need a terminal.

1. **Create a GitHub repo.** On github.com → **New repository** → give it a name → **Create**.
2. **Upload these files.** On the empty repo page → **uploading an existing file** →
   drag in *everything inside this folder* (keep the `backend/`, `frontend/`, `map/`
   folders intact) → **Commit changes**.
3. **Deploy on Render.** Go to [dashboard.render.com](https://dashboard.render.com) →
   **New** → **Blueprint** → connect your GitHub repo. Render reads `render.yaml`
   and creates the web service **and** the database for you. Click **Apply**.
4. Wait for the build to finish, then open the service URL. The map is populated
   with starter forecasts on first launch.

To update anything later: edit the file on github.com (pencil icon) → **Commit**.
Render redeploys automatically.

---

## Change things without touching code

**Conversion factors — `backend/factors.json`**
The single place that controls how volumes convert. Edit a density or payload,
commit, done. Any material or vehicle you add here automatically shows up in the
app's dropdowns.

```json
"material_density_t_per_m3": { "Sand": 1.5, "Gravel": 1.7, "_default": 1.6 },
"vehicle_payload_t":         { "8x4 Tipper": 20, "Artic Tipper": 28, "_default": 20 }
```

**Map look & settings — `map/config.js`**
Brand colours, the Mapbox token, the unit the map paints in (`vehicles` / `t` / `m3`),
and the API location. Change a hex, commit, and the map restyles.

**Routes shown in the dropdown — `backend/seed_data/routes.json`**
The list of haul routes (id, origin, destination, material guess). These match the
route IDs baked into the map, so add/edit here if the route network changes.

---

## Who can do what

Open the app and use an access code on the **Staff sign in** screen:

| Code | Role | Can do |
|------|------|--------|
| `submitter123` | Submitter | Enter & save forecasts |
| `planner123` | Planner | + Approve / reject |
| `admin123` | Admin | + Approve / reject |

The public map needs no login.

> These codes are demo-only and live in the frontend. **Replace them with real
> authentication before this is exposed to anyone outside the team.**

---

## Security checklist (please do these)

- **Rotate the keys that were in the original project.** The uploaded zips contained
  a live `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, and database URL in `.env` files.
  This build does **not** include or use them, but since they were shared, rotate
  them (issue new ones, revoke the old) in the Anthropic console and GitHub settings.
- **Restrict the Mapbox token.** It's a public browser token (safe to ship), but in
  your Mapbox account add a URL restriction to your Render domain, and rotate it in
  `map/config.js` if you want a fresh one.
- **Replace the demo access codes** with real auth before any external use.

---

## How the pieces fit

- `backend/main.py` — FastAPI: the `/api` endpoints, plus it serves the app and the map.
- `backend/conversions.py` + `factors.json` — the m³ ↔ tonnes ↔ vehicles engine.
- `backend/db.py` — Postgres on Render (persistent), SQLite locally.
- `frontend/index.html` — the staff app (matrix, approvals) and the embedded public map.
- `map/index.html` + `map/config.js` + `map/data/*` — the Mapbox map. Route geometry
  lives in `map/data/` so the map file itself stays small and editable.

Forecasts join to the map on `route_id`. The public map only ever shows **approved**
forecasts, aggregated per route for whatever month window you pick.

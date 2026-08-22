"""
RBE Alliance 1 Logistics — backend.

Serves three things from one process:
  1. The forecasting API under /api/*
  2. The React forecasting app at /
  3. The Mapbox route map at /map/

The Public Route Map reads /api/public/route-forecasts, so the map and the
forecasting app are always in sync — no CSV files to keep up to date.
"""
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import conversions
import db
import seed          # retained so an old import doesn't break; forecast seeding is gone
import network
import here_routing
import taxonomy

ROOT = Path(__file__).resolve().parent.parent          # repo root
HERE = Path(__file__).resolve().parent                 # backend/
SEED_DIR = HERE / "seed_data"

START_YEAR = 2026          # month_index 1 == Jan 2026
MONTH_COUNT = 60           # 5-year horizon


# ------------------------------------------------------------------ startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # NOTE: forecast seeding is gone. seed.seed_if_empty() used to fire whenever the
    # forecasts table was empty and re-insert the 69 legacy rows from the WP3 CSV. Phase 2
    # empties that table on purpose, so leaving the call in would have resurrected the
    # legacy data on the very next restart. See seed.py.
    try:
        db.init_network_db()
        db.init_taxonomy_db()
        if network.seed_network():
            print("Seeded routing network from V2 (locations + routes).")
        network.backfill_location_roles()
        filled = network.backfill_supplies_receives().get("filled", 0)
        if filled:
            print(f"Backfilled supplies/receives on {filled} location(s) from the route network.")
        counts = taxonomy.seed_taxonomy()
        if any(counts.values()):
            print("Seeded taxonomy:", counts)
        meta = network.apply_node_meta()
        if meta.get("applied"):
            print(f"Applied salvaged vendor/detail to {meta['applied']} location(s).")
    except Exception as e:
        print("Network seed skipped:", e)
    yield


app = FastAPI(title="RBE Alliance 1 Logistics", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


# ------------------------------------------------------------------ schemas
class Cell(BaseModel):
    month_index: int                # 1..60
    quantity: float


class MatrixRow(BaseModel):
    """
    One forecast LINE: a route, a discipline, a section, one material, ONE vehicle.

    vehicle_type_2 and split_pct are gone. A load split across two vehicles is now two
    lines, which the widened key makes storable and which keeps each line's cycle time
    honest -- an Artic Flatbed turns round in 45 minutes against a Tipper's 24, and
    blending them into one payload figure hid that.

    discipline and section_id join the UNIQUE key, so they are never None on the way in:
    '' is the unassigned sentinel. A NULL here would leave the row unconstrained and
    ON CONFLICT would silently stop firing.
    """
    route_id: str
    discipline: str = ""                       # '' = unassigned
    section_id: str = ""                       # '' = unassigned
    material_type: str                         # category, e.g. "Small aggregate"
    material_description: Optional[str] = None # free-text detail
    vehicle_type: str
    submitted_by: Optional[str] = None         # lightweight submitter identity
    unit: str                                  # 'm3' | 't' | 'vehicles'
    cells: List[Cell]
    status: str = "Pending"


class StatusUpdate(BaseModel):
    status: str                     # 'Approved' | 'Rejected' | 'Pending'
    reject_reason: Optional[str] = None


# ------------------------------------------------------------------ helpers
# --- _load_routes() removed in Phase 2 ---
# It read seed_data/routes.json: 69 legacy routes with no distance_km key on any row, so
# /api/meta handed the UI routes with no distance and the dashboard's km, truck-km, CO2
# and intensity all read zero. Routes now come from the live network via
# network.meta_routes(). Nothing else in the codebase reads that file.


def _material_list(factors):
    return conversions.material_names(factors)


def _vehicle_list(factors):
    return conversions.vehicle_names(factors)


# ------------------------------------------------------------------ meta
@app.get("/api/health")
def health():
    return {"ok": True, "backend": "postgres" if db.IS_PG else "sqlite"}


@app.get("/api/meta")
def meta():
    """Everything the UI needs to build its dropdowns — kept in sync with factors.json."""
    factors = conversions.load_factors()
    strip = lambda d: {k: v for k, v in d.items() if not k.startswith("_")}
    return {
        "units": conversions.UNITS,
        "materials": conversions.material_names(factors),
        "vehicles": conversions.vehicle_names(factors),
        # rich taxonomy for the new submission matrix (Commit 2)
        "material_categories": strip(factors.get("material_categories", {})),
        "vehicle_details": strip(factors.get("vehicles", {})),
        # live network, not the retired legacy file — carries real distances
        "routes": network.meta_routes(),
        # Phase 2 taxonomy for the discipline / section pickers.
        # work_sections is empty until its key space is settled against Appendix E;
        # the UI must cope with an empty list rather than assume rows exist.
        "disciplines": taxonomy.list_disciplines(),
        "work_sections": taxonomy.list_work_sections(in_scope_only=True),
        "ipts": taxonomy.list_ipts(),
        "months": {
            "start_year": START_YEAR,
            "count": MONTH_COUNT,
            "years": [START_YEAR + i for i in range(MONTH_COUNT // 12)],
        },
        # flat maps kept for the map + dashboard, derived from the taxonomy
        "factors": conversions.flat_factors(factors),
        "seasonal_restrictions": factors.get("seasonal_restrictions", []),
        "mapbox_token": os.getenv("MAPBOX_TOKEN",
            "pk.eyJ1IjoiamdkMjMwNDE5OTAiLCJhIjoiY21xbnJzaTRrMDYyOTJxcXowczRxNTlxdyJ9.xujuSc3O8RcgKIitWNGIWg"),
    }


@app.get("/api/convert")
def convert(quantity: float, from_unit: str, to_unit: str,
            material: str = "", vehicle: str = ""):
    try:
        val = conversions.convert(quantity, from_unit, to_unit, material, vehicle)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"value": conversions.round_for_unit(val, to_unit), "unit": to_unit}


# ------------------------------------------------------------------ forecasts (staff)
@app.get("/api/forecasts")
def list_forecasts(submitted_by: Optional[str] = None):
    if submitted_by:
        return db.query(
            "SELECT * FROM forecasts WHERE submitted_by = ? ORDER BY route_id, month_index",
            (submitted_by,),
        )
    return db.query("SELECT * FROM forecasts ORDER BY route_id, month_index")


@app.delete("/api/forecasts/{route_id}")
def withdraw_route(route_id: str, submitted_by: Optional[str] = None,
                   from_: Optional[int] = Query(None, alias="from"),
                   to: Optional[int] = None,
                   discipline: Optional[str] = None,
                   section_id: Optional[str] = None):
    """
    Withdraw a forecast, optionally scoped to a month range, a submitter, and a line.

    Pass discipline and section_id to withdraw ONE line. Without them this deletes every
    line on the route in range -- which, now that a route can carry several disciplines,
    means withdrawing a substructure forecast would take a superstructure one with it.
    The My-submissions view sends the line keys.
    """
    clauses, params = ["route_id = ?"], [route_id]
    if submitted_by:
        clauses.append("submitted_by = ?"); params.append(submitted_by)
    if from_ is not None and to is not None:
        clauses.append("month_index BETWEEN ? AND ?"); params += [min(from_, to), max(from_, to)]
    if discipline is not None:
        clauses.append("discipline = ?"); params.append(discipline or "")
    if section_id is not None:
        clauses.append("section_id = ?"); params.append(section_id or "")
    where = " AND ".join(clauses)
    n = db.query(f"SELECT COUNT(*) AS n FROM forecasts WHERE {where}", tuple(params))[0]["n"]
    db.execute(f"DELETE FROM forecasts WHERE {where}", tuple(params))
    return {"status": "success", "route_id": route_id, "deleted": n,
            "scoped_to_line": discipline is not None or section_id is not None}


@app.get("/api/forecasts/summary")
def forecasts_summary():
    """One row per route: status, span, and window total in vehicles — for the ledger/approvals view."""
    rows = db.query("SELECT * FROM forecasts")
    factors = conversions.load_factors()
    by_line = {}
    for r in rows:
        # grouped per LINE, not per route. Grouping on route_id alone would merge a
        # substructure line and a superstructure line on the same route into one ledger
        # row, silently summing two disciplines' traffic under one material and vehicle.
        key = (r["route_id"], r.get("discipline") or "", r.get("section_id") or "")
        g = by_line.setdefault(key, {
            "route_id": r["route_id"],
            "discipline": r.get("discipline") or "",
            "section_id": r.get("section_id") or "",
            "months": [], "statuses": set(),
            "unit": r["unit"], "material_type": r["material_type"],
            "vehicle_type": r["vehicle_type"], "submitted_by": r.get("submitted_by"),
            "total_vehicles": 0.0,
        })
        g["months"].append(r["month_index"])
        g["statuses"].add(r["status"])
        g["total_vehicles"] += conversions.convert_row(r, "vehicles", factors)

    out = []
    for g in by_line.values():
        statuses = g.pop("statuses")
        g["status"] = next(iter(statuses)) if len(statuses) == 1 else "Mixed"
        months = sorted(g["months"])
        g["span"] = f"M{months[0]}\u2013M{months[-1]}" if months else "-"
        g["month_count"] = len(months)
        g.pop("months")
        g["total_vehicles"] = int(round(g["total_vehicles"]))
        out.append(g)
    out.sort(key=lambda x: (x["route_id"], x["discipline"], x["section_id"]))
    return out


def _line_id(route_id, month_index, discipline, section_id):
    """
    Primary key for one forecast line.

    Every part is non-empty-or-'' rather than None. The old form was
    f"{route_id}::{month_index}"; adding nullable parts would have produced
    'route::3::None::None' for two different rows and collided them.
    """
    return f"{route_id}::{month_index}::{discipline or ''}::{section_id or ''}"


@app.post("/api/forecasts/bulk")
def save_matrix_row(row: MatrixRow):
    """
    Upsert one forecast line across a year. Cells at 0 are cleared.

    Both the conflict target and the delete clause carry the full widened key. The delete
    matters as much as the insert: scoped to (route_id, month_index) alone, a submitter
    typing 0 into the superstructure line would have deleted the substructure line for
    that month too.
    """
    if row.unit not in conversions.UNITS:
        raise HTTPException(400, f"unit must be one of {conversions.UNITS}")

    disc = row.discipline or ""
    sect = row.section_id or ""
    touched = 0

    for c in row.cells:
        if not (1 <= c.month_index <= MONTH_COUNT):
            continue
        rid = _line_id(row.route_id, c.month_index, disc, sect)
        if c.quantity and c.quantity > 0:
            db.execute(
                """
                INSERT INTO forecasts
                    (id, route_id, month_index, discipline, section_id, quantity, unit,
                     material_type, material_description, vehicle_type, submitted_by,
                     status, reject_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (route_id, month_index, discipline, section_id) DO UPDATE SET
                    quantity             = EXCLUDED.quantity,
                    unit                 = EXCLUDED.unit,
                    material_type        = EXCLUDED.material_type,
                    material_description = EXCLUDED.material_description,
                    vehicle_type         = EXCLUDED.vehicle_type,
                    submitted_by         = EXCLUDED.submitted_by,
                    status               = EXCLUDED.status,
                    reject_reason        = NULL
                """,
                (rid, row.route_id, c.month_index, disc, sect, float(c.quantity), row.unit,
                 row.material_type, row.material_description, row.vehicle_type,
                 row.submitted_by, row.status, None),
            )
            touched += 1
        else:
            db.execute(
                "DELETE FROM forecasts WHERE route_id = ? AND month_index = ? "
                "AND discipline = ? AND section_id = ?",
                (row.route_id, c.month_index, disc, sect),
            )

    return {"status": "success", "route_id": row.route_id,
            "discipline": disc, "section_id": sect, "months_saved": touched,
            "caution": _coexisting_lines(row.route_id, disc, sect)}


def _coexisting_lines(route_id, discipline, section_id):
    """
    Other disciplines already forecasting on this route in the same months.

    Not an error -- ballast and fill to one destination in one month under different
    disciplines is the case the whole taxonomy exists for. But two people filling in the
    matrix independently can double-count the same lorry movements without ever seeing
    each other's line, so the save says so.
    """
    rows = db.query(
        "SELECT DISTINCT discipline, section_id, submitted_by, COUNT(*) AS months "
        "FROM forecasts WHERE route_id = ? AND NOT (discipline = ? AND section_id = ?) "
        "GROUP BY discipline, section_id, submitted_by",
        (route_id, discipline or "", section_id or ""),
    )
    if not rows:
        return None
    others = [{"discipline": r["discipline"] or "(unassigned)",
               "section_id": r["section_id"] or "(unassigned)",
               "submitted_by": r.get("submitted_by"),
               "months": r["months"]} for r in rows]
    return {
        "message": (f"{len(others)} other forecast line(s) already exist on {route_id}. "
                    "Check you are not double-counting the same vehicle movements."),
        "lines": others,
    }


@app.put("/api/routes/{route_id}/status")
def set_route_status(route_id: str, req: StatusUpdate,
                     discipline: Optional[str] = None,
                     section_id: Optional[str] = None):
    """
    Approve / reject / reopen a forecast.

    Pass discipline and section_id to act on ONE line. Omit them and every line on the
    route is updated -- which is the old behaviour, kept so nothing breaks, but it now
    means approving another discipline's forecast as a side effect. The Approvals view
    should send the line keys.
    """
    clauses, params = ["route_id = ?"], [route_id]
    scoped = discipline is not None or section_id is not None
    if scoped:
        clauses.append("discipline = ?"); params.append(discipline or "")
        clauses.append("section_id = ?"); params.append(section_id or "")
    where = " AND ".join(clauses)

    existing = db.query(f"SELECT COUNT(*) AS n FROM forecasts WHERE {where}", tuple(params))
    if existing[0]["n"] == 0:
        raise HTTPException(404, "No forecast for that route.")
    db.execute(f"UPDATE forecasts SET status = ?, reject_reason = ? WHERE {where}",
               tuple([req.status, req.reject_reason] + params))
    return {"status": "success", "route_id": route_id, "new_status": req.status,
            "scoped_to_line": scoped, "rows": existing[0]["n"]}


# ------------------------------------------------------------------ public feed (map)
@app.get("/api/public/route-forecasts")
def public_route_forecasts(
    from_: int = Query(1, alias="from", ge=1, le=MONTH_COUNT),
    to: int = Query(MONTH_COUNT, ge=1, le=MONTH_COUNT),
    unit: str = Query("vehicles"),
):
    """
    Approved forecasts only, aggregated per route over [from, to], returned in
    the requested unit. This is what the map paints. Shape:
        { "hr-ew-...": {"avg": .., "peak": .., "total": .., "unit": ".."}, ... }
    """
    if unit not in conversions.UNITS:
        raise HTTPException(400, f"unit must be one of {conversions.UNITS}")
    lo, hi = min(from_, to), max(from_, to)
    rows = db.query(
        "SELECT * FROM forecasts WHERE status = 'Approved' AND month_index BETWEEN ? AND ?",
        (lo, hi),
    )
    factors = conversions.load_factors()
    agg = {}
    for r in rows:
        v = conversions.convert_row(r, unit, factors)
        a = agg.setdefault(r["route_id"], {"total": 0.0, "peak": 0.0, "months": 0})
        a["total"] += v
        a["peak"] = max(a["peak"], v)
        a["months"] += 1

    out = {}
    for rid, a in agg.items():
        months = a["months"] or 1
        out[rid] = {
            "avg": conversions.round_for_unit(a["total"] / months, unit),
            "peak": conversions.round_for_unit(a["peak"], unit),
            "total": conversions.round_for_unit(a["total"], unit),
            "unit": unit,
        }
    return out


@app.get("/api/public/forecast-matrix")
def public_forecast_matrix(
    from_: int = Query(1, alias="from", ge=1, le=MONTH_COUNT),
    to: int = Query(MONTH_COUNT, ge=1, le=MONTH_COUNT),
    unit: str = Query("vehicles"),
):
    """
    Approved forecasts as a month-by-month grid (for the map's detail table).
    Returns a list, one entry per route, each with a {month_index: quantity} map
    in the requested unit, plus the route's material and vehicle.
    """
    if unit not in conversions.UNITS:
        raise HTTPException(400, f"unit must be one of {conversions.UNITS}")
    lo, hi = min(from_, to), max(from_, to)
    rows = db.query(
        "SELECT * FROM forecasts WHERE status = 'Approved' AND month_index BETWEEN ? AND ? ORDER BY route_id, month_index",
        (lo, hi),
    )
    factors = conversions.load_factors()
    by_line = {}
    for r in rows:
        # one entry per LINE — a route carrying two disciplines is two rows in the
        # detail table, not one row with both disciplines' tonnage silently added up
        key = (r["route_id"], r.get("discipline") or "", r.get("section_id") or "")
        g = by_line.setdefault(key, {
            "route_id": r["route_id"],
            "discipline": r.get("discipline") or "",
            "section_id": r.get("section_id") or "",
            "material_type": r["material_type"],
            "material_description": r.get("material_description"),
            "vehicle_type": r["vehicle_type"],
            "monthly": {}, "total": 0.0,
        })
        v = conversions.convert_row(r, unit, factors)
        g["monthly"][str(r["month_index"])] = conversions.round_for_unit(v, unit)
        g["total"] += v
    out = list(by_line.values())
    for g in out:
        g["total"] = conversions.round_for_unit(g["total"], unit)
    out.sort(key=lambda x: x["total"], reverse=True)
    return {"unit": unit, "from": lo, "to": hi, "routes": out}


# ------------------------------------------------------------------ routing network (Phase 0)
@app.get("/api/routes/status")
def routes_status():
    return network.routes_status()


@app.get("/api/routes/summary")
def routes_summary():
    return network.summary()


@app.get("/api/routes/geojson")
def routes_geojson(profile: str = network.DEFAULT_PROFILE,
                   leg: str = "loaded", alt_index: int = 0):
    """
    Map source. A route holds several geometries per profile after Step B (laden and
    unladen legs, each with HERE alternatives), so the caller picks which one to draw.
    """
    return network.routes_geojson(profile, leg=leg, alt_index=alt_index)


@app.get("/api/routes/{route_id}/geometries")
def route_geometries(route_id: str, profile: Optional[str] = None,
                     leg: Optional[str] = None):
    """
    Every cached geometry for one route, one feature per (profile, leg, alternative).
    Used to draw a selected route's alternatives alongside the chosen line.
    """
    return network.route_geometries(route_id, profile=profile, leg=leg)


@app.post("/api/admin/routes/{route_id}/promote-alt")
def promote_alt(route_id: str, profile: str = network.DEFAULT_PROFILE,
                alt_index: int = Query(..., ge=1, le=10),
                leg: str = "loaded", token: Optional[str] = None):
    """
    Make one of HERE's alternatives the primary route for this vehicle and direction —
    a planner overruling the router's ranking. Swaps with the current primary, which
    stays available. Re-baking the route restores HERE's own ordering.
    """
    _check_admin(token)
    if leg not in network.LEGS:
        raise HTTPException(400, f"leg must be one of {list(network.LEGS)}")
    return network.promote_alternative(route_id, profile, alt_index, leg=leg)


@app.get("/api/routes/{route_id}/analysis")
def route_analysis(route_id: str, profile: Optional[str] = None):
    """
    Haul-cycle analysis for one route: cycle time, trips/day, tonnes and CO2 per vehicle
    profile x alternative. Derived from cached geometry plus factors.json planning
    constants — no HERE calls, so it is cheap to open per row.
    """
    return network.route_analysis(route_id, profiles=[profile] if profile else None)


@app.get("/api/routes/analysis-batch")
def routes_analysis_batch(route_ids: Optional[str] = None, profile: Optional[str] = None):
    """
    Primary-option haul-cycle figures for many routes at once, keyed route_id -> profile.

    This exists so the dashboard can stop computing its own cycle time. It had
    `round / 45 km/h + (12 + 8) / 60` against the backend's real HERE leg durations and
    per-vehicle turnaround, and the two disagreed by up to 47% on speed alone and 125% on
    turnaround for an Artic Flatbed. Converging them by making the dashboard call the
    backend is the point; a per-route request for every route on screen was the only
    reason not to, and this removes it.

    Only alt_index 0 is returned — the primary route, which is what a forecast's distance
    follows. Routes with no baked geometry are simply absent from the result rather than
    present with zeros; a caller must show that as 'not baked', not as nothing moving.
    """
    ids = [r.strip() for r in (route_ids or "").split(",") if r.strip()]
    if not ids:
        ids = [r["id"] for r in db.query("SELECT id FROM routes ORDER BY id")]

    profs = [profile] if profile else None
    out, missing = {}, []
    for rid in ids:
        res = network.route_analysis(rid, profiles=profs)
        rows = [r for r in res.get("rows", []) if r.get("alt_index") == 0]
        if not rows:
            missing.append(rid)
            continue
        out[rid] = {r["profile"]: r for r in rows}

    return {"analysis": out, "not_baked": missing,
            "planning": conversions.load_factors().get("planning", {}),
            "count": len(out)}


@app.get("/api/locations/geojson")
def locations_geojson():
    return network.locations_geojson()


@app.post("/api/admin/bake-routes")
def bake_routes(profile: str = network.DEFAULT_PROFILE,
                limit: int = Query(25, ge=1, le=60),
                legs: Optional[str] = None,
                token: Optional[str] = None):
    """
    Compute + cache HERE truck geometry for a batch of routes. Call repeatedly
    until 'remaining' is 0. Protected by ADMIN_TOKEN if that env var is set.

    'limit' counts legs rather than routes — each leg is one HERE call. Pass
    legs='loaded' to skip the unladen return and halve the call count.
    """
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if admin_token and token != admin_token:
        raise HTTPException(403, "bad or missing admin token")
    want = tuple(l.strip() for l in legs.split(",") if l.strip() in network.LEGS) if legs else network.LEGS
    return network.bake_batch(profile=profile, limit=limit, legs=want or network.LEGS)


@app.post("/api/admin/clear-geometry")
def clear_geometry(profile: Optional[str] = None, leg: Optional[str] = None,
                   token: Optional[str] = None):
    """Clear cached geometry (a profile and/or leg, or all) so it can be re-baked."""
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if admin_token and token != admin_token:
        raise HTTPException(403, "bad or missing admin token")
    network.clear_geometry(profile, leg=leg)
    return {"status": "cleared", "profile": profile or "all", "leg": leg or "all"}


class LocationIn(BaseModel):
    name: str
    role: str = "both"                     # 'origin' | 'destination' | 'both'
    materials: List[str] = []              # legacy: kept in sync with supplies
    supplies: List[str] = []               # categories an origin/both can provide
    receives: List[str] = []               # categories a destination/both can accept
    lat: float
    lon: float
    loc_type: Optional[str] = None
    # access/egress gate — the point HERE actually routes to when it differs from the
    # marker. Null (or omitted) means no gate surveyed yet; routing uses lat/lon.
    gate_lat: Optional[float] = None
    gate_lon: Optional[float] = None


def _fields_set(model):
    """Which fields the client actually sent (pydantic v1 and v2 spell this differently)."""
    return getattr(model, "model_fields_set", None) or getattr(model, "__fields_set__", set())


def _check_admin(token):
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if admin_token and token != admin_token:
        raise HTTPException(403, "bad or missing admin token")


@app.post("/api/admin/locations")
def create_location(body: LocationIn, token: Optional[str] = None):
    _check_admin(token)
    return network.create_location(body.name, body.role, body.materials, body.lat, body.lon,
                                   body.loc_type, supplies=body.supplies, receives=body.receives,
                                   gate_lat=body.gate_lat, gate_lon=body.gate_lon)


@app.put("/api/admin/locations/{location_id}")
def update_location(location_id: str, body: LocationIn, token: Optional[str] = None):
    _check_admin(token)
    # only touch the gate if the client mentioned it — otherwise a caller that doesn't
    # know about gates (or an older client) would silently wipe one that was set
    gate_given = bool({"gate_lat", "gate_lon"} & set(_fields_set(body)))
    return network.update_location(location_id, body.name, body.role, body.materials,
                                   body.lat, body.lon, body.loc_type,
                                   supplies=body.supplies, receives=body.receives,
                                   gate_lat=body.gate_lat, gate_lon=body.gate_lon,
                                   gate_given=gate_given)


@app.delete("/api/admin/locations/{location_id}")
def delete_location(location_id: str, token: Optional[str] = None):
    _check_admin(token)
    return network.delete_location(location_id)


class RouteIn(BaseModel):
    origin_id: str
    dest_id: str
    material_category: Optional[str] = None
    route_id: Optional[str] = None          # optional manual id; auto R00n if blank
    ipt: Optional[str] = None


@app.post("/api/admin/routes")
def create_route(body: RouteIn, token: Optional[str] = None):
    _check_admin(token)
    return network.create_route(body.origin_id, body.dest_id, body.material_category,
                                route_id=body.route_id, ipt=body.ipt)


@app.delete("/api/admin/routes/{route_id}")
def delete_route(route_id: str, token: Optional[str] = None):
    _check_admin(token)
    return network.delete_route(route_id)


@app.post("/api/admin/clear-routes")
def clear_routes(token: Optional[str] = None):
    _check_admin(token)
    return network.clear_routes()


# ------------------------------------------------------------------ diagnostics
@app.get("/api/admin/diagnostics/factors")
def diagnostics_factors(token: Optional[str] = None):
    """
    How each vehicle profile resolves against factors.json. Read this first when the
    analysis table reports the same payload or CO2 for different vehicles — a profile
    with in_factors=false has fallen through to _default and every such profile will
    report identical figures.
    """
    _check_admin(token)
    return network.factors_diagnostics()


@app.get("/api/admin/diagnostics/route/{route_id}")
def diagnostics_route(route_id: str, profile: str = network.DEFAULT_PROFILE,
                      probe: bool = False, token: Optional[str] = None):
    """
    What is cached for a route, which coordinates it routes to (gate or marker), and
    optionally a live HERE call showing exactly what was sent and returned.

    probe=true spends two HERE requests. It is the only way to see whether HERE
    declined an alternatives request or whether truck dimensions reached it at all.
    """
    _check_admin(token)
    return network.route_diagnostics(route_id, profile, probe=probe)


@app.get("/api/admin/diagnostics/compare/{route_id}")
def diagnostics_compare(route_id: str, probe: bool = False, token: Optional[str] = None):
    """
    Route one pair for every vehicle profile and report whether anything differs.
    Distinguishes 'the network offers no alternative' from 'the vehicle never reached
    HERE'. probe=true spends one HERE request per profile.
    """
    _check_admin(token)
    return network.compare_profiles(route_id, probe=probe)


@app.post("/api/admin/bake-route")
def bake_route(route_id: str, profile: str = network.DEFAULT_PROFILE,
               legs: Optional[str] = None, token: Optional[str] = None):
    """Bake one route. Both legs by default; pass legs='loaded' for the outbound only."""
    _check_admin(token)
    want = tuple(l.strip() for l in legs.split(",") if l.strip() in network.LEGS) if legs else None
    return network.bake_route(route_id, profile, legs=want)


# ------------------------------------------------------------------ static
# Map (Mapbox app) at /map/ ; must be mounted before the catch-all "/".
app.mount("/map", StaticFiles(directory=str(ROOT / "map"), html=True), name="map")


@app.get("/")
def frontend_index():
    return FileResponse(str(ROOT / "frontend" / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

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
import seed

ROOT = Path(__file__).resolve().parent.parent          # repo root
HERE = Path(__file__).resolve().parent                 # backend/
SEED_DIR = HERE / "seed_data"

START_YEAR = 2026          # month_index 1 == Jan 2026
MONTH_COUNT = 60           # 5-year horizon


# ------------------------------------------------------------------ startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    try:
        if seed.seed_if_empty():
            print("Seeded starter forecasts (table was empty).")
    except Exception as e:                              # never block startup on seed
        print("Seed skipped:", e)
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
    route_id: str
    material_type: str
    vehicle_type: str
    unit: str                       # 'm3' | 't' | 'vehicles'
    cells: List[Cell]
    status: str = "Pending"


class StatusUpdate(BaseModel):
    status: str                     # 'Approved' | 'Rejected' | 'Pending'
    reject_reason: Optional[str] = None


# ------------------------------------------------------------------ helpers
def _load_routes():
    with open(SEED_DIR / "routes.json", encoding="utf-8") as f:
        return json.load(f)


def _material_list(factors):
    return [k for k in factors["material_density_t_per_m3"] if not k.startswith("_")]


def _vehicle_list(factors):
    return [k for k in factors["vehicle_payload_t"] if not k.startswith("_")]


# ------------------------------------------------------------------ meta
@app.get("/api/health")
def health():
    return {"ok": True, "backend": "postgres" if db.IS_PG else "sqlite"}


@app.get("/api/meta")
def meta():
    """Everything the UI needs to build its dropdowns — kept in sync with factors.json."""
    factors = conversions.load_factors()
    return {
        "units": conversions.UNITS,
        "materials": _material_list(factors),
        "vehicles": _vehicle_list(factors),
        "routes": _load_routes(),
        "months": {
            "start_year": START_YEAR,
            "count": MONTH_COUNT,
            "years": [START_YEAR + i for i in range(MONTH_COUNT // 12)],
        },
        "factors": factors,          # lets the UI preview conversions with no round-trip
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
def list_forecasts():
    return db.query("SELECT * FROM forecasts ORDER BY route_id, month_index")


@app.get("/api/forecasts/summary")
def forecasts_summary():
    """One row per route: status, span, and window total in vehicles — for the ledger/approvals view."""
    rows = db.query("SELECT * FROM forecasts")
    factors = conversions.load_factors()
    by_route = {}
    for r in rows:
        g = by_route.setdefault(r["route_id"], {
            "route_id": r["route_id"], "months": [], "statuses": set(),
            "unit": r["unit"], "material_type": r["material_type"],
            "vehicle_type": r["vehicle_type"], "total_vehicles": 0.0,
        })
        g["months"].append(r["month_index"])
        g["statuses"].add(r["status"])
        g["total_vehicles"] += conversions.convert(
            r["quantity"], r["unit"], "vehicles", r["material_type"], r["vehicle_type"], factors)

    out = []
    for g in by_route.values():
        statuses = g.pop("statuses")
        g["status"] = next(iter(statuses)) if len(statuses) == 1 else "Mixed"
        months = sorted(g["months"])
        g["span"] = f"M{months[0]}\u2013M{months[-1]}" if months else "-"
        g["month_count"] = len(months)
        g.pop("months")
        g["total_vehicles"] = int(round(g["total_vehicles"]))
        out.append(g)
    out.sort(key=lambda x: x["route_id"])
    return out


@app.post("/api/forecasts/bulk")
def save_matrix_row(row: MatrixRow):
    """Upsert a whole route's monthly forecast. Cells at 0 are cleared."""
    if row.unit not in conversions.UNITS:
        raise HTTPException(400, f"unit must be one of {conversions.UNITS}")
    for c in row.cells:
        if not (1 <= c.month_index <= MONTH_COUNT):
            continue
        rid = f"{row.route_id}::{c.month_index}"
        if c.quantity and c.quantity > 0:
            db.execute(
                """
                INSERT INTO forecasts
                    (id, route_id, month_index, quantity, unit, material_type, vehicle_type, status, reject_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (route_id, month_index) DO UPDATE SET
                    quantity      = EXCLUDED.quantity,
                    unit          = EXCLUDED.unit,
                    material_type = EXCLUDED.material_type,
                    vehicle_type  = EXCLUDED.vehicle_type,
                    status        = EXCLUDED.status,
                    reject_reason = NULL
                """,
                (rid, row.route_id, c.month_index, float(c.quantity), row.unit,
                 row.material_type, row.vehicle_type, row.status, None),
            )
        else:
            db.execute("DELETE FROM forecasts WHERE route_id = ? AND month_index = ?",
                       (row.route_id, c.month_index))
    return {"status": "success", "route_id": row.route_id}


@app.put("/api/routes/{route_id}/status")
def set_route_status(route_id: str, req: StatusUpdate):
    """Approve / reject / reopen every cell of a route at once."""
    existing = db.query("SELECT COUNT(*) AS n FROM forecasts WHERE route_id = ?", (route_id,))
    if existing[0]["n"] == 0:
        raise HTTPException(404, "No forecast for that route.")
    db.execute("UPDATE forecasts SET status = ?, reject_reason = ? WHERE route_id = ?",
               (req.status, req.reject_reason, route_id))
    return {"status": "success", "route_id": route_id, "new_status": req.status}


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
        v = conversions.convert(r["quantity"], r["unit"], unit,
                                r["material_type"], r["vehicle_type"], factors)
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
    by_route = {}
    for r in rows:
        g = by_route.setdefault(r["route_id"], {
            "route_id": r["route_id"], "material_type": r["material_type"],
            "vehicle_type": r["vehicle_type"], "monthly": {}, "total": 0.0,
        })
        v = conversions.convert(r["quantity"], r["unit"], unit,
                                r["material_type"], r["vehicle_type"], factors)
        g["monthly"][str(r["month_index"])] = conversions.round_for_unit(v, unit)
        g["total"] += v
    out = list(by_route.values())
    for g in out:
        g["total"] = conversions.round_for_unit(g["total"], unit)
    out.sort(key=lambda x: x["total"], reverse=True)
    return {"unit": unit, "from": lo, "to": hi, "routes": out}


# ------------------------------------------------------------------ static
# Map (Mapbox app) at /map/ ; must be mounted before the catch-all "/".
app.mount("/map", StaticFiles(directory=str(ROOT / "map"), html=True), name="map")


@app.get("/")
def frontend_index():
    return FileResponse(str(ROOT / "frontend" / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

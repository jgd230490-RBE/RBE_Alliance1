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
import zones
import haul          # Phase 4 — temporary haul roads
import restrictions  # Phase 2.5a — Tark Tee restriction layers (proxied, reprojected)
import streetview    # Phase 2.5a — Google Street View proxy (key stays server-side)
import gates         # Phase 5a — multiple gates per location, with a direction on each
import weeks         # Week 1 — the 4-week look-ahead and typed actuals (Tasks C, D)
import stockpiles    # Week 1 — stockpile capacity and typed consumption (Task D2)

ROOT = Path(__file__).resolve().parent.parent          # repo root
HERE = Path(__file__).resolve().parent                 # backend/
SEED_DIR = HERE / "seed_data"

START_YEAR = 2026          # month_index 1 == Jan 2026
MONTH_COUNT = 60           # 5-year horizon

# One definition of the epoch, not two. stockpiles.py needs it for the public-map
# popup and cannot import main (circular), so it carries a default that this
# overwrites at import time — and test_week1.py asserts the two agree.
stockpiles.START_YEAR = START_YEAR


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
        db.init_zones_db()          # Phase 3. No seed — zones are drawn, never shipped.
        db.init_gates_db()          # Phase 5a. Table + the two gate columns on routes.
        # Week 1. forecast_weeks + stockpile_weeks, and the three capacity columns on
        # locations. Must sit HERE — after init_network_db(), which creates the table
        # those columns are ALTERed onto, and before init_tenant(), which rebuilds it.
        db.init_weeks_db()
        # Phase 4.5. Must sit exactly here: init_tenant() migrates a pre-4.5 database by
        # rebuilding each table, copying the columns the table actually has — several of
        # which the init_* calls above add by ALTER. Run it before them and those columns
        # are silently dropped; run it after the seeds below and the seeds write rows into
        # a table that has not got its tenant key yet.
        db.init_tenant()
        # Phase 5a. AFTER init_tenant(), because it inserts rows and every insert
        # filters on the tenant key the migration has just put in place. Idempotent:
        # a location that already has a gate row is skipped, so a restart cannot mint
        # a second 'Main gate'.
        try:
            gates.migrate_legacy_gates()
        except Exception as e:
            print("⚠️  Phase 5a: legacy gate migration failed:", e)
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

    Phase 4.5 widened that UNIQUE key again, to
    (tenant_id, route_id, month_index, discipline, section_id). The same failure mode
    applies to the new column: the ON CONFLICT target in save_matrix_row() must name
    tenant_id too, or it no longer matches any constraint, the upsert stops firing, and
    re-saving a matrix row inserts a duplicate instead of updating the row already there.
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
        # every vehicle, unchanged. The four EU-named planning vehicles added on
        # 2026-09-01 are IN this list, not instead of it: baked geometry is keyed on
        # vehicle_profile and every existing route names one of the older six, so a
        # picker that could not offer them would strand those routes.
        "vehicles": conversions.vehicle_names(factors),
        # ...and which of them are the four to lead with. A NAME LIST, not a re-ordering
        # of `vehicles` — the map, the dashboard and every saved forecast index into
        # `vehicles` and `factors.vehicle_payload_t` by name, so the full set has to stay
        # complete and in its existing order.
        "planning_vehicles": conversions.planning_vehicle_names(factors),
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
def list_forecasts(submitted_by: Optional[str] = None, route_id: Optional[str] = None):
    """
    Forecast rows, optionally scoped.

    route_id was added because the submission matrix reloads its cells whenever the
    route, year, discipline OR section changes, and it was pulling the entire forecasts
    table each time to find twelve of them. Four times the reload frequency on a
    whole-table read is not a shape that ages well.
    """
    clauses, params = [], []
    if submitted_by:
        clauses.append("submitted_by = ?"); params.append(submitted_by)
    if route_id:
        clauses.append("route_id = ?"); params.append(route_id)
    # tenant_id is written into the literal rather than pushed through `clauses`, so the
    # filter is in the SQL text on every branch (there is no longer a no-WHERE branch) and
    # its ? is always first — which is why db.current_tenant() leads the params tuple.
    where = "".join(" AND " + c for c in clauses)
    return db.query(
        f"SELECT * FROM forecasts WHERE tenant_id = ?{where} ORDER BY route_id, month_index",
        tuple([db.current_tenant()] + params))


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
    # tenant_id leads the literal WHERE on both statements, so db.current_tenant() leads
    # the params tuple ahead of everything `clauses` accumulated.
    n = db.query(f"SELECT COUNT(*) AS n FROM forecasts WHERE tenant_id = ? AND {where}",
                 tuple([db.current_tenant()] + params))[0]["n"]
    db.execute(f"DELETE FROM forecasts WHERE tenant_id = ? AND {where}",
               tuple([db.current_tenant()] + params))
    return {"status": "success", "route_id": route_id, "deleted": n,
            "scoped_to_line": discipline is not None or section_id is not None}


@app.get("/api/forecasts/summary")
def forecasts_summary():
    """One row per route: status, span, and window total in vehicles — for the ledger/approvals view."""
    rows = db.query("SELECT * FROM forecasts WHERE tenant_id = ?", (db.current_tenant(),))
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
            # the rejection reason was being written and then never read by anything,
            # so a submitter saw "Rejected" and no explanation
            "reject_reason": None,
            "total_vehicles": 0.0,
        })
        g["months"].append(r["month_index"])
        g["statuses"].add(r["status"])
        if r.get("reject_reason") and not g["reject_reason"]:
            g["reject_reason"] = r["reject_reason"]
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
                    (tenant_id, id, route_id, month_index, discipline, section_id,
                     quantity, unit,
                     material_type, material_description, vehicle_type, submitted_by,
                     status, reject_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                -- must match the widened UNIQUE key exactly; see MatrixRow's docstring
                ON CONFLICT (tenant_id, route_id, month_index, discipline, section_id)
                DO UPDATE SET
                    quantity             = EXCLUDED.quantity,
                    unit                 = EXCLUDED.unit,
                    material_type        = EXCLUDED.material_type,
                    material_description = EXCLUDED.material_description,
                    vehicle_type         = EXCLUDED.vehicle_type,
                    submitted_by         = EXCLUDED.submitted_by,
                    status               = EXCLUDED.status,
                    reject_reason        = NULL
                """,
                (db.current_tenant(),
                 rid, row.route_id, c.month_index, disc, sect, float(c.quantity), row.unit,
                 row.material_type, row.material_description, row.vehicle_type,
                 row.submitted_by, row.status, None),
            )
            touched += 1
        else:
            db.execute(
                "DELETE FROM forecasts WHERE tenant_id = ? AND route_id = ? "
                "AND month_index = ? AND discipline = ? AND section_id = ?",
                (db.current_tenant(), row.route_id, c.month_index, disc, sect),
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
        "FROM forecasts WHERE tenant_id = ? AND route_id = ? "
        "AND NOT (discipline = ? AND section_id = ?) "
        "GROUP BY discipline, section_id, submitted_by",
        (db.current_tenant(), route_id, discipline or "", section_id or ""),
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

    existing = db.query(f"SELECT COUNT(*) AS n FROM forecasts WHERE tenant_id = ? AND {where}",
                        tuple([db.current_tenant()] + params))
    if existing[0]["n"] == 0:
        raise HTTPException(404, "No forecast for that route.")
    # tenant_id goes in the WHERE, never the SET — an UPDATE with no tenant predicate
    # rewrites every tenant's rows. The two SET placeholders come first in the params,
    # then the tenant, then whatever `clauses` accumulated.
    db.execute(f"UPDATE forecasts SET status = ?, reject_reason = ? "
               f"WHERE tenant_id = ? AND {where}",
               tuple([req.status, req.reject_reason, db.current_tenant()] + params))

    # Week 1, Task C: approving a month line is what materialises its four weeks.
    #
    # Driven off the rows that are now Approved rather than off the request, because
    # an unscoped call updates every line on the route and each of them needs its own
    # four weeks. A re-approval refreshes the `derived` weeks and leaves `edited` and
    # `confirmed` ones alone — see weeks.materialise_line().
    #
    # ⚠️ Rejecting or reopening deletes NOTHING. A confirmed week with an actual typed
    # against it is a record of what happened on site; dropping it because a planner
    # reopened the month would destroy that.
    materialised = None
    if req.status == "Approved":
        try:
            done = {"created": 0, "refreshed": 0, "kept": 0}
            lines = db.query(
                "SELECT DISTINCT discipline, section_id FROM forecasts "
                "WHERE tenant_id = ? AND route_id = ? AND status = 'Approved'",
                (db.current_tenant(), route_id))
            for l in lines:
                r = weeks.materialise_line(route_id, l["discipline"], l["section_id"])
                for k in done:
                    done[k] += r[k]
            materialised = done
        except Exception as e:
            # a failure here must not undo an approval that has already committed
            print("⚠️  Week 1: week materialisation failed for", route_id, e)

    return {"status": "success", "route_id": route_id, "new_status": req.status,
            "scoped_to_line": scoped, "rows": existing[0]["n"],
            "weeks": materialised}


# ------------------------------------------------ look-ahead (Week 1, Tasks C + D)
#
# NOT under /api/admin. Until Task F lands, visibility is exactly the same as
# /api/forecasts — everyone on staff sees every line — and these endpoints inherit
# that rather than inventing a narrower rule that Task F would then have to undo.
# ⚠️ That means anyone who can reach the staff app can confirm a week and type an
# actual. Task F is what scopes it; do not describe this as access-controlled.
class WeekKey(BaseModel):
    """The forecast LINE plus a week. Same shape every write below takes."""
    route_id: str
    month_index: int
    discipline: str = ""
    section_id: str = ""
    week_index: int


class WeekEdit(WeekKey):
    planned_qty: Optional[float] = None
    # the four flags are free text and every one may be empty
    weather: Optional[str] = None
    wetness: Optional[str] = None
    traffic: Optional[str] = None
    other: Optional[str] = None
    edited_by: Optional[str] = None


class WeekConfirm(WeekEdit):
    confirmed_by: Optional[str] = None


class WeekActual(WeekKey):
    actual_qty: Optional[float] = None
    actual_note: Optional[str] = None
    actual_by: Optional[str] = None


class WeekCalibrate(WeekKey):
    # a typed figure INSTEAD of the variance formula, not on top of it
    override_qty: Optional[float] = None
    by: Optional[str] = None


def _flags_of(body):
    """Only the flag fields the client actually sent — an absent one is not a blank."""
    sent = set(_fields_set(body))
    return {k: getattr(body, k) for k in weeks.FLAG_FIELDS if k in sent}


@app.get("/api/forecast-weeks")
def list_forecast_weeks(from_month: int = Query(1, ge=1, le=MONTH_COUNT),
                        to_month: int = Query(MONTH_COUNT, ge=1, le=MONTH_COUNT),
                        route_id: Optional[str] = None):
    """
    The week rows in a month window, plus what the UI needs to label them.

    Reading materialises: a line approved before this shipped has no week rows, and an
    empty Look-ahead for an approved line reads as a broken feature rather than a
    missing migration. See weeks.list_weeks().
    """
    rows = weeks.list_weeks(from_month, to_month, route_id=route_id)
    nm, nw = weeks.editable_week(START_YEAR)
    return {"from": min(from_month, to_month), "to": max(from_month, to_month),
            "weeks": rows,
            # which cell the UI lets you edit and confirm. Computed server-side so the
            # browser's clock and time zone cannot move it.
            "next_week": {"month_index": nm, "week_index": nw},
            "statuses": list(weeks.WEEK_STATUSES),
            "flag_fields": list(weeks.FLAG_FIELDS),
            "summary": weeks.summary()}


@app.put("/api/forecast-weeks")
def edit_forecast_week(body: WeekEdit):
    """Edit one week's planned quantity and/or flags. Sets status `edited`."""
    res = weeks.set_week(body.route_id, body.month_index, body.discipline,
                         body.section_id, body.week_index,
                         planned_qty=body.planned_qty, flags=_flags_of(body),
                         by=body.edited_by)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


@app.post("/api/forecast-weeks/confirm")
def confirm_forecast_week(body: WeekConfirm):
    """Confirm one week, with the four optional flags. Sets status `confirmed`."""
    res = weeks.confirm_week(body.route_id, body.month_index, body.discipline,
                             body.section_id, body.week_index,
                             by=body.confirmed_by, flags=_flags_of(body))
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


@app.put("/api/forecast-weeks/actual")
def set_forecast_week_actual(body: WeekActual):
    """
    Type what actually moved in one week.

    ⭐ Does NOT calibrate. Variance appears immediately; next week's plan does not
    move until /api/forecast-weeks/calibrate is called.
    """
    res = weeks.set_actual(body.route_id, body.month_index, body.discipline,
                           body.section_id, body.week_index,
                           actual_qty=body.actual_qty, actual_note=body.actual_note,
                           by=body.actual_by)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


@app.post("/api/forecast-weeks/calibrate")
def calibrate_forecast_week(body: WeekCalibrate):
    """
    Carry this week's variance into next week — and only next week.

    400 when next week is already confirmed. The response's `blocked_by` says so, so
    the UI can disable the button rather than discovering it on click.
    """
    res = weeks.calibrate(body.route_id, body.month_index, body.discipline,
                          body.section_id, body.week_index,
                          override_qty=body.override_qty, by=body.by)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


# --------------------------------------------- stockpiles (Week 1, Task D2)
class CapacityIn(BaseModel):
    # None CLEARS the capacity — "we do not know how big this pile is" is a real state
    # and is not zero. See stockpiles.set_capacity().
    capacity_qty: Optional[float] = None
    capacity_unit: Optional[str] = None
    opening_qty: Optional[float] = None


class ConsumeIn(BaseModel):
    location_id: str
    month_index: int
    week_index: int
    consumed_qty: Optional[float] = None
    unit: Optional[str] = None
    note: Optional[str] = None
    updated_by: Optional[str] = None


@app.put("/api/locations/{location_id}/capacity")
def set_location_capacity(location_id: str, body: CapacityIn,
                          token: Optional[str] = None):
    """Max live stock, its unit, and the opening figure. Admin-gated like every other
    write to master data."""
    _check_admin(token)
    res = stockpiles.set_capacity(location_id, capacity_qty=body.capacity_qty,
                                  capacity_unit=body.capacity_unit,
                                  opening_qty=body.opening_qty)
    if res.get("error"):
        raise HTTPException(404, res["error"])
    return res


@app.get("/api/stockpiles")
def list_stockpiles(from_month: int = Query(1, ge=1, le=MONTH_COUNT),
                    to_month: int = Query(MONTH_COUNT, ge=1, le=MONTH_COUNT),
                    location_id: Optional[str] = None):
    """
    Per-week stock for every location that can hold it.

    A read model — nothing here is stored. Inbound comes from typed week ACTUALS, so a
    week nobody has reported shows inbound 0 rather than a quarter of the forecast.
    """
    out = stockpiles.balances(from_month, to_month, location_id=location_id)
    out["storage_types"] = list(stockpiles.STORAGE_TYPES)
    out["capacity_units"] = list(stockpiles.CAPACITY_UNITS)
    return out


@app.put("/api/stockpiles/consume")
def consume_stockpile(body: ConsumeIn):
    """One week's typed consumption. There is no file and no importer."""
    res = stockpiles.consume(body.location_id, body.month_index, body.week_index,
                             consumed_qty=body.consumed_qty, unit=body.unit,
                             note=body.note, by=body.updated_by)
    if res.get("error"):
        raise HTTPException(404, res["error"])
    return res


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
        "SELECT * FROM forecasts WHERE tenant_id = ? AND status = 'Approved' "
        "AND month_index BETWEEN ? AND ?",
        (db.current_tenant(), lo, hi),
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
        "SELECT * FROM forecasts WHERE tenant_id = ? AND status = 'Approved' "
        "AND month_index BETWEEN ? AND ? ORDER BY route_id, month_index",
        (db.current_tenant(), lo, hi),
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


@app.get("/api/public/route-alternatives")
def public_route_alternatives(profile: Optional[str] = None):
    """Every non-primary baked option, for the public map's grey underlay.

    A SEPARATE endpoint from /api/public/map-data on purpose — see the docstring
    on network.route_alternatives_geojson() for the five things that walk that
    FeatureCollection and would be corrupted by alternatives appearing in it."""
    return network.route_alternatives_geojson(profile)


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


@app.get("/api/public/map-data")
def public_map_data(profile: Optional[str] = None):
    """
    Everything the public route map draws: route lines both directions, plus location
    markers. Replaces the 4 MB static map/data/a1_data.js.

    Unbaked routes are omitted rather than drawn as straight lines. On the admin map a
    dashed straight line usefully says 'HERE could not route this'; on a public map it
    would just look like a road that isn't there.
    """
    return network.public_map_data(profile=profile)


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
        ids = [r["id"] for r in db.query(
            "SELECT id FROM routes WHERE tenant_id = ? ORDER BY id",
            (db.current_tenant(),))]

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
    # operator / free-text detail. Salvaged out of a1_data.js, where the quarry operator
    # was the only copy anywhere, and editable in the Locations panel since.
    vendor: Optional[str] = None
    detail: Optional[str] = None


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
                                   gate_lat=body.gate_lat, gate_lon=body.gate_lon,
                                   vendor=body.vendor, detail=body.detail)


@app.put("/api/admin/locations/{location_id}")
def update_location(location_id: str, body: LocationIn, token: Optional[str] = None):
    _check_admin(token)
    # only touch the gate if the client mentioned it — otherwise a caller that doesn't
    # know about gates (or an older client) would silently wipe one that was set
    gate_given = bool({"gate_lat", "gate_lon"} & set(_fields_set(body)))
    meta_given = bool({"vendor", "detail"} & set(_fields_set(body)))
    return network.update_location(location_id, body.name, body.role, body.materials,
                                   body.lat, body.lon, body.loc_type,
                                   supplies=body.supplies, receives=body.receives,
                                   gate_lat=body.gate_lat, gate_lon=body.gate_lon,
                                   gate_given=gate_given,
                                   vendor=body.vendor, detail=body.detail,
                                   meta_given=meta_given)


@app.delete("/api/admin/locations/{location_id}")
def delete_location(location_id: str, token: Optional[str] = None):
    _check_admin(token)
    return network.delete_location(location_id)


# ------------------------------------------------------------------ Phase 5a: gates
#
# The gate endpoints are admin-only for the same reason Locations and Zones are: a gate
# moves where HERE routes to, so writing one invalidates cached geometry and spends
# calls on the re-bake. ⚠️ ADMIN_TOKEN is set on Render (verified 2026-08-28), but the
# LOGINS dict in the frontend is still client-side with plaintext passwords, so this is
# an API boundary and not yet a user-permission one. Phase 6.
class GateIn(BaseModel):
    location_id: str
    name: str
    lat: float
    lon: float
    direction: str = "both"                 # 'access' | 'egress' | 'both'
    # B3: induction is per gate and applies to whatever arrives. No per-vehicle
    # override — offered and declined; the fix if it ever matters is a nullable
    # column plus a COALESCE, not a rebuild.
    safety_minutes: Optional[float] = None
    # B5(c): the FLAT fallback, used only where no drawn haul road already carries
    # these minutes in route_geometry.duration_hr. See network.INTERNAL_TRAVEL_SOURCES.
    internal_travel_minutes: Optional[float] = None
    is_default: bool = False
    active: bool = True
    note: Optional[str] = None
    gate_id: Optional[str] = None


class GatePatch(BaseModel):
    name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    direction: Optional[str] = None
    safety_minutes: Optional[float] = None
    internal_travel_minutes: Optional[float] = None
    is_default: Optional[bool] = None
    active: Optional[bool] = None
    note: Optional[str] = None


class RouteGatesIn(BaseModel):
    origin_gate_id: Optional[str] = None
    dest_gate_id: Optional[str] = None


@app.get("/api/gates")
def list_gates(location_id: Optional[str] = None, include_inactive: bool = True):
    """Readable without a token — the map and the route panels need it, and a gate
    coordinate is no more sensitive than the location marker already published."""
    return {"gates": gates.list_gates(location_id, include_inactive=include_inactive)}


@app.post("/api/admin/gates")
def create_gate(body: GateIn, token: Optional[str] = None):
    _check_admin(token)
    return gates.create_gate(body.location_id, body.name, body.lat, body.lon,
                             direction=body.direction,
                             safety_minutes=body.safety_minutes,
                             internal_travel_minutes=body.internal_travel_minutes,
                             is_default=body.is_default, active=body.active,
                             note=body.note, gate_id=body.gate_id)


@app.patch("/api/admin/gates/{gate_id}")
def update_gate(gate_id: str, body: GatePatch, token: Optional[str] = None):
    _check_admin(token)
    # only the fields the client actually sent are written. A PATCH that toggles
    # `active` must not blank a safety time it knows nothing about — the same trap the
    # Locations PUT hit when a node drag started wiping salvaged vendor names.
    sent = set(_fields_set(body))
    fields = {k: getattr(body, k) for k in sent}
    out = gates.update_gate(gate_id, **fields)
    if not out.get("error"):
        # moving a gate moves the point HERE routed to, so any cached geometry on a
        # route that uses it is stale. The re-bake is NOT run here — its cost is quoted
        # in the UI first, as zone and haul-road writes already do.
        moved = bool({"lat", "lon", "direction", "active"} & sent)
        out["rebake_needed"] = moved
        out["routes_affected"] = gates.routes_using(gate_id) if moved else []
    return out


@app.delete("/api/admin/gates/{gate_id}")
def delete_gate(gate_id: str, token: Optional[str] = None):
    _check_admin(token)
    return gates.delete_gate(gate_id)


@app.put("/api/admin/routes/{route_id}/gates")
def set_route_gates(route_id: str, body: RouteGatesIn, token: Optional[str] = None):
    _check_admin(token)
    sent = set(_fields_set(body))
    return gates.set_route_gates(route_id,
                                 origin_gate_id=body.origin_gate_id,
                                 dest_gate_id=body.dest_gate_id,
                                 origin_given="origin_gate_id" in sent,
                                 dest_given="dest_gate_id" in sent)


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


@app.get("/api/admin/diagnostics/zones")
def diagnostics_zones(route_id: Optional[str] = None,
                      profile: str = network.DEFAULT_PROFILE,
                      probe: bool = False, token: Optional[str] = None):
    """
    What the zone layer sends HERE, and with probe=true what HERE does with it.

    Read this before believing anything about how many avoid[areas] the API tolerates —
    that limit is not enforced in this codebase and has never been checked against the
    live service. probe=true spends two HERE requests: the same route with and without
    the current zone set, so a difference is attributable.
    """
    _check_admin(token)
    return network.zones_diagnostics(route_id=route_id, profile=profile, probe=probe)


# ------------------------------------------------------------------ zones (Phase 3)
class ZoneIn(BaseModel):
    """
    A drawn zone. Geometry is a GeoJSON *geometry* (Polygon or LineString), not a
    Feature — Mapbox Draw hands back Features, so the client sends feature.geometry.

    affects_routing is the whole point of merging Phases 3 and 3.5 into one table:
    TRUE and the bbox goes to HERE and baked routes crossing it are re-routed; FALSE and
    it is drawn on the maps and the router never hears about it.
    """
    name: str
    geometry: dict
    kind: Optional[str] = "other"
    affects_routing: bool = True
    starts_on: Optional[str] = None          # 'YYYY-MM-DD' or null for open-ended
    ends_on: Optional[str] = None
    note: Optional[str] = None
    active: bool = True
    # Phase 4, haul roads only. speed_kph null means "no assigned speed" and leaves
    # HERE's own timing alone — it is NOT a default speed. haul_mode null means the
    # module default, which is 'splice'; see haul.py for why that is the safe one.
    speed_kph: Optional[float] = None
    haul_mode: Optional[str] = None


def _zone_write_result(zone, inval):
    """Shape shared by create/update/delete: the zone, plus what it just invalidated."""
    return {"zone": zone, "invalidated": inval,
            "rebake_required": inval.get("leg_count", 0) > 0,
            "here_calls": inval.get("here_calls", 0)}


@app.get("/api/zones")
def list_zones(on: Optional[str] = None, include_inactive: bool = True):
    """
    Every zone, for the admin table and both maps.

    Deliberately NOT under /api/admin: the public map draws disruptions and has no
    token. Nothing here is sensitive — a zone is a shape, a name and a date range.
    Pass on='YYYY-MM-DD' to get only those in force that day, which is how the public
    map's timeline filters them.
    """
    return {"zones": zones.list_zones(include_inactive=include_inactive, on=on),
            "summary": zones.summary(),
            "kinds": list(zones.KINDS),
            # Phase 4: the zone table needs to show which routes use each haul road, and
            # a haul road attached to nothing is the commonest reason one "does nothing"
            "haul_modes": list(haul.MODES),
            "haul_default_mode": haul.DEFAULT_MODE,
            "haul_kind": zones.HAUL_KIND,
            "haul_links": haul.link_counts()}


@app.get("/api/zones/{zone_id}")
def get_zone(zone_id: str):
    z = zones.get_zone(zone_id)
    if not z:
        raise HTTPException(404, "zone not found")
    return z


@app.post("/api/admin/zones")
def create_zone(body: ZoneIn, token: Optional[str] = None):
    """
    Create a zone and immediately clear the baked geometry it invalidates.

    Clearing is not the same as re-baking. This endpoint does NOT call HERE: it reports
    how many legs must be re-routed and the client drives /api/admin/bake-routes until
    'remaining' is 0, the same loop the bulk-bake button already uses. Doing the HERE
    calls inline would block one HTTP request on dozens of round trips and time out on
    Render long before it finished.
    """
    _check_admin(token)
    z = zones.create_zone(
        body.name, body.geometry, kind=body.kind, affects_routing=body.affects_routing,
        starts_on=body.starts_on, ends_on=body.ends_on, note=body.note, active=body.active,
        speed_kph=body.speed_kph, haul_mode=body.haul_mode)
    if z.get("error"):
        raise HTTPException(400, z["error"])
    if z["kind"] == zones.HAUL_KIND:
        # A brand-new haul road is attached to nothing, so it invalidates nothing — it
        # cannot change a route until someone puts it on one. That is the whole reason
        # attachment is explicit: drawing a road near a route does not silently re-bake
        # it. Routing zones invalidate on creation; haul roads invalidate on attach.
        return _zone_write_result(z, haul.invalidate_for_zone(z["id"]))
    # a zone that does not affect routing invalidates nothing — it is drawn, not routed
    inval = (zones.invalidate(new_geometry=z["geometry"])
             if (z["affects_routing"] and z["active"] and zones.applies_on(z))
             else zones.invalidate(dry_run=True))
    return _zone_write_result(z, inval)


@app.put("/api/admin/zones/{zone_id}")
def update_zone(zone_id: str, body: ZoneIn, token: Optional[str] = None):
    """
    Edit a zone and clear what the edit makes wrong.

    An edit is two invalidations at once — routes that now cross the new shape, and
    routes that were baked while the OLD shape was avoided. Both are passed to
    zones.invalidate(), which is why the previous geometry is read before the write.

    Only fields the client actually sent are written; see zones.update_zone().
    """
    _check_admin(token)
    before = zones.get_zone(zone_id)
    if not before:
        raise HTTPException(404, "zone not found")

    sent = set(_fields_set(body))
    fields = {k: getattr(body, k) for k in
              ("name", "geometry", "kind", "affects_routing", "starts_on", "ends_on",
               "note", "active", "speed_kph", "haul_mode") if k in sent}
    z = zones.update_zone(zone_id, **fields)
    if z.get("error"):
        raise HTTPException(400, z["error"])

    # A haul road edit is exact — see haul.invalidate_for_zone(). It covers the case
    # where a zone was a haul road and has just stopped being one, because the legs baked
    # through it still carry its id in haul_zones and are cleared on that record, not on
    # what the zone happens to be now.
    if z["kind"] == zones.HAUL_KIND or before["kind"] == zones.HAUL_KIND:
        inval = haul.invalidate_for_zone(zone_id)
        haul.refresh_origin_temp_km()
        return _zone_write_result(z, inval)

    now_routes = z["affects_routing"] and z["active"] and zones.applies_on(z)
    was_routes = (before["affects_routing"] and before["active"]
                  and zones.applies_on(before))
    inval = zones.invalidate(
        zone_id=zone_id,
        new_geometry=(z["geometry"] if now_routes else None),
        # the old shape only matters if it was actually steering the router before
        old_geometry=(before["geometry"] if was_routes else None),
    )
    return _zone_write_result(z, inval)


@app.delete("/api/admin/zones/{zone_id}")
def delete_zone(zone_id: str, token: Optional[str] = None):
    """
    Delete a zone and clear geometry that was routed around it.

    Consider deactivating instead (PUT with active=false). A deleted zone takes with it
    the record of why a past bake went the way it did; a deactivated one stops steering
    the router and stays explainable.
    """
    _check_admin(token)
    before = zones.get_zone(zone_id)
    if not before:
        raise HTTPException(404, "zone not found")

    if before["kind"] == zones.HAUL_KIND:
        # Order matters: work out what to clear while the links still exist, then drop
        # them, then delete the zone. Deleting first would leave invalidate_for_zone()
        # with nothing to find and the affected legs baked through a road that is gone.
        inval = haul.invalidate_for_zone(zone_id)
        detached = haul.clear_links_for_zone(zone_id)
        res = zones.delete_zone(zone_id)
        if res.get("error"):
            raise HTTPException(404, res["error"])
        haul.refresh_origin_temp_km()
        out = _zone_write_result({"deleted": zone_id, "was": before}, inval)
        out["detached_from_routes"] = detached
        return out

    was_routes = (before["affects_routing"] and before["active"]
                  and zones.applies_on(before))
    res = zones.delete_zone(zone_id)
    if res.get("error"):
        raise HTTPException(404, res["error"])
    inval = zones.invalidate(
        zone_id=zone_id,
        old_geometry=(before["geometry"] if was_routes else None),
    )
    return _zone_write_result({"deleted": zone_id, "was": before}, inval)


@app.get("/api/admin/zones/{zone_id}/impact")
def zone_impact(zone_id: str, token: Optional[str] = None):
    """
    Dry run: what deleting or moving this zone would invalidate, without touching
    anything. Use it to see the HERE bill before agreeing to it.
    """
    _check_admin(token)
    z = zones.get_zone(zone_id)
    if not z:
        raise HTTPException(404, "zone not found")
    if z["kind"] == zones.HAUL_KIND:
        return {"zone": z,
                "routes_attached": haul.routes_for_zone(zone_id),
                "would_invalidate": haul.invalidate_for_zone(zone_id, dry_run=True)}
    return {"zone": z,
            "would_invalidate": zones.invalidate(
                zone_id=zone_id, new_geometry=z["geometry"],
                old_geometry=z["geometry"], dry_run=True)}


# ------------------------------------------------------- haul roads (Phase 4)
class HaulLinkIn(BaseModel):
    """Attach a haul road to a route. seq null appends to the end of the order."""
    zone_id: str
    seq: Optional[int] = None


class HaulOrderIn(BaseModel):
    """The full traversal order for one route's haul roads, loaded-leg direction."""
    zone_ids: List[str]


def _haul_write_result(route_id, inval, extra=None):
    """
    Same shape the zone writes return, so the frontend's existing re-bake loop drives
    this too. The one difference worth reading is here_calls: a spliced leg is more than
    one HERE request, and this counts the splices — see haul._here_calls_for().
    """
    out = {"route_id": route_id, "invalidated": inval,
           "rebake_required": inval.get("leg_count", 0) > 0,
           "here_calls": inval.get("here_calls", 0),
           "haul_roads": haul.links_for_route(route_id)}
    out.update(extra or {})
    return out


@app.get("/api/haul-roads")
def list_haul_roads(on: Optional[str] = None):
    """
    Haul roads and their route links, for the admin table and the public map.

    Not under /api/admin for the same reason /api/zones is not: the public map draws
    these, has no token, and a drawn road with a speed on it is not sensitive.
    """
    return {"haul_roads": zones.haul_roads(on=on, include_inactive=True,
                                           require_geometry=False),
            "links": db.query("SELECT * FROM route_haul_roads WHERE tenant_id = ? "
                              "ORDER BY route_id, seq", (db.current_tenant(),)),
            "summary": haul.summary()}


@app.get("/api/routes/{route_id}/haul-roads")
def route_haul_roads(route_id: str):
    """
    The haul roads on one route, plus the plan for each leg — entry and exit points,
    traversal direction, drawn length and the duration the assigned speed implies.

    No HERE calls. This is what the route panel shows before anything is baked, so a
    planner can see that the loaded leg enters a road at one end and the return leg at
    the other without spending a request to find out.
    """
    if not db.query("SELECT id FROM routes WHERE tenant_id = ? AND id = ?",
                    (db.current_tenant(), route_id)):
        raise HTTPException(404, "route not found")
    return haul.diagnostics(route_id=route_id, probe=False)


@app.post("/api/admin/routes/{route_id}/haul-roads")
def attach_haul_road(route_id: str, body: HaulLinkIn, token: Optional[str] = None):
    """
    Put a haul road on a route — the Phase 4 requirement that this is edited ON the
    route, not as a free-floating drawing.

    Clears the route's baked geometry, because every leg of it now routes differently.
    Does NOT call HERE: the client drives /api/admin/bake-routes afterwards, the same
    loop the zone writes use.
    """
    _check_admin(token)
    res = haul.attach(route_id, body.zone_id, seq=body.seq)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    inval = haul.invalidate_for_route(route_id)
    haul.refresh_origin_temp_km(route_id)
    return _haul_write_result(route_id, inval, {"attached": res["attached"]})


@app.delete("/api/admin/routes/{route_id}/haul-roads/{zone_id}")
def detach_haul_road(route_id: str, zone_id: str, token: Optional[str] = None):
    """Take a haul road off a route and clear the geometry baked through it."""
    _check_admin(token)
    res = haul.detach(route_id, zone_id)
    if res.get("error"):
        raise HTTPException(404, res["error"])
    inval = haul.invalidate_for_route(route_id)
    haul.refresh_origin_temp_km(route_id)
    return _haul_write_result(route_id, inval, {"detached": res["detached"]})


@app.put("/api/admin/routes/{route_id}/haul-roads/order")
def order_haul_roads(route_id: str, body: HaulOrderIn, token: Optional[str] = None):
    """
    Set the order a route enters its haul roads in, going out. The return leg reverses
    it. Order changes the spliced geometry, so this clears and re-bakes like any other
    haul change.
    """
    _check_admin(token)
    res = haul.reorder(route_id, body.zone_ids)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    inval = haul.invalidate_for_route(route_id)
    haul.refresh_origin_temp_km(route_id)
    return _haul_write_result(route_id, inval, {"order": res["order"]})


@app.post("/api/admin/haul-roads/refresh-temp-km")
def refresh_temp_km(route_id: Optional[str] = None, token: Optional[str] = None):
    """
    Recompute routes.origin_temp_km from the drawn haul roads.

    Phase 4 made that column derived — it was seeded from V2 and computed with nowhere.
    Every attach, detach, reorder and haul-road edit refreshes it already; this is the
    manual backstop for a database whose links predate the change.
    """
    _check_admin(token)
    return haul.refresh_origin_temp_km(route_id)


@app.get("/api/admin/diagnostics/haul-roads")
def diagnostics_haul_roads(route_id: Optional[str] = None,
                           zone_id: Optional[str] = None,
                           profile: Optional[str] = None,
                           probe: bool = False, token: Optional[str] = None):
    """
    What Phase 4 would send HERE, and with probe=true what HERE does with it.

    ⚠️ Read this before believing either of the two claims Phase 4 was designed around.
    Both are inherited from a code comment and neither has been checked against the live
    service from this codebase:

      * that HERE ignores 'alternatives' when via-waypoints are present
      * that a stopover via splits the response into per-leg sections

    The second is load-bearing: 'via' mode's assigned-speed substitution only works if
    HERE isolates the haul stretch as its own section. If it does not, via mode silently
    falls back to HERE's own timing and says so in the response.

    probe=true needs route_id and spends up to three HERE calls on one route.
    """
    _check_admin(token)
    return haul.diagnostics(zone_id=zone_id, route_id=route_id,
                            profile=profile, probe=probe)


# ------------------------------------- Tark Tee restrictions (Phase 2.5a)
@app.get("/api/restrictions")
def get_restrictions(layers: Optional[str] = None, include_expired: bool = False):
    """
    Estonian Transport Administration restriction layers as WGS84 GeoJSON.

    Proxied rather than fetched from the browser for three reasons, in order: it is
    somebody else's public service and this caches it to one request per layer per half
    hour however many people are looking; **the coordinates have to be corrected before
    anything can draw them** (the service declares WKID 4326 and returns L-EST97 metres);
    and CORS then never matters.

    Not under /api/admin — the public map draws these and has no token, and a bridge
    weight limit is not sensitive.

    ⚠️ Expired records are excluded by default. The live layers carry roadworks from 2017,
    and drawing those as current would be worse than not having the layer. Pass
    include_expired=true to see everything; the response always reports how many were
    dropped.
    """
    keys = [k.strip() for k in (layers or "").split(",") if k.strip()] or None
    return restrictions.fetch_all(keys, current_only=not include_expired)


@app.get("/api/restrictions/layers")
def restriction_layers():
    """
    The layer catalogue — label, severity, colour and which vehicle dimension (if any) a
    limit on that layer can be compared against.

    The COLOUR is served from here so the map's legend and its layers cannot drift apart.
    That has already had to be fixed once on this map.
    """
    return {"layers": [dict(v, key=k) for k, v in restrictions.LAYERS.items()],
            "match_m": restrictions.MATCH_M,
            "attribution": "Estonian Transport Administration — Tark Tee (tarktee.ee)"}


@app.get("/api/routes/{route_id}/restrictions")
def route_restrictions(route_id: str, profile: Optional[str] = None):
    """
    Which restrictions the baked geometry of one route passes through.

    ⚠️ Reports, never blocks, and makes **no tonnage judgement** about a weak bridge:
    `nominal_load` is a class like 'N-13/NG-60', not tonnes, and the mapping has never
    been established here. The class and the vehicle's gross weight are both returned and
    `needs_interpretation` is set. See restrictions.check_route().
    """
    if not db.query("SELECT id FROM routes WHERE tenant_id = ? AND id = ?",
                    (db.current_tenant(), route_id)):
        raise HTTPException(404, "route not found")
    return restrictions.check_route(route_id, profile=profile)


@app.get("/api/routes/restrictions")
def all_route_restrictions(profile: Optional[str] = None):
    """
    Every baked route checked in one pass, sharing a single fetch. Only routes with a hit
    are returned — a clean route is the normal case and listing all 107 buries the ones
    that matter.
    """
    return restrictions.check_all(profile=profile)


@app.get("/api/admin/diagnostics/restrictions")
def diagnostics_restrictions(layer: Optional[str] = None, probe: bool = False,
                             token: Optional[str] = None):
    """
    What this asks Tark Tee for, and with probe=true what comes back — including the raw
    coordinate next to the converted one, so the WKID mislabelling is visible rather than
    taken on trust. Spends no HERE calls and no Google quota.
    """
    _check_admin(token)
    return restrictions.diagnostics(layer=layer, probe=probe)


@app.post("/api/admin/restrictions/refresh")
def refresh_restrictions(token: Optional[str] = None):
    """Drop the cache so the next request re-fetches from Tark Tee."""
    _check_admin(token)
    return restrictions.clear_cache()


# ------------------------------------------ Street View (Phase 2.5a)
@app.get("/api/streetview/meta")
def streetview_meta(lat: float, lon: float, radius: int = streetview.DEFAULT_RADIUS_M):
    """
    Does Google have street-level imagery near this point? **Free** — Google consumes no
    quota for metadata, only for images. The UI calls this first and asks for a picture
    only when the answer is yes, so a quarry down a private track costs nothing and shows
    nothing instead of spending a billable request on a grey tile.

    Also returns `offset_m`: how far Google had to move to find a panorama. A large offset
    means the picture is of somewhere else, which matters for a gate on a long approach.
    """
    return streetview.metadata(lat, lon, radius)


@app.get("/api/streetview")
def streetview_image(lat: float, lon: float, size: str = "480x300",
                     heading: Optional[int] = None, pitch: int = 0, fov: int = 80,
                     radius: int = streetview.DEFAULT_RADIUS_M):
    """
    A Street View still, proxied so the Google key never reaches the browser.

    ⚠️ **This costs a billable Google request.** It checks the free metadata endpoint
    first and returns 404 rather than spending one when there is no imagery. Nothing is
    written to disk or to the database — Google's terms restrict storing Maps content.
    """
    from fastapi.responses import Response
    data, ctype = streetview.fetch_image(lat, lon, size=size, heading=heading,
                                         pitch=pitch, fov=fov, radius=radius)
    if data is None:
        raise HTTPException(404, ctype)
    # short cache only: enough to stop a re-render costing a second request, not storage
    return Response(content=data, media_type=ctype,
                    headers={"Cache-Control": "private, max-age=900"})


@app.get("/api/admin/diagnostics/streetview")
def diagnostics_streetview(lat: Optional[float] = None, lon: Optional[float] = None,
                           probe: bool = False, token: Optional[str] = None):
    """
    Whether Street View is wired up and how many billable requests this process has made.
    probe=true calls metadata only, which is free.
    """
    _check_admin(token)
    return streetview.diagnostics(lat=lat, lon=lon, probe=probe)


# ------------------------------------------------------------------ static
class NoCacheStatic(StaticFiles):
    """StaticFiles that revalidates instead of trusting the browser's guess.

    Same fault as GET / had, and the same fix. Starlette sends an ETag and no
    Cache-Control, so Chrome applies its own freshness heuristic and can serve a
    stale map/index.html or ipt_segments.js straight through a hard refresh —
    which on 2026-08-30 looked exactly like a failed deploy for an hour, and on
    the map specifically produced a HALF-upgraded pair: new index.html, old JS.

    `no-cache`, NOT `no-store`. The browser still revalidates with its ETag and
    still gets a 304 when nothing changed, so map/data/alignment.js — 8.8 MB — is
    re-downloaded only when it has actually changed. no-store would re-fetch it
    on every page load.

    ⚠️ This does NOT replace the ?v= cache-buster on ipt_segments.js. That guards
    the same failure one layer up and costs nothing; belt and braces is the right
    number of mechanisms for a bug that has now bitten twice.
    """

    # get_response() is the documented override point and has been stable across
    # Starlette versions. `file_response()` is the more obvious hook and is the
    # WRONG one to use here: it is internal, its signature has changed, and a
    # subclass that overrides a method the installed version no longer calls adds
    # no header and raises no error — it fails exactly as silently as the bug it
    # is meant to fix.
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        try:
            resp.headers["Cache-Control"] = "no-cache"
        except Exception:
            pass          # never let a header failure 500 the map
        return resp


# Map (Mapbox app) at /map/ ; must be mounted before the catch-all "/".
app.mount("/map", NoCacheStatic(directory=str(ROOT / "map"), html=True), name="map")


@app.get("/")
def frontend_index():
    # no-cache, NOT no-store: the browser still revalidates with its ETag and still gets
    # a 304 when nothing has changed, so a normal load costs the same as before. Without
    # it Starlette sends an ETag with no Cache-Control, Chrome applies its own freshness
    # heuristic, and a deployed index.html can stay invisible behind a cached copy that
    # survives Ctrl+Shift+R. That cost an hour on 2026-08-30 and looked exactly like a
    # failed deploy: the new API answered, the new page did not appear, and the repo,
    # the build and the commit were all correct.
    return FileResponse(str(ROOT / "frontend" / "index.html"),
                        headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

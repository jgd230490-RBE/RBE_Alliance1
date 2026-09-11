"""
Cost lines, 2026-09-11 — ONE read for the Dashboard and the Forecasts page: every visible
forecast line × month with its volume, haul and cost figures, from the same functions the
Look-ahead uses. Nothing is derived in the browser any more.

WHY
---
Until 11 Sep the Dashboard worked its own figures out client-side: trips = tonnes ÷
payload (fractional), km from /api/routes/analysis-batch, CO2 from the emissions factor,
a fleet size from a monthly peak. The Look-ahead computes the same things on the server
(derived.py: trips rounded UP, the route's own cycle) — two sources for one number, the
shape of the 04 Sep fleet-size bug. Adding cost on top of the client arithmetic would
have made a third. So: this module reads the forecast rows the caller may see, runs each
through derived.line_context() / day_figures() exactly as a Look-ahead line, and returns
the result. The Dashboard sums; it no longer derives.

TWO VISIBLE CONSEQUENCES, chosen by the human on 11 Sep
-------------------------------------------------------
1. Trips round up per line-month (ceil(tonnes / payload)), so totals move up slightly
   against the old fractional figure. That is the Look-ahead's figure, and the right one.
2. An UNBAKED route (no geometry for the line's vehicle) has NO distance here — km, t·km,
   CO2, fair € are None and are EXCLUDED from totals, which say so (`baked_lines` of
   `lines`). The old Dashboard fell back to the route's headline distance × 2; the human's
   09 Sep rule ("the route has to be baked — if not, show a notification") applies here
   as everywhere else now.

THE THREE PRICES, EACH IN FOUR UNITS
------------------------------------
Every line-month carries `planned` (the route's typed rates, else the Config target —
`source` says which; None when neither) and `fair` (the model, fairprice.py), each as
{eur, per_t, per_trip, per_km} — because every haulier and every client presents a price
differently, and a comparison needs the same unit on both sides. per_km is on the km the
trips actually run (basis km × trips), never on a loaded-leg-only figure.

CO2 is km × the vehicle's kg CO2e/km from factors.json, as the Dashboard always did —
the same factor, now applied to the server's km.
"""
import access
import conversions
import db
import derived


def _num(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


unit_prices = derived.unit_prices        # one definition, in derived.py


def _rows(acc, from_month=None, to_month=None, status=None):
    # the tenant predicate is in the literal on every branch (test_tenant_audit.py reads
    # the text); the optional clauses are appended after it, ? first in the params
    clauses, params = [], []
    if from_month is not None:
        clauses.append("month_index >= ?"); params.append(int(from_month))
    if to_month is not None:
        clauses.append("month_index <= ?"); params.append(int(to_month))
    if status and status != "All":
        clauses.append("status = ?"); params.append(status)
    where = "".join(" AND " + c for c in clauses)
    rows = db.query(f"SELECT * FROM forecasts WHERE tenant_id = ?{where} "
                    f"ORDER BY route_id, discipline, section_id, month_index",
                    tuple([db.current_tenant()] + params))
    return access.filter_lines(rows, acc)


def lines(acc=None, from_month=None, to_month=None, status=None, factors=None):
    """
    {lines: [...], totals: {...}, costing: {...}} for the caller's visible forecast rows.
    One derived.costing_context() (two statements), one route map, one cycle per
    (route, vehicle) pair — the same budget shape as the Look-ahead's read.
    """
    acc = acc if acc is not None else access.current()
    factors = factors or conversions.load_factors()
    rows = _rows(acc, from_month, to_month, status)
    routes = derived._routes_and_names()
    cost = derived.costing_context()
    cache = {}
    plan = factors.get("planning", {}) or {}
    work_days = float(plan.get("working_days_per_month") or 22)
    out = []
    tot = {"lines": 0, "baked_lines": 0, "tonnes": 0.0, "trips": 0, "km": 0.0, "tonne_km": 0.0,
           "co2_t": 0.0, "planned_eur": 0.0, "planned_lines": 0, "planned_target_lines": 0,
           "fair_eur": 0.0, "fair_lines": 0, "planned_tonnes": 0.0, "planned_km": 0.0,
           "fair_tonnes": 0.0, "fair_km": 0.0, "fair_trips": 0, "planned_trips": 0}
    for r in rows:
        line = {"route_id": r["route_id"], "unit": r.get("unit"), "material_type": r.get("material_type"),
                "vehicle_type": r.get("vehicle_type"), "ipt": r.get("ipt"), "discipline": r.get("discipline"),
                "section_id": r.get("section_id"), "month_index": r.get("month_index"),
                "week": {"planned_qty": r.get("quantity")}}
        ctx = derived.line_context(line, factors, routes=routes, cache=cache, cost=cost)
        f = derived.day_figures(r.get("quantity"), ctx, factors)
        baked = bool(ctx.get("baked"))
        km = f.get("km_day") if baked else None                       # trips × km_trip
        basis_km_total = (float(ctx.get("basis_km") or 0) * f["trips"]) if baked else None
        co2_t = (round(km * conversions._emissions(factors, r.get("vehicle_type")) / 1000.0, 3)
                 if km is not None else None)
        cycles = int(ctx.get("cycles_per_vehicle_day") or 0)
        # the fleet this month needs: trips spread over the working days, at what one
        # vehicle can do in a shift on this route — None when unbaked (no cycle)
        vehicles = (int(-(-f["trips"] // max(1, cycles * work_days))) if (baked and cycles > 0 and f["trips"] > 0)
                    else (0 if baked else None))
        planned = unit_prices(f.get("eur"), f["tonnes"], f["trips"], basis_km_total)
        planned["source"] = ctx.get("rate_source")
        planned["partial"] = bool(f.get("eur_partial"))
        planned["eur_adj"] = f.get("eur_adj")
        fair = unit_prices(f.get("fair_eur"), f["tonnes"], f["trips"], basis_km_total)
        fair["flags"] = list(((ctx.get("fair") or {}).get("flags")) or [])
        fair["fuel_share_pct"] = (ctx.get("fair") or {}).get("fuel_share_pct")
        out.append({
            "route_id": r["route_id"], "discipline": r.get("discipline") or "", "section_id": r.get("section_id") or "",
            "ipt": ctx.get("ipt") or r.get("ipt"), "month_index": int(r["month_index"]), "status": r.get("status"),
            "material_type": r.get("material_type"), "vehicle_type": r.get("vehicle_type"),
            "vehicle_short": ctx.get("vehicle_short"), "vehicle_class": ctx.get("vehicle_class"),
            "unit": r.get("unit"), "quantity": _num(r.get("quantity")),
            "origin_name": ctx.get("origin_name"), "dest_name": ctx.get("dest_name"),
            "tonnes": f["tonnes"], "trips": f["trips"], "vehicles": vehicles,
            "baked": baked, "km_trip": ctx.get("km_trip"), "basis_km": ctx.get("basis_km"),
            "cycle_min": ctx.get("cycle_min"), "cycles_per_vehicle_day": (cycles if baked else None),
            "km": km, "tonne_km": f.get("tonne_km"), "co2_t": co2_t,
            "planned": planned, "fair": fair,
            "flags": list(ctx.get("flags") or []),
        })
        tot["lines"] += 1
        tot["tonnes"] += f["tonnes"]; tot["trips"] += f["trips"]
        if baked:
            tot["baked_lines"] += 1
            tot["km"] += km or 0.0; tot["tonne_km"] += f.get("tonne_km") or 0.0; tot["co2_t"] += co2_t or 0.0
        if planned["eur"] is not None:
            tot["planned_eur"] += planned["eur"]; tot["planned_lines"] += 1
            tot["planned_tonnes"] += f["tonnes"]; tot["planned_trips"] += f["trips"]
            tot["planned_km"] += basis_km_total or 0.0
            if planned["source"] == "target":
                tot["planned_target_lines"] += 1
        if fair["eur"] is not None:
            tot["fair_eur"] += fair["eur"]; tot["fair_lines"] += 1
            tot["fair_tonnes"] += f["tonnes"]; tot["fair_trips"] += f["trips"]; tot["fair_km"] += basis_km_total or 0.0
    totals = {
        "lines": tot["lines"], "baked_lines": tot["baked_lines"],
        "excludes_unbaked": tot["baked_lines"] < tot["lines"],
        "tonnes": round(tot["tonnes"], 3), "trips": tot["trips"],
        "km": round(tot["km"], 2), "tonne_km": round(tot["tonne_km"], 1), "co2_t": round(tot["co2_t"], 3),
        # kg CO2e per tonne, over the BAKED tonnes only (the unbaked ones have no km)
        "co2_kg_per_t": (round(tot["co2_t"] * 1000.0 / sum(l["tonnes"] for l in out if l["baked"]), 2)
                         if tot["baked_lines"] and sum(l["tonnes"] for l in out if l["baked"]) > 0 else None),
        "planned": (unit_prices(tot["planned_eur"], tot["planned_tonnes"], tot["planned_trips"], tot["planned_km"])
                    if tot["planned_lines"] else unit_prices(None, 0, 0, 0)),
        "planned_lines": tot["planned_lines"], "planned_target_lines": tot["planned_target_lines"],
        "fair": (unit_prices(tot["fair_eur"], tot["fair_tonnes"], tot["fair_trips"], tot["fair_km"])
                 if tot["fair_lines"] else unit_prices(None, 0, 0, 0)),
        "fair_lines": tot["fair_lines"],
    }
    return {"lines": out, "totals": totals, "costing": cost,
            "working_days_per_month": work_days}

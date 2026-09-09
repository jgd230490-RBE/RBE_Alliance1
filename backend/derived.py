"""
Look-ahead v2, slice 2 (2026-09-09) — context and derived figures, computed on READ.

WHAT THIS IS
------------
The columns the Commit view prints beside a day's quantity — trips, vehicles, km/trip,
km/day, km/vehicle, loaded t·km — and the context chips beside the line — origin→dest,
IPT, work section, material, vehicle, payload, baked state, cycle. Nothing here is
stored. `decorate()` takes the response of days.list_days() and returns it with a
`context` and `week_derived` block per line, a `derived` block per day, and one `totals`
block for the KPI strip. Delete this module and the day layer is exactly slice 1.

THE RULES THAT MATTER
---------------------
1. **One source for the haul cycle: network.route_analysis(), alt 0, THIS line's
   vehicle profile.** That is the path /api/routes/analysis-batch and
   /api/public/month-kpis already read, so the Look-ahead, the dashboard and the public
   map cannot disagree about how many movements one vehicle makes in a day. The
   brief's `cycles_per_veh = floor(shift_min / cycle_min)` is exactly that row's
   `trips_per_day`, and it is READ from the row, not recomputed here.

2. **The formulas are the brief's §4, verbatim**, per day:
        trips          = ceil(qty / payload)           -- or qty itself if unit is vehicles
        cycles_per_veh = floor(shift_min / cycle_min)  -- route_analysis().trips_per_day
        vehicles_need  = ceil(trips / max(cycles_per_veh, 1))
        km_trip        = distance_km + (return_km if return_baked else 0)
        km_day         = trips * km_trip
        km_per_vehicle = km_day / vehicles_need
        tonne_km       = tonnes(qty) * distance_km     -- loaded leg only
   € (slice 5, 2026-09-09 evening) is the SUM of the three route rates where filled —
   not the brief's "first non-null", because the commonest Estonian quote is a base
   charge per load PLUS a per-km rate and an exclusive rule cannot hold it:
        eur = trips × rate_eur_per_load + tonnes × rate_eur_per_t
              + trips × basis_km × rate_eur_per_km
   `basis_km` follows the route's km_basis: 'round_trip' (loaded + baked return —
   the default, because a haulier charges the empty leg too) or 'loaded'. The human
   chose per-route on 09 Sep (C20), and the same basis is used for t·km.

3. **A route that is not baked for the line's vehicle gets trips and tonnes only.**
   Decided by the human on 09 Sep: "the route has to be baked — if not, show a
   notification." No distance is invented, so no vehicle count, no km and no t·km:
   those read None and the line carries the `UNBAKED` flag (brief §6). The brief's
   "45 km/h + 12 + 8 ‡" fallback cycle needs a distance to apply the speed to, and an
   unbaked route has none — so it is NOT used anywhere in this module. The only ‡ the
   figures carry is the one route_analysis() already makes: a return leg that is not
   baked, where the outbound duration stands in (`return_estimated`). Then the cycle is
   marked ‡ and km_trip excludes the return, per the brief's `if return_baked`.

4. **Payload is the planning figure, and the fallback is month-kpis' fallback.** A
   vehicle factors.json does not know falls back to the first PLANNING vehicle (V07,
   18 t), never to _default's 20 t, and says so in `payload_fallback` — the same rule
   /api/public/month-kpis applies, asserted equal in the tests. Tonnes for a line typed
   in vehicles are qty × that same payload, so trips and tonnes never disagree.

5. **Totals are computed AFTER the access filter**, on whatever lines the caller may
   see. `vehicles_peak` is the largest same-day SUM of vehicles across lines — the
   "vehicles / day peak" KPI — not the sum of per-line peaks, which would overstate a
   fleet that two lines use on different days.

WHAT IS NOT DONE (deliberately)
-------------------------------
No € (slice 5). No carrier / supplier (brief item 7, omitted by the human for now). No
clash rail beyond the single UNBAKED flag (slice 3). No figures on the week rows for the
Horizon view. No UI — slice 6.
"""
import math

import conversions
import db
import network

#: The one flag this slice raises. The full set is slice 3's.
FLAG_UNBAKED = "UNBAKED"


def _ceil(x):
    """ceil() that does not turn 3.0000000000000004 into 4."""
    return int(math.ceil(round(float(x), 9)))


# --------------------------------------------------------------------------- #
#  Per-line context                                                            #
# --------------------------------------------------------------------------- #
def payload_for(vehicle, factors):
    """
    (payload_t, fallback_vehicle_or_None) — the /api/public/month-kpis rule, verbatim.

    A vehicle factors.json knows: its own payload, no fallback. Anything else — an
    unknown name, a vehicle without a payload, a blank — the first planning vehicle's
    payload, and its name, so the caller can say so.
    """
    vs = factors.get("vehicles", {}) or {}
    known = bool(vehicle and vs.get(vehicle) and (vs.get(vehicle) or {}).get("payload_t"))
    if known:
        return float(vs[vehicle]["payload_t"]), None
    planning = conversions.planning_vehicle_names(factors)
    fb_name = planning[0] if planning else None
    fb_payload = float((vs.get(fb_name) or {}).get("payload_t") or 18.0)
    return fb_payload, fb_name


def vehicle_short(vehicle, factors):
    """
    The short label the chips print: the EU code (V12) when factors.json carries one,
    otherwise the legacy name without its Artic/Rigid prefix — the same rule the
    Routes table's chips already use (`vehShort` in frontend/index.html).
    """
    if not vehicle:
        return None
    v = (factors.get("vehicles", {}) or {}).get(vehicle) or {}
    code = (v.get("code") or "").strip()
    if code:
        return code
    for pre in ("Artic ", "Rigid "):
        if vehicle.startswith(pre):
            return vehicle[len(pre):]
    return vehicle


KM_BASES = ("round_trip", "loaded")


def km_basis_of(route):
    """The route's km basis, defaulting to round_trip — what a haulier charges."""
    b = (route or {}).get("km_basis")
    return b if b in KM_BASES else "round_trip"


def _num(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _routes_and_names():
    """{route_id: {origin_id, dest_id, ipt, names, rates, cap, km_basis}} for the tenant."""
    names = {r["id"]: r["name"] for r in db.query(
        "SELECT id, name FROM locations WHERE tenant_id = ?", (db.current_tenant(),))}
    out = {}
    for r in db.query("SELECT * FROM routes WHERE tenant_id = ?", (db.current_tenant(),)):
        out[r["id"]] = {
            "origin_id": r.get("origin_id"), "dest_id": r.get("dest_id"),
            "route_ipt": r.get("ipt"),
            "origin_name": names.get(r.get("origin_id")),
            "dest_name": names.get(r.get("dest_id")),
            "max_vehicles_per_day": (int(r["max_vehicles_per_day"])
                                     if r.get("max_vehicles_per_day") is not None else None),
            "rate_eur_per_load": _num(r.get("rate_eur_per_load")),
            "rate_eur_per_t": _num(r.get("rate_eur_per_t")),
            "rate_eur_per_km": _num(r.get("rate_eur_per_km")),
            "km_basis": km_basis_of(r),
        }
    return out


def cycle_for(route_id, vehicle, cache=None):
    """
    The alt-0 route_analysis() row for (route, vehicle), or None when the route is not
    baked for that vehicle. One call per pair per request, exactly as month-kpis.
    """
    key = (route_id, vehicle)
    if cache is not None and key in cache:
        return cache[key]
    row = None
    try:
        res = network.route_analysis(route_id, profiles=[vehicle] if vehicle else None)
        for r in res.get("rows", []) or []:
            if r.get("alt_index") == 0 and (not vehicle or r.get("profile") == vehicle):
                row = r
                break
    except Exception:
        row = None
    if cache is not None:
        cache[key] = row
    return row


def line_context(line, factors, routes=None, cache=None):
    """
    Everything a line's chips and expanded strip need, none of it per-day.

    `line` is one entry of days.list_days()["lines"] (route_id, unit, material_type,
    vehicle_type, ipt, discipline, section_id, week). Returns a dict; see the module
    docstring for what `baked`, `cycle_mark` and `flags` mean.
    """
    routes = routes if routes is not None else _routes_and_names()
    plan = factors.get("planning", {}) or {}
    shift_hr = float(plan.get("shift_hours_per_day") or 10)
    rid, veh = line.get("route_id"), line.get("vehicle_type")
    payload, fb = payload_for(veh, factors)
    rt = routes.get(rid) or {}
    row = cycle_for(rid, veh, cache)
    baked = row is not None
    return_baked = bool(baked and not row.get("return_estimated"))
    dist = float(row.get("loaded_km") or 0) if baked else None
    ret = float(row.get("return_km") or 0) if return_baked else None
    km_trip = (dist + (ret if return_baked else 0.0)) if baked else None
    cycle_min = round(float(row.get("cycle_hr") or 0) * 60.0, 1) if baked else None
    week_qty = float((line.get("week") or {}).get("planned_qty") or 0)
    flags = []
    if not baked and week_qty > 0:
        flags.append(FLAG_UNBAKED)
    basis = rt.get("km_basis") or "round_trip"
    basis_km = (dist if basis == "loaded" else km_trip) if baked else None
    rates = {"per_load": rt.get("rate_eur_per_load"), "per_t": rt.get("rate_eur_per_t"),
             "per_km": rt.get("rate_eur_per_km")}
    rate_set = any(v is not None for v in rates.values())
    return {
        "origin_id": rt.get("origin_id"), "origin_name": rt.get("origin_name"),
        "dest_id": rt.get("dest_id"), "dest_name": rt.get("dest_name"),
        "route_ipt": rt.get("route_ipt"),
        "ipt": line.get("ipt"), "discipline": line.get("discipline"),
        "section_id": line.get("section_id"),
        "material_type": line.get("material_type"),
        "vehicle_type": veh, "vehicle_short": vehicle_short(veh, factors),
        "unit": line.get("unit"),
        "payload_t": payload, "payload_fallback": fb,
        "baked": baked, "return_baked": return_baked,
        "distance_km": round(dist, 2) if dist is not None else None,
        "return_km": round(ret, 2) if ret is not None else None,
        "km_trip": round(km_trip, 2) if km_trip is not None else None,
        "cycle_min": cycle_min,
        # 'here' = both legs baked · 'here_return_estimated' = the return stood in for by
        # the outbound and the cycle is marked ‡ · None = not baked, no cycle at all
        "cycle_source": (None if not baked
                         else ("here" if return_baked else "here_return_estimated")),
        "cycle_mark": ("‡" if (baked and not return_baked) else None),
        "shift_hours": shift_hr,
        # brief: cycles_per_veh = floor(shift_min / cycle_min). route_analysis() already
        # holds exactly that as trips_per_day; reading it is what keeps every surface equal
        "cycles_per_vehicle_day": (int(row.get("trips_per_day") or 0) if baked else None),
        # slices 3-5: the route's planning cap and contract rates, as typed (never seeded)
        "max_vehicles_per_day": rt.get("max_vehicles_per_day"),
        "rates": rates, "rate_set": rate_set,
        "km_basis": basis,
        "basis_km": (round(basis_km, 2) if basis_km is not None else None),
        "flags": flags,
    }


# --------------------------------------------------------------------------- #
#  Per-day figures                                                             #
# --------------------------------------------------------------------------- #
def tonnes_of(qty, ctx, factors):
    """Tonnes for a quantity in the line's unit. vehicles × the SAME payload trips use."""
    q = float(qty or 0)
    if ctx.get("unit") == "vehicles":
        return q * float(ctx.get("payload_t") or 0)
    return float(conversions.to_tonnes(q, ctx.get("unit"), ctx.get("material_type"),
                                       ctx.get("vehicle_type"), factors))


def day_figures(qty, ctx, factors):
    """
    Brief §4 for one day's planned quantity. Unbaked ⇒ trips and tonnes only; every
    distance-based figure is None, never 0 — a zero would read as "nothing moves".
    """
    q = float(qty or 0)
    t = tonnes_of(q, ctx, factors)
    payload = float(ctx.get("payload_t") or 0)
    if ctx.get("unit") == "vehicles":
        trips = _ceil(q)
    else:
        trips = _ceil(t / payload) if payload > 0 else 0
    out = {"tonnes": round(t, 3), "trips": trips,
           "vehicles": None, "km_day": None, "km_per_vehicle": None, "tonne_km": None,
           "eur": None, "eur_partial": False}
    rates = ctx.get("rates") or {}
    per_load, per_t, per_km = rates.get("per_load"), rates.get("per_t"), rates.get("per_km")
    if not ctx.get("baked"):
        # no distance ⇒ the per-km term cannot be priced; the other two still can, and
        # the result says it is partial rather than pretending to be the whole cost
        if per_load is not None or per_t is not None:
            out["eur"] = round(trips * (per_load or 0) + t * (per_t or 0), 2)
            out["eur_partial"] = per_km is not None
        return out
    cycles = max(int(ctx.get("cycles_per_vehicle_day") or 0), 1)
    vehicles = _ceil(trips / cycles) if trips > 0 else 0
    km_trip = float(ctx.get("km_trip") or 0)
    basis_km = float(ctx.get("basis_km") if ctx.get("basis_km") is not None else km_trip)
    km_day = trips * km_trip
    out.update({
        "vehicles": vehicles,
        "km_day": round(km_day, 2),
        "km_per_vehicle": round(km_day / vehicles, 2) if vehicles > 0 else 0.0,
        # t·km on the route's chosen basis (C20, decided per route on 09 Sep)
        "tonne_km": round(t * basis_km, 1),
    })
    if ctx.get("rate_set"):
        out["eur"] = round(trips * (per_load or 0) + t * (per_t or 0)
                           + trips * basis_km * (per_km or 0), 2)
    return out


# --------------------------------------------------------------------------- #
#  The read                                                                    #
# --------------------------------------------------------------------------- #
def decorate(res, factors=None):
    """
    Add `context`, per-day `derived`, `week_derived` to every line of a
    days.list_days() response, and a `totals` block to the response. Mutates and returns
    `res`. Call it AFTER the access filter — totals are for what the caller can see.
    """
    factors = factors or conversions.load_factors()
    routes = _routes_and_names()
    cache = {}
    by_day = {}                                  # date -> summed vehicles across lines
    tot = {"planned_t": 0.0, "trips": 0, "tonne_km": 0.0, "km": 0.0,
           "lines": 0, "unbaked_lines": 0, "eur": 0.0, "eur_lines": 0, "eur_partial": False}
    for line in res.get("lines", []) or []:
        ctx = line_context(line, factors, routes=routes, cache=cache)
        line["context"] = ctx
        wk = {"tonnes": 0.0, "trips": 0, "vehicles_peak": (0 if ctx["baked"] else None),
              "km": (0.0 if ctx["baked"] else None),
              "tonne_km": (0.0 if ctx["baked"] else None),
              "eur": None, "eur_partial": False}
        for d in line.get("days", []) or []:
            f = day_figures(d.get("planned_qty"), ctx, factors)
            d["derived"] = f
            wk["tonnes"] += f["tonnes"]
            wk["trips"] += f["trips"]
            if f["eur"] is not None:
                wk["eur"] = (wk["eur"] or 0.0) + f["eur"]
                wk["eur_partial"] = wk["eur_partial"] or f["eur_partial"]
            if ctx["baked"]:
                wk["vehicles_peak"] = max(wk["vehicles_peak"], f["vehicles"] or 0)
                wk["km"] += f["km_day"] or 0.0
                wk["tonne_km"] += f["tonne_km"] or 0.0
                by_day[d["day_date"]] = by_day.get(d["day_date"], 0) + (f["vehicles"] or 0)
        wk["tonnes"] = round(wk["tonnes"], 3)
        if wk["eur"] is not None:
            wk["eur"] = round(wk["eur"], 2)
        if ctx["baked"]:
            wk["km"] = round(wk["km"], 2)
            wk["tonne_km"] = round(wk["tonne_km"], 1)
        line["week_derived"] = wk
        tot["planned_t"] += wk["tonnes"]
        tot["trips"] += wk["trips"]
        tot["tonne_km"] += wk["tonne_km"] or 0.0
        tot["km"] += wk["km"] or 0.0
        tot["lines"] += 1
        if FLAG_UNBAKED in ctx["flags"]:
            tot["unbaked_lines"] += 1
        if wk["eur"] is not None:
            tot["eur"] += wk["eur"]
            tot["eur_lines"] += 1
            tot["eur_partial"] = tot["eur_partial"] or wk["eur_partial"]
    peak_date = max(by_day, key=lambda k: by_day[k]) if by_day else None
    res["totals"] = {
        "planned_t": round(tot["planned_t"], 3),
        "trips": tot["trips"],
        # the largest same-day fleet across every visible line — NOT a sum of line peaks
        "vehicles_peak": (by_day[peak_date] if peak_date else 0),
        "vehicles_peak_date": peak_date,
        "vehicles_by_day": by_day,
        "tonne_km": round(tot["tonne_km"], 1),
        "km": round(tot["km"], 2),
        "lines": tot["lines"],
        "unbaked_lines": tot["unbaked_lines"],
        # every distance figure above omits unbaked lines; say so where the KPI is read
        "excludes_unbaked": tot["unbaked_lines"] > 0,
        # € only where a rate is typed: None when no line has one, so a KPI can say
        # "rate not set" instead of printing €0
        "eur": (round(tot["eur"], 2) if tot["eur_lines"] else None),
        "eur_lines": tot["eur_lines"],
        "eur_partial": tot["eur_partial"],
    }
    return res

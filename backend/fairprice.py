"""
Fair price, 2026-09-10 (night) — a haulage cost MODEL, computed per line from what the
forecast already holds: vehicle, distance, tonnage, month — and the live diesel index.

WHY THIS EXISTS
---------------
The human: "a backend formula that works out a fair price dependent on the season,
vehicle, distance and tonnage which will all be in the forecast, leaving just the fuel
price which has already been added." The typed rates (route quote, Config target) are
what somebody agreed or wants; this is what the job SHOULD cost at a public cost
structure. Three figures can now sit on a line: Quote · Target · Fair. The fair one is
always marked `fair (model)` and never replaces or mixes with the other two.

THE FORMULA
-----------
    litres_trip  = km_laden × L100(laden) / 100 + km_empty × L100(empty) / 100
                   L100(empty) = class base;  L100(laden) = base + per_tonne × tonnes carried
                   × (1 + winter_uplift) in winter months
    fuel_eur     = litres_trip × index €/L                    (the EU Oil Bulletin row)
    time_eur     = cycle_h × (driver €/h + vehicle standing €/h)
    running_eur  = km_trip × running €/km
    eur_trip     = (fuel + time + running) × (1 + margin)
    eur_per_t    = eur_trip / tonnes carried
A line's fair € for a day = trips × eur_trip (the same trips slice 2 derives).

THE COEFFICIENTS — SEEDED, SOURCED, EDITABLE (the human's decision, 10 Sep night)
-----------------------------------------------------------------------------------
This is the one place the product ships numbers it did not get from the human. The
standing rule "never seed a rate" was about CONTRACT rates and still holds for them.
A cost model's coefficients are public benchmarks; the human chose to have them seeded
so that nothing has to be typed. They live in factors.json under `fair_price`, every
one with a `_source` line beside it, and the Config page shows and edits them. Three
of them have NO public source and are marked "assumption": the new-vehicle price, the
running €/km, the margin. Replace them with the fleet's own figures.

Sources (read 2026-09-10 through the human's browser pane):
  * artic 40-44 t laden ~34 L/100 km (ICCT EU HDV baseline 2018, regional cycle) and
    +0.4-0.95 L/100 km per tonne of payload (UK DfT payload study) — both via
    lkw-control.com's sourced summary. The LOW end, 0.4, is used.
  * 32 t rigid ~35 L/100 km average-laden — DERIVED from the DEFRA/BEIS 0.95 kg CO2e/km
    already in factors.json ÷ 2.68 kg CO2e per litre of diesel. The weakest figure.
  * winter +8 % (Nov-Mar): US DOE fueleconomy.gov puts cars 15 % worse at -7 °C in city
    driving; the HGV effect is smaller. An assumption inside a sourced range.
  * spring thaw: Transpordiamet's 8 t load limit on many state and most municipal roads,
    early March to mid-April, permits free (Virumaa Teataja, 11 Mar 2025). A FLAG on the
    figure, not a number — which roads are restricted is not data the product has.
  * driver €20/h: Statistics Estonia Q2 2026 average gross €2,243, median €1,840;
    palgad.ee lorry-driver band €1,134-2,456; taken as €2,000 gross × 1.338 employer
    taxes (33 % social + 0.8 % unemployment) ÷ 160 h ÷ 0.85 productive.
  * sanity band €0.50-2.00 per km all-in (IRU, Europe 2025) — a check, not an input.

WHAT IS NOT HERE
----------------
No route type: the product stores no surface or road class. The time term already
prices a slow route (HERE's duration), which is the honest proxy. No per-haulier
anything. Not on the supplier's PDF — this is the planner's negotiating figure.
"""
import json
import os

DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "factors.json")
KG_CO2E_PER_L_DIESEL = 2.68

_FILE_CACHE = {"doc": None}


def file_defaults():
    """The `fair_price` block of factors.json — the seed, used when the live config
    document predates the block (a config row saved before 10 Sep night)."""
    if _FILE_CACHE["doc"] is None:
        try:
            with open(DEFAULTS_PATH, "r", encoding="utf-8") as f:
                _FILE_CACHE["doc"] = (json.load(f).get("fair_price") or {})
        except Exception:
            _FILE_CACHE["doc"] = {}
    return _FILE_CACHE["doc"]


def _num(v, default=None):
    try:
        return float(v) if v is not None and v != "" else default
    except (TypeError, ValueError):
        return default


def params(factors):
    """The model's coefficients from the live factors document, falling back to the file
    block key by key, so an older config row still prices. Numbers only; `_source`
    lines are left where they are."""
    live = (factors or {}).get("fair_price") or {}
    base = file_defaults()
    out = {}
    for k in set(base) | set(live):
        if k.startswith("_"):
            continue
        v = live.get(k, base.get(k))
        if isinstance(v, dict):
            bv = base.get(k) if isinstance(base.get(k), dict) else {}
            lv = live.get(k) if isinstance(live.get(k), dict) else {}
            out[k] = {kk: (lv.get(kk, bv.get(kk))) for kk in set(bv) | set(lv) if not kk.startswith("_")}
        else:
            out[k] = v
    return out


def vehicle_class(vehicle, factors):
    """'artic' for a tractor + semi (eu_category names O4, or gvw >= 36 t), else 'rigid'."""
    v = ((factors or {}).get("vehicles") or {}).get(vehicle) or {}
    cat = str(v.get("eu_category") or "")
    if "O4" in cat or "O3" in cat or _num(v.get("gvw_t"), 0) >= 36 or "Artic" in str(vehicle):
        return "artic"
    return "rigid"


def is_winter(month, p):
    return int(month) in [int(m) for m in (p.get("season") or {}).get("winter_months") or []]


def is_thaw(month, p):
    return int(month) in [int(m) for m in (p.get("season") or {}).get("thaw_months") or []]


def litres_per_100km(cls, tonnes_carried, p, month=None):
    """Empty base for the class + per-tonne × what is on the truck, winter uplift on top."""
    cons = p.get("consumption") or {}
    base = _num((cons.get(cls) or {}).get("l_per_100km_empty") if isinstance(cons.get(cls), dict)
                else cons.get(f"{cls}_l_per_100km_empty"))
    per_t = _num(cons.get("l_per_100km_per_tonne"))
    if base is None or per_t is None:
        return None
    l100 = base + per_t * max(float(tonnes_carried or 0), 0.0)
    if month is not None and is_winter(month, p):
        l100 *= 1.0 + _num((p.get("season") or {}).get("winter_consumption_uplift_pct"), 0.0) / 100.0
    return l100


def trip_cost(ctx, month, index_eur_per_l, p):
    """
    One trip's fair cost for a line's context (slice 2's `context` dict: baked,
    distance_km, return_km, return_baked, cycle_min, payload_t, vehicle_type) in a
    given month, at a given diesel index. None when the route is not baked (no distance
    — nothing is invented), when the index is missing, or when a coefficient is blank.
    """
    if not ctx or not ctx.get("baked"):
        return None
    index = _num(index_eur_per_l)
    if index is None:
        return None
    cls = ctx.get("vehicle_class") or "rigid"
    km_out = _num(ctx.get("distance_km"), 0.0)
    km_back = _num(ctx.get("return_km"))
    if km_back is None:
        km_back = km_out              # return not baked: the outbound stands in, as the cycle does
    payload = _num(ctx.get("payload_t"), 0.0)
    l100_laden = litres_per_100km(cls, payload, p, month)
    l100_empty = litres_per_100km(cls, 0.0, p, month)
    driver = _num(p.get("driver_eur_per_h"))
    standing = _num(p.get("vehicle_standing_eur_per_h"))
    running = _num(p.get("running_eur_per_km"))
    margin = _num(p.get("margin_pct"), 0.0)
    cycle_h = _num(ctx.get("cycle_min"), 0.0) / 60.0
    if None in (l100_laden, l100_empty, driver, standing, running):
        return None
    litres = km_out * l100_laden / 100.0 + km_back * l100_empty / 100.0
    fuel = litres * index
    time = cycle_h * (driver + standing)
    run = (km_out + km_back) * running
    sub = fuel + time + run
    total = sub * (1.0 + margin / 100.0)
    flags = []
    if is_winter(month, p):
        flags.append("WINTER")
    if is_thaw(month, p):
        flags.append("THAW")
    return {
        "eur_trip": round(total, 2),
        "eur_per_t": (round(total / payload, 2) if payload > 0 else None),
        "eur_per_km": (round(total / (km_out + km_back), 3) if (km_out + km_back) > 0 else None),
        "litres_trip": round(litres, 2),
        "fuel_eur": round(fuel, 2), "time_eur": round(time, 2), "running_eur": round(run, 2),
        "margin_eur": round(total - sub, 2),
        "fuel_share_pct": (round(fuel / sub * 100.0, 1) if sub > 0 else None),
        "l_per_100km_laden": round(l100_laden, 1), "l_per_100km_empty": round(l100_empty, 1),
        "vehicle_class": cls, "index_eur_per_l": index, "month": int(month),
        "flags": flags,
    }


def summary(p, index_eur_per_l):
    """What a page prints beside the figures: the coefficients in force and their status."""
    cons = p.get("consumption") or {}
    return {
        "index_eur_per_l": _num(index_eur_per_l),
        "driver_eur_per_h": _num(p.get("driver_eur_per_h")),
        "vehicle_standing_eur_per_h": _num(p.get("vehicle_standing_eur_per_h")),
        "running_eur_per_km": _num(p.get("running_eur_per_km")),
        "margin_pct": _num(p.get("margin_pct")),
        "l_per_100km_per_tonne": _num(cons.get("l_per_100km_per_tonne")),
        "rigid_l_per_100km_empty": _num((cons.get("rigid") or {}).get("l_per_100km_empty")),
        "artic_l_per_100km_empty": _num((cons.get("artic") or {}).get("l_per_100km_empty")),
        "winter_months": list((p.get("season") or {}).get("winter_months") or []),
        "winter_consumption_uplift_pct": _num((p.get("season") or {}).get("winter_consumption_uplift_pct")),
        "thaw_months": list((p.get("season") or {}).get("thaw_months") or []),
        "complete": all(_num(p.get(k)) is not None for k in
                        ("driver_eur_per_h", "vehicle_standing_eur_per_h", "running_eur_per_km"))
                    and _num(cons.get("l_per_100km_per_tonne")) is not None
                    and _num(index_eur_per_l) is not None,
    }

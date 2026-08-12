"""
First-run seeding.

Runs only when the forecasts table is empty, so it never overwrites real data.
It loads two things as *Approved* forecasts so the Public Route Map shows
something meaningful on the very first deploy:

  1. The real assigned routes from wp3_timeline_forecast.csv (entered in vehicles).
  2. A spread of demo routes in MIXED units (m3 / tonnes / vehicles) so you can
     see the conversion engine working end-to-end on the map.

Delete these later from the Approvals screen, or just ignore them — real
submissions live alongside and behave identically.
"""
import csv
import json
import os

from db import execute, count_forecasts

_HERE = os.path.dirname(__file__)
_SEED = os.path.join(_HERE, "seed_data")


def _routes():
    with open(os.path.join(_SEED, "routes.json"), encoding="utf-8") as f:
        return json.load(f)


def _insert(route_id, month_index, quantity, unit, material, vehicle, status="Approved"):
    execute(
        """
        INSERT INTO forecasts
            (id, route_id, month_index, quantity, unit, material_type, vehicle_type, status, reject_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (route_id, month_index) DO NOTHING
        """,
        (f"{route_id}::{month_index}", route_id, month_index, float(quantity),
         unit, material, vehicle, status, None),
    )


def _seed_real():
    """Assigned rows from the WP3 timeline CSV, as vehicle forecasts."""
    mat_by_route = {r["route_id"]: r["material_guess"] for r in _routes()}
    path = os.path.join(_SEED, "wp3_timeline_forecast.csv")
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("status", "").strip() != "Assigned":
                continue
            rid = row["route_id"].strip()
            material = mat_by_route.get(rid, "Soil")
            for m in range(1, 61):
                val = row.get(f"m{m}_veh", "0").strip()
                try:
                    v = int(float(val))
                except ValueError:
                    v = 0
                if v > 0:
                    _insert(rid, m, v, "vehicles", material, "8x4 Tipper")


def _seed_demo():
    """A handful of demo routes in mixed units, to exercise the conversion engine."""
    routes = _routes()
    # skip the two real ones so we don't clash
    real = {"HR-EW-ANELEMALIM-IPT3-WS3", "HR-TM-MUUGA-SOODEVAHE-IPT6-WS7"}
    pool = [r for r in routes if r["route_id"] not in real]
    units = ["m3", "t", "vehicles"]
    vehicles = ["8x4 Tipper", "Artic Tipper", "ADT / Dumper"]

    # deterministic pick: every 6th route, up to 10
    picks = pool[::6][:10]
    for i, r in enumerate(picks):
        rid = r["route_id"]
        material = r["material_guess"]
        unit = units[i % len(units)]
        vehicle = vehicles[i % len(vehicles)]
        start = 5 + (i % 8)          # month the route ramps up
        dur = 5 + (i % 4)            # active months
        # aim for ~30-70 vehicles/month regardless of input unit, so the demo
        # map looks consistent and shows conversion working across units
        veh_target = 30 + (i % 5) * 10
        if unit == "m3":
            peak = veh_target * 13   # ~ payload/density
        elif unit == "t":
            peak = veh_target * 20   # ~ payload
        else:
            peak = veh_target
        for k in range(dur + 1):
            m = start + k
            if m > 60:
                break
            # simple triangular profile peaking in the middle
            frac = 1 - abs((k - dur / 2) / (dur / 2 or 1))
            qty = round(peak * max(frac, 0.15), 1)
            if unit == "vehicles":
                qty = int(round(qty))
            _insert(rid, m, qty, unit, material, vehicle)


def seed_if_empty():
    if count_forecasts() > 0:
        return False
    _seed_real()
    _seed_demo()
    return True

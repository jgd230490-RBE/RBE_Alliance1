"""
Week 1, 2026-09-01 (Task D2) — stockpile max capacity and typed weekly consumption.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
This is **storage on a location**: how much a pile can hold, how much was in it to
start with, and how much somebody typed as consumed in a given week. It is NOT the
haul/gate capacity model — that is Phase 5a/5b, it lives on routes and gates, and
nothing here touches it.

There is no file, no CSV, no importer and no upload endpoint. Consumption is typed.

THE BALANCE IS A READ MODEL
---------------------------
Nothing below is stored. `balances()` recomputes from three sources every time:

    inbound   forecast_weeks.actual_qty on lines whose route.dest_id is this location
    consumed  stockpile_weeks.consumed_qty
    opening   locations.opening_qty

    balance_end(W) = opening + Σ inbound(≤ W) - Σ consumed(≤ W)
    remaining      = capacity_qty - balance_end        (null when no capacity is set)
    over           = balance_end > capacity_qty

Storing it would be a second copy that drifts the moment anyone edits an actual, and
actuals are edited constantly — that is the point of the look-ahead.

⚠️ THREE THINGS THIS DELIBERATELY DOES NOT GUESS
------------------------------------------------
1. **No week actual means inbound 0.** It does NOT fall back to a quarter of the month
   forecast. A forecast is what somebody intends to deliver; a balance built from
   intentions would report stock that is not there.
2. **A route with no dest_id contributes nothing.** Old routes may have a null
   destination. Attributing their tonnage to the nearest pile would be inventing it.
3. **No capacity recorded is not zero capacity.** `remaining` comes back null and
   `over` stays false; the balance still computes and the UI shows "—" for max.

The running total starts at month_index 1 regardless of the window asked for, because
`opening_qty` is defined as the stock at the start of month 1. Windowing the sum as
well as the output would silently drop everything that happened before the window.
"""
import datetime

import conversions
import db
import weeks as weeks_mod

# Location types that can hold stock. The build list names these four for the capacity
# fields; a Quarry or a Port is a source, not a store, and showing it a capacity box
# invites a number nobody means.
STORAGE_TYPES = ("Stockpile", "Site", "Compound", "Rail head")

# The build list's default. Applied on WRITE, not as a column default — see the note in
# db.init_weeks_db() for why "not recorded" has to stay distinguishable from "tonnes".
DEFAULT_CAPACITY_UNIT = "t"
CAPACITY_UNITS = ("m3", "t")


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
#  Master data                                                                 #
# --------------------------------------------------------------------------- #
def set_capacity(location_id, capacity_qty=None, capacity_unit=None, opening_qty=None):
    """
    Write a location's storage figures.

    Passing capacity_qty=None CLEARS the capacity, which is a real state — "we do not
    know how big this pile is" — and not the same as zero. The unit defaults to tonnes
    only when a capacity is actually being set.
    """
    cur = db.query("SELECT id, loc_type FROM locations WHERE tenant_id = ? AND id = ?",
                   (db.current_tenant(), location_id))
    if not cur:
        return {"error": "location not found"}
    cap = _num(capacity_qty)
    unit = (capacity_unit or "").strip() or None
    if cap is not None and unit not in CAPACITY_UNITS:
        unit = DEFAULT_CAPACITY_UNIT
    if cap is None:
        unit = unit if unit in CAPACITY_UNITS else None
    db.execute(
        "UPDATE locations SET capacity_qty = ?, capacity_unit = ?, opening_qty = ? "
        "WHERE tenant_id = ? AND id = ?",
        (cap, unit, _num(opening_qty), db.current_tenant(), location_id))
    return {"location_id": location_id, "capacity_qty": cap, "capacity_unit": unit,
            "opening_qty": _num(opening_qty)}


def storage_locations():
    """
    Locations that can hold stock, with their capacity figures.

    Two ways in, and the second one matters. A location of one of the four storage
    types is always listed, even with nothing recorded against it — that is how a new
    pile appears so somebody can give it a capacity. Anything ELSE is listed only once
    a figure has actually been entered against it, which is how a Quarry or a Port
    whose role is destination-or-both (the case the Data Management form shows the
    capacity fields for) reaches the panel without dragging in all 27 nodes that have
    never held anything.
    """
    rows = db.query(
        "SELECT id, name, loc_type, role, material, capacity_qty, capacity_unit, "
        "opening_qty FROM locations WHERE tenant_id = ? ORDER BY id",
        (db.current_tenant(),))
    return [r for r in rows
            if (r.get("loc_type") or "") in STORAGE_TYPES
            or r.get("capacity_qty") is not None
            or r.get("opening_qty") is not None]


# --------------------------------------------------------------------------- #
#  Consumption                                                                 #
# --------------------------------------------------------------------------- #
def consume(location_id, month_index, week_index, consumed_qty=None, unit=None,
            note=None, by=None):
    """Upsert one week's typed consumption for one location."""
    if not db.query("SELECT id FROM locations WHERE tenant_id = ? AND id = ?",
                    (db.current_tenant(), location_id)):
        return {"error": "location not found"}
    wi = int(week_index)
    if not 1 <= wi <= weeks_mod.WEEKS_PER_MONTH:
        return {"error": f"week_index must be 1-{weeks_mod.WEEKS_PER_MONTH}"}
    q = _num(consumed_qty)
    u = (unit or DEFAULT_CAPACITY_UNIT)
    have = db.query(
        "SELECT location_id FROM stockpile_weeks WHERE tenant_id = ? "
        "AND location_id = ? AND month_index = ? AND week_index = ?",
        (db.current_tenant(), location_id, int(month_index), wi))
    if have:
        db.execute(
            "UPDATE stockpile_weeks SET consumed_qty = ?, unit = ?, note = ?, "
            "updated_by = ?, updated_at = ? WHERE tenant_id = ? AND location_id = ? "
            "AND month_index = ? AND week_index = ?",
            (q, u, note, by, _now(), db.current_tenant(), location_id,
             int(month_index), wi))
    else:
        db.execute(
            "INSERT INTO stockpile_weeks (tenant_id, location_id, month_index, "
            "week_index, consumed_qty, unit, note, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (db.current_tenant(), location_id, int(month_index), wi, q, u, note,
             by, _now()))
    return {"location_id": location_id, "month_index": int(month_index),
            "week_index": wi, "consumed_qty": q, "unit": u}


# --------------------------------------------------------------------------- #
#  The balance read model                                                      #
# --------------------------------------------------------------------------- #
def _to_pile_unit(qty, from_unit, pile_unit, material, vehicle, factors):
    """
    Convert one movement into the pile's own unit.

    A forecast line can be entered in m3, tonnes or vehicles while a pile's capacity is
    in tonnes; adding those together untouched would produce a number with no meaning.
    conversions.py pivots everything through tonnes using the material density and the
    vehicle payload, so both are needed — which is why the parent forecast row is read
    for material_type and vehicle_type rather than the week row alone.

    ⚠️ A conversion that cannot be done returns None and the caller drops the movement
    rather than adding a raw figure. That undercounts visibly; adding it would
    overcount invisibly.
    """
    if qty is None:
        return None
    fu = from_unit or pile_unit
    if fu == pile_unit:
        return float(qty)
    try:
        return conversions.convert(float(qty), fu, pile_unit, material, vehicle, factors)
    except Exception:
        return None


def balances(from_month, to_month, location_id=None):
    """
    Per-location, per-week stock for a month window.

    The running total starts at month 1 whatever window is asked for; only the OUTPUT
    is windowed. See the module docstring.
    """
    lo, hi = min(int(from_month), int(to_month)), max(int(from_month), int(to_month))
    factors = conversions.load_factors()

    locs = storage_locations()
    if location_id:
        locs = [l for l in locs if l["id"] == location_id]
    if not locs:
        return {"from": lo, "to": hi, "stockpiles": []}

    # route -> destination, so a week actual can be attributed to a pile
    dest_of = {r["id"]: r.get("dest_id") for r in db.query(
        "SELECT id, dest_id FROM routes WHERE tenant_id = ?", (db.current_tenant(),))}
    # the parent forecast lines, for material and vehicle — a week row carries neither
    parent = {(f["route_id"], f["month_index"], f["discipline"] or "",
               f["section_id"] or ""): f
              for f in db.query(
                  "SELECT route_id, month_index, discipline, section_id, "
                  "material_type, vehicle_type FROM forecasts WHERE tenant_id = ?",
                  (db.current_tenant(),))}

    # ⭐ actual_qty only. A week with no actual typed contributes nothing — the month
    # forecast is NOT divided down to stand in for it.
    week_actuals = db.query(
        "SELECT route_id, month_index, discipline, section_id, week_index, "
        "actual_qty, unit FROM forecast_weeks WHERE tenant_id = ? "
        "AND actual_qty IS NOT NULL AND month_index <= ?",
        (db.current_tenant(), hi))

    consumed_rows = db.query(
        "SELECT location_id, month_index, week_index, consumed_qty, unit "
        "FROM stockpile_weeks WHERE tenant_id = ? AND month_index <= ?",
        (db.current_tenant(), hi))

    out = []
    for loc in locs:
        pile_unit = loc.get("capacity_unit") or DEFAULT_CAPACITY_UNIT
        cap = _num(loc.get("capacity_qty"))
        opening = _num(loc.get("opening_qty")) or 0.0
        material = loc.get("material")

        inbound = {}
        dropped = 0
        for w in week_actuals:
            # ⚠️ dest_id may be null on an older route. Nothing is attributed then —
            # see the module docstring, point 2.
            if dest_of.get(w["route_id"]) != loc["id"]:
                continue
            p = parent.get((w["route_id"], w["month_index"], w["discipline"] or "",
                            w["section_id"] or "")) or {}
            v = _to_pile_unit(w["actual_qty"], w["unit"], pile_unit,
                              p.get("material_type") or material,
                              p.get("vehicle_type"), factors)
            if v is None:
                dropped += 1
                continue
            inbound[(w["month_index"], w["week_index"])] = \
                inbound.get((w["month_index"], w["week_index"]), 0.0) + v

        used = {}
        for c in consumed_rows:
            if c["location_id"] != loc["id"]:
                continue
            v = _to_pile_unit(c["consumed_qty"], c["unit"], pile_unit, material,
                              None, factors)
            if v is None:
                dropped += 1
                continue
            used[(c["month_index"], c["week_index"])] = \
                used.get((c["month_index"], c["week_index"]), 0.0) + v

        running = opening
        rows = []
        for m in range(1, hi + 1):
            for w in range(1, weeks_mod.WEEKS_PER_MONTH + 1):
                inb = inbound.get((m, w), 0.0)
                con = used.get((m, w), 0.0)
                running = running + inb - con
                if m < lo:
                    continue                     # accumulated, not reported
                rows.append({
                    "month_index": m, "week_index": w,
                    "inbound": round(inb, 3), "consumed": round(con, 3),
                    "balance_end": round(running, 3),
                    # null, NOT zero, when no capacity is recorded
                    "remaining": (round(cap - running, 3) if cap is not None else None),
                    "over": bool(cap is not None and running > cap),
                    "unit": pile_unit,
                })
        out.append({
            "location_id": loc["id"], "name": loc["name"],
            "loc_type": loc.get("loc_type"), "role": loc.get("role"),
            "capacity_qty": cap, "capacity_unit": pile_unit,
            "opening_qty": opening,
            "unconvertible_movements": dropped,
            "weeks": rows,
        })
    return {"from": lo, "to": hi, "stockpiles": out}


def popup_summary(location_id):
    """
    'Stock 1 240 t / 3 000 t' for the public map popup, or None.

    Returns None rather than a half-sentence when either number is missing — the build
    list asks for the string only "when both numbers exist". The balance is taken at
    the LAST week of the current month, which is the most recent complete figure.
    """
    locs = [l for l in storage_locations() if l["id"] == location_id]
    if not locs:
        return None
    loc = locs[0]
    if _num(loc.get("capacity_qty")) is None:
        return None
    today = datetime.date.today()
    mi = weeks_mod.month_index_of(today, START_YEAR)
    if mi < 1:
        return None
    b = balances(mi, mi, location_id=location_id)["stockpiles"]
    if not b or not b[0]["weeks"]:
        return None
    last = b[0]["weeks"][-1]
    return {"balance": last["balance_end"], "capacity": b[0]["capacity_qty"],
            "unit": b[0]["capacity_unit"], "over": last["over"]}


# month_index 1 == January of this year. Mirrors main.START_YEAR; imported lazily to
# avoid a circular import, and asserted equal in the tests.
START_YEAR = 2026

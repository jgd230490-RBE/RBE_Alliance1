"""
Week 1, 2026-09-01 — the four-week look-ahead (Task C) and typed actuals (Task D).

WHAT THIS IS
------------
A week layer that sits ON the monthly forecast line. It does not widen `forecasts`,
it does not add a `week_index` to it, and it does not touch the monthly UNIQUE key.
A `forecast_weeks` row is keyed by the forecast line's own key plus a week number, so
a week can only exist for a line that exists, and the public map's monthly
aggregation is untouched.

THE FOUR RULES THAT MATTER
--------------------------
1. **Weeks exist only for APPROVED months.** A Pending month materialises nothing.
   Materialisation happens when a month line becomes Approved, and again lazily on the
   first read of the look-ahead for a line that was already Approved before this
   shipped.

2. **`derived` refreshes, `edited` and `confirmed` do not.** That is the whole reason
   `status` is three values and not a boolean. A re-approved month rewrites its
   derived weeks to quantity/4 and leaves the others exactly as somebody left them.

3. **`parent_qty` is what makes "parent month changed" truthful.** See db.py's note on
   the column. An edited week always differs from parent/4, so the flag cannot be
   derived by comparison — it needs the parent value as at the last write.

4. **Saving an actual NEVER calibrates.** Calibration is a separate button, writes
   exactly one week — the next one — and refuses when that week is confirmed. An
   actual that silently rewrote next week's plan is the behaviour the build list
   singles out to avoid, and it is one line away from happening by accident.

WEEK BUCKETS
------------
Week 1 = days 1-7, week 2 = 8-14, week 3 = 15-21, week 4 = 22 to the end of the month.
No holiday calendar, no ISO weeks, and deliberately no fifth bucket: a 31-day month's
last ten days are all week 4. That is a planning convention, not a calendar fact, and
it is the one the build list specifies.
"""
import datetime

import db

WEEK_STATUSES = ("derived", "edited", "confirmed")
WEEKS_PER_MONTH = 4
FLAG_FIELDS = ("weather", "wetness", "traffic", "other")

# Columns a caller may read back. Kept as a list rather than SELECT * so a new column
# added to the table does not silently start appearing in an API response.
_COLS = ("route_id, month_index, discipline, section_id, week_index, planned_qty, "
         "unit, status, weather, wetness, traffic, other, confirmed_by, confirmed_at, "
         "actual_qty, actual_note, actual_by, actual_at, parent_qty")


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


# --------------------------------------------------------------------------- #
#  Week arithmetic                                                             #
# --------------------------------------------------------------------------- #
def week_of_day(day):
    """
    Which bucket a day-of-month falls in. 1-7, 8-14, 15-21, 22-end.

    Clamped at 4 rather than allowed to reach 5: day 29 would otherwise be week 5 and
    there is no week 5 row to write to. The clamp is the definition, not a guard.
    """
    d = int(day)
    if d < 1:
        return 1
    return min(WEEKS_PER_MONTH, (d - 1) // 7 + 1)


def next_week(month_index, week_index):
    """
    The week after this one, as (month_index, week_index).

    Past week 4, the next week is week 1 of the NEXT month — which is why calibration
    can reach across a month boundary and why it has to look the parent line up again
    when it does.
    """
    if int(week_index) >= WEEKS_PER_MONTH:
        return int(month_index) + 1, 1
    return int(month_index), int(week_index) + 1


def month_index_of(date, start_year):
    """Absolute month_index for a date. 1 = January of start_year."""
    return (date.year - int(start_year)) * 12 + date.month


def editable_week(start_year, today=None):
    """
    The week the look-ahead lets you edit and confirm — "next week" in the build
    list's wording: *the week bucket that contains today, inside the current month.*

    ⚠️ That wording is worth keeping verbatim, because it is not what "next week"
    normally means. The editable bucket is the one TODAY is in, not the one after it.
    The build list's fallback — "if today is past week 4 of this month, next week =
    week 1 of next month" — cannot be reached while week 4 runs to the end of the
    month, so it is implemented defensively and never fires today.

    Returns (month_index, week_index), or (None, None) if the date sits outside the
    forecast horizon, which callers must render as "outside the horizon" rather than
    silently editing month 1.
    """
    d = today or datetime.date.today()
    mi = month_index_of(d, start_year)
    wi = week_of_day(d.day)
    if wi > WEEKS_PER_MONTH:          # unreachable with the buckets above; kept honest
        mi, wi = mi + 1, 1
    return mi, wi


# --------------------------------------------------------------------------- #
#  Reads                                                                       #
# --------------------------------------------------------------------------- #
def _parent_lines(from_month, to_month, approved_only=True):
    """The forecast lines (and their monthly quantities) in a month window."""
    status_clause = " AND status = 'Approved'" if approved_only else ""
    return db.query(
        "SELECT route_id, month_index, discipline, section_id, quantity, unit, "
        "material_type, vehicle_type, submitted_by, status FROM forecasts "
        "WHERE tenant_id = ? AND month_index BETWEEN ? AND ?" + status_clause +
        " ORDER BY route_id, month_index, discipline, section_id",
        (db.current_tenant(), int(from_month), int(to_month)))


def _parent_of(route_id, month_index, discipline, section_id):
    rows = db.query(
        "SELECT quantity, unit, status, material_type, vehicle_type FROM forecasts "
        "WHERE tenant_id = ? AND route_id = ? AND month_index = ? AND discipline = ? "
        "AND section_id = ?",
        (db.current_tenant(), route_id, int(month_index), discipline or "",
         section_id or ""))
    return rows[0] if rows else None


def _decorate(row, parent):
    """Add the two derived fields no column holds: variance and parent_changed."""
    planned, actual = row.get("planned_qty"), row.get("actual_qty")
    # ⭐ Blank until an actual is typed. 0.0 is a real actual (nothing moved) and must
    # produce a variance; None is "not reported yet" and must not.
    row["variance"] = (float(planned or 0) - float(actual)) if actual is not None else None
    pq = row.get("parent_qty")
    row["parent_qty_now"] = parent["quantity"] if parent else None
    row["parent_changed"] = bool(
        parent is not None and pq is not None
        and abs(float(pq) - float(parent["quantity"])) > 1e-9)
    return row


def get_week(route_id, month_index, discipline, section_id, week_index):
    rows = db.query(
        f"SELECT {_COLS} FROM forecast_weeks WHERE tenant_id = ? AND route_id = ? "
        "AND month_index = ? AND discipline = ? AND section_id = ? AND week_index = ?",
        (db.current_tenant(), route_id, int(month_index), discipline or "",
         section_id or "", int(week_index)))
    if not rows:
        return None
    return _decorate(rows[0],
                     _parent_of(route_id, month_index, discipline, section_id))


def list_weeks(from_month, to_month, route_id=None):
    """
    Every week row in a month window, materialising any that an already-Approved line
    is still missing.

    The lazy materialise is the second half of rule 1: a line approved before this
    shipped has no week rows and would otherwise show an empty Look-ahead forever.
    """
    lo, hi = min(int(from_month), int(to_month)), max(int(from_month), int(to_month))
    materialise_window(lo, hi)
    if route_id:
        rows = db.query(
            f"SELECT {_COLS} FROM forecast_weeks WHERE tenant_id = ? "
            "AND month_index BETWEEN ? AND ? AND route_id = ? "
            "ORDER BY route_id, month_index, discipline, section_id, week_index",
            (db.current_tenant(), lo, hi, route_id))
    else:
        rows = db.query(
            f"SELECT {_COLS} FROM forecast_weeks WHERE tenant_id = ? "
            "AND month_index BETWEEN ? AND ? "
            "ORDER BY route_id, month_index, discipline, section_id, week_index",
            (db.current_tenant(), lo, hi))
    parents = {(p["route_id"], p["month_index"], p["discipline"] or "",
                p["section_id"] or ""): p
               for p in _parent_lines(lo, hi, approved_only=False)}
    out = []
    for r in rows:
        key = (r["route_id"], r["month_index"], r["discipline"] or "",
               r["section_id"] or "")
        p = parents.get(key)
        r = _decorate(r, p)
        # the parent's own descriptive fields, so the Look-ahead table can label a row
        # without a second request per line
        r["material_type"] = p["material_type"] if p else None
        r["vehicle_type"] = p["vehicle_type"] if p else None
        r["parent_status"] = p["status"] if p else None
        out.append(r)
    return out


# --------------------------------------------------------------------------- #
#  Materialisation                                                             #
# --------------------------------------------------------------------------- #
def _insert_week(route_id, month_index, discipline, section_id, week_index,
                 planned_qty, unit, parent_qty):
    now = _now()
    db.execute(
        "INSERT INTO forecast_weeks (tenant_id, route_id, month_index, discipline, "
        "section_id, week_index, planned_qty, unit, status, parent_qty, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (db.current_tenant(), route_id, int(month_index), discipline or "",
         section_id or "", int(week_index), float(planned_qty), unit, "derived",
         float(parent_qty), now, now))


def materialise_line(route_id, discipline, section_id, month_index=None):
    """
    Create or refresh the four weeks of one forecast LINE.

    Called when a line is approved, and lazily on read. Only Approved months are
    materialised — a Pending month has nothing to plan against yet.

    ⚠️ Un-approving a line does NOT delete its weeks. A confirmed week with an actual
    typed against it is a record of what happened; dropping it because a planner
    reopened the month would destroy that. The rows simply stop being refreshed, and
    `parent_status` on the read tells the UI the parent is no longer approved.
    """
    created = refreshed = kept = 0
    rows = db.query(
        "SELECT route_id, month_index, discipline, section_id, quantity, unit "
        "FROM forecasts WHERE tenant_id = ? AND route_id = ? AND discipline = ? "
        "AND section_id = ? AND status = 'Approved'",
        (db.current_tenant(), route_id, discipline or "", section_id or ""))
    if month_index is not None:
        rows = [r for r in rows if r["month_index"] == int(month_index)]

    for p in rows:
        share = float(p["quantity"] or 0) / WEEKS_PER_MONTH
        have = {r["week_index"]: r for r in db.query(
            "SELECT week_index, status FROM forecast_weeks WHERE tenant_id = ? "
            "AND route_id = ? AND month_index = ? AND discipline = ? "
            "AND section_id = ?",
            (db.current_tenant(), p["route_id"], p["month_index"],
             p["discipline"] or "", p["section_id"] or ""))}
        for w in range(1, WEEKS_PER_MONTH + 1):
            cur = have.get(w)
            if cur is None:
                _insert_week(p["route_id"], p["month_index"], p["discipline"],
                             p["section_id"], w, share, p["unit"], p["quantity"])
                created += 1
            elif cur["status"] == "derived":
                # ⭐ parent_qty is re-stamped HERE and nowhere else in this branch:
                # a derived row is by definition in step with its parent again.
                db.execute(
                    "UPDATE forecast_weeks SET planned_qty = ?, unit = ?, "
                    "parent_qty = ?, updated_at = ? WHERE tenant_id = ? "
                    "AND route_id = ? AND month_index = ? AND discipline = ? "
                    "AND section_id = ? AND week_index = ?",
                    (share, p["unit"], float(p["quantity"] or 0), _now(),
                     db.current_tenant(), p["route_id"], p["month_index"],
                     p["discipline"] or "", p["section_id"] or "", w))
                refreshed += 1
            else:
                # ⭐ edited / confirmed: left EXACTLY as it is, parent_qty included.
                # Not re-stamping it is what makes parent_changed fire on the read.
                kept += 1
    return {"created": created, "refreshed": refreshed, "kept": kept}


def materialise_window(from_month, to_month):
    """Materialise every Approved line in a month window. Idempotent."""
    seen, out = set(), {"created": 0, "refreshed": 0, "kept": 0}
    for p in _parent_lines(from_month, to_month):
        key = (p["route_id"], p["discipline"] or "", p["section_id"] or "",
               p["month_index"])
        if key in seen:
            continue
        seen.add(key)
        r = materialise_line(p["route_id"], p["discipline"], p["section_id"],
                             month_index=p["month_index"])
        for k in out:
            out[k] += r[k]
    return out


# --------------------------------------------------------------------------- #
#  Writes                                                                      #
# --------------------------------------------------------------------------- #
def _flags_from(flags):
    """Only the four named flag fields, each a string. Empty string is allowed."""
    f = flags or {}
    return {k: ("" if f.get(k) is None else str(f.get(k))) for k in FLAG_FIELDS
            if k in f}


def set_week(route_id, month_index, discipline, section_id, week_index,
             planned_qty=None, flags=None, by=None):
    """
    Edit one week's planned quantity and/or its flags. Moves the row to `edited`.

    parent_qty is re-stamped, which CLEARS any 'parent month changed' flag: somebody
    has now set this number with the current parent in view.
    """
    cur = get_week(route_id, month_index, discipline, section_id, week_index)
    if not cur:
        return {"error": "no such week — the parent month is not approved"}
    if cur["status"] == "confirmed":
        return {"error": f"week {week_index} is confirmed and cannot be edited"}

    parent = _parent_of(route_id, month_index, discipline, section_id)
    qty = cur["planned_qty"] if planned_qty is None else float(planned_qty)
    f = _flags_from(flags)
    db.execute(
        "UPDATE forecast_weeks SET planned_qty = ?, status = ?, parent_qty = ?, "
        "weather = ?, wetness = ?, traffic = ?, other = ?, updated_at = ? "
        "WHERE tenant_id = ? AND route_id = ? AND month_index = ? "
        "AND discipline = ? AND section_id = ? AND week_index = ?",
        (qty, "edited",
         float(parent["quantity"]) if parent else cur.get("parent_qty"),
         f.get("weather", cur.get("weather")), f.get("wetness", cur.get("wetness")),
         f.get("traffic", cur.get("traffic")), f.get("other", cur.get("other")),
         _now(), db.current_tenant(), route_id, int(month_index), discipline or "",
         section_id or "", int(week_index)))
    return {"week": get_week(route_id, month_index, discipline, section_id, week_index)}


def confirm_week(route_id, month_index, discipline, section_id, week_index,
                 by=None, flags=None):
    """
    Confirm one week. Separate from editing on purpose — the build list asks for a
    distinct button, because "I have adjusted the number" and "I am committing to it"
    are different acts and only the second one blocks calibration into this week.

    The four flags are free text and every one of them may be empty. No scores, no
    weather API, no colour used as logic — they are notes.
    """
    cur = get_week(route_id, month_index, discipline, section_id, week_index)
    if not cur:
        return {"error": "no such week — the parent month is not approved"}
    parent = _parent_of(route_id, month_index, discipline, section_id)
    f = _flags_from(flags)
    db.execute(
        "UPDATE forecast_weeks SET status = ?, confirmed_by = ?, confirmed_at = ?, "
        "parent_qty = ?, weather = ?, wetness = ?, traffic = ?, other = ?, "
        "updated_at = ? WHERE tenant_id = ? AND route_id = ? AND month_index = ? "
        "AND discipline = ? AND section_id = ? AND week_index = ?",
        ("confirmed", by, _now(),
         float(parent["quantity"]) if parent else cur.get("parent_qty"),
         f.get("weather", cur.get("weather")), f.get("wetness", cur.get("wetness")),
         f.get("traffic", cur.get("traffic")), f.get("other", cur.get("other")),
         _now(), db.current_tenant(), route_id, int(month_index), discipline or "",
         section_id or "", int(week_index)))
    return {"week": get_week(route_id, month_index, discipline, section_id, week_index)}


def set_actual(route_id, month_index, discipline, section_id, week_index,
               actual_qty=None, actual_note=None, by=None):
    """
    Type what actually moved in one week.

    ⭐ THIS DOES NOT CALIBRATE, AND MUST NOT. Variance appears; next week's plan does
    not move until somebody presses the button. See calibrate().

    Writing an actual does not change `status` either: a week can be `derived` and
    still have an actual against it, which is the normal case for a week nobody needed
    to adjust.
    """
    cur = get_week(route_id, month_index, discipline, section_id, week_index)
    if not cur:
        return {"error": "no such week — the parent month is not approved"}
    q = None if actual_qty is None or actual_qty == "" else float(actual_qty)
    db.execute(
        "UPDATE forecast_weeks SET actual_qty = ?, actual_note = ?, actual_by = ?, "
        "actual_at = ?, updated_at = ? WHERE tenant_id = ? AND route_id = ? "
        "AND month_index = ? AND discipline = ? AND section_id = ? AND week_index = ?",
        (q, actual_note, by, _now(), _now(), db.current_tenant(), route_id,
         int(month_index), discipline or "", section_id or "", int(week_index)))
    return {"week": get_week(route_id, month_index, discipline, section_id, week_index)}


def calibrate(route_id, month_index, discipline, section_id, week_index,
              override_qty=None, by=None):
    """
    Carry this week's variance into NEXT week, and nowhere else.

        next.planned_qty = next.planned_qty + (this.planned_qty - this.actual_qty)

    or, with override_qty, next.planned_qty = override_qty — a replacement, not an
    addition, because the dialog offers the typed figure INSTEAD of the formula.

    ⭐ Exactly one row is written: the next week. Never this week, never week+2, never
    the parent month. A calibration that walked the rest of the month would turn one
    wet Tuesday into a rewritten quarter.

    Refuses when next week is already confirmed. Somebody has committed to that number
    and a button press must not quietly move it.
    """
    this = get_week(route_id, month_index, discipline, section_id, week_index)
    if not this:
        return {"error": "no such week — the parent month is not approved"}

    nm, nw = next_week(month_index, week_index)
    # crossing a month boundary reaches a different parent line, which may not have
    # been materialised yet
    materialise_line(route_id, discipline, section_id, month_index=nm)
    nxt = get_week(route_id, nm, discipline, section_id, nw)
    if not nxt:
        return {"error": f"next week is month {nm} week {nw}, which has no row — "
                         f"month {nm} is not approved for this line"}
    if nxt["status"] == "confirmed":
        return {"error": f"next week (month {nm}, week {nw}) is already confirmed — "
                         "reopen it before calibrating into it",
                "blocked_by": "confirmed"}

    if override_qty is not None and override_qty != "":
        new_qty = float(override_qty)
        basis = "override"
    else:
        if this.get("actual_qty") is None:
            return {"error": "no actual typed for this week, so there is no variance "
                             "to apply — type an actual or use the override"}
        variance = float(this.get("planned_qty") or 0) - float(this["actual_qty"])
        new_qty = float(nxt.get("planned_qty") or 0) + variance
        basis = "variance"

    parent = _parent_of(route_id, nm, discipline, section_id)
    db.execute(
        "UPDATE forecast_weeks SET planned_qty = ?, status = ?, parent_qty = ?, "
        "updated_at = ? WHERE tenant_id = ? AND route_id = ? AND month_index = ? "
        "AND discipline = ? AND section_id = ? AND week_index = ?",
        (new_qty, "edited",
         float(parent["quantity"]) if parent else nxt.get("parent_qty"),
         _now(), db.current_tenant(), route_id, int(nm), discipline or "",
         section_id or "", int(nw)))
    return {
        "from": {"month_index": int(month_index), "week_index": int(week_index)},
        "to": {"month_index": int(nm), "week_index": int(nw)},
        "basis": basis,
        "week": get_week(route_id, nm, discipline, section_id, nw),
    }


def summary():
    n = db.query("SELECT COUNT(*) AS n FROM forecast_weeks WHERE tenant_id = ?",
                 (db.current_tenant(),))[0]["n"]
    by_status = db.query(
        "SELECT status, COUNT(*) AS n FROM forecast_weeks WHERE tenant_id = ? "
        "GROUP BY status", (db.current_tenant(),))
    return {"weeks": n, "by_status": {r["status"]: r["n"] for r in by_status}}

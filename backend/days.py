"""
Look-ahead v2, slice 1 (2026-09-09) — the COMMIT week, day by day.

WHAT THIS IS
------------
A day layer that sits ON a forecast_weeks row, exactly as forecast_weeks sits on a
forecasts line. It does not widen `forecasts`, it does not touch forecast_weeks' key, and
the public map's monthly aggregation is untouched. A `forecast_days` row is keyed by the
week's own line key plus a calendar date, so a day can only exist for a line that exists.

⚠️ Tasks C / D / D2 are NOT rebuilt. weeks.py is imported and called; nothing in it is
copied or replaced. Where this module has to act on a week (confirming it, calibrating
into it) it wraps the weeks.py function and adds the day half after it.

THE RULES THAT MATTER
---------------------
1. **Days exist ONLY for the commit week, and ONLY for Approved lines.** The commit week
   is the server's `weeks.editable_week()` — the bucket that contains today. Weeks 2-4
   of the horizon never get days. A Pending month never gets days. Nothing here creates
   a day outside that bucket, and the read path materialises nothing else either.

2. **Mon-Fri carry the plan; Sat and Sun start at 0.** L1, locked. The week's planned
   quantity is split evenly across the WEEKDAYS in the bucket. Week 4 of a 31-day month
   is ten days long and is still weekday-weighted. No public-holiday calendar, same as
   weeks.py.

3. **`derived` refreshes, `edited` and `confirmed` do not.** Same three statuses as the
   week, same reason. A derived day follows its week's planned_qty; an edited day holds
   what somebody typed. `parent_week_qty` is the week's planned_qty AS AT the last write
   of the day row, so "week changed" is an exact equality test — the argument weeks.py
   makes for `parent_qty`, one level down.

4. **The sum rule is a FLAG, never a block.** sum(day planned) should equal the week's
   planned_qty. When it does not, the read says `days_ne_week` and the export prints
   both numbers. Confirm is still allowed. Nobody is refused a commitment because
   arithmetic drifted after an edit.

5. **Confirm is whole-week. One act.** L6, locked. `confirm_week()` here calls
   weeks.confirm_week() and then stamps every day in the bucket `confirmed`. There is no
   per-day confirm and this module does not add one.

6. **A day actual never overwrites a TYPED week actual.** Two ways a week actual can come
   to exist: a clerk types it (`actual_source = 'typed'`), or it is the running sum of
   the day actuals (`actual_source = 'days'`). Day actuals maintain the second and never
   touch the first. Without the source column the first partial day sum would have been
   indistinguishable from a typed figure and would have frozen the week there — so the
   column is not optional, it is what makes rule 6 implementable.

7. **Saving an actual still never calibrates.** Rule 4 of weeks.py, inherited unchanged.
   The only new thing calibrate gains is an OPT-IN spread of the applied delta across
   the commit week's remaining weekdays, default off, and it writes the days of the
   target week only.

WHAT IS NOT DONE (deliberately, per the brief)
----------------------------------------------
Per-day confirm. Daily stock consumption. A file body or CSV import. Days for weeks 2-4.
A week scrubber on /map/. Any change to the `forecasts` UNIQUE key.
"""
import calendar
import datetime

import db
import weeks

DAY_STATUSES = weeks.WEEK_STATUSES          # derived | edited | confirmed — same three
WEEKDAY_MAX = 4                             # Monday=0 .. Friday=4

#: Set by main.py at import, exactly as stockpiles.START_YEAR is. Tests set it too.
START_YEAR = 2026

_COLS = ("route_id, month_index, discipline, section_id, day_date, planned_qty, "
         "actual_qty, actual_note, actual_by, actual_at, status, parent_week_index, "
         "parent_week_qty, created_at, updated_at")


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _iso(d):
    return d.isoformat() if isinstance(d, datetime.date) else str(d)


def _date(s):
    return s if isinstance(s, datetime.date) else datetime.date.fromisoformat(str(s)[:10])


# --------------------------------------------------------------------------- #
#  Bucket arithmetic                                                           #
# --------------------------------------------------------------------------- #
def month_of(month_index, start_year=None):
    """(year, month) for an absolute month_index. 1 = January of start_year."""
    sy = int(start_year if start_year is not None else START_YEAR)
    mi = int(month_index) - 1
    return sy + mi // 12, mi % 12 + 1


def bucket_dates(month_index, week_index, start_year=None):
    """
    Every calendar date in one week bucket.

    Weeks 1-3 are seven days. Week 4 runs from the 22nd to the END of the month — eight,
    nine or ten days — because that is what weeks.week_of_day() says the bucket is, and
    the day layer must agree with the week layer about which days a week owns.
    """
    y, m = month_of(month_index, start_year)
    last = calendar.monthrange(y, m)[1]
    w = int(week_index)
    first_day = (w - 1) * 7 + 1
    last_day = last if w >= weeks.WEEKS_PER_MONTH else min(w * 7, last)
    return [datetime.date(y, m, d) for d in range(first_day, last_day + 1)]


def weekdays_in(dates):
    return [d for d in dates if d.weekday() <= WEEKDAY_MAX]


def commit_bucket(start_year=None, today=None):
    """(month_index, week_index) of the commit week — the bucket containing today."""
    sy = int(start_year if start_year is not None else START_YEAR)
    return weeks.editable_week(sy, today=today)


#: Which buckets may carry days. 'commit' is the bucket containing today (the brief's
#: rule). 'next' is the bucket after it — added 2026-09-09 for the Thursday process:
#: "we may confirm next week's look-ahead on a Thursday for next week's deliveries."
#: Days for 'next' are materialised only when a caller asks for that bucket, so the
#: default read is exactly the brief's.
DAY_BUCKETS = ("commit", "next")


def bucket_of(name, start_year=None, today=None):
    """(month_index, week_index) for a DAY_BUCKETS name. Unknown names read as 'commit'."""
    mi, wi = commit_bucket(start_year, today)
    if mi is None:
        return None, None
    if name == "next":
        return weeks.next_week(mi, wi)
    return mi, wi


def day_buckets(start_year=None, today=None):
    """Every (month_index, week_index) that may carry days, commit first."""
    mi, wi = commit_bucket(start_year, today)
    if mi is None:
        return []
    return [(mi, wi), weeks.next_week(mi, wi)]


def split_week(planned_qty, dates):
    """
    Rule 2 as a function: {date: planned} with the week's quantity spread evenly over the
    weekdays and 0 on every weekend day. A bucket is never shorter than seven days, so
    there are always weekdays to carry it.
    """
    wd = weekdays_in(dates)
    share = (float(planned_qty or 0) / len(wd)) if wd else 0.0
    return {d: (share if d.weekday() <= WEEKDAY_MAX else 0.0) for d in dates}


# --------------------------------------------------------------------------- #
#  Reads                                                                       #
# --------------------------------------------------------------------------- #
def _week_row(route_id, month_index, discipline, section_id, week_index):
    return weeks.get_week(route_id, month_index, discipline, section_id, week_index)


def _days_of(route_id, month_index, discipline, section_id, week_index):
    """Every day row of one (line, week), oldest first."""
    dates = [_iso(d) for d in bucket_dates(month_index, week_index)]
    if not dates:
        return []
    marks = ",".join("?" * len(dates))
    return db.query(
        f"SELECT {_COLS} FROM forecast_days WHERE tenant_id = ? AND route_id = ? "
        f"AND month_index = ? AND discipline = ? AND section_id = ? "
        f"AND day_date IN ({marks}) ORDER BY day_date",
        (db.current_tenant(), route_id, int(month_index), discipline or "",
         section_id or "", *dates))


def _decorate_day(row, week):
    """The derived fields no column holds: variance, and whether the week moved under it."""
    planned, actual = row.get("planned_qty"), row.get("actual_qty")
    # blank until an actual is typed — 0.0 is a real actual, None is "not reported"
    row["variance"] = (float(planned or 0) - float(actual)) if actual is not None else None
    wq = float(week.get("planned_qty") or 0) if week else None
    pq = row.get("parent_week_qty")
    row["week_planned_now"] = wq
    row["week_changed"] = bool(
        week is not None and pq is not None and abs(float(pq) - wq) > 1e-9)
    return row


def list_days(from_date=None, to_date=None, route_id=None, start_year=None, today=None,
              bucket="commit"):
    """
    The commit week, day by day, grouped by forecast line.

    Materialises first (rule 1's lazy half), then returns one entry per line that has a
    week row in the commit bucket:

        {line key…, ipt, unit, material_type, vehicle_type,
         week: {…the forecast_weeks row…},
         days: [{…day row…, variance, week_changed}],
         days_sum, days_ne_week}

    `from_date` / `to_date` clip the days returned; the default is the whole bucket. They
    do not widen it — there are no days outside the commit week to return.
    """
    cmi, cwi = commit_bucket(start_year, today)
    mi, wi = bucket_of(bucket, start_year, today)
    if mi is None:
        return {"commit_week": None, "bucket": bucket, "lines": []}
    materialise_commit_week(start_year=start_year, today=today, bucket=bucket)
    dates = bucket_dates(mi, wi, start_year)
    lo = _date(from_date) if from_date else dates[0]
    hi = _date(to_date) if to_date else dates[-1]

    week_rows = weeks.list_weeks(mi, mi, route_id=route_id)
    out = []
    for w in week_rows:
        if int(w["week_index"]) != int(wi):
            continue
        if w.get("parent_status") != "Approved":
            continue
        drows = _days_of(w["route_id"], w["month_index"], w["discipline"],
                         w["section_id"], wi)
        days = [_decorate_day(d, w) for d in drows if lo <= _date(d["day_date"]) <= hi]
        total = sum(float(d["planned_qty"] or 0) for d in drows)
        out.append({
            "route_id": w["route_id"], "month_index": w["month_index"],
            "discipline": w["discipline"], "section_id": w["section_id"],
            "week_index": int(wi),
            "ipt": w.get("ipt"), "unit": w.get("unit"),
            "material_type": w.get("material_type"), "vehicle_type": w.get("vehicle_type"),
            "week": w, "days": days,
            "days_sum": round(total, 6),
            "days_ne_week": abs(total - float(w.get("planned_qty") or 0)) > 1e-6,
        })
    return {"commit_week": {"month_index": mi, "week_index": wi,
                            "from": _iso(dates[0]), "to": _iso(dates[-1]),
                            "weekdays": len(weekdays_in(dates)), "days": len(dates)},
            # which bucket this is, and where today's bucket sits, so a UI switched to
            # 'next' can still say which week contains today
            "bucket": ("next" if bucket == "next" else "commit"),
            "today_week": {"month_index": cmi, "week_index": cwi},
            "lines": out}


# --------------------------------------------------------------------------- #
#  Materialisation                                                             #
# --------------------------------------------------------------------------- #
def _insert_day(route_id, month_index, discipline, section_id, day, planned, week_index,
                week_qty):
    now = _now()
    db.execute(
        "INSERT INTO forecast_days (tenant_id, route_id, month_index, discipline, "
        "section_id, day_date, planned_qty, status, parent_week_index, parent_week_qty, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (db.current_tenant(), route_id, int(month_index), discipline or "",
         section_id or "", _iso(day), float(planned), "derived", int(week_index),
         float(week_qty or 0), now, now))


def materialise_week_days(route_id, month_index, discipline, section_id, week_index):
    """
    Create or refresh the days of ONE (line, week). Only called for the commit bucket.

    A `derived` day is rewritten to its share of the week's CURRENT planned_qty and its
    stamp refreshed. An `edited` or `confirmed` day is left exactly as it is, stamp
    included — not re-stamping it is what makes `week_changed` fire on the read.
    """
    w = _week_row(route_id, month_index, discipline, section_id, week_index)
    if not w:
        return {"created": 0, "refreshed": 0, "kept": 0}
    dates = bucket_dates(month_index, week_index)
    shares = split_week(w.get("planned_qty"), dates)
    have = {r["day_date"]: r for r in _days_of(route_id, month_index, discipline,
                                                section_id, week_index)}
    created = refreshed = kept = 0
    wq = float(w.get("planned_qty") or 0)
    for d in dates:
        cur = have.get(_iso(d))
        if cur is None:
            _insert_day(route_id, month_index, discipline, section_id, d, shares[d],
                        week_index, wq)
            created += 1
        elif cur["status"] == "derived":
            # 09 Sep night: write ONLY when the figure moved. On Render every statement
            # is a network round trip, and a read that rewrote every derived row it
            # had just read cost 45 writes per page for nothing. Same figures ⇒ kept.
            if (abs(float(cur.get("planned_qty") or 0) - float(shares[d])) < 1e-9
                    and cur.get("parent_week_qty") is not None
                    and abs(float(cur["parent_week_qty"]) - wq) < 1e-9):
                kept += 1
                continue
            db.execute(
                "UPDATE forecast_days SET planned_qty = ?, parent_week_qty = ?, "
                "updated_at = ? WHERE tenant_id = ? AND route_id = ? AND month_index = ? "
                "AND discipline = ? AND section_id = ? AND day_date = ?",
                (shares[d], wq, _now(), db.current_tenant(), route_id, int(month_index),
                 discipline or "", section_id or "", _iso(d)))
            refreshed += 1
        else:
            kept += 1
    return {"created": created, "refreshed": refreshed, "kept": kept}


def materialise_commit_week(start_year=None, today=None, bucket="commit"):
    """
    Days for every Approved line's week row in ONE bucket — the commit bucket by
    default, or the one after it when asked (`bucket="next"`). Idempotent.

    Nothing outside that bucket is touched, and a line whose parent month is not Approved
    has no week row and therefore gets no days — the Pending case falls out of rule 1 of
    weeks.py rather than needing its own check here.
    """
    mi, wi = bucket_of(bucket, start_year, today)
    out = {"commit_week": {"month_index": mi, "week_index": wi},
           "created": 0, "refreshed": 0, "kept": 0}
    if mi is None:
        return out
    for w in weeks.list_weeks(mi, mi):
        if int(w["week_index"]) != int(wi) or w.get("parent_status") != "Approved":
            continue
        r = materialise_week_days(w["route_id"], w["month_index"], w["discipline"],
                                  w["section_id"], wi)
        for k in ("created", "refreshed", "kept"):
            out[k] += r[k]
    return out


# --------------------------------------------------------------------------- #
#  Writes                                                                      #
# --------------------------------------------------------------------------- #
def _in_commit_bucket(month_index, day, start_year=None, today=None):
    """
    (ok, month_index, week_index) — is this day in a bucket that may carry days, and
    which one. Checks the commit bucket first, then the next one (the Thursday case).
    When neither matches, the commit bucket is returned so the error can name it.
    """
    buckets = day_buckets(start_year, today)
    if not buckets:
        return False, None, None
    d = _date(day)
    for mi, wi in buckets:
        if int(month_index) == int(mi) and d in set(bucket_dates(mi, wi, start_year)):
            return True, mi, wi
    return False, buckets[0][0], buckets[0][1]


def set_day(route_id, month_index, discipline, section_id, day_date, planned_qty,
            by=None, start_year=None, today=None):
    """
    Type one day's planned quantity.

    The day goes to `edited`. If its week was still `derived`, the week goes to `edited`
    too — a planner has now touched this week's plan, and the ÷4 refresh must stop
    overwriting it. A confirmed week refuses: after confirm, planned cells are read-only
    (L6). Sat/Sun are typeable — the 0 is a default, not a rule.
    """
    ok, mi, wi = _in_commit_bucket(month_index, day_date, start_year, today)
    if not ok:
        return {"error": "that day is not in the commit week or the week after it — "
                         "only those two buckets have editable days",
                "commit_week": {"month_index": mi, "week_index": wi}}
    w = _week_row(route_id, month_index, discipline, section_id, wi)
    if not w:
        return {"error": "no such week — the parent month is not approved"}
    if w["status"] == "confirmed":
        return {"error": "this week is confirmed — its planned days are read-only",
                "blocked_by": "confirmed"}
    materialise_week_days(route_id, month_index, discipline, section_id, wi)
    q = float(planned_qty)
    db.execute(
        "UPDATE forecast_days SET planned_qty = ?, status = ?, parent_week_qty = ?, "
        "updated_at = ? WHERE tenant_id = ? AND route_id = ? AND month_index = ? "
        "AND discipline = ? AND section_id = ? AND day_date = ?",
        (q, "edited", float(w.get("planned_qty") or 0), _now(), db.current_tenant(),
         route_id, int(month_index), discipline or "", section_id or "", _iso(_date(day_date))))
    if w["status"] == "derived":
        # planned_qty=None keeps the week's number and only moves its status
        weeks.set_week(route_id, month_index, discipline, section_id, wi,
                       planned_qty=None, by=by)
    return {"line": _line_view(route_id, month_index, discipline, section_id, wi)}


def _line_view(route_id, month_index, discipline, section_id, week_index):
    w = _week_row(route_id, month_index, discipline, section_id, week_index)
    drows = _days_of(route_id, month_index, discipline, section_id, week_index)
    days = [_decorate_day(d, w) for d in drows]
    total = sum(float(d["planned_qty"] or 0) for d in drows)
    return {"week": w, "days": days, "days_sum": round(total, 6),
            "days_ne_week": bool(w) and abs(total - float(w.get("planned_qty") or 0)) > 1e-6}


def _sync_week_actual_from_days(route_id, month_index, discipline, section_id, week_index):
    """
    Rule 6. The week actual becomes the sum of the day actuals ONLY when it is empty or
    was itself produced by this function. A typed week actual is never touched.
    """
    w = _week_row(route_id, month_index, discipline, section_id, week_index)
    if not w:
        return None
    src = db.query(
        "SELECT actual_source FROM forecast_weeks WHERE tenant_id = ? AND route_id = ? "
        "AND month_index = ? AND discipline = ? AND section_id = ? AND week_index = ?",
        (db.current_tenant(), route_id, int(month_index), discipline or "",
         section_id or "", int(week_index)))
    source = (src[0].get("actual_source") if src else None)
    if w.get("actual_qty") is not None and source == "typed":
        return "kept_typed"
    typed = [d["actual_qty"] for d in _days_of(route_id, month_index, discipline,
                                                section_id, week_index)
             if d.get("actual_qty") is not None]
    total = float(sum(float(x) for x in typed)) if typed else None
    db.execute(
        "UPDATE forecast_weeks SET actual_qty = ?, actual_source = ?, updated_at = ? "
        "WHERE tenant_id = ? AND route_id = ? AND month_index = ? AND discipline = ? "
        "AND section_id = ? AND week_index = ?",
        (total, ("days" if typed else None), _now(), db.current_tenant(), route_id,
         int(month_index), discipline or "", section_id or "", int(week_index)))
    return "summed" if typed else "cleared"


def set_day_actual(route_id, month_index, discipline, section_id, day_date,
                   actual_qty=None, actual_note=None, by=None, start_year=None, today=None):
    """
    Type what actually moved on one day. Allowed on a confirmed week — actuals are the
    record of what happened, and confirming the plan does not close the ledger.

    ⭐ Does not calibrate. Does not change the day's status. Maintains the week actual
    per rule 6 and nothing else.
    """
    ok, mi, wi = _in_commit_bucket(month_index, day_date, start_year, today)
    if not ok:
        return {"error": "that day is not in the commit week",
                "commit_week": {"month_index": mi, "week_index": wi}}
    w = _week_row(route_id, month_index, discipline, section_id, wi)
    if not w:
        return {"error": "no such week — the parent month is not approved"}
    materialise_week_days(route_id, month_index, discipline, section_id, wi)
    q = None if actual_qty is None or actual_qty == "" else float(actual_qty)
    db.execute(
        "UPDATE forecast_days SET actual_qty = ?, actual_note = ?, actual_by = ?, "
        "actual_at = ?, updated_at = ? WHERE tenant_id = ? AND route_id = ? "
        "AND month_index = ? AND discipline = ? AND section_id = ? AND day_date = ?",
        (q, actual_note, by, _now(), _now(), db.current_tenant(), route_id,
         int(month_index), discipline or "", section_id or "", _iso(_date(day_date))))
    sync = _sync_week_actual_from_days(route_id, month_index, discipline, section_id, wi)
    view = _line_view(route_id, month_index, discipline, section_id, wi)
    view["week_actual"] = sync
    return {"line": view}


def confirm_week(route_id, month_index, discipline, section_id, week_index,
                 by=None, flags=None, start_year=None, today=None):
    """
    L6: confirm the WEEK, then stamp every day in its bucket `confirmed`.

    Wraps weeks.confirm_week() — Task C's confirm is not rebuilt, it is called. If this
    is the commit week and its days do not exist yet, they are materialised first, so
    "create on first open of the Commit view, or when that week is confirmed — whichever
    comes first" holds for the second case too. A week outside the commit bucket has no
    days and confirms exactly as it did before this module existed.
    """
    res = weeks.confirm_week(route_id, month_index, discipline, section_id, week_index,
                             by=by, flags=flags)
    if res.get("error"):
        return res
    stamped = 0
    if (int(month_index), int(week_index)) in [(int(a), int(b)) for a, b in day_buckets(start_year, today)]:
        materialise_week_days(route_id, month_index, discipline, section_id, week_index)
        dates = [_iso(d) for d in bucket_dates(month_index, week_index, start_year)]
        marks = ",".join("?" * len(dates))
        db.execute(
            f"UPDATE forecast_days SET status = 'confirmed', updated_at = ? "
            f"WHERE tenant_id = ? AND route_id = ? AND month_index = ? AND discipline = ? "
            f"AND section_id = ? AND day_date IN ({marks})",
            (_now(), db.current_tenant(), route_id, int(month_index), discipline or "",
             section_id or "", *dates))
        stamped = len(dates)
    res["days_confirmed"] = stamped
    return res


def calibrate(route_id, month_index, discipline, section_id, week_index,
              override_qty=None, by=None, spread=False, spread_from=None,
              start_year=None, today=None):
    """
    weeks.calibrate(), plus the opt-in spread. Default off.

    The week half is unchanged: exactly one week is written — the next one — and a
    confirmed next week refuses. The day half runs only when `spread` is true AND the
    target week is the commit bucket (the only week with days): the delta that was just
    applied to the target week is divided evenly over that week's WEEKDAYS on or after
    `spread_from` (today, unless given) and added to each.

    🔴 THE ORDER OF OPERATIONS IS THE WHOLE FUNCTION. A `derived` day tracks week/n, so if
    the days were refreshed AFTER the week moved they would already carry the new total
    evenly — and adding the delta again would count it twice. Found by the first test
    run. So: the days are materialised against the week AS IT WAS, the week is then
    calibrated, the delta goes onto the remaining weekdays, and EVERY day in the bucket
    is stamped `edited` — because a planner has just chosen a non-uniform distribution,
    and the only thing that keeps a derived day from re-uniforming it on the next read
    is not being derived any more.

    ⚠️ Two readings of "remaining" exist — the brief says *remaining commit-week days*,
    the Account mock's footer says *Mon-Fri of the commit week (36 t/day)*, which on a
    Monday is the same thing and on a Thursday is not. `spread_from` is a parameter so
    the caller decides and a test can pin it; the default follows the brief's word.
    Weeks after next are never touched.
    """
    nm, nw = weeks.next_week(month_index, week_index)
    target_is_commit = (int(nm), int(nw)) in [(int(a), int(b)) for a, b in day_buckets(start_year, today)]
    before = _week_row(route_id, nm, discipline, section_id, nw)
    before_qty = float(before.get("planned_qty") or 0) if before else None

    if spread and target_is_commit and before:
        # freeze the as-was distribution BEFORE the week moves — see the docstring
        materialise_week_days(route_id, nm, discipline, section_id, nw)

    res = weeks.calibrate(route_id, month_index, discipline, section_id, week_index,
                          override_qty=override_qty, by=by)
    if res.get("error") or not spread:
        res["spread"] = None
        return res

    if not target_is_commit:
        res["spread"] = {"applied": False,
                         "note": "the calibrated week is neither the commit week nor the "
                                 "one after it, so it has no days to spread across — the "
                                 "week total was written"}
        return res

    after = _week_row(route_id, nm, discipline, section_id, nw)
    wq = float(after.get("planned_qty") or 0)
    delta = wq - (before_qty or 0.0)
    start = _date(spread_from) if spread_from else (today or datetime.date.today())
    all_dates = bucket_dates(nm, nw, start_year)
    dates = [d for d in weekdays_in(all_dates) if d >= start]
    if not dates:
        res["spread"] = {"applied": False, "delta": round(delta, 6),
                         "note": "no weekdays remain in the commit week on or after "
                                 f"{_iso(start)} — the week total was written, no day moved"}
        return res
    per_day = delta / len(dates)
    for d in dates:
        db.execute(
            "UPDATE forecast_days SET planned_qty = planned_qty + ?, updated_at = ? "
            "WHERE tenant_id = ? AND route_id = ? AND month_index = ? AND discipline = ? "
            "AND section_id = ? AND day_date = ? AND status <> 'confirmed'",
            (per_day, _now(), db.current_tenant(), route_id, int(nm), discipline or "",
             section_id or "", _iso(d)))
    # every day in the bucket is now a chosen figure, not a derived one
    marks = ",".join("?" * len(all_dates))
    db.execute(
        f"UPDATE forecast_days SET status = 'edited', parent_week_qty = ?, updated_at = ? "
        f"WHERE tenant_id = ? AND route_id = ? AND month_index = ? AND discipline = ? "
        f"AND section_id = ? AND day_date IN ({marks}) AND status <> 'confirmed'",
        (wq, _now(), db.current_tenant(), route_id, int(nm), discipline or "",
         section_id or "", *[_iso(d) for d in all_dates]))
    res["spread"] = {"applied": True, "delta": round(delta, 6),
                     "per_day": round(per_day, 6), "days": [_iso(d) for d in dates]}
    res["line"] = _line_view(route_id, nm, discipline, section_id, nw)
    return res


def reopen_week(route_id, month_index, discipline, section_id, week_index, by=None,
                start_year=None, today=None):
    """
    weeks.reopen_week(), then every `confirmed` day of that week goes back to `edited`.
    Not `derived`: the figures were chosen when the week was confirmed and must not be
    re-uniformed by the next read. Nothing is deleted.
    """
    res = weeks.reopen_week(route_id, month_index, discipline, section_id, week_index, by=by)
    if res.get("error"):
        return res
    dates = [_iso(d) for d in bucket_dates(month_index, week_index, start_year)]
    marks = ",".join("?" * len(dates))
    db.execute(
        f"UPDATE forecast_days SET status = 'edited', updated_at = ? "
        f"WHERE tenant_id = ? AND route_id = ? AND month_index = ? AND discipline = ? "
        f"AND section_id = ? AND day_date IN ({marks}) AND status = 'confirmed'",
        (_now(), db.current_tenant(), route_id, int(month_index), discipline or "",
         section_id or "", *dates))
    res["line"] = _line_view(route_id, month_index, discipline, section_id, week_index)
    return res


def summary():
    n = db.query("SELECT COUNT(*) AS n FROM forecast_days WHERE tenant_id = ?",
                 (db.current_tenant(),))[0]["n"]
    by_status = db.query(
        "SELECT status, COUNT(*) AS n FROM forecast_days WHERE tenant_id = ? "
        "GROUP BY status", (db.current_tenant(),))
    return {"days": n, "by_status": {r["status"]: r["n"] for r in by_status}}

"""
Look-ahead v2, slices 3–6 (2026-09-09) — the page's read model, in one call.

    GET /api/lookahead?bucket=commit|next

returns everything the three views need:

    commit    the chosen bucket's lines, day by day, with slice 2's context / derived /
              totals — and the clash rail for it
    account   the bucket BEFORE the commit bucket: per line planned, actual, actual €,
              variance, hold (98 % band), what calibrate has carried, and the action
              state the Account view renders (held · spread · applied · waiting)
    horizon   the week rows of the commit month and the month after, with a ROLE on
              each bucket relative to the commit bucket (account / commit / make-ready /
              early-warning), so the Horizon view can label its columns
    stock     the destination piles' forecast balance at the end of the commit week
    clashes   the rail

Visibility: the caller's IPT scope filters every line list. Cross-IPT clash sums are
computed on the unfiltered lines and only the caller's flags come back (see
clashes.py). Nothing is written by a read except the lazy day materialisation slice 1
already does.
"""
import datetime

import access
import clashes
import days
import derived
import weeks

ROLES = ("account", "commit", "make-ready", "early-warning")


def _role_of(bucket, commit, prev, following, after):
    b = (int(bucket[0]), int(bucket[1]))
    if b == commit:
        return "commit"
    if b == prev:
        return "account"
    if b == following:
        return "make-ready"
    if b == after:
        return "early-warning"
    return None


def prev_week(month_index, week_index):
    if int(week_index) > 1:
        return int(month_index), int(week_index) - 1
    return int(month_index) - 1, weeks.WEEKS_PER_MONTH


def account_rows(commit_mi, commit_wi, route_id=None):
    """The bucket before the commit bucket, one row per Approved line, with the hold test."""
    pm, pw = prev_week(commit_mi, commit_wi)
    if pm < 1:
        return {"week": None, "rows": []}
    rows = []
    for w in weeks.list_weeks(pm, pm, route_id=route_id):
        if int(w["week_index"]) != int(pw) or w.get("parent_status") != "Approved":
            continue
        planned = float(w.get("planned_qty") or 0)
        actual = w.get("actual_qty")
        held = (actual is not None and planned > 0 and float(actual) >= clashes.HOLD_BAND * planned)
        variance = w.get("variance")
        carried = float(w.get("calibrated_qty") or 0)
        # a line inside the band HOLDS — its small variance is not a shortage to spread
        remaining_short = (round(variance - carried, 6)
                           if variance is not None and variance > 0 and not held else 0.0)
        if actual is None:
            action = "waiting"
        elif held or (variance is not None and variance <= 0):
            action = "held"
        elif remaining_short > 1e-6:
            action = "spread"
        else:
            action = "applied"
        w["held"] = held
        w["carried"] = carried
        w["remaining_short"] = remaining_short
        w["action"] = action
        rows.append(w)
    dates = days.bucket_dates(pm, pw)
    n_actual = sum(1 for r in rows if r.get("actual_qty") is not None)
    return {
        "week": {"month_index": pm, "week_index": pw,
                 "from": dates[0].isoformat(), "to": dates[-1].isoformat()},
        "rows": rows,
        "delivered": sum(1 for r in rows if r["held"]),
        "lines": len(rows),
        "reported": n_actual,
        "actual_t": None,                  # filled by page() once units are known
        "shortfall": round(sum(r["remaining_short"] for r in rows), 3),
        "open_to_calibrate": sum(1 for r in rows if r["action"] == "spread"),
        "hold_band": clashes.HOLD_BAND,
    }


def horizon_rows(commit_mi, commit_wi, route_id=None):
    """Week rows for the commit month and the next, each carrying its role."""
    prev = prev_week(commit_mi, commit_wi)
    following = weeks.next_week(commit_mi, commit_wi)
    after = weeks.next_week(*following)
    commit = (int(commit_mi), int(commit_wi))
    rows = weeks.list_weeks(commit_mi, commit_mi + 1, route_id=route_id)
    for r in rows:
        r["role"] = _role_of((r["month_index"], r["week_index"]), commit, prev, following, after)
    return {"from_month": int(commit_mi), "to_month": int(commit_mi) + 1, "rows": rows,
            "roles": {"account": list(prev), "commit": list(commit),
                      "make-ready": list(following), "early-warning": list(after)}}


def tark_tee_flags(bucket="commit", route_id=None, acc=None, today=None):
    """
    The TARK_TEE flags alone, for the caller's lines — fetched by the page AFTER it has
    rendered, because this is live data and a geometry loop (see clashes.tark_tee).
    """
    acc = acc if acc is not None else access.current()
    full = days.list_days(route_id=route_id, bucket=bucket, today=today)
    derived.decorate(full)
    visible = access.filter_lines(list(full["lines"]), acc)
    vis_keys = {clashes.line_key(l) for l in visible}
    flags, status, routes = clashes.tark_tee(full["lines"])
    vis = [f for f in flags
           if (f["route_id"], int(f["month_index"]), f["discipline"], f["section_id"]) in vis_keys]
    return {"flags": vis, "count": len(vis), "status": status, "routes_checked": routes,
            "commit_week": full.get("commit_week")}


def page(bucket="commit", route_id=None, acc=None, with_tark_tee=False, today=None):
    acc = acc if acc is not None else access.current()
    factors = None
    # the commit bucket's days, for EVERY line (clash sums), then filtered
    full = days.list_days(route_id=route_id, bucket=bucket, today=today)
    derived.decorate(full)
    all_lines = full["lines"]
    visible = access.filter_lines(list(all_lines), acc)
    vis_keys = {clashes.line_key(l) for l in visible}
    cw = full.get("commit_week") or {}
    mi, wi = cw.get("month_index"), cw.get("week_index")

    commit = {k: v for k, v in full.items() if k != "lines"}
    commit["lines"] = visible
    # totals for what the caller can see — decorate() ran on the full set above
    derived.decorate(commit)
    commit["statuses"] = list(days.DAY_STATUSES)

    out = {"bucket": commit.get("bucket"), "commit_week": cw, "today_week": full.get("today_week"),
           "today": (today or datetime.date.today()).isoformat(),
           "commit": commit}
    if mi is None:
        out.update({"account": {"week": None, "rows": []}, "horizon": {"rows": []},
                    "clashes": {"flags": [], "count": 0, "sources": {}}, "stock": []})
        return out

    # account: previous bucket, filtered — and its actual tonnes via the same payloads
    acct_all = account_rows(mi, wi, route_id=route_id)
    acct = dict(acct_all)
    acct["rows"] = access.filter_lines(acct_all["rows"], acc)
    import conversions
    factors = conversions.load_factors()
    t = 0.0
    routes = derived._routes_and_names()
    cache = {}
    for r in acct["rows"]:
        if r.get("actual_qty") is not None:
            ctx = {"unit": r.get("unit"), "material_type": r.get("material_type"),
                   "vehicle_type": r.get("vehicle_type"),
                   "payload_t": derived.payload_for(r.get("vehicle_type"), factors)[0]}
            t += derived.tonnes_of(r["actual_qty"], ctx, factors)
        # planned € for the account week, from the route's rates, so € variance exists
        rt = routes.get(r["route_id"]) or {}
        r["rate_set"] = any(rt.get(k) is not None for k in
                            ("rate_eur_per_load", "rate_eur_per_t", "rate_eur_per_km"))
        # the week's planned € and (once typed) actual € against it, through the same
        # formulas the Commit view uses for a day — a week is just a bigger quantity
        ctx = derived.line_context({"route_id": r["route_id"], "unit": r.get("unit"),
                                    "material_type": r.get("material_type"),
                                    "vehicle_type": r.get("vehicle_type"), "ipt": r.get("ipt"),
                                    "discipline": r.get("discipline"), "section_id": r.get("section_id"),
                                    "week": r}, factors, routes=routes, cache=cache)
        pf = derived.day_figures(r.get("planned_qty"), ctx, factors)
        r["planned_eur"] = pf.get("eur")
        r["planned_eur_partial"] = pf.get("eur_partial")
        r["planned_t"] = pf.get("tonnes")
        r["eur_variance"] = (round(float(pf["eur"]) - float(r["actual_cost_eur"]), 2)
                             if pf.get("eur") is not None and r.get("actual_cost_eur") is not None
                             else None)
    acct["actual_t"] = round(t, 3)
    acct["delivered"] = sum(1 for r in acct["rows"] if r.get("held"))
    acct["lines"] = len(acct["rows"])
    acct["reported"] = sum(1 for r in acct["rows"] if r.get("actual_qty") is not None)
    acct["shortfall"] = round(sum(r.get("remaining_short") or 0 for r in acct["rows"]), 3)
    acct["open_to_calibrate"] = sum(1 for r in acct["rows"] if r.get("action") == "spread")
    out["account"] = acct

    # horizon, filtered
    hz = horizon_rows(mi, wi, route_id=route_id)
    hz["rows"] = access.filter_lines(hz["rows"], acc)
    out["horizon"] = hz

    # the rail, on the unfiltered lines, returned for the visible ones
    cl = clashes.compute(all_lines, vis_keys, acct_all["rows"], mi, wi,
                         with_tark_tee=with_tark_tee)
    out["stock"] = cl.pop("stock")
    out["clashes"] = cl
    return out

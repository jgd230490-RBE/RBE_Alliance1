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
    stock     the destination stockpiles' forecast balance at the end of the commit week
    clashes   the rail

Visibility: the caller's IPT scope filters every line list. Cross-IPT clash sums are
computed on the unfiltered lines and only the caller's flags come back (see
clashes.py). Nothing is written by a read except the lazy day materialisation slice 1
already does.
"""
import datetime
import json

import access
import db
import clashes
import days
import derived
import restrictions
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
    return weeks.prev_week(month_index, week_index)


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


def account_stock(pm, pw):
    """
    10 Sep: the stockpiles for the ACCOUNT week — one row per stockpile: opening (the
    balance at the end of the week before), in (typed week actuals on routes delivering
    there), out (the typed consumption of this week, None until typed), closing, and
    remaining against capacity. This is where "what came out" is typed now; the W1–W4
    grid on Horizon is gone. Everything is stockpiles.balances()'s arithmetic — nothing
    is recomputed here. Not IPT-scoped (C10): a stockpile has no IPT.
    """
    import stockpiles
    if pm is None or int(pm) < 1:
        return []
    bal = stockpiles.balances(1, int(pm))
    qm, qw = prev_week(pm, pw)
    typed = {(r["location_id"], int(r["month_index"]), int(r["week_index"])): r for r in db.query(
        "SELECT location_id, month_index, week_index, consumed_qty, unit, note "
        "FROM stockpile_weeks WHERE tenant_id = ? AND month_index = ? AND week_index = ?",
        (db.current_tenant(), int(pm), int(pw)))}
    out = []
    for sp in bal["stockpiles"]:
        wk = {(int(w["month_index"]), int(w["week_index"])): w for w in sp["weeks"]}
        this = wk.get((int(pm), int(pw)))
        before = wk.get((qm, qw))
        opening = float(before["balance_end"]) if before else float(sp.get("opening_qty") or 0)
        t = typed.get((sp["location_id"], int(pm), int(pw)))
        cap = sp.get("capacity_qty")
        closing = float(this["balance_end"]) if this else opening
        out.append({
            "location_id": sp["location_id"], "name": sp["name"], "loc_type": sp.get("loc_type"),
            "unit": sp.get("capacity_unit"), "capacity_qty": cap,
            "opening": round(opening, 3),
            "inbound": round(float(this["inbound"]), 3) if this else 0.0,
            # None = not typed. 0 is a typed zero. The balance treats both as nothing out.
            "consumed": (float(t["consumed_qty"]) if t and t.get("consumed_qty") is not None else None),
            "consumed_unit": (t.get("unit") if t else None),
            "note": (t.get("note") if t else None),
            "closing": round(closing, 3),
            "remaining": (round(float(cap) - closing, 3) if cap is not None else None),
            "over": bool(cap is not None and closing > float(cap)),
            "unconvertible_movements": sp.get("unconvertible_movements") or 0,
        })
    return out


def week_geometry(route_ids, vehicles=None):
    """
    10 Sep — the Commit view's map and the PDF's map read this. For each route id (in the
    order given): the LOADED alt-0 geometry baked for that line's vehicle, else any baked
    profile (named), plus both ends with their coordinates. A route with no baked
    geometry is returned with `geometry: null` — drawn as nothing, never as a straight
    line pretending to be a road. Not a page read: three small queries, whatever the
    number of lines.
    """
    ids = [r for r in dict.fromkeys(route_ids or []) if r]
    if not ids:
        return []
    vehicles = vehicles or {}
    marks = ",".join("?" * len(ids))
    routes = {r["id"]: r for r in db.query(
        f"SELECT * FROM routes WHERE tenant_id = ? AND id IN ({marks})", (db.current_tenant(), *ids))}
    loc_ids = list({x for r in routes.values() for x in (r.get("origin_id"), r.get("dest_id")) if x})
    locs = {}
    if loc_ids:
        lm = ",".join("?" * len(loc_ids))
        locs = {l["id"]: l for l in db.query(
            f"SELECT id, name, loc_type, lat, lon FROM locations WHERE tenant_id = ? AND id IN ({lm})",
            (db.current_tenant(), *loc_ids))}
    geo = {}
    for g in db.query(
            f"SELECT route_id, vehicle_profile, geometry, distance_km FROM route_geometry "
            f"WHERE tenant_id = ? AND leg = 'loaded' AND alt_index = 0 AND geometry IS NOT NULL "
            f"AND route_id IN ({marks})", (db.current_tenant(), *ids)):
        geo.setdefault(g["route_id"], {})[g["vehicle_profile"]] = g

    def end(loc_id):
        l = locs.get(loc_id) or {}
        return {"id": loc_id, "name": l.get("name") or loc_id, "loc_type": l.get("loc_type"),
                "lat": (float(l["lat"]) if l.get("lat") is not None else None),
                "lon": (float(l["lon"]) if l.get("lon") is not None else None)}

    out = []
    for rid in ids:
        r = routes.get(rid)
        if not r:
            continue
        per = geo.get(rid, {})
        want = vehicles.get(rid)
        used = want if want in per else (sorted(per)[0] if per else None)
        coords = None
        if used:
            try:
                coords = [[float(p[0]), float(p[1])] for p in json.loads(per[used]["geometry"])]
            except Exception:
                coords = None
        out.append({"route_id": rid, "ipt": r.get("ipt"),
                    "origin": end(r.get("origin_id")), "dest": end(r.get("dest_id")),
                    "vehicle_profile": used, "vehicle_as_planned": (used == want) if used else None,
                    "distance_km": (float(per[used]["distance_km"]) if used and per[used].get("distance_km") is not None else None),
                    "geometry": coords})
    return out


def horizon_rows(commit_mi, commit_wi, route_id=None):
    """Week rows for the commit month and the next, each carrying its role."""
    prev = prev_week(commit_mi, commit_wi)
    following = weeks.next_week(commit_mi, commit_wi)
    after = weeks.next_week(*following)
    commit = (int(commit_mi), int(commit_wi))
    rows = weeks.list_weeks(commit_mi, commit_mi + 1, route_id=route_id)
    for r in rows:
        r["role"] = _role_of((r["month_index"], r["week_index"]), commit, prev, following, after)
    # 10 Sep: calendar weeks — a month has four or five, so the grid asks rather than
    # assuming four. Each week carries its Monday–Sunday span for the column header.
    spans = {}
    for m in (int(commit_mi), int(commit_mi) + 1):
        for w in range(1, weeks.weeks_in_month(m) + 1):
            mon, sun = weeks.week_span(m, w)
            spans[f"{m}|{w}"] = {"from": mon.isoformat(), "to": sun.isoformat()}
    return {"from_month": int(commit_mi), "to_month": int(commit_mi) + 1, "rows": rows,
            "weeks_in_month": {str(m): weeks.weeks_in_month(m) for m in (int(commit_mi), int(commit_mi) + 1)},
            "week_spans": spans,
            "roles": {"account": list(prev), "commit": list(commit),
                      "make-ready": list(following), "early-warning": list(after)}}


def tark_tee_status(bucket="commit", route_id=None, acc=None, today=None):
    """
    The stored TARK_TEE flags for the caller's lines, plus how current they are and
    whether a refresh is running — the page polls this while a refresh runs.
    """
    acc = acc if acc is not None else access.current()
    full = days.list_days(route_id=route_id, bucket=bucket, today=today)
    derived.decorate(full)
    visible = access.filter_lines(list(full["lines"]), acc)
    vis_keys = {clashes.line_key(l) for l in visible}
    flags, source = clashes.tark_tee(full["lines"])
    vis = [f for f in flags
           if (f["route_id"], int(f["month_index"]), f["discipline"], f["section_id"]) in vis_keys]
    return {"flags": vis, "count": len(vis), "status": source["status"],
            "checked_at": source["checked_at"], "unchecked": source["unchecked"],
            "routes": source["routes"], "refresh": restrictions.refresh_state(),
            "commit_week": full.get("commit_week")}


def page(bucket="commit", route_id=None, acc=None, with_tark_tee=True, today=None):
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
        out.update({"account": {"week": None, "rows": [], "stock": []}, "horizon": {"rows": []},
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
    cost = derived.costing_context()          # once, not per row
    for r in acct["rows"]:
        if r.get("actual_qty") is not None:
            ctx = {"unit": r.get("unit"), "material_type": r.get("material_type"),
                   "vehicle_type": r.get("vehicle_type"),
                   "payload_t": derived.payload_for(r.get("vehicle_type"), factors)[0]}
            t += derived.tonnes_of(r["actual_qty"], ctx, factors)
        # planned € for the account week, from the route's rates — or, since 10 Sep
        # evening, the tenant's target rates where the route has none — so € variance
        # exists. `rate_source` says which; the row prints "target" when it is.
        # the week's planned € and (once typed) actual € against it, through the same
        # formulas the Commit view uses for a day — a week is just a bigger quantity
        ctx = derived.line_context({"route_id": r["route_id"], "unit": r.get("unit"),
                                    "month_index": r.get("month_index"),
                                    "material_type": r.get("material_type"),
                                    "vehicle_type": r.get("vehicle_type"), "ipt": r.get("ipt"),
                                    "discipline": r.get("discipline"), "section_id": r.get("section_id"),
                                    "week": r}, factors, routes=routes, cache=cache, cost=cost)
        pf = derived.day_figures(r.get("planned_qty"), ctx, factors)
        r["rate_set"] = bool(ctx.get("rate_set"))
        r["rate_source"] = ctx.get("rate_source")
        r["planned_eur"] = pf.get("eur")
        r["planned_eur_partial"] = pf.get("eur_partial")
        r["planned_eur_adj"] = pf.get("eur_adj")
        r["planned_fair_eur"] = pf.get("fair_eur")
        r["fair_flags"] = list(((ctx.get("fair") or {}).get("flags")) or [])
        # 11 Sep: the same figures per tonne / trip / km, for like-for-like comparison
        _bk = (float(ctx.get("basis_km") or 0) * pf["trips"]) if ctx.get("baked") else None
        r["planned_units"] = derived.unit_prices(pf.get("eur"), pf.get("tonnes"), pf["trips"], _bk)
        r["fair_units"] = derived.unit_prices(pf.get("fair_eur"), pf.get("tonnes"), pf["trips"], _bk)
        r["planned_trips"] = pf["trips"]
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
    aw = acct.get("week") or {}
    acct["stock"] = account_stock(aw.get("month_index"), aw.get("week_index")) if aw else []
    acct["eur_target_lines"] = sum(1 for r in acct["rows"] if r.get("rate_source") == "target")
    acct["fair_lines"] = sum(1 for r in acct["rows"] if r.get("planned_fair_eur") is not None)
    out["account"] = acct
    # the settings and index behind every € on the page (also on commit.costing)
    out["costing"] = cost

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

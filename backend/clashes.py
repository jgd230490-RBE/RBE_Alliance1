"""
Look-ahead v2, slice 3 (2026-09-09) — the clash rail. FLAGS, NEVER BLOCKS.

Every code in the brief's §6, computed on read from the decorated day lines
(derived.decorate() output) plus the week layer, the stock read model and — when it can
be reached — Tark Tee. Nothing here is stored, nothing here refuses a confirm.

    SHORTAGE        last week's actual < planned and calibrate has not carried it
    DAYS_NE_WEEK    sum(day planned) ≠ week planned      (slice 1's flag, surfaced)
    PILE_OVER       the destination pile's forecast balance at week end > capacity
    ROUTE_CAP       max_vehicles_per_day typed on the route and the vehicles that day
                    on that route_id — across EVERY IPT — exceed it
    IPT_SHARE       two or more IPTs with quantity the same day on the same route_id,
                    or the same origin+dest, or the same haul-road id
    TARK_TEE        a Tark Tee restriction intersects the route's baked geometry
    UNBAKED         quantity on a route not baked for the line's vehicle (slice 2)
    PARENT_CHANGED  the month moved under a set week (Task C's flag, surfaced)

Rules that are the module, not details:

* **Cross-IPT sums are computed on the UNFILTERED lines.** ROUTE_CAP and IPT_SHARE only
  mean something if they see every IPT's quantity on a route; an IPT code that could
  only see its own lines would never be told it shares a route. So the caller passes
  the whole tenant's decorated lines and the flags are then filtered to what the
  caller may see by `line_key` — the other IPT's quantity is never in the response,
  only the fact that it exists.
* **A source that cannot be reached says so.** Tark Tee is live data; when it is down
  the rail must not read "no restrictions" — `sources.tark_tee` is 'unavailable' and no
  TARK_TEE flag is raised or suppressed. Never cache a failure (warning-stack lesson).
  🔴 **And the LIVE check is never on a page read.** The page reads the STORED per-route
  check (restrictions.store_checks, run on demand or after a bake) and reports its age;
  a route never checked is listed as such, not shown clean.
* **Nothing invents a 40-trip constant.** ROUTE_CAP fires only on a typed cap.
"""
import datetime

import db
import derived
import restrictions
import stockpiles
import weeks

CODES = ("SHORTAGE", "DAYS_NE_WEEK", "PILE_OVER", "ROUTE_CAP", "IPT_SHARE", "TARK_TEE",
         "UNBAKED", "PARENT_CHANGED")

#: The 98 % band (brief L5): a line "holds" when actual ≥ 98 % of planned.
HOLD_BAND = 0.98


def line_key(l):
    return (l["route_id"], int(l["month_index"]), l.get("discipline") or "",
            l.get("section_id") or "")


def _flag(code, line, text, **extra):
    d = {"code": code, "route_id": line.get("route_id"), "month_index": line.get("month_index"),
         "discipline": line.get("discipline") or "", "section_id": line.get("section_id") or "",
         "ipt": line.get("ipt"), "text": text}
    d.update(extra)
    return d


def _label(line):
    c = line.get("context") or {}
    o, d = c.get("origin_name") or line.get("route_id"), c.get("dest_name") or ""
    return f"{o} → {d}" if d else str(o)


# --------------------------------------------------------------------------- #
#  Per-line flags                                                              #
# --------------------------------------------------------------------------- #
def per_line(lines, account_rows):
    """
    UNBAKED, DAYS_NE_WEEK, PARENT_CHANGED from the decorated lines; SHORTAGE from the
    account week's rows (keyed by line key, minus week_index).
    """
    out = []
    short = {}
    for a in account_rows or []:
        if a.get("actual_qty") is None:
            continue
        planned, actual = float(a.get("planned_qty") or 0), float(a["actual_qty"])
        if planned <= 0 or actual >= HOLD_BAND * planned:
            continue
        # carried already? calibrated_qty records the variance carried; a shortage
        # remains only for the part not yet carried
        variance = planned - actual
        carried = float(a.get("calibrated_qty") or 0)
        if variance - carried > 1e-6:
            short[(a["route_id"], a.get("discipline") or "", a.get("section_id") or "")] = {
                "short": round(variance - carried, 3), "unit": a.get("unit")}
    for l in lines:
        c = l.get("context") or {}
        if derived.FLAG_UNBAKED in (c.get("flags") or []):
            out.append(_flag("UNBAKED", l,
                             f"{_label(l)} is not baked for {c.get('vehicle_short') or c.get('vehicle_type') or 'its vehicle'} — km omitted on that line"))
        if l.get("days_ne_week"):
            out.append(_flag("DAYS_NE_WEEK", l,
                             f"days ≠ week on {_label(l)} — days sum {l.get('days_sum')} against week {(l.get('week') or {}).get('planned_qty')}",
                             days_sum=l.get("days_sum"), week_qty=(l.get("week") or {}).get("planned_qty")))
        if (l.get("week") or {}).get("parent_changed"):
            out.append(_flag("PARENT_CHANGED", l, f"month changed under {_label(l)}'s set week"))
        k = (l["route_id"], l.get("discipline") or "", l.get("section_id") or "")
        if k in short:
            s = short[k]
            out.append(_flag("SHORTAGE", l,
                             f"{_label(l)} last week {s['short']:g} {s['unit'] or ''} short, not yet carried".strip(),
                             short=s["short"]))
    return out


# --------------------------------------------------------------------------- #
#  Cross-line flags (need every IPT's lines)                                   #
# --------------------------------------------------------------------------- #
def route_cap(lines):
    """ROUTE_CAP: per route per day, vehicles across every line vs the typed cap."""
    by = {}                                   # (route_id, date) -> [veh_sum, cap, lines]
    for l in lines:
        c = l.get("context") or {}
        cap = c.get("max_vehicles_per_day")
        if cap is None:
            continue
        for d in l.get("days", []) or []:
            v = (d.get("derived") or {}).get("vehicles")
            if not v:
                continue
            e = by.setdefault((l["route_id"], d["day_date"]), [0, int(cap), []])
            e[0] += int(v)
            e[2].append(l)
    out = []
    for (rid, day), (veh, cap, ls) in sorted(by.items()):
        if veh > cap:
            for l in ls:
                out.append(_flag("ROUTE_CAP", l,
                                 f"{rid} {day}: {veh} veh against a cap of {cap}",
                                 day_date=day, vehicles=veh, cap=cap))
    return out


def ipt_share(lines):
    """
    IPT_SHARE: two+ IPTs with quantity the same day on the same route_id, the same
    origin+dest, or the same haul-road id. One flag per (line, day, reason).
    """
    haul = {}
    for r in db.query("SELECT route_id, zone_id FROM route_haul_roads WHERE tenant_id = ?",
                      (db.current_tenant(),)):
        haul.setdefault(r["route_id"], set()).add(r["zone_id"])
    # group key -> date -> {ipt: [lines]}
    groups = {}
    for l in lines:
        ipt = l.get("ipt")
        if not ipt:
            continue                          # a line with no IPT cannot share one
        c = l.get("context") or {}
        keys = [("route", l["route_id"])]
        if c.get("origin_id") and c.get("dest_id"):
            keys.append(("od", f"{c['origin_id']}→{c['dest_id']}"))
        for z in haul.get(l["route_id"], ()):
            keys.append(("haul", z))
        for d in l.get("days", []) or []:
            if float(d.get("planned_qty") or 0) <= 0:
                continue
            for k in keys:
                groups.setdefault(k, {}).setdefault(d["day_date"], {}).setdefault(ipt, []).append(l)
    out, seen = [], set()
    for (kind, what), days in groups.items():
        for day, by_ipt in days.items():
            if len(by_ipt) < 2:
                continue
            ipts = sorted(by_ipt)
            for ipt, ls in by_ipt.items():
                for l in ls:
                    sig = (line_key(l), day, kind, what)
                    if sig in seen:
                        continue
                    seen.add(sig)
                    where = {"route": f"on {what}", "od": f"on the same haul {what}",
                             "haul": f"on haul road {what}"}[kind]
                    out.append(_flag("IPT_SHARE", l,
                                     f"{' + '.join(ipts)} {where} on {day}",
                                     day_date=day, ipts=ipts, share=kind, share_id=what))
    return out


# --------------------------------------------------------------------------- #
#  Stock and Tark Tee                                                          #
# --------------------------------------------------------------------------- #
def stock_forecast(lines, month_index, week_index):
    """
    Per pile: opening (balance at the end of the previous week, from actuals), planned
    inbound this week (the commit week's PLANNED quantities of every line into it, in the
    pile's unit), typed consumption for the week, and the forecast balance. Over when
    forecast > capacity. Piles with no capacity are listed with `over` False.
    """
    import conversions
    factors = conversions.load_factors()
    bal = stockpiles.balances(1, int(month_index))
    pm, pw = (int(month_index), int(week_index) - 1) if int(week_index) > 1 \
        else (int(month_index) - 1, weeks.WEEKS_PER_MONTH)
    out = []
    for sp in bal["stockpiles"]:
        wk = {(w["month_index"], w["week_index"]): w for w in sp["weeks"]}
        prev = wk.get((pm, pw))
        this = wk.get((int(month_index), int(week_index)))
        opening = float(prev["balance_end"]) if prev else float(sp.get("opening_qty") or 0)
        consume = float(this["consumed"]) if this else 0.0
        inbound, dropped = 0.0, 0
        for l in lines:
            c = l.get("context") or {}
            if c.get("dest_id") != sp["location_id"]:
                continue
            q = float((l.get("week") or {}).get("planned_qty") or 0)
            v = stockpiles._to_pile_unit(q, l.get("unit"), sp["capacity_unit"],
                                         l.get("material_type"), l.get("vehicle_type"), factors)
            if v is None:
                dropped += 1
                continue
            inbound += v
        forecast = opening + inbound - consume
        cap = sp.get("capacity_qty")
        out.append({
            "location_id": sp["location_id"], "name": sp["name"],
            "unit": sp["capacity_unit"], "capacity_qty": cap,
            "opening": round(opening, 3), "inbound_planned": round(inbound, 3),
            "consume": round(consume, 3), "forecast": round(forecast, 3),
            "remaining": (round(cap - forecast, 3) if cap is not None else None),
            "over": bool(cap is not None and forecast > cap),
            "over_by": (round(forecast - cap, 3) if cap is not None and forecast > cap else None),
            "unconvertible_lines": dropped,
        })
    return out


def pile_over(lines, stock):
    """PILE_OVER on every line whose destination pile forecasts over capacity."""
    over = {s["location_id"]: s for s in stock if s.get("over")}
    out = []
    for l in lines:
        c = l.get("context") or {}
        s = over.get(c.get("dest_id"))
        if s:
            out.append(_flag("PILE_OVER", l,
                             f"{s['name']} forecasts {s['forecast']:g} / {s['capacity_qty']:g} {s['unit']} at week end — OVER by {s['over_by']:g}",
                             location_id=s["location_id"], over_by=s["over_by"]))
    return out


def tark_tee(lines):
    """
    TARK_TEE from the STORED per-route check (restrictions.store_checks) — a DB read,
    instant, always part of the page. Returns (flags, source) where source says how
    current the stored result is:

        status      'ok' every route on the page has a stored check
                    'partial' some have, some never checked (or re-baked since)
                    'unchecked' none has
                    'skipped' no baked line on the page
        checked_at  the OLDEST stored check among the page's routes
        unchecked   the routes with no stored check — a refresh fills them

    🔴 History: the live check (six services + a geometry loop) was on the page's
    critical path on 09 Sep and froze the Look-ahead; then, off the path, it still took
    30 s+ per read. It now runs on demand / after a bake and is stored on the route.
    A stored result is never a silent clean: the source block carries its age.
    """
    routes = sorted({l["route_id"] for l in lines if (l.get("context") or {}).get("baked")})
    if not routes:
        return [], {"status": "skipped", "checked_at": None, "unchecked": [], "routes": []}
    stored = restrictions.stored_checks(routes)
    unchecked = [r for r in routes if not (stored.get(r) or {}).get("checked_at")]
    checked = [stored[r]["checked_at"] for r in routes if (stored.get(r) or {}).get("checked_at")]
    status = ("unchecked" if len(unchecked) == len(routes)
              else "partial" if unchecked else "ok")
    out = []
    for l in lines:
        st = stored.get(l["route_id"]) or {}
        hits = st.get("hits") or []
        if hits:
            first = hits[0] if isinstance(hits[0], dict) else {}
            what = first.get("headline") or first.get("layer") or "a restriction"
            out.append(_flag("TARK_TEE", l,
                             f"{_label(l)} crosses {what}" + (f" (+{len(hits) - 1} more)" if len(hits) > 1 else ""),
                             hits=len(hits), checked_at=st.get("checked_at")))
    return out, {"status": status, "checked_at": (min(checked) if checked else None),
                 "unchecked": unchecked, "routes": routes}


# --------------------------------------------------------------------------- #
#  The rail                                                                    #
# --------------------------------------------------------------------------- #
def compute(all_lines, visible_keys, account_rows, month_index, week_index,
            with_tark_tee=True):
    """
    Every flag for the visible lines. `all_lines` is the WHOLE tenant's decorated lines
    (cross-IPT sums need them); `visible_keys` is the set of line_key() the caller may
    see, and only their flags are returned.
    """
    stock = stock_forecast(all_lines, month_index, week_index)
    flags = []
    flags += per_line(all_lines, account_rows)
    flags += route_cap(all_lines)
    flags += ipt_share(all_lines)
    flags += pile_over(all_lines, stock)
    # the STORED check — a DB read, so it is always on. `with_tark_tee=False` is for a
    # caller that wants the rail without it (none today).
    tt_source = {"status": "off", "checked_at": None, "unchecked": [], "routes": []}
    if with_tark_tee:
        tt, tt_source = tark_tee(all_lines)
        flags += tt
    vis = [f for f in flags
           if (f["route_id"], int(f["month_index"]), f["discipline"], f["section_id"]) in visible_keys]
    order = {c: i for i, c in enumerate(CODES)}
    vis.sort(key=lambda f: (order.get(f["code"], 99), f["route_id"], f.get("day_date") or ""))
    by_code = {}
    for f in vis:
        by_code[f["code"]] = by_code.get(f["code"], 0) + 1
    return {"flags": vis, "count": len(vis), "by_code": by_code,
            "codes": list(CODES), "hold_band": HOLD_BAND,
            "sources": {"tark_tee": tt_source["status"], "tark_tee_checked_at": tt_source["checked_at"],
                        "tark_tee_unchecked": tt_source["unchecked"],
                        "tark_tee_refresh": restrictions.refresh_state()},
            "stock": stock}

"""
Costing, 2026-09-10 (evening) — the tenant's TARGET rates and its FUEL settings.

WHY THIS EXISTS
---------------
Slice 5 (09 Sep) prices a Look-ahead line from the rates typed on ITS route — the sum
of €/load + €/t + €/km, each term zero when its rate is blank — and prints "rate not
set" where none is typed. On the live site no route has a rate, so every Planned € is
blank. The human asked (10 Sep, from a Grok note on a fuel index) for two things on
top of that, in this order:

  1. A TARGET (planned / fair) rate set, typed once per tenant on the Config page and
     used as the Planned € wherever a route has NO rate of its own. A line priced this
     way is flagged `target` everywhere € shows (row, KPI, export), never silently.
     ⭐ All-or-nothing per route: a route with ANY typed rate prices from its own rates
     only. Mixing a typed €/t with a target €/km would double-count the commonest
     Estonian quote (base + €/km) against a €/t contract. `rate_source` says which.

  2. A fuel surcharge (BAF — bunker adjustment factor, the haulage word for it) on
     whatever quote is there, from the EU Weekly Oil Bulletin's Estonian diesel price
     (fuel.py). Two typed settings and one locked base:
        share_pct           fuel's share of the haulage price, 0–100. Empty ⇒ no BAF.
        yard_eur_per_l      optional — what the haulier actually pays at the yard. For a
                            later cost-plus slice; it NEVER replaces the bulletin index.
        baf_base_eur_per_l  the index the quotes were given against. Locked by the FIRST
          + bulletin_date   "Confirm week" that finds a bulletin row and no base
                            (weeks.confirm_week → lock_baf_base_if_empty). Later
                            confirms never move it. Admin resets it on Config.
     Then, per the note's formula, for a line that has a quote €:
        baf_pct   = (index_now / index_base − 1) × share
        eur_adj   = eur × (1 + baf_pct)
     Shown as TWO figures — Quote and Quote + BAF. The quote is never replaced.

WHAT IS NOT HERE (deliberately — the human ticked "price + BAF", not the TCO card)
------------------------------------------------------------------------------------
No L/100 km, no standing €/h or running €/km, no target €/trip from litres, no
UNDER_COST clash, no BAF on actuals. Nothing is seeded: no 25 % share, no €1.92 base.
One BAF base per tenant, not per route or per haulier — a simplification the human
chose knowingly (hauliers who quoted on different dates share one base).

STORAGE
-------
One tenanted row in the existing `config` table, key 'costing' (beside 'factors'):
    {"target": {"rate_eur_per_load", "rate_eur_per_t", "rate_eur_per_km"},
     "fuel":   {"country", "yard_eur_per_l", "share_pct",
                "baf_base_eur_per_l", "baf_base_bulletin_date", "baf_base_set_at",
                "baf_base_set_by"}}
No new tenanted table (and so no new registration). The index row itself is global —
see fuel.py.
"""
import datetime
import json

import db

KEY = "costing"
TARGET_FIELDS = ("rate_eur_per_load", "rate_eur_per_t", "rate_eur_per_km")
FUEL_FIELDS = ("country", "yard_eur_per_l", "share_pct")
DEFAULT_COUNTRY = "EE"

_CACHE = {"doc": None, "at": 0.0, "tenant": None}
CACHE_TTL_S = 5.0


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _num(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def empty_doc():
    return {"target": {k: None for k in TARGET_FIELDS},
            "fuel": {"country": DEFAULT_COUNTRY, "yard_eur_per_l": None, "share_pct": None,
                     "baf_base_eur_per_l": None, "baf_base_bulletin_date": None,
                     "baf_base_set_at": None, "baf_base_set_by": None}}


def _normalise(doc):
    """A stored document with every key present, whatever an older row held."""
    out = empty_doc()
    if isinstance(doc, dict):
        for k in TARGET_FIELDS:
            out["target"][k] = _num((doc.get("target") or {}).get(k))
        f = doc.get("fuel") or {}
        out["fuel"]["country"] = (f.get("country") or DEFAULT_COUNTRY).strip().upper()[:2]
        for k in ("yard_eur_per_l", "share_pct", "baf_base_eur_per_l"):
            out["fuel"][k] = _num(f.get(k))
        for k in ("baf_base_bulletin_date", "baf_base_set_at", "baf_base_set_by"):
            out["fuel"][k] = f.get(k) or None
    return out


def invalidate():
    _CACHE["doc"], _CACHE["at"], _CACHE["tenant"] = None, 0.0, None


def get_row():
    try:
        rows = db.query("SELECT value, updated_by, updated_at FROM config "
                        "WHERE tenant_id = ? AND key = ?", (db.current_tenant(), KEY))
    except Exception:
        return None
    if not rows:
        return None
    try:
        return {"doc": _normalise(json.loads(rows[0]["value"])),
                "updated_by": rows[0]["updated_by"], "updated_at": rows[0]["updated_at"]}
    except Exception:
        return None


def settings(use_cache=True):
    """The tenant's costing document, every key present. Never None."""
    import time
    t = db.current_tenant()
    if (use_cache and _CACHE["doc"] is not None and _CACHE["tenant"] == t
            and time.time() - _CACHE["at"] < CACHE_TTL_S):
        return _CACHE["doc"]
    row = get_row()
    doc = row["doc"] if row else empty_doc()
    _CACHE["doc"], _CACHE["at"], _CACHE["tenant"] = doc, time.time(), t
    return doc


def _write(doc, by=None):
    payload = json.dumps(_normalise(doc), ensure_ascii=False)
    if get_row():
        db.execute("UPDATE config SET value = ?, updated_by = ?, updated_at = ? "
                   "WHERE tenant_id = ? AND key = ?",
                   (payload, by, _now(), db.current_tenant(), KEY))
    else:
        db.execute("INSERT INTO config (tenant_id, key, value, updated_by, updated_at) "
                   "VALUES (?, ?, ?, ?, ?)", (db.current_tenant(), KEY, payload, by, _now()))
    invalidate()


# --------------------------------------------------------------------------- #
#  Settings writes                                                             #
# --------------------------------------------------------------------------- #
def validate_target(fields):
    p = []
    for k, v in fields.items():
        if k not in TARGET_FIELDS:
            p.append(f"unknown target rate '{k}'")
            continue
        n = _num(v)
        if v not in (None, "") and n is None:
            p.append(f"{k} must be a number")
        elif n is not None and n < 0:
            p.append(f"{k} cannot be negative")
    return p


def set_target(fields, by=None):
    """
    Type the target rates. Only the keys SENT are written; a sent blank clears that
    rate. Returns {ok, problems, target}.
    """
    problems = validate_target(fields)
    if problems:
        return {"ok": False, "problems": problems}
    doc = json.loads(json.dumps(settings(use_cache=False)))
    for k, v in fields.items():
        doc["target"][k] = _num(v)
    _write(doc, by=by)
    return {"ok": True, "problems": [], "target": settings(use_cache=False)["target"]}


def validate_fuel(fields):
    p = []
    for k, v in fields.items():
        if k not in FUEL_FIELDS:
            p.append(f"unknown fuel setting '{k}'")
            continue
        if k == "country":
            if not isinstance(v, str) or len(v.strip()) != 2:
                p.append("country must be a two-letter code")
            continue
        n = _num(v)
        if v not in (None, "") and n is None:
            p.append(f"{k} must be a number")
        elif n is not None and n < 0:
            p.append(f"{k} cannot be negative")
        elif k == "share_pct" and n is not None and n > 100:
            p.append("share_pct is a percentage, 0–100")
    return p


def set_fuel(fields, by=None):
    """Type the yard price and the fuel share. Never touches the base or the index row."""
    problems = validate_fuel(fields)
    if problems:
        return {"ok": False, "problems": problems}
    doc = json.loads(json.dumps(settings(use_cache=False)))
    for k, v in fields.items():
        doc["fuel"][k] = (v.strip().upper() if k == "country" else _num(v))
    _write(doc, by=by)
    return {"ok": True, "problems": [], "fuel": settings(use_cache=False)["fuel"]}


def lock_baf_base_if_empty(index_row, by=None):
    """
    Stamp the BAF base from the current index row — ONLY when no base exists. The
    hook weeks.confirm_week() calls. Returns {"locked": bool, "base": ...}.
    A missing index row (feed never reached, nothing typed) locks nothing: a base
    of None is "not yet", never a guess.
    """
    doc = json.loads(json.dumps(settings(use_cache=False)))
    if doc["fuel"].get("baf_base_eur_per_l") is not None:
        return {"locked": False, "base": doc["fuel"]["baf_base_eur_per_l"],
                "base_date": doc["fuel"].get("baf_base_bulletin_date")}
    if not index_row or _num(index_row.get("eur_per_l")) is None:
        return {"locked": False, "base": None, "base_date": None}
    doc["fuel"]["baf_base_eur_per_l"] = _num(index_row["eur_per_l"])
    doc["fuel"]["baf_base_bulletin_date"] = index_row.get("bulletin_date")
    doc["fuel"]["baf_base_set_at"] = _now()
    doc["fuel"]["baf_base_set_by"] = by
    _write(doc, by=by)
    return {"locked": True, "base": doc["fuel"]["baf_base_eur_per_l"],
            "base_date": doc["fuel"]["baf_base_bulletin_date"]}


def reset_baf_base(by=None):
    """Admin: clear the base so the next Confirm week locks a fresh one."""
    doc = json.loads(json.dumps(settings(use_cache=False)))
    for k in ("baf_base_eur_per_l", "baf_base_bulletin_date", "baf_base_set_at", "baf_base_set_by"):
        doc["fuel"][k] = None
    _write(doc, by=by)
    return {"ok": True, "fuel": settings(use_cache=False)["fuel"]}


# --------------------------------------------------------------------------- #
#  The two formulas derived.py reads                                           #
# --------------------------------------------------------------------------- #
def resolve_rates(route_rates, target=None):
    """
    (rates, source) for one route. `route_rates` = {per_load, per_t, per_km} as typed
    on the route; `target` = the tenant's target block (settings()["target"]).
      any route rate typed  -> the route's rates, 'route'
      none, any target set  -> the target rates,  'target'
      neither               -> all None,          None      (prints "rate not set")
    """
    rr = {k: _num((route_rates or {}).get(k)) for k in ("per_load", "per_t", "per_km")}
    if any(v is not None for v in rr.values()):
        return rr, "route"
    tg = target if target is not None else settings()["target"]
    tr = {"per_load": _num(tg.get("rate_eur_per_load")), "per_t": _num(tg.get("rate_eur_per_t")),
          "per_km": _num(tg.get("rate_eur_per_km"))}
    if any(v is not None for v in tr.values()):
        return tr, "target"
    return {"per_load": None, "per_t": None, "per_km": None}, None


def baf_pct(index_eur_per_l, fuel=None):
    """
    (index_now / index_base − 1) × share, as a FRACTION (0.021 = +2.1 %). None when the
    index, the base or the share is missing — never 0, because 0 would read as "no
    change" when the truth is "not set up".
    """
    f = fuel if fuel is not None else settings()["fuel"]
    now = _num(index_eur_per_l)
    base = _num(f.get("baf_base_eur_per_l"))
    share = _num(f.get("share_pct"))
    if now is None or base is None or share is None or base <= 0:
        return None
    return round((now / base - 1.0) * (share / 100.0), 6)


def adjust(eur, pct):
    """Quote + BAF, or None when either side is missing. The quote itself is untouched."""
    if eur is None or pct is None:
        return None
    return round(float(eur) * (1.0 + float(pct)), 2)


def summary(index_row=None):
    """
    What a page or an export prints beside the figures: the settings, the index and
    the BAF that results. `index_row` is fuel.get_index() (passed in, so a page read
    costs one query for it, not one per line).
    """
    s = settings()
    idx = index_row or {}
    pct = baf_pct(idx.get("eur_per_l"), s["fuel"])
    return {
        "target": dict(s["target"]),
        "target_set": any(v is not None for v in s["target"].values()),
        "fuel": dict(s["fuel"]),
        "index_eur_per_l": _num(idx.get("eur_per_l")),
        "index_bulletin_date": idx.get("bulletin_date"),
        "index_source": idx.get("source"),
        "baf_pct": pct,
        # why there is no BAF, in one word, so the widget can say it
        "baf_reason": (None if pct is not None
                       else "no index" if _num(idx.get("eur_per_l")) is None
                       else "no base" if _num(s["fuel"].get("baf_base_eur_per_l")) is None
                       else "no share"),
    }

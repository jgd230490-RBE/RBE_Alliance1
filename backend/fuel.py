"""
Fuel index, 2026-09-10 (evening) — the EU Weekly Oil Bulletin's Estonian diesel price,
fetched server-side, cached in ONE global row, never invented.

THE SOURCE (Grok's K6, verified from this sandbox on 10 Sep)
-----------------------------------------------------------
    GET https://eurooilwatch.com/api/v1/prices          (no key)
    { lastUpdated, bulletinDate: "2026-09-07",
      dataSource: "EC Weekly Oil Bulletin (2026-09-07)",
      countries: [ {countryCode: "EE", countryName: "Estonia",
                    petrolPrice: 1.812, dieselPrice: 1.922, ...}, ... ], euAverage }
`bulletinDate` and `dataSource` are TOP-LEVEL, not per country (the note implied per
country). The feed never says "with taxes" — €1.922 can only be the taxed figure (the
ex-tax diesel price is about €1.0), and the widget's attribution says which bulletin
series it is taken to be. That is an inference; if the Commission's figure and this one
ever differ, the Commission's XLSX wins and this row can be typed over (source 'manual').

THE RULES
---------
* The browser never calls the feed. This module does, with an 8 s timeout, and stores
  the last GOOD row. A failed fetch keeps the last good row and records when and why
  it failed (`last_error`), so the page can say "the feed could not be reached at …"
  instead of falling silent — the warning-stack lesson of 09 Sep.
* One row per country, no tenant column: a national index is not client data. It is
  registered as untenanted in test_tenant_audit.py with that reason.
* ⭐ NEVER ON A PAGE READ'S CRITICAL PATH. lookahead.page() reads the stored row only.
  GET /api/fuel-index returns the stored row at once and, if it is older than 12 h,
  starts ONE background refresh (the Tark Tee pattern); the widget polls `refresh`.
  `refresh(sync=True)` runs inline for tests and for the admin's explicit POST.
* Empty table + failed fetch ⇒ eur_per_l None and `stale` true — the widget prints
  "Index unavailable — type a price". Never a litre price this module made up.
* Stale = the last good BULLETIN is older than 8 days (the bulletin is weekly, Monday
  dated, published Thursday), or there is no row at all. Old-but-fresh is not stale:
  a 5-day-old bulletin fetched a minute ago is the current bulletin.
* A manual index (Config, admin) writes the same row with source 'manual' and is
  treated exactly as a bulletin row — including for locking the BAF base.

WHAT THIS DOES NOT DO
---------------------
No station prices (Alexela, Circle K, Fuelo), no OilPriceAPI, no scraping the
Commission's XLSX (its download ids rotate). No yard price here — that is a typed
tenant setting in costing.py, and the feed never overwrites it.
"""
import datetime
import json
import threading
import urllib.request

import db

FEED_URL = "https://eurooilwatch.com/api/v1/prices"
SOURCE_BULLETIN = "eu_oil_bulletin_diesel_with_tax"
SOURCE_MANUAL = "manual"
ATTRIBUTION = "EU Weekly Oil Bulletin via EuroOilWatch"
TIMEOUT_S = 8
REFRESH_AFTER_H = 12          # re-fetch when the last attempt is older than this
STALE_AFTER_DAYS = 8          # a bulletin older than this is flagged stale
DEFAULT_COUNTRY = "EE"

_REFRESH = {"running": False, "started_at": None, "finished_at": None, "status": None, "error": None}
_REFRESH_LOCK = threading.Lock()


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _num(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
#  The feed                                                                    #
# --------------------------------------------------------------------------- #
def fetch_feed(timeout=TIMEOUT_S):
    """The raw JSON document from the feed. Raises on any failure; never retries."""
    req = urllib.request.Request(FEED_URL, headers={"Accept": "application/json",
                                                     "User-Agent": "rbe-alliance1/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_feed(doc, country=DEFAULT_COUNTRY):
    """
    {eur_per_l, bulletin_date, raw_source_label} for one country, or an {"error"}.
    Strict on purpose: a missing price is an error, never a 0 or a guess.
    """
    if not isinstance(doc, dict):
        return {"error": "feed is not a JSON object"}
    rows = doc.get("countries")
    if not isinstance(rows, list):
        return {"error": "feed has no countries[]"}
    hit = None
    for r in rows:
        if isinstance(r, dict) and str(r.get("countryCode") or "").upper() == country.upper():
            hit = r
            break
    if hit is None:
        return {"error": f"feed has no row for {country}"}
    price = _num(hit.get("dieselPrice"))
    if price is None or price <= 0:
        return {"error": f"feed has no diesel price for {country}"}
    bdate = str(doc.get("bulletinDate") or hit.get("bulletinDate") or "")[:10]
    try:
        datetime.date.fromisoformat(bdate)
    except ValueError:
        return {"error": "feed has no bulletin date"}
    label = str(doc.get("dataSource") or "EC Weekly Oil Bulletin")[:120]
    return {"eur_per_l": round(price, 3), "bulletin_date": bdate, "raw_source_label": label}


# --------------------------------------------------------------------------- #
#  The stored row                                                              #
# --------------------------------------------------------------------------- #
def get_index(country=DEFAULT_COUNTRY):
    """The stored row for a country, or None. Always one query; never a fetch."""
    try:
        rows = db.query("SELECT * FROM fuel_index WHERE country = ?", (country.upper(),))
    except Exception:
        return None
    return dict(rows[0]) if rows else None


def _upsert(country, source, eur_per_l, bulletin_date, label, fetched_at=None,
            attempt_at=None, error=None):
    cur = get_index(country)
    if cur:
        db.execute("UPDATE fuel_index SET source = ?, bulletin_date = ?, fetched_at = ?, "
                   "eur_per_l = ?, raw_source_label = ?, last_attempt_at = ?, last_error = ? "
                   "WHERE country = ?",
                   (source, bulletin_date, fetched_at, eur_per_l, label, attempt_at, error,
                    country.upper()))
    else:
        db.execute("INSERT INTO fuel_index (country, source, bulletin_date, fetched_at, "
                   "eur_per_l, raw_source_label, last_attempt_at, last_error) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (country.upper(), source, bulletin_date, fetched_at, eur_per_l, label,
                    attempt_at, error))


def _mark_failed(country, error):
    """Keep the last good row; record only that an attempt failed, and why."""
    cur = get_index(country)
    if cur:
        db.execute("UPDATE fuel_index SET last_attempt_at = ?, last_error = ? WHERE country = ?",
                   (_now(), error[:200], country.upper()))
    else:
        _upsert(country, None, None, None, None, fetched_at=None, attempt_at=_now(),
                error=error[:200])


def set_manual(eur_per_l, bulletin_date, by=None, country=DEFAULT_COUNTRY):
    """Type the index by hand (admin, Config). Same row, source 'manual'."""
    price = _num(eur_per_l)
    if price is None or price <= 0:
        return {"ok": False, "problems": ["eur_per_l must be a positive number"]}
    bdate = str(bulletin_date or "")[:10]
    try:
        datetime.date.fromisoformat(bdate)
    except ValueError:
        return {"ok": False, "problems": ["bulletin_date must be YYYY-MM-DD"]}
    _upsert(country, SOURCE_MANUAL, round(price, 3), bdate, f"typed by {by or 'unknown'}",
            fetched_at=_now(), attempt_at=_now(), error=None)
    return {"ok": True, "problems": [], "index": get_index(country)}


# --------------------------------------------------------------------------- #
#  Refresh                                                                     #
# --------------------------------------------------------------------------- #
def needs_refresh(row, now=None):
    """No successful fetch, or the last ATTEMPT older than REFRESH_AFTER_H hours."""
    if not row:
        return True
    last = row.get("last_attempt_at") or row.get("fetched_at")
    if not last:
        return True
    try:
        t = datetime.datetime.fromisoformat(str(last).replace("Z", ""))
    except ValueError:
        return True
    now = now or datetime.datetime.utcnow()
    return (now - t) > datetime.timedelta(hours=REFRESH_AFTER_H)


def is_stale(row, today=None):
    """No good row, or its bulletin older than STALE_AFTER_DAYS. The widget's chip."""
    if not row or _num(row.get("eur_per_l")) is None or not row.get("bulletin_date"):
        return True
    try:
        b = datetime.date.fromisoformat(str(row["bulletin_date"])[:10])
    except ValueError:
        return True
    today = today or datetime.date.today()
    return (today - b).days > STALE_AFTER_DAYS


def refresh_state():
    with _REFRESH_LOCK:
        return dict(_REFRESH)


def refresh(country=DEFAULT_COUNTRY, sync=False, timeout=TIMEOUT_S):
    """
    Fetch the feed and store the row. `sync=True` runs inline and returns the outcome;
    otherwise ONE background thread runs it and the current state is returned at once.
    On failure the last good row stands and `last_error` says why.
    """
    with _REFRESH_LOCK:
        if _REFRESH["running"]:
            return dict(_REFRESH)
        _REFRESH.update({"running": True, "started_at": _now(), "finished_at": None,
                         "status": None, "error": None})

    def run():
        try:
            try:
                doc = fetch_feed(timeout=timeout)
            except Exception as e:
                err = f"{type(e).__name__}: {str(e)[:120]}"
                _mark_failed(country, err)
                with _REFRESH_LOCK:
                    _REFRESH.update({"status": "unavailable", "error": err})
                return
            p = parse_feed(doc, country)
            if p.get("error"):
                _mark_failed(country, p["error"])
                with _REFRESH_LOCK:
                    _REFRESH.update({"status": "unavailable", "error": p["error"]})
                return
            now = _now()
            _upsert(country, SOURCE_BULLETIN, p["eur_per_l"], p["bulletin_date"],
                    p["raw_source_label"], fetched_at=now, attempt_at=now, error=None)
            with _REFRESH_LOCK:
                _REFRESH.update({"status": "ok", "error": None})
        except Exception as e:                       # a DB fault must not kill the thread silently
            with _REFRESH_LOCK:
                _REFRESH.update({"status": "error", "error": str(e)[:200]})
        finally:
            with _REFRESH_LOCK:
                _REFRESH.update({"running": False, "finished_at": _now()})

    if sync:
        run()
    else:
        threading.Thread(target=run, name="fuel-index-refresh", daemon=True).start()
    return refresh_state()


def ensure_fresh(country=DEFAULT_COUNTRY, sync=False):
    """The lazy path GET /api/fuel-index takes: refresh only when the row is old."""
    row = get_index(country)
    if needs_refresh(row):
        return refresh(country, sync=sync)
    return refresh_state()


def state(country=DEFAULT_COUNTRY, today=None):
    """What the widget reads: the row, its staleness, the attribution, the refresh."""
    row = get_index(country) or {}
    return {
        "country": country.upper(),
        "eur_per_l": _num(row.get("eur_per_l")),
        "bulletin_date": row.get("bulletin_date"),
        "fetched_at": row.get("fetched_at"),
        "source": row.get("source"),
        "source_label": row.get("raw_source_label"),
        "attribution": ATTRIBUTION,
        "stale": is_stale(row, today=today),
        "last_attempt_at": row.get("last_attempt_at"),
        "last_error": row.get("last_error"),
        "refresh": refresh_state(),
    }

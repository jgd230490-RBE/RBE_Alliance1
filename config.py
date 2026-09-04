"""
2.5b, 2026-09-03 — the editable copy of factors.json.

WHAT CHANGED AND WHY IT MATTERS
-------------------------------
Until now `backend/factors.json` was the single source of truth for materials, vehicles
and planning constants, and editing it meant a commit and a redeploy. The Config page
needs those numbers editable in the app, and Render's filesystem does not persist, so
the document now lives in the `config` table (key `factors`) and the file is the SEED.

    conversions.load_factors()  -> the config row if there is one, else the file

⚠️ Consequence: once the row exists, EDITING factors.json DOES NOTHING until somebody
presses "Reset to file" on the Config page (or the row is deleted). The file is where
a fresh database starts, not where the live numbers live. The Config page says so.

VALIDATION
----------
A bad document here would silently change every payload, density and cycle time in the
system, so a write is refused unless it passes validate(). The checks are structural
and physical (a payload must be a positive number; a vehicle the network is baked for
must still exist; the planning vehicles must exist) — they are not a judgement about
whether 18 t is the right payload. That stays with the person editing.
"""
import datetime
import json

import db

KEY = "factors"
# a small in-process cache so the many load_factors() calls inside one request do not
# each hit the database. Invalidated on every write through this module; a write made
# by another process (another Render instance) shows up within TTL seconds.
_CACHE = {"doc": None, "at": 0.0}
CACHE_TTL_S = 5.0


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _file_doc(conversions):
    with open(conversions._FACTORS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_row():
    """The stored document plus its stamp, or None when the row does not exist."""
    try:
        rows = db.query("SELECT value, updated_by, updated_at FROM config "
                        "WHERE tenant_id = ? AND key = ?", (db.current_tenant(), KEY))
    except Exception:
        return None          # table not created yet (a cold database, or a test)
    if not rows:
        return None
    try:
        return {"doc": json.loads(rows[0]["value"]), "updated_by": rows[0]["updated_by"],
                "updated_at": rows[0]["updated_at"]}
    except Exception:
        return None          # a corrupt row must not take the app down; fall back


def load(conversions, use_cache=True):
    """The live factors document: the config row, else the file."""
    import time
    if use_cache and _CACHE["doc"] is not None and time.time() - _CACHE["at"] < CACHE_TTL_S:
        return _CACHE["doc"]
    row = get_row()
    doc = row["doc"] if row else _file_doc(conversions)
    _CACHE["doc"], _CACHE["at"] = doc, time.time()
    return doc


def invalidate():
    _CACHE["doc"], _CACHE["at"] = None, 0.0


def seed_from_file(conversions):
    """First boot: copy the file into the table. Does nothing if the row exists."""
    if get_row():
        return {"seeded": False}
    doc = _file_doc(conversions)
    db.execute("INSERT INTO config (tenant_id, key, value, updated_by, updated_at) "
               "VALUES (?, ?, ?, ?, ?)",
               (db.current_tenant(), KEY, json.dumps(doc, ensure_ascii=False),
                "seed:factors.json", _now()))
    invalidate()
    return {"seeded": True}


def validate(doc, network=None):
    """Problems with a candidate document, as a list of sentences. Empty = fine."""
    p = []
    if not isinstance(doc, dict):
        return ["the document must be a JSON object"]
    cats = doc.get("material_categories")
    vs = doc.get("vehicles")
    plan = doc.get("planning")
    if not isinstance(cats, dict) or not any(not k.startswith("_") for k in cats):
        p.append("material_categories must be an object with at least one category")
    if not isinstance(vs, dict) or not any(not k.startswith("_") for k in vs):
        p.append("vehicles must be an object with at least one vehicle")
    if not isinstance(plan, dict):
        p.append("planning must be an object")

    def num(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool)

    if isinstance(cats, dict):
        for k, c in cats.items():
            if k.startswith("_") or not isinstance(c, dict):
                continue
            d = c.get("density_t_per_m3")
            if not num(d) or d <= 0:
                p.append(f"material '{k}': density_t_per_m3 must be a positive number")
            if c.get("default_unit") not in (None, "m3", "t", "vehicles"):
                p.append(f"material '{k}': default_unit must be m3, t or vehicles")
            for v in c.get("vehicles") or []:
                if isinstance(vs, dict) and v not in vs:
                    p.append(f"material '{k}' lists vehicle '{v}', which does not exist")
    if isinstance(vs, dict):
        for k, v in vs.items():
            if k.startswith("_") or not isinstance(v, dict):
                continue
            pl = v.get("payload_t")
            if not num(pl) or pl <= 0:
                p.append(f"vehicle '{k}': payload_t must be a positive number")
            em = v.get("emissions_kg_co2e_per_km")
            if em is not None and (not num(em) or em < 0):
                p.append(f"vehicle '{k}': emissions_kg_co2e_per_km must be a number >= 0")
            for f in ("load_minutes", "unload_minutes", "gvw_t"):
                if v.get(f) is not None and (not num(v[f]) or v[f] < 0):
                    p.append(f"vehicle '{k}': {f} must be a number >= 0")
            if v.get("gvw_t") is not None and num(v.get("gvw_t")) and num(pl) and v["gvw_t"] < pl:
                p.append(f"vehicle '{k}': gvw_t is less than payload_t")
        for name in doc.get("planning_vehicles") or []:
            if name not in vs:
                p.append(f"planning_vehicles names '{name}', which does not exist")
        # a vehicle the network is baked for cannot be deleted — its geometry rows are
        # keyed on the name and every figure on those routes would fall to _default
        if network is not None:
            try:
                baked = {g["vehicle_profile"] for g in db.query(
                    "SELECT DISTINCT vehicle_profile FROM route_geometry WHERE tenant_id = ?",
                    (db.current_tenant(),))}
                for b in sorted(baked - set(vs)):
                    p.append(f"vehicle '{b}' has baked route geometry and cannot be removed")
            except Exception:
                pass
            if getattr(network, "DEFAULT_PROFILE", None) and network.DEFAULT_PROFILE not in vs:
                p.append(f"the default routing profile '{network.DEFAULT_PROFILE}' must exist")
    if isinstance(plan, dict):
        for f in ("working_days_per_month", "shift_hours_per_day", "avg_haul_speed_kmh",
                  "load_minutes", "unload_minutes"):
            x = plan.get(f)
            if not num(x) or x <= 0:
                p.append(f"planning.{f} must be a positive number")
    for w in doc.get("seasonal_restrictions") or []:
        if not isinstance(w, dict) or not w.get("name"):
            p.append("every seasonal restriction needs a name")
            continue
        ms = w.get("months") or []
        if not all(isinstance(m, int) and 1 <= m <= 12 for m in ms):
            p.append(f"seasonal '{w['name']}': months must be integers 1-12")
        for v in w.get("restricted_vehicles") or []:
            if isinstance(vs, dict) and v not in vs:
                p.append(f"seasonal '{w['name']}' names vehicle '{v}', which does not exist")
    return p


def save(doc, by=None, network=None):
    """Validate and store. Returns {ok, problems}."""
    problems = validate(doc, network=network)
    if problems:
        return {"ok": False, "problems": problems}
    if get_row():
        db.execute("UPDATE config SET value = ?, updated_by = ?, updated_at = ? "
                   "WHERE tenant_id = ? AND key = ?",
                   (json.dumps(doc, ensure_ascii=False), by, _now(), db.current_tenant(), KEY))
    else:
        db.execute("INSERT INTO config (tenant_id, key, value, updated_by, updated_at) "
                   "VALUES (?, ?, ?, ?, ?)",
                   (db.current_tenant(), KEY, json.dumps(doc, ensure_ascii=False), by, _now()))
    invalidate()
    return {"ok": True, "problems": []}


def reset_to_file(conversions, by=None):
    """Overwrite the stored document with the file. The one way the file wins again."""
    doc = _file_doc(conversions)
    res = save(doc, by=f"reset:factors.json ({by or 'unknown'})")
    return {"ok": res["ok"], "problems": res["problems"], "doc": doc}


def status(conversions):
    """What the Config page shows at the top: where the live numbers come from."""
    row = get_row()
    file_doc = _file_doc(conversions)
    live = row["doc"] if row else file_doc
    return {
        "source": "database" if row else "file",
        "updated_by": row["updated_by"] if row else None,
        "updated_at": row["updated_at"] if row else None,
        # a cheap "has the file drifted from the live copy" signal
        "differs_from_file": bool(row) and json.dumps(live, sort_keys=True) != json.dumps(file_doc, sort_keys=True),
    }

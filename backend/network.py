"""
Dynamic routing network for RBE Alliance 1 (Phase 0).

Seeds the V2 master (locations + route pairs) into the DB, bakes truck-legal
geometry for a vehicle profile via HERE (cached, route-on-change), and serves
the cached geometry as GeoJSON for the map.
"""
import os
import json
from datetime import datetime, timezone

import db
import conversions
import here_routing

DEFAULT_PROFILE = "Artic Tipper (44t)"
_SEED = os.path.join(os.path.dirname(__file__), "seed_data", "v2_network.json")

# map V2 raw materials -> our categories (locations carry raw V2 strings on first seed)
_V2_TO_CAT = {
    "sand": "Small aggregate", "gravel": "Small aggregate",
    "limestone - rockfill": "Large aggregate / ballast",
    "limestone (shale aggregate)": "Small aggregate",
    "imported goods": "General / imported",
}


def _cat(raw):
    return _V2_TO_CAT.get((raw or "").strip().lower())


_cols_ensured = False


def _ensure_location_columns():
    """
    Belt-and-braces: make sure the newer location columns exist before we write
    them. The canonical migration is in db.init_network_db() (runs on startup),
    but this guards against a live DB whose 'locations' table predates it, so the
    admin endpoints can't hard-500 on a missing column. Idempotent, runs once.
    """
    global _cols_ensured
    if _cols_ensured:
        return
    for col in ("role", "materials", "supplies", "receives"):
        try:
            if db.IS_PG:
                db.execute(f"ALTER TABLE locations ADD COLUMN IF NOT EXISTS {col} TEXT")
            else:
                db.execute(f"ALTER TABLE locations ADD COLUMN {col} TEXT")
        except Exception:
            pass  # already present (or table not created yet — init_network_db handles that)
    _cols_ensured = True


def _role_for(loc_type):
    t = (loc_type or "").strip().lower()
    if t in ("quarry", "port"):
        return "origin"
    if t in ("compound", "site"):
        return "destination"
    return "both"


def seed_network():
    """Load the V2 network into locations + routes if not already present."""
    if db.count_locations() > 0:
        return False
    net = json.load(open(_SEED, encoding="utf-8"))
    for l in net["locations"]:
        role = _role_for(l.get("loc_type"))
        mats = [_cat(l.get("material"))] if (role in ("origin", "both") and _cat(l.get("material"))) else []
        db.execute(
            "INSERT INTO locations (id, name, loc_type, role, materials, lat, lon, material) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (l["id"], l["name"], l.get("loc_type"), role, json.dumps(mats),
             l["lat"], l["lon"], l.get("material")),
        )
    for r in net["routes"]:
        db.execute(
            "INSERT INTO routes (id, origin_id, dest_id, long_route_id, material_category, ipt, origin_temp_km) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (r["id"], r["origin_id"], r["dest_id"], r.get("long_route_id"),
             r.get("material_category"), r.get("ipt"), r.get("origin_temp_km", 0)),
        )
    return True


def backfill_location_roles():
    """Give any location still missing a role one derived from its type/material."""
    for l in db.query("SELECT * FROM locations"):
        if l.get("role"):
            continue
        role = _role_for(l.get("loc_type"))
        mats = [_cat(l.get("material"))] if (role in ("origin", "both") and _cat(l.get("material"))) else []
        db.execute("UPDATE locations SET role = ?, materials = ? WHERE id = ?",
                   (role, json.dumps(mats), l["id"]))


def _upsert_geom(route_id, profile, geometry, dist, dur, error):
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """
        INSERT INTO route_geometry
            (route_id, vehicle_profile, geometry, distance_km, duration_hr, computed_at, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (route_id, vehicle_profile) DO UPDATE SET
            geometry    = EXCLUDED.geometry,
            distance_km = EXCLUDED.distance_km,
            duration_hr = EXCLUDED.duration_hr,
            computed_at = EXCLUDED.computed_at,
            error       = EXCLUDED.error
        """,
        (route_id, profile, geometry, dist, dur, now, error),
    )


def clear_geometry(profile=None):
    """Delete cached geometry so it can be recomputed. All profiles if profile is None."""
    if profile:
        db.execute("DELETE FROM route_geometry WHERE vehicle_profile = ?", (profile,))
    else:
        db.execute("DELETE FROM route_geometry")
    return True


def bake_batch(profile=DEFAULT_PROFILE, limit=25):
    """
    Route up to `limit` not-yet-attempted routes for a profile via HERE and cache
    the geometry. Idempotent — call repeatedly until 'remaining' is 0. To recompute
    existing routes, clear_geometry() first.
    """
    if not here_routing.configured():
        return {"error": "HERE_API_KEY not set on the server", "baked": 0, "remaining": None}

    all_routes = db.query("SELECT * FROM routes ORDER BY id")
    attempted = {g["route_id"] for g in db.query(
        "SELECT route_id FROM route_geometry WHERE vehicle_profile = ?", (profile,))}
    todo = [r for r in all_routes if r["id"] not in attempted]

    locs = {l["id"]: l for l in db.query("SELECT * FROM locations")}
    factors = conversions.load_factors()
    baked = errors = 0
    samples = []
    for r in todo[:limit]:
        o = locs.get(r["origin_id"]); d = locs.get(r["dest_id"])
        if not o or not d:
            _upsert_geom(r["id"], profile, None, None, None, "missing origin/destination coordinates")
            errors += 1
            continue
        try:
            res = here_routing.route(o["lat"], o["lon"], d["lat"], d["lon"], profile, factors)
            _upsert_geom(r["id"], profile, json.dumps(res["geometry"]),
                         res["distance_km"], res["duration_hr"], None)
            baked += 1
        except Exception as e:
            _upsert_geom(r["id"], profile, None, None, None, str(e)[:300])
            errors += 1
            if len(samples) < 5:
                samples.append(f"{r['id']}: {str(e)[:100]}")
    remaining = max(0, len(todo) - min(limit, len(todo)))
    return {"profile": profile, "baked": baked, "errors": errors,
            "remaining": remaining, "error_samples": samples}


def routes_geojson(profile=DEFAULT_PROFILE):
    """Cached routes for a profile as a GeoJSON FeatureCollection (map source)."""
    locs = {l["id"]: l for l in db.query("SELECT * FROM locations")}
    geoms = {g["route_id"]: g for g in db.query(
        "SELECT * FROM route_geometry WHERE vehicle_profile = ? AND geometry IS NOT NULL", (profile,))}
    feats = []
    for r in db.query("SELECT * FROM routes ORDER BY id"):
        g = geoms.get(r["id"])
        if not g:
            continue
        o = locs.get(r["origin_id"], {}); d = locs.get(r["dest_id"], {})
        feats.append({
            "type": "Feature",
            "properties": {
                "route_id": r["id"], "long_route_id": r["long_route_id"],
                "origin": o.get("name"), "dest": d.get("name"),
                "origin_id": r["origin_id"], "dest_id": r["dest_id"],
                "material_category": r["material_category"], "ipt": r["ipt"],
                "vehicle_profile": profile,
                "distance_km": g["distance_km"], "duration_hr": g["duration_hr"],
            },
            "geometry": {"type": "LineString", "coordinates": json.loads(g["geometry"])},
        })
    return {"type": "FeatureCollection", "features": feats}


def locations_geojson():
    def _parse(s):
        try:
            return json.loads(s or "[]")
        except Exception:
            return []

    feats = []
    for l in db.query("SELECT * FROM locations ORDER BY id"):
        mats = _parse(l.get("materials"))
        supplies = _parse(l.get("supplies")) or mats     # older rows only have materials
        receives = _parse(l.get("receives"))
        feats.append({
            "type": "Feature",
            "properties": {"id": l["id"], "name": l["name"], "loc_type": l["loc_type"],
                           "role": l.get("role") or "both", "materials": mats,
                           "supplies": supplies, "receives": receives,
                           "lat": l["lat"], "lon": l["lon"]},
            "geometry": {"type": "Point", "coordinates": [l["lon"], l["lat"]]},
        })
    return {"type": "FeatureCollection", "features": feats}


def routes_touching(location_id):
    return [r["id"] for r in db.query(
        "SELECT id FROM routes WHERE origin_id = ? OR dest_id = ?", (location_id, location_id))]


def _next_location_id():
    existing = {l["id"] for l in db.query("SELECT id FROM locations")}
    i = 1
    while f"L{i:03d}" in existing:
        i += 1
    return f"L{i:03d}"


def create_location(name, role, materials=None, lat=None, lon=None, loc_type=None,
                    supplies=None, receives=None):
    _ensure_location_columns()
    lid = _next_location_id()
    supplies = supplies if supplies is not None else (materials or [])
    receives = receives or []
    # 'materials' mirrors 'supplies' so the existing map popup keeps working.
    db.execute(
        "INSERT INTO locations (id, name, loc_type, role, materials, supplies, receives, lat, lon, material) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (lid, name, loc_type or role, role, json.dumps(supplies), json.dumps(supplies),
         json.dumps(receives), float(lat), float(lon), None),
    )
    return {"id": lid}


def update_location(location_id, name=None, role=None, materials=None, lat=None, lon=None,
                    loc_type=None, supplies=None, receives=None):
    _ensure_location_columns()
    cur = db.query("SELECT * FROM locations WHERE id = ?", (location_id,))
    if not cur:
        return {"error": "not found"}
    cur = cur[0]
    moved = (lat is not None and float(lat) != cur["lat"]) or (lon is not None and float(lon) != cur["lon"])
    # when supplies is given, mirror it into materials so the map popup stays in sync
    if supplies is not None:
        supplies_json = json.dumps(supplies)
        materials_json = json.dumps(supplies)
    else:
        supplies_json = cur.get("supplies")
        materials_json = json.dumps(materials) if materials is not None else cur.get("materials")
    receives_json = json.dumps(receives) if receives is not None else cur.get("receives")
    db.execute(
        "UPDATE locations SET name = ?, loc_type = ?, role = ?, materials = ?, supplies = ?, "
        "receives = ?, lat = ?, lon = ? WHERE id = ?",
        (name if name is not None else cur["name"],
         loc_type if loc_type is not None else cur["loc_type"],
         role if role is not None else cur["role"],
         materials_json, supplies_json, receives_json,
         float(lat) if lat is not None else cur["lat"],
         float(lon) if lon is not None else cur["lon"],
         location_id),
    )
    affected = []
    if moved:
        affected = routes_touching(location_id)
        for rid in affected:
            db.execute("DELETE FROM route_geometry WHERE route_id = ?", (rid,))
    return {"id": location_id, "moved": moved, "affected_routes": affected}


def delete_location(location_id):
    routes = routes_touching(location_id)
    for rid in routes:
        db.execute("DELETE FROM route_geometry WHERE route_id = ?", (rid,))
        db.execute("DELETE FROM routes WHERE id = ?", (rid,))
    db.execute("DELETE FROM locations WHERE id = ?", (location_id,))
    return {"id": location_id, "deleted_routes": routes}


# --------------------------------------------------------------------------- #
#  Route authoring (Phase 1)                                                    #
# --------------------------------------------------------------------------- #
def _loc_list(loc, key):
    try:
        return json.loads(loc.get(key) or "[]")
    except Exception:
        return []


def _next_route_id():
    existing = {r["id"] for r in db.query("SELECT id FROM routes")}
    i = 1
    while f"R{i:03d}" in existing:
        i += 1
    return f"R{i:03d}"


def create_route(origin_id, dest_id, material_category=None, route_id=None, ipt=None):
    """
    Manually pair an origin -> destination for a material category. Validates that
    the origin supplies the category and the destination receives it (server-side
    guard mirroring the UI). Geometry is baked separately (bake_route).
    """
    if not origin_id or not dest_id:
        return {"error": "origin and destination are required"}
    if origin_id == dest_id:
        return {"error": "origin and destination must be different"}
    o = db.query("SELECT * FROM locations WHERE id = ?", (origin_id,))
    d = db.query("SELECT * FROM locations WHERE id = ?", (dest_id,))
    if not o or not d:
        return {"error": "origin or destination not found"}
    o, d = o[0], d[0]

    if material_category:
        supplies = _loc_list(o, "supplies") or _loc_list(o, "materials")
        receives = _loc_list(d, "receives")
        if supplies and material_category not in supplies:
            return {"error": f"{o['name']} does not supply {material_category}"}
        if receives and material_category not in receives:
            return {"error": f"{d['name']} does not receive {material_category}"}

    rid = (route_id or "").strip() or _next_route_id()
    if db.query("SELECT id FROM routes WHERE id = ?", (rid,)):
        return {"error": f"route id '{rid}' already exists"}

    db.execute(
        "INSERT INTO routes (id, origin_id, dest_id, long_route_id, material_category, ipt, origin_temp_km) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (rid, origin_id, dest_id, None, material_category, (ipt or None), 0),
    )
    return {"id": rid, "origin_id": origin_id, "dest_id": dest_id,
            "material_category": material_category}


def delete_route(route_id):
    db.execute("DELETE FROM route_geometry WHERE route_id = ?", (route_id,))
    db.execute("DELETE FROM routes WHERE id = ?", (route_id,))
    return {"id": route_id}


def clear_routes():
    """Drop every route + its geometry, keeping locations. For retiring the V2 seed."""
    n = len(db.query("SELECT id FROM routes"))
    db.execute("DELETE FROM route_geometry")
    db.execute("DELETE FROM routes")
    return {"cleared": n}


def bake_route(route_id, profile=DEFAULT_PROFILE):
    """Route + cache geometry for a single route (used to bake a freshly authored route)."""
    if not here_routing.configured():
        return {"error": "HERE_API_KEY not set on the server", "route_id": route_id}
    r = db.query("SELECT * FROM routes WHERE id = ?", (route_id,))
    if not r:
        return {"error": "route not found", "route_id": route_id}
    r = r[0]
    locs = {l["id"]: l for l in db.query("SELECT * FROM locations")}
    o = locs.get(r["origin_id"]); d = locs.get(r["dest_id"])
    if not o or not d:
        _upsert_geom(route_id, profile, None, None, None, "missing origin/destination coordinates")
        return {"error": "missing origin/destination", "route_id": route_id}
    factors = conversions.load_factors()
    try:
        res = here_routing.route(o["lat"], o["lon"], d["lat"], d["lon"], profile, factors)
        _upsert_geom(route_id, profile, json.dumps(res["geometry"]),
                     res["distance_km"], res["duration_hr"], None)
        return {"route_id": route_id, "profile": profile,
                "distance_km": res["distance_km"], "baked": True}
    except Exception as e:
        _upsert_geom(route_id, profile, None, None, None, str(e)[:300])
        return {"route_id": route_id, "profile": profile, "error": str(e)[:200]}


def routes_status():
    """Per-route metadata + which profiles are baked (for the admin table)."""
    locs = {l["id"]: l for l in db.query("SELECT * FROM locations")}
    geoms = {}
    for g in db.query("SELECT * FROM route_geometry"):
        geoms.setdefault(g["route_id"], {})[g["vehicle_profile"]] = {
            "baked": bool(g["geometry"]), "distance_km": g["distance_km"],
            "duration_hr": g["duration_hr"], "error": g["error"],
        }
    out = []
    for r in db.query("SELECT * FROM routes ORDER BY id"):
        o = locs.get(r["origin_id"], {}); d = locs.get(r["dest_id"], {})
        out.append({
            "id": r["id"], "origin": o.get("name"), "dest": d.get("name"),
            "material_category": r["material_category"], "ipt": r["ipt"],
            "origin_temp_km": r["origin_temp_km"], "profiles": geoms.get(r["id"], {}),
        })
    return out


def summary():
    total = len(db.query("SELECT id FROM routes"))
    profiles = {}
    for g in db.query("SELECT vehicle_profile, geometry, error FROM route_geometry"):
        p = profiles.setdefault(g["vehicle_profile"], {"baked": 0, "errors": 0})
        if g["geometry"]:
            p["baked"] += 1
        elif g["error"]:
            p["errors"] += 1
    return {"locations": db.count_locations(), "routes": total,
            "here_configured": here_routing.configured(), "profiles": profiles}

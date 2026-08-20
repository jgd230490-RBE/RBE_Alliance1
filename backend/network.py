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


def seed_network():
    """Load the V2 network into locations + routes if not already present."""
    if db.count_locations() > 0:
        return False
    net = json.load(open(_SEED, encoding="utf-8"))
    for l in net["locations"]:
        db.execute(
            "INSERT INTO locations (id, name, loc_type, lat, lon, material) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (l["id"], l["name"], l.get("loc_type"), l["lat"], l["lon"], l.get("material")),
        )
    for r in net["routes"]:
        db.execute(
            "INSERT INTO routes (id, origin_id, dest_id, long_route_id, material_category, ipt, origin_temp_km) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (r["id"], r["origin_id"], r["dest_id"], r.get("long_route_id"),
             r.get("material_category"), r.get("ipt"), r.get("origin_temp_km", 0)),
        )
    return True


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
    feats = []
    for l in db.query("SELECT * FROM locations ORDER BY id"):
        feats.append({
            "type": "Feature",
            "properties": {"id": l["id"], "name": l["name"], "loc_type": l["loc_type"],
                           "material": l["material"]},
            "geometry": {"type": "Point", "coordinates": [l["lon"], l["lat"]]},
        })
    return {"type": "FeatureCollection", "features": feats}


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

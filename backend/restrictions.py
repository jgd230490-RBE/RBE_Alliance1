"""
Phase 2.5a — Estonian Transport Administration (Tark Tee) restriction layers.

Tark Tee publishes an ArcGIS REST server at tarktee.ee carrying exactly the data this
project has been recording by hand: mass, height and width restrictions, weak bridges,
traffic restrictions and diversions. This module fetches it, fixes its coordinates, caches
it, and cross-references it against baked route geometry.

WHY THIS IS A BACKEND PROXY AND NOT A BROWSER FETCH
---------------------------------------------------
Three reasons, in order of importance:

  1. **It is somebody else's public service.** The public map would otherwise hit
     tarktee.ee once per visitor per pan. Cached here, the whole deployment makes one
     request per layer per CACHE_TTL_S no matter how many people are looking.
  2. **The coordinates have to be fixed before anyone can draw them.** See below.
  3. CORS. Unverified either way — but proxying means it never matters.

⚠️ THE COORDINATES ARE MISLABELLED AT SOURCE
--------------------------------------------
Checked against the live service 2026-08-27: the server returns L-EST97 (EPSG:3301)
projected metres while declaring `"spatialReference":{"wkid":4326}`, and it ignores
`outSR`. Both `f=geojson` and `f=json&outSR=4326` were tried. A client that believes the
declared SR draws Estonian roads in the Indian Ocean.

So every coordinate goes through `projection.normalise()`, which converts only if the
value actually looks projected. If Tark Tee ever starts telling the truth, this keeps
working rather than double-converting.

⚠️ LAYER IDS ARE DISCOVERED, NOT HARD-CODED
-------------------------------------------
`restrictions_mass` splits into a points layer and a lines layer. Whether the other
restriction services do the same was NOT checked, so nothing here assumes it: the service
metadata is fetched once and every layer it lists is queried. That costs one extra request
per service per cache period and removes a guess that would have been wrong silently.

⚠️ WHAT `nominal_load` MEANS IS AN OPEN QUESTION — SEE check_route()
--------------------------------------------------------------------
Real values from the live service look like `"N-8/NG-30"`, `"N-13/NG-60"`, and sometimes
`""`. That is an Estonian/Soviet bridge load *classification*, not a tonnage. **Nothing
here converts it to tonnes**, because nobody has said what the mapping is. A weak bridge
on a route is reported with its class quoted verbatim and the vehicle's gross weight
alongside, and the reader draws the conclusion. Inventing a threshold would produce a
number that looks authoritative and could put a 44 t artic on a bridge that cannot take
it.

NOT VERIFIED ANYWHERE IN THE TEST SUITE
---------------------------------------
tarktee.ee is never called from the build sandbox, exactly as HERE never is. Every
assertion is against parsing, projection and matching maths with recorded fixtures. Use
`/api/admin/diagnostics/restrictions?probe=true` against the deployment to see what the
live service actually returns.
"""
import json
import time
import urllib.request
import urllib.parse
import urllib.error

import db
import projection
import zones

BASE = "https://tarktee.ee/tarktee/rest/services"

# How long a fetched layer is reused. Restrictions change on the scale of days; traffic
# restrictions and diversions on the scale of hours. 30 minutes is a compromise that
# keeps the load on a government service negligible without showing week-old closures.
CACHE_TTL_S = 1800
SERVICE_TTL_S = 86400          # layer metadata changes far less often than the data

TIMEOUT_S = 20

#: Services exposed, in the order the map lists them. `severity` drives whether a hit on
#: a route is a warning or a note: a weak bridge or a mass limit can stop a laden truck,
#: a diversion is information. Nothing here is a hard block — this is advice on a route a
#: planner already chose, not a router input.
LAYERS = {
    "bridges_weak":       {"service": "bridges_weak",       "label": "Weak bridges",        "severity": "warn"},
    "restrictions_mass":  {"service": "restrictions_mass",  "label": "Mass restrictions",   "severity": "warn"},
    "restrictions_height": {"service": "restrictions_height", "label": "Height restrictions", "severity": "warn"},
    "restrictions_width": {"service": "restrictions_width",  "label": "Width restrictions",  "severity": "warn"},
    "restrictions_traffic": {"service": "restrictions_traffic", "label": "Traffic restrictions", "severity": "note"},
    "detours":            {"service": "detours",            "label": "Diversions",          "severity": "note"},
}

#: How close a restriction has to be to a baked route before it is called a hit. HERE
#: geometry is a road centreline and a bridge point sits on that line, so the true
#: distance is metres. 30 m allows for the polyline being decimated between vertices
#: without picking up the parallel road on the other side of a dual carriageway.
#: Judgement, not a measured figure — same status as zones.DETOUR_PAD_KM.
MATCH_M = 30.0

_cache = {}          # key -> (fetched_at, payload)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "RBE-Alliance1/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.load(resp)


def _cached(key, ttl, build):
    hit = _cache.get(key)
    now = time.time()
    if hit and (now - hit[0]) < ttl:
        return hit[1]
    val = build()
    _cache[key] = (now, val)
    return val


def clear_cache():
    _cache.clear()
    return {"cleared": True}


def cache_state():
    now = time.time()
    return {k: {"age_s": round(now - v[0]), "features": (
        len(v[1].get("features", [])) if isinstance(v[1], dict) else None)}
        for k, v in sorted(_cache.items())}


# --------------------------------------------------------------------------- #
#  Fetching                                                                    #
# --------------------------------------------------------------------------- #
def _service_layers(service):
    """
    Every layer id in a service, discovered rather than assumed. See the module docstring:
    restrictions_mass has a points layer AND a lines layer, and whether the others do was
    never checked.
    """
    def build():
        meta = _get(f"{BASE}/{urllib.parse.quote(service)}/MapServer?f=json")
        return [{"id": l.get("id"), "name": l.get("name"),
                 "geometry_type": l.get("geometryType")}
                for l in (meta.get("layers") or []) if l.get("id") is not None]
    return _cached(f"svc:{service}", SERVICE_TTL_S, build)


def _rings_to_wgs84(coords):
    """Recursively walk a GeoJSON coordinate array, fixing every leaf pair."""
    if not isinstance(coords, list) or not coords:
        return coords
    if isinstance(coords[0], (int, float)):
        return projection.normalise(coords[0], coords[1])
    out = []
    for c in coords:
        fixed = _rings_to_wgs84(c)
        if fixed is not None:
            out.append(fixed)
    return out


def _fix_geometry(geom):
    if not isinstance(geom, dict) or not geom.get("coordinates"):
        return None
    fixed = _rings_to_wgs84(geom["coordinates"])
    if not fixed:
        return None
    return {"type": geom.get("type"), "coordinates": fixed}


def fetch_layer(key):
    """
    One restriction layer as a WGS84 GeoJSON FeatureCollection.

    Every layer in the service is queried and the results concatenated, with the source
    layer name carried on each feature so a points hit and a lines hit stay
    distinguishable downstream.
    """
    spec = LAYERS.get(key)
    if not spec:
        return {"error": f"unknown layer '{key}'", "known": sorted(LAYERS)}

    def build():
        service = spec["service"]
        feats, errors = [], []
        for lyr in _service_layers(service):
            q = urllib.parse.urlencode({
                "where": "1=1", "outFields": "*", "f": "geojson",
                "returnGeometry": "true",
                # asked for even though the service is known to ignore it: if it is ever
                # honoured, projection.normalise() sees degrees and passes them through
                "outSR": "4326",
            })
            url = f"{BASE}/{urllib.parse.quote(service)}/MapServer/{lyr['id']}/query?{q}"
            try:
                raw = _get(url)
            except Exception as e:
                errors.append(f"layer {lyr['id']}: {str(e)[:160]}")
                continue
            for f in (raw.get("features") or []):
                geom = _fix_geometry(f.get("geometry"))
                if not geom:
                    continue
                props = dict(f.get("properties") or {})
                props["_layer"] = lyr["name"]
                props["_kind"] = key
                props["_label"] = spec["label"]
                props["_severity"] = spec["severity"]
                feats.append({"type": "Feature", "properties": props, "geometry": geom})
        return {"type": "FeatureCollection", "features": feats,
                "layer": key, "label": spec["label"],
                "severity": spec["severity"],
                "source": "Estonian Transport Administration — Tark Tee",
                "fetched_layers": len(_service_layers(service)),
                "errors": errors}

    return _cached(f"lyr:{key}", CACHE_TTL_S, build)


def fetch_all(keys=None):
    keys = [k for k in (keys or list(LAYERS)) if k in LAYERS]
    out, errors = [], {}
    for k in keys:
        try:
            fc = fetch_layer(k)
            if fc.get("error"):
                errors[k] = fc["error"]
                continue
            out.extend(fc["features"])
            if fc.get("errors"):
                errors[k] = fc["errors"]
        except Exception as e:
            errors[k] = str(e)[:200]
    return {"type": "FeatureCollection", "features": out,
            "layers": keys, "errors": errors,
            "attribution": "Estonian Transport Administration — Tark Tee (tarktee.ee)"}


# --------------------------------------------------------------------------- #
#  Matching restrictions to baked routes                                       #
# --------------------------------------------------------------------------- #
def _point_to_segment_km(p, a, b):
    """
    Shortest distance from point p to segment a-b, in km.

    Flat-earth projection of the segment before the perpendicular drop: over the tens of
    metres that matter here the curvature error is far below the 30 m matching threshold,
    and doing it properly on the ellipsoid would be precision nobody can use against a
    polyline that HERE already decimated.
    """
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    px, py = float(p[0]), float(p[1])
    # scale longitude so a degree east is worth what it is at this latitude
    import math
    k = math.cos(math.radians((ay + by) / 2.0)) or 1e-9
    axs, bxs, pxs = ax * k, bx * k, px * k
    dx, dy = bxs - axs, by - ay
    if dx == 0 and dy == 0:
        return zones.haversine_km(p, a)
    t = ((pxs - axs) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = axs + t * dx, ay + t * dy
    return zones.haversine_km([px, py], [cx / k, cy])


def _min_distance_km(feature_geom, line):
    """Closest approach between a restriction geometry and a route polyline, in km."""
    pts = []
    g = feature_geom or {}
    t, c = g.get("type"), g.get("coordinates") or []
    if t == "Point":
        pts = [c]
    elif t == "MultiPoint":
        pts = list(c)
    elif t == "LineString":
        pts = list(c)
    elif t == "MultiLineString":
        pts = [p for part in c for p in part]
    elif t == "Polygon":
        pts = [p for ring in c for p in ring]
    elif t == "MultiPolygon":
        pts = [p for poly in c for ring in poly for p in ring]
    if not pts or len(line) < 2:
        return None
    best = None
    for p in pts:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        for i in range(len(line) - 1):
            d = _point_to_segment_km(p, line[i], line[i + 1])
            if best is None or d < best:
                best = d
                if best == 0:
                    return 0.0
    return best


def _vehicle_gross_t(profile):
    """Laden gross weight in tonnes for a profile, or None."""
    try:
        import conversions
        v = (conversions.load_factors().get("vehicles", {}) or {}).get(profile, {}) or {}
        g = (v.get("routing", {}) or {}).get("gross_weight_kg")
        return round(float(g) / 1000.0, 1) if g else (float(v["gvw_t"]) if v.get("gvw_t") else None)
    except Exception:
        return None


def _describe(props):
    """A one-line human description of a restriction feature, from whatever fields it has."""
    for k in ("bridge_name", "name", "nimi", "road_name", "description"):
        if props.get(k):
            base = str(props[k])
            break
    else:
        base = props.get("_label") or "restriction"
    road = props.get("road_name") or props.get("road_nr")
    if road and str(road) not in base:
        base = f"{base} ({road})"
    return base


def check_route(route_id, profile=None, layers=None, _fc=None):
    """
    Which Tark Tee restrictions the baked geometry of one route passes through.

    Reports, never blocks. This is advice about a route somebody already chose, and the
    router is never given this data — HERE has its own restriction model and returns its
    own `notices`, so feeding Tark Tee in as avoid-areas would be two systems arguing.

    ⚠️ **No tonnage comparison is made against a weak bridge.** `nominal_load` comes back
    as a class like "N-13/NG-60", not a number of tonnes, and what that permits has never
    been established. The class is quoted verbatim next to the vehicle's gross weight and
    `needs_interpretation` is set, so the UI can say plainly that a human has to read it.
    Turning "N-8/NG-30" into a tonne threshold by guesswork would be a confident number
    with nothing behind it.
    """
    rows = db.query(
        "SELECT * FROM route_geometry WHERE route_id = ? AND alt_index = 0 "
        "AND geometry IS NOT NULL" + (" AND vehicle_profile = ?" if profile else ""),
        (route_id, profile) if profile else (route_id,))
    if not rows:
        return {"route_id": route_id, "baked": False, "hits": [],
                "note": "not baked — nothing to check against"}

    fc = _fc if _fc is not None else fetch_all(layers)
    feats = fc.get("features") or []

    hits, seen = [], set()
    for g in rows:
        try:
            line = json.loads(g["geometry"])
        except Exception:
            continue
        gross_t = _vehicle_gross_t(g["vehicle_profile"])
        for f in feats:
            d = _min_distance_km(f.get("geometry"), line)
            if d is None or d * 1000.0 > MATCH_M:
                continue
            p = f.get("properties") or {}
            key = (g["vehicle_profile"], g["leg"], p.get("_kind"),
                   p.get("objectid"), _describe(p))
            if key in seen:
                continue
            seen.add(key)
            hit = {
                "vehicle_profile": g["vehicle_profile"], "leg": g["leg"],
                "kind": p.get("_kind"), "label": p.get("_label"),
                "severity": p.get("_severity"),
                "what": _describe(p),
                "distance_m": round(d * 1000.0, 1),
                "vehicle_gross_t": gross_t,
            }
            if p.get("_kind") == "bridges_weak":
                hit["nominal_load"] = p.get("nominal_load") or None
                hit["construction_year"] = p.get("construction_year")
                # the honest flag: we have a class and a weight and no mapping between them
                hit["needs_interpretation"] = True
                hit["interpretation_note"] = (
                    "nominal_load is an Estonian bridge load class, not tonnes. No "
                    "comparison against the vehicle's gross weight has been made because "
                    "the mapping has never been established — read the class.")
            hits.append(hit)

    hits.sort(key=lambda h: (h["severity"] != "warn", h["distance_m"]))
    return {
        "route_id": route_id,
        "baked": True,
        "hits": hits,
        "warn_count": len([h for h in hits if h["severity"] == "warn"]),
        "note_count": len([h for h in hits if h["severity"] != "warn"]),
        "match_m": MATCH_M,
        "layers_checked": fc.get("layers"),
        "fetch_errors": fc.get("errors") or {},
        "attribution": fc.get("attribution"),
    }


def check_all(profile=None, layers=None):
    """
    Every baked route against every restriction layer, one fetch shared across all of
    them. Returns only routes with at least one hit — a clean route is the normal case
    and listing 107 of them buries the four that matter.
    """
    fc = fetch_all(layers)
    if not fc.get("features") and fc.get("errors"):
        return {"error": "no restriction data could be fetched", "detail": fc["errors"]}
    out = {}
    for r in db.query("SELECT id FROM routes ORDER BY id"):
        res = check_route(r["id"], profile=profile, _fc=fc)
        if res.get("hits"):
            out[r["id"]] = res
    return {"routes": out, "route_count": len(out),
            "features_checked": len(fc.get("features") or []),
            "layers": fc.get("layers"), "fetch_errors": fc.get("errors") or {},
            "attribution": fc.get("attribution")}


# --------------------------------------------------------------------------- #
#  Diagnostics                                                                 #
# --------------------------------------------------------------------------- #
def diagnostics(layer=None, probe=False):
    """
    What this module would ask Tark Tee for, and with probe=true what comes back.

    ⚠️ Read this before trusting any coordinate. The live service declares WKID 4326 and
    returns L-EST97 metres (checked 2026-08-27). `sample_raw` shows the coordinate exactly
    as it arrived and `sample_converted` shows it after `projection.normalise()`, so the
    mislabelling is visible rather than asserted.
    """
    out = {
        "base": BASE,
        "layers": {k: dict(v, key=k) for k, v in LAYERS.items()},
        "match_m": MATCH_M,
        "cache_ttl_s": CACHE_TTL_S,
        "cache": cache_state(),
        "probe": None,
        "unverified": [
            "tarktee.ee is never called from the build sandbox — every assertion is "
            "against parsing, projection and matching maths, not the live service",
            "what an Estonian bridge nominal_load class ('N-13/NG-60') permits in "
            "tonnes — NO comparison is made against vehicle gross weight",
            "whether the service sets CORS headers (moot: this is proxied server-side)",
            f"whether {MATCH_M:g} m is the right matching distance against HERE's "
            "decimated polylines",
        ],
    }
    if not probe:
        return out
    key = layer if layer in LAYERS else "bridges_weak"
    p = {"layer": key}
    try:
        p["service_layers"] = _service_layers(LAYERS[key]["service"])
        q = urllib.parse.urlencode({"where": "1=1", "outFields": "*", "f": "geojson",
                                    "outSR": "4326", "resultRecordCount": 3})
        url = (f"{BASE}/{urllib.parse.quote(LAYERS[key]['service'])}/MapServer/"
               f"{p['service_layers'][0]['id']}/query?{q}")
        p["url"] = url
        raw = _get(url)
        p["declared_crs"] = raw.get("crs") or raw.get("spatialReference")
        sample = (raw.get("features") or [{}])[0]
        p["sample_raw"] = (sample.get("geometry") or {}).get("coordinates")
        p["sample_properties"] = sample.get("properties")
        fixed = _fix_geometry(sample.get("geometry"))
        p["sample_converted"] = (fixed or {}).get("coordinates")
        p["was_projected"] = bool(
            p["sample_raw"] and projection.looks_projected(
                *(p["sample_raw"] if isinstance(p["sample_raw"][0], (int, float))
                  else p["sample_raw"][0])))
        p["reading"] = (
            "service returned L-EST97 metres and this code converted them"
            if p["was_projected"] else
            "service returned degrees — it may have been fixed upstream; conversion was "
            "skipped, which is the intended behaviour")
    except Exception as e:
        p["error"] = str(e)[:400]
    out["probe"] = p
    return out


def summary():
    return {"layers": sorted(LAYERS), "cache": cache_state(), "match_m": MATCH_M}

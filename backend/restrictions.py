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
from datetime import datetime, timezone

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

#: Services exposed, in the order the map lists them.
#:
#: `dimension` is what makes a hit actionable. Every restriction layer carries a numeric
#: `restriction_limit` — metres for height and width, tonnes for mass — and every vehicle
#: in factors.json carries the matching figure. So for those three a REAL comparison is
#: possible and is made: "4.0 m limit, this vehicle is 4.0 m" is a verdict, not a hint.
#: Weak bridges are the exception and stay unjudged; see check_route().
#:
#: `colour` lives here rather than in the map so the legend cannot drift from the layer —
#: that has already been fixed once on this map. Chosen to be readable against the
#: existing palette (inbound #039E86, outbound #f59e0b, haul #C2790B, zones #BF2E55):
#:   deep maroon   structural failure          (weak bridges)
#:   bronze        weight                      (mass)
#:   violet/magenta  a dimension your vehicle either fits or does not
#:   strong red    the road is shut or ordered (traffic restriction) — the stop colour
#:   orange        diversion, the universal colour for one
LAYERS = {
    "bridges_weak": {
        "service": "bridges_weak", "label": "Weak bridges", "severity": "warn",
        "dimension": None, "unit": None, "colour": "#7F1D1D"},
    "restrictions_mass": {
        "service": "restrictions_mass", "label": "Mass limits", "severity": "warn",
        "dimension": "mass_t", "unit": "t", "colour": "#92400E"},
    "restrictions_height": {
        "service": "restrictions_height", "label": "Height limits", "severity": "warn",
        "dimension": "height_m", "unit": "m", "colour": "#7C3AED"},
    "restrictions_width": {
        "service": "restrictions_width", "label": "Width limits", "severity": "warn",
        "dimension": "width_m", "unit": "m", "colour": "#DB2777"},
    "restrictions_traffic": {
        "service": "restrictions_traffic", "label": "Traffic restrictions", "severity": "warn",
        "dimension": None, "unit": None, "colour": "#DC2626"},
    "detours": {
        "service": "detours", "label": "Diversions", "severity": "note",
        "dimension": None, "unit": None, "colour": "#EA580C"},
}

#: Estonian enum values that appear in `cause` and `effect` on the traffic layer, so the
#: UI shows "Complete closure" rather than "COMPLETE_CLOSURE". Anything not listed is
#: title-cased rather than hidden — an unknown cause is still worth reading.
_EFFECTS = {
    "COMPLETE_CLOSURE": "Road completely closed",
    "PARTIAL_CLOSURE": "Road partially closed",
    "LANE_CLOSURE": "Lane closed",
    "SPEED_LIMIT": "Speed limit in force",
    "DIVERSION": "Traffic diverted",
}
_CAUSES = {
    "CONSTRUCTION": "Construction",
    "CULVERT_REPAIRS": "Culvert repairs",
    "BRIDGE_REPAIRS": "Bridge repairs",
    "ROAD_REPAIRS": "Road repairs",
    "SIDEWALK_CONSTRUCTION": "Footway construction",
    "EVENT": "Event",
    "MAINTENANCE": "Maintenance",
}

#: How close a restriction has to be to a baked route before it is called a hit. HERE
#: geometry is a road centreline and a bridge point sits on that line, so the true
#: distance is metres. 30 m allows for the polyline being decimated between vertices
#: without picking up the parallel road on the other side of a dual carriageway.
#: Judgement, not a measured figure — same status as zones.DETOUR_PAD_KM.
MATCH_M = 30.0

_cache = {}          # key -> (fetched_at, payload)


# --------------------------------------------------------------------------- #
#  Dates — the thing that stops this feature lying                             #
# --------------------------------------------------------------------------- #
# ⚠️ THE LAYERS CONTAIN EXPIRED RECORDS. Every record in the first sample taken from the
# live service (2026-08-27) carried date_from / date_to in June–August **2017** — nine
# years stale. A roadworks closure from 2017 drawn as if it were live, or warned about on
# a route, would be worse than not having the layer at all: it is the kind of confidently
# wrong number that destroys trust in everything next to it.
#
# So dates are parsed and CURRENT-ONLY is the default everywhere. The counts of what was
# dropped are reported rather than hidden, because "312 records, 4 in force" is useful and
# an unexplained near-empty layer looks broken.
def _epoch_ms(v):
    """Tark Tee dates are epoch milliseconds. Returns a date, or None."""
    if v in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(v) / 1000.0, tz=timezone.utc).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _iso(v):
    d = _epoch_ms(v)
    return d.isoformat() if d else None


def in_force(props, on=None):
    """
    Is this restriction in force on `on` (default today)?

    A NULL at either end is open-ended, matching how zones.applies_on() treats the same
    question — so a restriction with no dates at all always applies. Both bounds
    inclusive.
    """
    today = on or datetime.now(timezone.utc).date()
    if isinstance(today, str):
        try:
            today = datetime.fromisoformat(today).date()
        except ValueError:
            today = datetime.now(timezone.utc).date()
    a, b = _epoch_ms(props.get("date_from")), _epoch_ms(props.get("date_to"))
    if a and today < a:
        return False
    if b and today > b:
        return False
    return True


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


def _limit(props):
    """
    The numeric restriction value and its unit, or (None, None).

    `restriction_limit` is a genuine number on the mass, height and width layers — 8 for
    eight tonnes, 4.9 for 4.9 metres — and the unit comes from which layer it arrived on.
    ArcGIS hands back float32 widened to double, so 4.15 arrives as 4.150000095367432;
    rounded here once rather than in three display paths.
    """
    spec = LAYERS.get(props.get("_kind")) or {}
    if not spec.get("dimension"):
        return None, None
    v = props.get("restriction_limit")
    if v in (None, ""):
        return None, None
    try:
        return round(float(v), 2), spec["unit"]
    except (TypeError, ValueError):
        return None, None


def _headline(props):
    """
    The single line that says what the restriction actually IS.

    This is the fix for "it does state the restriction, i.e. 4 m height restriction" —
    the first version showed that a height restriction was near a route without ever
    showing the number, which is the only part anyone needs.
    """
    kind = props.get("_kind")
    val, unit = _limit(props)
    if val is not None:
        return f"{val:g} {unit} {LAYERS[kind]['label'].replace(' limits', '').lower()} limit"
    if kind == "bridges_weak":
        nl = props.get("nominal_load")
        return f"Weak bridge · load class {nl}" if nl else "Weak bridge · no load class recorded"
    if kind in ("restrictions_traffic", "detours"):
        eff = props.get("effect")
        cause = props.get("cause")
        bits = [_EFFECTS.get(eff, (eff or "").replace("_", " ").capitalize() or None),
                _CAUSES.get(cause, (cause or "").replace("_", " ").capitalize() or None)]
        bits = [b for b in bits if b]
        return " · ".join(bits) if bits else (LAYERS[kind]["label"])
    return LAYERS.get(kind, {}).get("label") or "Restriction"


def _enrich(props, key, spec, layer_name):
    """Stamp everything the UI needs so no display path has to know Tark Tee's schema."""
    props["_layer"] = layer_name
    props["_kind"] = key
    props["_label"] = spec["label"]
    props["_severity"] = spec["severity"]
    props["_colour"] = spec["colour"]
    val, unit = _limit(props)
    props["_limit"] = val
    props["_unit"] = unit
    props["_headline"] = _headline(props)
    props["_from"] = _iso(props.get("date_from"))
    props["_to"] = _iso(props.get("date_to"))
    props["_in_force"] = in_force(props)
    # km_from/km_to are chainage along the numbered road, and they arrive as float32 noise
    for k in ("km_from", "km_to"):
        if isinstance(props.get(k), (int, float)):
            props[k] = round(float(props[k]), 2)
    return props


def fetch_layer(key, current_only=True):
    """
    One restriction layer as a WGS84 GeoJSON FeatureCollection.

    Every layer in the service is queried and the results concatenated, with the source
    layer name carried on each feature so a points hit and a lines hit stay
    distinguishable downstream.

    ⚠️ `current_only` defaults True and drops records whose date window has passed. See
    the note above in_force(): the live layers carry records from 2017. The full and
    filtered counts are both reported so a near-empty layer reads as "nothing in force
    today", not as a broken fetch.
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
                props = _enrich(dict(f.get("properties") or {}), key, spec, lyr["name"])
                feats.append({"type": "Feature", "properties": props, "geometry": geom})
        return {"type": "FeatureCollection", "features": feats,
                "layer": key, "label": spec["label"], "severity": spec["severity"],
                "colour": spec["colour"],
                "source": "Estonian Transport Administration — Tark Tee",
                "fetched_layers": len(_service_layers(service)),
                "errors": errors}

    fc = _cached(f"lyr:{key}", CACHE_TTL_S, build)
    if fc.get("error") or not current_only:
        return fc
    live = [f for f in fc["features"] if f["properties"].get("_in_force")]
    out = dict(fc)
    out["features"] = live
    out["total_records"] = len(fc["features"])
    out["expired_or_future"] = len(fc["features"]) - len(live)
    return out


def fetch_all(keys=None, current_only=True):
    keys = [k for k in (keys or list(LAYERS)) if k in LAYERS]
    out, errors, counts = [], {}, {}
    total, dropped = 0, 0
    for k in keys:
        try:
            fc = fetch_layer(k, current_only=current_only)
            if fc.get("error"):
                errors[k] = fc["error"]
                continue
            out.extend(fc["features"])
            counts[k] = len(fc["features"])
            total += fc.get("total_records", len(fc["features"]))
            dropped += fc.get("expired_or_future", 0)
            if fc.get("errors"):
                errors[k] = fc["errors"]
        except Exception as e:
            errors[k] = str(e)[:200]
    return {"type": "FeatureCollection", "features": out,
            "layers": keys, "errors": errors, "counts": counts,
            "current_only": current_only,
            "total_records": total,
            # surfaced, not swallowed: the layers carry a lot of 2017
            "expired_or_future": dropped,
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


def vehicle_dimensions(profile):
    """
    The vehicle figures a Tark Tee limit can actually be compared against.

    mass_t / height_m / width_m, keyed to match LAYERS[...]["dimension"], so adding a
    layer needs no change here. Height and width are the same laden or unladen; mass is
    the laden gross weight, which is the case a limit bites on.
    """
    try:
        import conversions
        v = (conversions.load_factors().get("vehicles", {}) or {}).get(profile, {}) or {}
        r = v.get("routing", {}) or {}
    except Exception:
        return {}
    out = {}
    if r.get("gross_weight_kg"):
        out["mass_t"] = round(float(r["gross_weight_kg"]) / 1000.0, 1)
    elif v.get("gvw_t"):
        out["mass_t"] = float(v["gvw_t"])
    if r.get("height_cm"):
        out["height_m"] = round(float(r["height_cm"]) / 100.0, 2)
    if r.get("width_cm"):
        out["width_m"] = round(float(r["width_cm"]) / 100.0, 2)
    return out


def assess(props, profile):
    """
    Does this vehicle breach this restriction? A real verdict where one is possible.

    Returns {"verdict", "limit", "unit", "vehicle", "dimension", "note"} where verdict is:

        "exceeds"    the vehicle is over the posted limit. A real comparison of two
                     numbers in the same unit — 44 t against an 8 t limit, 4.0 m against
                     a 3.2 m headroom.
        "within"     under the limit, with the margin available in `margin`.
        "unknown"    ⚠️ no comparison was possible, and the reason is in `note`. This is
                     what a weak bridge always returns: `nominal_load` is an Estonian
                     load CLASS ("N-13/NG-60"), not tonnes, and nobody has established
                     what it permits. It is also what a closure returns, because a
                     closure is not a dimension.

    The distinction matters more than the convenience of a single answer. Mass, height and
    width are two numbers in the same unit and deserve a verdict; a bridge load class is a
    domain question and inventing a threshold for it would produce a confident green tick
    on a bridge a 44 t artic cannot cross.
    """
    kind = props.get("_kind")
    spec = LAYERS.get(kind) or {}
    dim = spec.get("dimension")
    limit, unit = _limit(props)
    dims = vehicle_dimensions(profile)
    have = dims.get(dim) if dim else None

    if dim and limit is not None and have is not None:
        return {"verdict": "exceeds" if have > limit else "within",
                "dimension": dim, "limit": limit, "unit": unit, "vehicle": have,
                "margin": round(limit - have, 2), "note": None}

    if kind == "bridges_weak":
        return {"verdict": "unknown", "dimension": None,
                "limit": props.get("nominal_load") or None, "unit": "load class",
                "vehicle": dims.get("mass_t"), "margin": None,
                "note": "nominal_load is an Estonian bridge load class, not tonnes. No "
                        "comparison against the vehicle's gross weight has been made "
                        "because the mapping has never been established — read the class."}

    if dim and limit is None:
        return {"verdict": "unknown", "dimension": dim, "limit": None, "unit": unit,
                "vehicle": have, "margin": None,
                "note": "the source record carries no restriction_limit value"}
    if dim and have is None:
        return {"verdict": "unknown", "dimension": dim, "limit": limit, "unit": unit,
                "vehicle": None, "margin": None,
                "note": f"factors.json has no {dim} for this vehicle profile"}

    return {"verdict": "unknown", "dimension": None, "limit": None, "unit": None,
            "vehicle": None, "margin": None,
            "note": "not a dimension limit — read the description"}


def _describe(props):
    """
    One human line for a restriction: what it is, and where.

    Leads with the VALUE — "4 m height limit" — because that is the only part anybody
    needs at a glance, and the first version of this buried it entirely.
    """
    base = props.get("_headline") or props.get("_label") or "Restriction"
    if props.get("_kind") == "bridges_weak" and props.get("bridge_name"):
        base = f"{props['bridge_name']} · {base}"
    road = props.get("road_name") or (f"road {props['road_nr']}" if props.get("road_nr") else None)
    if road and str(road) not in base:
        base = f"{base} — {road}"
    a, b = props.get("km_from"), props.get("km_to")
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and (a or b):
        base += f" (km {a:g}–{b:g})"
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
        "SELECT * FROM route_geometry WHERE tenant_id = ? AND route_id = ? AND alt_index = 0 "
        "AND geometry IS NOT NULL" + (" AND vehicle_profile = ?" if profile else ""),
        # tenant is the first placeholder in both branches, so it leads the tuple
        (db.current_tenant(), route_id, profile) if profile
        else (db.current_tenant(), route_id))
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
        gross_t = vehicle_dimensions(g["vehicle_profile"]).get("mass_t")
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
            a = assess(p, g["vehicle_profile"])
            hit = {
                "vehicle_profile": g["vehicle_profile"], "leg": g["leg"],
                "kind": p.get("_kind"), "label": p.get("_label"),
                "colour": p.get("_colour"),
                "what": _describe(p),
                "headline": p.get("_headline"),
                "distance_m": round(d * 1000.0, 1),
                "road_name": p.get("road_name"), "road_nr": p.get("road_nr"),
                "extra_info": p.get("extra_info") or None,
                "from": p.get("_from"), "to": p.get("_to"),
                "links": p.get("links") or None,
                # the actual numbers, and the verdict where one is possible
                "limit": a["limit"], "unit": a["unit"],
                "vehicle_value": a["vehicle"], "dimension": a["dimension"],
                "verdict": a["verdict"], "margin": a["margin"],
                "note": a["note"],
                "needs_interpretation": a["verdict"] == "unknown",
                "vehicle_gross_t": gross_t,
            }
            # a breach outranks the layer's own severity: a 3.2 m headroom on a 4.0 m
            # vehicle is not a "note" whatever kind of layer it arrived on
            hit["severity"] = ("breach" if a["verdict"] == "exceeds"
                               else p.get("_severity") or "note")
            if p.get("_kind") == "bridges_weak":
                hit["nominal_load"] = p.get("nominal_load") or None
                hit["construction_year"] = p.get("construction_year")
            if p.get("_kind") in ("restrictions_traffic", "detours"):
                hit["cause"] = _CAUSES.get(p.get("cause"), p.get("cause"))
                hit["effect"] = _EFFECTS.get(p.get("effect"), p.get("effect"))
                hit["detour_comment"] = p.get("detour_comment") or None
                hit["contact"] = (p.get("traffic_ctrl_organization")
                                  or p.get("contractor_organization") or None)
                hit["contact_phone"] = (p.get("traffic_ctrl_contact_phone")
                                        or p.get("contractor_contact_phone") or None)
            hits.append(hit)

    _order = {"breach": 0, "warn": 1, "note": 2}
    hits.sort(key=lambda h: (_order.get(h["severity"], 3), h["distance_m"]))
    return {
        "route_id": route_id,
        "baked": True,
        "hits": hits,
        # breaches are counted separately because they are the only ones that are a
        # statement of fact rather than something to go and read
        "breach_count": len([h for h in hits if h["severity"] == "breach"]),
        "warn_count": len([h for h in hits if h["severity"] == "warn"]),
        "note_count": len([h for h in hits if h["severity"] == "note"]),
        "unjudged_count": len([h for h in hits if h["verdict"] == "unknown"]),
        "match_m": MATCH_M,
        "layers_checked": fc.get("layers"),
        "current_only": fc.get("current_only", True),
        "expired_or_future_excluded": fc.get("expired_or_future", 0),
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
    for r in db.query("SELECT id FROM routes WHERE tenant_id = ? ORDER BY id",
                      (db.current_tenant(),)):
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

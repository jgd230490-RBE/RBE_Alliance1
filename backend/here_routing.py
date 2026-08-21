"""
Server-side HERE truck routing for RBE Alliance 1.

Given an origin, destination and a vehicle profile, calls HERE's Routing API v8
with the vehicle's HGV dimensions and returns decoded geometry + distance + time.
Dependency-free: uses urllib for HTTP and an inlined HERE flexible-polyline
decoder (verified against HERE's official decoder during the spike).

The HERE API key comes from the HERE_API_KEY environment variable and never
leaves the backend.
"""
import os
import json
import urllib.request
import urllib.parse
import urllib.error

import flexpolyline as fp   # HERE's official flexible-polyline decoder

import conversions

HERE_ENDPOINT = "https://router.hereapi.com/v8/routes"


def api_key():
    return os.environ.get("HERE_API_KEY", "").strip()


def configured():
    return bool(api_key())


def decode_polyline(enc):
    """Decode a HERE flexible polyline to a list of [lon, lat] pairs."""
    return [[round(lng, 6), round(lat, 6)] for (lat, lng, *_) in fp.decode(enc)]


# --------------------------------------------------------------------------- #
#  Vehicle profile -> HERE truck parameters                                    #
# --------------------------------------------------------------------------- #
def tare_weight_kg(profile, factors=None):
    """
    Unladen weight of the vehicle: gross vehicle weight minus typical payload.

    This is what makes a return leg worth routing separately. HERE picks roads partly
    on vehicle[grossWeight], so an Artic Tipper going home empty at ~15t may legally use
    roads it is barred from at its laden 44t. Falls back to the laden figure if the
    factors don't carry enough detail to work a tare out.
    """
    factors = factors or conversions.load_factors()
    v = (factors.get("vehicles", {}) or {}).get(profile, {})
    gvw_t, payload_t = v.get("gvw_t"), v.get("payload_t")
    if gvw_t and payload_t and gvw_t > payload_t:
        return int(round((gvw_t - payload_t) * 1000))
    return int((v.get("routing", {}) or {}).get("gross_weight_kg") or 0)


def _truck_params(profile, factors=None, laden=True):
    """
    Build HERE truck query params from a vehicle's routing dimensions.

    Dimensions (height/width/length/axles) are the same whether or not the vehicle is
    loaded — only the gross weight changes.
    """
    factors = factors or conversions.load_factors()
    v = (factors.get("vehicles", {}) or {}).get(profile, {})
    r = v.get("routing", {})
    p = {"transportMode": "truck"}
    gross = int(r["gross_weight_kg"]) if r.get("gross_weight_kg") else 0
    if not laden:
        gross = tare_weight_kg(profile, factors) or gross
    if gross:
        p["vehicle[grossWeight]"] = gross
    if r.get("height_cm"):
        p["vehicle[height]"] = int(r["height_cm"])
    if r.get("width_cm"):
        p["vehicle[width]"] = int(r["width_cm"])
    if r.get("length_cm"):
        p["vehicle[length]"] = int(r["length_cm"])
    if r.get("axle_count"):
        p["vehicle[axleCount]"] = int(r["axle_count"])
    return p


def routes(o_lat, o_lon, d_lat, d_lon, profile, factors=None, avoid_areas=None,
           alternatives=1, laden=True):
    """
    Route origin -> destination for a vehicle profile, returning up to `alternatives`
    options ranked best-first.

    Returns a list of {"geometry": [[lon,lat],...], "distance_km": float,
    "duration_hr": float} — always at least one entry, or RuntimeError.

    One HTTP call covers every alternative, so asking for 3 costs the same as asking
    for 1. Set laden=False for the unladen return leg.
    """
    key = api_key()
    if not key:
        raise RuntimeError("HERE_API_KEY is not set")
    params = {
        "origin": f"{o_lat},{o_lon}",
        "destination": f"{d_lat},{d_lon}",
        "return": "polyline,summary",
        "apiKey": key,
    }
    # HERE counts alternatives *in addition to* the optimal route, so asking for
    # 3 options means alternatives=2. It also silently ignores the parameter when
    # the request carries via-waypoints — relevant once haul roads land (Phase 4).
    want = max(1, int(alternatives or 1))
    if want > 1:
        params["alternatives"] = want - 1
    params.update(_truck_params(profile, factors, laden=laden))
    if avoid_areas:  # list of "bbox:west,south,east,north" strings
        params["avoid[areas]"] = "|".join(avoid_areas)
    url = HERE_ENDPOINT + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:300]}")
    except Exception as e:
        raise RuntimeError(str(e))
    if not data.get("routes"):
        raise RuntimeError("no route returned: " + json.dumps(data)[:200])

    out = []
    for rt in data["routes"][:want]:
        secs = rt.get("sections") or []
        if not secs:
            continue
        # a truck route can come back in several sections; concatenate them so the
        # caller always gets one continuous line per alternative
        coords, dist_m, dur_s = [], 0.0, 0.0
        for sec in secs:
            pts = decode_polyline(sec["polyline"])
            coords.extend(pts[1:] if coords else pts)
            summ = sec.get("summary") or {}
            dist_m += summ.get("length", 0)
            dur_s += summ.get("duration", 0)
        out.append({
            "geometry": coords,
            "distance_km": round(dist_m / 1000.0, 2),
            "duration_hr": round(dur_s / 3600.0, 3),
        })
    if not out:
        raise RuntimeError("route had no usable sections: " + json.dumps(data)[:200])
    return out


def route(o_lat, o_lon, d_lat, d_lon, profile, factors=None, avoid_areas=None):
    """Single best route — thin wrapper over routes() for callers that want just one."""
    return routes(o_lat, o_lon, d_lat, d_lon, profile, factors,
                  avoid_areas=avoid_areas, alternatives=1)[0]

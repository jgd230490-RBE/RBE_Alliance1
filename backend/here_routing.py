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
def _truck_params(profile, factors=None):
    """Build HERE truck query params from a vehicle's routing dimensions."""
    factors = factors or conversions.load_factors()
    v = (factors.get("vehicles", {}) or {}).get(profile, {})
    r = v.get("routing", {})
    p = {"transportMode": "truck"}
    if r.get("gross_weight_kg"):
        p["vehicle[grossWeight]"] = int(r["gross_weight_kg"])
    if r.get("height_cm"):
        p["vehicle[height]"] = int(r["height_cm"])
    if r.get("width_cm"):
        p["vehicle[width]"] = int(r["width_cm"])
    if r.get("length_cm"):
        p["vehicle[length]"] = int(r["length_cm"])
    if r.get("axle_count"):
        p["vehicle[axleCount]"] = int(r["axle_count"])
    return p


def route(o_lat, o_lon, d_lat, d_lon, profile, factors=None, avoid_areas=None):
    """
    Route origin -> destination for a vehicle profile.
    Returns {"geometry": [[lon,lat],...], "distance_km": float, "duration_hr": float}.
    Raises RuntimeError on any HERE error (caller records it against the route).
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
    params.update(_truck_params(profile, factors))
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
    sec = data["routes"][0]["sections"][0]
    summ = sec["summary"]
    return {
        "geometry": decode_polyline(sec["polyline"]),
        "distance_km": round(summ["length"] / 1000.0, 2),
        "duration_hr": round(summ["duration"] / 3600.0, 3),
    }

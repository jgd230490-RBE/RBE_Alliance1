"""
Phase 2.5a — Google Street View imagery for locations and gates.

Mapbox has no street-level imagery, so this is Google Maps Platform: a separate product,
a separate key and separate billing from HERE. It is proxied through the backend for the
same reason `HERE_API_KEY` never leaves it — a key in `frontend/index.html` is a key in
view-source, and this deployment already has enough of that problem
(see the `LOGINS` note in claude/roadmap.md).

THE COST MODEL, AND THE ONE TRICK THAT MATTERS
----------------------------------------------
Checked against Google's documentation 2026-08-27:

  * **Metadata requests are free.** Verbatim: "Street View Static API metadata requests
    are available at no charge. No quota is consumed when you request metadata." Quota is
    consumed only when an image is loaded.
  * The universal $200 monthly credit is gone, replaced by per-SKU free tiers.

So every lookup here checks metadata FIRST and only ever requests an image for a location
Google actually has imagery for. A quarry down a private track costs nothing and shows
nothing, instead of spending a paid request to receive a grey "no imagery" tile. Metadata
results are cached hard — whether a panorama exists at a fixed gate is not news.

⚠️ **Exact per-1000 pricing was NOT pinned down** and is deliberately not quoted anywhere
in this codebase. Google's rates have changed twice recently and a stale figure in a
comment is worse than none. `/api/admin/diagnostics/streetview` reports how many image
requests this deployment has actually made since boot, which is the number worth watching;
read the current rate off Google's own pricing page.

LICENSING
---------
Imagery is served through the proxy and never stored. Google's terms restrict caching and
storing Maps content — so nothing here writes an image to disk or to the database, and the
proxy sets a short cache header only. If that ever needs to change, read the License
Restrictions section of the Maps Platform Terms first; do not assume.
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error

STATIC_ENDPOINT = "https://maps.googleapis.com/maps/api/streetview"
META_ENDPOINT = "https://maps.googleapis.com/maps/api/streetview/metadata"

TIMEOUT_S = 15
#: Whether a panorama exists at a fixed point changes when Google redrives the road —
#: months, not minutes. A week keeps the free metadata calls near zero without pinning a
#: stale "no imagery" answer for ever.
META_TTL_S = 7 * 86400
#: How far Google may wander to find a panorama. 50 m keeps the view of a gate rather than
#: jumping to the next junction; a location with nothing within 50 m genuinely has no
#: useful street view.
DEFAULT_RADIUS_M = 50

_meta_cache = {}
_stats = {"meta_calls": 0, "image_calls": 0, "meta_cache_hits": 0, "started": time.time()}


def api_key():
    return os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()


def signing_secret():
    return os.environ.get("GOOGLE_MAPS_URL_SIGNING_SECRET", "").strip()


def configured():
    return bool(api_key())


def _sign(url):
    """
    Append Google's URL signature.

    Optional but recommended by Google, and worth having: an unsigned key scraped from
    anywhere can be used by anyone until the quota is gone. Unset the secret and requests
    still work — the signature is added only when there is one, so a deployment that has
    not set it degrades rather than breaks.
    """
    secret = signing_secret()
    if not secret:
        return url
    parsed = urllib.parse.urlparse(url)
    to_sign = f"{parsed.path}?{parsed.query}".encode("utf-8")
    key = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    sig = base64.urlsafe_b64encode(hmac.new(key, to_sign, hashlib.sha1).digest())
    return f"{url}&signature={sig.decode('utf-8')}"


def _round_key(lat, lon):
    # ~1 m at Estonian latitudes. Two lookups of the same gate should share a cache entry
    # even if one came from the node coordinate and one from a drag that moved it 30 cm.
    return (round(float(lat), 5), round(float(lon), 5))


def metadata(lat, lon, radius=DEFAULT_RADIUS_M):
    """
    Does Google have imagery near this point? FREE — no quota is consumed.

    Returns {"available": bool, "status": str, ...}. Never raises: a failure is an answer,
    and the caller's job is to show no thumbnail rather than an error.
    """
    if not configured():
        return {"available": False, "status": "NO_KEY",
                "error": "GOOGLE_MAPS_API_KEY is not set on the server"}
    ck = _round_key(lat, lon) + (int(radius),)
    hit = _meta_cache.get(ck)
    if hit and (time.time() - hit[0]) < META_TTL_S:
        _stats["meta_cache_hits"] += 1
        return hit[1]

    q = urllib.parse.urlencode({"location": f"{lat},{lon}", "radius": int(radius),
                                "key": api_key()})
    try:
        _stats["meta_calls"] += 1
        with urllib.request.urlopen(_sign(f"{META_ENDPOINT}?{q}"), timeout=TIMEOUT_S) as r:
            data = json.load(r)
    except Exception as e:
        # not cached: a transient network failure should not pin "no imagery" for a week
        return {"available": False, "status": "FETCH_FAILED", "error": str(e)[:200]}

    status = data.get("status")
    out = {
        "available": status == "OK",
        "status": status,
        "date": data.get("date"),              # e.g. "2024-06" — how old the imagery is
        "pano_id": data.get("pano_id"),
        "copyright": data.get("copyright"),
        # how far Google had to move to find a panorama: a big number means the picture is
        # of somewhere else, which matters for a gate on a long approach road
        "pano_location": data.get("location"),
    }
    loc = data.get("location") or {}
    if out["available"] and loc.get("lat") is not None:
        try:
            import zones
            out["offset_m"] = round(zones.haversine_km(
                [float(lon), float(lat)], [float(loc["lng"]), float(loc["lat"])]) * 1000.0, 1)
        except Exception:
            pass
    _meta_cache[ck] = (time.time(), out)
    return out


def image_url(lat, lon, size="480x300", heading=None, pitch=0, fov=80,
              radius=DEFAULT_RADIUS_M):
    """
    The signed Street View Static URL for a point. **Costs a paid request when fetched.**

    `heading` is left off by default on purpose: with none set Google points the camera at
    the requested coordinate, which is exactly what is wanted for a gate seen from the
    road. Pass one only to override that.
    """
    params = {"location": f"{lat},{lon}", "size": size, "fov": int(fov),
              "pitch": int(pitch), "radius": int(radius),
              "return_error_code": "true", "key": api_key()}
    if heading is not None:
        params["heading"] = int(heading)
    return _sign(f"{STATIC_ENDPOINT}?{urllib.parse.urlencode(params)}")


def fetch_image(lat, lon, **kw):
    """
    (bytes, content_type) for a Street View still, or (None, error).

    Checks metadata first — free — and does not spend a paid image request when Google has
    nothing to show. That check is the whole reason this is a server-side proxy rather
    than an <img> tag pointed at Google.
    """
    if not configured():
        return None, "GOOGLE_MAPS_API_KEY is not set on the server"
    meta = metadata(lat, lon, kw.get("radius", DEFAULT_RADIUS_M))
    if not meta.get("available"):
        return None, f"no imagery: {meta.get('status')}"
    try:
        _stats["image_calls"] += 1
        with urllib.request.urlopen(image_url(lat, lon, **kw), timeout=TIMEOUT_S) as r:
            return r.read(), r.headers.get("Content-Type", "image/jpeg")
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
    except Exception as e:
        return None, str(e)[:200]


def stats():
    up = time.time() - _stats["started"]
    return dict(_stats, uptime_s=round(up),
                meta_cached=len(_meta_cache),
                # the number to watch: metadata is free, images are not
                billable_calls=_stats["image_calls"])


def diagnostics(lat=None, lon=None, probe=False):
    """
    Whether Street View is wired up, and with probe=true what Google says about one point.

    The probe spends NO paid request — it calls metadata only, which is free. To see an
    actual image, open /api/streetview?lat=…&lon=… in a browser, and know that doing so
    costs one billable request.
    """
    out = {
        "configured": configured(),
        "url_signing": bool(signing_secret()),
        "default_radius_m": DEFAULT_RADIUS_M,
        "metadata_ttl_s": META_TTL_S,
        "stats": stats(),
        "notes": [
            "metadata requests are free and consume no quota — images do",
            "the API key is held server-side and never sent to the browser",
            "imagery is proxied and never stored; Google's terms restrict caching",
        ],
        "unverified": [
            "current per-1000 image pricing — deliberately not quoted in code, read it "
            "off Google's pricing page; the $200 universal credit no longer exists",
            "whether 50 m is the right search radius for a rural quarry gate",
        ],
        "probe": None,
    }
    if probe and lat is not None and lon is not None:
        out["probe"] = {"lat": lat, "lon": lon, "metadata": metadata(lat, lon),
                        "cost": "none — metadata only"}
    return out

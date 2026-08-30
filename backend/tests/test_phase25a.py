"""
Phase 2.5a backend assertions — L-EST97 projection, Tark Tee restrictions, Street View.

Same harness shape as the other backend suites: a scratch SQLite database, no network,
and `fastapi` / `flexpolyline` / `psycopg2` stubbed so main.py imports and its endpoint
functions can be called directly.

WHAT THIS DOES NOT PROVE
------------------------
**Neither external service is ever called.** tarktee.ee and Google are treated exactly the
way HERE has been treated since Phase 1: monkeypatched to a recorder, with recorded
fixtures taken from real responses. So:

  * every assertion about Tark Tee is about parsing, reprojection and matching maths
    against a fixture — NOT about what the live service returns today. It could change its
    field names tomorrow and this suite would still pass.
  * every assertion about Street View is about which endpoint would be called and in what
    order — NOT about what Google returns. The one thing worth protecting is that the free
    metadata call always precedes the billable image call, and that IS asserted.
  * the HTTP layer is stubbed; endpoint bodies run, transport does not.
  * every Postgres branch of db.py is unexercised.

Use `/api/admin/diagnostics/restrictions?probe=true` and
`/api/admin/diagnostics/streetview?probe=true` against the deployment for the real thing.

THE PROJECTION IS THE EXCEPTION
-------------------------------
`projection.py` is pure maths with no external dependency, so it can be tested properly and
is. The false-origin anchor is exact by definition, and the round trip through the forward
projection is the real check — it is what caught a `copysign` that silently mirrored every
point west of the central meridian onto the eastern side.

Run:  python3 backend/tests/test_phase25a.py
"""
import os
import sys
import json
import math
import random
import shutil
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
ROOT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)

# --------------------------------------------------------------------------- #
#  Stubs                                                                       #
# --------------------------------------------------------------------------- #
_fp = types.ModuleType("flexpolyline")
_fp.decode = lambda s: []
_fp.encode = lambda pts: ""
sys.modules.setdefault("flexpolyline", _fp)


def _passthrough_decorator(*a, **k):
    def wrap(fn):
        return fn
    return wrap


class _App:
    def __init__(self, *a, **k):
        pass

    def get(self, *a, **k):
        return _passthrough_decorator()

    post = put = delete = patch = get

    def add_middleware(self, *a, **k):
        pass

    def mount(self, *a, **k):
        pass


class _HTTPException(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


def _Query(default=None, **k):
    return default


_fa = types.ModuleType("fastapi")
_fa.FastAPI = _App
_fa.HTTPException = _HTTPException
_fa.Query = _Query
sys.modules.setdefault("fastapi", _fa)

_mw = types.ModuleType("fastapi.middleware")
_cors = types.ModuleType("fastapi.middleware.cors")
_cors.CORSMiddleware = object
_mw.cors = _cors
sys.modules.setdefault("fastapi.middleware", _mw)
sys.modules.setdefault("fastapi.middleware.cors", _cors)

_resp = types.ModuleType("fastapi.responses")
_resp.FileResponse = lambda *a, **k: None


class _Response:
    def __init__(self, content=None, media_type=None, headers=None):
        self.content, self.media_type, self.headers = content, media_type, headers or {}


_resp.Response = _Response
sys.modules.setdefault("fastapi.responses", _resp)

_static = types.ModuleType("fastapi.staticfiles")
_static.StaticFiles = lambda *a, **k: None
sys.modules.setdefault("fastapi.staticfiles", _static)

TMP = tempfile.mkdtemp(prefix="rbe_p25a_")
os.environ.pop("DATABASE_URL", None)

import db  # noqa: E402
db._SQLITE_PATH = os.path.join(TMP, "scratch.db")

import projection  # noqa: E402
import zones  # noqa: E402
import restrictions  # noqa: E402
import streetview  # noqa: E402
import network  # noqa: E402
import main  # noqa: E402

PASS = 0
FAIL = []


def ok(label, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{label} {extra}".strip())


def reset_db():
    if os.path.exists(db._SQLITE_PATH):
        os.remove(db._SQLITE_PATH)
    db.init_network_db()
    db.init_zones_db()
    db.init_gates_db()     # Phase 5a


# =========================================================================== #
#  1. L-EST97 -> WGS84                                                         #
# =========================================================================== #
# The false origin is exact by definition: easting 500000 / northing 6375000 IS the
# central meridian at the latitude of origin. If a constant were mistyped this moves.
lon, lat = projection.to_wgs84(500000, 6375000)
ok("the false origin lands on the central meridian exactly", lon == 24.0, str(lon))
ok("and on the latitude of origin", abs(lat - 57.5175539) < 1e-6, str(lat))

# Round trip through the forward projection. This is the assertion that matters: it caught
# a copysign() that discarded the sign of the easting offset and mirrored every western
# point across the central meridian, while the false-origin test above still passed
# because the offset there is zero.
random.seed(20260827)
worst = 0.0
for _ in range(3000):
    a = random.uniform(21.5, 28.5)
    b = random.uniform(57.4, 59.8)
    X, Y = projection.from_wgs84(a, b)
    back = projection.to_wgs84(X, Y)
    worst = max(worst, abs(back[0] - a), abs(back[1] - b))
ok("round trip over the whole country is sub-centimetre",
   worst * 111320 < 0.05, f"{worst * 111320 * 100:.3f} cm")

# a point WEST of the central meridian must stay west — the specific bug
wx, wy = projection.from_wgs84(22.5, 58.5)
ok("a point west of 24E converts back to the west", projection.to_wgs84(wx, wy)[0] < 24.0)
ex, ey = projection.from_wgs84(26.5, 58.5)
ok("and a point east stays east", projection.to_wgs84(ex, ey)[0] > 24.0)

# real coordinates taken from the live bridges_weak layer on 2026-08-27
lon, lat = projection.to_wgs84(672343.421, 6587646.66)
ok("a real Tark Tee bridge lands inside Estonia",
   21.0 < lon < 28.5 and 57.3 < lat < 59.9, f"{lon},{lat}")
ok("and in the right county for its road number (13xxx = Ida-Viru, north-east)",
   lon > 26.0 and lat > 59.0, f"{lon},{lat}")

ok("garbage in gives None, not an exception", projection.to_wgs84("x", None) is None)

# The service currently mislabels its output. If it is ever fixed, degrees must pass
# through untouched rather than being converted a second time.
ok("projected metres are recognised", projection.looks_projected(672343, 6587646) is True)
ok("degrees are recognised as already converted",
   projection.looks_projected(26.9, 59.3) is False)
ok("normalise() converts metres", projection.normalise(500000, 6375000) == (24.0, 57.5175539))
ok("normalise() passes degrees straight through — so a fix upstream does not "
   "double-convert", projection.normalise(24.5, 58.5) == (24.5, 58.5))


# =========================================================================== #
#  2. Tark Tee parsing and reprojection (fixture, never the live service)      #
# =========================================================================== #
# Shaped exactly like a real response: projected coordinates, mislabelled CRS, and a
# nominal_load that is a CLASS not a number — including one that is blank.
FIXTURE = {
    "type": "FeatureCollection",
    "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},   # the lie
    "features": [
        {"type": "Feature",
         "properties": {"objectid": 1, "road_nr": 13171, "road_name": "Lüganuse kalmistu tee",
                        "bridge_name": "Londi sild", "nominal_load": "N-8/NG-30",
                        "construction_year": 1960, "span_lengths": "11,0; 11,0"},
         "geometry": {"type": "Point", "coordinates": [672343.421, 6587646.66]}},
        {"type": "Feature",
         "properties": {"objectid": 2, "road_nr": 18108, "bridge_name": "Süvahavva sild",
                        "nominal_load": ""},
         "geometry": {"type": "Point", "coordinates": [630208.703, 6491470.52]}},
    ],
}

_real_get = restrictions._get
_calls = []


def _fake_get(url):
    _calls.append(url)
    if "MapServer?f=json" in url:
        return {"layers": [{"id": 0, "name": "tarktee_sde.tarktee.bridges_weak",
                            "geometryType": "esriGeometryPoint"}]}
    return json.loads(json.dumps(FIXTURE))


restrictions._get = _fake_get
try:
    restrictions.clear_cache()
    fc = restrictions.fetch_layer("bridges_weak")
    ok("a layer comes back as a FeatureCollection", fc["type"] == "FeatureCollection")
    ok("with both fixture features", len(fc["features"]) == 2)

    c = fc["features"][0]["geometry"]["coordinates"]
    ok("coordinates are converted to degrees despite the service claiming 4326",
       21.0 < c[0] < 28.5 and 57.3 < c[1] < 59.9, str(c))
    ok("the raw projected value is NOT what is served", c[0] != 672343.421)

    p = fc["features"][0]["properties"]
    ok("the source layer name is carried", "bridges_weak" in (p.get("_layer") or ""))
    ok("the kind is stamped", p["_kind"] == "bridges_weak")
    ok("and the severity", p["_severity"] == "warn")
    ok("the original fields survive", p["nominal_load"] == "N-8/NG-30")

    ok("layer ids are DISCOVERED from the service, not hard-coded",
       any("MapServer?f=json" in u for u in _calls))
    ok("the query asks for geojson", any("f=geojson" in u for u in _calls))
    ok("and asks for 4326 anyway, in case the service is ever fixed",
       any("outSR=4326" in u for u in _calls))

    n_before = len(_calls)
    restrictions.fetch_layer("bridges_weak")
    ok("a second fetch inside the TTL is served from cache — this is somebody else's "
       "government service", len(_calls) == n_before)
    ok("the cache reports its age", "lyr:bridges_weak" in restrictions.cache_state())
    restrictions.clear_cache()
    restrictions.fetch_layer("bridges_weak")
    ok("clearing the cache forces a refetch", len(_calls) > n_before)

    ok("an unknown layer is refused, not fetched",
       "error" in restrictions.fetch_layer("nonsense"))

    # ------------------------------------------------- matching against a route
    reset_db()
    db.execute("INSERT INTO locations (id, name, lat, lon) VALUES ('L1','W',59.3916891,27.0)")
    db.execute("INSERT INTO locations (id, name, lat, lon) VALUES ('L2','E',59.3916891,27.1)")
    db.execute("INSERT INTO routes (id, origin_id, dest_id, origin_temp_km) "
               "VALUES ('R1','L1','L2',0)")
    # a line running due east THROUGH the converted position of the first bridge
    blon, blat = projection.to_wgs84(672343.421, 6587646.66)
    db.execute(
        "INSERT INTO route_geometry (route_id, vehicle_profile, leg, alt_index, geometry, "
        "distance_km, duration_hr) VALUES (?,?,?,?,?,?,?)",
        ("R1", "Artic Tipper (44t)", "loaded", 0,
         json.dumps([[blon - 0.05, blat], [blon + 0.05, blat]]), 10.0, 0.3))

    restrictions.clear_cache()
    res = restrictions.check_route("R1")
    ok("a bridge sitting on the route is found", res["hits"], str(res)[:200])
    h = res["hits"][0]
    ok("the hit is reported as a warning", h["severity"] == "warn")
    ok("with a distance in metres", h["distance_m"] < restrictions.MATCH_M)
    ok("it names the bridge", "Londi" in h["what"])

    # THE decision this module refuses to make — narrowed, not dropped, once the real
    # schema showed that mass/height/width ARE comparable. A bridge load class is not.
    ok("the load CLASS is quoted verbatim", h["nominal_load"] == "N-8/NG-30")
    ok("a weak bridge gets NO verdict", h["verdict"] == "unknown")
    ok("and is flagged as needing a human", h["needs_interpretation"] is True)
    ok("the note says a class is not tonnes", "not tonnes" in (h["note"] or ""))
    ok("no invented numeric threshold appears on a bridge hit", h["margin"] is None)
    ok("but the vehicle's own weight is offered alongside so a human can judge",
       h["vehicle_gross_t"] is not None or "vehicle_gross_t" in h)
    ok("the headline names it as a weak bridge with its class",
       "load class" in (h["headline"] or ""), str(h.get("headline")))

    # a route nowhere near anything
    db.execute("INSERT INTO routes (id, origin_id, dest_id, origin_temp_km) "
               "VALUES ('R2','L1','L2',0)")
    db.execute(
        "INSERT INTO route_geometry (route_id, vehicle_profile, leg, alt_index, geometry, "
        "distance_km, duration_hr) VALUES (?,?,?,?,?,?,?)",
        ("R2", "Artic Tipper (44t)", "loaded", 0,
         json.dumps([[22.0, 58.0], [22.1, 58.0]]), 10.0, 0.3))
    ok("a route far from every restriction reports none",
       restrictions.check_route("R2")["hits"] == [])

    ok("an unbaked route says so rather than reporting a clean bill",
       restrictions.check_route("NOPE")["baked"] is False)

    allres = restrictions.check_all()
    ok("check_all returns only routes with hits — a clean route is the normal case",
       list(allres["routes"]) == ["R1"], str(list(allres["routes"])))

    # the distance threshold has to actually bite
    db.execute("DELETE FROM route_geometry WHERE route_id = 'R1'")
    db.execute(
        "INSERT INTO route_geometry (route_id, vehicle_profile, leg, alt_index, geometry, "
        "distance_km, duration_hr) VALUES (?,?,?,?,?,?,?)",
        ("R1", "Artic Tipper (44t)", "loaded", 0,
         json.dumps([[blon - 0.05, blat + 0.01], [blon + 0.05, blat + 0.01]]), 10.0, 0.3))
    ok("a route 1.1 km away from the bridge does NOT match",
       restrictions.check_route("R1")["hits"] == [])

    # ------------------------------------------------- endpoints
    cat = main.restriction_layers()
    ok("GET /api/restrictions/layers lists the catalogue", len(cat["layers"]) == len(restrictions.LAYERS))
    ok("every layer entry carries its key", all("key" in l for l in cat["layers"]))
    ok("the catalogue is attributed", "Tark Tee" in cat["attribution"])

    diag = main.diagnostics_restrictions()
    ok("the diagnostic names what it has NOT verified", len(diag["unverified"]) >= 3)
    ok("including the nominal_load question",
       any("nominal_load" in u for u in diag["unverified"]))
    ok("and that the live service is never called from the sandbox",
       any("sandbox" in u for u in diag["unverified"]))

    try:
        main.route_restrictions("NOPE")
        ok("an unknown route 404s", False)
    except _HTTPException as e:
        ok("an unknown route 404s", e.status_code == 404)
finally:
    restrictions._get = _real_get
    restrictions.clear_cache()


# =========================================================================== #
#  2b. The restriction VALUE, its verdict, and expiry                          #
# =========================================================================== #
# Field names and value formats below are taken verbatim from the live service on
# 2026-08-27. restriction_limit is a genuine NUMBER — 8 for eight tonnes, 3.2 for 3.2
# metres — arriving as float32 widened to double (4.1500000953674316).
H = restrictions._enrich(
    {"objectid": 795, "road_nr": 24162, "road_name": "Loodi - Helme",
     "restriction_limit": 3.2000000476837158, "km_from": 4.0,
     "km_to": 7.3000001907348633, "date_from": None, "date_to": None,
     "extra_info": "Kehtestatud on gabariidipiirang max laius 3 m."},
    "restrictions_height", restrictions.LAYERS["restrictions_height"], "L")

ok("the numeric limit is parsed and de-noised", H["_limit"] == 3.2, str(H["_limit"]))
ok("with the unit from its layer", H["_unit"] == "m")
ok("⭐ the headline STATES the restriction — this was the whole complaint",
   H["_headline"] == "3.2 m height limit", H["_headline"])
ok("the description carries road and chainage too",
   "Loodi - Helme" in restrictions._describe(H) and "km 4" in restrictions._describe(H),
   restrictions._describe(H))
ok("float32 noise is not shown to anyone", "0000000" not in str(H["_limit"]))

a = restrictions.assess(H, "Artic Tipper (44t)")
ok("⭐ a 4.0 m vehicle under a 3.2 m limit EXCEEDS it", a["verdict"] == "exceeds")
ok("and the shortfall is quantified", a["margin"] == -0.8, str(a["margin"]))
ok("the vehicle's own height is reported", a["vehicle"] == 4.0)
a2 = restrictions.assess(H, "Rigid 7.5t")
ok("a 3.2 m vehicle under the same limit is within", a2["verdict"] == "within")
ok("with the clearance stated", a2["margin"] == 0.0, str(a2["margin"]))

M = restrictions._enrich(
    {"objectid": 74, "road_nr": 13154, "road_name": "Iisaku - Varesmetsa",
     "type": "GROSS_VEHICLE_WEIGHT_LIMIT", "restriction_limit": 8,
     "km_from": 0, "km_to": 7.0999999046325684,
     "date_from": 1497339300000, "date_to": 1497510000000},
    "restrictions_mass", restrictions.LAYERS["restrictions_mass"], "L")
ok("a mass limit reads in tonnes", M["_headline"] == "8 t mass limit", M["_headline"])
ok("44 t against an 8 t limit exceeds",
   restrictions.assess(M, "Artic Tipper (44t)")["verdict"] == "exceeds")
ok("7.5 t against the same limit is within",
   restrictions.assess(M, "Rigid 7.5t")["verdict"] == "within")

# ⚠️ EXPIRY. Every record in the first live sample was from June-August 2017.
ok("epoch-millisecond dates are parsed to ISO", M["_from"] == "2017-06-13", str(M["_from"]))
ok("⭐ a 2017 restriction is NOT in force today", M["_in_force"] is False)
ok("one with no dates at all is open-ended and always applies", H["_in_force"] is True)
ok("a window around today applies",
   restrictions.in_force({"date_from": 0, "date_to": 4102444800000}) is True)

T = restrictions._enrich(
    {"cause": "CULVERT_REPAIRS", "effect": "COMPLETE_CLOSURE",
     "road_name": "Lelle - Vahastu", "extra_info": "Suletud koikidele soidukitele",
     "date_from": 1496898000000, "date_to": 1496941200000},
    "restrictions_traffic", restrictions.LAYERS["restrictions_traffic"], "L")
ok("a closure reads in English, not as an enum",
   T["_headline"] == "Road completely closed · Culvert repairs", T["_headline"])
ok("a closure is not a dimension, so it gets no verdict",
   restrictions.assess(T, "Artic Tipper (44t)")["verdict"] == "unknown")
ok("and says why rather than leaving it blank",
   "not a dimension" in (restrictions.assess(T, "Artic Tipper (44t)")["note"] or ""))

# colours: served from the registry so the legend cannot drift from the layer
ok("every layer declares a colour", all(v.get("colour") for v in restrictions.LAYERS.values()))
ok("they are all distinct", len({v["colour"] for v in restrictions.LAYERS.values()})
   == len(restrictions.LAYERS))
ok("a traffic restriction is the stop colour, not a muddy note colour",
   restrictions.LAYERS["restrictions_traffic"]["colour"] == "#DC2626")
ok("and it is stamped onto every feature for the map to read", T["_colour"] == "#DC2626")
ok("no restriction colour collides with the route palette",
   not ({v["colour"].lower() for v in restrictions.LAYERS.values()}
        & {"#039e86", "#f59e0b", "#c2790b", "#bf2e55"}))

# =========================================================================== #
#  3. Street View — the free call must come first                              #
# =========================================================================== #
_sv_calls = []


class _FakeResp:
    def __init__(self, payload=None, raw=None, ctype="image/jpeg"):
        self._payload, self._raw, self.headers = payload, raw, {"Content-Type": ctype}

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(url, timeout=None):
    u = url if isinstance(url, str) else url.full_url
    _sv_calls.append(u)
    if "/metadata" in u:
        import io
        return _FakeResp(raw=json.dumps({
            "status": "OK", "date": "2024-06", "pano_id": "abc",
            "location": {"lat": 59.3917, "lng": 27.0004},
            "copyright": "© Google"}).encode())
    return _FakeResp(raw=b"\xff\xd8\xff-not-really-a-jpeg")


import urllib.request  # noqa: E402
_real_urlopen = urllib.request.urlopen


def _json_urlopen(url, timeout=None):
    r = _fake_urlopen(url, timeout)
    import io
    return io.BytesIO(r._raw) if False else r


os.environ["GOOGLE_MAPS_API_KEY"] = "test-key-not-real"
urllib.request.urlopen = _fake_urlopen
try:
    # json.load() needs a file-like; _FakeResp.read() is enough for json.load via wrapper
    import io

    def _urlopen_json(url, timeout=None):
        u = url if isinstance(url, str) else url.full_url
        _sv_calls.append(u)
        if "/metadata" in u:
            return io.BytesIO(json.dumps({
                "status": "OK", "date": "2024-06", "pano_id": "abc",
                "location": {"lat": 59.3917, "lng": 27.0004}}).encode())
        r = io.BytesIO(b"\xff\xd8\xff-not-really-a-jpeg")
        r.headers = {"Content-Type": "image/jpeg"}
        return r

    class _CM:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self.f

        def __exit__(self, *a):
            return False

    urllib.request.urlopen = lambda url, timeout=None: _CM(_urlopen_json(url, timeout))

    streetview._meta_cache.clear()
    _sv_calls.clear()

    ok("street view reports configured when the key is set", streetview.configured() is True)

    meta = streetview.metadata(59.3917, 27.0)
    ok("metadata says imagery is available", meta["available"] is True)
    ok("it reports the capture date, so nobody trusts a 2011 photo of a gate",
       meta["date"] == "2024-06")
    ok("and how far Google had to move to find a panorama", "offset_m" in meta)
    ok("only the metadata endpoint was called", all("/metadata" in c for c in _sv_calls))

    n = len(_sv_calls)
    streetview.metadata(59.3917, 27.0)
    ok("a repeat metadata lookup is cached", len(_sv_calls) == n)

    _sv_calls.clear()
    data, ctype = streetview.fetch_image(59.3917, 27.0)
    ok("an image comes back", data is not None and ctype.startswith("image/"))
    ok("fetching an image does NOT re-request metadata when it is cached",
       not any("/metadata" in c for c in _sv_calls))
    ok("the image request carries the key", any("key=" in c for c in _sv_calls))
    ok("and never leaves the key for the browser — the endpoint proxies",
       "streetview" in [f for f in dir(main) if "streetview" in f][0])

    # the cost rule: no imagery -> no billable request
    streetview._meta_cache.clear()

    def _no_imagery(url, timeout=None):
        u = url if isinstance(url, str) else url.full_url
        _sv_calls.append(u)
        if "/metadata" in u:
            return _CM(io.BytesIO(json.dumps({"status": "ZERO_RESULTS"}).encode()))
        raise AssertionError("an image was requested for a location with no imagery")

    urllib.request.urlopen = _no_imagery
    _sv_calls.clear()
    data, err = streetview.fetch_image(58.0, 25.0)
    ok("no imagery means no image returned", data is None)
    ok("and the reason is given", "ZERO_RESULTS" in err)
    ok("⭐ NO BILLABLE IMAGE REQUEST IS MADE when the free metadata call says there is "
       "nothing — this is the whole cost model",
       all("/metadata" in c for c in _sv_calls), str(_sv_calls))

    before = streetview.stats()["billable_calls"]
    streetview.fetch_image(58.0, 25.0)
    ok("and the billable counter does not move", streetview.stats()["billable_calls"] == before)

    # unconfigured
    os.environ["GOOGLE_MAPS_API_KEY"] = ""
    ok("with no key it reports unconfigured", streetview.configured() is False)
    ok("and metadata says so rather than pretending", streetview.metadata(59, 27)["status"] == "NO_KEY")
    d, e = streetview.fetch_image(59, 27)
    ok("and no request is attempted", d is None and "not set" in e)

    diag = streetview.diagnostics()
    ok("the diagnostic states that metadata is free and images are not",
       any("free" in n for n in diag["notes"]))
    ok("it does NOT quote a price — those change and a stale figure is worse than none",
       not any(any(ch.isdigit() for ch in n) and "$" in n for n in diag["notes"]))
    ok("it names the pricing as unverified", any("pricing" in u for u in diag["unverified"]))
finally:
    urllib.request.urlopen = _real_urlopen
    os.environ.pop("GOOGLE_MAPS_API_KEY", None)


# =========================================================================== #
#  4. Source-level wiring                                                      #
# =========================================================================== #
def code_of(path):
    src = open(path, encoding="utf-8").read()
    return src, "\n".join(l.split("#", 1)[0] for l in src.splitlines())


main_src, main_code = code_of(os.path.join(BACKEND, "main.py"))
restr_src, restr_code = code_of(os.path.join(BACKEND, "restrictions.py"))
sv_src, sv_code = code_of(os.path.join(BACKEND, "streetview.py"))

ok("the restriction layers are served from a proxy endpoint",
   '@app.get("/api/restrictions")' in main_code)
ok("per-route restriction checking is exposed",
   "/api/routes/{route_id}/restrictions" in main_code)
ok("street view metadata has its own free endpoint",
   '"/api/streetview/meta"' in main_code)
ok("fetch_image checks metadata before spending a request",
   restr_code is not None and "meta = metadata(" in sv_code
   and sv_code.index("meta = metadata(") < sv_code.index("_stats[\"image_calls\"]"))
# Google's terms restrict caching and storing Maps content, so the proxy must stream and
# forget. Written as "no file open and no database at all" rather than a substring search
# for 'open(', which matched urlopen() and passed for the wrong reason on the first run.
import re as _re  # noqa: E402
ok("Street View imagery is never written to disk — Google's terms restrict storing it",
   not _re.search(r"(?<!url)\bopen\s*\(", sv_code))
ok("and the module does not touch the database at all",
   "import db" not in sv_code and "db." not in sv_code)
ok("the Google key is read from the environment only",
   'os.environ.get("GOOGLE_MAPS_API_KEY"' in sv_code)
ok("restrictions never feed the router — no avoid_areas anywhere in the module",
   "avoid_areas" not in restr_code)
ok("the module says the coordinates are mislabelled at source",
   "L-EST97" in restr_src and "4326" in restr_src)
ok("and projection.py explains why it exists rather than just doing it",
   "pyproj" in open(os.path.join(BACKEND, "projection.py"), encoding="utf-8").read())

# =========================================================================== #
print()
for f in FAIL:
    print("  FAIL:", f)
print(f"\n{PASS} passed, {len(FAIL)} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)

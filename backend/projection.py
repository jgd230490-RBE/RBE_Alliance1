"""
L-EST97 (EPSG:3301) -> WGS84, hand-rolled.

WHY THIS FILE EXISTS
--------------------
The Estonian Transport Administration's Tark Tee ArcGIS service returns coordinates in
L-EST97 (Lambert Conformal Conic, metres) **while declaring them as WKID 4326**. Both
`f=geojson` and `f=json&outSR=4326` were checked against the live service on 2026-08-27
and both returned projected metres with `"spatialReference":{"wkid":4326}` in the
response. The declared SR is wrong, `outSR` is not honoured, and a client that believes
either ends up drawing Estonian roads somewhere off the coast of Somalia.

So the conversion is done here rather than trusted to the service. pyproj would be the
obvious tool and PyPI is blocked in the build sandbox, so the inverse projection is
implemented directly. It is textbook Snyder; the risk is a transcribed constant, which is
what the assertions in tests/test_phase25a.py are for.

EPSG:3301 parameters (Estonian Land Board / EPSG registry):
    projection            Lambert Conformal Conic, 2 standard parallels
    ellipsoid             GRS80   a = 6378137 m, 1/f = 298.257222101
    standard parallel 1   59 20 00 N   = 59.333333333...
    standard parallel 2   58 00 00 N   = 58.0
    latitude of origin    57 31 03.19415 N = 57.517553930555...
    central meridian      24 00 00 E
    false easting         500000 m
    false northing        6375000 m

The false origin gives a free exact test: (500000, 6375000) must convert to
(24.0, 57.51755393055...) with no error at all beyond floating point. That is asserted,
and so is a round trip through the forward projection, which is implemented only so the
inverse can be checked against it.
"""
import math

# GRS80
_A = 6378137.0
_INV_F = 298.257222101
_F = 1.0 / _INV_F
_E = math.sqrt(2 * _F - _F * _F)          # first eccentricity

# EPSG:3301
_LAT_1 = math.radians(59.0 + 20.0 / 60.0)
_LAT_2 = math.radians(58.0)
_LAT_0 = math.radians(57.0 + 31.0 / 60.0 + 3.19415 / 3600.0)
_LON_0 = math.radians(24.0)
_FALSE_E = 500000.0
_FALSE_N = 6375000.0


def _m(lat):
    """Snyder 14-15: m = cos(lat) / sqrt(1 - e^2 sin^2(lat))."""
    s = math.sin(lat)
    return math.cos(lat) / math.sqrt(1.0 - _E * _E * s * s)


def _t(lat):
    """
    Snyder 15-9. Written in the (1 - e sin) / (1 + e sin) form rather than with a
    half-angle tangent, because the latter loses precision near the pole and this is
    cheap enough not to care.
    """
    s = math.sin(lat)
    return (math.tan(math.pi / 4.0 - lat / 2.0)
            / (((1.0 - _E * s) / (1.0 + _E * s)) ** (_E / 2.0)))


# Cone constants, computed once. n is the cone constant, F the scale factor, rho0 the
# radius at the latitude of origin.
_M1, _M2 = _m(_LAT_1), _m(_LAT_2)
_T1, _T2 = _t(_LAT_1), _t(_LAT_2)
_N = (math.log(_M1) - math.log(_M2)) / (math.log(_T1) - math.log(_T2))
_BIG_F = _M1 / (_N * (_T1 ** _N))
_RHO_0 = _A * _BIG_F * (_t(_LAT_0) ** _N)


def to_wgs84(x, y):
    """
    L-EST97 easting/northing in metres -> (lon, lat) in degrees, GeoJSON order.

    Returns None for a coordinate that cannot be converted rather than raising: this is
    fed by a third-party service, and one malformed feature should drop out of the layer,
    not fail the whole request.
    """
    try:
        x = float(x)
        y = float(y)
    except (TypeError, ValueError):
        return None

    dx = x - _FALSE_E
    dy = _RHO_0 - (y - _FALSE_N)
    rho = math.hypot(dx, dy)
    if rho == 0:
        return None
    if _N < 0:
        rho = -rho

    # Snyder: theta = atan2(x', rho0 - y'), with BOTH arguments negated when the cone
    # constant is negative (a southern-hemisphere projection). Multiply by the sign —
    # copysign is the wrong tool here and silently threw away the sign of dx, which
    # mirrored every western point onto the eastern side of the central meridian. The
    # false-origin test still passed because dx is 0 there; the round trip is what caught
    # it. Keep the round-trip assertion.
    sign = 1.0 if _N >= 0 else -1.0
    theta = math.atan2(dx * sign, dy * sign)
    t = (rho / (_A * _BIG_F)) ** (1.0 / _N)

    # Snyder 3-5: iterate for the geodetic latitude. Converges in three or four passes at
    # Estonian latitudes; the cap and the tolerance are belt and braces.
    phi = math.pi / 2.0 - 2.0 * math.atan(t)
    for _ in range(12):
        s = math.sin(phi)
        prev = phi
        phi = (math.pi / 2.0
               - 2.0 * math.atan(t * (((1.0 - _E * s) / (1.0 + _E * s)) ** (_E / 2.0))))
        if abs(phi - prev) < 1e-12:
            break

    lon = theta / _N + _LON_0
    return (round(math.degrees(lon), 7), round(math.degrees(phi), 7))


def from_wgs84(lon, lat):
    """
    (lon, lat) degrees -> L-EST97 easting/northing.

    Implemented ONLY so that to_wgs84() can be checked against it — nothing in the app
    converts in this direction. A round trip that returns the input is the strongest
    available evidence that the constants above were transcribed correctly, short of a
    reference implementation the sandbox cannot install.
    """
    try:
        lon = math.radians(float(lon))
        lat = math.radians(float(lat))
    except (TypeError, ValueError):
        return None
    rho = _A * _BIG_F * (_t(lat) ** _N)
    theta = _N * (lon - _LON_0)
    return (_FALSE_E + rho * math.sin(theta),
            _FALSE_N + _RHO_0 - rho * math.cos(theta))


def looks_projected(x, y):
    """
    True if a coordinate pair looks like L-EST97 metres rather than degrees.

    The service currently mislabels its output, and a fix at their end would silently
    double-convert everything if this code just assumed. Degrees over Estonia are
    ~21-29 and ~57-60; L-EST97 metres are ~370000-740000 and ~6370000-6640000. There is
    no overlap, so the test is safe, and it means the day Tark Tee starts telling the
    truth this keeps working instead of breaking.
    """
    try:
        x = abs(float(x))
        y = abs(float(y))
    except (TypeError, ValueError):
        return False
    return x > 1000.0 or y > 1000.0


def normalise(x, y):
    """(lon, lat) whatever the service gave us — converting only if it needs it."""
    if looks_projected(x, y):
        return to_wgs84(x, y)
    try:
        return (round(float(x), 7), round(float(y), 7))
    except (TypeError, ValueError):
        return None

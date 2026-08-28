"""
Phase 3 — zones: geofencing and disruptions in one table.

A zone is a drawn area with a date range. Two things read it:

  * the router, for zones with affects_routing = TRUE. Their bounding boxes go to
    HERE as avoid[areas], so a re-bake steers around them.
  * both maps, for every zone. A works area that does not close a road is still
    something a planner needs to see.

This module owns the geometry maths and the invalidation rules. It talks to db and
nothing else — network.py imports zones for avoid_areas(), so the dependency has to
point one way or the two modules deadlock on import.

WHAT IS EXACT AND WHAT IS A HEURISTIC
-------------------------------------
Deciding which baked routes a zone change invalidates has two halves, and only one of
them is exact:

  * "which routes cross this new zone" is exact. Segment-level intersection of the
    baked polyline against the zone's rings.
  * "which routes detoured around a zone that has now moved or gone" cannot be
    answered from the geometry, because a route that avoided an area does not touch
    it. It is approximated by testing against a padded bounding box — see
    DETOUR_PAD_KM. That over-invalidates (routes that merely pass nearby get re-baked
    for nothing, costing HERE calls) and under-invalidates for any detour that swung
    wider than the pad.

Nothing here records which zones were in force at bake time, so there is no way to be
exact about the second case short of adding that column and re-baking the network to
populate it. Said plainly rather than hidden behind a number that looks precise.
"""
import json
import math
import re
from datetime import datetime, timezone

import db

# How far outside a zone a route is still assumed to have detoured because of it.
# Pure judgement, not a measured figure: 3 km is roughly the scale of a detour around a
# closed junction on this network. Raise it and zone edits re-bake more routes than they
# need to; lower it and a wide detour is left pointing at a zone that no longer exists.
DETOUR_PAD_KM = 3.0

KINDS = ("closure", "weight_limit", "works", "haul_road", "other")

# A haul road is the one kind that is not an obstacle. Every other kind, with
# affects_routing on, becomes an avoid[areas] box: "route around this". A haul road with
# affects_routing on means the opposite — "route THROUGH this" — and handing its bbox to
# HERE as an avoid area would tell the router to steer clear of the very road it is
# supposed to use, silently, and only visible as a route that inexplicably got longer.
#
# So haul roads are excluded from avoid_areas() by kind, not by a flag someone has to
# remember to unset. Phase 4 reads them through haul.py instead.
HAUL_KIND = "haul_road"

# Shapes a zone may be drawn as. A Point is rejected: it has no extent, so its bbox is
# degenerate and HERE would be handed a zero-area rectangle. If a single-point hazard
# ever needs recording it wants a small drawn box, not a marker.
GEOM_TYPES = ("Polygon", "MultiPolygon", "LineString", "MultiLineString")


# --------------------------------------------------------------------------- #
#  Geometry                                                                    #
# --------------------------------------------------------------------------- #
def _rings(geom):
    """
    Every coordinate ring in a GeoJSON geometry, as lists of [lon, lat].

    Polygon -> its rings (outer first, then holes). LineString -> one open ring.
    MultiPolygon / MultiLineString are flattened. Anything else -> [].
    """
    if not isinstance(geom, dict):
        return []
    t = geom.get("type")
    c = geom.get("coordinates") or []
    if t == "Polygon":
        return [r for r in c if isinstance(r, list) and len(r) >= 3]
    if t == "MultiPolygon":
        return [r for poly in c for r in poly if isinstance(r, list) and len(r) >= 3]
    if t == "LineString":
        return [c] if len(c) >= 2 else []
    if t == "MultiLineString":
        return [r for r in c if isinstance(r, list) and len(r) >= 2]
    return []


def _valid_geometry(geom):
    """A zone shape must be an area or a line with real coordinates. See GEOM_TYPES."""
    return (isinstance(geom, dict)
            and geom.get("type") in GEOM_TYPES
            and bool(_points(geom)))


def _points(geom):
    return [p for ring in _rings(geom) for p in ring if isinstance(p, (list, tuple)) and len(p) >= 2]


def bbox_of(geom):
    """
    (west, south, east, north) for a GeoJSON geometry, or None if it has no coordinates.

    This is the reduction HERE forces on us. avoid[areas] takes bounding boxes only —
    not arbitrary polygons — so an L-shaped or diagonal closure is sent to the router as
    the rectangle that contains it, and the router therefore avoids more ground than was
    drawn. The map still draws the real shape, so the two disagree on purpose: what you
    see is what was drawn, what HERE gets is the box around it.

    If that over-block matters for a particular zone, draw it as several smaller zones
    rather than one large one. Each contributes its own bbox.
    """
    pts = _points(geom)
    if not pts:
        return None
    lons = [float(p[0]) for p in pts]
    lats = [float(p[1]) for p in pts]
    return (min(lons), min(lats), max(lons), max(lats))


def _pad_bbox(bbox, km):
    """Grow a bbox by `km` on every side. Longitude degrees shrink with latitude."""
    if not bbox or km <= 0:
        return bbox
    w, s, e, n = bbox
    dlat = km / 111.32
    mid = math.radians((s + n) / 2.0)
    dlon = km / max(1e-6, 111.32 * math.cos(mid))
    return (w - dlon, s - dlat, e + dlon, n + dlat)


def _bbox_of_points(pts):
    if not pts:
        return None
    lons = [float(p[0]) for p in pts]
    lats = [float(p[1]) for p in pts]
    return (min(lons), min(lats), max(lons), max(lats))


def _bboxes_overlap(a, b):
    if not a or not b:
        return False
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _point_in_ring(pt, ring):
    """Even-odd ray casting. Ring may be open or closed; both work."""
    x, y = float(pt[0]), float(pt[1])
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])
        if (yi > y) != (yj > y):
            xc = (xj - xi) * (y - yi) / ((yj - yi) or 1e-18) + xi
            if x < xc:
                inside = not inside
        j = i
    return inside


def _orient(a, b, c):
    v = (float(b[0]) - float(a[0])) * (float(c[1]) - float(a[1])) - \
        (float(b[1]) - float(a[1])) * (float(c[0]) - float(a[0]))
    if v > 1e-14:
        return 1
    if v < -1e-14:
        return -1
    return 0


def _on_seg(a, b, p):
    return (min(float(a[0]), float(b[0])) - 1e-12 <= float(p[0]) <= max(float(a[0]), float(b[0])) + 1e-12 and
            min(float(a[1]), float(b[1])) - 1e-12 <= float(p[1]) <= max(float(a[1]), float(b[1])) + 1e-12)


def _segments_cross(p1, p2, p3, p4):
    """Proper or touching intersection of segments p1p2 and p3p4."""
    o1, o2 = _orient(p1, p2, p3), _orient(p1, p2, p4)
    o3, o4 = _orient(p3, p4, p1), _orient(p3, p4, p2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_seg(p1, p2, p3):
        return True
    if o2 == 0 and _on_seg(p1, p2, p4):
        return True
    if o3 == 0 and _on_seg(p3, p4, p1):
        return True
    if o4 == 0 and _on_seg(p3, p4, p2):
        return True
    return False


def line_hits_geometry(line, geom):
    """
    True if a route polyline (list of [lon, lat]) touches a zone geometry.

    Three ways it can, and all three matter:
      1. a route vertex falls inside a polygon — the usual case
      2. a route segment crosses a ring edge without any vertex landing inside, which
         is what happens when a long straight leg passes clean through a small zone
      3. the zone is a LineString (a closed road drawn as a line rather than an area)
         and a route segment crosses it

    A bbox pre-check rejects the overwhelming majority of pairs before any of the
    per-segment work runs; on 107 routes x 2 legs x up to 3 alternatives that is the
    difference between instant and not.
    """
    if not line or len(line) < 2:
        return False
    rings = _rings(geom)
    if not rings:
        return False
    zb = bbox_of(geom)
    lb = _bbox_of_points(line)
    if not _bboxes_overlap(zb, lb):
        return False

    is_area = (geom.get("type") in ("Polygon", "MultiPolygon"))
    if is_area:
        outer = rings[0]
        for p in line:
            if _point_in_ring(p, outer):
                return True

    for i in range(len(line) - 1):
        a, b = line[i], line[i + 1]
        for ring in rings:
            # an area's ring wraps back to its first point; a LineString zone does not.
            # A ring already stored closed (first == last) just gains one zero-length
            # segment from the wrap, which no intersection test can match.
            span = len(ring) if is_area else len(ring) - 1
            for j in range(span):
                c = ring[j]
                d = ring[(j + 1) % len(ring)]
                if _segments_cross(a, b, c, d):
                    return True
    return False


EARTH_R_KM = 6371.0088


def haversine_km(a, b):
    """Great-circle distance between two [lon, lat] points, in km."""
    lon1, lat1 = math.radians(float(a[0])), math.radians(float(a[1]))
    lon2, lat2 = math.radians(float(b[0])), math.radians(float(b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(h)))


def line_length_km(line):
    """
    Length of a polyline of [lon, lat] points, summed great-circle segment by segment.

    This is the drawn length of a haul road, and Phase 4 divides it by the assigned speed
    to get a duration. It is the length of the line as drawn on a sphere — it does not
    know about gradient, and it is only as accurate as the drawing. A road sketched with
    four clicks along a curve reads short. Said here because the number it produces looks
    precise and the drawing behind it may not be.
    """
    if not line or len(line) < 2:
        return 0.0
    return sum(haversine_km(line[i], line[i + 1]) for i in range(len(line) - 1))


def as_line(geom):
    """
    A zone geometry as a single ordered list of [lon, lat], or None.

    A haul road has to be a line with a direction — it is traversed, not enclosed. A
    LineString is taken as drawn. A MultiLineString takes its first part rather than
    guessing how disjoint parts join. A Polygon is refused: a closed ring has no
    unambiguous entry and exit, and silently taking its boundary would produce a haul
    road that runs the long way round.
    """
    if not isinstance(geom, dict):
        return None
    t = geom.get("type")
    c = geom.get("coordinates") or []
    if t == "LineString" and len(c) >= 2:
        return [list(p) for p in c]
    if t == "MultiLineString":
        parts = [p for p in c if isinstance(p, list) and len(p) >= 2]
        return [list(p) for p in parts[0]] if parts else None
    return None


def oriented_line(geom, from_point):
    """
    A haul road's line, ordered so the end nearest `from_point` comes first.

    Which end of a drawn line a truck enters depends on where it is coming from, and the
    drawing order carries no such information — a planner clicking a road left to right
    has said nothing about traffic direction. So entry is decided per leg by proximity,
    which is why the return leg can traverse the same drawn line in the opposite order.

    Returns (line, reversed_flag) or (None, False).
    """
    line = as_line(geom)
    if not line:
        return None, False
    if not from_point:
        return line, False
    if haversine_km(line[-1], from_point) < haversine_km(line[0], from_point):
        return list(reversed(line)), True
    return line, False


def line_in_bbox(line, bbox):
    """True if any vertex of the polyline falls inside bbox. Used for the padded test."""
    if not line or not bbox:
        return False
    w, s, e, n = bbox
    for p in line:
        if w <= float(p[0]) <= e and s <= float(p[1]) <= n:
            return True
    return False


# --------------------------------------------------------------------------- #
#  Dates                                                                       #
# --------------------------------------------------------------------------- #
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clean_date(v):
    v = (v or "").strip()
    return v if _DATE_RE.match(v) else None


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def applies_on(zone, on=None):
    """
    Is this zone in force on `on` (default today)?

    NULL at either end is open-ended, so a zone with neither date always applies.
    Both bounds are inclusive — a closure that ends_on the 14th is shut on the 14th.
    String comparison is safe here because the format is fixed ISO 'YYYY-MM-DD'.
    """
    on = _clean_date(on) or _today()
    s, e = _clean_date(zone.get("starts_on")), _clean_date(zone.get("ends_on"))
    if s and on < s:
        return False
    if e and on > e:
        return False
    return True


def _truthy(v):
    """SQLite hands booleans back as 0/1, Postgres as True/False. Both arrive here."""
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no")
    return bool(v)


# --------------------------------------------------------------------------- #
#  CRUD                                                                        #
# --------------------------------------------------------------------------- #
def _now():
    return datetime.now(timezone.utc).isoformat()


def _next_zone_id():
    # per tenant: Z-numbers are only unique within a tenant, and scanning every
    # tenant's ids would make one client's zone count push the next client's numbering
    ids = [r["id"] for r in db.query("SELECT id FROM zones WHERE tenant_id = ?",
                                     (db.current_tenant(),))]
    n = 0
    for i in ids:
        m = re.match(r"^Z(\d+)$", (i or "").strip())
        if m:
            n = max(n, int(m.group(1)))
    return f"Z{n + 1:03d}"


def _row(z):
    """DB row -> API shape. geometry is parsed, booleans normalised, bbox computed."""
    geom = None
    if z.get("geometry"):
        try:
            geom = json.loads(z["geometry"])
        except Exception:
            geom = None
    bb = bbox_of(geom) if geom else None
    return {
        "id": z["id"],
        "name": z.get("name"),
        "kind": z.get("kind") or "other",
        "geometry": geom,
        "affects_routing": _truthy(z.get("affects_routing")),
        "starts_on": z.get("starts_on"),
        "ends_on": z.get("ends_on"),
        "note": z.get("note"),
        "active": _truthy(z.get("active")),
        "created_at": z.get("created_at"),
        "updated_at": z.get("updated_at"),
        # Phase 4 — meaningful only for kind = 'haul_road', and null everywhere else
        # rather than defaulted, so a zone that has never been given a speed is
        # distinguishable from one deliberately set to HERE's own timing.
        "speed_kph": (float(z["speed_kph"]) if z.get("speed_kph") not in (None, "") else None),
        "haul_mode": (z.get("haul_mode") or None),
        "length_km": (round(line_length_km(as_line(geom)), 3)
                      if geom and as_line(geom) else None),
        # what the router would actually receive, exposed so the difference between the
        # drawn shape and the avoided rectangle is visible in the UI rather than a
        # surprise after a re-bake
        "bbox": list(bb) if bb else None,
        "avoid_area": _avoid_str(bb) if bb else None,
    }


def list_zones(include_inactive=True, on=None):
    rows = [_row(z) for z in db.query("SELECT * FROM zones WHERE tenant_id = ? ORDER BY id",
                                      (db.current_tenant(),))]
    if not include_inactive:
        rows = [z for z in rows if z["active"]]
    if on:
        rows = [z for z in rows if applies_on(z, on)]
    return rows


def get_zone(zone_id):
    r = db.query("SELECT * FROM zones WHERE tenant_id = ? AND id = ?",
                 (db.current_tenant(), zone_id))
    return _row(r[0]) if r else None


def _clean_speed(v):
    """
    An assigned haul speed, or None. Zero and negative are rejected rather than stored:
    a division by them is what turns an assigned speed into an infinite cycle time, and
    the sensible reading of "0 km/h" is "I have not set this", which is None.
    """
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _clean_mode(v):
    v = (v or "").strip().lower()
    return v if v in ("splice", "via") else None


def create_zone(name, geometry, kind=None, affects_routing=True,
                starts_on=None, ends_on=None, note=None, active=True, zone_id=None,
                speed_kph=None, haul_mode=None):
    if not (name or "").strip():
        return {"error": "name is required"}
    if not _valid_geometry(geometry):
        return {"error": "geometry must be a GeoJSON Polygon or LineString with coordinates"}
    kind = kind or "other"
    if kind == HAUL_KIND and not as_line(geometry):
        return {"error": "a haul road must be drawn as a LineString — it is travelled "
                         "along, and a closed shape has no entry or exit"}
    zid = (zone_id or "").strip() or _next_zone_id()
    # the id clash that matters is one within this tenant — the primary key is
    # (tenant_id, id), so two tenants may each hold a Z001
    if db.query("SELECT id FROM zones WHERE tenant_id = ? AND id = ?",
                (db.current_tenant(), zid)):
        return {"error": f"zone {zid} already exists"}
    now = _now()
    db.execute(
        "INSERT INTO zones (tenant_id, id, name, kind, geometry, affects_routing, "
        "starts_on, ends_on, note, active, created_at, updated_at, speed_kph, haul_mode) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (db.current_tenant(), zid, name.strip(), kind, json.dumps(geometry),
         bool(affects_routing), _clean_date(starts_on), _clean_date(ends_on),
         (note or None), bool(active), now, now,
         _clean_speed(speed_kph), _clean_mode(haul_mode)),
    )
    return get_zone(zid)


def update_zone(zone_id, **fields):
    """
    Partial update. Only keys actually present in `fields` are written, so a client that
    knows nothing about (say) `note` cannot blank one by omitting it — the same trap the
    Locations PUT hit when a node drag started wiping salvaged vendor names.
    """
    cur = db.query("SELECT * FROM zones WHERE tenant_id = ? AND id = ?",
                   (db.current_tenant(), zone_id))
    if not cur:
        return {"error": "zone not found", "id": zone_id}
    sets, params = [], []
    if "name" in fields:
        if not (fields["name"] or "").strip():
            return {"error": "name cannot be empty"}
        sets.append("name = ?"); params.append(fields["name"].strip())
    if "kind" in fields:
        sets.append("kind = ?"); params.append(fields["kind"] or "other")
    if "geometry" in fields:
        if not _valid_geometry(fields["geometry"]):
            return {"error": "geometry must be a GeoJSON Polygon or LineString with coordinates"}
        sets.append("geometry = ?"); params.append(json.dumps(fields["geometry"]))
    # a haul road must still be a line after the edit, whichever of kind or geometry
    # moved. Checked against the resulting pair, not the one field that was sent —
    # switching an existing polygon's kind to haul_road is the case a per-field check
    # would wave through.
    new_kind = fields.get("kind", cur[0].get("kind")) or "other"
    new_geom = fields["geometry"] if "geometry" in fields else _row(cur[0])["geometry"]
    if new_kind == HAUL_KIND and not as_line(new_geom):
        return {"error": "a haul road must be drawn as a LineString — it is travelled "
                         "along, and a closed shape has no entry or exit"}
    if "speed_kph" in fields:
        sets.append("speed_kph = ?"); params.append(_clean_speed(fields["speed_kph"]))
    if "haul_mode" in fields:
        sets.append("haul_mode = ?"); params.append(_clean_mode(fields["haul_mode"]))
    if "affects_routing" in fields:
        sets.append("affects_routing = ?"); params.append(bool(fields["affects_routing"]))
    if "starts_on" in fields:
        sets.append("starts_on = ?"); params.append(_clean_date(fields["starts_on"]))
    if "ends_on" in fields:
        sets.append("ends_on = ?"); params.append(_clean_date(fields["ends_on"]))
    if "note" in fields:
        sets.append("note = ?"); params.append(fields["note"] or None)
    if "active" in fields:
        sets.append("active = ?"); params.append(bool(fields["active"]))
    if not sets:
        return get_zone(zone_id)
    sets.append("updated_at = ?"); params.append(_now())
    # tenant_id constrains the WHERE and never appears in the SET — an edit must not be
    # able to move a row into another tenant. Params run SET-values, then tenant, then id,
    # matching the ? order. Built with .format rather than an f-string so that `UPDATE
    # zones` and the tenant predicate stay in one string literal: split across an
    # f-string's fragments the write reads as unfiltered to the Phase 4.5 audit.
    params.append(db.current_tenant())
    params.append(zone_id)
    db.execute("UPDATE zones SET {} WHERE tenant_id = ? AND id = ?".format(", ".join(sets)),
               tuple(params))
    return get_zone(zone_id)


def delete_zone(zone_id):
    if not db.query("SELECT id FROM zones WHERE tenant_id = ? AND id = ?",
                    (db.current_tenant(), zone_id)):
        return {"error": "zone not found", "id": zone_id}
    db.execute("DELETE FROM zones WHERE tenant_id = ? AND id = ?",
               (db.current_tenant(), zone_id))
    return {"deleted": zone_id}


# --------------------------------------------------------------------------- #
#  What the router sees                                                        #
# --------------------------------------------------------------------------- #
def _avoid_str(bbox):
    w, s, e, n = bbox
    return f"bbox:{w:.6f},{s:.6f},{e:.6f},{n:.6f}"


def routing_zones(on=None):
    """
    Active, routing-affecting zones in force on a date (default today) — the ones whose
    bounding boxes go to HERE as avoid[areas].

    ⚠️ Haul roads are excluded, by kind. See HAUL_KIND: for every other kind
    affects_routing means "steer around this", and for a haul road it means the reverse.
    Before Phase 4 that distinction did not exist, because 'haul_road' was in KINDS but
    nothing read it; any haul road drawn against the Phase 3 build with affects_routing
    left on would have been sent to HERE as a box to avoid.
    """
    return [z for z in list_zones(include_inactive=False)
            if z["affects_routing"] and z["geometry"] and z["kind"] != HAUL_KIND
            and applies_on(z, on)]


def haul_roads(on=None, include_inactive=False, require_geometry=True):
    """
    Haul-road zones in force on a date. `affects_routing` gates them here exactly as it
    gates the avoid list: off means drawn on the map but never threaded into a route.
    """
    out = [z for z in list_zones(include_inactive=include_inactive)
           if z["kind"] == HAUL_KIND and applies_on(z, on)]
    if require_geometry:
        out = [z for z in out if z["geometry"]]
    return out


def avoid_areas(on=None):
    """
    HERE avoid[areas] strings for every zone in force. Empty list if there are none,
    which here_routing treats as "don't send the parameter at all".

    NOT capped, and deliberately so. HERE documents limits on this parameter that this
    codebase has never verified against the live API, and inventing a cap here would
    silently drop zones a planner drew. If a bake starts failing once several zones
    exist, /api/admin/diagnostics/zones probes one route with the current set and shows
    the actual request and the actual response — read that rather than guessing.
    """
    out = []
    for z in routing_zones(on):
        bb = bbox_of(z["geometry"])
        if bb:
            out.append(_avoid_str(bb))
    return out


# --------------------------------------------------------------------------- #
#  Invalidation                                                                #
# --------------------------------------------------------------------------- #
def _baked_lines():
    """
    Every cached geometry with a decodable polyline, as (key, coords, zone_tag).

    zone_tag is the zones_applied column: a comma-separated list of the zone ids that
    were sent to HERE when this leg was baked, '' for "baked with none in force", and
    None for a row that predates Phase 3 and whose provenance is therefore unknown. The
    three cases are treated differently in invalidate(), so they must not be collapsed.
    """
    out = []
    try:
        rows = db.query("SELECT route_id, vehicle_profile, leg, alt_index, geometry, "
                        "zones_applied FROM route_geometry "
                        "WHERE tenant_id = ? AND geometry IS NOT NULL",
                        (db.current_tenant(),))
    except Exception:
        # database that has not run the Phase 3 migration: every row is pre-Phase-3
        rows = db.query("SELECT route_id, vehicle_profile, leg, alt_index, geometry "
                        "FROM route_geometry "
                        "WHERE tenant_id = ? AND geometry IS NOT NULL",
                        (db.current_tenant(),))
        for r in rows:
            r["zones_applied"] = None
    for g in rows:
        try:
            coords = json.loads(g["geometry"])
        except Exception:
            continue
        if isinstance(coords, list) and len(coords) >= 2:
            out.append(((g["route_id"], g["vehicle_profile"], g["leg"], g["alt_index"]),
                        coords, g.get("zones_applied")))
    return out


def _tagged_with(zone_id, lines):
    """Baked legs whose zones_applied names this zone — an exact record, not a guess."""
    if not zone_id:
        return []
    out = []
    for key, _coords, tag in lines:
        if tag and zone_id in [t.strip() for t in tag.split(",") if t.strip()]:
            out.append(key)
    return out


def affected_by(geometry, padded=False, _lines=None):
    """
    Which baked geometries a zone shape touches.

    padded=False  exact: the polyline crosses or enters the drawn shape. This is the
                  "a new zone blocks this route" question.
    padded=True   approximate: the polyline passes within DETOUR_PAD_KM of the shape's
                  bounding box. This is the "did this route detour around the zone I
                  just moved or deleted" question, and it cannot be answered exactly —
                  see the module docstring.

    Returns a sorted list of {route_id, vehicle_profile, leg, alt_index}.
    """
    if not geometry:
        return []
    lines = _lines if _lines is not None else _baked_lines()
    hits = []
    if padded:
        bb = _pad_bbox(bbox_of(geometry), DETOUR_PAD_KM)
        for key, coords, _tag in lines:
            if line_in_bbox(coords, bb):
                hits.append(key)
    else:
        for key, coords, _tag in lines:
            if line_hits_geometry(coords, geometry):
                hits.append(key)
    return [{"route_id": k[0], "vehicle_profile": k[1], "leg": k[2], "alt_index": k[3]}
            for k in sorted(set(hits))]


def invalidate(zone_id=None, new_geometry=None, old_geometry=None, dry_run=False):
    """
    Clear the baked geometry a zone change makes wrong, and report what has to be
    re-routed.

    A zone edit is two events at once, and they are answered by three different tests —
    only the third of which is a guess:

      1. new_geometry, EXACT      routes whose baked line now runs through the zone.
                                  Segment-level intersection; no approximation.
      2. zone_id, EXACT           legs whose zones_applied column names this zone. They
                                  were routed with it avoided, so any change to it —
                                  moved, resized, deactivated, deleted — can change them.
                                  This is a record of what happened, not an inference.
      3. old_geometry, HEURISTIC  legs with NO zones_applied record at all (baked before
                                  Phase 3) that pass within DETOUR_PAD_KM of where the
                                  zone used to be. Test 2 cannot see these because
                                  nothing was ever written down for them. It over- and
                                  under-invalidates; see the module docstring.

    Test 3 is deliberately restricted to untagged rows. Applying it to tagged ones would
    re-bake neighbours that provably had nothing to do with this zone, and every one of
    those is a HERE call spent for nothing.

    A create passes new_geometry; a delete passes zone_id and old_geometry; an edit
    passes all three. Alternatives are cleared alongside the primary — a promoted
    alternative that crossed a new closure would otherwise survive as the route the
    planner is shown.

    Returns (route_id, vehicle_profile, leg) triples to re-bake. alt_index is dropped
    because baking is per leg: one HERE call covers every alternative on it.
    """
    lines = _baked_lines()
    hits, why = [], {}

    def _add(keys, reason):
        for k in keys:
            hits.append(k)
            why.setdefault(k, reason)

    if new_geometry:
        _add([(h["route_id"], h["vehicle_profile"], h["leg"], h["alt_index"])
              for h in affected_by(new_geometry, padded=False, _lines=lines)],
             "crosses the zone")
    if zone_id:
        _add(_tagged_with(zone_id, lines), "was baked with this zone avoided")
    if old_geometry:
        untagged = [(k, c, t) for (k, c, t) in lines if t is None]
        _add([(h["route_id"], h["vehicle_profile"], h["leg"], h["alt_index"])
              for h in affected_by(old_geometry, padded=True, _lines=untagged)],
             f"no bake record, passes within {DETOUR_PAD_KM:g} km (approximate)")

    keys = []
    seen = set()
    for k in hits:
        if k not in seen:
            seen.add(k)
            keys.append(k)

    legs = sorted({(k[0], k[1], k[2]) for k in keys})
    leg_reason = {}
    for k in keys:
        leg_reason.setdefault((k[0], k[1], k[2]), why.get(k))

    if not dry_run:
        for rid, prof, leg in legs:
            db.execute(
                "DELETE FROM route_geometry WHERE tenant_id = ? AND route_id = ? "
                "AND vehicle_profile = ? AND leg = ?",
                (db.current_tenant(), rid, prof, leg))
    return {
        "cleared": 0 if dry_run else len(keys),
        "legs": [{"route_id": r, "vehicle_profile": p, "leg": l,
                  "reason": leg_reason.get((r, p, l))} for r, p, l in legs],
        "leg_count": len(legs),
        "route_ids": sorted({r for r, _p, _l in legs}),
        "profiles": sorted({p for _r, p, _l in legs}),
        "approximate": sum(1 for v in leg_reason.values()
                           if v and v.startswith("no bake record")),
        "dry_run": bool(dry_run),
        # one HERE call per leg. Returned so the UI can say how many before firing them,
        # not after — this endpoint is currently unauthenticated (ADMIN_TOKEN unset).
        "here_calls": len(legs),
    }


def summary():
    zs = list_zones()
    active = [z for z in zs if z["active"]]
    return {
        "zones": len(zs),
        "active": len(active),
        "routing": len([z for z in active if z["affects_routing"]]),
        "in_force_today": len(routing_zones()),
        "kinds": sorted({z["kind"] for z in zs}),
        # counted separately from `routing` because haul roads are the one kind that is
        # never in the avoid list — see HAUL_KIND
        "haul_roads": len([z for z in zs if z["kind"] == HAUL_KIND]),
        "haul_roads_in_force": len(haul_roads()),
    }

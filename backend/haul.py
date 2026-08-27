"""
Phase 4 — temporary haul roads.

A haul road is a zone of kind 'haul_road': a drawn LineString with an assigned speed.
Storage, dating and invalidation come from Phase 3 for free. What Phase 4 adds is the
routing half, and it exists because of one fact about HERE:

    HERE ROUTES ON HERE'S MAP. A haul road that has just been built across a field is
    not in that map, and no parameter makes it appear.

That single fact is why this module has two modes rather than one, and why the default
is the more expensive of them.

  MODE 'splice'  (default)   Assume HERE does not know the road exists.
      Route origin -> haul entry with HERE. Route haul exit -> destination with HERE.
      Between them, use the DRAWN line as the geometry, at the assigned speed. Two HERE
      calls per leg per haul road boundary instead of one. Always works, because it never
      asks HERE about a road it has never heard of. The geometry between entry and exit is
      exactly what was drawn, so it is as accurate as the drawing and no more.

  MODE 'via'                 Assume HERE does know the road.
      One call with the road's two ends as via-waypoints, and HERE finds its own way
      between them. Cheaper, and the geometry comes back snapped to real roads. It is
      correct only when the road is genuinely in HERE's map — otherwise HERE routes
      between the two points along whatever public roads it does know, produces a route
      that looks plausible and is wrong, and nothing in the response says so.

**Which mode a given road wants is a fact about HERE's map data, not a preference.**
/api/admin/diagnostics/haul-roads?zone_id=...&probe=true answers it: it sends both and
reports the difference. Until it has been run for a road, 'splice' is what happens,
because the failure mode of splice (geometry only as good as the drawing) is visible and
the failure mode of via (a confident wrong route) is not.

THE SPEED SUBSTITUTION
----------------------
HERE returns its own duration and will not take a custom one, so an assigned speed is
applied afterwards:

    splice   the haul stretch is the drawn line. Its duration is
             line_length_km / speed_kph outright — HERE never sees that stretch, so
             there is nothing to substitute, only to supply.
    via      the haul stretch is the HERE section between the two via waypoints, which
             exists only because the vias are sent as STOPOVERS and HERE therefore splits
             the response at them. Its HERE duration is replaced by
             section_length_km / speed_kph.

The adjusted total lands in route_geometry.duration_hr, which is what route_analysis()
reads, so an assigned speed changes cycle time, trips per day, tonnes and CO2. That is
the point of assigning one. HERE's own figure is kept in duration_hr_here so the
adjustment can be seen rather than inferred.

A haul road with no speed_kph is routed through and its duration left exactly as HERE
gave it. Null is not 'default speed'.

WHAT IS NOT VERIFIED HERE
-------------------------
Everything about live HERE behaviour, because HERE is never called from the sandbox:

  * whether HERE honours `alternatives` alongside via-waypoints (the code assumes NOT,
    on the strength of a comment; the probe settles it)
  * whether a stopover via actually splits the response into per-leg sections — the via
    mode's speed substitution depends entirely on that split
  * what HERE does when a via waypoint sits on no road at all

None of the three can be answered by a test in this repo. Run the diagnostic.
"""
import json
from datetime import datetime, timezone

import db
import zones
import here_routing

# See the module docstring. 'splice' is the default because its failure mode is visible.
MODES = ("splice", "via")
DEFAULT_MODE = "splice"


def _now():
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
#  Which routes use which haul road                                            #
# --------------------------------------------------------------------------- #
def links_for_route(route_id):
    """Haul-road links on one route, in traversal order for the LOADED leg."""
    return db.query(
        "SELECT * FROM route_haul_roads WHERE route_id = ? ORDER BY seq, zone_id",
        (route_id,))


def routes_for_zone(zone_id):
    """Which routes are attached to a haul road. Drives invalidation on an edit."""
    return [r["route_id"] for r in db.query(
        "SELECT route_id FROM route_haul_roads WHERE zone_id = ? ORDER BY route_id",
        (zone_id,))]


def link_counts():
    """zone_id -> number of routes attached, for the zones table's 'used by' column."""
    out = {}
    for r in db.query("SELECT zone_id, COUNT(*) AS n FROM route_haul_roads GROUP BY zone_id"):
        out[r["zone_id"]] = r["n"]
    return out


def attach(route_id, zone_id, seq=None):
    """
    Use a haul road on a route.

    Refuses a zone that is not a haul road: attaching a closure here would mean "route
    through the closed area", which is the opposite of what drawing it said. Refuses an
    unknown route or zone for the same reason the rest of the codebase does — a dangling
    link would surface later as a bake that quietly skips a road nobody can find.
    """
    if not db.query("SELECT id FROM routes WHERE id = ?", (route_id,)):
        return {"error": "route not found", "route_id": route_id}
    z = zones.get_zone(zone_id)
    if not z:
        return {"error": "zone not found", "zone_id": zone_id}
    if z["kind"] != zones.HAUL_KIND:
        return {"error": f"zone {zone_id} is a '{z['kind']}', not a haul road"}
    if not zones.as_line(z["geometry"]):
        return {"error": f"zone {zone_id} is not drawn as a line, so it cannot be traversed"}
    if db.query("SELECT * FROM route_haul_roads WHERE route_id = ? AND zone_id = ?",
                (route_id, zone_id)):
        return {"error": f"{zone_id} is already attached to {route_id}"}
    if seq is None:
        existing = links_for_route(route_id)
        seq = (max([l["seq"] for l in existing]) + 1) if existing else 0
    db.execute(
        "INSERT INTO route_haul_roads (route_id, zone_id, seq, created_at) "
        "VALUES (?, ?, ?, ?)", (route_id, zone_id, int(seq), _now()))
    refresh_origin_temp_km(route_id)
    return {"attached": {"route_id": route_id, "zone_id": zone_id, "seq": int(seq)}}


def detach(route_id, zone_id):
    if not db.query("SELECT * FROM route_haul_roads WHERE route_id = ? AND zone_id = ?",
                    (route_id, zone_id)):
        return {"error": f"{zone_id} is not attached to {route_id}"}
    db.execute("DELETE FROM route_haul_roads WHERE route_id = ? AND zone_id = ?",
               (route_id, zone_id))
    refresh_origin_temp_km(route_id)
    return {"detached": {"route_id": route_id, "zone_id": zone_id}}


def reorder(route_id, zone_ids):
    """
    Set the traversal order of a route's haul roads.

    Order matters and cannot be worked out from the geometry: two haul roads either side
    of a site are entered in one order going out and the other coming back, and picking
    the wrong one splices a line that doubles back on itself. The loaded leg follows this
    list; the return leg reverses it.
    """
    have = {l["zone_id"] for l in links_for_route(route_id)}
    want = list(zone_ids or [])
    # exactly, in both directions: a missing id would drop a road off the route and a
    # stray one would look accepted. Filtering the list to what happens to be attached
    # is the version that fails silently, so the check is on the raw list.
    if set(want) != have or len(want) != len(have):
        return {"error": "the list must name exactly the haul roads attached to this "
                         "route, once each",
                "attached": sorted(have), "sent": list(zone_ids or [])}
    for i, zid in enumerate(want):
        db.execute("UPDATE route_haul_roads SET seq = ? WHERE route_id = ? AND zone_id = ?",
                   (i, route_id, zid))
    refresh_origin_temp_km(route_id)
    return {"route_id": route_id, "order": want}


def clear_links_for_zone(zone_id):
    """Drop every link to a haul road. Called when the zone itself is deleted."""
    affected = routes_for_zone(zone_id)
    db.execute("DELETE FROM route_haul_roads WHERE zone_id = ?", (zone_id,))
    for rid in affected:
        refresh_origin_temp_km(rid)
    return len(affected)


# --------------------------------------------------------------------------- #
#  Planning a leg                                                              #
# --------------------------------------------------------------------------- #
def plan_leg(route_id, origin_pt, dest_pt, leg="loaded", on=None):
    """
    Which haul roads a leg runs through, in traversal order, each oriented for travel.

    Returns a list of steps:
        {"zone_id", "name", "mode", "speed_kph", "line", "length_km",
         "entry": (lat, lon), "exit": (lat, lon), "reversed": bool}

    Only haul roads that are ACTIVE, in force on `on`, have affects_routing set and are
    drawn as a line make it in. A haul road can be attached to a route months before it
    opens; the date range is what keeps it out of routing until then, and dropping it
    from the plan is how "not yet built" is expressed, rather than a separate flag.

    The return leg reverses the sequence and re-orients each line, because a route that
    enters haul road A then B on the way out meets B then A on the way home.
    """
    links = links_for_route(route_id)
    if not links:
        return []
    by_id = {z["id"]: z for z in zones.haul_roads(on=on)}
    steps = []
    for l in links:
        z = by_id.get(l["zone_id"])
        if not z or not z["affects_routing"]:
            continue
        line = zones.as_line(z["geometry"])
        if not line:
            continue
        steps.append({"zone_id": z["id"], "name": z["name"],
                      "mode": (z["haul_mode"] or DEFAULT_MODE),
                      "speed_kph": z["speed_kph"], "_line": line})
    if leg != "loaded":
        steps.reverse()

    # orient each road relative to where the truck is coming from: the origin for the
    # first, and the previous road's exit for each one after it
    cursor = list(origin_pt)
    out = []
    for s in steps:
        line, rev = zones.oriented_line({"type": "LineString", "coordinates": s["_line"]}, cursor)
        out.append({
            "zone_id": s["zone_id"], "name": s["name"], "mode": s["mode"],
            "speed_kph": s["speed_kph"],
            "line": line,
            "length_km": round(zones.line_length_km(line), 3),
            # HERE takes lat,lon; GeoJSON is lon,lat. Converted once, here, so no caller
            # has to remember which way round it is.
            "entry": (line[0][1], line[0][0]),
            "exit": (line[-1][1], line[-1][0]),
            "reversed": rev,
        })
        cursor = line[-1]
    return out


def haul_km_for_route(route_id, on=None):
    """
    Total drawn haul-road length on a route's loaded leg.

    This is what populates routes.origin_temp_km — the scalar that has been seeded and
    displayed since V2 and computed with nowhere. Phase 4 makes it derived: it now means
    "km of this route that run on drawn temporary haul road", recomputed whenever the
    links or the drawings change. It is NOT read back by the router; the geometry is the
    source of truth and this is the summary of it.
    """
    origin = _route_endpoints(route_id)
    if not origin:
        return 0.0
    o, d = origin
    return round(sum(s["length_km"] for s in plan_leg(route_id, o, d, "loaded", on=on)), 3)


def _route_endpoints(route_id):
    """([lon, lat] origin, [lon, lat] destination) for a route, or None."""
    r = db.query("SELECT * FROM routes WHERE id = ?", (route_id,))
    if not r:
        return None
    locs = {l["id"]: l for l in db.query("SELECT * FROM locations")}
    o, d = locs.get(r[0]["origin_id"]), locs.get(r[0]["dest_id"])
    if not o or not d:
        return None

    def pt(l):
        lat = l.get("gate_lat") if l.get("gate_lat") is not None else l.get("lat")
        lon = l.get("gate_lon") if l.get("gate_lon") is not None else l.get("lon")
        if lat is None or lon is None:
            return None
        return [float(lon), float(lat)]

    a, b = pt(o), pt(d)
    return (a, b) if a and b else None


def refresh_origin_temp_km(route_id=None):
    """
    Recompute routes.origin_temp_km from the drawn haul roads.

    Called from inside attach/detach/reorder/clear_links_for_zone rather than only from
    the endpoints, so the column cannot drift when something else calls those directly —
    and again from the zone-write endpoints, because an edit to a road's geometry, dates
    or active flag moves it without any link changing. Cheap: no HERE calls, pure
    geometry, so calling it twice costs nothing.
    """
    ids = [route_id] if route_id else [r["id"] for r in db.query("SELECT id FROM routes")]
    changed = 0
    for rid in ids:
        km = haul_km_for_route(rid)
        db.execute("UPDATE routes SET origin_temp_km = ? WHERE id = ?", (km, rid))
        changed += 1
    return {"routes_updated": changed}


# --------------------------------------------------------------------------- #
#  Routing a leg through its haul roads                                        #
# --------------------------------------------------------------------------- #
def _join(coords, more):
    """Append a polyline to another without repeating the shared vertex."""
    if not coords:
        return list(more)
    return coords + (more[1:] if (more and coords[-1] == more[0]) else list(more))


def _speed_hr(length_km, speed_kph):
    return (length_km / speed_kph) if (speed_kph and speed_kph > 0) else None


def route_with_haul(o_lat, o_lon, d_lat, d_lon, profile, steps, factors=None,
                    avoid_areas=None, laden=True, alternatives=1):
    """
    One leg, routed through its haul roads. Returns a list shaped exactly like
    here_routing.routes() so _bake_leg does not need to know which path it took, plus
    Phase 4 provenance on entry 0:

        haul_km            drawn or measured km on haul road
        duration_hr_here   HERE's own total before any speed substitution
        haul_zones         zone ids applied, in traversal order
        haul_calls         HERE requests this leg cost
        haul_detail        per-road: mode, length, HERE duration, adjusted duration

    With no steps this is a plain here_routing.routes() call and nothing changes.

    ⚠️ Only ONE option is returned whenever any haul road applies, whatever
    `alternatives` asks for. In 'via' mode that is HERE's own behaviour (believed, not
    verified — see the module docstring). In 'splice' mode it is this code's decision:
    alternatives across a spliced route would be the cross product of the alternatives on
    each sub-call, and ranking those against each other means comparing durations that
    are partly HERE's and partly assigned. Returning the best of each sub-call and saying
    'one option' is the honest version. The consequence is real and visible: routes with
    a haul road have nothing for promote_alternative() to promote, and the UI says so.
    """
    if not steps:
        return here_routing.routes(o_lat, o_lon, d_lat, d_lon, profile, factors,
                                   avoid_areas=avoid_areas, alternatives=alternatives,
                                   laden=laden)

    via_steps = [s for s in steps if s["mode"] == "via"]
    splice_steps = [s for s in steps if s["mode"] != "via"]

    if via_steps and not splice_steps:
        return _route_all_via(o_lat, o_lon, d_lat, d_lon, profile, steps,
                              factors, avoid_areas, laden)
    if splice_steps and not via_steps:
        return _route_all_splice(o_lat, o_lon, d_lat, d_lon, profile, steps,
                                 factors, avoid_areas, laden)
    # Mixed modes on one leg. Splicing wins: every road is spliced, including the ones
    # marked 'via'. Interleaving the two would need the via road to be reached by a
    # sub-call that itself carries vias, and the section indices that the via speed
    # substitution reads would no longer line up with the roads they belong to. Reported
    # rather than silently downgraded.
    res = _route_all_splice(o_lat, o_lon, d_lat, d_lon, profile, steps,
                            factors, avoid_areas, laden)
    res[0]["haul_note"] = (
        f"{len(via_steps)} haul road(s) on this leg are set to 'via' mode but were "
        f"spliced instead: modes cannot be mixed on one leg.")
    return res


def _route_all_splice(o_lat, o_lon, d_lat, d_lon, profile, steps,
                      factors, avoid_areas, laden):
    """
    Origin -> entry1, [drawn road 1], exit1 -> entry2, [drawn road 2], ... -> destination.

    N haul roads cost N+1 HERE calls. Each connecting call asks for one option only:
    alternatives on a sub-leg are not alternatives on the route.
    """
    coords, dist, here_hr, haul_km = [], 0.0, 0.0, 0.0
    detail, adjusted_hr = [], 0.0
    cur_lat, cur_lon = o_lat, o_lon
    calls = 0

    for s in steps:
        e_lat, e_lon = s["entry"]
        r = here_routing.routes(cur_lat, cur_lon, e_lat, e_lon, profile, factors,
                                avoid_areas=avoid_areas, alternatives=1, laden=laden)[0]
        calls += 1
        coords = _join(coords, r["geometry"])
        dist += r["distance_km"]
        here_hr += r["duration_hr"]
        adjusted_hr += r["duration_hr"]

        # the haul stretch itself: drawn geometry, drawn length, assigned duration.
        # HERE is not asked about it and contributes nothing to duration_hr_here, which
        # is why a spliced leg's raw and adjusted durations differ by the whole haul
        # time rather than by a substitution.
        coords = _join(coords, s["line"])
        dist += s["length_km"]
        haul_km += s["length_km"]
        hr = _speed_hr(s["length_km"], s["speed_kph"])
        adjusted_hr += (hr or 0.0)
        detail.append({"zone_id": s["zone_id"], "name": s["name"], "mode": "splice",
                       "length_km": s["length_km"], "speed_kph": s["speed_kph"],
                       "here_duration_hr": None,
                       "applied_duration_hr": (round(hr, 3) if hr is not None else None),
                       "reversed": s["reversed"],
                       "note": (None if hr is not None else
                                "no speed assigned — this stretch contributes 0 h to the "
                                "cycle. Set a speed on the haul road.")})
        cur_lat, cur_lon = s["exit"]

    r = here_routing.routes(cur_lat, cur_lon, d_lat, d_lon, profile, factors,
                            avoid_areas=avoid_areas, alternatives=1, laden=laden)[0]
    calls += 1
    coords = _join(coords, r["geometry"])
    dist += r["distance_km"]
    here_hr += r["duration_hr"]
    adjusted_hr += r["duration_hr"]

    return [{
        "geometry": coords,
        "distance_km": round(dist, 2),
        "duration_hr": round(adjusted_hr, 3),
        "duration_hr_here": round(here_hr, 3),
        "haul_km": round(haul_km, 3),
        "haul_zones": [s["zone_id"] for s in steps],
        "haul_mode": "splice",
        "haul_calls": calls,
        "haul_detail": detail,
        "sections": [],
    }]


def _route_all_via(o_lat, o_lon, d_lat, d_lon, profile, steps,
                   factors, avoid_areas, laden):
    """
    One HERE call, each road's entry and exit sent as STOPOVER via-waypoints.

    The stopovers are what make this mode's speed substitution possible: HERE splits the
    response into one section per leg between waypoints, so with N roads there are 2N+1
    sections and the haul stretches are the even-indexed... no — sections 1, 3, 5, …
    counting from 0: origin->entry1 is 0, entry1->exit1 is 1, exit1->entry2 is 2. Section
    2i+1 is road i.

    ⚠️ If HERE does NOT split at the vias, that mapping is wrong and there is no way to
    tell which part of one summary was the haul road. The response is then returned with
    the speed substitution SKIPPED and a note saying so, rather than applying it to a
    section that might be the whole route. Run the probe.
    """
    via = []
    for s in steps:
        via.append(s["entry"])
        via.append(s["exit"])
    opts = here_routing.routes(o_lat, o_lon, d_lat, d_lon, profile, factors,
                               avoid_areas=avoid_areas, alternatives=1, laden=laden,
                               via=via, pass_through=False)
    best = opts[0]
    secs = best.get("sections") or []
    expected = len(via) + 1
    split_ok = (len(secs) == expected)

    here_hr = best["duration_hr"]
    adjusted_hr = here_hr
    haul_km, detail = 0.0, []

    for i, s in enumerate(steps):
        idx = 2 * i + 1
        sec = secs[idx] if (split_ok and idx < len(secs)) else None
        seg_km = sec["distance_km"] if sec else s["length_km"]
        seg_here_hr = sec["duration_hr"] if sec else None
        haul_km += seg_km
        hr = _speed_hr(seg_km, s["speed_kph"])
        applied = None
        if split_ok and sec is not None and hr is not None:
            adjusted_hr += (hr - seg_here_hr)
            applied = round(hr, 3)
        detail.append({
            "zone_id": s["zone_id"], "name": s["name"], "mode": "via",
            "length_km": round(seg_km, 3), "speed_kph": s["speed_kph"],
            "here_duration_hr": seg_here_hr,
            "applied_duration_hr": applied,
            "reversed": s["reversed"],
            "note": (None if applied is not None else
                     ("HERE did not split the response at the via waypoints, so the haul "
                      "stretch could not be isolated and HERE's own duration stands."
                      if not split_ok else
                      "no speed assigned — HERE's own duration stands.")),
        })

    out = {
        "geometry": best["geometry"],
        "distance_km": best["distance_km"],
        "duration_hr": round(adjusted_hr, 3),
        "duration_hr_here": round(here_hr, 3),
        "haul_km": round(haul_km, 3),
        "haul_zones": [s["zone_id"] for s in steps],
        "haul_mode": "via",
        "haul_calls": 1,
        "haul_detail": detail,
        "sections": secs,
        "sections_split_at_vias": split_ok,
    }
    if not split_ok:
        out["haul_note"] = (
            f"HERE did not split the response at the via waypoints: {len(secs)} "
            f"section(s) came back for {len(via)} via waypoint(s), where {expected} were "
            f"expected. The haul stretch could not be isolated, so NO assigned speed was "
            f"applied on this leg and HERE's own duration stands. Either the vias were "
            f"not treated as stopovers or HERE merged the sections — run "
            f"/api/admin/diagnostics/haul-roads?probe=true, and switch these roads to "
            f"'splice' mode if it confirms it.")
    # a list of one, like here_routing.routes() and _route_all_splice(): a leg with a
    # haul road has exactly one option, but the caller must not have to know that
    return [out]


# --------------------------------------------------------------------------- #
#  Invalidation                                                                #
# --------------------------------------------------------------------------- #
def invalidate_for_zone(zone_id, dry_run=False):
    """
    Clear baked geometry that a haul-road change makes wrong.

    Exact, with no heuristic half — and that is not luck. A closure is invisible in the
    geometry of a route that avoided it, which is why zones.invalidate() has to guess for
    pre-Phase-3 rows. A haul road is different: nothing uses one unless it is explicitly
    attached, so "which legs does this change" is a table lookup, both for legs already
    baked with it (route_geometry.haul_zones) and for legs that will now pick it up
    (route_haul_roads). No proximity test, no pad, no approximation.

    Returns the same shape as zones.invalidate() so the frontend's re-bake loop is
    unchanged.
    """
    linked = set(routes_for_zone(zone_id))
    rows = db.query("SELECT route_id, vehicle_profile, leg, haul_zones FROM route_geometry")
    keys, why = [], {}
    for g in rows:
        tag = g.get("haul_zones")
        baked_with = zone_id in [t.strip() for t in (tag or "").split(",") if t.strip()]
        if baked_with:
            k = (g["route_id"], g["vehicle_profile"], g["leg"])
            keys.append(k); why.setdefault(k, "was baked through this haul road")
        elif g["route_id"] in linked:
            k = (g["route_id"], g["vehicle_profile"], g["leg"])
            keys.append(k); why.setdefault(k, "this route now uses this haul road")

    legs = sorted(set(keys))
    if not dry_run:
        for rid, prof, leg in legs:
            db.execute("DELETE FROM route_geometry WHERE route_id = ? AND "
                       "vehicle_profile = ? AND leg = ?", (rid, prof, leg))
    return {
        "cleared": 0 if dry_run else len(legs),
        "legs": [{"route_id": r, "vehicle_profile": p, "leg": l,
                  "reason": why.get((r, p, l))} for r, p, l in legs],
        "leg_count": len(legs),
        "route_ids": sorted({r for r, _p, _l in legs}),
        "profiles": sorted({p for _r, p, _l in legs}),
        "approximate": 0,      # nothing here is approximate; see the docstring
        "dry_run": bool(dry_run),
        "here_calls": _here_calls_for(legs),
    }


def invalidate_for_route(route_id, dry_run=False):
    """Clear every baked leg of one route — used when its haul-road set changes."""
    rows = db.query("SELECT DISTINCT route_id, vehicle_profile, leg FROM route_geometry "
                    "WHERE route_id = ?", (route_id,))
    legs = sorted({(g["route_id"], g["vehicle_profile"], g["leg"]) for g in rows})
    if not dry_run:
        db.execute("DELETE FROM route_geometry WHERE route_id = ?", (route_id,))
    return {
        "cleared": 0 if dry_run else len(legs),
        "legs": [{"route_id": r, "vehicle_profile": p, "leg": l,
                  "reason": "the haul roads on this route changed"} for r, p, l in legs],
        "leg_count": len(legs),
        "route_ids": sorted({r for r, _p, _l in legs}),
        "profiles": sorted({p for _r, p, _l in legs}),
        "approximate": 0,
        "dry_run": bool(dry_run),
        "here_calls": _here_calls_for(legs),
    }


def _here_calls_for(legs):
    """
    HERE calls a re-bake of these legs will cost.

    NOT one per leg any more, which is what every pre-Phase-4 estimate assumed. A spliced
    leg costs one call per haul road plus one, so a route with two spliced haul roads
    costs three calls a leg and six for both directions. The zone UI quotes this number
    before spending it, so it has to count the splices rather than the legs.
    """
    total = 0
    plans = {}
    for rid, _prof, leg in legs:
        if rid not in plans:
            ends = _route_endpoints(rid)
            plans[rid] = ends
        ends = plans[rid]
        if not ends:
            total += 1
            continue
        steps = plan_leg(rid, ends[0], ends[1], leg)
        splices = [s for s in steps if s["mode"] != "via"]
        total += (len(splices) + 1) if splices else 1
    return total


# --------------------------------------------------------------------------- #
#  Diagnostics                                                                 #
# --------------------------------------------------------------------------- #
def diagnostics(zone_id=None, route_id=None, profile=None, probe=False):
    """
    What Phase 4 would send HERE, and with probe=true what HERE actually does with it.

    probe=true spends up to THREE HERE calls on one route and one haul road:
      1. the leg with no haul road at all — the baseline
      2. the same leg with the road's ends as stopover vias — does HERE still return
         alternatives? does it split the response into sections?
      3. the first spliced sub-leg, origin -> haul entry — does HERE reach the entry
         point at all, or is the drawn entry somewhere no truck can get to?

    Read (2) before believing anything this codebase says about via-waypoints. Both
    claims in the Phase 4 notes — that HERE drops alternatives, and that stopovers split
    the response — are inherited from a code comment and have never been checked against
    the live service.
    """
    import conversions
    import network

    profile = profile or network.DEFAULT_PROFILE
    factors = conversions.load_factors()
    roads = zones.haul_roads(include_inactive=True, require_geometry=False)
    counts = link_counts()

    out = {
        "haul_roads": [{
            "id": z["id"], "name": z["name"], "active": z["active"],
            "affects_routing": z["affects_routing"],
            "in_force_today": zones.applies_on(z),
            "mode": z["haul_mode"] or DEFAULT_MODE,
            "mode_is_default": not z["haul_mode"],
            "speed_kph": z["speed_kph"],
            "length_km": z["length_km"],
            "is_line": bool(zones.as_line(z["geometry"])),
            "routes_attached": counts.get(z["id"], 0),
        } for z in roads],
        "links": db.query("SELECT * FROM route_haul_roads ORDER BY route_id, seq"),
        "default_mode": DEFAULT_MODE,
        "here_configured": here_routing.configured(),
        "probe": None,
        "unverified": [
            "whether HERE honours 'alternatives' when via-waypoints are present",
            "whether a stopover via splits the response into per-leg sections — the "
            "'via' mode speed substitution depends entirely on it",
            "what HERE does with a via waypoint that sits on no mapped road",
        ],
    }

    if route_id:
        ends = _route_endpoints(route_id)
        if ends:
            o, d = ends
            for leg in ("loaded", "return"):
                a, b = (o, d) if leg == "loaded" else (d, o)
                steps = plan_leg(route_id, a, b, leg)
                out.setdefault("plan", {})[leg] = [{
                    "zone_id": s["zone_id"], "name": s["name"], "mode": s["mode"],
                    "speed_kph": s["speed_kph"], "length_km": s["length_km"],
                    "entry": s["entry"], "exit": s["exit"], "reversed": s["reversed"],
                    "assigned_duration_hr": (round(_speed_hr(s["length_km"], s["speed_kph"]), 3)
                                             if _speed_hr(s["length_km"], s["speed_kph"]) else None),
                } for s in steps]
            out["here_calls_per_bake"] = _here_calls_for(
                [(route_id, profile, l) for l in ("loaded", "return")])
        else:
            out["plan_error"] = f"route {route_id} has no usable origin/destination coordinates"

    if not probe:
        return out
    if not here_routing.configured():
        out["probe"] = {"error": "HERE_API_KEY is not set on the server"}
        return out
    if not route_id:
        out["probe"] = {"error": "probe needs route_id — it routes one real leg"}
        return out
    ends = _route_endpoints(route_id)
    if not ends:
        out["probe"] = {"error": "route has no usable coordinates"}
        return out

    o, d = ends
    steps = plan_leg(route_id, o, d, "loaded")
    if zone_id:
        steps = [s for s in steps if s["zone_id"] == zone_id]
    avoid = zones.avoid_areas()
    p = {"route_id": route_id, "profile": profile,
         "haul_roads_on_leg": [s["zone_id"] for s in steps]}

    p["baseline_no_haul"] = here_routing.probe(
        o[1], o[0], d[1], d[0], profile, factors, avoid_areas=(avoid or None),
        alternatives=here_routing.ALTERNATIVES_DEFAULT)

    if steps:
        s = steps[0]
        p["with_via"] = here_routing.probe(
            o[1], o[0], d[1], d[0], profile, factors, avoid_areas=(avoid or None),
            alternatives=here_routing.ALTERNATIVES_DEFAULT,
            via=[s["entry"], s["exit"]], pass_through=False)
        p["splice_first_leg"] = here_routing.probe(
            o[1], o[0], s["entry"][0], s["entry"][1], profile, factors,
            avoid_areas=(avoid or None), alternatives=1)
        base_n = p["baseline_no_haul"].get("routes_returned")
        via_n = p["with_via"].get("routes_returned")
        p["reading"] = {
            "alternatives_without_via": base_n,
            "alternatives_with_via": via_n,
            "here_drops_alternatives_with_via": (
                None if (base_n is None or via_n is None) else (via_n < base_n)),
            "sections_split_at_vias": p["with_via"].get("sections_split_at_vias"),
            "via_mode_usable": (p["with_via"].get("sections_split_at_vias") is True),
        }
    else:
        p["note"] = ("no haul road applies to this route's loaded leg today, so only the "
                     "baseline was routed. Attach one, or check its dates and active flag.")
    out["probe"] = p
    return out


def summary():
    roads = zones.haul_roads(include_inactive=True, require_geometry=False)
    counts = link_counts()
    return {
        "haul_roads": len(roads),
        "in_force": len(zones.haul_roads()),
        "with_speed": len([z for z in roads if z["speed_kph"]]),
        "without_speed": len([z for z in roads if not z["speed_kph"]]),
        "links": db.count_haul_links(),
        "routes_using": len({r["route_id"] for r in
                             db.query("SELECT route_id FROM route_haul_roads")}),
        "attached_counts": counts,
        "default_mode": DEFAULT_MODE,
    }

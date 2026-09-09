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

# How many route options to ask HERE for. Note HERE counts alternatives *in addition
# to* the optimal route, so the wire value is this minus one.
ALTERNATIVES_DEFAULT = 3


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


def _via_values(via, pass_through=False):
    """
    HERE `via` query values for a list of (lat, lon) waypoints.

    Two flavours, and the difference decides whether Phase 4's speed model can work
    at all:

      pass_through=False  (default)  a STOPOVER. HERE splits the response into one
        section per leg between waypoints, each with its own summary. That per-section
        summary is the only way to get HERE's own length and duration for *just* the
        stretch between two points, which is exactly what substituting an assigned haul
        speed needs. The cost is a modelled stop at each waypoint (and no U-turn there).

      pass_through=True   HERE routes through the point without stopping and does NOT
        split the section. Cleaner geometry, but the haul stretch is then
        indistinguishable inside one summary, so the speed substitution has nothing to
        bite on.

    Phase 4 uses stopovers deliberately. See haul.py for what is done with the sections.
    """
    out = []
    for (lat, lon) in (via or []):
        v = f"{lat},{lon}"
        if pass_through:
            v += "!passThrough=true"
        out.append(v)
    return out


def _query(params, via_values):
    """
    Encode params plus any number of repeated `via` keys.

    urlencode takes a dict, and a dict cannot hold two `via` entries — which is why via
    support could not be bolted on without changing how the query string is built. A
    list of pairs can, and HERE requires the vias in traversal order, which a list also
    preserves and a dict would not.
    """
    pairs = list(params.items())
    if via_values:
        # HERE reads via waypoints in the order they appear in the query string
        insert_at = next((i for i, (k, _v) in enumerate(pairs) if k == "destination"), len(pairs))
        for n, v in enumerate(via_values):
            pairs.insert(insert_at + n, ("via", v))
    return urllib.parse.urlencode(pairs)


def routes(o_lat, o_lon, d_lat, d_lon, profile, factors=None, avoid_areas=None,
           alternatives=1, laden=True, via=None, pass_through=False,
           departure_time=None):
    """
    Route origin -> destination for a vehicle profile, returning up to `alternatives`
    options ranked best-first.

    Returns a list of {"geometry": [[lon,lat],...], "distance_km": float,
    "duration_hr": float, "sections": [...]} — always at least one entry, or
    RuntimeError. Each entry in "sections" is {"geometry", "distance_km",
    "duration_hr"} for one HERE section; with stopover via-waypoints that is one entry
    per leg between waypoints, which is what the haul-road speed model reads.

    One HTTP call covers every alternative, so asking for 3 costs the same as asking
    for 1. Set laden=False for the unladen return leg.

    `via` is a list of (lat, lon) waypoints in traversal order. ⚠️ HERE is documented to
    ignore `alternatives` once a request carries via-waypoints — that has NOT been
    verified against the live API from this codebase. Run
    /api/admin/diagnostics/haul-roads?probe=true, which sends the same route with and
    without vias and reports how many routes came back each time, before believing
    either answer.

    ⚠️ `departure_time` — NEW 2026-09-09, and it exists because of a measured problem, not
    a feature request. R001's laden leg was baked at 06:29 as 16.15 km and re-requested at
    ~08:00 as 15.11 km: same endpoints, same profile, same (empty) avoid set, one section,
    no notices. **Nothing in this request pinned the time**, so whatever HERE does about
    time-dependent conditions was free to change between the two calls, and every figure
    derived from a baked route — cycle, trips/day, vehicles, tonnes, t·km, CO2 — moved
    with it.

    🔴 **Passing None keeps today's behaviour exactly.** Nothing in the bake path sets this
    yet, deliberately: what pinning actually does to HERE's answer is a question for
    departure_probe(), not an assumption to bake in. Decide from the probe, then pin.
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
    # 3 options means alternatives=2.
    want = max(1, int(alternatives or 1))
    if want > 1:
        params["alternatives"] = want - 1
    params.update(_truck_params(profile, factors, laden=laden))
    if avoid_areas:  # list of "bbox:west,south,east,north" strings
        params["avoid[areas]"] = "|".join(avoid_areas)
    if departure_time:
        params["departureTime"] = str(departure_time)
    url = HERE_ENDPOINT + "?" + _query(params, _via_values(via, pass_through))
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
        # caller always gets one continuous line per alternative, but keep the
        # per-section breakdown alongside it — with via stopovers that breakdown is
        # the haul stretch, and collapsing it loses the only figure worth having
        coords, dist_m, dur_s, parts = [], 0.0, 0.0, []
        for sec in secs:
            pts = decode_polyline(sec["polyline"])
            coords.extend(pts[1:] if coords else pts)
            summ = sec.get("summary") or {}
            dist_m += summ.get("length", 0)
            dur_s += summ.get("duration", 0)
            parts.append({
                "geometry": pts,
                "distance_km": round(summ.get("length", 0) / 1000.0, 2),
                "duration_hr": round(summ.get("duration", 0) / 3600.0, 3),
            })
        out.append({
            "geometry": coords,
            "distance_km": round(dist_m / 1000.0, 2),
            "duration_hr": round(dur_s / 3600.0, 3),
            "sections": parts,
        })
    if not out:
        raise RuntimeError("route had no usable sections: " + json.dumps(data)[:200])
    return out


def route(o_lat, o_lon, d_lat, d_lon, profile, factors=None, avoid_areas=None,
          via=None, laden=True):
    """Single best route — thin wrapper over routes() for callers that want just one."""
    return routes(o_lat, o_lon, d_lat, d_lon, profile, factors,
                  avoid_areas=avoid_areas, alternatives=1, via=via, laden=laden)[0]


# --------------------------------------------------------------------------- #
#  Diagnostics                                                                 #
# --------------------------------------------------------------------------- #
def probe(o_lat, o_lon, d_lat, d_lon, profile, factors=None, avoid_areas=None,
          alternatives=ALTERNATIVES_DEFAULT, laden=True, via=None, pass_through=False,
          departure_time=None):
    """
    Make one real HERE call and report what went out and what came back.

    This exists because several failure modes look identical from the outside: a
    profile whose truck parameters never reached HERE, and a road network that
    genuinely routes every vehicle the same way, both produce identical geometry for
    every profile. So does an 'alternatives' request that HERE quietly declined. The
    only way to tell them apart is to see the actual request and response.

    With `via` set it also answers the two Phase 4 questions that cannot be answered
    from the code: whether HERE still returns alternatives when via-waypoints are
    present (`alternatives_honoured`), and whether a stopover via actually split the
    response into per-leg sections (`section_summaries`) — without that split there is
    no HERE figure for the haul stretch alone and the assigned-speed substitution has
    nothing to attach to.

    Never raises — a failure is part of the answer. The API key is redacted.
    """
    key = api_key()
    params = {
        "origin": f"{o_lat},{o_lon}",
        "destination": f"{d_lat},{d_lon}",
        "return": "polyline,summary",
        "apiKey": key,
    }
    want = max(1, int(alternatives or 1))
    if want > 1:
        params["alternatives"] = want - 1
    params.update(_truck_params(profile, factors, laden=laden))
    if avoid_areas:
        params["avoid[areas]"] = "|".join(avoid_areas)
    if departure_time:
        params["departureTime"] = str(departure_time)
    via_values = _via_values(via, pass_through)

    sent = {k: v for k, v in params.items() if k != "apiKey"}
    if via_values:
        sent["via"] = via_values
    out = {
        "profile": profile, "laden": laden,
        "params_sent": sent,
        "truck_params_present": any(k.startswith("vehicle[") for k in sent),
        "alternatives_requested_param": sent.get("alternatives"),
        "via_count": len(via_values),
        "via_pass_through": bool(pass_through),
        "api_key_set": bool(key),
    }
    if not key:
        out["error"] = "HERE_API_KEY is not set on the server"
        return out

    url = HERE_ENDPOINT + "?" + _query(params, via_values)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        out["error"] = f"HTTP {e.code}: {body[:400]}"
        return out
    except Exception as e:
        out["error"] = str(e)[:400]
        return out

    rts = data.get("routes") or []
    out["routes_returned"] = len(rts)
    out["alternatives_honoured"] = len(rts) > 1
    summaries, notices = [], []
    for i, rt in enumerate(rts):
        secs = rt.get("sections") or []
        dist = sum((s.get("summary") or {}).get("length", 0) for s in secs)
        dur = sum((s.get("summary") or {}).get("duration", 0) for s in secs)
        summaries.append({"alt_index": i, "sections": len(secs),
                          "distance_km": round(dist / 1000.0, 2),
                          "duration_hr": round(dur / 3600.0, 3)})
        if i == 0:
            # per-section breakdown of the best route only. With N stopover vias this
            # should be N+1 entries; if it is 1, HERE did not split and the haul
            # stretch cannot be isolated from the response.
            out["section_summaries"] = [
                {"index": j,
                 "distance_km": round((s.get("summary") or {}).get("length", 0) / 1000.0, 2),
                 "duration_hr": round((s.get("summary") or {}).get("duration", 0) / 3600.0, 3)}
                for j, s in enumerate(secs)]
            if via_values:
                out["sections_split_at_vias"] = (len(secs) == len(via_values) + 1)
        for s in secs:
            for n in (s.get("notices") or []):
                notices.append({"alt_index": i, "title": n.get("title"),
                                "code": n.get("code"), "severity": n.get("severity")})
    out["summaries"] = summaries
    out["notices"] = notices          # e.g. violated truck restrictions
    out["multi_section"] = any(s["sections"] > 1 for s in summaries)
    if len(summaries) > 1:
        d = [s["distance_km"] for s in summaries]
        out["alternatives_distinct"] = len(set(d)) > 1
    return out


# --------------------------------------------------------------------------- #
#  The departure-time experiment                                              #
# --------------------------------------------------------------------------- #
#
# 🔴 WHY THIS EXISTS. On 2026-09-09 R001's laden leg was measured against the live
# deployment and did not reproduce:
#
#     cached, baked 06:29:59   16.15 km / 0.318 h
#     re-requested ~08:00      15.11 km / 0.291 h
#
# Same endpoints, same profile, same (empty) avoid set, one HERE section, no notices --
# and the return leg reproduced to the metre (16.64 both times). Three repeats within a
# few minutes all gave 15.11, including for a different vehicle profile, so the answer is
# stable NOW and was a different road THIS MORNING. Corroborating: at bake time HERE had
# offered a 15.22 km alternative and ranked the 16.15 km road first; it ranks the short
# one first now.
#
# The only input that changed is the clock, and nothing in the request pins it. That is a
# hypothesis with a name, not a proven cause -- so this is the experiment, not a fix.
#
# ⚠️ WHAT THIS DOES NOT DO. It does not pin anything. Nothing in the bake path passes
# departure_time. Deciding to pin bakes to a representative time is a product decision
# that should follow the answer, not precede it.
#
# ⭐ THE CONTROL IS RUN TWICE, FIRST AND LAST. If HERE's unpinned answer changes DURING
# the experiment, then nothing measured between the two controls can be attributed to the
# departureTime values, and the report says so instead of quietly reporting a difference.

import hashlib          # noqa: E402  (kept beside the code that uses it)
from datetime import datetime, timedelta, timezone   # noqa: E402

#: Times of day the default experiment brackets a working day with. Chosen to straddle
#: the morning peak, mid-morning, and the evening peak -- NOT sourced from any traffic
#: study, and deliberately few, because each one is a real HERE request.
_DEFAULT_HOURS = ((6, 30), (8, 0), (11, 0), (17, 0))

#: HERE accepts the literal `any` to mean "ignore time-dependent effects entirely".
#: Included as its own row because it is the candidate value for pinning: if `any`
#: reproduces and the unpinned control does not, `any` is the answer.
TIME_ANY = "any"


def _fingerprint(coords):
    """
    A short stable hash of a decoded polyline.

    ⭐ Distance alone cannot answer this question. Two genuinely different roads can come
    back within rounding of each other, and a route that changed shape but not length
    would read as 'no change'. The fingerprint is what makes "same km" and "same road"
    separable, which is the whole point of the experiment.
    """
    h = hashlib.sha1()
    for pt in coords or ():
        h.update(f"{pt[0]:.5f},{pt[1]:.5f};".encode())
    return h.hexdigest()[:12] if coords else None


def default_departure_times(now=None):
    """
    Four ISO timestamps on the next weekday, and the source of the timezone.

    Returns (times, tz_note). ⚠️ A past departureTime is not a sensible request, so this
    always lands on a FUTURE weekday rather than on the 06:29 the bake actually happened
    at -- which means the experiment can show that the answer varies with time of day, but
    can never reproduce that specific morning.
    """
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        tz, note = ZoneInfo("Europe/Tallinn"), "Europe/Tallinn from the system tz database"
    except Exception:
        # ⚠️ A guess, and labelled as one: EEST is +03:00 but only for part of the year.
        tz, note = timezone(timedelta(hours=3)), "fixed +03:00 -- no tz database available, EEST ASSUMED"
    local = now.astimezone(tz)
    day = local.date() + timedelta(days=1)
    while day.weekday() > 4:            # Mon=0 .. Fri=4
        day += timedelta(days=1)
    return ([datetime(day.year, day.month, day.day, h, m, tzinfo=tz).isoformat()
             for (h, m) in _DEFAULT_HOURS], note)


def departure_probe(o_lat, o_lon, d_lat, d_lon, profile, factors=None, avoid_areas=None,
                    laden=True, times=None, alternatives=1, now=None):
    """
    Does HERE's answer for one leg depend on the departure time we do not send?

    Runs the SAME request several times and reports distance, duration, section count and
    a geometry fingerprint for each:

        control_before   no departureTime at all -- exactly what a bake sends today
        any              departureTime=any
        <each time>      departureTime pinned to that ISO timestamp
        control_after    no departureTime again

    Never raises; a failed run is a row with an error. Costs one HERE request per row.

    The verdict block states only what was measured, plus one conditional reading. It
    cannot prove WHY an answer moved -- only that it did, or did not, under these inputs.
    """
    times = list(times) if times else None
    tz_note = None
    if times is None:
        times, tz_note = default_departure_times(now=now)

    def one(label, dep):
        row = {"label": label, "departure_time": dep}
        try:
            opts = routes(o_lat, o_lon, d_lat, d_lon, profile, factors,
                          avoid_areas=avoid_areas, alternatives=alternatives,
                          laden=laden, departure_time=dep)
            best = opts[0]
            row.update({"distance_km": best["distance_km"],
                        "duration_hr": best["duration_hr"],
                        "sections": len(best.get("sections") or []),
                        "options": len(opts),
                        "fingerprint": _fingerprint(best.get("geometry")),
                        "error": None})
        except Exception as e:
            row.update({"distance_km": None, "duration_hr": None, "sections": None,
                        "options": 0, "fingerprint": None, "error": str(e)[:300]})
        return row

    rows = [one("control_before", None), one(TIME_ANY, TIME_ANY)]
    rows += [one(t, t) for t in times]
    rows.append(one("control_after", None))

    by = {r["label"]: r for r in rows}
    cb, ca = by.get("control_before", {}), by.get("control_after", {})
    pinned = [r for r in rows if r["label"] not in ("control_before", "control_after")
              and r["fingerprint"]]
    prints = {r["fingerprint"] for r in pinned}
    control_stable = bool(cb.get("fingerprint")) and cb.get("fingerprint") == ca.get("fingerprint")

    out = {
        "profile": profile, "laden": laden,
        "timezone_note": tz_note or "times supplied by the caller",
        "times_tried": [r["label"] for r in rows],
        "rows": rows,
        "control_stable": control_stable,
        "pinned_all_agree": len(prints) <= 1,
        "distinct_geometries": len({r["fingerprint"] for r in rows if r["fingerprint"]}),
    }
    if not control_stable:
        out["reads_as"] = ("🔴 the UNPINNED answer changed during the experiment itself, so "
                           "nothing below is attributable to departureTime. Re-run.")
    elif len(prints) > 1:
        out["reads_as"] = ("HERE returns different roads for different departure times, so "
                           "an unpinned bake is a snapshot of whenever it ran. Pinning is "
                           "both necessary and sufficient to make a bake reproducible.")
    elif pinned and cb.get("fingerprint") and cb["fingerprint"] not in prints:
        out["reads_as"] = ("every pinned time agrees with the others but NOT with the "
                           "unpinned control, so pinning changes the road while making it "
                           "reproducible. Which road is wanted is a product decision.")
    elif pinned:
        out["reads_as"] = ("pinned and unpinned agree right now. That does NOT clear "
                           "departureTime -- it means conditions are flat at this moment. "
                           "Re-run at a peak hour before concluding anything.")
    else:
        out["reads_as"] = "no run produced geometry; read the per-row errors."
    return out

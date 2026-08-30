// ===========================================================================
// Public map — IPT / Work Section colouring of the surveyed rail alignment
// ===========================================================================
//
// Reference geometry only. Nothing here touches routes, forecasts, zones or
// haul roads, and nothing here is read by the backend. It exists so a planner
// looking at haul routes can see which works package the adjacent alignment
// belongs to.
//
// Bounds: Scope diagram v1.0 + IPT Matrix, GLOBAL chainage in metres.
//
// ---------------------------------------------------------------------------
// WHY THIS FILE SPLITS LINES INSTEAD OF STAMPING THEM
// ---------------------------------------------------------------------------
// The handoff spec said: take each alignment feature's midpoint, find the
// nearest chainage marker, stamp one IPT on the whole feature. That was
// checked against the data before it was built and it does not work.
//
// `map/data/alignment.js` carries NO chainage property (verified: 0 of 531
// features have `chain`, `chaintxt`, `chain_from` or `chain_to`), so deriving
// it from `chainage.js` is correct. But the features are long:
//
//   * 58 features are align_type 'Main Track'
//   * 39 of those 58 span MORE THAN 3 km of chainage
//   * the longest spans 45.2 km  (chainage 143700 -> 189000)
//   * another spans 26.5 km      (chainage 107000 -> 133600)
//
// The narrowest bands below are IPT 5 at 2.3 km and IPT 4/WS4 at 5.4 km. A
// single midpoint stamp on the 107000 -> 133600 feature lands at 114800, i.e.
// IPT 1, and paints 26 km of track blue — swallowing IPT 2, IPT 3 and most of
// IPT 4 entirely. Those are exactly the bands the overlay exists to show.
//
// So: every vertex gets a chainage, and the line is CUT where the band
// changes. One source feature becomes one output feature per band it crosses.
// Same output properties the spec asked for (`ipt`, `ws`, `ipt_label`,
// `ipt_colour`), same paint expression — computed per segment instead of per
// feature.
//
// ---------------------------------------------------------------------------
// NAMING COLLISION — read this before wiring anything together
// ---------------------------------------------------------------------------
// `properties.ipt` ALSO exists on ROUTE features from /public/map-data, where
// it drives the "All sector IPTs" filter (`#filter-ipt`). That is a different
// source ('routes-source' vs 'alignment-source') and a different meaning: on a
// route it is the IPT that owns the delivery; here it is the IPT whose works
// package that stretch of railway sits in. They are not interchangeable and
// the route filter must never be pointed at this layer.
// ===========================================================================

// ---------------------------------------------------------------------------
// VERSION HANDSHAKE — read this if something on the map "hasn't worked"
// ---------------------------------------------------------------------------
// Every delivery ships BOTH map/index.html and map/ipt_segments.js, and they are
// a matched pair: index.html draws layers from data this file builds. If only
// one of the two is updated, the map does not error — it half-works, silently.
//
// That happened on 2026-08-30 and produced three symptoms at once: IPT 6 still
// green, faint bridges present, and a "Work section boundaries" checkbox that
// toggled nothing. All three are one fact — index.html was at v5 and this file
// was still the polish build, so window.buildWsBoundaries did not exist and the
// tick source fell back to an empty collection.
//
// So the two halves now announce their version to each other. A mismatch prints
// a console error AND puts a red line in the sidebar, because a silent
// half-upgrade costs more to diagnose than it does to prevent.
window.IPT_SEGMENTS_VERSION = 'v7';

// --- The bands -------------------------------------------------------------
// Contiguous by construction: each chain_to is the next chain_from. Asserted
// in backend/tests/parse_map.js — a gap would leave unpainted track and an
// overlap would make the first-match scan silently win.
window.IPT_SEGMENTS = [
  { ipt: 'IPT 6', ws: ['WS7', 'WS8', 'WS9', 'WS10', 'WS11'], label: 'Superstructure / Ülemiste / Soodevahe', ws_primary: 'WS7', chain_from: -5000, chain_to: 105480, colour: '#86198F' },
  { ipt: 'IPT 1', ws: ['WS1', 'WS12'], label: 'Tootsi–Timmermanni / local stops', ws_primary: 'WS1', chain_from: 105480, chain_to: 117278, colour: '#7F1D1D' },
  { ipt: 'IPT 2', ws: ['WS2', 'WS13'], label: 'Timmermanni–Orasselja', ws_primary: 'WS2', chain_from: 117278, chain_to: 125000, colour: '#78716C' },
  { ipt: 'IPT 3', ws: ['WS3'], label: 'Rääma bog / Papiniidu approach', ws_primary: 'WS3', chain_from: 125000, chain_to: 130036, colour: '#854D0E' },
  { ipt: 'IPT 4', ws: ['WS4'], label: 'Pärnu Papiniidu bridge BR2032', ws_primary: 'WS4', chain_from: 130036, chain_to: 135400, colour: '#155E75' },
  { ipt: 'IPT 5', ws: ['WS5'], label: 'Pärnu passenger terminal area', ws_primary: 'WS5', chain_from: 135400, chain_to: 137685, colour: '#166534' },
  { ipt: 'IPT 4', ws: ['WS6'], label: 'Pärnu terminal – A1/A2 border', ws_primary: 'WS6', chain_from: 137685, chain_to: 142000, colour: '#155E75' },
  { ipt: 'Outside A1', ws: [], label: 'Outside Alliance 1 mainline scope', ws_primary: null, chain_from: 142000, chain_to: 250000, colour: '#94a3b8' },
];

// ---------------------------------------------------------------------------
// The colour rule, and what it is protecting
// ---------------------------------------------------------------------------
// ⚠️ MANDATORY (2026-08-28): no IPT colour may reuse a hex the map already
//    spends on a route, a forecast, a selection or temporary haul. If it did,
//    a planner could not tell "this stretch is IPT 6" from "this route is
//    laden" — and the route layers are the ones that carry money.
//
// Every hex below was checked against the real source, not taken on trust:
//
//   #039E86  inbound (laden)          7 uses in map/index.html
//   #f59e0b  outbound (empty)         4
//   #C2790B  temporary haul           2
//   #3398DB  forecast routes          1 in map/config.js
//   #BF2E55  selection / peak / zones 4
//   #003787  brand navy               6
//   #0A1446  brand dark / casing      2
//
// None of them appears in IPT_SEGMENTS. Asserted in parse_map.js so a future
// palette edit cannot quietly reintroduce one.
//
// ⭐ The first palette put IPT 2 on #6D28D9, which is also MAT_PALETTE[4] in
//    map/index.html (a material chip in the forecast-detail drawer table) and
//    NODE_COLORS['Site'] in the admin app. That was accepted at the time because
//    neither is a line on this map. The 2026-08-28 rebuild removed it anyway —
//    no band colour now matches anything the codebase uses for anything else.
window.IPT_RESERVED_COLOURS = [
  '#039E86', '#f59e0b', '#C2790B', '#3398DB', '#BF2E55', '#003787', '#0A1446',
];

// ---------------------------------------------------------------------------
// ⚠️ MEASURED — the palette was rebuilt on 2026-08-28 because the first one failed
// ---------------------------------------------------------------------------
// The mandated palette put IPT 6, IPT 1 and IPT 2 in the same indigo/violet
// family, and those are three CONSECUTIVE bands: the corridor changes package
// three times between chainage 105480 and 125000. Measured with CIE ΔE2000,
// where ~1 is a just-noticeable difference and two 2.5 px lines on a light
// basemap need roughly 20+ to read as different colours:
//
//                              first palette      this palette
//   weakest adjacent pair            6.3               22.1
//   weakest pair anywhere            5.7               21.0
//   underlay vs the colour on it     0.0               34.5
//   weakest adjacent, colour-blind   2.0               21.5
//
// ⭐ That last row is the one that decided it. Simulated for deuteranopia and
//    protanopia, three consecutive bands of the first palette were ΔE 2.0 apart
//    — the same colour, not a similar one.
//
// ⚠️ 21.0 is the CEILING, not a compromise nobody tried to beat. Seven hexes are
//    already spent on route, forecast, selection and brand layers, and the
//    remaining hue space will not hold six band colours plus an underlay any
//    further apart than this. Searched exhaustively over a bank of 28 corporate
//    tones under all four constraints at once (every pair apart, every adjacent
//    pair further apart, colour-blind safe, and clear of every line the map
//    already draws). If a band ever needs more room, the way to get it is to
//    free up a reserved colour, not to re-shuffle these.
//
// How the palette is built, so an edit keeps the property rather than losing it:
//   * six hue families, one per band: plum, oxblood, stone, bronze, teal blue,
//     pine. No two bands share a family, so the LEGEND reads as six things.
//   * the underlay is a light lavender wash (L* 77) rather than another deep
//     colour, so it separates from everything it sits under by lightness rather
//     than competing with it on hue.
//   * 'Outside A1' stays muted slate.
//
// ⚠️ Closest remaining approaches to a line the map already draws, all
//    acceptable but worth knowing: Outside A1 vs forecast ΔE 15.4 (both muted,
//    and Outside A1 predates this palette), IPT 1 vs selection 17.1, IPT 4 vs
//    brand navy 17.8, IPT 3 vs temporary haul 19.5 — temporary haul is dashed,
//    which separates it independently of colour.
window.IPT_UNDERLAY = {
  colour: '#C4B5FD',   // lavender 300 — a wash, not a seventh competing colour
  width: 4.5,
  opacity: 0.9,
};

// ---------------------------------------------------------------------------
// Work sections — official names, and where they are DISPUTED
// ---------------------------------------------------------------------------
// ⚠️ The names below came from the IPT Matrix. `claude/roadmap.md` marks that
//    document "second-hand AI output, superseded", and `claude/scope-diagram.md`
//    — derived from the real Appendix E scope diagram — corrects it. Where the
//    two disagree, the DIAGRAM wins here, and the disagreement is recorded on
//    the row rather than silently resolved.
//
// ⭐ Two corrections were applied on 2026-08-30, and the first was already
//    shipped wrong:
//
//    * WS13 (Urge halt) belongs to **IPT 2**, not IPT 1. `ipt-matrix.md` lists
//      "WS 13 (Urge) → IPT 1" in its own table of KNOWN ERRORS, and the diagram
//      shows the ITP 2 band directly beneath Urge. The first overlay delivery
//      inherited the error and IPT 1's legend row has been reading
//      "WS1, WS12, WS13" ever since. Now WS1, WS12 — and WS2, WS13.
//    * WS14 / WS15 ownership is **NOT settled**. The spec asserts IPT 6; the
//      scope diagram draws NO IPT band beneath them, and "Which IPT owns
//      WS 14 & 15?" is open question A3.2. Marked provisional, not asserted.
//
// ⚠️ Also unmodelled: the diagram subdivides WS7 into **WS 7.1–7.4** along the
//    alignment. This overlay carries a single WS7, so the popup says WS7 for the
//    whole northern band. That is a simplification, not a finding.
window.WS_NAMES = {
  WS1:  { name: 'Tootsi – Timmermanni',            ipt: 'IPT 1' },
  WS2:  { name: 'Timmermanni to Orasselja',        ipt: 'IPT 2' },
  WS3:  { name: 'Timmermanni to Papiniidu',        ipt: 'IPT 3',
          note: 'the scope diagram labels this stretch "Rääma bog"' },
  WS4:  { name: 'Pärnu Papiniidu bridge BR2032',   ipt: 'IPT 4' },
  WS5:  { name: 'Pärnu passenger terminal area',   ipt: 'IPT 5' },
  WS6:  { name: 'Pärnu terminal to A1/A2 border',  ipt: 'IPT 4' },
  WS7:  { name: 'Superstructure',                  ipt: 'IPT 6',
          note: 'the scope diagram subdivides this into WS 7.1–7.4' },
  WS8:  { name: 'Ülemiste Terminal',               ipt: 'IPT 6' },
  WS9:  { name: 'Rolling Stock Depot',             ipt: 'IPT 6' },
  WS10: { name: 'Soodevahe construction base',     ipt: 'IPT 6' },
  WS11: { name: 'Soodevahe IMF',                   ipt: 'IPT 6' },
  WS12: { name: 'Tootsi local stop',               ipt: 'IPT 1' },
  WS13: { name: 'Urge halt',                       ipt: 'IPT 2',
          note: 'the IPT Matrix says IPT 1; that is one of its own listed errors' },
  WS14: { name: 'Pärnu Construction Base',         ipt: null, provisional: true,
          note: 'no IPT band is drawn beneath it — open question A3.2' },
  WS15: { name: 'Pärnu IMF',                       ipt: null, provisional: true,
          note: 'no IPT band is drawn beneath it — open question A3.2' },
};

window.wsLabel = function (code) {
  var w = (window.WS_NAMES || {})[code];
  return w ? w.name : '';
};

// Chainage as a railway engineer writes it: 105480 -> "105+480".
window.chainText = function (m) {
  if (m == null || isNaN(m)) return '–';
  var v = Math.round(m);
  var sign = v < 0 ? '-' : '';
  v = Math.abs(v);
  var km = Math.floor(v / 1000);
  var rem = v % 1000;
  return sign + km + '+' + String(rem).padStart(3, '0');
};

window.IPT_DEFAULT = { ipt: 'Unknown', ws: [], label: 'Unassigned', colour: '#64748B' };

// ⚠️ OPEN, owner = human. The WS2 <-> WS3 boundary at 125000 is approximate
// and wants checking against the design drawings after the first visual pass.
// The WS5/WS6 border quoted in Work_Sections as "31+507" is LOCAL chainage and
// is deliberately not used here.
window.IPT_BOUNDS_PROVISIONAL = ['WS2/WS3 @ 125000'];

window.iptForChainage = function (chain_m) {
  if (chain_m == null || isNaN(chain_m)) return window.IPT_DEFAULT;
  var segs = window.IPT_SEGMENTS || [];
  for (var i = 0; i < segs.length; i++) {
    var s = segs[i];
    if (chain_m >= s.chain_from && chain_m < s.chain_to) return s;
  }
  return window.IPT_DEFAULT;
};

// ---------------------------------------------------------------------------
// Chainage lookup
// ---------------------------------------------------------------------------
// 212,628 alignment vertices against 2,180 chainage points is 463 million
// distance tests brute force, which is seconds of blocked main thread on page
// load. A flat grid index makes it a handful of tests per vertex.
//
// Cell 0.02 deg lon x 0.01 deg lat is about 1.15 km x 1.11 km at 58.8N, and
// the chainage markers average ~94 m apart along the line, so ring 0 or ring 1
// almost always contains a hit.

var CELL_LON = 0.02;
var CELL_LAT = 0.01;
var LON_SCALE = 0.515;   // cos(58.8 deg): scales lon degrees to lat degrees
var MAX_RING = 60;       // ~66 km. Beyond that the vertex is not near the line.

function buildChainageIndex(chainageFC) {
  var grid = Object.create(null);
  var feats = (chainageFC && chainageFC.features) || [];
  var n = 0;
  for (var i = 0; i < feats.length; i++) {
    var p = feats[i];
    var props = p.properties || {};
    var raw = props.chain;
    if (raw == null) continue;
    // "42,900.00" -> 42900
    var m = parseFloat(String(raw).replace(/,/g, ''));
    if (isNaN(m)) continue;
    var c = p.geometry && p.geometry.coordinates;
    if (!c) continue;
    var lon = c[0], lat = c[1];
    var key = Math.floor(lon / CELL_LON) + '|' + Math.floor(lat / CELL_LAT);
    (grid[key] || (grid[key] = [])).push([lon, lat, m]);
    n++;
  }
  return { grid: grid, count: n };
}

// Nearest chainage in metres, or null if nothing is within MAX_RING cells.
function nearestChainage(index, lon, lat) {
  var gx = Math.floor(lon / CELL_LON);
  var gy = Math.floor(lat / CELL_LAT);
  var grid = index.grid;
  var best = null, bestD2 = Infinity;

  for (var r = 0; r <= MAX_RING; r++) {
    // Once a hit is closer than the guaranteed-empty inner radius of the next
    // ring, no further ring can beat it.
    if (best !== null) {
      var innerLat = (r - 1) * CELL_LAT;
      if (innerLat > 0 && bestD2 < innerLat * innerLat) break;
    }
    for (var dx = -r; dx <= r; dx++) {
      for (var dy = -r; dy <= r; dy++) {
        // ring only, not the filled square — inner cells were done already
        if (r > 0 && Math.abs(dx) !== r && Math.abs(dy) !== r) continue;
        var bucket = grid[(gx + dx) + '|' + (gy + dy)];
        if (!bucket) continue;
        for (var i = 0; i < bucket.length; i++) {
          var b = bucket[i];
          var ddx = (b[0] - lon) * LON_SCALE;
          var ddy = b[1] - lat;
          var d2 = ddx * ddx + ddy * ddy;
          if (d2 < bestD2) { bestD2 = d2; best = b[2]; }
        }
      }
    }
  }
  return best;
}

// ---------------------------------------------------------------------------
// Band splitting
// ---------------------------------------------------------------------------

var MIN_RUN_M = 250;   // collapse band runs shorter than this — see below
var EARTH_R = 6371000;

function metres(a, b) {
  var dLat = (b[1] - a[1]) * Math.PI / 180;
  var dLon = (b[0] - a[0]) * Math.PI / 180;
  var lat1 = a[1] * Math.PI / 180, lat2 = b[1] * Math.PI / 180;
  var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
          Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
  return 2 * EARTH_R * Math.asin(Math.sqrt(Math.min(1, h)));
}
window.iptMetres = metres;   // exposed for the test harness

function bandIndexOf(seg) {
  var segs = window.IPT_SEGMENTS;
  for (var i = 0; i < segs.length; i++) if (segs[i] === seg) return i;
  return -1;   // IPT_DEFAULT
}

// Nearest-marker assignment is not monotonic near a boundary: two adjacent
// vertices can snap to markers either side of it and the band flickers. That
// would emit dozens of one-vertex features. So any run shorter than MIN_RUN_M
// is absorbed into the longer of its neighbours before cutting.
// `steps[i]` is the length of the leg from vertex i to i+1, precomputed once —
// re-haversining inside the smoothing loop was the whole cost of this file.
function smoothRuns(bands, steps) {
  if (bands.length < 3) return bands;
  var guard = 0;
  while (guard++ < 50) {
    var runs = [];
    var start = 0;
    for (var i = 1; i <= bands.length; i++) {
      if (i === bands.length || bands[i] !== bands[start]) {
        var len = 0;
        for (var j = start; j < i - 1; j++) len += steps[j];
        runs.push({ from: start, to: i - 1, band: bands[start], len: len });
        start = i;
      }
    }
    if (runs.length < 2) break;

    // Absorb every too-short run in one pass, shortest first, so a long line
    // with many boundary flickers does not need one full rebuild per flicker.
    var order = [];
    for (var k = 0; k < runs.length; k++) if (runs[k].len < MIN_RUN_M) order.push(k);
    if (!order.length) break;
    order.sort(function (a, b) { return runs[a].len - runs[b].len; });

    var changed = false;
    for (var o = 0; o < order.length; o++) {
      var k2 = order[o];
      var prev = k2 > 0 ? runs[k2 - 1] : null;
      var next = k2 < runs.length - 1 ? runs[k2 + 1] : null;
      var into = null;
      if (prev && next) into = (prev.len >= next.len) ? prev.band : next.band;
      else if (prev) into = prev.band;
      else if (next) into = next.band;
      if (into === null || into === runs[k2].band) continue;
      for (var v = runs[k2].from; v <= runs[k2].to; v++) bands[v] = into;
      changed = true;
    }
    if (!changed) break;
  }
  return bands;
}

// ---------------------------------------------------------------------------
// Gap splitting — a PRE-EXISTING map defect this overlay exposed
// ---------------------------------------------------------------------------
// `alignment.js` stores physically discontinuous track as a single LineString
// with a straight edge bridging the hole. Feature OBJECTID 11014 is the clear
// case: 3,862 vertices at ~7 m spacing, and one edge of 7,021 m between
// chainage 115900 and 122900 with nothing in between.
//
// Measured across the whole file:
//
//   drawn length today          646.7 km
//   edges longer than the cut     271, totalling 239.2 km  (37% of what is drawn)
//   of which > 1 km                77
//   real length after cutting   407.5 km   (~205 km corridor, double track — checks out)
//
// The public map draws all 646.7 km today, so those straights are already on
// screen in black. Colouring by IPT makes them worse than cosmetic: the 45 km
// straight from chainage 143700 would be painted as a confident grey
// 'Outside A1' band cutting across country that has no railway on it.
//
// The cut is a RATIO test, not an absolute one. Some genuine short tracks are
// stored as a single two-vertex straight (median edge == the only edge, ratio
// 1x) and an absolute threshold alone would delete them. A gap is an edge that
// is both over GAP_MIN_M and far longer than the rest of its own part.
//
// ⚠️ This changes what the existing 'Rail alignment' layer draws, beyond the
//    handoff spec. Set GAP_SPLIT = false to restore the old behaviour and see
//    the straights again. Counts land in window.IPT_BUILD_STATS either way.

var GAP_SPLIT = true;
var GAP_MIN_M = 150;
var GAP_RATIO = 20;
var gapStats = { edges_cut: 0, km_removed: 0 };

function medianOf(arr) {
  if (!arr.length) return 0;
  var a = arr.slice().sort(function (x, y) { return x - y; });
  var h = a.length >> 1;
  return a.length % 2 ? a[h] : (a[h - 1] + a[h]) / 2;
}

// Every cut edge is also KEPT, as a two-point "bridge". The geometry cut stays
// hard — a bridge is a separate feature, flagged is_bridge, painted at 30%
// opacity on its own layer. So the corridor reads as continuous while the
// missing stretches stay visibly weaker, and nothing measured off the solid
// features includes a straight line across country. See BRIDGE_STEP_M below for
// why they are then densified rather than drawn as single straights.
function splitOnce(coords, out, bridges) {
  if (coords.length < 3) { out.push(coords); return false; }
  var steps = [];
  for (var i = 0; i < coords.length - 1; i++) steps.push(metres(coords[i], coords[i + 1]));
  var med = medianOf(steps);
  var cut = false;
  var start = 0;
  for (var j = 0; j < steps.length; j++) {
    if (steps[j] > GAP_MIN_M && steps[j] > GAP_RATIO * med) {
      if (j + 1 - start >= 2) out.push(coords.slice(start, j + 1));
      if (bridges) bridges.push([coords[j], coords[j + 1]]);
      start = j + 1;
      cut = true;
      gapStats.edges_cut++;
      gapStats.km_removed += steps[j] / 1000;
    }
  }
  if (!cut) { out.push(coords); return false; }
  if (coords.length - start >= 2) out.push(coords.slice(start));
  return true;
}

// Cutting a part changes the median of what is left, so an edge that looked
// ordinary against the whole line can look like a gap against the surviving
// run. Iterate until nothing more splits — otherwise the invariant asserted in
// backend/tests/test_ipt_overlay.js ("no gap-shaped edge survives") is only
// true of the first pass.
function splitAtGaps(coords, bridges) {
  if (!GAP_SPLIT || coords.length < 3) return [coords];
  var work = [coords];
  var depth = 0;
  while (depth++ < 20) {
    var next = [];
    var any = false;
    for (var i = 0; i < work.length; i++) if (splitOnce(work[i], next, bridges)) any = true;
    work = next;
    if (!any) break;
  }
  return work.length ? work : [coords];
}

// A bridge can be 45 km long, which is longer than five of the seven IPT bands.
// Drawn as one straight it would have to be given ONE colour — the exact
// midpoint-stamping mistake this file exists to avoid, just at 30% opacity. So
// each bridge is densified to a vertex every BRIDGE_STEP_M and then fed through
// the same band splitter as everything else: it changes colour where it crosses
// a boundary, like the real track beside it.
var BRIDGE_STEP_M = 200;

function densify(a, b) {
  var d = metres(a, b);
  var n = Math.max(1, Math.min(400, Math.ceil(d / BRIDGE_STEP_M)));
  var pts = [];
  for (var i = 0; i <= n; i++) {
    var f = i / n;
    pts.push([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
  }
  return pts;
}

function segmentPart(coords, index, props, out, isBridge) {
  if (!coords || coords.length < 2) return;

  var bands = new Array(coords.length);
  var chains = new Array(coords.length);
  for (var i = 0; i < coords.length; i++) {
    var m = nearestChainage(index, coords[i][0], coords[i][1]);
    chains[i] = m;
    bands[i] = bandIndexOf(window.iptForChainage(m));
  }
  var steps = new Array(coords.length - 1);
  for (var s = 0; s < coords.length - 1; s++) steps[s] = metres(coords[s], coords[s + 1]);
  smoothRuns(bands, steps);

  var segStart = 0;
  for (var j = 1; j <= coords.length; j++) {
    if (j < coords.length && bands[j] === bands[segStart]) continue;

    // Share the boundary vertex with the next segment so the painted line has
    // no visible gap at a band change.
    var end = (j < coords.length) ? j : coords.length - 1;
    var slice = coords.slice(segStart, end + 1);
    if (slice.length >= 2) {
      var band = bands[segStart];
      var seg = band >= 0 ? window.IPT_SEGMENTS[band] : window.IPT_DEFAULT;
      var cs = [], ce = [];
      for (var q = segStart; q <= end; q++) if (chains[q] != null) { cs.push(chains[q]); ce.push(chains[q]); }
      var p = {};
      for (var key in props) p[key] = props[key];
      p.ipt = seg.ipt;
      p.ws = (seg.ws || []).join(',');
      // ⭐ The band table's boundaries ARE the seven work-section boundaries, so
      // every segment already falls inside exactly one WS band — no finer split
      // is needed to stamp it. ws_primary is the MAINLINE section that owns this
      // chainage; the other codes in `ws` are point assets (stations, halts,
      // depots) that sit on the same ground without owning the band.
      p.ws_primary = seg.ws_primary || '';
      p.ws_name = seg.ws_primary ? window.wsLabel(seg.ws_primary) : '';
      p.ipt_label = seg.label;
      p.ipt_colour = seg.colour;
      p.chain_from_m = cs.length ? Math.round(Math.min.apply(null, cs)) : null;
      p.chain_to_m = ce.length ? Math.round(Math.max.apply(null, ce)) : null;
      p.is_main_track = (props.align_type === 'Main Track');
      p.is_bridge = !!isBridge;
      out.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: slice }, properties: p });
    }
    segStart = j;
  }
}

// Returns a NEW FeatureCollection. The input is not mutated, and the result is
// computed once and held, so a basemap style switch re-adds the same object
// with the properties already on it — no re-stamping on style.load.
window.buildIptAlignment = function (alignmentFC, chainageFC) {
  var t0 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
  if (!alignmentFC || !alignmentFC.features) return alignmentFC;
  var index = buildChainageIndex(chainageFC);
  if (!index.count) {
    console.warn('[IPT] no usable chainage markers — alignment left uncoloured');
    return alignmentFC;
  }

  gapStats = { edges_cut: 0, km_removed: 0 };
  var out = [];
  var src = alignmentFC.features;
  for (var i = 0; i < src.length; i++) {
    var f = src[i];
    var g = f.geometry;
    if (!g) continue;
    var raw = (g.type === 'LineString') ? [g.coordinates]
            : (g.type === 'MultiLineString') ? g.coordinates : [];
    for (var k = 0; k < raw.length; k++) {
      var bridges = [];
      var runs = splitAtGaps(raw[k], bridges);
      for (var r = 0; r < runs.length; r++) segmentPart(runs[r], index, f.properties || {}, out);
      // Bridges are emitted AFTER their own feature's runs but into the same
      // array, and the layer that draws them is added after the solid one, so a
      // bridge never paints over real track.
      for (var b = 0; b < bridges.length; b++) {
        segmentPart(densify(bridges[b][0], bridges[b][1]), index, f.properties || {}, out, true);
      }
    }
  }

  var t1 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
  var nBridge = 0;
  for (var q = 0; q < out.length; q++) if (out[q].properties.is_bridge) nBridge++;

  window.IPT_BUILD_STATS = {
    source_features: src.length,
    output_features: out.length,
    solid_features: out.length - nBridge,
    bridge_features: nBridge,
    chainage_markers: index.count,
    gap_split: GAP_SPLIT,
    gap_edges_cut: gapStats.edges_cut,
    gap_km_removed: Math.round(gapStats.km_removed * 10) / 10,
    ms: Math.round(t1 - t0),
  };
  if (typeof console !== 'undefined' && console.log) {
    console.log('[IPT] alignment overlay', window.IPT_BUILD_STATS);
  }
  return { type: 'FeatureCollection', features: out };
};

window.IPT_GAP_CONFIG = { split: GAP_SPLIT, min_m: GAP_MIN_M, ratio: GAP_RATIO };

// ---------------------------------------------------------------------------
// Chainage marker density
// ---------------------------------------------------------------------------
// 2,180 markers on a 205 km corridor is one every ~94 m. At corridor zoom that
// is a grey smear, not information. Each point gets a `step_m` — the coarsest
// round interval its chainage falls on — and the map shows only the coarse ones
// until you zoom in.
//
//   step_m   points   what it is
//   10000        24   the 10 km ticks. All that is drawn at corridor zoom.
//    5000        21   the intermediate 5 km ticks
//    1000       173   kilometre ticks
//     100      1962   everything else
//
// ⚠️ Mapbox GL cannot use ['zoom'] inside a layer `filter`. So the tiering is
//    done in the PAINT and LAYOUT expressions instead — an outermost
//    ['step', ['zoom'], ...] whose branches test step_m — which is allowed, and
//    keeps both layer ids unchanged so the existing checkbox still works.
window.CHAINAGE_STEPS = [10000, 5000, 1000, 500, 100];

// ⚠️ The spec gave the on-tick test as
//        Math.abs(chain_m % step) < 0.5 || Math.abs(chain_m % step - step) < 0.5
//    which is WRONG for negative chainage, and this corridor has chainage down
//    to -3982.3. In JS, (-3982.3 % 10000) is -3982.3, so the first clause fails
//    and the second compares against -13982.3 — a negative tick can never
//    match. Normalising the remainder into [0, step) first fixes it and keeps
//    the intended tolerance, which exists because these values are floats
//    ("42,900.00") and not all of them land on an exact integer.
function onTick(m, step) {
  var r = ((m % step) + step) % step;
  return r < 0.5 || r > step - 0.5;
}
window.iptOnTick = onTick;

window.buildChainageSteps = function (chainageFC) {
  if (!chainageFC || !chainageFC.features) return chainageFC;
  var out = [];
  var counts = {};
  var steps = window.CHAINAGE_STEPS;
  for (var i = 0; i < chainageFC.features.length; i++) {
    var f = chainageFC.features[i];
    var raw = (f.properties || {}).chain;
    var m = raw == null ? NaN : parseFloat(String(raw).replace(/,/g, ''));
    var step = steps[steps.length - 1];
    if (!isNaN(m)) {
      for (var s = 0; s < steps.length; s++) {
        if (onTick(m, steps[s])) { step = steps[s]; break; }
      }
    }
    counts[step] = (counts[step] || 0) + 1;
    var p = {};
    for (var k in f.properties) p[k] = f.properties[k];
    p.step_m = step;
    p.chain_m = isNaN(m) ? null : Math.round(m);
    out.push({ type: 'Feature', geometry: f.geometry, properties: p });
  }
  window.CHAINAGE_STEP_COUNTS = counts;
  return { type: 'FeatureCollection', features: out };
};

// ---------------------------------------------------------------------------
// Work-section boundary ticks
// ---------------------------------------------------------------------------
// Seven package edges, drawn as a short mark ACROSS the alignment. Not a second
// coloured corridor and not a second chainage ladder: the chainage layer is a
// 100 m grid of 2,180 points, this is seven lines that mean something.
//
// ⚠️ These are the SAME seven chainages the band table already cuts on. They are
//    declared once, here, and asserted against IPT_SEGMENTS in
//    backend/tests/test_ipt_overlay.js — a tick that drifts from the colour
//    change it marks would be worse than no tick.
//
// ⚠️ 142000 is the A1/A2 border. The scope diagram says ~141+930, so the tick is
//    about 70 m east of the drawn boundary. The band table has always used
//    142000 and moving one without the other would put the tick off the colour
//    change, so both stay at 142000 until the real figure is confirmed.
//
// ⚠️ NOT drawn: WS12, WS13, WS14, WS15. Those are point assets — a station, a
//    halt and two Pärnu facilities — not mainline bands, and giving them ticks
//    would assert edges the scope diagram does not draw. Nor is the Work_Sections
//    figure "31+507": that is LOCAL chainage and this map is global throughout.
window.WS_BOUNDARIES = [
  { chain_m: 105480, from_ws: 'WS7', to_ws: 'WS1', from_ipt: 'IPT 6', to_ipt: 'IPT 1' },
  { chain_m: 117278, from_ws: 'WS1', to_ws: 'WS2', from_ipt: 'IPT 1', to_ipt: 'IPT 2' },
  { chain_m: 125000, from_ws: 'WS2', to_ws: 'WS3', from_ipt: 'IPT 2', to_ipt: 'IPT 3',
    provisional: true },
  { chain_m: 130036, from_ws: 'WS3', to_ws: 'WS4', from_ipt: 'IPT 3', to_ipt: 'IPT 4' },
  { chain_m: 135400, from_ws: 'WS4', to_ws: 'WS5', from_ipt: 'IPT 4', to_ipt: 'IPT 5' },
  { chain_m: 137685, from_ws: 'WS5', to_ws: 'WS6', from_ipt: 'IPT 5', to_ipt: 'IPT 4' },
  { chain_m: 142000, from_ws: 'WS6', to_ws: null,  from_ipt: 'IPT 4', to_ipt: 'Outside A1',
    note: 'A1/A2 border — the scope diagram says ~141+930' },
];

window.WS_TICK_COLOUR = '#334155';   // neutral slate: not a route, not a package

// A boundary further than this from any drawn Main Track is sitting over one of
// the alignment file's holes. 100 m is comfortably beyond survey noise (the
// nearest-marker residual is under 50 m everywhere) and far below the smallest
// real hole, which is 400 m.
var NO_TRACK_M = 100;

function bearingBetween(a, b) {
  var y = Math.sin((b[0] - a[0]) * Math.PI / 180) * Math.cos(b[1] * Math.PI / 180);
  var x = Math.cos(a[1] * Math.PI / 180) * Math.sin(b[1] * Math.PI / 180) -
          Math.sin(a[1] * Math.PI / 180) * Math.cos(b[1] * Math.PI / 180) *
          Math.cos((b[0] - a[0]) * Math.PI / 180);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

// Points, not lines. The tick is drawn as a rotated glyph in a symbol layer so
// it stays a constant SIZE ON SCREEN: a fixed ground length would be one pixel
// at corridor zoom and half the viewport at zoom 16.
window.buildWsBoundaries = function (chainageFC, alignmentFC) {
  if (!chainageFC || !chainageFC.features) return { type: 'FeatureCollection', features: [] };
  var pts = [];
  for (var i = 0; i < chainageFC.features.length; i++) {
    var f = chainageFC.features[i];
    var raw = (f.properties || {}).chain;
    var m = raw == null ? NaN : parseFloat(String(raw).replace(/,/g, ''));
    var c = f.geometry && f.geometry.coordinates;
    if (isNaN(m) || !c) continue;
    pts.push({ m: m, lon: c[0], lat: c[1] });
  }
  pts.sort(function (a, b) { return a.m - b.m; });

  // de-duplicate: chainage.js has 121 repeated chainage values, and two markers
  // sharing a value give a zero-length span and therefore no bearing
  var uniq = [];
  for (var u = 0; u < pts.length; u++) {
    if (!uniq.length || pts[u].m > uniq[uniq.length - 1].m) uniq.push(pts[u]);
  }

  var out = [];
  for (var k = 0; k < window.WS_BOUNDARIES.length; k++) {
    var b = window.WS_BOUNDARIES[k];
    if (uniq.length < 2) break;

    // index of the first marker at or beyond the boundary
    var hi = 0;
    while (hi < uniq.length && uniq[hi].m < b.chain_m) hi++;
    if (hi >= uniq.length) hi = uniq.length - 1;
    var lo = Math.max(0, hi - 1);
    if (lo === hi) hi = Math.min(uniq.length - 1, lo + 1);

    // position: interpolate between the bracketing markers
    var span = uniq[hi].m - uniq[lo].m;
    var t = span > 0 ? Math.max(0, Math.min(1, (b.chain_m - uniq[lo].m) / span)) : 0;
    var lon = uniq[lo].lon + (uniq[hi].lon - uniq[lo].lon) * t;
    var lat = uniq[lo].lat + (uniq[hi].lat - uniq[lo].lat) * t;

    // ⚠️ bearing comes from the markers EITHER SIDE of the boundary, widened by
    // one where the boundary lands exactly on a marker. Taking it from the
    // bracketing pair alone gave a zero-length span on an exact hit, and the
    // first version then silently reused the PREVIOUS tick's bearing — three of
    // the seven ticks were rotated to a different stretch of railway.
    var bl = Math.max(0, lo - (t <= 0.001 ? 1 : 0));
    var bh = Math.min(uniq.length - 1, hi + (t >= 0.999 ? 1 : 0));
    if (bh <= bl) { bl = Math.max(0, bh - 1); }
    var brg = bearingBetween([uniq[bl].lon, uniq[bl].lat], [uniq[bh].lon, uniq[bh].lat]);

    out.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [lon, lat] },
      properties: {
        chain_m: b.chain_m,
        chain_txt: window.chainText(b.chain_m),
        bearing: Math.round(brg * 10) / 10,
        tick_rotate: Math.round(((brg + 90) % 360) * 10) / 10,
        from_ws: b.from_ws || '',
        to_ws: b.to_ws || '',
        label: (b.from_ws || '—') + ' | ' + (b.to_ws || 'Outside A1'),
        from_ipt: b.from_ipt || '',
        to_ipt: b.to_ipt || '',
        provisional: !!b.provisional,
        note: b.note || '',
        bearing_span_m: Math.round(uniq[bh].m - uniq[bl].m),
      },
    });
  }
  // ⭐ Which of these boundaries has no surveyed track under it?
  //
  // The ticks are positioned from chainage.js, whose 2,180 markers are all
  // align_type 'Main Track' and cover the corridor continuously. alignment.js
  // does NOT — it has ~60 km of holes. Three of the seven package edges fall in
  // one, so there is genuinely no drawn line for the tick to sit on.
  //
  // Measured here rather than hard-coded, so a refreshed alignment file changes
  // the answer instead of leaving a stale warning on the map.
  var flagged = 0;
  if (alignmentFC && alignmentFC.features) {
    var solid = [];
    for (var q = 0; q < alignmentFC.features.length; q++) {
      var af = alignmentFC.features[q];
      if (af.properties && af.properties.align_type === 'Main Track' &&
          af.properties.is_bridge !== true) solid.push(af.geometry.coordinates);
    }
    for (var o = 0; o < out.length; o++) {
      var pt = out[o].geometry.coordinates;
      var best = Infinity;
      for (var r2 = 0; r2 < solid.length; r2++) {
        var cs = solid[r2];
        for (var c2 = 0; c2 < cs.length; c2++) {
          var dd = metres(pt, cs[c2]);
          if (dd < best) best = dd;
        }
      }
      out[o].properties.track_gap_m = Math.round(best);
      out[o].properties.no_surveyed_track = best > NO_TRACK_M;
      if (best > NO_TRACK_M) flagged++;
    }
  }

  window.WS_BOUNDARY_STATS = {
    built: out.length,
    expected: window.WS_BOUNDARIES.length,
    no_surveyed_track: flagged,
  };
  return { type: 'FeatureCollection', features: out };
};

// Legend rows, one per unique IPT, generated from IPT_SEGMENTS so the table
// stays the single source of truth. 'Outside A1' is shown muted rather than
// hidden — an unexplained grey line reads as a bug.
window.iptLegendRows = function () {
  var seen = Object.create(null);
  var rows = [];
  var segs = window.IPT_SEGMENTS || [];
  for (var i = 0; i < segs.length; i++) {
    var s = segs[i];
    if (seen[s.ipt]) {
      var r = seen[s.ipt];
      for (var j = 0; j < s.ws.length; j++) if (r.ws.indexOf(s.ws[j]) === -1) r.ws.push(s.ws[j]);
      if (r.labels.indexOf(s.label) === -1) r.labels.push(s.label);
      continue;
    }
    seen[s.ipt] = { ipt: s.ipt, colour: s.colour, ws: s.ws.slice(), labels: [s.label], muted: s.ipt === 'Outside A1' };
    rows.push(seen[s.ipt]);
  }
  return rows;
};

if (typeof module !== 'undefined' && module.exports) module.exports = window;

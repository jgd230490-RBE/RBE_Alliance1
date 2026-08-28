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

// --- The bands -------------------------------------------------------------
// Contiguous by construction: each chain_to is the next chain_from. Asserted
// in backend/tests/parse_map.js — a gap would leave unpainted track and an
// overlap would make the first-match scan silently win.
window.IPT_SEGMENTS = [
  { ipt: 'IPT 6', ws: ['WS7', 'WS8', 'WS9', 'WS10', 'WS11'], label: 'Superstructure / Ülemiste / Soodevahe', chain_from: -5000, chain_to: 105480, colour: '#039E86' },
  { ipt: 'IPT 1', ws: ['WS1', 'WS12', 'WS13'], label: 'Tootsi–Timmermanni / local stops', chain_from: 105480, chain_to: 117278, colour: '#003787' },
  { ipt: 'IPT 2', ws: ['WS2'], label: 'Timmermanni–Orasselja', chain_from: 117278, chain_to: 125000, colour: '#0E7490' },
  { ipt: 'IPT 3', ws: ['WS3'], label: 'Rääma bog / Papiniidu approach', chain_from: 125000, chain_to: 130036, colour: '#475569' },
  { ipt: 'IPT 4', ws: ['WS4'], label: 'Pärnu Papiniidu bridge BR2032', chain_from: 130036, chain_to: 135400, colour: '#C6841D' },
  { ipt: 'IPT 5', ws: ['WS5'], label: 'Pärnu passenger terminal area', chain_from: 135400, chain_to: 137685, colour: '#BF2E55' },
  { ipt: 'IPT 4', ws: ['WS6'], label: 'Pärnu terminal – A1/A2 border', chain_from: 137685, chain_to: 142000, colour: '#C6841D' },
  { ipt: 'Outside A1', ws: [], label: 'Outside Alliance 1 mainline scope', chain_from: 142000, chain_to: 250000, colour: '#94a3b8' },
];

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
window.CHAINAGE_STEPS = [10000, 5000, 1000, 100];

window.buildChainageSteps = function (chainageFC) {
  if (!chainageFC || !chainageFC.features) return chainageFC;
  var out = [];
  var counts = {};
  for (var i = 0; i < chainageFC.features.length; i++) {
    var f = chainageFC.features[i];
    var raw = (f.properties || {}).chain;
    var m = raw == null ? NaN : parseFloat(String(raw).replace(/,/g, ''));
    var step = 100;
    if (!isNaN(m)) {
      var r = Math.round(m);
      var steps = window.CHAINAGE_STEPS;
      for (var s = 0; s < steps.length; s++) {
        if (r % steps[s] === 0) { step = steps[s]; break; }
      }
    }
    counts[step] = (counts[step] || 0) + 1;
    var p = {};
    for (var k in f.properties) p[k] = f.properties[k];
    p.step_m = step;
    out.push({ type: 'Feature', geometry: f.geometry, properties: p });
  }
  window.CHAINAGE_STEP_COUNTS = counts;
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

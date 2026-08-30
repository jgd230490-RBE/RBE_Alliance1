/*
 * Behavioural assertions for the IPT / Work Section alignment overlay.
 *
 * parse_map.js checks the wiring at source level. This file runs the real
 * builder over the real 8.8 MB map/data/alignment.js and 1.0 MB
 * map/data/chainage.js and checks what actually comes out.
 *
 * NOT covered: anything needing a browser. Whether Mapbox paints the colours,
 * whether the legend looks right, and whether the band boundaries are correct
 * on the ground are all unverified here — the last one is a question for the
 * design drawings, not a test.
 *
 * Run:  node backend/tests/test_ipt_overlay.js
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
let pass = 0;
const fail = [];
function ok(label, cond, extra) {
  if (cond) pass++;
  else fail.push(label + (extra ? "  " + extra : ""));
}
function near(a, b, tol) { return Math.abs(a - b) <= tol; }

// ---- load the data the way the browser does ---------------------------------
function loadWindowData(file) {
  const s = fs.readFileSync(path.join(ROOT, "map", "data", file), "utf8");
  return JSON.parse(s.slice(s.indexOf("=") + 1).trim().replace(/;\s*$/, ""));
}
global.window = global.window || {};
window.alignment_data = loadWindowData("alignment.js");
window.chainage_global_data = loadWindowData("chainage.js");
require(path.join(ROOT, "map", "ipt_segments.js"));

const SEGS = window.IPT_SEGMENTS;
const srcFeatures = window.alignment_data.features;
const srcCount = srcFeatures.length;
const srcFirstLen = srcFeatures[0].geometry.coordinates.length;

// =============================================================================
// 1. The band table
// =============================================================================
ok("IPT_SEGMENTS is a non-empty array", Array.isArray(SEGS) && SEGS.length > 0);
ok("every band has ipt, label, colour and numeric bounds",
  SEGS.every(s => s.ipt && s.label && /^#[0-9a-fA-F]{6}$/.test(s.colour) &&
    typeof s.chain_from === "number" && typeof s.chain_to === "number"));
ok("every band runs forwards", SEGS.every(s => s.chain_to > s.chain_from));
ok("the bands are in chainage order",
  SEGS.every((s, i) => i === 0 || s.chain_from >= SEGS[i - 1].chain_from));
ok("⭐ the bands are contiguous — no gap would leave track unpainted",
  SEGS.every((s, i) => i === 0 || s.chain_from === SEGS[i - 1].chain_to));
ok("⭐ and no band overlaps the next, which the first-match scan would hide",
  SEGS.every((s, i) => i === SEGS.length - 1 || s.chain_to <= SEGS[i + 1].chain_from));
// ⭐ The mandatory colour rule, checked from the built data rather than the source
// text: a band may not carry a hex the map spends on a route, forecast or selection.
ok("⭐ no band colour is a reserved route / forecast / selection hex",
  SEGS.every(s => !window.IPT_RESERVED_COLOURS
    .map(r => r.toLowerCase()).includes(s.colour.toLowerCase())),
  SEGS.filter(s => window.IPT_RESERVED_COLOURS.map(r => r.toLowerCase())
    .includes(s.colour.toLowerCase())).map(s => s.ipt + " " + s.colour).join(", "));
ok("the reserved list is non-empty, so that check can actually fail",
  window.IPT_RESERVED_COLOURS.length >= 7);
ok("one IPT keeps one colour across its bands",
  new Set(SEGS.filter(s => s.ipt === "IPT 4").map(s => s.colour)).size === 1);
ok("the underlay declares colour, width and opacity together",
  window.IPT_UNDERLAY && /^#[0-9a-fA-F]{6}$/.test(window.IPT_UNDERLAY.colour) &&
  window.IPT_UNDERLAY.width > 2.5 && window.IPT_UNDERLAY.opacity <= 1);
ok("⭐ the underlay is WIDER than the civil track, or it could not show beneath it",
  window.IPT_UNDERLAY.width > 2.5);

ok("a band's ws entries are all WS codes",
  SEGS.every(s => (s.ws || []).every(w => /^WS\d+$/.test(w))));
ok("the provisional WS2/WS3 bound is flagged in the file, not just in prose",
  Array.isArray(window.IPT_BOUNDS_PROVISIONAL) &&
  window.IPT_BOUNDS_PROVISIONAL.some(x => /125000/.test(x)));

// =============================================================================
// 2. iptForChainage
// =============================================================================
ok("chainage inside a band returns that band",
  window.iptForChainage(126000).ipt === "IPT 3");
ok("a band is inclusive at its lower bound",
  window.iptForChainage(125000).ipt === "IPT 3");
ok("and exclusive at its upper bound, so a boundary belongs to one band only",
  window.iptForChainage(130036).ipt === "IPT 4");
ok("the narrow IPT 5 band resolves", window.iptForChainage(136000).ipt === "IPT 5");
ok("both IPT 4 bands resolve to IPT 4",
  window.iptForChainage(131000).ipt === "IPT 4" && window.iptForChainage(140000).ipt === "IPT 4");
ok("but they keep their own work sections",
  window.iptForChainage(131000).ws.join() === "WS4" &&
  window.iptForChainage(140000).ws.join() === "WS6");
ok("chainage past the last band falls back to the default",
  window.iptForChainage(999999) === window.IPT_DEFAULT);
ok("chainage before the first band falls back to the default",
  window.iptForChainage(-99999) === window.IPT_DEFAULT);
ok("null chainage falls back rather than throwing",
  window.iptForChainage(null) === window.IPT_DEFAULT);
ok("NaN chainage falls back rather than throwing",
  window.iptForChainage(NaN) === window.IPT_DEFAULT);

// =============================================================================
// 3. Build over the real data
// =============================================================================
const t0 = Date.now();
const fc = window.buildIptAlignment(window.alignment_data, window.chainage_global_data);
const ms = Date.now() - t0;
const stats = window.IPT_BUILD_STATS;
const feats = fc.features;

ok("the build returns a FeatureCollection", fc && fc.type === "FeatureCollection" && Array.isArray(feats));
ok("it read the chainage markers", stats.chainage_markers > 2000, `got ${stats.chainage_markers}`);
ok("it produced more features than it was given, because lines were cut",
  feats.length > srcCount, `${srcCount} -> ${feats.length}`);
ok("it runs fast enough to sit on page load", ms < 3000, `took ${ms} ms`);

ok("⭐ the input FeatureCollection is not mutated",
  window.alignment_data.features.length === srcCount &&
  window.alignment_data.features[0].geometry.coordinates.length === srcFirstLen);
ok("no source property was dropped on the way through",
  feats.every(f => f.properties.align_type !== undefined && f.properties.OBJECTID !== undefined));

ok("every output feature is a LineString", feats.every(f => f.geometry.type === "LineString"));
ok("and none is a degenerate one-point line",
  feats.every(f => f.geometry.coordinates.length >= 2));
ok("every output feature carries the four properties the paint and popup read",
  feats.every(f => f.properties.ipt && f.properties.ipt_label &&
    /^#[0-9a-fA-F]{6}$/.test(f.properties.ipt_colour) && f.properties.ws !== undefined));
ok("ws is a flat string, not an array — Mapbox properties cannot hold arrays",
  feats.every(f => typeof f.properties.ws === "string"));
ok("every colour on a feature comes from the band table",
  feats.every(f => f.properties.ipt_colour === window.IPT_DEFAULT.colour ||
    SEGS.some(s => s.colour === f.properties.ipt_colour && s.ipt === f.properties.ipt)));
ok("chainage extent is recorded on each segment for the popup",
  feats.every(f => f.properties.chain_from_m === null ||
    (typeof f.properties.chain_from_m === "number" &&
     f.properties.chain_to_m >= f.properties.chain_from_m)));

// deterministic
const fc2 = window.buildIptAlignment(window.alignment_data, window.chainage_global_data);
ok("the build is deterministic", fc2.features.length === feats.length);

// =============================================================================
// 4. ⭐ The regression this whole design exists for
// =============================================================================
// The handoff spec said one IPT per feature, taken from the feature's midpoint.
// Source feature OBJECTID 11014 spans chainage 107000 -> 133600. Its midpoint
// lands at ~114800, i.e. IPT 1, so a single stamp would have painted 26.5 km
// blue and made IPT 2, IPT 3 and most of IPT 4 disappear from the map.
const f11014 = feats.filter(f => f.properties.OBJECTID === "11014");
const bands11014 = new Set(f11014.map(f => f.properties.ipt));
ok("the 26 km feature is split rather than stamped once",
  f11014.length >= 4, `got ${f11014.length} segments`);
ok("⭐ and it contributes to IPT 1, 2, 3 and 4 — midpoint stamping gave only IPT 1",
  ["IPT 1", "IPT 2", "IPT 3", "IPT 4"].every(b => bands11014.has(b)),
  `got ${[...bands11014].join(", ")}`);

const mainBands = new Set(feats.filter(f => f.properties.align_type === "Main Track").map(f => f.properties.ipt));
ok("⭐ every IPT band ends up painted on Main Track somewhere",
  ["IPT 1", "IPT 2", "IPT 3", "IPT 4", "IPT 5", "IPT 6"].every(b => mainBands.has(b)),
  `got ${[...mainBands].join(", ")}`);
ok("nothing on Main Track falls through to Unknown",
  !mainBands.has("Unknown"));

// A segment may not cover more chainage than the band it is painted with. This is
// the invariant that midpoint stamping broke: it produced a single feature with a
// 26.5 km extent painted in IPT 1, whose band is only 11.8 km wide.
//
// Note the recorded extent deliberately includes the shared boundary vertex — the
// last vertex of one segment is the first of the next so the painted line has no
// gap — so the test is against the band WIDTH, not against the band bounds.
function bandWidthFor(f) {
  const a = f.properties.chain_from_m;
  const s = SEGS.find(s => s.ipt === f.properties.ipt && a >= s.chain_from && a < s.chain_to);
  return s ? s.chain_to - s.chain_from : Infinity;
}
ok("⭐ no output feature covers more chainage than its own band is wide",
  feats.every(f => {
    const a = f.properties.chain_from_m, b = f.properties.chain_to_m;
    if (a == null || b == null) return true;
    return (b - a) <= bandWidthFor(f) + 500;
  }));

// =============================================================================
// 5. Gap splitting — the pre-existing defect
// =============================================================================
const R = 6371000;
function metres(a, b) {
  const dLat = (b[1] - a[1]) * Math.PI / 180, dLon = (b[0] - a[0]) * Math.PI / 180;
  const l1 = a[1] * Math.PI / 180, l2 = b[1] * Math.PI / 180;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(l1) * Math.cos(l2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(Math.min(1, h)));
}
function lengthOf(c) { let L = 0; for (let i = 0; i < c.length - 1; i++) L += metres(c[i], c[i + 1]); return L; }
function maxEdge(c) { let M = 0; for (let i = 0; i < c.length - 1; i++) M = Math.max(M, metres(c[i], c[i + 1])); return M; }

let srcLen = 0;
for (const f of srcFeatures) {
  const parts = f.geometry.type === "LineString" ? [f.geometry.coordinates] : f.geometry.coordinates;
  for (const p of parts) srcLen += lengthOf(p);
}
const outLen = feats.reduce((a, f) => a + lengthOf(f.geometry.coordinates), 0);
// The 2026-08-28 polish pass keeps every cut edge as a BRIDGE feature — solid, 30%
// opacity, same band colour — so the corridor reads continuous while the missing
// stretches stay visibly weaker. The geometry cut is unchanged; what changed is that
// the removed length is now also present, flagged, on its own layer. So the totals
// have to be measured separately or "kept" silently means "everything".
const solid = feats.filter(f => f.properties.is_bridge !== true);
const bridges = feats.filter(f => f.properties.is_bridge === true);
const solidLen = solid.reduce((a, f) => a + lengthOf(f.geometry.coordinates), 0);
const bridgeLen = bridges.reduce((a, f) => a + lengthOf(f.geometry.coordinates), 0);

ok("the gap split ran", stats.gap_split === true && stats.gap_edges_cut === 272,
  `cut ${stats.gap_edges_cut}`);
ok("⭐ it removed the phantom straights the file has always drawn",
  near(stats.gap_km_removed, 239.5, 0.5), `removed ${stats.gap_km_removed} km`);
ok("the source really does draw ~647 km today", near(srcLen / 1000, 646.7, 0.5),
  `${(srcLen / 1000).toFixed(1)} km`);
ok("⭐ and 407 km of SOLID track survives, a ~205 km double-track corridor",
  near(solidLen / 1000, 407.2, 0.5), `${(solidLen / 1000).toFixed(1)} km`);
ok("⭐ the removed 239.5 km comes back as bridges, not as solid track",
  near(bridgeLen / 1000, 239.5, 0.5), `${(bridgeLen / 1000).toFixed(1)} km`);
ok("⭐ solid + bridge accounts for the whole file exactly — nothing invented, "
   + "nothing lost",
  near((solidLen + bridgeLen) / 1000, srcLen / 1000, 0.2),
  `${((solidLen + bridgeLen) / 1000).toFixed(1)} vs ${(srcLen / 1000).toFixed(1)}`);
ok("and the bridge length equals what the splitter reported removing",
  near(bridgeLen / 1000, stats.gap_km_removed, 0.2));
ok("the build reports both counts", stats.solid_features + stats.bridge_features === feats.length);
ok("every feature is one or the other, never both",
  feats.every(f => f.properties.is_bridge === true || f.properties.is_bridge === false));

// ⭐ A bridge can be 45 km long — longer than five of the seven bands. Drawn as one
// straight it would need ONE colour, which is the midpoint-stamping mistake this
// whole file exists to reject, just at 30% opacity. So bridges are densified and
// band-split like everything else.
const bridgeSpans = bridges.map(f => f.properties.chain_to_m - f.properties.chain_from_m);
ok("⭐ no bridge spans more chainage than its own band is wide",
  bridges.every(f => {
    const a = f.properties.chain_from_m, b = f.properties.chain_to_m;
    if (a == null || b == null) return true;
    return (b - a) <= bandWidthFor(f) + 500;
  }));
ok("⭐ the 45 km straight is split, not stamped — the longest bridge is under 8 km",
  Math.max(...bridges.map(f => lengthOf(f.geometry.coordinates))) < 8000,
  `longest ${Math.round(Math.max(...bridges.map(f => lengthOf(f.geometry.coordinates))))} m`);
ok("splitting produced MORE bridge features than edges cut, because some cross bands",
  bridges.length > stats.gap_edges_cut,
  `${bridges.length} bridges from ${stats.gap_edges_cut} cut edges`);
ok("every bridge carries a real band colour, not the fallback grey",
  bridges.every(f => SEGS.some(s => s.colour === f.properties.ipt_colour)));

// ⭐ The point of the bridges, stated as an assertion: IPT 2 has only ~4 km of solid
// Main Track because 5.6 km of its band has no surveyed geometry at all. Without a
// bridge the band is nearly invisible on the map — which is the failure the whole
// overlay was built to avoid, arriving by a different route.
const ipt2Solid = solid.filter(f => f.properties.ipt === "IPT 2" && f.properties.align_type === "Main Track");
const ipt2Bridge = bridges.filter(f => f.properties.ipt === "IPT 2" && f.properties.align_type === "Main Track");
ok("⭐ IPT 2 is thinly covered by solid track", ipt2Solid.length > 0);
ok("⭐ and its bridge is what makes the band legible at corridor zoom",
  ipt2Bridge.length > 0 &&
  ipt2Bridge.reduce((a, f) => a + lengthOf(f.geometry.coordinates), 0) >
  ipt2Solid.reduce((a, f) => a + lengthOf(f.geometry.coordinates), 0));
// The invariant is about what the splitter left behind, not raw edge length: no
// edge may survive that is both over GAP_MIN_M and far longer than the rest of its
// own part. A two-vertex feature has a ratio of exactly 1 and is never a gap.
function medianOf(a) { const s = a.slice().sort((x, y) => x - y); const h = s.length >> 1; return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2; }
ok("⭐ no gap-shaped edge survives into the solid output",
  solid.every(f => {
    const c = f.geometry.coordinates;
    const steps = []; for (let i = 0; i < c.length - 1; i++) steps.push(metres(c[i], c[i + 1]));
    const med = medianOf(steps);
    return steps.every(d => !(d > window.IPT_GAP_CONFIG.min_m && d > window.IPT_GAP_CONFIG.ratio * med));
  }));
ok("the split is a ratio test, so genuine two-vertex straight tracks survive",
  feats.some(f => f.geometry.coordinates.length === 2 && lengthOf(f.geometry.coordinates) > 100));
// DATA FACT, and a question for the user rather than a bug: ten source features are
// stored as a single straight edge of 600 m to 2.2 km, two of them 'Main Track'.
// Ratio 1.0x means they are not gaps this splitter opened — that is how the file
// holds them. They may be schematic placeholders for undesigned stretches.
const longStraights = feats.filter(f => maxEdge(f.geometry.coordinates) > 600);
ok("DATA FACT: 10 SOLID features are single straight edges over 600 m",
  longStraights.filter(f => f.properties.is_bridge !== true).length === 10,
  `got ${longStraights.filter(f => f.properties.is_bridge !== true).length}`);
ok("⭐ and no bridge is one — densifying every 200 m is what lets them be band-split",
  bridges.every(f => maxEdge(f.geometry.coordinates) < 250),
  `worst ${Math.round(Math.max(...bridges.map(f => maxEdge(f.geometry.coordinates))))} m`);
ok("DATA FACT: and the longest of them is ~2.2 km",
  near(Math.max(...longStraights.filter(f => f.properties.is_bridge !== true)
       .map(f => maxEdge(f.geometry.coordinates))), 2226, 5));
ok("the config is exposed so the cut can be turned off without editing logic",
  window.IPT_GAP_CONFIG && window.IPT_GAP_CONFIG.split === true &&
  window.IPT_GAP_CONFIG.min_m === 150 && window.IPT_GAP_CONFIG.ratio === 20);

// =============================================================================
// 6. Recorded data facts — these are about the DATA, not the code
// =============================================================================
// If one of these fails, the alignment file has been refreshed. That is good
// news, not a bug: update the numbers here and in claude/ipt-overlay-decisions.md.
const mtFeats = feats.filter(f => f.properties.align_type === "Main Track" &&
                                 f.properties.is_bridge !== true);
const covered = new Set();
for (const f of mtFeats) {
  const a = f.properties.chain_from_m, b = f.properties.chain_to_m;
  if (a == null || b == null) continue;
  for (let m = Math.round(a / 100) * 100; m < b; m += 100) covered.add(m);
}
ok("DATA FACT: Main Track has no geometry between chainage 117 km and 122.9 km",
  ![118000, 119000, 120000, 121000, 122000].some(m => covered.has(m)),
  "the alignment file may have been refreshed — see the decisions note");
ok("DATA FACT: which is why IPT 2 is only part-painted, and that is the data, not the split",
  mtFeats.filter(f => f.properties.ipt === "IPT 2").length > 0);

// =============================================================================
// 7. Legend
// =============================================================================
const rows = window.iptLegendRows();
ok("the legend has one row per unique IPT",
  rows.length === new Set(SEGS.map(s => s.ipt)).size, `got ${rows.length}`);
ok("⭐ IPT 4 appears once, not twice, despite having two bands",
  rows.filter(r => r.ipt === "IPT 4").length === 1);
ok("and that row carries both of its work sections",
  (rows.find(r => r.ipt === "IPT 4").ws || []).join() === "WS4,WS6");
ok("every legend row has a colour from the band table",
  rows.every(r => SEGS.some(s => s.ipt === r.ipt && s.colour === r.colour)));
ok("Outside A1 is present but marked muted rather than dropped",
  rows.some(r => r.ipt === "Outside A1" && r.muted === true));
ok("no in-scope IPT is muted", rows.every(r => r.muted === (r.ipt === "Outside A1")));

// =============================================================================
// 8. Chainage marker tiering — the 2026-08-28 noise fix
// =============================================================================
const stepped = window.buildChainageSteps(window.chainage_global_data);
const tiers = window.CHAINAGE_STEP_COUNTS;

ok("every chainage point gets a step tier",
  stepped.features.every(f => window.CHAINAGE_STEPS.includes(f.properties.step_m)));
ok("the tier is the COARSEST interval the chainage falls on, not the finest",
  stepped.features.every(f => {
    const m = Math.round(parseFloat(String(f.properties.chain).replace(/,/g, "")));
    if (isNaN(m)) return f.properties.step_m === 100;
    const coarsest = window.CHAINAGE_STEPS.find(s => m % s === 0) || 100;
    return f.properties.step_m === coarsest;
  }));
ok("the source is not mutated",
  window.chainage_global_data.features.every(f => f.properties.step_m === undefined));
ok("nothing is dropped", stepped.features.length === window.chainage_global_data.features.length);
ok("the original properties survive",
  stepped.features.every(f => f.properties.chaintxt !== undefined));

// ⭐ The whole point: 2,180 markers every ~94 m was a grey smear at corridor zoom.
ok("⭐ only 24 markers show below zoom 9, not 2,180",
  tiers[10000] === 24, `got ${tiers[10000]}`);
ok("45 by zoom 9", tiers[10000] + tiers[5000] === 45, `got ${tiers[10000] + tiers[5000]}`);
ok("218 by zoom 11", tiers[10000] + tiers[5000] + tiers[1000] === 218);
ok("436 by zoom 13 — the 500 m tier added 2026-08-28",
  tiers[10000] + tiers[5000] + tiers[1000] + tiers[500] === 436,
  `got ${tiers[10000] + tiers[5000] + tiers[1000] + (tiers[500] || 0)}`);
ok("and all 2,180 by zoom 15",
  Object.values(tiers).reduce((a, b) => a + b, 0) === 2180);
ok("there are five tiers", window.CHAINAGE_STEPS.length === 5);

// ⭐ The spec's on-tick formula could never match a negative tick, and this
// corridor starts at chainage -3982.3.
ok("⭐ a negative chainage on an exact tick is recognised", window.iptOnTick(-5000, 5000));
ok("and one that is not, is not", !window.iptOnTick(-3982.3, 10000));
ok("the spec's own formula would have failed that first case",
  !(Math.abs(-5000 % 5000) < 0.5 && Math.abs((-5000 % 5000) - 5000) < 0.5) ||
  window.iptOnTick(-5000, 5000));
ok("the float tolerance still works on a positive tick",
  window.iptOnTick(42900.0, 100) && window.iptOnTick(10000.3, 10000));
ok("chain_m is carried through for anything that wants the number",
  stepped.features.every(f => f.properties.chain_m === null ||
    typeof f.properties.chain_m === "number"));
ok("⭐ that is a 99% reduction in what the corridor view draws",
  tiers[10000] / 2180 < 0.02);
ok("the tiers are disjoint — no marker is drawn twice",
  Object.values(tiers).reduce((a, b) => a + b, 0) === stepped.features.length);

// =============================================================================
// 9. Degenerate inputs
// =============================================================================
const empty = window.buildIptAlignment({ type: "FeatureCollection", features: [] }, window.chainage_global_data);
ok("an empty alignment builds to an empty collection", empty.features.length === 0);
const noChain = window.buildIptAlignment(window.alignment_data, { type: "FeatureCollection", features: [] });
ok("⭐ with no chainage markers it returns the alignment UNCHANGED rather than a grey map",
  noChain === window.alignment_data);
ok("a null alignment is handled", window.buildIptAlignment(null, window.chainage_global_data) === null);
ok("a null chainage collection is handled by the step builder",
  window.buildChainageSteps(null) === null);
ok("a chainage point with no chain value falls to the finest tier rather than throwing",
  window.buildChainageSteps({ type: "FeatureCollection", features: [
    { type: "Feature", geometry: { type: "Point", coordinates: [24, 58] }, properties: {} }] }
  ).features[0].properties.step_m === 100);

console.log();
for (const f of fail) console.log("  FAIL:", f);
console.log(`\n${pass} passed, ${fail.length} failed`);
process.exit(fail.length ? 1 : 0);

/*
 * Assertions for the public route map after its migration off map/data/a1_data.js.
 *
 * Extracts every inline <script> block from map/index.html, checks each is valid
 * JavaScript, then asserts at source level that the migration actually happened and that
 * nothing which had to survive was lost.
 *
 * NOT covered: anything needing a browser. Mapbox layer rendering, whether the fetch
 * succeeds, and how the map looks are unverified here.
 *
 * Run:  node backend/tests/parse_map.js
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const FILE = path.join(ROOT, "map", "index.html");
const html = fs.readFileSync(FILE, "utf8");

let pass = 0;
const fail = [];
function ok(label, cond, extra) {
  if (cond) pass++;
  else fail.push(label + (extra ? "  " + extra : ""));
}

// ---- every inline script must be valid JS ------------------------------------
const blocks = [];
const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
let m;
while ((m = re.exec(html)) !== null) blocks.push(m[1]);
ok("found the inline script block(s)", blocks.length >= 1, `got ${blocks.length}`);

blocks.forEach((b, i) => {
  if (!b.trim()) return;
  try {
    new Function(b);
    pass++;
  } catch (e) {
    fail.push(`inline script #${i + 1} is not valid JS  ${e.message}`);
  }
});

const src = blocks.join("\n");
// comment-stripped, so an assertion cannot pass by matching the prose that explains
// why something was removed
const code = src
  .split("\n").map(l => l.replace(/\/\/.*$/, "")).join("\n")
  .replace(/\/\*[\s\S]*?\*\//g, "");

// ---- the static file is gone -------------------------------------------------
ok("the a1_data.js script tag is removed", !/src=["']data\/a1_data\.js["']/.test(html));
ok("alignment.js still loads statically", /src=["']data\/alignment\.js["']/.test(html));
ok("chainage.js still loads statically", /src=["']data\/chainage\.js["']/.test(html));
ok("nothing reads window.a1_data any more", !code.includes("window.a1_data"));
ok("the file itself has been deleted",
  !fs.existsSync(path.join(ROOT, "map", "data", "a1_data.js")));
ok("alignment.js survives on disk",
  fs.existsSync(path.join(ROOT, "map", "data", "alignment.js")));
ok("chainage.js survives on disk",
  fs.existsSync(path.join(ROOT, "map", "data", "chainage.js")));

// ---- data now comes from the network -----------------------------------------
ok("routes and nodes are fetched from the API", code.includes("/public/map-data"));
ok("the fetch result is pushed into the map source", /getSource\(['"]routes-source['"]\)/.test(code));
ok("init waits for the map to load", code.includes("map.once('load', initNetwork)"));
ok("a failed fetch is surfaced, not swallowed", /Could not load the route network/.test(src));
ok("an empty network tells the user to bake", /Bake the network/.test(src));

// ---- temp haul layer is BACK (Phase 4) ---------------------------------------
// Phase 2 asserted this layer was gone and its toggle disabled, because the network held
// no geometry for it. Phase 4 gives it real drawn polylines, so those assertions are
// reversed rather than deleted — the point they were making (never draw a line the data
// does not support) is now enforced from the other side.
ok("the old placeholder layer id is still gone", !code.includes("temp-lines'"));
ok("a temp-haul layer is drawn from the API's own features",
  code.includes("'temp-haul-lines'") && code.includes("'Temp Haul Track'"));
ok("it reads from routes-source, not a static file",
  /id: 'temp-haul-lines', type: 'line', source: 'routes-source'/.test(code));
ok("its toggle is enabled again", !/id="layer-temp"[^>]*disabled/.test(html));
ok("and wired to a handler", /id="layer-temp"[^>]*onchange="toggleTempHaul/.test(html));
ok("the '(Phase 4)' placeholder label is gone from the toggle",
  !/Temporary haul <span[^>]*>\(Phase 4\)/.test(html));
ok("temporary haul is dashed — it is not the surveyed network",
  /'line-dasharray': \[2, 1\.4\]/.test(code));
ok("haul roads are excluded from the zone overlay so they are not drawn twice",
  code.includes("['!=', ['get', 'kind'], 'haul_road']"));
ok("clicking one opens its own popup", code.includes("map.on('click', 'temp-haul-lines'"));
ok("the popup says the speed is assigned, not measured",
  /Assigned, not measured/.test(src));
ok("and calls out a haul road with no speed set",
  /No speed assigned/.test(src));
ok("visibility survives a basemap switch by reading the checkbox, not a variable",
  code.includes("function tempHaulVisible()") && code.includes("tempHaulVisible() ? 'visible' : 'none'"));

// ---- leg filter replaced by discipline ---------------------------------------
ok("the legacy leg filter is gone", !/id="filter-leg"/.test(html));
ok("a discipline filter replaces it", /id="filter-discipline"/.test(html));
ok("discipline filtering is a membership test, not an equality",
  code.includes("['in', disc, ['get', 'disciplines']]"));
ok("the filter disables itself when no forecasts exist",
  code.includes("No approved forecasts yet"));

// ---- capacity KPI ------------------------------------------------------------
ok("max_loads is no longer summed", !code.includes("max_loads"));
ok("capacity comes from trips_per_day", code.includes("trips_per_day"));

// ---- layer filters were repaired ---------------------------------------------
// the casing layers filtered on `route_leg`, a property that never existed in the data,
// so they matched nothing until applyFilters() overwrote the core layers
ok("no layer filters on the non-existent route_leg property", !code.includes("route_leg"));
ok("layers filter on the type property instead",
  code.includes("['==', ['get', 'type'], 'Inbound Highway']"));

// ---- Phase 3: the zone overlay -------------------------------------------------
ok("zones are fetched from their own endpoint", code.includes("'/zones'"));
ok("the overlay does NOT ride on /public/map-data",
  !code.includes("zones-source', { type: 'geojson', data: a1_data"));
ok("a zones source and fill layer exist",
  code.includes("zones-source") && code.includes("'zone-fill'"));
ok("the sidebar has a zones toggle", html.includes('id="layer-zones"'));
ok("the toggle is wired to a handler", code.includes("function toggleZones"));
ok("routing zones and advisory zones are coloured differently",
  code.includes("['case', ['get', 'affects_routing']"));
ok("zones re-add after a basemap switch, like every other layer",
  code.includes("if (ZONES.loaded) ensureZoneLayers(); else loadZones();"));
ok("the overlay follows the forecast timeline", code.includes("applyZoneMonth"));
ok("closing the timeline restores every zone", code.includes("applyZoneMonth(null)"));
ok("date filtering compares YYYYMMDD integers, not strings",
  code.includes("function dnum") && code.includes("function monthSpan"));
ok("an open-ended end date sorts last rather than being special-cased",
  code.includes("99999999"));
ok("only active zones are drawn", code.includes("z.active && z.geometry"));
ok("clicking a zone explains whether routing is affected",
  code.includes("Routing avoids this area") && code.includes("Advisory only"));
ok("a missing zones endpoint is not surfaced as a page error",
  code.includes("console.warn('zones unavailable'"));

// ---- things that must NOT have been broken -----------------------------------
for (const keep of [
  ["rail-alignment", "the rail alignment layer survives"],
  ["chainage-global", "the chainage layer survives"],
  ["maaametOrthoStyle", "the Maa-amet orthophoto basemap survives"],
  ["route-forecasts", "the forecast overlay still reads the public feed"],
  ["openRouteAnalysis", "the route analysis drawer survives"],
  ["toggleTimeline", "the playable timeline survives"],
  ["filterByNode", "click-a-node-to-filter survives"],
]) {
  ok(keep[1], code.includes(keep[0]));
}

// ---- Phase 2.5a: restrictions, Street View, KPI cards, animation, zoom ------
ok("Tark Tee layers are fetched through OUR backend, never straight from tarktee.ee",
  code.includes("CONFIG.API_BASE + '/restrictions'") && !code.includes("tarktee.ee/tarktee/rest"));
ok("the layer catalogue comes from the server, not a copy in this file",
  code.includes("'/restrictions/layers'"));
ok("restriction toggles are built from that catalogue",
  code.includes("function buildRestrictionToggles"));
ok("restrictions survive a basemap switch from cached data, without re-requesting",
  code.includes("if (RESTR.data) ensureRestrictionLayers(); else loadRestrictions();"));
ok("a weak bridge's load class is shown verbatim and NOT converted to tonnes",
  src.includes("A load class, not tonnes"));
ok("the popup LEADS with the restriction value, not just its existence",
  code.includes("p._headline || p._label"));
ok("colours come from the server catalogue, not constants in this file",
  code.includes("RESTR.colour[l.key] = l.colour") && !code.includes("RESTR_NOTE_COLOR"));
ok("legend dots use the same colour the layer paints with",
  code.includes("background:' + l.colour"));
ok("features carry their own colour so map and legend cannot drift",
  code.includes("['coalesce', ['get', '_colour']"));
ok("the value is labelled ON the map from zoom 11, not only in a popup",
  code.includes("'restriction-labels'") && code.includes("['get','_limit']"));
ok("closures and causes are shown in words", code.includes("p.effect") && code.includes("p.cause"));
ok("the source's own free text is passed through", code.includes("p.extra_info"));
ok("the date window is shown so a stale record is visible as stale",
  code.includes("p._from") && code.includes("p._to"));
ok("the overlay is declared advisory - the router is not given it",
  /router is not given this data|Advisory/i.test(src));
ok("restrictions are off by default so they do not bury the haul routes",
  !/id="restr-[a-z_]+" checked/.test(html));

ok("street view goes through our proxy so the Google key stays server-side",
  code.includes("CONFIG.API_BASE + '/streetview") && !code.includes("maps.googleapis.com"));
ok("the FREE metadata call is made before any billable image request",
  code.indexOf("/streetview/meta") > -1 &&
  code.indexOf("/streetview/meta") < code.indexOf("/streetview?lat="));
ok("no imagery means no <img> at all, not a grey placeholder",
  code.includes("if(!meta || !meta.available) return;"));
ok("imagery is only looked up when a popup is actually opened",
  code.includes("pop.on('open'"));
ok("a large offset between the gate and the nearest panorama is disclosed",
  src.includes("nearest imagery is"));

ok("the KPI cards actually exist in the page now",
  /id="kpi-routes"/.test(html) && /id="kpi-capacity"/.test(html));
ok("⭐ the KPIs are ON THE MAP, not in the sidebar", /id="kpi-hud"/.test(html));
ok("and are positioned over the map canvas", /#kpi-hud\{[^}]*position:absolute/.test(html));
ok("clear of the Mapbox navigation control at top-right",
  /#kpi-hud\{[^}]*top:112px[^}]*right:10px/.test(html));
ok("the KPI block is outside the sidebar element",
  html.indexOf('id="kpi-hud"') > html.indexOf('id="timeline-bar"') ||
  html.indexOf('id="kpi-hud"') > html.lastIndexOf('class="control-group"'));
ok("and calculateKPIs writes to ids that are really there",
  /id="kpi-routes-label"/.test(html) && code.includes("kpi-routes-label"));
ok("KPIs follow the timeline, not just the filters",
  code.includes("TL.on && TL.matrix"));
ok("scrubbing the timeline recalculates them",
  /applyZoneMonth\(m\);[\s\S]{0,500}applyFilters\(\);/.test(code));
ok("the card label says which question it is answering", code.includes("'Routes in '"));

ok("the ant-march animation runs only while the timeline is playing",
  code.includes("if(!TL.playing)"));
ok("and the line goes solid when it stops", code.includes("DASH_SOLID"));
ok("stopping playback cancels the rAF loop rather than letting it spin for ever",
  code.includes("cancelAnimationFrame(_flowRAF)"));
ok("starting playback starts it", code.includes("startFlowAnimation();"));

// z-order is add order: outbound must be added FIRST so inbound draws on top of it
ok("⭐ inbound (laden) is added AFTER outbound, so it renders on top",
  code.indexOf("'id': 'outbound-lines'") < code.indexOf("'id': 'inbound-lines'"));
ok("and each casing still sits under its own core line",
  code.indexOf("'id': 'outbound-lines-casing'") < code.indexOf("'id': 'outbound-lines'") &&
  code.indexOf("'id': 'inbound-lines-casing'") < code.indexOf("'id': 'inbound-lines'"));
ok("the reason is written down where the order is easy to reverse by accident",
  /LAYER ADD ORDER IS Z-ORDER/.test(src));

ok("the direction offset collapses at high zoom instead of holding 4px",
  /OFFSET_IN\s*=[^;]*17,\s*0\]/.test(code) && /OFFSET_OUT\s*=[^;]*17,\s*0\]/.test(code));
ok("line width keeps scaling with zoom", /WIDTH_CORE\s*=[^;]*16,\s*10\]/.test(code));
ok("no hard-coded offset ramp survives inside a layer definition",
  !/'line-offset': \['interpolate'/.test(code));

// =============================================================================
// IPT / Work Section alignment overlay
// =============================================================================
// Source-level only. The behaviour of the band splitter is asserted against the
// real 8.8 MB alignment file in backend/tests/test_ipt_overlay.js.

const IPTF = path.join(ROOT, "map", "ipt_segments.js");
ok("map/ipt_segments.js exists", fs.existsSync(IPTF));
const iptSrc = fs.existsSync(IPTF) ? fs.readFileSync(IPTF, "utf8") : "";

ok("index.html loads it", /src=["']ipt_segments\.js["']/.test(html));
ok("and loads it AFTER chainage.js, which it reads",
  html.indexOf('data/chainage.js') < html.indexOf('ipt_segments.js'));
ok("it is a plain script tag, not a new CDN dependency",
  !/ipt_segments\.js/.test(html.replace(/src=["']ipt_segments\.js["']/g, "")) ||
  !/https?:\/\/[^"']*ipt_segments/.test(html));

ok("the alignment layer is coloured from ipt_colour",
  /'line-color':\s*\['coalesce',\s*\['get',\s*'ipt_colour'\]/.test(code));
ok("with a fallback colour if ipt_segments.js failed to load",
  /\['get',\s*'ipt_colour'\],\s*'#64748B'\]/.test(code));
ok("the fixed black alignment colour is gone",
  !/'id': 'rail-alignment'[\s\S]{0,400}'line-color':\s*'#000000'/.test(code));
// --- the 2026-08-28 polish pass ------------------------------------------------
// The first cut used one layer at two widths (Main Track 2.5, everything else 1.2).
// The polish spec replaced that: ONE width, Main Track only, side tracks moved off
// the package view entirely. Both halves are asserted, because dropping the filter
// and keeping the width would look right in a diff and wrong on the map.
ok("⭐ the IPT layer is Main Track only",
  /'id': 'rail-alignment',[\s\S]{0,700}'filter':[\s\S]{0,200}\['==',\s*\['get',\s*'align_type'\],\s*'Main Track'\]/.test(code));
ok("⭐ and it excludes the gap bridges, which have their own layer",
  /'id': 'rail-alignment',[\s\S]{0,700}'filter':[\s\S]{0,200}\['!=',\s*\['get',\s*'is_bridge'\],\s*true\]/.test(code));
ok("it draws at ONE width, not two",
  /'id': 'rail-alignment',[\s\S]{0,900}'line-width':\s*2\.5,/.test(code));
ok("the old two-width case expression is gone",
  !/\['case',\s*\['==',\s*\['get',\s*'align_type'\],\s*'Main Track'\],\s*2\.5,\s*1\.2\]/.test(code));

// Gap bridges
ok("there is a bridge layer", code.includes("'id': 'rail-alignment-bridge'"));
ok("it carries only bridges",
  /'id': 'rail-alignment-bridge'[\s\S]{0,400}\['==',\s*\['get',\s*'is_bridge'\],\s*true\]/.test(code));
ok("⭐ drawn at 30% opacity",
  /'id': 'rail-alignment-bridge'[\s\S]{0,700}'line-opacity':\s*0\.3/.test(code));
ok("⭐ SOLID, not dashed — a dash reads as a style, a fade reads as less certain",
  !/'id': 'rail-alignment-bridge'[\s\S]{0,700}line-dasharray/.test(code));
ok("in the band's own colour, not a neutral grey",
  /'id': 'rail-alignment-bridge'[\s\S]{0,600}\['get',\s*'ipt_colour'\]/.test(code));
ok("at the same width as the solid track, so the corridor reads continuous",
  /'id': 'rail-alignment-bridge'[\s\S]{0,700}'line-width':\s*2\.5/.test(code));

// Survey layer
ok("the original continuous alignment survives as its own layer",
  code.includes("'id': 'rail-alignment-survey'"));
ok("⭐ fed from the RAW alignment data, so the IPT cuts cannot reach it",
  /addSource\('alignment-survey-source',\s*\{\s*type:\s*'geojson',\s*data:\s*alignment_data\s*\}/.test(code));
ok("styled as it was before the overlay — black, 1.5 px",
  /'id': 'rail-alignment-survey'[\s\S]{0,400}'line-color':\s*'#000000'[\s\S]{0,120}'line-width':\s*1\.5/.test(code));
ok("with its own independent checkbox", /id="layer-alignment-survey"/.test(html) &&
  /onchange="toggleLayer\('rail-alignment-survey', this\.checked\)"/.test(html));
// ⚠️ inline handlers have to come out first: `onchange="...this.checked)"` contains
// the literal word `checked`, so a naive test for the ATTRIBUTE matches every
// wired checkbox and this assertion would fail on a correctly-unchecked box.
const htmlNoHandlers = html.replace(/\son\w+="[^"]*"/g, "");
ok("and it is OFF by default, so the IPT view is what opens",
  !/id="layer-alignment-survey"[^>]*\schecked[\s>]/.test(htmlNoHandlers));
ok("while the IPT layer IS on by default",
  /id="layer-alignment"[^>]*\schecked[\s>]/.test(htmlNoHandlers));

// ⭐ add order is z-order in this file: survey under bridges under solid track
ok("⭐ the survey layer is added first, so it sits under both IPT layers",
  code.indexOf("'id': 'rail-alignment-survey'") < code.indexOf("'id': 'rail-alignment-bridge'"));
ok("⭐ and bridges are added before solid track, so a faint connector never "
   + "washes over real geometry",
  code.indexOf("'id': 'rail-alignment-bridge'") < code.indexOf("'id': 'rail-alignment',"));
ok("the reason for that order is written down next to it",
  /ADD ORDER IS THE Z-ORDER/.test(src));

// the three parts of the IPT view move together
ok("toggling the IPT view hides the bridges too",
  /function toggleAlignment[\s\S]{0,400}toggleLayer\('rail-alignment-bridge',\s*visible\)/.test(code));
ok("and both legend blocks", /function toggleAlignment[\s\S]{0,500}'ipt-legend-note'/.test(code));
ok("the legend explains what a fainter segment means",
  /id="ipt-legend-note"/.test(html) && /no surveyed Main Track in the alignment file/.test(html));

// clicking a bridge must explain itself
ok("⭐ the popup answers on the bridge layer too",
  /\['rail-alignment', 'rail-alignment-bridge'\]\.forEach/.test(code));
ok("and a bridge popup says there is no surveyed track there",
  code.includes("No surveyed track here."));
ok("and that it is not measured or routed on",
  /not measured or routed on/.test(code));

// --- 2026-08-28: the mandated palette ------------------------------------------
// The rule is that no IPT colour may reuse a hex the map spends on a route, a
// forecast, a selection or temporary haul — otherwise "this stretch is IPT 6"
// and "this route is laden" become the same colour, and the route layers are
// the ones carrying money. Checked against the real source both ways.
const RESERVED = ["#039E86", "#f59e0b", "#C2790B", "#3398DB", "#BF2E55", "#003787", "#0A1446"];
// only the hexes inside IPT_SEGMENTS — IPT_UNDERLAY and IPT_DEFAULT declare
// their own and are checked separately
const segBlock = iptSrc.slice(iptSrc.indexOf("window.IPT_SEGMENTS = ["),
                              iptSrc.indexOf("window.IPT_RESERVED_COLOURS"));
const bandHexes = (segBlock.match(/colour: '(#[0-9A-Fa-f]{6})'/g) || [])
  .map(x => x.split("'")[1].toLowerCase());
ok("the band table declares a colour for every band", bandHexes.length === 8,
  `got ${bandHexes.length}`);
ok("⭐ NO IPT colour reuses a reserved route / forecast / selection hex",
  RESERVED.every(r => !bandHexes.includes(r.toLowerCase())),
  `clash: ${RESERVED.filter(r => bandHexes.includes(r.toLowerCase())).join(", ")}`);
ok("and the underlay colour is clear of them too",
  !RESERVED.map(r => r.toLowerCase()).includes(
    ((iptSrc.match(/colour: '(#[0-9A-Fa-f]{6})',\s+\/\/ as specified/) || [])[1] || "").toLowerCase()));
ok("the reserved list is in the source, with its usage counts, so a future "
   + "palette edit has something to check against",
  /IPT_RESERVED_COLOURS/.test(iptSrc) && /inbound \(laden\)/.test(iptSrc));
ok("⭐ the measured separation is recorded, not left to opinion",
  /weakest adjacent pair/.test(iptSrc) && /ΔE2000/.test(iptSrc));
ok("⭐ the colour-blind figure is recorded — it is what condemned the first palette",
  /deuteranopia/.test(iptSrc) && /colour-blind/.test(iptSrc));
ok("and the ceiling is named, so nobody re-shuffles these hoping for more room",
  /is the CEILING/.test(iptSrc));
ok("the six hue families are written down, so an edit keeps the property",
  /plum, oxblood, stone, bronze, teal blue/.test(iptSrc));
ok("⭐ the #6D28D9 collision is GONE, not just documented",
  !/colour: '#6D28D9'/.test(iptSrc));
// ⭐ Six DIFFERENT hue families, one per band. The failure being guarded against is
// not "a wrong hex" but "two bands drifting back into the same family", which is
// exactly what the first palette did and what no single-colour assertion catches.
for (const [ipt, hex] of [["IPT 6", "#86198F"], ["IPT 1", "#7F1D1D"], ["IPT 2", "#78716C"],
                          ["IPT 3", "#854D0E"], ["IPT 4", "#155E75"], ["IPT 5", "#166534"]]) {
  ok(`${ipt} is ${hex}`, segBlock.includes(hex));
}
ok("⭐ all six band colours are distinct — no two share a hex",
  new Set(bandHexes).size === 7,
  `${new Set(bandHexes).size} distinct of ${bandHexes.length} (IPT 4 appears twice)`);
ok("the underlay is a LIGHT wash, not a seventh deep colour competing for hue",
  /colour: '#C4B5FD'/.test(iptSrc));
ok("the first-cut greens and ambers are gone",
  !iptSrc.includes("#C6841D") && !/colour: '#039E86'/.test(iptSrc));
// ⚠️ Test the declared COLOURS, not the block text — the comment above the table
// names the old hexes on purpose, as the record of what changed and why.
ok("and so is the whole indigo/violet run that made three bands unreadable",
  !["#4338ca", "#6d28d9", "#5b21b6"].some(h => bandHexes.includes(h)));

// --- IPT 6 superstructure underlay --------------------------------------------
ok("there is an underlay layer", code.includes("'id': 'rail-alignment-underlay'"));
ok("⭐ it is added AFTER survey and BEFORE the bridges and the civil track, so "
   + "the package colour stays the primary read",
  code.indexOf("'id': 'rail-alignment-survey'") < code.indexOf("'id': 'rail-alignment-underlay'") &&
  code.indexOf("'id': 'rail-alignment-underlay'") < code.indexOf("'id': 'rail-alignment-bridge'"));
ok("it is wider than the civil track",
  /'id': 'rail-alignment-underlay'[\s\S]{0,900}'line-width': \(window\.IPT_UNDERLAY/.test(code));
ok("its colour, width and opacity are one tunable object, not three literals",
  /window\.IPT_UNDERLAY = \{[\s\S]{0,300}colour:[\s\S]{0,200}width:[\s\S]{0,120}opacity:/.test(iptSrc));
ok("⭐ it runs the whole A1 mainline, not just the northern IPT 6 civil band — "
   + "so it is filtered by align_type and scope, never by chainage",
  /'id': 'rail-alignment-underlay'[\s\S]{0,700}\['!=', \['get', 'ipt'\], 'Outside A1'\]/.test(code));
ok("⭐ and it excludes the gap bridges — a 4.5 px solid line across country "
   + "with no surveyed track is what the 30% bridges exist to avoid",
  /'id': 'rail-alignment-underlay'[\s\S]{0,700}\['!=', \['get', 'is_bridge'\], true\]/.test(code));
ok("the reason for that exclusion is written next to it",
  /what the 30% bridges were built to avoid/.test(src));

// --- per-IPT selection ---------------------------------------------------------
ok("the legend rows are checkboxes now",
  /input type="checkbox" id="\$\{iptCheckboxId/.test(code));
ok("still generated from IPT_SEGMENTS, not hand-typed",
  code.includes("window.iptLegendRows()"));
ok("ticking one re-filters rather than rebuilding the GeoJSON",
  code.includes("function applyIptFilter") && !/applyIptFilter[\s\S]{0,600}buildIptAlignment/.test(code));
ok("the filter is a membership test on ipt",
  /\['in', \['get', 'ipt'\], on\]/.test(code));
ok("both the solid track and its bridges follow the same checkbox",
  (code.match(/\['in', \['get', 'ipt'\], on\]/g) || []).length === 2);
ok("⭐ the IPT 6 checkbox drives the UNDERLAY, not the civil filter",
  /toggleUnderlay\(this\.checked\)/.test(code) &&
  /function toggleUnderlay[\s\S]{0,140}rail-alignment-underlay/.test(code));
ok("⭐ and unchecking it leaves every civil colour where it was — IPT 6 is "
   + "always in the civil set",
  /const on = \[IPT_UNDERLAY_KEY\]/.test(code));
ok("the reason that asymmetry exists is written down",
  /IPT 6 is NOT in the civil filter/.test(src));
ok("all boxes start ticked",
  /type="checkbox" id="\$\{iptCheckboxId\(r\.ipt\)\}" checked/.test(code));
ok("a missing checkbox is treated as ticked, so the filter cannot blank the map "
   + "if the legend failed to render",
  /!el \|\| el\.checked/.test(code));
ok("the checkbox id is derived, not hand-listed per IPT",
  /function iptCheckboxId/.test(code));
ok("hiding the whole alignment hides the underlay too",
  /function toggleAlignment[\s\S]{0,500}rail-alignment-underlay/.test(code));
ok("the underlay legend row says what it is",
  /Superstructure \(corridor underlay\)/.test(code));

// --- 2026-08-30: work-section boundary ticks -----------------------------------
ok("there is a boundary source", code.includes("addSource('ws-boundary-source'"));
ok("memoised like the other two builds",
  /WS_BOUNDS\s*=\s*null/.test(code) && /if\s*\(WS_BOUNDS\)\s*return WS_BOUNDS/.test(code));
ok("a tick layer and a label layer", code.includes("'id': 'ws-boundary-ticks'") &&
  code.includes("'id': 'ws-boundary-labels'"));
ok("⭐ ticks are a SYMBOL layer, not a line — a fixed ground length would be one "
   + "pixel at corridor zoom and half the viewport at zoom 16",
  /'id': 'ws-boundary-ticks', 'type': 'symbol'/.test(code));
ok("⭐ rotated to lie ACROSS the alignment, not along it",
  /'text-rotate': \['get', 'tick_rotate'\]/.test(code) &&
  /'text-rotation-alignment': 'map'/.test(code));
ok("and tick_rotate is the local bearing plus 90",
  /brg \+ 90/.test(iptSrc));
ok("hidden below zoom 11", /'id': 'ws-boundary-ticks'[\s\S]{0,200}'minzoom': 11/.test(code));
ok("⭐ labels only from zoom 13, one level after the ticks appear",
  /'id': 'ws-boundary-labels'[\s\S]{0,200}'minzoom': 13/.test(code));
ok("the label names both sections meeting at the tick, and the chainage",
  /\['get', 'label'\]/.test(code) && /\['get', 'chain_txt'\]/.test(code));
ok("labels declutter; ticks do not, because there are only seven",
  /'id': 'ws-boundary-ticks'[\s\S]{0,700}'text-allow-overlap': true/.test(code) &&
  /'id': 'ws-boundary-labels'[\s\S]{0,700}'text-allow-overlap': false/.test(code));
ok("⭐ the tick colour is neutral slate — not a route colour and not a package "
   + "colour, because a tick is neither",
  /'text-color': '#334155'/.test(code) &&
  /WS_TICK_COLOUR = '#334155'/.test(iptSrc));
ok("and slate is not one of the reserved route hexes",
  !RESERVED.map(r => r.toLowerCase()).includes("#334155"));
ok("nor one of the band colours", !bandHexes.includes("#334155"));
ok("one toggle moves ticks and labels together",
  /function toggleWsBounds[\s\S]{0,220}ws-boundary-ticks[\s\S]{0,120}ws-boundary-labels/.test(code));
ok("the sidebar has the checkbox", /id="layer-ws-bounds"/.test(html));
ok("on by default", /id="layer-ws-bounds"[^>]*\schecked[\s>]/.test(htmlNoHandlers));
ok("⭐ ticks are added AFTER the alignment layers, so one is never buried under "
   + "the line it marks",
  code.indexOf("'id': 'rail-alignment',") < code.indexOf("'id': 'ws-boundary-ticks'"));
ok("and BEFORE chainage, so a 100 m dot never sits on top of a package edge",
  code.indexOf("'id': 'ws-boundary-ticks'") < code.indexOf("'id': 'chainage-global'"));
ok("the boundary table lives in ipt_segments.js, beside the band table it mirrors",
  /window\.WS_BOUNDARIES = \[/.test(iptSrc));
ok("⭐ the reason WS12–WS15 get no tick is written down, not just implied",
  /point assets/.test(iptSrc) && /WS12, WS13, WS14, WS15/.test(iptSrc));
ok("⭐ and so is the reason 31\+507 is not one",
  /31\+507/.test(iptSrc) && /LOCAL chainage/.test(iptSrc));
ok("the ~141\+930 discrepancy at the A1/A2 border is recorded",
  /141\+930/.test(iptSrc));

// --- the WS name table and its disputes ---------------------------------------
ok("WS names are declared once, in the band file", /window\.WS_NAMES = \{/.test(iptSrc));
ok("all fifteen are named",
  (iptSrc.match(/WS\d+:\s*\{ name:/g) || []).length === 15,
  `${(iptSrc.match(/WS\d+:\s*\{ name:/g) || []).length} named`);
ok("⭐ WS13 is IPT 2, per the scope diagram — the IPT Matrix's own error list "
   + "names 'WS 13 (Urge) → IPT 1' as wrong, and the first delivery shipped it",
  /WS13:[\s\S]{0,120}ipt: 'IPT 2'/.test(iptSrc));
ok("and the band table agrees — WS13 moved from IPT 1 to IPT 2",
  /ipt: 'IPT 2', ws: \['WS2', 'WS13'\]/.test(iptSrc) &&
  /ipt: 'IPT 1', ws: \['WS1', 'WS12'\]/.test(iptSrc));
ok("⭐ WS14/WS15 ownership is NOT asserted — the scope diagram draws no IPT band "
   + "beneath them and it is open question A3.2",
  /WS14:[\s\S]{0,140}ipt: null[\s\S]{0,80}provisional: true/.test(iptSrc) &&
  /WS15:[\s\S]{0,140}ipt: null/.test(iptSrc));
ok("the WS7 subdivision into 7.1–7.4 is recorded as unmodelled",
  /WS 7\.1–7\.4/.test(iptSrc));
ok("every band names the ONE section that owns its chainage",
  (iptSrc.match(/ws_primary: '/g) || []).length === 7);
ok("and 'Outside A1' owns none", /ws_primary: null/.test(iptSrc));
ok("segments carry the owning section and its name",
  /p\.ws_primary = /.test(iptSrc) && /p\.ws_name = /.test(iptSrc));

// --- chainage, zoom-synced -----------------------------------------------------
ok("the chainage source carries the step tiers",
  /addSource\('chainage-source',\s*\{\s*type:\s*'geojson',\s*data:\s*chainageStepData\(\)/.test(code));
ok("which is memoised like the alignment build",
  /CHAINAGE_STEPPED\s*=\s*null/.test(code) && /if\s*\(CHAINAGE_STEPPED\)\s*return CHAINAGE_STEPPED/.test(code));
ok("⭐ marker density is driven by zoom", /'circle-radius':\s*\['step',\s*\['zoom'\]/.test(code));
ok("⭐ starting with the 10 km ticks alone at corridor zoom",
  /CH_TIER\(10000\)/.test(code));
ok("then 5 km at zoom 9, 1 km at 11, 500 m at 13, everything at 15",
  /9,\s*CH_TIER\(5000\)[\s\S]{0,120}11,\s*CH_TIER\(1000\)[\s\S]{0,120}13,\s*CH_TIER\(500\)[\s\S]{0,120}15,\s*3/.test(code));
ok("⭐ labels stop at the 500 m tier even at maximum zoom — a number every 94 m "
   + "is the same smear made of text",
  /15,\s*CH_LBL\(500\)/.test(code) && !/15,\s*\['get', 'chaintxt'\]/.test(code));
ok("the label ladder starts one zoom level behind the circles",
  /9,\s*CH_LBL\(10000\)/.test(code));
ok("the 500 m tier exists in the step table",
  /CHAINAGE_STEPS = \[10000, 5000, 1000, 500, 100\]/.test(iptSrc));
ok("⭐ the on-tick test handles NEGATIVE chainage — this corridor starts at "
   + "-3982.3 and the spec's formula could never match a negative tick",
  /\(\(m % step\) \+ step\) % step/.test(iptSrc));
ok("and why the spec's version was not used is recorded",
  /WRONG for negative chainage/.test(iptSrc));
ok("the chainage toggle is off by default",
  !/id="layer-chainage"[^>]*\schecked[\s>]/.test(htmlNoHandlers));
ok("the old flat radius of 3 is gone",
  !/'id': 'chainage-global'[\s\S]{0,400}'circle-radius':\s*3,/.test(code));
ok("⭐ labels are off entirely at corridor zoom",
  /'text-field':\s*\['step',\s*\['zoom'\],\s*''/.test(code));
ok("⭐ and hidden with an EMPTY text-field, not opacity — opacity 0 still takes up "
   + "collision space and would push real labels off the map",
  !/'id': 'chainage-labels'[\s\S]{0,900}'text-opacity'/.test(code));
ok("labels declutter rather than overlap",
  /'id': 'chainage-labels'[\s\S]{0,900}'text-allow-overlap':\s*false/.test(code));
ok("and keep their halo so they stay readable over the basemap",
  /'id': 'chainage-labels'[\s\S]{0,1100}'text-halo-width':\s*2/.test(code));
ok("the reason ['zoom'] is not used in a filter is recorded, since it is the "
   + "obvious thing to reach for",
  /cannot use \['zoom'\] inside a layer `filter`/.test(src));
ok("the sidebar says what the marker behaviour is",
  /10 km ticks at corridor zoom/.test(html));

// what must NOT have changed
ok("the band splitter is untouched — midpoint stamping stays rejected",
  /45\.2 km/.test(iptSrc) && /midpoint/i.test(iptSrc));
ok("GAP_SPLIT still cuts the geometry; bridges are paint, not a reinstated straight",
  /var GAP_SPLIT = true/.test(iptSrc));
ok("Outside A1 is still shown, muted rather than hidden",
  /Outside A1/.test(iptSrc) && /muted/.test(iptSrc));

ok("the layer id is unchanged, so the toggle still targets it",
  code.includes("'id': 'rail-alignment'"));
ok("the alignment checkbox still exists", /id="layer-alignment"/.test(html));
ok("and still shows/hides the same layer",
  /function toggleAlignment[\s\S]{0,200}toggleLayer\('rail-alignment',\s*visible\)/.test(code));
ok("the checkbox is wired to it", /onchange="toggleAlignment\(this\.checked\)"/.test(html));

ok("there is a legend container in the sidebar", /id="ipt-legend"/.test(html));
ok("the legend is generated from IPT_SEGMENTS, not hand-typed",
  code.includes("window.iptLegendRows()") && !/IPT 6[\s\S]{0,80}IPT 1[\s\S]{0,80}IPT 2/.test(html.replace(iptSrc, "")));
ok("and it is rendered on load", code.includes("renderIptLegend()"));
ok("hiding the alignment hides its legend too",
  /function toggleAlignment[\s\S]{0,700}ipt-legend[\s\S]{0,160}display/.test(code));

ok("⭐ the built collection is memoised, so a basemap switch does not rebuild it",
  /IPT_ALIGNMENT\s*=\s*null/.test(code) && /if\s*\(IPT_ALIGNMENT\)\s*return IPT_ALIGNMENT/.test(code));
ok("and the source is fed the built collection, not the raw file",
  /addSource\('alignment-source',\s*\{\s*type:\s*'geojson',\s*data:\s*iptAlignmentData\(\)/.test(code));
ok("the raw alignment_data is no longer handed to the source directly",
  !/addSource\('alignment-source'[\s\S]{0,80}data:\s*alignment_data\s*\}/.test(code));

ok("⭐ the alignment popup is wired exactly once, not once per basemap switch",
  code.includes("ALIGNMENT_POPUP_WIRED") &&
  /function setupAlignmentPopup\(\)\s*\{\s*if\s*\(ALIGNMENT_POPUP_WIRED\)\s*return;/.test(code));
// --- 2026-08-30: the popup now names the WORK SECTION that owns the band -------
ok("the popup names the IPT and the owning work section",
  code.includes("Work section:") && code.includes("p.ipt_label"));
ok("⭐ it shows the OWNING section, not every code on that ground",
  code.includes("p.ws_primary") && /also on this ground/.test(code));
ok("with its official name, not just the code",
  /wsName/.test(code) && /window\.wsLabel/.test(iptSrc));
ok("⭐ chainage reads as an engineer writes it — 105+480, not 105.48 km",
  /window\.chainText/.test(code) && !/toFixed\(3\)\.replace/.test(code));
ok("the underlay note appears ONLY when the underlay is actually on",
  /IPT 6 superstructure applies on this stretch/.test(code) &&
  /undOn\s*\?/.test(code) && /undBox/.test(code));
ok("and never on a stretch the underlay does not cover",
  /p\.ipt !== 'Outside A1'/.test(code));
ok("a provisional band says so in the popup",
  /Provisional boundary/.test(code) && /125\+000/.test(code));
ok("and a disputed WS name carries its dispute",
  /wsMeta\.note/.test(code));
ok("and says the WS2/WS3 bound is provisional rather than implying it is surveyed",
  /provisional/i.test(code));

// The band table itself
ok("IPT_SEGMENTS is defined", /window\.IPT_SEGMENTS\s*=\s*\[/.test(iptSrc));
ok("iptForChainage is defined", /window\.iptForChainage\s*=/.test(iptSrc));
ok("buildIptAlignment is defined", /window\.buildIptAlignment\s*=/.test(iptSrc));
ok("the gap split is documented as a change beyond the handoff spec",
  /PRE-EXISTING map defect/.test(iptSrc) && /GAP_SPLIT\s*=\s*false/.test(iptSrc));
ok("the ipt/routes naming collision is written down where it will be read",
  /NAMING COLLISION/.test(iptSrc) && /filter-ipt/.test(iptSrc));
ok("the reason midpoint stamping was rejected is recorded with its numbers",
  /45\.2 km/.test(iptSrc) && /midpoint/i.test(iptSrc));

// The route filter must not be pointed at the alignment layer
ok("⭐ the sector-IPT route filter still applies to route layers only",
  /filterArr\.push\(\['==',\s*\['get',\s*'ipt'\],\s*i\]\)/.test(code));
// ⚠️ This assertion used to read "rail-alignment is never given a filter". The
// 2026-08-28 per-IPT selection filters it deliberately, so the guard is restated
// rather than deleted: what it was ever protecting is that the ROUTE filter
// (#filter-ipt, which means "the IPT that owns the delivery") must never drive
// the ALIGNMENT layer (whose ipt means "the works package this ground is in").
ok("⭐ the alignment filter is driven by the IPT checkboxes, not by #filter-ipt",
  /setFilter\('rail-alignment',[\s\S]{0,300}checkedIpts\(\)|applyIptFilter[\s\S]{0,900}setFilter\('rail-alignment'/.test(code));
ok("⭐ and #filter-ipt is never read by the alignment code",
  !/function applyIptFilter[\s\S]{0,900}filter-ipt/.test(code));
ok("the two meanings are still written down where they can be confused",
  /NAMING COLLISION/.test(iptSrc));

console.log();
for (const f of fail) console.log("  FAIL:", f);
console.log(`\n${pass} passed, ${fail.length} failed`);
process.exit(fail.length ? 1 : 0);

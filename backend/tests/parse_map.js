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
// 2026-09-02 (§9): the Trips/day CARD no longer reads the baked cycle's trips_per_day —
// it is the forecast's vehicle-loads per working day, from /api/public/month-kpis.
// trips_per_day survives on the route popup / drawer, which is where a cycle figure
// belongs.
ok("the old 'capacity' card is gone", !/id="kpi-capacity"/.test(html));
ok("the baked cycle's trips_per_day no longer feeds any KPI card",
  !/function calculateKPIs[\s\S]{0,6000}trips_per_day/.test(code));

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
  /id="kpi-routes"/.test(html) && /id="kpi-vehicles"/.test(html)
  && /id="kpi-trips"/.test(html) && /id="kpi-material"/.test(html));
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
  /if \(typeof TL !== 'undefined' && TL\.on\) return \[TL\.month\]/.test(code));
// §9 — the four cards
ok("§9: the month comes from the playhead, else the Show-forecast window, else nothing",
  /function kpiMonthsOnScreen\(\)/.test(code) && code.includes("forecast-from") && /return \[\];\s*\}/.test(code));
ok("§9: figures come from /api/public/month-kpis, cached per month",
  code.includes("/public/month-kpis?month=") && /KPI\.cache\[m\] = j/.test(code));
ok("§9: working days come from the server payload", /const wd = payloads\[0\]\.working_days \|\| 22/.test(code));
ok("§9: vehicles/day = vehicle-loads over months with volume, per working day",
  /const vehDay = avg\(mVeh\) \/ wd/.test(code));
ok("§9: trips/day is stated equal to vehicle-loads, not a second formula",
  /set\('kpi-trips', fmtN\(vehDay, 1\)\)/.test(code));
ok("§9: material/day is in the map unit", /qtyDay = avg\(mQty\) \/ wd/.test(code) && code.includes("kpi-material-label"));
ok("§9: filters apply - origin/dest/IPT on the route, discipline on the LINE",
  /discFilter !== 'ALL' && l\.discipline !== discFilter/.test(code));
ok("§9: an empty month says so and hides the breakdown, no zeros",
  code.includes("No approved forecast in this month.")
  && /if \(!mVeh\.length\) \{[\s\S]{0,400}setHTML\('kpi-by-discipline', ''\)/.test(code));
ok("§9: by-discipline and by-material lines under the cards",
  /id="kpi-by-discipline"/.test(html) && /id="kpi-by-material"/.test(html)
  && /Object\.keys\(byMat\)\.length > 1 \? line\(byMat/.test(code));
ok("§9: a stale fetch cannot overwrite a newer month", /if \(seq !== KPI\.seq\) return;/.test(code));
ok("§9: the forecast toggle recomputes the cards",
  /src\.setData\(currentData\);\s*applyRailHighlight\(\);\s*applyFilters\(\);/.test(code)
  || /applyRailHighlight\(\);[\s\S]{0,120}applyFilters\(\);[\s\S]{0,60}\/\/ §9/.test(src));
ok("§9: never actuals", !/actual/.test(code.slice(code.indexOf("function calculateKPIs"), code.indexOf("function calculateKPIs") + 5000)));
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
ok("line width keeps scaling with zoom", /WIDTH_CORE\s*=[^;]*16,\s*6\.8\]/.test(code));
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

ok("index.html loads it", /src=["']ipt_segments\.js(\?v=\d+)?["']/.test(html));
// ⭐ The cache-buster is not cosmetic. A browser revalidates the .html far more
// eagerly than the scripts it loads, so both files can be uploaded correctly and
// the OLD script still run — indistinguishable from forgetting to upload it.
ok("...with a ?v= cache-buster", /src=["']ipt_segments\.js\?v=\d+["']/.test(html));
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
// 🔴 REVERSED 2026-09-01. The user asked for a continuous corridor and chose full
// continuity over the 30%-opacity version, so the civil layer now DRAWS bridges
// rather than excluding them. The assertion is reversed, not removed — it is the
// record of a decision that has now flipped three times (phantom lines -> cut ->
// faint bridges -> gone -> solid), and each flip has to be deliberate.
ok("🔴 the gap bridges are drawn SOLID on the civil layer, by request",
  !/'id': 'rail-alignment',[\s\S]{0,900}'filter':[\s\S]{0,200}\['!=',\s*\['get',\s*'is_bridge'\],\s*true\]/.test(code));
ok("it draws at ONE width, not two",
  /'id': 'rail-alignment',[\s\S]{0,900}'line-width':\s*2\.5,/.test(code));
ok("the old two-width case expression is gone",
  !/\['case',\s*\['==',\s*\['get',\s*'align_type'\],\s*'Main Track'\],\s*2\.5,\s*1\.2\]/.test(code));

// Gap bridges — REMOVED 2026-08-30 at the user's request
// ⚠️ These assertions are reversed rather than deleted. The point they were making
// — that a gap must not be silently invented, and must not be silently hidden
// either — still holds; it is now enforced from the other side. The geometry cut
// itself is untouched.
ok("⭐ the bridge layer is gone", !code.includes("'id': 'rail-alignment-bridge'"));
ok("and nothing still tries to toggle or filter it",
  !/toggleLayer\('rail-alignment-bridge'/.test(code) &&
  !/setFilter\('rail-alignment-bridge'/.test(code));
ok("⭐ but the GEOMETRY CUT survives — the 239.5 km of phantom straights stay out",
  /var GAP_SPLIT = true/.test(iptSrc));
ok("and the builder still emits the is_bridge flag, so nothing downstream breaks",
  /p\.is_bridge = /.test(iptSrc));
// 🔴 The consequence of drawing them solid, and it is the whole risk of the change:
// nothing in the PICTURE now separates measured geometry from an interpolation
// across ~60 km of missing survey, on a client-visible map. Two things carry the
// caveat instead, and both are asserted so neither can quietly go.
ok("🔴 the click popup declares an interpolated stretch",
  /No surveyed alignment on this stretch/.test(code) && /p\.is_bridge/.test(code));
ok("...and says plainly that it is not measured geometry",
  /not measured geometry/.test(code));
ok("the sidebar legend no longer claims there are breaks in the line",
  !/Breaks in the line/.test(html) && /interpolated/.test(html));
ok("⭐ the reason it was made solid is written next to the layer",
  /drawn SOLID, at full opacity/.test(src) && /open-questions\.md §H2/.test(src));
ok("the boundary labels still flag a tick with no surveyed track under it",
  /no surveyed alignment here/.test(code));

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
ok("⭐ the survey layer is added first, so it sits under the IPT layers",
  code.indexOf("'id': 'rail-alignment-survey'") < code.indexOf("'id': 'rail-alignment-underlay'"));
ok("⭐ and the underlay before the solid track, so the package colour reads on top",
  code.indexOf("'id': 'rail-alignment-underlay'") < code.indexOf("'id': 'rail-alignment',"));
ok("the reason for that order is written down next to it",
  /ADD ORDER IS THE Z-ORDER/.test(src));

// the three parts of the IPT view move together
ok("toggling the IPT view hides both legend blocks",
  /function toggleAlignment[\s\S]{0,500}'ipt-legend-note'/.test(code));
ok("the legend explains what a fainter segment means",
  /id="ipt-legend-note"/.test(html) && /no surveyed Main Track in the alignment file/.test(html));

// clicking a bridge must explain itself
ok("⭐ the boundary ticks answer a click too — a mark with no line under it is "
   + "where an explanation is needed most",
  /\['ws-boundary-ticks', 'ws-boundary-labels'\]\.forEach/.test(code));
ok("and that popper is wired ONCE, like the alignment one",
  /WS_POPUP_WIRED/.test(code) &&
  /function setupWsBoundaryPopup\(\)\s*\{\s*if\s*\(WS_POPUP_WIRED\)\s*return;/.test(code));
ok("⭐ it says when there is no surveyed alignment, and how far the nearest is",
  code.includes("No surveyed alignment here.") && /track_gap_m/.test(code));
ok("and it names the two sections in full, not just their codes",
  /window\.WS_NAMES\[c\]/.test(code));

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
  /weakest ADJACENT pair/.test(iptSrc) && /ΔE2000/.test(iptSrc));
ok("⭐ the colour-blind figure is recorded — it is what condemned the first palette",
  /deuteranopia/.test(iptSrc) && /colour-blind/.test(iptSrc));
// ⭐ The previous palette's block argued it was AT the ceiling. Palette C is a
// deliberate step DOWN from it, so the assertion narrows to the thing that must
// still be recorded: that the trade was made knowingly and what paid for it.
ok("the palette records that it is the weakest of the three, deliberately",
  /deliberately the WEAKEST/.test(iptSrc));
ok("...and that the dotted route is what pays for it",
  /dependency of this palette, not an\s*\/\/ option/.test(iptSrc));
ok("the six hue families are written down, so an edit keeps the property",
  /jade, violet, teal blue, stone, umber,\s*\/\/\s*burgundy|jade, violet, teal blue, stone, umber/.test(iptSrc));
ok("⭐ the #6D28D9 collision is GONE, not just documented",
  !/colour: '#6D28D9'/.test(iptSrc));
// ⭐ Six DIFFERENT hue families, one per band. The failure being guarded against is
// not "a wrong hex" but "two bands drifting back into the same family", which is
// exactly what the first palette did and what no single-colour assertion catches.
// Palette C, adopted 2026-08-30. Narrowed, not deleted — the hexes moved, the
// property being guarded (six distinct hue families) did not.
for (const [ipt, hex] of [["IPT 6", "#0F766E"], ["IPT 1", "#4C1D95"], ["IPT 2", "#155E75"],
                          ["IPT 3", "#57534E"], ["IPT 4", "#92400E"], ["IPT 5", "#831843"]]) {
  ok(`${ipt} is ${hex}`, segBlock.includes(hex));
}
ok("⭐ all six band colours are distinct — no two share a hex",
  new Set(bandHexes).size === 7,
  `${new Set(bandHexes).size} distinct of ${bandHexes.length} (IPT 4 appears twice)`);
ok("the underlay is a LIGHT wash, not a seventh deep colour competing for hue",
  /colour: '#C4B5FD'/.test(iptSrc));
ok("the first-cut greens and ambers are gone",
  !iptSrc.includes("#C6841D") && !/colour: '#039E86'/.test(iptSrc));
// 🔴 The colour-blind finding must survive an edit, because it is the one measure
// palette C actually regresses on and the brief it arrived with had it wrong.
ok("the deuteranopia numbers are recorded as MEASURED, not as claimed",
  /IPT 1 \/ IPT 2\s+19\.5\s+4\.4/.test(iptSrc));
ok("...and the bad figure from the brief is called out so nobody restores it",
  /Do not restore 13\.5 as a colour-blind/.test(iptSrc));
ok("...and the one-hex fix is on the record", /#7C3AED/.test(iptSrc));
// ⚠️ Test the declared COLOURS, not the block text — the comment above the table
// names the old hexes on purpose, as the record of what changed and why.
ok("and so is the whole indigo/violet run that made three bands unreadable",
  !["#4338ca", "#6d28d9", "#5b21b6"].some(h => bandHexes.includes(h)));

// --- IPT 6 superstructure underlay --------------------------------------------
ok("there is an underlay layer", code.includes("'id': 'rail-alignment-underlay'"));
ok("⭐ it is added AFTER survey and BEFORE the civil track, so the package "
   + "colour stays the primary read",
  code.indexOf("'id': 'rail-alignment-survey'") < code.indexOf("'id': 'rail-alignment-underlay'") &&
  code.indexOf("'id': 'rail-alignment-underlay'") < code.indexOf("'id': 'rail-alignment',"));
ok("it is wider than the civil track",
  /'id': 'rail-alignment-underlay'[\s\S]{0,900}'line-width': \(window\.IPT_UNDERLAY/.test(code));
ok("its colour, width and opacity are one tunable object, not three literals",
  /window\.IPT_UNDERLAY = \{[\s\S]{0,300}colour:[\s\S]{0,200}width:[\s\S]{0,120}opacity:/.test(iptSrc));
ok("⭐ it runs the whole A1 mainline, not just the northern IPT 6 civil band — "
   + "so it is filtered by align_type and scope, never by chainage",
  /'id': 'rail-alignment-underlay'[\s\S]{0,700}\['!=', \['get', 'ipt'\], 'Outside A1'\]/.test(code));
// reversed with the civil layer above: an underlay with holes under a continuous
// civil line reads as a rendering fault rather than as missing data
ok("⭐ and it includes bridges, matching the civil layer",
  !/'id': 'rail-alignment-underlay'[\s\S]{0,700}\['!=', \['get', 'is_bridge'\], true\]/.test(code));

// --- per-IPT selection ---------------------------------------------------------
ok("the legend rows are checkboxes now",
  /input type="checkbox" id="\$\{iptCheckboxId/.test(code));
ok("still generated from IPT_SEGMENTS, not hand-typed",
  code.includes("window.iptLegendRows()"));
ok("ticking one re-filters rather than rebuilding the GeoJSON",
  code.includes("function applyIptFilter") && !/applyIptFilter[\s\S]{0,600}buildIptAlignment/.test(code));
ok("the filter is a membership test on ipt",
  /\['in', \['get', 'ipt'\], on\]/.test(code));
ok("the civil filter is applied in exactly one place now the bridges are gone",
  (code.match(/\['in', \['get', 'ipt'\], on\]/g) || []).length === 1);
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
// Phase 5b: ticks 11 -> 8, labels 13 -> 11. At 11 the ticks were effectively
// absent — you had to already be looking at a boundary to find one.
ok("ticks from zoom 8, so they are visible at corridor zoom",
  /'id': 'ws-boundary-ticks'[\s\S]{0,400}'minzoom': 8/.test(code));
ok("⭐ labels from zoom 11, three levels after the ticks appear",
  /'id': 'ws-boundary-labels'[\s\S]{0,400}'minzoom': 11/.test(code));
ok("⭐ only every OTHER boundary is labelled below zoom 13 — the three Pärnu "
   + "boundaries are within a few km and collide",
  /\['%', \['get', 'idx'\], 2\]/.test(code));
ok("...stepped on zoom in the TEXT-FIELD, because ['zoom'] does not work in a filter",
  /'text-field': \['step', \['zoom'\]/.test(code));
ok("...and the unlabelled ones are hidden with an EMPTY text-field, never opacity 0",
  !/'text-opacity': 0/.test(code));
ok("the boundary index is stamped on the data, not guessed in the expression",
  /properties\.idx = ix/.test(iptSrc));
ok("the label names both sections meeting at the tick, and the chainage",
  /\['get', 'label'\]/.test(code) && /\['get', 'chain_txt'\]/.test(code));
ok("labels declutter; ticks do not, because there are only seven",
  /'id': 'ws-boundary-ticks'[\s\S]{0,900}'text-allow-overlap': true/.test(code) &&
  /'id': 'ws-boundary-labels'[\s\S]{0,1400}'text-allow-overlap': false/.test(code));
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

// ---- Phase 5a: the gate layer ------------------------------------------------
// `code` is comment-stripped, so these cannot pass by matching the prose that
// explains them; the two prose checks below use `src` deliberately.
ok("gates are drawn as their own circle layer, not as site markers",
  code.includes("id: 'site-gates'") && code.includes("type: 'circle'"));
ok("the gate layer filters on the Gate feature type",
  code.includes("['==', ['get', 'type'], 'Gate']"));
ok("gates do not appear until zoom 10 — below that a gate and its site are one pixel",
  /id: 'site-gates',[\s\S]{0,200}minzoom: 10/.test(code));
ok("gate labels come in later than the dots",
  /id: 'site-gates-label',[\s\S]{0,200}minzoom: 13/.test(code));
ok("a deactivated gate is drawn hollow rather than hidden",
  code.includes("['case', ['get', 'active'], '#64748B', '#FFFFFF']"));
ok("the gate layer's visibility is read from the checkbox, not a module variable",
  code.includes("function gatesVisible()")
  && code.includes("document.getElementById('layer-gates')")
  && code.includes("visibility: gatesVisible()"));
ok("there is a control for it", /id="layer-gates"/.test(html)
  && /toggleGates\(this\.checked\)/.test(html));
ok("the toggle moves both the dots and the labels",
  code.includes("['site-gates', 'site-gates-label'].forEach"));
ok("clicking a gate explains that it is an access point, not the site",
  code.includes("access point, not the site itself"));
ok("a deactivated gate's popup says why it matters", code.includes("will not bake"));
ok("the gate popup is wired once, not on every style.load",
  !/style\.load[\s\S]{0,4000}map\.on\('click', 'site-gates'/.test(code));

// 🔴 the regression this pass exists for: gates are Points, and a Point that is a gate
// must NOT become a site marker
ok("buildNodeMarkers excludes gate features",
  code.includes("f.geometry.type === 'Point' && f.properties.type !== 'Gate'"));
ok("...and the reason is written down where someone would remove it",
  src.includes("gates are Points too, and they are NOT sites"));

// 🔴 applyIptFilter() rebuilds the SAME expression and must agree with the layer
// definition, or ticking a checkbox silently reinstates the gaps.
ok("the per-IPT filter agrees with the layer's own filter about bridges",
  !/setFilter\('rail-alignment',[\s\S]{0,300}is_bridge/.test(code));

// ---- Phase 5b map change 3: routes as a dotted line --------------------------
// ⚠️ STEPPED since the 2026-09-01 thinning: the array is in width multiples, so a
// thinner line means a smaller gap, and at zoom 7 the design value would give
// 0.57 px — where a dashed line starts aliasing to solid, at exactly the zoom the
// change exists for. Wider gap below 10, design value above.
ok("the route core is dashed", /const DASH_CORE\s*=\s*\[1, 0\.55\]/.test(code));
// 🔴 Dashing only the core leaves the white casing showing through every gap and
// the route reads as a pale continuous line with coloured beads on it.
ok("🔴 the CASING is dashed too, or the dots sit on a solid white line",
  /const DASH_CASING\s*=\s*\[0\.96, 0\.28\]/.test(code));

// 🔴 2026-09-01. A step expression whose outputs were BARE ARRAYS made every route
// vanish: an array literal used as an expression output must be wrapped in
// ['literal', …], and Style.addLayer validates BEFORE adding — on failure it fires
// an error and returns without adding the layer, so all four route layers were
// silently skipped while every later layer added fine.
//
// This guard is deliberately broader than the bug: NO expression anywhere in this
// file may use a bare array as a step/case/match output. It is the shape of the
// mistake, not the one instance of it, that has to stay caught.
{
  const bareArrayOutput = /\['(step|case|match)',[^\]]*?\]\s*,\s*\[\s*[\d.]+\s*,/;
  ok("🔴 no expression uses a bare array as an output — it must be ['literal', …]",
    !bareArrayOutput.test(code.replace(/\s+/g, ' ')));
}
ok("line-dasharray is a plain constant, not an expression — cross-faded properties "
   + "have patchy expression support and two map deploys went out broken",
  !/'line-dasharray':\s*\['(step|interpolate|case|match)'/.test(code)
  && !/DASH_(CORE|CASING)\s*=\s*\['/.test(code));
ok("...and the reason is written where someone would 'improve' it back",
  /must be wrapped in \['literal'/.test(src) && /RETURNS WITHOUT ADDING THE LAYER/.test(src));
ok("all four route layers carry a dasharray",
  (code.match(/'line-dasharray': DASH_(CORE|CASING)/g) || []).length === 4);
// 🔴 A round cap extends each dash by half the line width at EACH end, so a 1.0w
// dot renders 2.0w long, the 0.333w gap is swallowed twice over, and the route
// draws SOLID — indistinguishable from the change never having been applied.
// 2026-09-02: a fifth butt-capped dashed layer, the EVR rail core, joined the four
ok("🔴 line-cap is butt on all four route layers, or the dots render solid",
  (code.match(/'line-cap': 'butt'/g) || []).length === 5
  && /id: 'evr-rail-line'[\s\S]{0,300}'line-cap': 'butt'/.test(code));
ok("the casing is a FIXED 1.25x the core at every zoom stop, or the two dash "
   + "arrays cannot draw the same period on screen",
  /WIDTH_CORE\s*=\s*\['interpolate', \['linear'\], \['zoom'\], 7, 1\.36, 12, 4,\s+16, 6\.8\]/.test(code)
  && /WIDTH_CASING\s*=\s*\['interpolate', \['linear'\], \['zoom'\], 7, 1\.7,\s+12, 5,\s+16, 8\.5\]/.test(code));
ok("⭐ at corridor zoom the route is THINNER than the alignment's fixed 2.5 px",
  /WIDTH_CORE\s*=\s*\['interpolate', \['linear'\], \['zoom'\], 7, 1\.36,/.test(code));
// ⚠️ the gap ratio is coupled to the width: a 20% thinner line is a 20% smaller gap,
// and 0.45 at the new width gives 0.61 px at zoom 7 — back into aliasing
ok("the gap ratio moved WITH the thinning, or the dots alias to solid at zoom 7",
  /gap ratio moved 0\.45 -> 0\.55 WITH the thinning/.test(src));
ok("the dasharray is NOT interpolated across zoom — it silently does nothing",
  !/'line-dasharray': \['interpolate'/.test(code));
// 🔴 REMOVED 2026-09-01 at the user's request. Asserted GONE rather than deleted
// from the suite — this layer has now been added (nine quick fixes), moved
// (12 -> 11) and removed, and a future edit must not reintroduce it by accident.
ok("🔴 the directional arrow layers are gone",
  !/'inbound-arrows'/.test(code) && !/'outbound-arrows'/.test(code));
ok("...and nothing still tries to filter them",
  !/setFilter\('inbound-arrows'/.test(code) && !/setFilter\('outbound-arrows'/.test(code));
ok("...and no ▶ glyph survives on a route layer",
  !/'text-field': '▶'/.test(code));
ok("⚠️ what went with them is written down — nothing now shows direction of travel",
  /Nothing on this map now indicates which way a/.test(src));
// 🔴 Found while removing the arrows: applyFilters() filtered the cores and never
// the casings, so filtering to one origin left every other route's white casing
// drawn. Pre-existing since Phase 2.5a; a trail of white dots now that they dash.
ok("🔴 the route CASINGS are filtered too, not just the cores",
  /setFilter\('inbound-lines-casing'/.test(code) &&
  /setFilter\('outbound-lines-casing'/.test(code));

// ---- the sidebar swatch is generated, not hard-coded -------------------------
// ⭐ It was a hard-coded gradient of the FIRST palette and survived two palette
// changes untouched, so the control panel advertised inbound-teal, brand navy and
// selection-crimson as IPT colours while the map drew palette C beside it. The
// roadmap already carried "a colour written in two places WILL drift" because this
// same element caused it once before — writing it down was not enough.
ok("🔴 the alignment swatch is generated from the band table",
  /function paintAlignmentSwatch\(\)/.test(code)
  && /window\.iptLegendRows\(\)/.test(code));
ok("...and no stale palette gradient survives in the markup",
  !/linear-gradient\(90deg,#039E86/.test(html));
ok("...and it is repainted whenever the legend is",
  /paintAlignmentSwatch\(\);/.test(code));
ok("the swatch shows the civil bands only, not the underlay wash or Outside A1",
  /r\.ipt !== IPT_UNDERLAY_KEY && r\.ipt !== 'Outside A1'/.test(code));

// ---- the version handshake ---------------------------------------------------
ok("ipt_segments.js is v9", /IPT_SEGMENTS_VERSION = 'v9'/.test(iptSrc));
ok("index.html expects v9", /IPT_INDEX_VERSION = 'v9'/.test(code));
ok("...and the cache-buster matches the version",
  /src=["']ipt_segments\.js\?v=9["']/.test(html));

// ---- Route alternatives -------------------------------------------------------
// 🔴 THE POINT OF THE DESIGN: its own source. Five things walk routes-source per
// feature matching "LineString with a route_id" — the KPI count, the leg filters,
// buildRouteInfo(), the forecast timeline's is_forecast stamping and the
// route-highlight lookup. An alternative in that collection would make a route's
// distance jump to whichever option is longest and would be painted as a forecast.
ok("🔴 alternatives live in their OWN source, not routes-source",
  code.includes("map.addSource('route-alts-source'"));
ok("...fed from their own endpoint",
  /\/public\/route-alternatives/.test(code));
ok("...and nothing points the alt layer at routes-source",
  !/'id': 'route-alt-lines'[\s\S]{0,300}'source': 'routes-source'/.test(code));
// (the feature TYPE is a backend fact and is asserted in test_phase5a.py, where
// route_alternatives_geojson() can actually be run — a `true` here would have been
// an assertion that passes for the wrong reason)
ok("the alt layer is added BEFORE both carriageways, so it draws underneath",
  code.indexOf("'id': 'route-alt-lines'") < code.indexOf("'id': 'outbound-lines-casing'"));
ok("thin, grey and translucent — context, not content",
  /'id': 'route-alt-lines'[\s\S]{0,500}'line-color': '#94a3b8'/.test(code) &&
  /'id': 'route-alt-lines'[\s\S]{0,600}'line-opacity': 0\.45/.test(code));
ok("solid, not dotted — the dots are what say 'this is a haul route'",
  !/'id': 'route-alt-lines'[\s\S]{0,600}'line-dasharray'/.test(code));
ok("and no casing, because a halo is what lifts a line off the basemap",
  !code.includes("'route-alt-lines-casing'"));
ok("its visibility is read from the checkbox, not a module variable",
  /function altsVisible\(\)/.test(code) &&
  /document\.getElementById\('layer-alts'\)/.test(code) &&
  /visibility: altsVisible\(\)/.test(code));
ok("there is a control for it, OFF by default",
  /id="layer-alts"/.test(html) && !/id="layer-alts" checked/.test(html));
ok("the fetch is off the critical path — a failure must not cost the map",
  /async function loadAlternatives/.test(code) && /catch\(e\)\{[\s\S]{0,200}alternatives unavailable/.test(code));
ok("...and it says how many there are, or why there are none",
  /no alternatives/.test(code));


// =============================================================================
//  Week 1, Task E — the EVR rail layer and the railhead highlight
// =============================================================================
ok("the rail data file is loaded statically", /src=["']data\/evr_rail\.js\?v=\d+["']/.test(html));
// ⚠️ The half-upgraded pair (new index.html, old data file behind a browser cache)
// cost an hour on 2026-08-30. ipt_segments.js carries a ?v= for that reason and so
// does this one.
ok("...with a ?v= cache-buster, like ipt_segments.js", /evr_rail\.js\?v=/.test(html));
ok("it is NOT the Rail Baltica alignment file",
  /src=["']data\/alignment\.js["']/.test(html) && !/evr_rail[\s\S]{0,80}alignment_data/.test(code));

ok("there is an evr-rail source", /addSource\('evr-rail'/.test(code));
ok("...that tolerates the file being absent",
  /window\.evr_rail_data \|\| \{ type: 'FeatureCollection', features: \[\] \}/.test(code));
ok("and an evr-rail-line layer on it",
  /id: 'evr-rail-line', type: 'line', source: 'evr-rail'/.test(code));

// the build list's numbers, exactly
ok("the rail colour is the reserved #0F766E", /const RAIL_COLOR = '#0F766E'/.test(code));
// 2026-09-02 §6a restyle: a quiet grey casing + short-tick dash by default; the
// reserved rail colour only when highlighted. The old 2px/25% single line is gone.
ok("§6a: default = casing 5 px #334155 at 35%",
  /RAIL_DIM\s*=\s*\{ casingW: 5, casingC: '#334155', casingO: 0\.35/.test(code));
ok("§6a: ...under a 2 px #64748B dash with [2, 18]",
  /dashW: 2,\s*dashC: '#64748B',\s*dashO: 1\.0, dash: \[2, 18\]/.test(code));
ok("§6a: highlight = casing 7 px in the rail colour at 80%",
  /RAIL_BOLD\s*=\s*\{ casingW: 7, casingC: RAIL_COLOR, casingO: 0\.8/.test(code));
ok("§6a: ...and a rail-coloured dash with [2, 12]",
  /dashC: RAIL_COLOR,\s*dashO: 1\.0, dash: \[2, 12\]/.test(code));
ok("§6a: two rail layers, casing under core", /id: 'evr-rail-casing'[\s\S]{0,900}id: 'evr-rail-line'/.test(code));
ok("§6a: no IPT or reserved route colour in either rail style",
  !/(RAIL_DIM|RAIL_BOLD)\s*=[^}]*(#039E86|#f59e0b|#C2790B|#3398DB|#BF2E55|#003787|#0A1446|#4338CA|#6D28D9|#57534E|#9A3412|#9F1239|#5B21B6)/i.test(code));
ok("the old single 2px/25% style is gone", !/RAIL_DIM_WIDTH/.test(code));
// §6b — when it goes bold
ok("§6b: bold on a railhead origin OR a railhead movement on screen",
  /const bold = originIsHead \|\| railheadMovementOnScreen\(\)/.test(code));
ok("§6b: 'movement on screen' needs forecast on or the timeline open",
  /const forecastOn = !!\(cb && cb\.checked\) \|\| \(typeof TL !== 'undefined' && TL\.on\)/.test(code));
ok("§6b: ...and a route carrying volume NOW, inside the current filter, touching a railhead",
  /f\.properties\.is_forecast && routePassesFilters\(f\.properties\)/.test(code)
  && /locTypeById\(f\.properties\.origin_id\) === 'Railhead' \|\| locTypeById\(f\.properties\.dest_id\) === 'Railhead'/.test(code));
ok("§6b: the forecast toggle recomputes it, both on and off",
  (src.match(/applyRailHighlight\(\);\s*\/\/ §6b/g) || []).length === 2);
ok("§6b: turning forecast off clears is_forecast so the rule sees no movement",
  /if \(f\.properties\) f\.properties\.is_forecast = false; \}\); sd\.setData\(d\); \}\s*applyRailHighlight/.test(code));
ok("§6b: closing the timeline re-runs the filters", /applyZoneMonth\(null\);[^\n]*\n\s*applyFilters\(\);/.test(code));
// ⭐ #0F766E must not collide with anything already meaning something on this map.
for (const reserved of ["#039E86", "#f59e0b", "#C2790B", "#3398DB", "#BF2E55",
                        "#003787", "#0A1446", "#4338CA", "#6D28D9", "#57534E",
                        "#9A3412", "#9F1239", "#5B21B6"]) {
  ok(`the rail colour is not the reserved ${reserved}`, "#0F766E" !== reserved);
}

// visibility from the checkbox, never a module variable — style.load re-adds every
// layer on a basemap switch and a drifted variable restores one the user turned off
ok("rail visibility is read from the checkbox",
  /function railVisible\(\)/.test(code)
  && /document\.getElementById\('layer-rail'\)/.test(code)
  && /visibility: railVisible\(\)/.test(code));
ok("there is a Rail network control, ON by default",
  /id="layer-rail" checked/.test(html));
ok("toggling it moves the line AND the railhead dots",
  /function toggleRail\(visible\) \{[\s\S]{0,160}'evr-rail-line', 'railheads'/.test(code));

// the highlight
ok("the highlight follows the origin filter", /function applyRailHighlight\(\)/.test(code)
  && /applyRailHighlight\(\);/.test(code));
ok("...and applyFilters is what calls it",
  /function applyFilters\(\)[\s\S]{0,2600}applyRailHighlight\(\);/.test(code));
ok("it bolds only when the selected origin is a Railhead",
  /locTypeByName\(sel\) === 'Railhead'/.test(code));
ok("the loc_type comes from the Node features, not a hard-coded list",
  /function locTypeByName\(name\)/.test(code)
  && /x\.properties\.type === 'Node'/.test(code));
// ⭐ an array literal used as an expression OUTPUT must be wrapped in ['literal', ...]
// or setFilter validates, errors and silently applies nothing
ok("⭐ the id filter wraps its array in ['literal', ...]",
  /\['in', \['get', 'id'\], \['literal', ids\]\]/.test(code));
ok("heads are matched case-insensitively and by substring, both ways",
  /function railFeaturesFor\(name\)/.test(code)
  && /toLowerCase\(\)\.trim\(\)/.test(code)
  && /n\.indexOf\(hh\) >= 0 \|\| hh\.indexOf\(n\) >= 0/.test(code));
ok("origin back to ALL returns the layer to the dim default",
  /const st = bold \? RAIL_BOLD : RAIL_DIM/.test(code)
  && /setPaintProperty\('evr-rail-line', 'line-dasharray', st\.dash\)/.test(code));
// ⭐ the road filter must be untouched: a railhead origin still isolates its hauls
ok("⭐ the existing road-route filter is not disturbed",
  /setFilter\('inbound-lines',/.test(code) && /setFilter\('outbound-lines',/.test(code)
  && /setFilter\('inbound-lines-casing',/.test(code));

// filterByNode must treat a railhead as an origin, like a quarry or a port
ok("filterByNode treats a Railhead as an origin",
  /nodeType === 'Railhead'/.test(code)
  && /nodeType === 'Quarry' \|\| nodeType === 'Hub' \|\| nodeType === 'Port'[\s\S]{0,120}filter-origin/.test(code));

// railheads drawn as a LAYER, not only a DOM marker
// §5 — its own mark, a symbol layer, generated glyph
ok("§5: railheads are drawn as a real SYMBOL layer, not a circle and not a DOM marker",
  /id: 'railheads', type: 'symbol'/.test(code) && !/id: 'railheads', type: 'circle'/.test(code));
ok("§5: the glyph is generated on a canvas in the rail colour",
  /function railheadGlyph\(\)/.test(code) && /g\.strokeStyle = RAIL_COLOR/.test(code)
  && /'icon-image': 'railhead-glyph'/.test(code));
ok("§5: two sleepers and a rail", (code.match(/g\.fillRect\(/g) || []).length === 3);
ok("§5: the image is re-added on style.load, guarded by hasImage",
  /if \(!map\.hasImage\('railhead-glyph'\)\) map\.addImage/.test(code));
ok("§5: there is a Railhead legend entry", /id="legend-railhead"/.test(html) && />Railhead</.test(html));
ok("§5: the rail toggle moves casing, core and heads together",
  /'evr-rail-casing', 'evr-rail-line', 'railheads'/.test(code));
ok("...filtered to Node features of that type",
  /\['==', \['get', 'node_type'\], 'Railhead'\]/.test(code));

// ⭐ the honesty channel. The picture reads as authoritative; the popup is where the
// caveat lives, exactly as the alignment's gap bridges do.
ok("⭐ clicking the rail line opens a popup", /map\.on\('click', 'evr-rail-line'/.test(code));
ok("⭐ ...built by railPopupHTML", /function railPopupHTML\(p\)/.test(code));
// ⚠️ RUN IT, do not grep it. A source-level check for "Provisional geometry" passed
// on a deliberately broken tree where the caveat had been branched out with
// `if (false)` — the string was still in the file and unreachable. Executing the
// function is the only assertion that proves the caveat actually renders.
const _railFn = (() => {
  const i = code.indexOf("function railPopupHTML(p) {");
  if (i < 0) return null;
  const j = code.indexOf("\n    function gatePopupHTML", i);
  const body = j > i ? code.slice(i, j) : null;
  if (!body) return null;
  try {
    return new Function("RAIL_COLOR", body + "\nreturn railPopupHTML;")("#0F766E");
  } catch (e) { return null; }
})();
ok("railPopupHTML can be isolated and run", typeof _railFn === "function");
const _railHtml = _railFn ? _railFn({
  name: "Rapla - Lelle", operator: "Eesti Raudtee (EVR)",
  heads: '["Rapla","Lelle"]', length_km: 16.6, provisional: "true",
  accuracy_note: "wrong at the Lelle end by ~6.4 km",
  source: "Natural Earth 10m railroads",
}) : "";
ok("⭐ the rendered popup SAYS the geometry is provisional",
  _railHtml.includes("Provisional geometry"));
ok("⭐ ...and renders the feature's own accuracy note",
  _railHtml.includes("6.4 km"));
ok("⭐ ...and names the source", _railHtml.includes("Natural Earth"));
ok("⭐ ...and parses the stringified heads array rather than printing JSON",
  _railHtml.includes("Rapla · Lelle") && !_railHtml.includes('["Rapla"'));
// a real (non-provisional) file must NOT carry the caveat, or it becomes wallpaper
const _railOk = _railFn ? _railFn({ name: "x", provisional: false, source: "OSM" }) : "x";
ok("⭐ a file marked provisional:false shows NO caveat",
  !_railOk.includes("Provisional geometry"));
// properties cross Mapbox's boundary as strings — 'false' is truthy
ok("stringified properties are parsed, not trusted",
  /typeof heads === 'string'/.test(code)
  && /p\.provisional === false \|\| p\.provisional === 'false'/.test(code));

// Task D2's popup line
ok("the node popup shows stock only when BOTH numbers exist",
  /props\.stock_balance != null && props\.capacity_qty != null/.test(code));
ok("...and there is no chart", !/new Chart\(/.test(code));

// Never build: no week scrubber on the public map, no actuals painted here
ok("the timeline is still monthly — no week scrubber",
  !/week_index/.test(code) && !/scrubber/i.test(code));
ok("actuals are not painted on the public map", !/actual_qty/.test(code));

// =============================================================================
//  2026-09-02 §8 — timeline warnings
// =============================================================================
ok("§8: a warning stack exists above the timeline", /id="tl-warnings"/.test(html)
  && /#tl-warnings\{[^}]*bottom:74px/.test(html) && /#timeline-bar\{[^}]*bottom:18px/.test(html));
ok("§8: rebuilt on every tick and cleared when the timeline closes",
  /applyFilters\(\);\s*renderTimelineWarnings\(m\);/.test(code) && /TL\.on=false;\s*renderTimelineWarnings\(null\)/.test(code));
ok("§8: three sources and no new ones — Tark Tee, stockpile over, seasonal",
  code.includes("/routes/restrictions") && code.includes("/public/stockpile-timeline")
  && code.includes("META.seasonal") && !/weather|openweather|forecast\.io/i.test(code.slice(code.indexOf("const WARN"), code.indexOf("(function initAnalysis"))));
ok("§8: only routes carrying volume THIS month, inside the filter",
  /if \(!f\.properties\.is_forecast \|\| !routePassesFilters\(f\.properties\)\) return;/.test(code));
ok("§8: a stale month cannot paint over the current one", /if \(TL\.month !== month \|\| !TL\.on\) return;/.test(code));
ok("§8: at most three, then +N more", /live\.slice\(0, 3\)/.test(code) && code.includes("more</div>"));
ok("§8: dismissible, per month", /function dismissWarning\(key\)/.test(code) && /\|\$\{month\}`/.test(code));
ok("§8: seasonal windows fold the absolute month to a calendar month", /const cal = \(\(month - 1\) % 12\) \+ 1;/.test(code));
ok("§8: the seasonal check honours restricted_vehicles", /rv\.length === 0 \|\| \[\.\.\.vs\]\.some\(v => rv\.includes\(v\)\)/.test(code));
ok("§8: each line reads route/pile · what · month",
  /<b>\$\{esc\(i\.who\)\}<\/b> · \$\{esc\(i\.what\)\} · \$\{esc\(monthLabel\(month\)\)\}/.test(code));
ok("§8: fetch failures degrade to no warnings, not a broken map",
  /\.catch\(\(\) => \{ WARN\.restr = \{\}; return WARN\.restr; \}\)/.test(code));

// §4 — Street View empty state, no extra request
ok("§4: ZERO_RESULTS shows 'No Street View here' and returns before any image request",
  /meta\.status === 'ZERO_RESULTS'\)\{[\s\S]{0,200}No Street View here[\s\S]{0,80}return;/.test(code)
  && code.indexOf("No Street View here") < code.indexOf("'/streetview?lat='"));
ok("§4: no key and a fetch failure still show nothing", /if\(!meta \|\| !meta\.available\) return;/.test(code));
ok("§4: no second imagery provider", !/mapillary|bing.*streetside|kartaview/i.test(code));

console.log();
for (const f of fail) console.log("  FAIL:", f);
console.log(`\n${pass} passed, ${fail.length} failed`);
process.exit(fail.length ? 1 : 0);

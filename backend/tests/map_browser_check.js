/*
 * 2026-09-07 — the PUBLIC MAP in a real browser, with the real Mapbox GL JS.
 *
 * ⚠️ NOT PART OF THE DEFAULT SUITE, AND IT CANNOT BE.
 * -------------------------------------------------
 * map/index.html loads Mapbox GL JS and Inter from CDNs the sandbox blocks, so this
 * substitutes a local copy and needs it installed first:
 *
 *     mkdir -p /tmp/shot && cd /tmp/shot
 *     npm i mapbox-gl@2.14.1
 *     node <repo>/backend/tests/map_browser_check.js
 *
 * WHY IT EXISTS
 * -------------
 * parse_map.js is 439 source-level assertions and it cannot see a single pixel. The
 * 2026-09-07 pass is almost entirely about WHERE things are and WHETHER they appear:
 * a KPI panel that moved corner, chips that have to fit, callouts that are DOM popups,
 * and a phone layout where two absolutely-positioned blocks used to overlap. None of
 * that has a textual signature. This runs the page and looks.
 *
 * HOW THE MAPBOX PART WORKS
 * -------------------------
 * The style URL is intercepted and answered with a minimal style: version 8, one
 * background layer, no sources, no sprite and no glyphs. That means:
 *   ✅ the real GL JS runs, layers really are added, filters really are validated
 *      (which is what caught four route layers vanishing on 2026-09-01), and
 *      mapboxgl.Popup really renders — so the callouts are real DOM;
 *   ❌ no basemap tiles, and NO GLYPHS, so every symbol layer with a text-field errors.
 *      Those errors are counted and reported separately, not swallowed — they are the
 *      price of running offline, not a finding about the page.
 *
 * ⚠️ WHAT THIS STILL DOES NOT PROVE: the basemap is blank, so nothing about legibility
 * over real map tiles is tested; the API is fixtures, not the backend; and the route
 * geometry is four short invented lines, not the 107-route network.
 */
const fs = require("fs");
const http = require("http");
const path = require("path");
const G = "/home/claude/.npm-global/lib/node_modules/";
const { chromium } = require(G + "playwright");

const ROOT = path.resolve(__dirname, "..", "..");
const MAPDIR = path.join(ROOT, "map");
const GL = "/tmp/shot/node_modules/mapbox-gl/dist/";
const PORT = 8942;
const OUT = process.env.SHOT_DIR || "/tmp/shot";

// ---- fixtures -----------------------------------------------------------------
// Two quarries, a stockpile and a railhead, four routes between them. Small on purpose:
// the cap at six callouts and the chip row are both about crowding, and a fixture with
// one route would exercise neither.
const NODES = [
  ["N1", "Kuusiku quarry", "Quarry", 24.62, 58.99, "Aggregate"],
  ["N2", "Rapla railhead", "Railhead", 24.79, 59.01, "Aggregate"],
  ["N3", "Tootsi cut", "Site", 24.81, 58.58, "Fill"],
  ["N4", "Pärnu stockpile", "Stockpile", 24.50, 58.39, "Fill"],
  ["N5", "Lelle compound", "Compound", 24.82, 58.77, "Fill"],
];
const ROUTES = [
  ["R-001", "Kuusiku quarry", "Rapla railhead", "N1", "N2", "IPT3", ["Earthworks"]],
  ["R-002", "Pärnu stockpile", "Tootsi cut", "N4", "N3", "IPT2", ["Track", "Earthworks"]],
  ["R-003", "Kuusiku quarry", "Tootsi cut", "N1", "N3", "IPT3", ["Earthworks"]],
  ["R-004", "Rapla railhead", "Lelle compound", "N2", "N5", "IPT6", ["Track"]],
];
const byName = Object.fromEntries(NODES.map(n => [n[1], n]));
function mapData() {
  const features = [];
  NODES.forEach(([id, name, node_type, lon, lat, material]) => features.push({
    type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] },
    properties: { type: "Node", id, name, node_type, material, ipt: "IPT3" },
  }));
  ROUTES.forEach(([route_id, origin, dest, origin_id, dest_id, ipt, disciplines]) => {
    const a = byName[origin], b = byName[dest];
    [["Inbound Highway", 0], ["Outbound Highway", 0.01]].forEach(([type, dx]) => features.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[a[3] + dx, a[4]], [(a[3] + b[3]) / 2 + dx, (a[4] + b[4]) / 2], [b[3] + dx, b[4]]] },
      properties: { type, route_id, origin, dest, origin_id, dest_id, ipt, disciplines, distance_km: 31.2 },
    }));
  });
  return { type: "FeatureCollection", features };
}
// one line per route per month, so a month has volume, disciplines and two materials
function monthKpis(m) {
  const lines = ROUTES.map(([route_id, , , , , , disc], i) => ({
    route_id, discipline: disc[0], material_type: i % 2 ? "Fill" : "Aggregate",
    vehicle_loads: 120 + i * 30 + m * 4, qty_unit: 120 + i * 30 + m * 4,
    trips_per_vehicle_day: i === 3 ? 0 : 4 - (i % 2),   // R-004 unbaked on purpose
    payload_fallback: false,
  }));
  return { month: m, unit: "vehicles", working_days: 21, lines, payload_fallback_vehicle: "V07" };
}
function forecastMatrix() {
  return {
    unit: "vehicles",
    routes: ROUTES.map(([route_id], i) => ({
      route_id,
      monthly: Object.fromEntries(Array.from({ length: 60 }, (_, k) => [k + 1, (k + i) % 5 === 0 ? 0 : 90 + i * 20 + k]))
    })),
  };
}
function api(u) {
  const p = u.replace(/^.*\/api/, "").split("?")[0];
  const q = u.includes("?") ? new URLSearchParams(u.split("?")[1]) : new URLSearchParams();
  if (p === "/public/map-data") return mapData();
  if (p === "/public/route-alternatives") return { type: "FeatureCollection", features: [] };
  if (p === "/public/month-kpis") return monthKpis(parseInt(q.get("month") || "1"));
  if (p === "/public/forecast-matrix") return forecastMatrix();
  if (p === "/public/route-forecasts") return { routes: [] };
  if (p === "/public/stockpile-timeline") return { piles: [] };
  if (p === "/routes/restrictions") return { routes: [] };
  if (p === "/restrictions/layers") return { layers: [] };
  if (p === "/restrictions") return { type: "FeatureCollection", features: [] };
  if (p === "/zones") return [];
  if (p === "/meta") return { months: { start_year: 2026, count: 60 }, seasonal_restrictions: [] };
  return {};
}

const STYLE = {
  version: 8, name: "offline-stub", sources: {}, layers: [
    { id: "bg", type: "background", paint: { "background-color": "#eef1f5" } },
  ],
};

const server = http.createServer((req, res) => {
  const p = decodeURIComponent(req.url.split("?")[0]);
  const f = path.join(MAPDIR, p === "/" ? "index.html" : p.replace(/^\/map\/?/, ""));
  try {
    const body = fs.readFileSync(f);
    res.writeHead(200, { "Content-Type": p.endsWith(".js") ? "application/javascript"
      : p.endsWith(".css") ? "text/css" : "text/html" });
    res.end(body);
  } catch (e) { res.writeHead(404); res.end("no"); }
});

let pass = 0; const fail = [];
function ok(label, cond, extra) { if (cond) pass++; else fail.push(label + (extra ? "  " + extra : "")); }

(async () => {
  await new Promise(r => server.listen(PORT, r));
  const b = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
  const page = await b.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [], glyphNoise = [];
  const noteErr = (t) => (/glyphs|Failed to load resource|sprite|NetworkError|text-field/i.test(t)
    ? glyphNoise : errors).push(t);
  page.on("pageerror", e => noteErr("pageerror: " + e.message));
  page.on("console", m => { if (m.type() === "error") noteErr("console: " + m.text().slice(0, 180)); });

  await page.route("**/*", async (route) => {
    const url = route.request().url();
    if (url.includes("/api/")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(api(url)) });
    if (url.includes("mapbox-gl.js")) return route.fulfill({ status: 200, contentType: "application/javascript", body: fs.readFileSync(GL + "mapbox-gl.js", "utf8") });
    if (url.includes("mapbox-gl.css")) return route.fulfill({ status: 200, contentType: "text/css", body: fs.readFileSync(GL + "mapbox-gl.css", "utf8") });
    if (url.includes("/styles/v1/")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(STYLE) });
    if (url.startsWith("http://localhost:" + PORT)) return route.continue();
    return route.fulfill({ status: 200, contentType: "text/css", body: "" });   // fonts, tiles, sprites
  });

  await page.goto(`http://localhost:${PORT}/index.html`);
  await page.waitForTimeout(2500);

  const shot = (n) => page.screenshot({ path: path.join(OUT, `map-${n}.png`) });
  const vis = (sel) => page.evaluate(s => {
    const el = document.querySelector(s); if (!el) return null;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
             display: st.display, visible: r.width > 0 && r.height > 0 && st.display !== "none" };
  }, sel);

  ok("the map page loads and builds its Mapbox instance",
    await page.evaluate(() => typeof map !== "undefined" && !!map.getStyle()));
  await shot("01-loaded");

  // ---- the KPI panel is where it should be -------------------------------------
  let k = await vis("#kpi-hud");
  ok("the KPI panel is on screen", !!k && k.visible, JSON.stringify(k));
  ok("⭐ top-left of the map pane, clear of the sidebar", k && k.x >= 300 && k.x < 360 && k.y < 40,
    JSON.stringify(k));
  // the zoom / compass column must be left alone: it lives top-right
  const nav = await vis(".mapboxgl-ctrl-top-right");
  ok("⭐ it does not overlap the zoom / compass stack",
    !!nav && !!k && (k.x + k.w) < nav.x, JSON.stringify({ k, nav }));

  // ---- open the timeline and read a month --------------------------------------
  await page.evaluate(() => openTimeline());
  await page.waitForTimeout(1800);
  await page.evaluate(() => setTimelineMonth(7));
  await page.waitForTimeout(1500);
  await shot("02-timeline-month");

  const title = await page.evaluate(() => document.getElementById("kpi-title").textContent);
  ok("the panel titles the month on the playhead", /what is moving/.test(title), title);
  const chips = await page.evaluate(() => Array.from(document.querySelectorAll("#kpi-by-discipline .kpi-chip")).map(e => e.textContent));
  ok("⭐ the disciplines of THAT month render as chips", chips.length >= 2, JSON.stringify(chips));
  ok("...carrying a per-day figure, not just a name", chips.every(c => /[\d.]/.test(c)), JSON.stringify(chips));
  const matChips = await page.evaluate(() => document.querySelectorAll("#kpi-by-material .kpi-chip").length);
  ok("material chips appear when the month mixes materials", matChips >= 2, String(matChips));
  const vehicles = await page.evaluate(() => document.getElementById("kpi-vehicles").textContent);
  ok("the vehicles card is a fleet size, not the movement count", /\d/.test(vehicles), vehicles);
  // the fixture leaves R-004 with no cycle time on purpose
  const vehLabel = await page.evaluate(() => document.getElementById("kpi-vehicles-label").textContent);
  ok("⭐ an unbaked line is declared, not counted as zero", /unbaked/.test(vehLabel), vehLabel);

  // the panel must not have grown into the map
  k = await vis("#kpi-hud");
  ok("the panel is still a panel, not a column down the screen", k && k.h < 340, JSON.stringify(k));

  // ---- the origin / destination callouts ---------------------------------------
  const callouts = await page.evaluate(() => Array.from(document.querySelectorAll(".od-callout"))
    .map(e => ({ role: (e.querySelector(".od-role") || {}).textContent, name: (e.querySelector(".od-name") || {}).textContent })));
  ok("⭐ the ends of the live routes are named on the map", callouts.length > 0, JSON.stringify(callouts));
  ok("...at most six of them", callouts.length <= 6, String(callouts.length));
  ok("...each with a role and a name",
    callouts.every(c => /Origin|Destination/.test(c.role || "") && (c.name || "").length > 1), JSON.stringify(callouts));
  ok("...and no close button on any of them",
    await page.evaluate(() => document.querySelectorAll(".od-callout .mapboxgl-popup-close-button").length) === 0);
  // ⭐ THE ONE THAT MATTERS, and the reason this harness exists. The first version of the
  // callouts drew Kuusiku's box underneath Rapla's and neither name could be read; every
  // source-level assertion passed. This measures the rendered rectangles and refuses any
  // pair that intersects — the outcome, not the mechanism that produces it.
  const overlaps = await page.evaluate(() => {
    const r = Array.from(document.querySelectorAll(".od-callout")).map(e => e.getBoundingClientRect());
    const bad = [];
    for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) {
      const a = r[i], b = r[j];
      if (!(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top)) bad.push([i, j]);
    }
    return bad;
  });
  ok("⭐ no two callouts are drawn on top of each other", overlaps.length === 0, JSON.stringify(overlaps));

  // ⚠️ Asserted on CONTENT, not on the count. The count is not a safe proxy any more:
  // the collision skip means an unfiltered month may already be showing fewer boxes than
  // it has ends, so "fewer after filtering" can be true while nothing was actually
  // dropped — and can be false while it was.
  await page.evaluate(() => { document.getElementById("filter-origin").value = "Kuusiku quarry"; applyFilters(); });
  await page.waitForTimeout(700);
  const filtered = await page.evaluate(() => Array.from(document.querySelectorAll(".od-callout .od-name")).map(e => e.textContent));
  ok("⭐ a filter that drops a route drops its callouts",
    filtered.length > 0 && !filtered.includes("Pärnu stockpile") && !filtered.includes("Lelle compound"),
    JSON.stringify(filtered));
  await shot("03-filtered");
  await page.evaluate(() => { document.getElementById("filter-origin").value = "ALL"; applyFilters(); });
  await page.waitForTimeout(500);

  // ---- the EVR overlay ----------------------------------------------------------
  const flowState = () => page.evaluate(() => ({
    exists: !!map.getLayer("evr-rail-flow"),
    vis: map.getLayer("evr-rail-flow") ? map.getLayoutProperty("evr-rail-flow", "visibility") : null,
    dash: map.getLayer("evr-rail-flow") ? map.getPaintProperty("evr-rail-flow", "line-dasharray") : null,
  }));
  let f = await flowState();
  ok("the EVR flow layer exists", f.exists, JSON.stringify(f));
  ok("⭐ and is hidden while the timeline is merely open, not playing", f.vis === "none", JSON.stringify(f));

  await page.evaluate(() => startTimeline());
  await page.waitForTimeout(900);
  f = await flowState();
  ok("⭐ Play makes it march", f.vis === "visible", JSON.stringify(f));
  const d1 = JSON.stringify(f.dash);
  await page.waitForTimeout(500);
  const d2 = JSON.stringify((await flowState()).dash);
  ok("...and the dash is actually stepping", d1 !== d2, d1 + " -> " + d2);
  await shot("04-playing");

  await page.evaluate(() => stopTimeline());
  await page.waitForTimeout(500);
  f = await flowState();
  ok("⭐ pause hides it again", f.vis === "none", JSON.stringify(f));

  // the checkbox half of the rule
  await page.evaluate(() => { const c = document.getElementById("layer-rail"); c.checked = false; toggleRail(false); startTimeline(); });
  await page.waitForTimeout(700);
  f = await flowState();
  ok("⭐ Play with the EVR checkbox OFF does not march", f.vis === "none", JSON.stringify(f));
  await page.evaluate(() => { stopTimeline(); const c = document.getElementById("layer-rail"); c.checked = true; toggleRail(true); });
  await page.waitForTimeout(400);

  const railNote = await page.evaluate(() => document.getElementById("rail-note").textContent);
  ok("⭐ the EVR note says provisional and points at the click popup",
    /Provisional corridor/.test(railNote) && /Click the line/.test(railNote), railNote);

  // ---- closing the timeline ------------------------------------------------------
  await page.evaluate(() => closeTimeline());
  await page.waitForTimeout(800);
  ok("⭐ closing the timeline removes every callout",
    await page.evaluate(() => document.querySelectorAll(".od-callout").length) === 0);
  ok("...and the marching overlay is gone", (await flowState()).vis === "none");
  ok("⭐ ...and the vehicles caveat from the last month goes with it",
    await page.evaluate(() => document.getElementById("kpi-vehicles-label").textContent) === "Vehicles needed",
    await page.evaluate(() => document.getElementById("kpi-vehicles-label").textContent));
  await shot("05-closed");

  // ---- sidebar closed --------------------------------------------------------------
  await page.evaluate(() => toggleSidebar());
  await page.waitForTimeout(600);
  const k2 = await vis("#kpi-hud"), menu = await vis("#menu-toggle");
  ok("⭐ with the sidebar closed the panel clears the menu button",
    !!k2 && !!menu && (k2.y >= menu.y + menu.h || k2.x >= menu.x + menu.w),
    JSON.stringify({ k2, menu }));
  await shot("06-sidebar-closed");
  await page.evaluate(() => toggleSidebar());

  // ---- phone -----------------------------------------------------------------------
  // ⚠️ RELOAD at the phone size, do not just resize. index.html closes the sidebar at
  // load time (`if (window.innerWidth <= 768) toggleSidebar()`), so resizing an
  // already-loaded desktop page leaves the sidebar open over the map and the screenshot
  // shows a layout no phone user would ever see.
  await page.setViewportSize({ width: 390, height: 780 });
  await page.reload();
  await page.waitForTimeout(2500);
  await page.evaluate(() => openTimeline());
  await page.waitForTimeout(1600);
  await page.evaluate(() => setTimelineMonth(7));
  await page.waitForTimeout(1200);
  const kp = await vis("#kpi-hud"), tb = await vis("#timeline-bar"), tw = await vis("#tl-warnings");
  ok("on a phone the panel is a bar above the timeline",
    !!kp && !!tb && kp.y + kp.h <= tb.y + 4 && kp.w > 300, JSON.stringify({ kp, tb }));
  ok("...with the note hidden",
    await page.evaluate(() => getComputedStyle(document.querySelector("#kpi-hud .kpi-note")).display) === "none");
  ok("...and the warning stack does not sit on top of it",
    !tw || !tw.visible || tw.y + tw.h <= kp.y + 4, JSON.stringify({ kp, tw }));
  await shot("07-phone");

  console.log();
  for (const x of fail) console.log("  FAIL:", x);
  console.log(`\n${pass} passed, ${fail.length} failed`);
  console.log(`page errors: ${errors.length} (offline glyph/tile noise ignored: ${glyphNoise.length})`);
  for (const e of errors.slice(0, 8)) console.log("   ", e);
  console.log(`screenshots in ${OUT}/map-*.png`);
  await b.close(); server.close();
  process.exit(fail.length || errors.length ? 1 : 0);
})();

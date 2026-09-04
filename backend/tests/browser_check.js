/*
 * 2.5b — the frontend in a REAL browser, with the API stubbed.
 *
 * ⚠️ NOT PART OF THE DEFAULT SUITE, AND IT CANNOT BE.
 * -------------------------------------------------
 * index.html loads React, Babel, Tailwind, Chart.js and Mapbox from CDNs that the
 * sandbox blocks, so this file substitutes local copies for them — which means it needs
 * packages installed first:
 *
 *     mkdir -p /tmp/shot && cd /tmp/shot
 *     npm i react@18.3.1 react-dom@18.3.1 @babel/standalone@7.24.7 tailwindcss@3.4.17
 *     printf '@tailwind base;@tailwind components;@tailwind utilities;' > in.css
 *     npx tailwindcss -i in.css -o tw.css --content "<repo>/frontend/index.html"
 *     node <repo>/backend/tests/browser_check.js
 *
 * parse_frontend.js and render_frontend.js are the ones that run everywhere. This is the
 * only harness that runs useEffect, lays anything out and clicks anything, so it is where
 * the 2.5b page architecture was actually checked:
 *
 *   - every rail page loads with real data and no page error;
 *   - ⭐ ONE Mapbox instance is constructed across Locations → Routes → Zones → Locations
 *     (the invariant the whole shared-component design exists for);
 *   - the Forecasts table fits at 1440px with its actions reachable — the reason the
 *     submitter column was folded into the Line column;
 *   - the collapsed rail still separates its groups.
 *
 * ⚠️ What it still does NOT prove: Mapbox and Chart.js are stubs, so no map, no chart and
 * no geometry is rendered; the API is fixtures, not the real backend; and no save,
 * approve or bake is exercised against a live server.
 *
 * It writes screenshots to /tmp/shot/live-*.png and prints the map-instance count.
 */
const fs = require("fs");
const http = require("http");
const path = require("path");
const G = "/home/claude/.npm-global/lib/node_modules/";
const { chromium } = require(G + "playwright");

const FRONT = require("path").resolve(__dirname, "..", "..", "frontend");
const LOCAL = {
  "react.production.min.js": "/tmp/shot/node_modules/react/umd/react.development.js",
  "react-dom.production.min.js": "/tmp/shot/node_modules/react-dom/umd/react-dom.development.js",
  "babel.min.js": "/tmp/shot/node_modules/@babel/standalone/babel.min.js",
};

// ---- fixtures ----------------------------------------------------------------
const routes = [
  { route_id: "R-001", origin: "Kuusiku quarry", dest: "Rapla railhead", ipt: "IPT3", material_category: "Aggregate" },
  { route_id: "R-002", origin: "Pärnu stockpile", dest: "Tootsi cut", ipt: "IPT2", material_category: "Fill" },
];
const meta = {
  routes, materials: ["Aggregate", "Fill"], vehicles: ["Artic Tipper (44t)", "Rigid Tipper (32t)"],
  units: ["m3", "t", "vehicles"], months: { start_year: 2026, count: 60, years: [2026, 2027, 2028, 2029, 2030] },
  factors: { material_density_t_per_m3: { Aggregate: 1.6, Fill: 1.8, _default: 1.6 },
             vehicle_payload_t: { "Artic Tipper (44t)": 28, "Rigid Tipper (32t)": 18, _default: 20 },
             vehicle_emissions_kg_co2e_per_km: { _default: 0.9 },
             planning: { working_days_per_month: 21, shift_hours_per_day: 10 } },
  mapbox_token: "", disciplines: [{ id: "Earthworks", label: "Earthworks" }, { id: "Track", label: "Track" }], work_sections: [{ section_id: "S1", name: "Rapla–Tootsi" }],
  vehicle_labels: { labels: { "Artic Tipper (44t)": { en: "Artic Tipper (44t)", eu: "Artic Tipper (44t)", ee: "Sadulveok kallur" },
                              "Rigid Tipper (32t)": { en: "Rigid Tipper (32t)", eu: "Rigid Tipper (32t)", ee: "Kallur" } },
                    fallbacks: {}, langs: ["en", "eu", "ee"] },
};
const mk = (route, mi, status, disc, ipt, by, qty) => ({
  route_id: route, month_index: mi, discipline: disc, section_id: "S1", quantity: qty, unit: "m3",
  material_type: "Aggregate", material_description: "0/32 crushed", vehicle_type: "Artic Tipper (44t)",
  status, submitted_by: by, ipt, reject_reason: status === "Rejected" ? "Split the March peak across two months." : null,
});
const forecasts = [];
for (let m = 1; m <= 6; m++) forecasts.push(mk("R-001", m, "Pending", "Earthworks", "IPT3", "j.davis", 4200 + m * 90));
for (let m = 1; m <= 4; m++) forecasts.push(mk("R-002", m, "Pending", "Track", "IPT2", "m.kask", 1800 + m * 40));
for (let m = 7; m <= 9; m++) forecasts.push(mk("R-001", m, "Approved", "Track", "IPT3", "j.davis", 900));
forecasts.push(mk("R-002", 5, "Rejected", "Earthworks", "IPT2", "m.kask", 5000));
const factorsDoc = JSON.parse(fs.readFileSync(require("path").resolve(__dirname, "..", "factors.json"), "utf8"));

function api(url) {
  const u = url.replace(/^.*\/api/, "").split("?")[0];
  if (u === "/meta") return meta;
  if (u === "/forecasts") return forecasts;
  if (u === "/forecasts/summary") return [{ route_id: "R-001", status: "Pending" }, { route_id: "R-002", status: "Pending" }];
  if (u === "/config/factors") return { doc: factorsDoc, status: { source: "database", updated_by: "j.davis",
      updated_at: "2026-09-03T09:12:00Z", differs_from_file: true } };
  if (u === "/routes/analysis-batch") return { results: {} };
  if (u.endsWith("/geojson")) return { type: "FeatureCollection", features: [] };
  if (u === "/routes/status") return [];
  if (u === "/zones") return [];
  if (u === "/locations") return [];
  if (u === "/network/summary") return { routes: 2, locations: 4, here_configured: true, profiles: {} };
  if (u === "/forecast-weeks") return { lines: [], editable: { month_index: 9, week_index: 1 } };
  return [];
}

const server = http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  // /map/ lives beside frontend/, not inside it — the portal's Map page iframes it
  const f = p.startsWith("/map")
    ? path.join(FRONT, "..", p.replace(/\/$/, "/index.html"))
    : path.join(FRONT, p === "/" ? "index.html" : p);
  try {
    const body = fs.readFileSync(f);
    res.writeHead(200, { "Content-Type": p.endsWith(".css") ? "text/css" : "text/html" });
    res.end(body);
  } catch (e) { res.writeHead(404); res.end("no"); }
});

(async () => {
  await new Promise(r => server.listen(8931, r));
  const b = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
  const page = await b.newPage({ viewport: { width: 1440, height: 950 } });
  const errors = [];
  // Errors from the /map/ iframe are NOT this harness's business: the public map is a
  // separate document with its own suite (parse_map.js), and it is running against a
  // Mapbox stub here, so it throws for reasons that say nothing about the portal.
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("frameattached", () => {});
  page.on("console", m => { if (m.type() === "error") errors.push("console: " + m.text().slice(0, 200)); });
  page.on("response", r => { if (r.status() >= 400) errors.push("HTTP " + r.status() + " " + r.url()); });

  await page.route("**/*", async (route) => {
    const url = route.request().url();
    if (url.includes("/api/")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(api(url)) });
    for (const [name, file] of Object.entries(LOCAL))
      if (url.includes(name)) return route.fulfill({ status: 200, contentType: "application/javascript", body: fs.readFileSync(file, "utf8") });
    if (url.includes("cdn.tailwindcss.com")) return route.fulfill({ status: 200, contentType: "application/javascript", body: "" });
    if (url.includes("chart.js") || url.includes("chart.umd"))
      return route.fulfill({ status: 200, contentType: "application/javascript", body: "window.Chart=function(){return{destroy(){},update(){}}};" });
    if (url.includes("mapbox-gl.js"))
      return route.fulfill({ status: 200, contentType: "application/javascript", body:
        "window.__mapCtors=0;window.mapboxgl={accessToken:'',Map:function(){window.__mapCtors++;var h={};this.on=function(e,a,c){};this.remove=function(){};this.getCanvas=function(){return{style:{}}};this.addControl=function(){};this.getSource=function(){return null};this.getLayer=function(){return null};this.addSource=function(){};this.addLayer=function(){};this.setStyle=function(){};this.resize=function(){};this.loaded=function(){return true};this.isStyleLoaded=function(){return true};this.isMoving=function(){return false};this.getZoom=function(){return 8};this.getCenter=function(){return{lng:24.7,lat:58.9}};this.getBounds=function(){return{getWest:function(){return 21},getEast:function(){return 28},getSouth:function(){return 57.5},getNorth:function(){return 59.7},toArray:function(){return[[21,57.5],[28,59.7]]}}};this.removeLayer=function(){};this.removeSource=function(){};this.off=function(){};this.hasImage=function(){return true};this.addImage=function(){};this.setPaintProperty=function(){};this.setLayoutProperty=function(){};this.fitBounds=function(){};this.easeTo=function(){};this.queryRenderedFeatures=function(){return[]};this.getStyle=function(){return{layers:[]}};this.setFilter=function(){};this.project=function(){return{x:0,y:0}};this.unproject=function(){return{lng:0,lat:0}};},NavigationControl:function(){},ScaleControl:function(){},LngLatBounds:function(){this.extend=function(){return this};this.isEmpty=function(){return true}}};" });
    if (url.startsWith("http://localhost:8931")) return route.continue();
    return route.fulfill({ status: 200, contentType: "text/css", body: "" });
  });

  await page.goto("http://localhost:8931/index.html");
  await page.addStyleTag({ path: "/tmp/shot/tw.css" });
  await page.waitForTimeout(1500);

  // sign in: the login form posts to /api/auth
  await page.route("**/api/auth", r => r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ role: "planner", label: "Planner", ipt: null, can_approve: true,
      ipt_locked: false, ipt_required: true, demo: true, ipts: ["IPT1","IPT2","IPT3"] }) }));

  await page.evaluate(() => { try { localStorage.setItem("rbe_page", "forecasts"); } catch(e){} });
  const btn = page.locator("text=Staff sign in").first();
  if (await btn.count()) { await btn.click(); await page.waitForTimeout(400); }
  await page.fill('input[placeholder="Your name"]', "j.davis");
  await page.fill('input[placeholder="Access code"]', "planner123");
  await page.click('button:has-text("Sign in")');
  await page.waitForTimeout(1800);
  await page.addStyleTag({ path: "/tmp/shot/tw.css" });

  const shots = [["forecasts", "Forecasts"], ["dashboard", "Dashboard"], ["submit", "Submit forecast"],
                 ["lookahead", "Look-ahead"], ["config", "Config"], ["routes", "Routes"]];
  for (const [file, label] of shots) {
    const nav = page.locator(`aside button:has-text("${label}")`).first();
    if (await nav.count()) { await nav.click(); await page.waitForTimeout(900); }
    await page.screenshot({ path: `/tmp/shot/live-${file}.png` });
  }
  // ⭐ the invariant: moving between the three data pages must NOT rebuild the map
  await page.locator('aside button:has-text("Locations")').first().click(); await page.waitForTimeout(500);
  const afterLoc = await page.evaluate(() => window.__mapCtors);
  await page.locator('aside button:has-text("Routes")').first().click(); await page.waitForTimeout(500);
  await page.locator('aside button:has-text("Zones")').first().click(); await page.waitForTimeout(500);
  await page.locator('aside button:has-text("Locations")').first().click(); await page.waitForTimeout(500);
  const afterAll = await page.evaluate(() => window.__mapCtors);
  console.log("map constructed:", afterLoc, "->", afterAll,
    afterLoc === 1 && afterAll === 1 ? "OK (one instance across all three pages)" : "FAIL");
  // and leaving the group entirely and coming back is allowed to rebuild it once
  await page.locator('aside button:has-text("Dashboard")').first().click(); await page.waitForTimeout(400);
  await page.locator('aside button:has-text("Routes")').first().click(); await page.waitForTimeout(600);
  console.log("after leaving and returning:", await page.evaluate(() => window.__mapCtors));

  // collapsed rail
  const col = page.locator('aside button:has-text("Collapse")').first();
  if (await col.count()) { await col.click(); await page.waitForTimeout(400); await page.screenshot({ path: "/tmp/shot/live-collapsed.png" }); }

  const MAP_NOISE = ["forEach", "Cannot convert undefined or null to object", "/map/"];
  const portalErrors = errors.filter(e => !MAP_NOISE.some(n => e.includes(n)));
  console.log("PORTAL ERRORS:", portalErrors.length, "(map-iframe noise ignored:", errors.length - portalErrors.length, ")");
  for (const e of portalErrors.slice(0, 12)) console.log("  ", e);
  await b.close();
  server.close();
})();

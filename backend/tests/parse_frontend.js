/*
 * Frontend assertions for the Phase 2 changes.
 *
 * There is no build step -- index.html is React via CDN with in-browser Babel -- so this
 * pulls the <script type="text/babel"> block out of the file, parses it as TSX with the
 * global TypeScript compiler, and then asserts at SOURCE level.
 *
 * Source-level assertions are the point, not a shortcut. useEffect does not run under
 * renderToStaticMarkup, so the only way to prove that a fetch was rewired or a retired
 * control is really gone is to assert against the source. That has caught dangling UI
 * text more than once.
 *
 * NOT covered: anything requiring a browser. Mapbox, Chart.js rendering, actual fetch
 * behaviour, and whether the new pickers look right are unverified here.
 *
 * Run:  node backend/tests/parse_frontend.js
 */
const fs = require("fs");
const path = require("path");
const TS = "/home/claude/.npm-global/lib/node_modules/typescript";
const ts = require(TS);

const ROOT = path.resolve(__dirname, "..", "..");
const FILE = path.join(ROOT, "frontend", "index.html");
const html = fs.readFileSync(FILE, "utf8");

let pass = 0;
const fail = [];
function ok(label, cond, extra) {
  if (cond) pass++;
  else fail.push(label + (extra ? "  " + extra : ""));
}

// ---- extract the babel block -------------------------------------------------
const m = html.match(/<script type="text\/babel"[^>]*>([\s\S]*?)<\/script>/);
ok("the babel script block is present", !!m);
const src = m ? m[1] : "";

// ---- 1. it must still parse --------------------------------------------------
const sf = ts.createSourceFile("index.tsx", src, ts.ScriptTarget.Latest, true,
  ts.ScriptKind.TSX);
const diags = sf.parseDiagnostics || [];
ok("JSX parses with no syntax errors", diags.length === 0,
  diags.slice(0, 3).map(d => {
    const p = sf.getLineAndCharacterOfPosition(d.start);
    return `line ${p.line + 1}: ${ts.flattenDiagnosticMessageText(d.messageText, " ")}`;
  }).join(" | "));

// and it must survive a real transpile, which catches things parse alone does not
let emitted = "";
try {
  emitted = ts.transpileModule(src, {
    compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2019 },
  }).outputText;
  ok("JSX transpiles to runnable JS", emitted.length > 1000);
} catch (e) {
  ok("JSX transpiles to runnable JS", false, e.message);
}
try {
  new Function(emitted.replace(/^import .*$/gm, ""));
  ok("transpiled output is syntactically valid JS", true);
} catch (e) {
  ok("transpiled output is syntactically valid JS", false, e.message);
}

// ---- 2. the dashboard no longer computes its own cycle time ------------------
ok("dashboard fetches the backend analysis batch",
  src.includes("/routes/analysis-batch"));
ok("the flat-speed cycle formula is gone",
  !src.includes("round/P.avg_haul_speed_kmh") &&
  !src.includes("round / P.avg_haul_speed_kmh"));
ok("no local cycleH is computed from avg_haul_speed_kmh",
  !/cycleH\s*=\s*[^;]*avg_haul_speed_kmh/.test(src));
ok("cycle time is read from the analysis payload",
  /cycleH\s*:\s*\(?\s*an\s*\?\s*an\.cycle_hr/.test(src));
ok("an unbaked route renders 'not baked' rather than a number",
  src.includes("not baked"));
ok("distance falls back to null, not 0",
  !src.includes("route.distance_km || 0"));
ok("the dashboard warns when lines reference unbaked routes",
  src.includes("D.unbaked") && src.includes("no cached geometry"));
ok("fleet size is derived from demand over capacity",
  src.includes("peakPerDay / a.capacityPerDay"));
ok("the footnote no longer claims a local estimate",
  src.includes("no longer estimates its own"));

// ---- 3. per-line model -------------------------------------------------------
ok("dashboard rows are keyed per line, not per route",
  src.includes("`${r.route_id}|${r.discipline||\"\"}|${r.section_id||\"\"}`"));
ok("dashboard shows a discipline column", src.includes('setSort("discipline")'));
ok("matrix posts a discipline", /discipline\s*:/.test(src));
ok("matrix posts a section_id", /section_id\s*:/.test(src));

// ---- 4. the two-vehicle split UI is really gone ------------------------------
// Comments are stripped first. The file now EXPLAINS why the split was removed, and a
// raw substring check would match the explanation and report the code as still present.
const code = src
  .split("\n")
  .map(l => l.replace(/\/\/.*$/, ""))
  .join("\n")
  .replace(/\/\*[\s\S]*?\*\//g, "");

for (const gone of [
  ["vehicle_type_2", "vehicle_type_2 no longer read or sent"],
  ["split_pct", "split_pct no longer read or sent"],
  ["setUseTwo", "the 'split across a second vehicle' state is gone"],
  ["useTwo", "no useTwo branching survives"],
  ["setVehicle2", "the second-vehicle state is gone"],
  ["Split the load across a second vehicle", "its checkbox label is gone"],
  ["1 / (s / p1", "the blended-payload maths is gone"],
  ["Vehicle 2", "the Vehicle 2 picker is gone"],
  ["blended ${effPayload", "the 'blended t/load' readout is gone"],
]) {
  ok(gone[1], !code.includes(gone[0]));
}
ok("effective payload is now simply the vehicle's own payload",
  /const effPayload\s*=\s*payload\(factors,\s*vehicle\)/.test(code));

// the delete/withdraw paths must be line-scoped too, or one discipline's withdrawal
// takes another's forecast with it
ok("withdraw sends the line keys",
  code.includes("discipline: g.discipline") && code.includes("section_id: g.sectionId"));
ok("my-submissions groups per line",
  code.includes('[r.route_id, year, disc, sect].join("|")'));
ok("edit carries the line keys back into the matrix",
  code.includes("editTarget.discipline") && code.includes("editTarget.sectionId"));

// ---- 5. the double-counting caution --------------------------------------------
ok("the save surfaces the backend caution", src.includes("caution"));

// ---- 6. things that must NOT have been broken ---------------------------------
for (const keep of [
  ["mapboxgl", "the Mapbox instance is untouched"],
  ["applyEmphasis", "basemap-switch paint restoration survives"],
  ["promote-alt", "promote-to-primary survives"],
  ["RouteAnalysis", "the route analysis panel survives"],
  ["Diagnostics", "the diagnostics panel survives"],
  ["gate_lat", "gate points survive"],
]) {
  ok(keep[1], src.includes(keep[0]));
}

// ---- 7. Phase 3: zones ---------------------------------------------------------
// Source-level again, and here that is not a compromise but the only option: the whole
// feature is map interaction, and there is no browser in this harness. What CAN be
// proved is that the wiring exists and that nothing sends HERE calls by accident.
ok("a Zones sub-tab exists", code.includes('["zones", "Zones", zoneList.length]'));
ok("the sub-tab switch is three-way, not 'not locations'",
  code.includes("const onRoutes = subTab === \"routes\"") &&
  code.includes("const onZones = subTab === \"zones\""));
ok("no `!onLoc` branch survives — it would render the routes panel on the Zones tab",
  !code.includes("{!onLoc &&"));
ok("zones are fetched from /api/zones", code.includes("`${API}/zones`"));
ok("zone writes go to the admin endpoint", code.includes("`${API}/admin/zones"));
ok("zone deletion checks the impact first", code.includes("/impact"));
ok("the admin token rides along on zone writes",
  code.includes("tokenRef.current ? `?token=${encodeURIComponent(tokenRef.current)}` : \"\""));

ok("drawing collects vertices from map clicks", code.includes("cbRef.current.addVertex"));
ok("zone drawing is checked BEFORE add-location mode, so the two cannot both fire",
  code.indexOf("zoneDrawRef.current") < code.indexOf('modeRef.current === "add"'));
ok("the drawn ring is explicitly closed before it is sent", code.includes("const ringOf"));
// Phase 4 replaced the literal 3 with minPts(), which is 3 for a zone and 2 for a haul
// road. The rule for zones is unchanged; the assertion follows it to its new home rather
// than being deleted.
ok("a minimum vertex count is enforced before save",
  code.includes("drawPts.length < minPts(zoneForm)"));
ok("a zone still needs three corners and a haul road two",
  /minPts\s*=\s*\(f\)\s*=>\s*isHaul\(f\)\s*\?\s*2\s*:\s*3/.test(code));

ok("saving re-bakes what it invalidated", code.includes("rebakeAfterZone"));
ok("the re-bake drives the existing batch endpoint rather than a new one",
  code.includes("`${API}/admin/bake-routes?${q}`"));
ok("the re-bake loop has a guard against spinning forever", code.includes("guard++ < 200"));
ok("only the affected vehicle profiles are re-baked",
  code.includes("legs.map(l => l.vehicle_profile)"));
ok("the invalidated leg count is surfaced to the user", code.includes("inv.leg_count"));
ok("approximate matches are declared, not hidden", code.includes("inv.approximate"));

ok("routing zones and advisory zones are drawn differently",
  code.includes("ZONE_ROUTING") && code.includes("ZONE_ADVISORY"));
ok("the panel states that HERE takes boxes, not polygons",
  src.includes("bounding boxes, not polygons"));
ok("the panel shows the box that will actually be sent", src.includes("Sent to HERE as bbox:"));
ok("deactivating is offered as the alternative to deleting",
  src.includes("without deleting it"));
ok("leaving the Zones tab abandons a half-drawn shape",
  code.includes('if(subTab !== "zones"'));

// ---- Phase 4: temporary haul roads -------------------------------------------
// The user requirement was that a haul road is editable ON THE ROUTE, not in a separate
// drawing tool. These assertions are what keeps that true: a HaulRoads panel that lives
// inside the per-route expansion, and no second map or second tab.
ok("there is a haul-road panel", code.includes("function HaulRoads("));
ok("it is rendered inside the per-route expansion, not as its own tab",
  code.includes("<HaulRoads routeId={routeId}"));
ok("RouteAnalysis is given the haul roads to offer", code.includes("haulRoads={haulRoadList}"));
ok("attaching posts to the route's own endpoint",
  code.includes("/haul-roads${qs()}`"));
ok("detaching is a DELETE on the route + zone pair",
  code.includes('method: "DELETE"') && code.includes("haul-roads/${encodeURIComponent(zid)}"));
ok("the traversal order can be changed", code.includes("haul-roads/order${qs()}"));
ok("a haul change drives the SAME re-bake loop as a zone write, not a second one",
  code.includes("onHaulChanged={rebakeAfterZone}"));

ok("a haul road is drawn as an open LineString, not a closed ring",
  code.includes('{ type: "LineString", coordinates: drawPts.map'));
ok("and a zone is still a Polygon", code.includes('{ type: "Polygon", coordinates: [ringOf(drawPts)] }'));
ok("the in-progress preview does not close a haul road",
  code.includes("const line = (!haulNow && drawPts.length >= 3) ? ringOf(drawPts) : drawPts"));
// the pop() that strips a polygon's repeated closing vertex must not run on a haul road,
// or reopening one would shorten it by a point every time
ok("editing a haul road does not pop its last point as if it closed a ring",
  code.includes('if(z.geometry && z.geometry.type === "Polygon"'));

ok("a blank speed is sent as null, not zero", code.includes("? Number(zoneForm.speed_kph) : null"));
ok("the speed field explains that HERE will not take a custom speed",
  src.includes("will not accept a custom speed"));
ok("the mode field says which failure mode is invisible",
  src.includes("wrong without warning"));
ok("splice is named as the default", src.includes("Default (splice)"));

ok("the panel says a haul road does nothing until it is attached",
  src.includes("does nothing until it is"));
ok("an unattached haul road is called out in the zones table", src.includes("no routes"));
ok("the single-option regression is stated where the roads are attached",
  src.includes("single option"));
ok("an assigned haul speed is marked in the cycle column", src.includes("haul_speed_applied"));
ok("and the HERE-timed cycle is offered alongside it", code.includes("r.cycle_hr_here"));

ok("haul roads have their own colour — red would read as 'closed'",
  code.includes("const ZONE_HAUL"));
ok("the map draws them by kind, not by affects_routing",
  code.includes('is_haul: z.kind === HAUL_KIND'));
ok("the haul kind is a constant, not a string literal scattered about",
  code.includes('const HAUL_KIND = "haul_road"'));
ok("the Phase 4 placeholder label is gone from the kind list",
  !src.includes("Haul road (Phase 4)"));

// ---- Phase 2.5a: road restrictions on a route --------------------------------
ok("there is a per-route restriction panel", code.includes("function RouteRestrictions("));
ok("it is rendered inside the route expansion, beside the haul roads",
  code.includes("<RouteRestrictions routeId={routeId} />"));
ok("it reads the per-route endpoint", code.includes("/restrictions`"));
ok("a weak bridge's load class is shown verbatim",
  code.includes("{h.nominal_load}"));
ok("and explicitly NOT compared to tonnes",
  src.includes("a load class, not tonnes"));
ok("no pass/fail verdict is rendered anywhere in the panel",
  !/\b(exceeds|over limit|cannot cross|too heavy)\b/i.test(src.split("function RouteRestrictions")[1].split("function RouteAnalysis")[0]));
ok("the vehicle's own laden weight is offered so a human can judge",
  code.includes("h.vehicle_gross_t"));
ok("the panel says it is advisory and not fed to the router",
  src.includes("The router is never given this data"));
ok("a partial fetch is declared rather than passing as a clean check",
  code.includes("fetch_errors") && src.includes("not a complete check"));
ok("an unreachable service degrades to a note, not an error banner",
  src.includes("Tark Tee could not be reached"));
ok("a route with no hits says so explicitly rather than rendering nothing",
  src.includes("No Tark Tee restrictions within"));

// ---- report ------------------------------------------------------------------
console.log();
for (const f of fail) console.log("  FAIL:", f);
console.log(`\n${pass} passed, ${fail.length} failed`);
process.exit(fail.length ? 1 : 0);

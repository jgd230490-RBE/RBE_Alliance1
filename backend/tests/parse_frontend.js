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
ok("a zone needs at least three corners", code.includes("drawPts.length < 3"));

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

// ---- report ------------------------------------------------------------------
console.log();
for (const f of fail) console.log("  FAIL:", f);
console.log(`\n${pass} passed, ${fail.length} failed`);
process.exit(fail.length ? 1 : 0);

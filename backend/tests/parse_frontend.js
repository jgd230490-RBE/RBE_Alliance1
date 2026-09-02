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
// ⚠️ CHANGED 2026-09-01. This asserted `[r.route_id, year, disc, sect]` — per line AND
// year. The multi-year save lets one line span several years, and that key splits it
// into a 2026 row and a 2027 row that read as two separate forecasts. The year came OUT
// of the key; discipline and section stay in it, which is the half this assertion has
// always really been protecting.
ok("my-submissions groups per line, across every year the line covers",
  code.includes('[r.route_id, disc, sect].join("|")')
  && !/\[r\.route_id,\s*year,/.test(code));
ok("edit carries the line keys back into the matrix",
  code.includes("editTarget.discipline") && code.includes("editTarget.sectionId"));

// ---- 4b. Task A — the four planning vehicles lead the picker -------------------
ok("the matrix reads planning_vehicles off /api/meta",
  code.includes("planning_vehicles"));
ok("...and defaults it to [] so an older meta payload still renders one flat list",
  /planning_vehicles\s*=\s*\[\]/.test(code));
ok("the picker groups planning vehicles above the rest",
  code.includes('<optgroup label="Planning vehicles">')
  && code.includes('<optgroup label="Other vehicles">'));
ok("the legacy group is the complement, not a second hard-coded list",
  /legacyVehicles\s*=\s*useMemo\(\s*\(\)\s*=>\s*vehicles\.filter/.test(code));
// one <option> renderer, shared: two copies of the disabled rule would drift
ok("both groups render through one shared option builder",
  /function vehOption\(/.test(code)
  && (code.match(/vehOption\(v, suggestedVehicles, vehicle\)/g) || []).length >= 3);
ok("a vehicle outside the material's list is still visible, just disabled",
  code.includes("not used for this material"));
ok("...and the currently-selected vehicle is never disabled",
  /disabled=\{!fits && v !== current\}/.test(code));

// ---- 4b2. 2026-09-02 — one vehicle, three labels -------------------------------
ok("there is a label language toggle with exactly EN / EU / EE",
  /const VEH_LANGS = \[\["en", "English"\], \["eu", "European"\], \["ee", "Estonian"\]\]/.test(code));
ok("the default is European", /const VEH = \{ lang: "eu"/.test(code));
ok("labels come off /api/meta, not a second list in the client",
  code.includes("m.vehicle_labels") && code.includes("VEH.labels = vl.labels"));
// ⭐ THE INVARIANT. The <option value> is the canonical id; only the text changes.
ok("⭐ the picker's option VALUE is the canonical id, the text is the label",
  /<option key=\{v\} value=\{v\} disabled=[\s\S]{0,160}\{vehLabel\(v\)\}/.test(code));
ok("a fallback slot is marked, not passed off as a translation",
  /vehIsFallback\(v\) && VEH\.lang !== "eu" \? " \*"/.test(code));
ok("vehLabel falls back to the id itself", /\|\| v \|\| ""/.test(code));
ok("the save still posts the stored id", code.includes("vehicle_type: vehicle,"));
ok("tables show the label with the stored id in the title",
  code.includes('title={g.vehicle}>{vehLabel(g.vehicle)}')
  && code.includes('title={r.vehicle_type}>{r.material_type} · {vehLabel(r.vehicle_type)}'));
ok("the header carries the control", /VEH_LANGS\.map\(\(\[k, l\]\) =>/.test(code));

// ---- 4c. Task B — the multi-year matrix ---------------------------------------
// The single-year state has to be GONE, not shadowed. A surviving setYear would be a
// second source of truth for the same window.
for (const gone of [
  ["setYear(", "the single-year setter is gone"],
  ["const [year,", "the single-year state is gone"],
  ["clearYear", "'Clear year' no longer refers to a single year's state"],
]) {
  ok(gone[1], !code.includes(gone[0]));
}
ok("the matrix carries a From year and a To year",
  /const \[fromYear, setFromYear\]/.test(code) && /const \[toYear, setToYear\]/.test(code));
ok("both come from months.years", code.includes("yearsAvail.map(y =>"));
ok("⭐ the To list only offers years at or after From",
  code.includes("yearsAvail.filter(y => y >= fromYear)"));
ok("...and to >= from is enforced in state as well as in the dropdown",
  /setToYear\(t => \(t < fromYear \? fromYear : t\)\)/.test(code));
ok("the default range is the CURRENT calendar year",
  code.includes("new Date().getFullYear()"));
ok("...falling back to the first year the backend offers",
  /yearsAvail\.includes\(now\) \? now : \(yearsAvail\[0\]/.test(code));
ok("one labelled twelve-cell row is rendered per year in the range",
  code.includes("yearsInRange.map(y =>") && code.includes("MONTHS.map((mn, i) =>"));
// ⭐ absolute month_index keys. A relative 1..12 key collides across years — Jan 2026
// and Jan 2027 would both be "1" and overwrite each other in `vals`.
ok("⭐ cells are keyed by absolute month_index, not 1..12",
  code.includes("const mi = base + i + 1") && code.includes("vals[mi]")
  && !/vals\[i \+ 1\]/.test(code));
ok("the edit window is a month_index range computed from the two years",
  /const mLo = \(fromYear - months\.start_year\) \* 12 \+ 1/.test(code)
  && /const mHi = \(Math\.max\(fromYear, toYear\) - months\.start_year\) \* 12 \+ 12/.test(code));
ok("the load filters on that window", code.includes("x.month_index >= mLo && x.month_index <= mHi"));
ok("...and re-runs when either year moves",
  code.includes("[routeId, fromYear, toYear, discipline, sectionId]"));
ok("⭐ ONE bulk POST covers the whole range",
  /for\(let mi = mLo; mi <= mHi; mi\+\+\) cells\.push/.test(code)
  && (code.match(/\/forecasts\/bulk/g) || []).length === 1);
ok("totals, per-day and the seasonal check all read the same in-range list",
  (code.match(/inRange/g) || []).length >= 4);
// seasonal_restrictions.months are CALENDAR months; month_index is absolute
ok("⭐ the seasonal check folds an absolute index back to a calendar month",
  code.includes("((mi - 1) % 12) + 1"));
ok("clearing is scoped to the range, not the whole of vals",
  /for\(let mi = mLo; mi <= mHi; mi\+\+\) delete next\[mi\]/.test(code));
ok("the save message names the range", code.includes("Saved ${rangeLabel} forecast"));
// my submissions
ok("⭐ my submissions shows a year SPAN when a line covers more than one",
  code.includes("fromYear === toYear ? String(fromYear) : `${fromYear}–${toYear}`"));
ok("withdraw is scoped to the months the row actually shows, not 1..60",
  code.includes("from: String(g.minMonth), to: String(g.maxMonth)"));
ok("...and the confirmation says how many months go",
  code.includes("will be removed"));
ok("edit reopens the line on its full span",
  code.includes("year: g.fromYear, toYear: g.toYear"));
ok("...and the matrix accepts that span", code.includes("setToYear(editTarget.toYear || editTarget.year)"));

// ---- 4c2. 2026-09-02 §3 — the six per-day cards ---------------------------------
for (const card of ["Avg vehicles / day", "Peak vehicles / day", "Avg trips / day", "Peak trips / day"]) {
  ok(`the range strip carries '${card}'`, code.includes(`"${card}"`));
}
ok("...and avg / peak material in the CHOSEN unit",
  code.includes('`Avg ${unit === "m3" ? "m³"') && code.includes('`Peak ${unit === "m3" ? "m³"'));
ok("working days come from factors.planning, defaulting to 22",
  /working_days_per_month\) \|\| 22/.test(code));
ok("averages are over months WITH a figure, not the whole range",
  code.includes("inRange.filter(([, q]) => +q > 0)") && code.includes("/ months.length) / wd"));
ok("peak is the busiest single month per working day",
  code.includes("Math.max(...monthlyT) / wd") && code.includes("Math.max(...monthlyQ) / wd"));
ok("vehicle loads convert through the payload", code.includes("avgT / p, peakVeh = peakT / p"));
ok("trips are stated to equal vehicle loads on one line", code.includes("avgTrips: avgVeh, peakTrips: peakVeh"));
ok("the cards sit in the navy strip with tabular numerals, no new fonts",
  code.includes('style={{ background: "var(--navy)" }}') && code.includes("tabular-nums")
  && !/fontFamily/.test(code.slice(code.indexOf("Avg vehicles / day") - 2000, code.indexOf("Avg vehicles / day") + 2000)));

// ---- 4d. Tasks C + D — the Look-ahead tab -------------------------------------
ok("there is a Look-ahead tab", code.includes('"Look-ahead"'));
ok("...placed straight after Submit Forecast",
  /"Dashboard", "Submit Forecast", "Look-ahead", "My submissions"/.test(code));
ok("...and it renders the LookAhead component",
  /tab === "Look-ahead" && <LookAhead/.test(code));
// ⚠️ Visible to submitters too. Until Task F, /api/forecast-weeks has exactly the same
// visibility as /api/forecasts, and hiding the tab would imply a boundary that is not
// there. Asserted so nobody "fixes" it into a false permission.
ok("⭐ the tab is NOT gated on canApprove",
  !/canApprove \? \[[^\]]*Look-ahead/.test(code));
ok("the window is the current month plus the next one",
  /to: Math\.min\(months\.count, from \+ 1\)/.test(code));
// ⭐ which cell is editable comes from the SERVER, not the browser clock
ok("⭐ the editable week comes from the server's next_week",
  code.includes("payload.next_week") && /const isNext = \(r\) =>/.test(code));
ok("only next week, or an already-edited week, is editable",
  /r\.status !== "confirmed" && \(next \|\| r\.status === "edited"\)/.test(code));
ok("a confirmed week's plan is read-only", code.includes("Confirmed — reopen it to change the plan"));
ok("Confirm next week is its own button, separate from editing",
  code.includes("Confirm next week") && code.includes("/forecast-weeks/confirm"));
ok("confirm offers exactly the four flag fields, all optional",
  /\["weather", "wetness", "traffic", "other"\]/.test(code)
  && code.includes("optional and may be left blank"));
// ⭐ THE ONE THAT MATTERS. Saving an actual must not calibrate.
ok("⭐ the actual is saved through /forecast-weeks/actual",
  code.includes("/forecast-weeks/actual"));
// Scoped to saveActual's OWN body, sliced out between its declaration and the next
// one. A window-sized regex matched doCalibrate() further down the file and passed on
// a clean tree AND on a deliberately broken one — vacuously true in both directions.
const _saveActualBody = (() => {
  const i = code.indexOf("const saveActual =");
  if (i < 0) return null;
  const j = code.indexOf("const doConfirm =", i);
  return j > i ? code.slice(i, j) : code.slice(i);
})();
ok("saveActual is findable in the source", !!_saveActualBody);
ok("⭐ and saveActual's own body never touches the calibrate endpoint",
  !!_saveActualBody && !_saveActualBody.includes("forecast-weeks/calibrate"));
ok("⭐ calibrate is reached only from the → next week button",
  (code.match(/\/forecast-weeks\/calibrate/g) || []).length === 1
  && code.includes("→ next week"));
ok("...and the button is disabled when next week is confirmed",
  code.includes('after.status !== "confirmed"')
  && code.includes("Next week is already confirmed"));
ok("the calibrate dialog offers a typed override instead of the formula",
  code.includes("override_qty") && code.includes("leave blank to use the formula"));
ok("variance is blank rather than 0 until an actual is typed",
  /r\.variance == null \? "—"/.test(code));
ok("an emptied actual box clears it back to null, not to zero",
  /actual_qty: \(v === "" \|\| v == null \? null/.test(code));
ok("a week whose parent month changed says so",
  code.includes("parent month changed") && code.includes("parent_changed"));
ok("reopening a month is shown as a note, not by hiding its weeks",
  code.includes("month is now"));

// ---- 4e. Task D2 — stock held --------------------------------------------------
ok("the stockpile panel sits under the look-ahead",
  /<Stockpiles meta=\{meta\}/.test(code) && /function Stockpiles\(/.test(code));
ok("consumption is typed through /stockpiles/consume", code.includes("/stockpiles/consume"));
ok("...and the balance is read from /stockpiles", code.includes("/stockpiles?from_month="));
// ⭐ no capacity recorded is NOT zero capacity
ok("⭐ an unset capacity renders '—', never 0",
  /s\.capacity_qty == null \? "—"/.test(code)
  && /cell\.remaining == null \? "—"/.test(code));
ok("over capacity uses the existing red, not a reserved route colour",
  /cell\.over \? "var\(--red\)"/.test(code)
  && !/cell\.over \? "#039E86"/.test(code));
ok("the panel says inbound comes from typed actuals only",
  code.includes("a week with no") && code.includes("actual counts as nothing"));
ok("the capacity fields appear for the four storage types",
  /const STORAGE_TYPES = \["Stockpile", "Site", "Compound", "Railhead"\]/.test(code)
  && /STORAGE_TYPES\.includes\(form\.loc_type\)/.test(code));
ok("...and on other types only when the location receives material",
  /form\.role === "destination" \|\| form\.role === "both"/.test(code));
ok("capacity has ONE write path, its own endpoint",
  (code.match(/\/capacity\$\{qs\}/g) || []).length === 1);

// ---- 4f. Task E — the two new location types -----------------------------------
ok("Railhead and Stockpile are offered as location types",
  /const LOC_TYPES = \["Quarry", "Port", "Compound", "Site", "Railhead", "Stockpile", "Other"\]/.test(code));
ok("Railhead is drawn in the reserved rail colour",
  /"Railhead": "#0F766E"/.test(code));

// ---- 4h. 2026-09-02 Task F — access codes resolved by the server ----------------
ok("⭐ the client-side LOGINS dict with plaintext codes is GONE",
  !/const LOGINS\s*=/.test(code) && !/CAN_APPROVE\s*=/.test(code));
ok("sign-in POSTs the code to /api/auth", code.includes("`${API}/auth`"));
ok("⭐ every /api request carries the code in X-Access-Code, from ONE wrapper",
  code.includes('"X-Access-Code": ACCESS.code') && /window\.fetch = \(url, opts\) =>/.test(code)
  && (code.match(/X-Access-Code/g) || []).length === 1);
ok("the code lives in sessionStorage, not localStorage",
  code.includes('sessionStorage.setItem("rbe_access_code"') && !code.includes('localStorage.setItem("rbe_access_code"'));
ok("sign-out clears it", code.includes('sessionStorage.removeItem("rbe_access_code")'));
ok("canApprove comes from the server payload", /const canApprove = !!\(role && role\.can_approve\)/.test(code));
ok("the form has an IPT field", code.includes('label={iptRequired ? "IPT (required)" : "IPT"}'));
ok("⭐ an IPT code's field is locked to its IPT", code.includes("Your access code is for this IPT"));
ok("⭐ a planner's default is EMPTY, never IPT 1",
  code.includes('useState((access && access.ipt) || "")') && code.includes("— pick an IPT —")
  && !/useState\("IPT1"\)/.test(code));
ok("...and the save refuses without one when required",
  code.includes("Pick which IPT this line belongs to before saving."));
ok("the save posts ipt on the line", /ipt: \(iptLocked \? access\.ipt : ipt\) \|\| null/.test(code));
ok("a line with no IPT is flagged in Approvals as planner-only",
  code.includes("no IPT") && code.includes("only planners can see it until one is set"));

// ---- 4g. NOTHING may upload ----------------------------------------------------
// "Never build: file upload, OCR..." — asserted at source level so a later edit that
// adds one trips here rather than shipping.
for (const banned of ['type="file"', "FormData(", ".files[", "multipart/form-data"]) {
  ok(`no upload path: ${banned} is absent`, !code.includes(banned));
}

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
// ⚠️ These four were REVERSED, not deleted, once the real Tark Tee schema was read.
// restriction_limit is a genuine number in tonnes or metres, so mass/height/width DO get
// a verdict now. The original point — no invented verdict on a bridge LOAD CLASS — is
// narrowed to exactly that case rather than dropped.
ok("the restriction's actual value is shown, not just its existence",
  code.includes("{h.limit}") && code.includes("h.unit"));
ok("a real EXCEEDS verdict is given where two numbers share a unit",
  code.includes('h.verdict === "exceeds"') && src.includes("EXCEEDS by"));
ok("and the margin either way is quantified", code.includes("h.margin"));
ok("but a bridge load class is still NOT converted to tonnes",
  src.includes("load class is not a tonnage") || src.includes("a weak bridge's load class is not a tonnage"));
ok("an uncomparable restriction says 'not comparable' rather than guessing",
  src.includes("not comparable"));
ok("and carries the reason it could not be compared", code.includes("h.note"));
ok("the vehicle's own figure is shown beside the limit",
  code.includes("h.vehicle_value"));
ok("the panel says it is advisory and not fed to the router",
  src.includes("The router is never given this data"));
ok("a partial fetch is declared rather than passing as a clean check",
  code.includes("fetch_errors") && src.includes("not a complete check"));
ok("an unreachable service degrades to a note, not an error banner",
  src.includes("Tark Tee could not be reached"));
ok("a route with no hits says so explicitly rather than rendering nothing",
  src.includes("No Tark Tee restrictions in force within"));
ok("expired records are declared as excluded, not silently dropped",
  code.includes("expired_or_future_excluded"));

// ---- Phase 5a: gates, and the turnaround split -------------------------------
ok("the gate editor is present on the Locations panel",
  code.includes("saveGate") && code.includes("gateForm"));
ok("gates are only offered once a location exists — a gate needs a location_id",
  src.includes("Gates can be added once the location has been created"));
ok("a gate can be placed by clicking the map, like every other point in this app",
  code.includes("placeGateAt") && code.includes("gatePlaceRef"));
ok("gate placement is a separate click mode from node placement",
  code.includes("gatePlaceRef.current") && code.includes('modeRef.current === "add"'));
ok("leaving the Locations tab abandons a half-placed gate",
  src.includes('if(subTab !== "locations") setGatePlace(false)'));
ok("the three directions are offered by their meaning, not by their column value",
  src.includes("Entry only") && src.includes("Exit only") && src.includes("In and out"));
ok("induction time is editable per gate (B3)", code.includes("safety_minutes"));
ok("the flat gate-to-face allowance is editable per gate (B5)",
  code.includes("internal_travel_minutes"));
// copy replaced 2026-09-02 with the human's wording; the B5(c) statement survives
ok("the UI says the flat allowance is IGNORED where a haul road is drawn — B5(c)",
  src.includes("ignored if the") && src.includes("not counted twice"));
ok("the two gate fields carry the 2026-09-02 label copy",
  src.includes("Safety / briefing at this gate. Once per arrival.")
  && src.includes("Drive from this gate to the face. Ignored if the route uses a drawn haul road."));
// §2 — old spelling accepted on read, never written
ok("the old 'Rail head' spelling is read as 'Railhead'",
  /const locTypeOf = \(t\) => \(t === "Rail head" \? "Railhead" : t\)/.test(code)
  && (code.match(/locTypeOf\(p\.loc_type\)/g) || []).length >= 2);
ok("...and appears nowhere else in the app", (code.match(/"Rail head"/g) || []).length === 1);
ok("a deactivated gate is shown as deactivated rather than just missing",
  src.includes("deactivated"));

ok("routes can select a gate at each end", code.includes("RouteGates")
  && code.includes("origin_gate_id") && code.includes("dest_gate_id"));
ok("'no explicit choice' is an offered option, not an empty select",
  src.includes("Default for this site"));
ok("B2: a blocked route says so on the collapsed row, not only when expanded",
  src.includes("gate blocked") && code.includes("r.gate_blockers"));
ok("and the blocker text names what is wrong",
  src.includes("This route will not bake"));
ok("changing a gate warns that the cached geometry is now stale",
  src.includes("cached geometry now points at the old gate"));

ok("the turnaround cell reads the backend's total, never its own sum",
  code.includes("r.turnaround_hr") && !code.includes("load_minutes + r.unload_minutes"));
ok("the turnaround breakdown comes from the backend's parts",
  code.includes("r.turnaround_parts"));
ok("the tooltip explains a drawn-road cycle rather than hiding the missing allowance",
  src.includes("not a flat allowance"));
ok("the 125% lesson is recorded where the total is displayed",
  src.includes("125%"));
ok("the factors panel no longer calls its load+unload figure 'turnaround'",
  !code.includes("v.turnaround_hr") && code.includes("v.unloading_hr"));

// ---- report ------------------------------------------------------------------
console.log();
for (const f of fail) console.log("  FAIL:", f);
console.log(`\n${pass} passed, ${fail.length} failed`);
process.exit(fail.length ? 1 : 0);

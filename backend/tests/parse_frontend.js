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
// 🔴 09 Sep night: the page shipped with TWO `function Kpi` declarations. TypeScript's
// transpile and `new Function()` (sloppy mode) both accept a redeclared function; the
// browser's Babel does not, and the app rendered a blank white page. Every top-level
// name must be declared exactly once.
const _decls = [...src.matchAll(/^(?:function|const|let|class)\s+([A-Za-z_$][\w$]*)/gm)].map(m => m[1]);
const _dups = _decls.filter((n, i) => _decls.indexOf(n) !== i);
ok("🔴 no top-level name is declared twice (Babel refuses it; the harness alone did not)",
  _dups.length === 0, "duplicates: " + [...new Set(_dups)].join(", "));
// and, when @babel/standalone is on this machine, compile exactly as the browser does
const _babelPaths = ["/tmp/babelcheck/node_modules/@babel/standalone/babel.min.js",
                     "/tmp/shot/node_modules/@babel/standalone/babel.min.js"];
const _babel = _babelPaths.find(p => fs.existsSync(p));
if (_babel) {
  try {
    const Babel = require(_babel);
    const out = Babel.transform(src, { presets: ["react"], filename: "index.html" });
    ok("⭐ @babel/standalone compiles the page the way the browser does", out.code.length > 1000);
  } catch (e) {
    ok("⭐ @babel/standalone compiles the page the way the browser does", false, e.message.split("\n")[0]);
  }
} else {
  ok("(@babel/standalone not installed here — the browser-compile check did not run; `npm i @babel/standalone@7.24.7` in /tmp/babelcheck to enable it)", true);
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
  code.includes('title={g.vehicle}>{vehLabel(g.vehicle)}'));
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
// ⭐ 2026-09-03, the human's correction: MOVEMENTS and VEHICLES are different numbers.
// A movement is one load carried one way; how many a single vehicle can make per day is
// a property of the ROUTE (the baked HERE cycle), so vehicles needed = movements/day ÷
// that figure, rounded UP. The earlier "trips == vehicles" cards are gone.
for (const card of ["Avg movements / day", "Peak movements / day", "Vehicles needed (avg)", "Vehicles needed (peak)"]) {
  ok(`the range strip carries '${card}'`, code.includes(`"${card}"`));
}
ok("⭐ no card claims trips equal vehicles any more",
  !code.includes("Avg vehicles / day") && !code.includes("Trips equal vehicle loads"));
ok("⭐ trips-per-vehicle-per-day comes from the route's baked cycle, the same source the dashboard uses",
  code.includes("/analysis?profile=") && /setTpv\(row && row\.trips_per_day > 0 \? row\.trips_per_day : null\)/.test(code));
ok("⭐ vehicles needed = movements ÷ trips-per-vehicle, rounded UP",
  /Math\.ceil\(movesPerDay \/ tripsPerVehicleDay\)/.test(code));
ok("⭐ an unbaked route reads 'not baked', never a fleet size",
  code.includes('"not baked" : String(fleetOf(') && code.includes("not baked yet for"));
ok("the dashboard's fleet column uses the same model", code.includes("Math.ceil(peakPerDay / a.capacityPerDay)"));
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

// ---- 4d. Tasks C + D — the Look-ahead page ------------------------------------
// 2.5b: a rail entry now, not a tab. The nav entry and the render are checked
// separately because a page that is listed but not rendered is a blank screen.
ok("there is a Look-ahead nav entry",
  /\{ id: "lookahead", label: "Look-ahead"/.test(code));
ok("...in the Track group, on its own",
  /group: "Track", items: \[\s*\{ id: "lookahead"/.test(code));
ok("...and it renders the LookAhead component",
  /page === "lookahead" && <LookAhead/.test(code));
// ⚠️ Visible to submitters too. /api/forecast-weeks is filtered per IPT on the server
// since Task F, so the boundary is there, not in the nav. Asserted so nobody "fixes"
// this into a second, inconsistent permission.
ok("⭐ the Track group is NOT admin-gated",
  !/group: "Track", admin/.test(code));
// Look-ahead v2 (09 Sep evening): the page reads ONE endpoint and the server decides
// every window. Reversed from "current month + next" — the browser no longer does
// month arithmetic for the look-ahead at all.
const _laBody = (() => {
  const i = code.indexOf("function LookAhead(");
  const j = code.indexOf("function RoutePlanning(", i);
  return i >= 0 && j > i ? code.slice(i, j) : "";
})();
ok("LookAhead is findable in the source", _laBody.length > 1000);
ok("⭐ the window comes from the server's horizon block, not the browser clock",
  _laBody.includes("horizon.from_month") && !/new Date\(\)\.getMonth/.test(_laBody)
  && !/months\.count, from \+ 1/.test(_laBody));
// ⭐ which week is the commit week comes from the SERVER; the browser only picks the bucket
ok("⭐ the commit week comes from the server's commit_week, through /lookahead?bucket=",
  _laBody.includes("fetch(`${API}/lookahead?bucket=${bucket}`)") && _laBody.includes("page.commit_week")
  && !_laBody.includes("payload.next_week"));
ok("...and the bucket switch offers exactly this week and next week — the Thursday process",
  /\[\["commit", "this week"\], \["next", "next week"\]\]/.test(_laBody));
ok("a planned day is editable while its week is not confirmed, this bucket or next",
  /const locked = w\.status === "confirmed"/.test(_laBody) && /\{!locked \? \(/.test(_laBody));
ok("a confirmed week's plan is read-only", code.includes("Confirmed — reopen it to change the plan"));
ok("⭐ Confirm week is ONE button for the whole week, separate from editing (L6)",
  _laBody.includes(">Confirm week<") && _laBody.includes("/forecast-weeks/confirm")
  && /const doConfirm = async \(\) => \{[\s\S]*?for\(const l of lines\)/.test(_laBody));
// scoped to doConfirm's own body: a `break` after the first line would confirm ONE line
// and still contain the loop, so the loop alone proved nothing (regression F2 was green)
const _doConfirmBody = (() => { const i = _laBody.indexOf("const doConfirm ="); const j = _laBody.indexOf("const doReopen =", i); return i >= 0 && j > i ? _laBody.slice(i, j) : ""; })();
ok("⭐ ...and doConfirm walks EVERY unconfirmed line — the only break is the failed-request one",
  _doConfirmBody.length > 0 && (_doConfirmBody.match(/break;/g) || []).length === 1 && _doConfirmBody.includes("if(!j) break;"));
ok("...and a confirmed week can be re-opened, through its own endpoint, deleting nothing",
  _laBody.includes("/forecast-weeks/reopen") && _laBody.includes("Nothing is deleted"));
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
// C18, decided: the per-line "spread on this week" button (the Account mock), not a
// dialog checkbox. Still the ONLY thing that reaches calibrate.
ok("⭐ calibrate is reached only from the per-line 'spread on this week' button",
  (code.match(/\/forecast-weeks\/calibrate/g) || []).length === 1
  && code.includes("spread on this week") && /const doSpread = \(row\) => send\("\/forecast-weeks\/calibrate", \{ \.\.\.wkBody\(row\), spread: true/.test(_laBody));
ok("...and the button says the server refuses it when that week is confirmed",
  _laBody.includes("Refused if that week is confirmed"));
// reversed: the calibrate DIALOG is gone with C18 — there is no typed override in the UI
// (the endpoint keeps it; nothing in the page sends override_qty)
ok("the calibrate dialog and its typed override are gone from the page (C18: per-line button)",
  !_laBody.includes("override_qty") && !_laBody.includes("leave blank to use the formula")
  && !_laBody.includes("setCalibrating"));
ok("the four action states of the Account mock are all rendered: held · spread · applied · waiting",
  ['r.action === "held"', 'r.action === "spread"', 'r.action === "applied"', 'r.action === "waiting"'].every(t => _laBody.includes(t)));
ok("variance is blank rather than 0 until an actual is typed",
  /r\.variance == null \? "—"/.test(code));
ok("an emptied actual box clears it back to null, not to zero",
  /actual_qty: \(v === "" \|\| v == null \? null/.test(code));
ok("a week whose parent month changed says so",
  code.includes("parent month changed") && code.includes("parent_changed"));
ok("reopening a month is shown as a note, not by hiding its weeks",
  code.includes("month is now"));

// ---- 4d2. Look-ahead v2 slices 3-6 — the page (09 Sep evening) ------------------
ok("three pills, in the mock's order, and the page lands on Commit",
  /pill\("account", "Account"\)/.test(_laBody) && /pill\("commit", /.test(_laBody) && /pill\("horizon", "Horizon"\)/.test(_laBody)
  && /localStorage\.getItem\("rbe_la_view"\) \|\| "commit"/.test(_laBody));
ok("the pills sit under PageHeader — the left rail stays (C18's unsettled point, decided)",
  /<PageHeader title="Look-ahead"/.test(_laBody) && !_laBody.includes("Dashboard · Submit Forecast"));
ok("the Commit KPI strip carries the mock's cards: planned, trips, vehicles peak, t·km, last-week shortage, last-week delivered — and planned €",
  ["planned this week", "trips", "vehicles / day peak", "t·km", "last week shortage", "last week delivered", "planned €"]
    .every(t => _laBody.includes(t)));
ok("🔴 the word PPC is printed nowhere (L5)", !/\bPPC\b/.test(code));
ok("⭐ the clash rail is ONE row with '+N more', not a stack",
  _laBody.includes("rail.flags.slice(0, 3)") && _laBody.includes("more`") && !_laBody.includes("dismiss"));
// 🔴 09 Sep night: the LIVE Tark Tee check froze the page on the critical path, and off
// it still took 30 s+. 10 Sep: the page reads the STORED per-route check (the rail carries
// it), and the live check runs on demand in the background — a "check now / re-check"
// button that polls until it finishes. A route never checked is named, never shown clean.
ok("🔴 the page never fetches live Tark Tee — the refresh is a POST the person triggers, then a poll",
  !/lookahead\?bucket=\$\{bucket\}&tark_tee=0/.test(_laBody)
  && _laBody.includes('fetch(`${API}/forecast-weeks/tark-tee/refresh`, { method: "POST" })')
  && /fetch\(`\$\{API\}\/forecast-weeks\/tark-tee\?bucket=\$\{bucket\}`\)/.test(_laBody)
  && _laBody.includes("setTimeout(tickPoll, 4000)"));
ok("...the status line says checked-when, partial-with-names, or NOT checked — never a silent clean",
  _laBody.includes("Road restrictions (Tark Tee): checked") && _laBody.includes("not checked since baking")
  && _laBody.includes("NOT checked for these routes") && _laBody.includes("check now"));
ok("...and the rail's flags come from the page read alone (no second rail, no client-side merge)",
  /const rail = \(page && page\.clashes\) \|\| \{ flags: \[\], count: 0, sources: \{\} \};/.test(_laBody)
  && !_laBody.includes("...(tt.flags || [])"));
// Mon–Fri only (10 Sep, the human)
ok("⭐ the Commit grid shows Mon–Fri only — weekend days are filtered out of the columns and the cells",
  /\.filter\(iso => !isWeekend\(iso\)\)/.test(_laBody) && _laBody.includes("const weekdaysOf = (line) =>")
  && _laBody.includes("{weekdaysOf(line).map(d => {") && !_laBody.includes("{line.days.map(d => {"));
ok("...and the footer says so", _laBody.includes("Mon–Fri only — Sat/Sun are held at 0 and not shown"));
// calendar weeks (10 Sep): nothing in the browser assumes four weeks a month
ok("🔴 the four-week constant and the 1–7 / 8–14 labels are gone; the Horizon asks the server",
  !/const WEEK_NOS = \[1, 2, 3, 4\]/.test(code) && !code.includes('"8–14"')
  && /const weekNosOf = \(horizon, m\) =>/.test(code) && /weekNosOf\(horizon, m\)\.forEach\(w => cols\.push/.test(_laBody)
  && /W\{w\} · \{weekSpanLbl\(horizon, m, w\)\}/.test(_laBody) && !_laBody.includes("derived ÷4<"));
// the Commit week map (10 Sep): its own small read, one map instance, never the public map in a frame
const _cmBody = code.slice(code.indexOf("function CommitMap("), code.indexOf("function RoutePlanning("));
ok("the Commit map reads /lookahead/geometry with the page's route ids — never the page again, never /public/map-data",
  _cmBody.includes("/lookahead/geometry?ids=") && !_cmBody.includes("/lookahead?") && !_cmBody.includes("map-data") && !_cmBody.includes("<iframe"));
ok("...creates ONE map and keeps it (the guard), and never draws an unbaked route as a line",
  _cmBody.includes("if(mapObj.current || !mapDiv.current || !window.mapboxgl) return;")
  && /if\(r\.geometry && r\.geometry\.length > 1\)\{/.test(_cmBody) && _cmBody.includes("Not drawn (no baked route)"));
ok("...colours lines by IPT from palette C, and is not rendered under the render harness",
  _cmBody.includes("IPT_PALETTE_C[iptKey(") && _laBody.includes("!initialPage && <CommitMap"));
ok("today's column carries the wash and the '· today' suffix",
  _laBody.includes('" · today"') && _laBody.includes('#eff6ff'));
ok("the expanded row prints km/trip, km/week, t·km, cycle and € or 'rate not set'",
  _laBody.includes("km/trip") && _laBody.includes("t·km (") && _laBody.includes("cycle {grp(c.cycle_min)} min")
  && _laBody.includes('"rate not set"'));
ok("⭐ the honest-gap sentence for an unbaked line ships as written",
  _laBody.includes("km — until the route is baked · trips still from"));
ok("an unbaked line shows trips and NO vehicle count (the human's rule), never a 1",
  _laBody.includes("veh —") && _laBody.includes("Vehicles need a baked route"));
ok("the expand trigger is the Routes table's ▶ toggle, reused",
  _laBody.includes('{isOpen ? "▼" : "▶"}'));
ok("the empty state uses EmptyState", _laBody.includes("<EmptyState title={`No approved forecast line in the commit week"));
ok("planned days are saved through PUT /forecast-days with the day's key",
  /send\("\/forecast-days", \{ route_id: line\.route_id, month_index: line\.month_index,[\s\S]*?day_date: d\.day_date, planned_qty:/.test(_laBody)
  && _laBody.includes('"PUT"'));
// slice 4 — export
ok("⭐ export downloads through fetch + blob, never a bare <a href> (the access code is a header)",
  /fetch\(`\$\{API\}\/forecast-weeks\/export\?format=\$\{format\}&bucket=\$\{bucket\}`\)/.test(_laBody)
  && _laBody.includes("URL.createObjectURL(blob)") && !/href=\{`\$\{API\}\/forecast-weeks\/export/.test(code));
ok("...the mock's bar: Export XLSX beside the green Confirm week, PDF as a smaller second button",
  _laBody.includes(">Export XLSX<") && _laBody.includes('download("pdf")') && _laBody.includes('background: "var(--green)" }}>{anyConfirmed'));
ok("no email and no upload in the page", !/mailto:|sendEmail|<input type="file"/.test(_laBody));
// slice 5 — rates and the confirmed cost
ok("⭐ the confirmed cost is sent ONLY when its box was touched — an absent field leaves it alone",
  /if\(draft\[kc\] !== undefined\) body\.actual_cost_eur =/.test(_laBody)
  && !/actual_cost_eur: draft\[kc\] \?\?/.test(_laBody));
ok("the Account table has Planned €, Actual € and € var columns",
  _laBody.includes(">Planned €<") && _laBody.includes(">Actual €<") && _laBody.includes(">€ var<"));
ok("the route form gained a RoutePlanning block with the three rates, the cap and the km basis",
  /function RoutePlanning\(/.test(code) && /<RoutePlanning route=/.test(code)
  && code.includes("/planning${qs}") && ["rate_eur_per_load", "rate_eur_per_t", "rate_eur_per_km", "max_vehicles_per_day", "km_basis"].every(k => code.includes(k))
  && code.includes('<option value="round_trip">') && code.includes('<option value="loaded">'));
ok("🔴 no default rate anywhere in the page — blank means 'rate not set'",
  !/rate_eur_per_\w+: \d/.test(code) && code.includes('Blank = no rate'));
// palette C — the IPT pills reuse the map's six hexes
const _mapSeg = fs.readFileSync(path.join(ROOT, "map", "ipt_segments.js"), "utf8");
const _mapHex = {};
for (const m of _mapSeg.matchAll(/ipt: 'IPT (\d)'[^\n]*colour: '(#[0-9A-Fa-f]{6})'/g)) _mapHex["IPT" + m[1]] = m[2];
const _pageHex = (() => { const m = code.match(/const IPT_PALETTE_C = \{([^}]*)\}/); const o = {};
  if (m) for (const x of m[1].matchAll(/(IPT\d): "(#[0-9A-Fa-f]{6})"/g)) o[x[1]] = x[2]; return o; })();
ok("⭐ the IPT pills use palette C — the same six hexes the public map paints the corridor with",
  Object.keys(_mapHex).length === 6 && ["IPT1", "IPT2", "IPT3", "IPT4", "IPT5", "IPT6"].every(k => _pageHex[k] && _pageHex[k].toUpperCase() === _mapHex[k].toUpperCase()));
// the Horizon view
ok("the Horizon columns carry a ROLE label — account · commit · make-ready · early warning",
  ["ACCOUNT", "COMMIT", "MAKE-READY", "EARLY WARNING"].every(t => _laBody.includes(`"${t}"`)) && _laBody.includes("horizon.roles"));
ok("...and no Confirm button lives on the Horizon view",
  (() => { const i = _laBody.indexOf("{horizon.rows.length === 0"); const j = _laBody.indexOf("{confirming && (", i);
           const h = i > 0 && j > i ? _laBody.slice(i, j) : ""; return h.length > 0 && !h.includes("Confirm week") && h.includes("No day columns here"); })());
ok("the Horizon footer states the weeks-not-days rule and the blue-column rule",
  _laBody.includes("stay week totals so hauliers are not sent a four-week daily spreadsheet") && _laBody.includes("That is the only week that materialises daily rows"));

let _dsBody;
// ---- 4e. Task D2 — stock held (moved on 10 Sep) --------------------------------------
// The W1–W4 consumption grid is GONE from the Look-ahead. Consumption is typed on the
// Account view for the account week; the read-only balances are on the Dashboard.
ok("🔴 the old Stockpiles grid component no longer exists",
  !/function Stockpiles\(/.test(code) && !/<Stockpiles /.test(code));
ok("the Dashboard carries the read-only stock panel",
  /function DashboardStock\(/.test(code) && /<DashboardStock meta=\{meta\} \/>/.test(code)
  && /function Dashboard\(/.test(code) && code.indexOf("<DashboardStock") > code.indexOf("function Dashboard(")
  && code.indexOf("<DashboardStock") < code.indexOf("function App("));
ok("...and it types nothing: no consume call, no input, in that component",
  !(_dsBody = code.slice(code.indexOf("function DashboardStock("), code.indexOf("function App("))).includes("/stockpiles/consume")
  && !_dsBody.includes("<input"));
ok("consumption is typed on the ACCOUNT view through /stockpiles/consume, for the account week",
  _laBody.includes("/stockpiles/consume") && /month_index: account\.week\.month_index,\s*week_index: account\.week\.week_index/.test(_laBody));
ok("...one box per stockpile, and an emptied box is null (not typed), not zero",
  /consumed_qty: \(v === "" \? null : numOr\(v, null\)\)/.test(_laBody) && _laBody.includes("account.stock.map("));
ok("...and the balance is read from /stockpiles", code.includes("/stockpiles?from_month="));
// ⭐ no capacity recorded is NOT zero capacity
ok("⭐ an unset capacity renders '—' / 'not recorded', never 0",
  /sp\.capacity_qty == null \? "—"/.test(code)
  && /cell\.remaining == null \? "no cap"/.test(code)
  && /s\.capacity_qty == null \? <span className="text-slate-300">not recorded<\/span>/.test(_laBody));
ok("over capacity uses the existing red, not a reserved route colour",
  /cell\.over \? "var\(--red\)"/.test(code)
  && !/cell\.over \? "#039E86"/.test(code));
ok("the panel says inbound comes from typed actuals only",
  code.includes("a week nobody") && code.includes("reported counts as nothing"));
ok("🔴 the Commit view's stock card is gone — the week map stands in its place",
  !_laBody.includes("Stock held") && _laBody.includes("<CommitMap ") && /function CommitMap\(/.test(code));
ok("🔴 the word 'pile' never appears on its own — always 'stockpile' (the human, 10 Sep)",
  !/(?<![Ss]tock)\b[Pp]iles?\b/.test(code));
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
ok("a line with no IPT is flagged on the Forecasts page as planner-only",
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
ok("a Zones page exists", /\{ id: "zones", label: "Zones"/.test(code));
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

// ---- 8. 2.5b: the page architecture -------------------------------------------
// The left rail replaced the tab row, My submissions and Approvals became one page,
// routes became editable and factors.json became editable. Everything here is
// source-level: there is no browser in this harness, so what can be proved is that the
// wiring exists and that the two things that would break silently — the shared Mapbox
// instance and the scope of a withdraw — are still right.

// 8a. the rail
ok("the nav is four groups in order: Plan, Track, Data, Map",
  (code.match(/group: "(Plan|Track|Data|Map)"/g) || []).join(",") === 'group: "Plan",group: "Track",group: "Data",group: "Map"');
ok("⭐ only the Data group is admin-gated", /group: "Data", admin: true/.test(code)
  && (code.match(/admin: true/g) || []).length === 1);
ok("an admin-only page falls back to the dashboard rather than a blank screen",
  code.includes("if(!allowed.has(page)) setPage(\"dashboard\")"));
ok("the rail collapses and the page survives a refresh",
  code.includes('localStorage.getItem("rbe_page")') && code.includes('localStorage.setItem("rbe_rail"'));
ok("the pending count badges Forecasts for approvers only",
  code.includes('it.id === "forecasts" && canApprove && pending > 0'));

// 8b. ⭐ ONE Mapbox instance across Locations / Routes / Zones
// Three nav entries, one component. Rendering DataManagement in three separate
// branches would unmount and remount it on every switch — the map would reload its
// style, lose its sources and jump back to the default viewport. This is the
// assertion that catches that.
ok("⭐ Locations/Routes/Zones render exactly ONE <DataManagement>",
  (code.match(/<DataManagement/g) || []).length === 1);
ok("...selected by DATA_PAGES, not by three separate branches",
  code.includes("DATA_PAGES.includes(page) && canApprove") &&
  /const DATA_PAGES = \["locations", "routes", "zones"\]/.test(code));
ok("...and the page reaches it as a prop", /<DataManagement meta=\{meta\} who=\{who\} subTab=\{page\}/.test(code));
ok("the old in-component sub-tab bar is gone",
  !code.includes('["zones", "Zones", zoneList.length]'));
// A map click that opens a node, or "create a route from here", still switches page
// from inside the component. Each of those must tell the rail too, or the rail and the
// panel disagree about which page you are looking at.
ok("⭐ an internal page switch tells the rail as well",
  code.includes("const setSubTab = (t) => { setSubTabState(t); if(onSubTab) onSubTab(t); }"));
ok("...and a page change from the rail is mirrored inward",
  /if\(propSubTab && propSubTab !== subTab\) setSubTabState\(propSubTab\)/.test(code));

// 8c. Forecasts — My submissions and Approvals, merged
ok("there is one Forecasts page, not two lists",
  code.includes("function Forecasts({ meta, who, access, onEdit, onChanged })")
  && !code.includes("function MySubmissions") && !code.includes("function Approvals("));
ok("lines group per route + discipline + section, not per month",
  code.includes('const key = [r.route_id, disc, sect].join("|")'));
ok("a group spanning several statuses reads Mixed rather than picking one",
  code.includes('e.statuses.size === 1 ? [...e.statuses][0] : "Mixed"'));
ok("⭐ withdraw is scoped to the months the row shows, never 1..count",
  code.includes('from: String(g.minMonth), to: String(g.maxMonth)'));
ok("⭐ a submitter withdraws only its own lines; an approver is not narrowed",
  code.includes('if(!canApprove) p.set("submitted_by", who)'));
ok("⭐ a rejection without a reason is refused, not stored blank",
  code.includes('A rejection needs a reason — nothing was changed.'));
ok("edit hands back the whole year span, not one year",
  /onEdit\(\{ routeId: g\.routeId, year: g\.fromYear, toYear: g\.toYear/.test(code));
ok("approve/reject are shown only to approvers",
  (code.match(/canApprove && g\.status !== "(Approved|Rejected)"/g) || []).length === 2);

// 8d. Config — the editable copy of factors.json
ok("the Config page saves the whole document to the admin endpoint",
  code.includes("`${API}/admin/config/factors${qs}`") && code.includes('method: "PUT"'));
ok("...and can put the file back", code.includes("admin/config/factors/reset"));
ok("⭐ it says the file is only the seed once the row exists",
  src.includes("Reset to file") && src.includes("differs from the file in the repo"));
ok("a raw JSON tab exists for what the forms do not cover",
  code.includes('["raw", "Everything (JSON)"]'));
ok("bad JSON in the raw tab is refused before it is sent",
  code.includes('setRawErr("Not valid JSON: "'));
ok("the vehicle id is not editable — baked geometry is keyed on it",
  src.includes("it cannot be renamed here"));

// 8e. route editing in place
ok("a route row can be edited", code.includes("startEditRoute") && code.includes("saveRouteEdit"));
ok("⭐ the impact is fetched and confirmed BEFORE the write",
  code.indexOf("/impact") < code.indexOf('method: "PATCH"') && code.includes("Continue?"));
ok("the write is a PATCH of the changed fields, not a delete and re-create",
  code.includes('method: "PATCH"') && !code.includes("recreateRoute"));
ok("the route id itself is not editable while editing",
  code.includes("disabled={!!editingRoute}"));

// 8e-ter. 2026-09-09: changing which VEHICLES a route is baked for.
//
// The reported bug was "edit route cannot change vehicle", and the guard that caused it
// was deliberate -- the panel hid the tick-boxes whenever editingRoute was set. Removing
// a guard is exactly the kind of change that leaves no textual trace if it regresses, so
// the retired guard is asserted ABSENT rather than the new behaviour merely asserted
// present. Reversed from what it said before this shipped; never delete an assertion.
ok("🔴 the vehicle tick-boxes are no longer hidden while editing a route",
  !code.includes("{!editingRoute && ("));
ok("...and the panel says what ticking and unticking now MEAN while editing",
  src.includes("tick to bake, untick to remove"));

// The edit set is separate state. Sharing rVehicles would have been one line shorter and
// would have silently re-pointed the network-wide Bake all button at whatever the route
// under edit happens to carry -- while its own tooltip still said the list came from the
// Create panel.
ok("⭐ the edited vehicle set is its OWN state, not rVehicles",
  code.includes("const [eVehicles, setEVehicles]") && code.includes("const toggleEVehicle"));
{
  const i = code.indexOf("const bulkBake = async");
  const j = code.indexOf("const clearRoutes", i);
  const body = i >= 0 && j > i ? code.slice(i, j) : "";
  ok("🔴 bulk bake still reads rVehicles and never the edit set",
    body.includes("rVehicles.length ? rVehicles") && !body.includes("eVehicles"));
}

{
  const i = code.indexOf("const saveRouteEdit = async");
  const j = code.indexOf("const deleteRoute = async", i);
  const body = i >= 0 && j > i ? code.slice(i, j) : "";
  ok("the save computes BOTH directions of the vehicle diff against what is cached",
    body.includes("Object.keys(cur.profiles || {})")
    && /const add = eVehicles\.filter/.test(body)
    && /const drop = had\.filter/.test(body));
  // A move clears every profile server-side. Restoring affected.profiles afterwards --
  // which is what this did before the diff existed -- would bring back a profile the
  // user had just unticked, and the route list would show it as though nothing happened.
  ok("🔴 a moved route re-bakes the NEWLY TICKED set, not the profiles it used to have",
    body.includes("const toBake = moved ? eVehicles : add") && !body.includes("rebakeAffected"));
  // Without route_id this call clears the profile from EVERY route in the network. The
  // parameter is the whole point of the backend half of this change.
  ok("🔴 dropping a vehicle clears geometry for THIS ROUTE only",
    /clear-geometry/.test(body)
    && /route_id: editingRoute, profile: prof/.test(body));
  ok("the confirm names the forecast lines a dropped vehicle leaves reading 'not baked'",
    body.includes("forecast_vehicles") && body.includes("not baked"));
  ok("...and warns when the last vehicle is being removed",
    body.includes("NO baked vehicle at all"));
}
ok("each tick-box says whether it will be baked, removed, or is already baked",
  code.includes('"will be baked"') && code.includes('"will be removed"'));
ok("the Save button shows the vehicle change before the confirm dialog does",
  code.includes("Save changes · "));

// 8e-bis. the names the UI calls things
// Renaming a page and leaving its old name in a hint is exactly the dangling-text
// failure that source-level assertions exist to catch; it has bitten twice already.
ok("⭐ no visible text still points at the retired 'Data Management tab'",
  !src.includes("Data Management tab") && !/in Data\s+Management/.test(src));
ok("...and nothing calls the data pages 'sub-tabs' to the user",
  !/sub-tab/i.test(src.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, "")));

// 8f. the shared page furniture
ok("one PageHeader component, and every portal page uses it",
  (code.match(/function PageHeader\(/g) || []).length === 1
  && (code.match(/<PageHeader/g) || []).length >= 6);
// The layout pass is only worth anything if it is the ONLY way a page titles itself.
// A page that hand-rolls its own heading drifts on font size, spacing and colour, which
// is exactly what 2.5b was asked to stop.
ok("⭐ no portal page hand-rolls its own heading any more",
  !/<h1 className="text-lg font-bold/.test(code) && !/<h2 className="text-lg font-bold/.test(code));
ok("there is a shared empty state", code.includes("function EmptyState("));

// ---- 4f. 10 Sep evening — the fuel widget, target rate and Quote + BAF ---------------
const _fwBody = code.slice(code.indexOf("function FuelWidget("), code.indexOf("function LookAhead("));
const _ctBody = code.slice(code.indexOf("function CostingTab("), code.indexOf("function ConfigPage("));
ok("⭐ ONE FuelWidget component, mounted THREE times: Commit (compact), Account (strip), Config (config)",
  (code.match(/function FuelWidget\(/g) || []).length === 1 && (code.match(/<FuelWidget\b/g) || []).length === 3
  && /<FuelWidget mode="compact"/.test(_laBody) && /<FuelWidget mode="strip"/.test(_laBody) && /<FuelWidget mode="config"/.test(_ctBody));
ok("...the widget reads /fuel-index and never the feed host; it names the bulletin in its attribution",
  _fwBody.includes("fetch(`${API}/fuel-index?lazy=") && !/eurooilwatch\.com/i.test(code)
  && _fwBody.includes("EU Weekly Oil Bulletin via EuroOilWatch"));
ok("...settings go to PUT /fuel-index/settings; manual index, refresh and reset-base are ADMIN calls with the token",
  _fwBody.includes('put("/fuel-index/settings"') && _fwBody.includes('"/admin/fuel-index/manual"')
  && _fwBody.includes('"/admin/fuel-index/refresh?sync=1"') && _fwBody.includes("/admin/fuel-index/reset-base?")
  && /token=\$\{encodeURIComponent\(token\)\}/.test(_fwBody));
ok("...the yard and share boxes are typeable by approvers only, and an emptied box sends null (not 0)",
  _fwBody.includes("access.can_approve") && /const v = draft\[k\] === "" \? null : numOr\(draft\[k\], null\)/.test(_fwBody));
ok("...it says 'Index unavailable' with no price, 'stale' after 8 days, and why there is no BAF",
  _fwBody.includes("Index unavailable") && _fwBody.includes(">stale<") && _fwBody.includes("set a base on Confirm week")
  && _fwBody.includes("type a fuel share"));
ok("...and it polls `refresh` while the server's background fetch runs — never blocks on it",
  _fwBody.includes("rf.running") && _fwBody.includes("setTimeout(() => load(false).then(poll), 3000)"));
ok("🔴 nothing is seeded in the page: no 25 % share and no €1.9x price as a default",
  !/share_pct[^\n]{0,40}25\b/.test(_fwBody) && !/1\.9\d/.test(_fwBody) && !/1\.9\d/.test(_ctBody));
ok("the Commit KPI strip is eight cards with the widget right of t·km, and the planned € caption counts target lines and shows + BAF",
  _laBody.includes("xl:grid-cols-8") && _laBody.indexOf('caption="t·km · route basis"') < _laBody.indexOf('<FuelWidget mode="compact"')
  && _laBody.indexOf('<FuelWidget mode="compact"') < _laBody.indexOf("planned € · no rate typed")
  && _laBody.includes("at target") && _laBody.includes("+ BAF € ${grp(totals.eur_adj)}"));
ok("...the expanded Commit row and the Account cell print '+ BAF' as a SECOND figure and mark a target-priced line",
  _laBody.includes("+ BAF € ${grp(wd.eur_adj)}") && _laBody.includes("+ BAF € {grp(r.planned_eur_adj)}")
  && _laBody.includes('r.rate_source === "target"') && _laBody.includes('c.rate_source === "target"'));
ok("...and the Account footer says the quote is never replaced and € var is against the quote",
  _laBody.includes("the quote is never replaced") && _laBody.includes("€ var compares the actual against the quote"));
ok("the Account strip is mounted above the KPI grid; the widget is passed the page's own costing block",
  _laBody.indexOf('<FuelWidget mode="strip"') < _laBody.indexOf('caption="last week delivered · 98% band"')
  && (_laBody.match(/initial=\{page && page\.costing\}/g) || []).length === 2);
ok("Config has a 'Costing · fuel' tab whose target rate saves to PUT /admin/costing/target with the admin token — not inside the factors document",
  code.includes('["costing", "Costing · fuel"]') && _ctBody.includes("/admin/costing/target${qs}")
  && !_ctBody.includes("admin/config/factors") && _ctBody.includes("Save target rate"));
ok("...three target inputs; a blank sends null (clears), and the copy says target is used ONLY where the route has no rate",
  (_ctBody.match(/rate_eur_per_(load|t|km)/g) || []).length >= 6 && _ctBody.includes('if(draft[k] === ""){ body[k] = null; return; }')
  && _ctBody.includes("Used ONLY for routes with no rate typed on the route form"));
ok("the route form says a typed rate is used alone and never mixed with the target",
  code.includes("A typed rate here is used ALONE; it is never mixed with the target."));
ok("LookAhead now receives the role (for the widget's typeable boxes)", /<LookAhead meta=\{meta\} who=\{who\} access=\{role\} \/>/.test(code));
ok("🔴 no fuel on the public map: map/index.html has no widget, no fuel-index, no BAF",
  !/FuelWidget|fuel-index|\bBAF\b/.test(fs.readFileSync(path.join(__dirname, "..", "..", "map", "index.html"), "utf8")));

// ---- 4g. 10 Sep night — the fair-price model on the page -------------------------------
const _fpBody = code.slice(code.indexOf("const FAIR_FIELDS = "), code.indexOf("function ConfigPage("));   // the field list sits just above the component
ok("⭐ ONE FairPriceSection, mounted once inside the Costing tab, editing doc.fair_price through the page's upd()",
  (code.match(/function FairPriceSection\(/g) || []).length === 1 && (code.match(/<FairPriceSection\b/g) || []).length === 1
  && _ctBody.includes("<FairPriceSection doc={doc} upd={upd} fileDoc={fileDoc}") && _fpBody.includes("d.fair_price = JSON.parse(JSON.stringify(fileFp))"));
ok("...it saves through the factors document (the page's Save + admin token), never its own endpoint",
  !_fpBody.includes("fetch(") && _fpBody.includes("Saved with the page's Save button (admin token)"));
ok("...the four money coefficients, the three consumption figures, the uplift and both month pickers are editable",
  ["driver_eur_per_h", "vehicle_standing_eur_per_h", "running_eur_per_km", "margin_pct"].every(k => _fpBody.includes(`"${k}"`))
  && _fpBody.includes("l_per_100km_per_tonne") && _fpBody.includes("f.consumption.rigid.l_per_100km_empty") && _fpBody.includes("f.consumption.artic.l_per_100km_empty")
  && _fpBody.includes("winter_consumption_uplift_pct") && _fpBody.includes('monthsBox("winter_months"') && _fpBody.includes('monthsBox("thaw_months"'));
ok("...and says which three are assumptions", (_fpBody.match(/ASSUMPTION/g) || []).length === 3);
ok("...a live document without the block shows the file's values and says so",
  _fpBody.includes("const fp = (doc && doc.fair_price) || fileFp") && _fpBody.includes("the live document has no fair_price block yet"));
ok("the Look-ahead prints the fair figure as a THIRD figure, marked 'model': KPI caption, expanded row, Account column",
  _laBody.includes("fair € ${grp(totals.fair_eur)} (model") && _laBody.includes("fair € {grp(wd.fair_eur)}") && _laBody.includes("Fair € <span")
  && _laBody.includes("€ {grp(r.planned_fair_eur)}"));
ok("...with the winter and thaw flags on the Account cell and the expanded row",
  _laBody.includes('(r.fair_flags || []).includes("WINTER")') && _laBody.includes('(r.fair_flags || []).includes("THAW")')
  && _laBody.includes('c.fair.flags.includes("THAW") ? ", thaw restrictions may apply"'));
ok("...an unbaked line prints — with the reason, never a number", _laBody.includes("No fair price: the route is not baked for this vehicle, or the diesel index is missing"));
ok("...and the footer calls it a floor for negotiation, never a quote", _laBody.includes("a floor for negotiation, never a quote"));
ok("the Config page hands the file document to the tab (for the fallback values)", code.includes("setFileDoc(j.file || null)"));

// ---- report ------------------------------------------------------------------
console.log();
for (const f of fail) console.log("  FAIL:", f);
console.log(`\n${pass} passed, ${fail.length} failed`);
process.exit(fail.length ? 1 : 0);

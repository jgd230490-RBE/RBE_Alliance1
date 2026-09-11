/*
 * 2.5b — RENDER the frontend, don't just grep it.
 *
 * WHY THIS EXISTS
 * ---------------
 * parse_frontend.js asserts at source level, which is right for "is this control really
 * gone" but cannot catch the failure 2.5b was most likely to introduce: a page in the
 * new rail that names a component that does not exist, or a component whose render path
 * reaches an identifier that was deleted with My submissions and Approvals. TypeScript
 * parses that happily; it only blows up when React tries to render it.
 *
 * So this evaluates the whole <script type="text/babel"> block in a vm with the real
 * React, stubs everything a browser would provide, and calls
 * ReactDOMServer.renderToStaticMarkup on each page of the portal.
 *
 * ⚠️ WHAT THIS DOES **NOT** PROVE
 * -------------------------------
 * useEffect never runs under renderToStaticMarkup, so every fetch in this file is a
 * promise that never settles and every page renders its LOADING or EMPTY branch. That
 * means:
 *   - no table row, no filter behaviour and no save path is exercised here;
 *   - Mapbox is a stub, so Data Management renders its chrome and an empty div;
 *   - CSS, layout and anything visual are unverified — there is no browser.
 * What it proves is exactly one thing, and it is the thing greps cannot: every page the
 * rail can reach mounts without throwing.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const G = "/home/claude/.npm-global/lib/node_modules/";
const ts = require(G + "typescript");
const React = require(G + "react");
const ReactDOMServer = require(G + "react-dom/server");

const ROOT = path.resolve(__dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "frontend", "index.html"), "utf8");

let pass = 0;
const fail = [];
function ok(label, cond, extra) {
  if (cond) pass++;
  else fail.push(label + (extra ? "  " + extra : ""));
}

const m = html.match(/<script type="text\/babel"[^>]*>([\s\S]*?)<\/script>/);
if (!m) { console.log("no babel block"); process.exit(1); }
// the mount call is the one line that needs a real DOM; drop it and export instead
const src = m[1].replace(/^ReactDOM\.createRoot\([\s\S]*?\);\s*$/m, "");
const js = ts.transpileModule(src, {
  compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2019 },
}).outputText;

// ---- the browser, stubbed ------------------------------------------------------
const store = () => {
  const d = {};
  return { getItem: k => (k in d ? d[k] : null), setItem: (k, v) => { d[k] = String(v); },
           removeItem: k => { delete d[k]; }, clear: () => { for (const k in d) delete d[k]; } };
};
// a fetch that never settles: every useEffect is unreachable here anyway, and a
// promise that resolves would only invite this harness to pretend it tested the
// loaded state, which it does not.
const fetchStub = () => new Promise(() => {});
const el = () => ({ appendChild(){}, removeChild(){}, click(){}, setAttribute(){}, style: {},
                    addEventListener(){}, removeEventListener(){}, getContext: () => null });
const sandbox = {
  React, console, Math, Date, JSON, URL: { createObjectURL: () => "blob:x", revokeObjectURL(){} },
  URLSearchParams, Blob: function(){}, setTimeout, clearTimeout, setInterval, clearInterval,
  localStorage: store(), sessionStorage: store(), fetch: fetchStub,
  alert(){}, confirm: () => true, prompt: () => "reason",
  mapboxgl: { Map: function(){ return { on(){}, remove(){}, getCanvas: () => ({ style: {} }),
              addControl(){}, getSource: () => null, getLayer: () => null, resize(){}, setStyle(){} }; },
              accessToken: "", NavigationControl: function(){}, ScaleControl: function(){} },
  Chart: function(){ return { destroy(){}, update(){} }; },
};
sandbox.window = sandbox;
sandbox.document = { getElementById: () => el(), createElement: () => el(), body: el(),
                     addEventListener(){}, removeEventListener(){} };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

let loaded = true;
try {
  vm.runInContext(js, sandbox, { filename: "index.tsx" });
  ok("the whole script block evaluates", true);
} catch (e) {
  loaded = false;
  ok("the whole script block evaluates", false, e.message);
}

// ---- the fixture ---------------------------------------------------------------
// Deliberately minimal and deliberately EMPTY of routes: a first-boot database is the
// state most likely to throw, and it is the state a new empty-state branch is written
// for. A populated fixture would not reach those branches at all.
const meta = {
  routes: [], materials: ["Aggregate"], vehicles: ["Artic Tipper (44t)"], units: ["m3", "t", "vehicles"],
  months: { start_year: 2026, count: 60 }, factors: { material_density_t_per_m3: {}, vehicle_payload_t: {},
  vehicle_emissions_kg_co2e_per_km: {}, planning: { working_days_per_month: 21 } },
  mapbox_token: "", vehicle_labels: { labels: {}, fallbacks: {}, langs: ["en", "eu", "ee"] },
  disciplines: [], sections: [],
};
const planner = { role: "planner", label: "Planner", ipt: null, can_approve: true,
                  ipt_locked: false, ipt_required: true, demo: false, ipts: ["IPT1", "IPT2"] };
const submitter = { role: "ipt", label: "IPT 3", ipt: "IPT3", can_approve: false,
                    ipt_locked: true, ipt_required: true, demo: false, ipts: ["IPT3"] };

function render(label, node) {
  try {
    const out = ReactDOMServer.renderToStaticMarkup(node);
    ok(label, typeof out === "string" && out.length > 0);
    return out;
  } catch (e) {
    ok(label, false, e.message);
    return "";
  }
}

if (loaded) {
  const h = React.createElement;
  const { Portal, Forecasts, ConfigPage, PageHeader, EmptyState, Dashboard, LookAhead,
          DataManagement, Matrix, CostingTab, FuelWidget } = sandbox;

  ok("the new shell components are all defined",
    [Portal, Forecasts, ConfigPage, PageHeader, EmptyState].every(f => typeof f === "function"));
  ok("My submissions and Approvals are gone from the module, not just unrendered",
    sandbox.MySubmissions === undefined && sandbox.Approvals === undefined);

  // ---- every page of the rail, as a planner ------------------------------------
  // Portal keeps the page in localStorage, so each page is selected by seeding it and
  // rendering fresh. That also proves the persistence path parses a value it did not
  // write itself.
  const PAGES = ["dashboard", "submit", "forecasts", "lookahead", "locations", "routes",
                 "zones", "config", "map"];
  for (const page of PAGES) {
    sandbox.localStorage.setItem("rbe_page", page);
    const out = render(`the ${page} page renders for a planner`,
      h(Portal, { role: planner, who: "tester", meta, metaErr: false, vehLang: "en" }));
    if (page === "map") ok("...the map page is an iframe", out.includes("<iframe"));
    if (page === "config") ok("...the config page asks for the admin token", out.includes("Admin token"));
  }

  // ---- the submitter sees the same shell minus the Data group -------------------
  sandbox.localStorage.setItem("rbe_page", "forecasts");
  const subOut = render("the portal renders for an IPT submitter",
    h(Portal, { role: submitter, who: "tester", meta, metaErr: false, vehLang: "en" }));
  ok("⭐ a submitter is not offered the Data group",
    !subOut.includes(">Locations<") && !subOut.includes(">Config<") && subOut.includes(">Look-ahead<"));
  ok("...and is not offered Zones either", !subOut.includes(">Zones<"));
  const planOut = (sandbox.localStorage.setItem("rbe_page", "forecasts"), render(
    "the planner is offered the Data group",
    h(Portal, { role: planner, who: "tester", meta, metaErr: false, vehLang: "en" })));
  ok("...which lists all four data pages",
    [">Locations<", ">Routes<", ">Zones<", ">Config<"].every(s => planOut.includes(s)));

  // ---- the states a page can be in ---------------------------------------------
  ok("metaErr renders the service message rather than an empty shell",
    render("portal with metaErr",
      h(Portal, { role: planner, who: "t", meta: null, metaErr: true, vehLang: "en" }))
      .includes("reach the forecasting service"));   // apostrophes arrive HTML-escaped
  ok("no meta yet renders a loading state",
    render("portal with no meta",
      h(Portal, { role: planner, who: "t", meta: null, metaErr: false, vehLang: "en" }))
      .includes("Loading"));

  // ---- the merged page, both ways ----------------------------------------------
  const fPlanner = render("Forecasts renders for an approver",
    h(Forecasts, { meta, who: "t", access: planner, onEdit(){}, onChanged(){} }));
  const fSubmitter = render("Forecasts renders for a submitter",
    h(Forecasts, { meta, who: "t", access: submitter, onEdit(){}, onChanged(){} }));
  ok("⭐ the two roles get different copy, not one wording for both",
    fPlanner !== fSubmitter && fPlanner.includes("Approve or reject each line here")
    && fSubmitter.includes("across every year each one covers")
    && !fPlanner.includes("across every year each one covers"));

  // ---- 11 Sep: the Dashboard and the Forecasts page POPULATED from the server's cost lines
  // (test_costlines.py writes the fixture through costlines.lines() on its scratch database)
  const clPath = path.join(__dirname, "fixtures", "cost_lines.json");
  ok("the cost-lines fixture exists (written by test_costlines.py)", fs.existsSync(clPath));
  const cl = fs.existsSync(clPath) ? JSON.parse(fs.readFileSync(clPath, "utf8")) : null;
  if (cl) {
    const dMeta = { ...meta, months: { start_year: 2026, count: 24 }, materials: ["Small aggregate"], vehicles: ["Rigid 8-wheeler (32t)"],
                    factors: { ...meta.factors, material_density_t_per_m3: { "Small aggregate": 1.6 }, vehicle_payload_t: { "Rigid 8-wheeler (32t)": 20 } } };
    const dOut = render("the Dashboard renders POPULATED from the cost lines (initialData)", h(Dashboard, { meta: dMeta, initialData: cl }));
    ok("...with the three KPI groups — Volume, Cost, Carbon — and the coverage line",
      dOut.includes(">Volume<") && dOut.includes(">Cost<") && dOut.includes(">Carbon<") && dOut.includes("line-months on a baked route")
      && dOut.includes("exclude the " + (cl.totals.lines - cl.totals.baked_lines) + " unbaked"));
    // the default view is Approved only, so the KPIs are sums over the Approved rows of the fixture
    const appr = cl.lines.filter(l => l.status === "Approved");
    const sumTrips = appr.reduce((a, l) => a + l.trips, 0);
    const fairT = appr.filter(l => l.fair.eur != null);
    const fairPerT = fairT.reduce((a, l) => a + l.fair.eur, 0) / fairT.reduce((a, l) => a + l.tonnes, 0);
    ok("...the trips KPI is the SUM of the server's rounded-up trips (no arithmetic of its own) and fair €/t the weighted figure",
      dOut.includes(">" + sumTrips.toLocaleString("en-US").replace(/,/g, " ") + "<") && dOut.includes("planned €") && dOut.includes("fair € (model)")
      && dOut.includes("€ " + fairPerT.toFixed(2)));
    ok("...the diesel index is in the header, and IPT / discipline filters exist",
      dOut.includes("€" + (+cl.costing.index_eur_per_l).toFixed(3) + "/L") && dOut.includes("All IPTs") && dOut.includes("All disciplines"));
    ok("...the route table prints planned € with a 'target' chip, fair € and fair €/t, and 'not baked' on the unbaked route",
      dOut.includes(">target<") && dOut.includes("Fair € (model)") && dOut.includes("not baked") && dOut.includes("Cost over time"));
    ok("...nothing renders as undefined or NaN", !/undefined|NaN/.test(dOut));
    // 11 Sep pm: the four groups, the Delivered figures from the fixture's reported line, every Fig with a title
    ok("...the Delivered-to-date group prints the server's actual tonnes and % of plan (a reported line is in the fixture)",
      dOut.includes(">Delivered to date<") && cl.totals.actual_tonnes != null
      && dOut.includes(">" + cl.totals.actual_tonnes.toLocaleString("en-US").replace(/,/g, " ") + "<")
      && dOut.includes("tonnes moved") && dOut.includes("line-months reported"));
    ok("...every KPI value sits in a nowrap clamp()-sized div, none truncated",
      (dOut.match(/font-size:clamp\(1\.1rem/g) || []).length >= 16 && !/kpi-num[^"]*truncate/.test(dOut));
    ok("...every KPI cell has a title attribute (hover)",
      (dOut.match(/<div title="[^"]+" class="min-w-0 cursor-help">/g) || []).length >= 16);
    ok("...the Today strip renders its loading state without the week, and the stock chart card is there (no stock table)",
      dOut.includes("Loading this week") && dOut.includes("Stockpile capacity") && !dOut.includes("Stock held") && dOut.includes("Big screen"));
    ok("...the route table has a Delivered column with a % on the reported line and — elsewhere",
      dOut.includes(">Delivered") && / %<\/span>/.test(dOut));
    // with the week fixture: today's deliveries, this week to date, last week, the map card
    const wkPath = path.join(__dirname, "fixtures", "lookahead_page_fuel.json");
    const wk = fs.existsSync(wkPath) ? JSON.parse(fs.readFileSync(wkPath, "utf8")) : null;
    if (wk) {
      const wOut = render("the Dashboard renders POPULATED with the week too (initialWeek)", h(Dashboard, { meta: dMeta, initialData: cl, initialWeek: wk }));
      const todayRows = [];
      wk.commit.lines.forEach(l => (l.days || []).forEach(d => { if (d.day_date === wk.today && ((d.planned_qty || 0) > 0 || d.actual_qty != null)) todayRows.push({ l, d }); }));
      const tT = todayRows.reduce((a, x) => a + ((x.d.derived || {}).tonnes || 0), 0);
      const tTrips = todayRows.reduce((a, x) => a + ((x.d.derived || {}).trips || 0), 0);
      ok(`...today (${wk.today}) lists ${todayRows.length} lines with the SERVER's day tonnes and trips summed (${tT} t, ${tTrips} trips)`,
        todayRows.length > 0 && wOut.includes(">tonnes<") && wOut.includes("day actuals typed today") && wOut.includes(">" + tT.toLocaleString("en-US").replace(/,/g, " ") + "<")
        && wOut.includes(">" + tTrips.toLocaleString("en-US").replace(/,/g, " ") + "<")
        && todayRows.every(x => wOut.includes(">" + x.l.route_id + "</span>")));
      ok("...this week to date and last week's account are printed with their bars",
        wOut.includes("This week to date") && wOut.includes("Last week") && (wOut.match(/h-2 rounded-full bg-slate-100/g) || []).length === 2
        && wOut.includes(`${wk.account.reported} of ${wk.account.lines} lines reported`));
      ok("...the map card carries the Look-ahead's CommitMap with the Dashboard's title, and no € anywhere in the Today strip",
        wOut.includes("This week&#x27;s lines on the map") && !/€/.test(wOut.slice(wOut.indexOf("Today · "), wOut.indexOf("Delivered to date"))));
      ok("...nothing renders as undefined or NaN", !/undefined|NaN/.test(wOut));
    }
    // Approved-only default: the Pending IPT2 line is not in the default view
    ok("...the default view is Approved only (the Pending line is filtered out; the status select says so)",
      dOut.includes("approved only") && !dOut.includes(">R4<"));
    const rowsFix = cl.lines.map(l => ({ route_id: l.route_id, discipline: l.discipline, section_id: l.section_id, month_index: l.month_index,
      quantity: l.quantity, unit: l.unit, status: l.status, ipt: l.ipt, material_type: l.material_type, material_description: null,
      vehicle_type: l.vehicle_type, submitted_by: "tester", reject_reason: null }));
    const fOut = render("the Forecasts page renders POPULATED with the cost column (initialRows + initialCost)",
      h(Forecasts, { meta: dMeta, who: "t", access: planner, onEdit(){}, onChanged(){}, initialRows: rowsFix, initialCost: cl }));
    const nLines = new Set(cl.lines.map(l => `${l.route_id}|${l.discipline}|${l.section_id}`)).size;
    ok("...one Cost cell per line, each with a planned figure (or 'rate not set') and a fair figure (or 'fair —')",
      (fOut.match(/data-cost-cell="1"/g) || []).length === nLines && (fOut.match(/fair € /g) || []).length >= 1 && fOut.includes("fair —")
      && (fOut.match(/>target<\/span>/g) || []).length >= 1);
    ok("...and the fair figure carries €/t beside it", /fair € [\d ]+<span class="text-slate-400"> · [\d.]+\/t<\/span>/.test(fOut));
    ok("...nothing renders as undefined or NaN", !/undefined|NaN/.test(fOut));
  }

  // ---- 11 Sep pm: the Submit matrix with the price while typing (initialCost) -------
  const pvPath = path.join(__dirname, "fixtures", "cost_preview.json");
  ok("the cost-preview fixture exists (written by test_costlines.py)", fs.existsSync(pvPath));
  const pv = fs.existsSync(pvPath) ? JSON.parse(fs.readFileSync(pvPath, "utf8")) : null;
  if (pv) {
    const mMeta = { ...meta, months: { start_year: 2026, count: 24 }, routes: [{ route_id: "R1", origin: "Pit", dest: "Site", distance_km: 30 }],
                    materials: ["Small aggregate"], vehicles: [pv.vehicle_type], planning_vehicles: [pv.vehicle_type],
                    factors: { ...meta.factors, material_density_t_per_m3: { "Small aggregate": 1.6, _default: 1.6 }, vehicle_payload_t: { [pv.vehicle_type]: 20, _default: 20 } } };
    const mOut = render("the Submit matrix renders with a cost preview (initialCost)",
      h(Matrix, { meta: mMeta, who: "t", editTarget: null, vehLang: "en", access: planner, initialCost: pv }));
    const fairT = pv.totals.fair;
    ok("...the strip prints the preview's fair € total, €/t, €/trip, €/km and the planned € with 'target rate'",
      mOut.includes('data-cost-strip="1"') && mOut.includes("€ " + Math.round(fairT.eur).toLocaleString("en-US").replace(/,/g, " "))
      && mOut.includes("€ " + (+fairT.per_t).toFixed(2)) && mOut.includes("€ " + (+fairT.per_km).toFixed(2)) && mOut.includes("target rate"));
    ok("...and the diesel index the model priced at, with the winter note", mOut.includes("€" + (+pv.index_eur_per_l).toFixed(3) + "/L") && mOut.includes("winter months"));
    ok("...nothing renders as undefined or NaN", !/undefined|NaN/.test(mOut));
    const mNone = render("the matrix renders with NO preview (nothing typed)", h(Matrix, { meta: mMeta, who: "t", editTarget: null, vehLang: "en", access: planner, initialCost: null }));
    ok("...and says 'type a quantity to see the price' with fair — and 'rate not set'", mNone.includes("type a quantity to see the price") && mNone.includes("rate not set"));
    const mUnb = render("the matrix renders an UNBAKED preview", h(Matrix, { meta: mMeta, who: "t", editTarget: null, vehLang: "en", access: planner,
      initialCost: { ...pv, baked: false, fair_reason: "not baked", cells: [], totals: { ...pv.totals, fair: { eur: null, per_t: null, per_trip: null, per_km: null } } } }));
    ok("...which says why there is no fair price and where to fix it", mUnb.includes("not baked for") && mUnb.includes("bake it on the Routes page"));
  }

  // ---- the shared furniture -----------------------------------------------------
  ok("PageHeader renders a title, a subtitle and actions",
    (() => { const o = ReactDOMServer.renderToStaticMarkup(
      h(PageHeader, { title: "T", subtitle: "S", actions: h("button", null, "A") }));
      return o.includes("T") && o.includes("S") && o.includes("<button"); })());
  ok("EmptyState renders its title and body",
    ReactDOMServer.renderToStaticMarkup(h(EmptyState, { title: "T", body: "B" })).includes("B"));

  // ---- Look-ahead v2 (09 Sep evening): the three views, POPULATED ----------------
  // useEffect never runs here, so the page is handed in through `initialPage` — a fixture
  // written by the BACKEND test harness (test_lookahead.py's own scratch database, run
  // through lookahead.page()). The shape the page renders is therefore the shape the
  // server actually produces, not one typed by hand.
  const fixPath = path.join(__dirname, "fixtures", "lookahead_page.json");
  ok("the Look-ahead page fixture exists (written by test_lookahead.py)", fs.existsSync(fixPath));
  const fixture = fs.existsSync(fixPath) ? JSON.parse(fs.readFileSync(fixPath, "utf8")) : null;
  const laMeta = { ...meta, months: { start_year: 2026, count: 60 } };
  if (fixture) {
    const commitOut = render("the Commit view renders with a populated page",
      h(LookAhead, { meta: laMeta, who: "t", initialPage: fixture, initialView: "commit" }));
    ok("...with the KPI strip, the clash rail and one row per line",
      commitOut.includes("planned this week") && commitOut.includes("+" + (fixture.clashes.count - 3) + " more")
      && (commitOut.match(/▶/g) || []).length === fixture.commit.lines.length);
    ok("...the day headers name today, and there are exactly five day columns — Mon–Fri only (10 Sep)",
      commitOut.includes("· today") && !commitOut.includes("0 default")
      && (commitOut.match(/qty · tr · veh/gi) || []).length === 5);
    ok("...every weekday cell carries trips and vehicles (baked fixture)",
      (commitOut.match(/ tr · /g) || []).length >= fixture.commit.lines.length * 5);
    ok("...no stock card on Commit any more (10 Sep) — and no map either under the render harness (initialPage)",
      !commitOut.includes("Stock held") && !commitOut.includes("This week's movements"));
    ok("...Export XLSX and the green Confirm week are in the bar",
      commitOut.includes(">Export XLSX<") && commitOut.includes(">Confirm week<"));
    ok("...nothing renders as 'undefined' or 'NaN'", !/undefined|NaN/.test(commitOut));

    const acctOut = render("the Account view renders with a populated page",
      h(LookAhead, { meta: laMeta, who: "t", initialPage: fixture, initialView: "account" }));
    ok("...with the four KPI cards and the held / applied / waiting states of the fixture",
      acctOut.includes("last week delivered · 98% band") && acctOut.includes("open to calibrate")
      && acctOut.includes(">held<") && acctOut.includes("applied · ") && acctOut.includes(">waiting<"));
    ok("...the confirmed cost box is rendered next to the actual",
      acctOut.includes("The confirmed cost of this week"));
    ok("...and the footer states the not-reported rule",
      acctOut.includes("Empty actual is not zero"));

    const hzOut = render("the Horizon view renders with a populated page",
      h(LookAhead, { meta: laMeta, who: "t", initialPage: fixture, initialView: "horizon" }));
    ok("...with the role labels on the columns and no Confirm button",
      hzOut.includes("COMMIT") && hzOut.includes("MAKE-READY") && hzOut.includes("EARLY WARNING")
      && !hzOut.includes(">Confirm week<"));
    ok("...and NO stockpile grid under it any more (10 Sep)", !hzOut.includes("max · opening") && !hzOut.includes("Type what came out"));
    ok("...the Account view carries the account week's stockpiles with one 'out' box each",
      acctOut.includes("Stockpiles · ") && acctOut.includes("Type what came out of each stockpile")
      && (acctOut.match(/placeholder="out —"/g) || []).length === fixture.account.stock.length && fixture.account.stock.length >= 1);
    ok("...and says the fixture's stockpile is OVER, with the same figure the server sent",
      acctOut.includes("OVER by " + Math.round(-fixture.account.stock[0].remaining).toLocaleString("en-US").replace(/,/g, " ")));

    // ---- 10 Sep evening: the fuel widget and Quote + BAF, from test_costing.py's fixture --
    const fuelFixPath = path.join(__dirname, "fixtures", "lookahead_page_fuel.json");
    ok("the fuel page fixture exists (written by test_costing.py)", fs.existsSync(fuelFixPath));
    const fuelFix = fs.existsSync(fuelFixPath) ? JSON.parse(fs.readFileSync(fuelFixPath, "utf8")) : null;
    if (fuelFix) {
      const pct = fuelFix.costing.baf_pct;
      const bafLbl = `BAF ${pct >= 0 ? "+" : ""}${(pct * 100).toFixed(2)} % of quote`;
      const fOut = render("the Commit view renders with the fuel fixture (target + index + base + share)",
        h(LookAhead, { meta: laMeta, who: "t", access: planner, initialPage: fuelFix, initialView: "commit" }));
      ok("...the compact widget is in the KPI strip with the index, its bulletin date and the BAF %",
        fOut.includes('data-fuel-widget="compact"') && fOut.includes("€" + fuelFix.costing.index_eur_per_l.toFixed(3) + " / L")
        && fOut.includes("diesel EE · bulletin") && fOut.includes(bafLbl));
      ok("...the planned € card counts the target-priced lines and prints the + BAF total beside the quote",
        fOut.includes(`· ${fuelFix.commit.totals.eur_target_lines} at target`) && fOut.includes("+ BAF € ")
        && fOut.includes("€ " + Math.round(fuelFix.commit.totals.eur).toLocaleString("en-US").replace(/,/g, " ")));
      ok("...nothing renders as 'undefined' or 'NaN'", !/undefined|NaN/.test(fOut));
      const fAcct = render("the Account view renders with the fuel fixture",
        h(LookAhead, { meta: laMeta, who: "t", access: planner, initialPage: fuelFix, initialView: "account" }));
      const nTarget = fuelFix.account.rows.filter(r => r.rate_source === "target").length;
      ok("...the strip widget sits above the KPI cards with typeable yard and share boxes for a planner",
        fAcct.includes('data-fuel-widget="strip"') && fAcct.indexOf('data-fuel-widget="strip"') < fAcct.indexOf("last week delivered · 98% band")
        && fAcct.includes("Yard €/L") && fAcct.includes("Fuel share %") && (fAcct.match(/inputmode="decimal"/gi) || []).length >= 2);
      ok("...every target-priced account row carries the 'target' chip and every priced row a + BAF line",
        (fAcct.match(/>target<\/span>/g) || []).length === nTarget && nTarget >= 1
        && (fAcct.match(/\+ BAF € /g) || []).length === fuelFix.account.rows.filter(r => r.planned_eur_adj != null).length);
      const fSub = render("the Account view renders for a SUBMITTER with the fuel fixture",
        h(LookAhead, { meta: laMeta, who: "t", access: submitter, initialPage: fuelFix, initialView: "account" }));
      ok("...whose widget shows the yard and share as text, not inputs",
        fSub.includes('data-fuel-widget="strip"') && !fSub.includes("Yard €/L</span><input"));
      const fCfg = render("the Costing tab renders (no data yet — useEffect never runs here)",
        h(CostingTab, { who: "t", access: planner, token: "", setToken: () => {} }));
      ok("...with the target-rate copy and the config widget's admin controls",
        fCfg.includes("Target rate") && fCfg.includes('data-fuel-widget="config"') && fCfg.includes("Reset the BAF base")
        && fCfg.includes("Fetch the bulletin now") && fCfg.includes("Use this index") && !/undefined|NaN/.test(fCfg));
      // 10 Sep night: the fair price on the Account view, and the coefficients section on Config
      const nFair = fuelFix.account.rows.filter(r => r.planned_fair_eur != null).length;
      ok("...the Account view has a 'Fair € (model)' column with a figure on every baked row and — on the unbaked one",
        fAcct.includes("Fair € <span") && nFair >= 1
        && (fAcct.match(/title="No fair price: the route is not baked/g) || []).length === fuelFix.account.rows.length - nFair);
      ok("...the KPI caption on Commit carries the fair total and how many lines it covers",
        fuelFix.commit.totals.fair_eur != null && fOut.includes(`(model, ${fuelFix.commit.totals.fair_lines} of ${fuelFix.commit.totals.lines})`));
      const fileDoc = { fair_price: { driver_eur_per_h: 20, vehicle_standing_eur_per_h: 16, running_eur_per_km: 0.12, margin_pct: 8,
        consumption: { l_per_100km_per_tonne: 0.4, rigid: { l_per_100km_empty: 27 }, artic: { l_per_100km_empty: 23.6 } },
        season: { winter_months: [11, 12, 1, 2, 3], winter_consumption_uplift_pct: 8, thaw_months: [3, 4] } } };
      const fCfg2 = render("the Costing tab renders the fair-price section from the FILE when the document has no block",
        h(CostingTab, { who: "t", access: planner, token: "", setToken: () => {}, doc: { vehicles: {} }, upd: () => {}, fileDoc }));
      ok("...with the file's values in the inputs, the 'no block yet' note, and the three assumption labels",
        fCfg2.includes('data-fair-price-section="1"') && fCfg2.includes('value="20"') && fCfg2.includes('value="23.6"')
        && fCfg2.includes("the live document has no fair_price block yet") && (fCfg2.match(/ASSUMPTION/g) || []).length === 3 && !/undefined|NaN/.test(fCfg2));
      const fCfg3 = render("...and from the DOCUMENT when it has one",
        h(CostingTab, { who: "t", access: planner, token: "", setToken: () => {}, doc: { fair_price: { ...fileDoc.fair_price, driver_eur_per_h: 25 } }, upd: () => {}, fileDoc }));
      ok("...the document's value wins and the note is gone", fCfg3.includes('value="25"') && !fCfg3.includes("no fair_price block yet"));
      const fNone = render("the widget renders with NO data at all", h(FuelWidget, { who: "t", access: planner, mode: "strip", initial: null }));
      ok("...as 'Index unavailable — type a price' for a planner, and nothing undefined",
        fNone.includes("Index unavailable — type a price") && !/undefined|NaN/.test(fNone));
      const fStale = render("the widget renders a stale index", h(FuelWidget, { who: "t", access: planner, mode: "compact",
        initial: { ...fuelFix.costing, index_eur_per_l: null } }));
      ok("...a compact card with no index says so instead of a price", fStale.includes("Index unavailable"));
    }

    // the empty page: no lines, no account, no horizon — every view has an EmptyState
    const empty = { ...fixture, commit: { ...fixture.commit, lines: [], totals: {} }, account: { week: null, rows: [] },
                    horizon: { rows: [], from_month: 9, to_month: 10, roles: {} }, clashes: { flags: [], count: 0, sources: {} }, stock: [] };
    for (const v of ["commit", "account", "horizon"]) {
      const o = render(`the ${v} view renders its empty state`, h(LookAhead, { meta: laMeta, who: "t", initialPage: empty, initialView: v }));
      ok(`...${v}: an EmptyState, and nothing undefined`, o.includes("border-dashed") && !/undefined|NaN/.test(o));
    }
  }
}

console.log();
for (const f of fail) console.log("  FAIL:", f);
console.log(`\n${pass} passed, ${fail.length} failed`);
process.exit(fail.length ? 1 : 0);

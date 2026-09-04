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
          DataManagement, Matrix } = sandbox;

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

  // ---- the shared furniture -----------------------------------------------------
  ok("PageHeader renders a title, a subtitle and actions",
    (() => { const o = ReactDOMServer.renderToStaticMarkup(
      h(PageHeader, { title: "T", subtitle: "S", actions: h("button", null, "A") }));
      return o.includes("T") && o.includes("S") && o.includes("<button"); })());
  ok("EmptyState renders its title and body",
    ReactDOMServer.renderToStaticMarkup(h(EmptyState, { title: "T", body: "B" })).includes("B"));
}

console.log();
for (const f of fail) console.log("  FAIL:", f);
console.log(`\n${pass} passed, ${fail.length} failed`);
process.exit(fail.length ? 1 : 0);

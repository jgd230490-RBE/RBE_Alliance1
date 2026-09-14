/**
 * The user guide, rendered in a real browser.  2026-09-14.
 *
 * The guide gained JavaScript on 14 Sep — a role switcher that hides sections, and a
 * troubleshooting picker. A source-level test cannot see whether a section is actually
 * hidden, so this loads the page in headless Chromium and looks.
 *
 * NOT in the default suite: it needs Playwright, which the harness does not assume.
 *   node backend/tests/help_browser_check.js
 */
const path = require("path");
const { chromium } = require("playwright");

const FILE = "file://" + path.resolve(__dirname, "../../frontend/help/index.html");
let pass = 0;
const fail = [];
const ok = (label, cond, extra) => { if (cond) pass++; else fail.push(label + (extra ? " — " + extra : "")); };

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  // A blocked webfont is not a page fault: the sandbox has no route to fonts.googleapis.com
  // and the guide declares a full system fallback stack. Script errors are still fatal.
  page.on("console", m => {
    if (m.type() !== "error") return;
    const t = m.text();
    if (/Failed to load resource/.test(t)) return;
    errors.push("console: " + t);
  });

  // ---- 1. default view: a submitter ----------------------------------------
  await page.goto(FILE, { waitUntil: "domcontentloaded" });
  ok("the page loads with no JavaScript error", errors.length === 0, errors[0]);
  ok("the role bar is revealed by the script (it ships hidden, so no-JS shows everything)",
     await page.isVisible("#roles"));
  ok("default role is the submitter",
     (await page.getAttribute('#roles button[data-role="submitter"]', "aria-pressed")) === "true");

  const visible = async sel => page.isVisible(sel);
  ok("a submitter sees the Dashboard section", await visible('[id="3-the-dashboard"]'));
  ok("🔴 a submitter does NOT see the Data pages", !(await visible('[id="7-data-pages-planners-and-admins"]')));
  ok("...nor the planner's approve/reject section",
     !(await visible('[id="5-1-for-planners-approving-and-rejecting"]')));
  ok("...nor the admin technical appendix", !(await visible("#appendix-d-technical-reference")));
  ok("...and the hidden sections are gone from the nav too",
     !(await visible('#nav a[href="#7-1-locations"]')));
  ok("the body of a hidden section goes with its heading — not just the heading",
     !(await visible("#d-1-how-a-number-is-made")));

  // ---- 2. planner -----------------------------------------------------------
  await page.click('#roles button[data-role="planner"]');
  ok("a planner sees the Data pages", await visible('[id="7-data-pages-planners-and-admins"]'));
  ok("...and Locations, Routes, Zones, Config",
     (await visible('[id="7-1-locations"]')) && (await visible('[id="7-2-routes"]'))
     && (await visible('[id="7-3-zones-and-temporary-haul-roads"]')) && (await visible('[id="7-4-config"]')));
  ok("🔴 ...but still NOT the admin technical appendix",
     !(await visible("#appendix-d-technical-reference")));
  ok("...and the nav gains the Data entries", await visible('#nav a[href="#7-1-locations"]'));

  // ---- 3. admin -------------------------------------------------------------
  await page.click('#roles button[data-role="admin"]');
  ok("an admin sees the technical appendix", await visible("#appendix-d-technical-reference"));
  ok("...including its subsections", await visible("#d-2-where-the-figures-come-from"));
  ok("...and everything a planner sees", await visible('[id="7-4-config"]'));
  ok("...and the nav gains Appendix D",
     await visible('#nav a[href="#appendix-d-technical-reference"]'));

  // ---- 4. the choice sticks -------------------------------------------------
  await page.reload({ waitUntil: "domcontentloaded" });
  ok("the chosen role survives a reload",
     (await page.getAttribute('#roles button[data-role="admin"]', "aria-pressed")) === "true");

  // ---- 5. the app's link decides the opening view ---------------------------
  await page.goto(FILE + "?role=planner", { waitUntil: "domcontentloaded" });
  ok("?role= from the app's rail wins over the remembered choice",
     (await page.getAttribute('#roles button[data-role="planner"]', "aria-pressed")) === "true");
  ok("...and it says who you are signed in as",
     /signed in as planner/.test(await page.textContent("#whoami")));
  await page.goto(FILE + "?role=ipt", { waitUntil: "domcontentloaded" });
  ok("an IPT code reads as a submitter",
     (await page.getAttribute('#roles button[data-role="submitter"]', "aria-pressed")) === "true");
  await page.goto(FILE + "?role=nonsense", { waitUntil: "domcontentloaded" });
  // A junk value is ignored and the remembered choice stands — deliberately NOT a reset to
  // submitter, which would silently demote an admin who mistyped a URL.
  ok("a junk role is ignored, the page still renders and exactly one role is selected",
     (await page.$$eval('#roles button[data-role]', bs => bs.filter(b => b.getAttribute("aria-pressed") === "true").length)) === 1
     && (await page.isVisible('[id="1-welcome"]')));

  // ---- 6. troubleshooting ---------------------------------------------------
  await page.goto(FILE, { waitUntil: "domcontentloaded" });
  const qs = await page.$$("#tsq button");
  ok("the troubleshooting picker builds its questions", qs.length >= 10, "got " + qs.length);
  ok("every answer starts closed", (await page.$$("#tsa .ts-a.on")).length === 0);
  await qs[0].click();
  ok("clicking a symptom opens exactly one answer", (await page.$$("#tsa .ts-a.on")).length === 1);
  ok("...and the answer says whether it is a fault or correct behaviour",
     await page.isVisible("#tsa .ts-a.on .verdict"));
  await qs[1].click();
  ok("opening another closes the first — one answer at a time",
     (await page.$$("#tsa .ts-a.on")).length === 1);
  await qs[1].click();
  ok("clicking the open one closes it", (await page.$$("#tsa .ts-a.on")).length === 0);

  // ---- 7. deep links --------------------------------------------------------
  const links = await page.$$eval("figcaption a.go", as => as.map(a => a.getAttribute("href")));
  ok("every figure that has a screen carries a deep link into it", links.length === 21,
     "got " + links.length);
  ok("...staff figures point at an app page by hash, map figures at the map",
     links.some(h => h === "/#lookahead") && links.some(h => h === "/#dashboard")
     && links.some(h => h === "/map/"));
  ok("...and they open in a new tab so the guide is not lost",
     (await page.$$eval("figcaption a.go", as => as.every(a => a.target === "_blank"))));

  ok("no JavaScript error across the whole run", errors.length === 0, errors[0]);

  await browser.close();
  console.log();
  for (const f of fail) console.log("  FAIL:", f);
  console.log(`\n${pass} passed, ${fail.length} failed`);
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });

RBE Alliance 1 — hotfix: routes invisible, sidebar swatch stale
===============================================================
Delivered 2026-09-01.  Zip: rbe-map-hotfix.zip

TWO FILES. Extract over the repo root, on top of `rbe-5a-fix-5b-map-v2.zip`
(which you have deployed).

    map/index.html
    backend/tests/parse_map.js

No backend change, no migration, no version bump — `ipt_segments.js` is untouched
so the handshake stays at v9 and the `?v=9` buster is still correct.


-----------------------------------------------------------------------------------
1. 🔴 WHY THE ROUTES DISAPPEARED — my bug, and it was not the dasharray
-----------------------------------------------------------------------------------
I wrote this to widen the dot gap at corridor zoom:

    const DASH_CORE = ['step', ['zoom'], [1, 0.6], 10, [1, 0.333]];

**An array literal used as an expression OUTPUT must be wrapped in
`['literal', …]`.** A bare `[1, 0.6]` is parsed as an expression whose operator is
the number `1`, which is not an operator, so the paint property failed style
validation.

And this is the part that made it invisible rather than noisy: **`Style.addLayer`
validates BEFORE adding.** On a validation failure it fires an error event on the
map and **returns without adding the layer**. So all four route layers were
silently skipped, while every layer added after them — the alignment, the boundary
ticks, the chainage, the gates — went in normally. Nothing crashed, nothing looked
out of order, and the routes were simply not there. That is exactly the symptom you
saw: IPT fine, routes gone.

**FIXED AS A PLAIN CONSTANT, NOT AS A CORRECTED EXPRESSION:**

    const DASH_CORE   = [1, 0.45];
    const DASH_CASING = [0.96, 0.2];

The correctly-wrapped step is in the comment, unused, because `line-dasharray` is
a **cross-faded** property and expression support for those has been patchy across
GL JS versions. Two map deploys have now gone out broken. A constant cannot fail
validation, and the step is worth trying only once the constant is confirmed
drawing.

⚠️ **The constant is a compromise on the agreed 3:1 dot:gap.** At the thinned
widths the design value of 0.333 gives a 0.57 px gap at zoom 7, which aliases to
solid. 0.45 gives 0.77 px at zoom 7 and 2.25 px at zoom 12 — slightly airier than
agreed at close zoom, legible at corridor zoom. Tell me if the dots read too loose
at zoom 12 and I will split the difference.

The casing still matches the core in PIXELS (dash 1.2w against the core's 1.0w, so
0.1w of halo past each dot end; identical period). That part was never the problem.

**THE GUARD IS BROADER THAN THE BUG.** parse_map.js now fails on ANY expression in
`map/index.html` using a bare array as a `step` / `case` / `match` output, not just
this one. Re-introducing the exact line that shipped fails three assertions;
wrapping it correctly but leaving it as an expression still fails two, because the
decision to keep dasharray constant is itself recorded.


-----------------------------------------------------------------------------------
2. 🔴 THE CONTROL PANEL COLOURS
-----------------------------------------------------------------------------------
The "Rail alignment (by IPT)" swatch in the sidebar was a hard-coded

    linear-gradient(90deg, #039E86, #003787, #0E7490, #C6841D, #BF2E55)

— the **first** palette. It survived the v4 rebuild and palette C untouched, so by
this morning the control panel was advertising **inbound-teal, brand navy and
selection-crimson** as IPT colours, three palettes out of date, while the map beside
it drew jade / violet / teal blue / stone / umber / burgundy.

Now generated at render time from `window.iptLegendRows()` — the same table that
paints the map — civil bands only (the underlay is a wash, not a band; Outside A1
is out of scope), with **hard stops rather than a blend**, because the bands are
discrete and a smooth gradient would invent colours that are not on the map.

⚠️ Worth naming: `claude/roadmap.md` already carried the rule *"a colour written in
two places WILL drift — generate every swatch from the table that paints the map"*,
**and it was written because this same element drifted once before.** Writing it
down did not stop it happening again. Generating it does. The per-IPT legend rows
below it were already generated and were correct all along.


-----------------------------------------------------------------------------------
3. TESTS — 1,489, 0 failed
-----------------------------------------------------------------------------------
    parse_map.js  297  (was 291)     everything else unchanged

Three deliberate regressions confirmed the new guards bite:
  * the exact line that shipped -> 3 failures
  * the same line correctly `['literal', …]`-wrapped -> 2 failures
  * the stale hard-coded gradient restored -> 1 failure

🔴 **Still not seen rendered on a real basemap.** No Mapbox in this sandbox. What I
can now say with confidence is *why* the layers were dropped — the validation
behaviour is documented and the expression rule is unambiguous — not that the new
constant looks right.


-----------------------------------------------------------------------------------
4. AFTER DEPLOYING
-----------------------------------------------------------------------------------
  1. Routes should be back, dotted, on both legs.
  2. The sidebar alignment swatch should show five hard-edged blocks matching the
     bands on the map: violet, teal blue, stone, umber, burgundy.
  3. Zoom 7–9: are the dots reading as dots?
  4. Zoom 12–14: are they too far apart? That is the 0.45 compromise and it is one
     number.
  5. If routes are STILL missing, open DevTools → Console and send me the first
     red line. A dropped layer logs an error naming the layer and the property.

Everything else from the previous zip is unchanged: solid gap bridges, palette C,
arrows removed, casing filter fix, WS ticks from zoom 8, gates, the cache headers.

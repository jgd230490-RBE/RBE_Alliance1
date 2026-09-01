RBE Alliance 1 — Phase 5a fix + Phase 5b map changes + the /map/ cache fix
==========================================================================
Delivered 2026-09-01.  Zip: rbe-5a-fix-5b-map.zip

⚠️ THIS ZIP SUPERSEDES `rbe-phase5a-fix.zip`. If you have not applied that one —
   and you told me you had not — apply THIS instead. Do not apply both.

Extract over the repo root. Eleven files. **No database migration, no backup
needed, nothing to delete.**

REQUIRES `rbe-phase5a.zip` to be applied first. It is (confirmed live: /api/gates
answers, and the boot log showed the Phase 4.5 tenant migration succeed on all
eleven tables).


-----------------------------------------------------------------------------------
0. WHAT IS IN HERE — three separate pieces of work in one delivery
-----------------------------------------------------------------------------------
A. The Phase 5a fixes (site marker, gate layer) — carried over unchanged.
B. The Phase 5b MAP changes (palette C, dotted routes, WS ticks earlier).
C. The /map/ half of the browser-cache fix.

They are together because (A) was never deployed, so shipping them separately
would mean two uploads and two chances to half-upload.

⚠️ NAME COLLISION, and it matters when you read the notes: "Phase 5b" is BOTH the
laydown-areas phase AND these three map changes. This zip is only the map changes.
The laydown phase is not started and is still blocked on the E1 HERE probe.


-----------------------------------------------------------------------------------
1. 🔴 READ THIS BEFORE YOU LOOK AT THE MAP — a finding about palette C
-----------------------------------------------------------------------------------
Palette C is shipped exactly as decided. But the numbers it arrived with do not
all hold up, and one of them is the measure that condemned the previous palette.

I re-measured every hex myself (CIE ΔE2000, sRGB/D65) rather than taking the
brief's figures on trust. Two of the three reproduce exactly:

    weakest pair anywhere    16.6   ✅ matches the brief
    weakest adjacent pair    20.1   ✅ matches the brief
    "colour-blind      13.5"        ❌ COULD NOT BE REPRODUCED

13.5 is exactly palette C's nearest approach to a reserved colour (IPT 1 vs brand
navy #003787). It looks like one number was copied into two roles.

The actual deuteranopia figures, ADJACENT bands — the pairs that matter, because
they meet along the corridor:

                        previous palette   palette C
    IPT 1 / IPT 2            19.5             4.4    violet vs teal blue
    IPT 6 / IPT 1            21.7             5.7    jade vs violet
    IPT 4 / IPT 5            15.2             8.8    umber vs burgundy

Protanopia is the same shape (IPT 1 / IPT 2 = 5.4).

**So for a red-green colour-blind viewer — roughly 1 in 12 men — three of the six
package changes along this corridor are close to invisible by colour alone.** The
previous palette's worst adjacent pair was 15.2; palette C's is 4.4.

WHAT I DID ABOUT IT
  * Shipped palette C. It was your decision, explicitly confirmed, and the dotted
    route is a real mitigation for the routes-vs-alignment confusion it was
    chosen to solve. Overriding it would have been me substituting my judgement.
  * Wrote the MEASURED numbers into map/ipt_segments.js, not the claimed ones,
    and said in the file that 13.5 must not be restored as a colour-blind score.
  * Recorded the mitigations that are real: the legend uses labelled swatches
    rather than thin lines, there are per-IPT checkboxes, and the click popup
    names the package. Colour is not the only channel.

⭐ IF YOU WANT IT FIXED, IT IS ONE HEX AND THE HUE FAMILIES SURVIVE.
   Move IPT 1 from `#4C1D95` to `#7C3AED` (a lighter violet):

       IPT 1 / IPT 2   4.4 -> 21.5
       IPT 6 / IPT 1   5.7 -> 19.6
       every normal-vision measure unchanged (16.6 / 20.1 / 13.7)
       IPT 4 / IPT 5 stays 8.8 and becomes the binding pair

   One line in `map/ipt_segments.js`. Say the word and I will cut it.

✅ The MANDATORY colour rule still holds: no band hex equals any of the seven
   reserved hexes. Checked against the real list, not assumed.


-----------------------------------------------------------------------------------
2. THE DOTTED ROUTES — two things that were nearly wrong
-----------------------------------------------------------------------------------
🔴 THE CASING. The approved mockup drew each route as one stroke. The real map
draws TWO layers — a white casing under a coloured core — plus a ±4 px offset.
Dashing only the core would have left the casing showing through every gap, and
the route would read as a continuous pale line with coloured beads on it: worse
than the solid line it replaces.

Both layers are dashed. The casing is pinned to a FIXED 1.25x the core at every
zoom stop (it drifted 1.0 -> 1.167 -> 1.2 before), because `line-dasharray` units
are multiples of that layer's OWN width — two layers of different widths need
different arrays to draw the same pattern on screen:

               core          casing
    width       w             1.25w
    dash       1.0w           1.2w      (0.1w of halo past each dot end)
    gap        0.333w         0.133w
    period     1.333w         1.333w    ✓
    array      [1, 0.333]     [0.96, 0.107]

⚠️ k = 1.25 is near a ceiling. Inbound and outbound are offset ±4 px, so their
centres are 8 px apart; at zoom 12 the casings are 7.5 px and clear by 0.5 px.
The old casing cleared by 1.0 px, so this DOES tighten it. **Worth one look at
zoom 11–13 where two routes run parallel.**

🔴 `line-cap: butt`. A round cap extends every dash by half the line width at each
end, so a 1.0w dot renders 2.0w long, the gap is swallowed twice over, and the
route draws SOLID — indistinguishable from the change never being applied. Set on
all four layers and asserted; flipping it to round fails a named assertion.

WIDTHS. Only the zoom-7 core stop moved, 2.5 -> 2, so at corridor zoom the route
is thinner than the alignment's fixed 2.5 px (ratio 0.8, close to the mockup's
3:4). The 12 and 16 stops are untouched — that ramp is a Phase 2.5a decision and
flattening it would make routes hairlines at road zoom.

⚠️ UNVERIFIED AND WORTH A LOOK: at zoom 7 a 2 px line gives a 0.67 px gap, which
may alias to solid at exactly the zoom where this change matters most. I could not
test it — no Mapbox in the sandbox. If it looks solid at 7–9, the fix is a stepped
array, and the comment in map/index.html says which one. Do NOT interpolate it;
`line-dasharray` cannot be interpolated across zoom and silently does nothing.


-----------------------------------------------------------------------------------
3. WORK-SECTION TICKS AND LABELS
-----------------------------------------------------------------------------------
Ticks 11 -> 8, labels 13 -> 11. At zoom 11 the ticks were effectively absent —
you had to already be looking at a boundary to discover one existed.

Below zoom 13 only every OTHER boundary is labelled, because the three Pärnu
boundaries (135400, 137685, 142000) are within a few km and their labels collide.
Two mechanics worth knowing, both forced by documented Mapbox constraints:

  * it is done in the TEXT-FIELD, not the filter — `['zoom']` cannot be used
    inside a layer `filter`, which is the obvious thing to reach for and silently
    does not work;
  * the unlabelled ones get an EMPTY text-field, never `text-opacity: 0` — an
    invisible label still occupies collision space and would push the labels that
    ARE showing off the map.

The ordinal is stamped onto the data in `buildWsBoundaries()` (sorted by chainage
first), because Mapbox has no index-of operator.


-----------------------------------------------------------------------------------
4. THE /map/ CACHE FIX — the other half
-----------------------------------------------------------------------------------
`GET /` got `Cache-Control: no-cache` in the 5a fix. `/map/` is served by
`StaticFiles` and had the same exposure — and it is where the bug actually bit
worst, producing a HALF-upgraded map: new index.html, old ipt_segments.js.

Now served by a `NoCacheStatic` subclass.

⚠️ It overrides `get_response()`, NOT `file_response()`. `file_response` is the
more obvious hook and is the wrong one: it is internal, its signature has changed
between Starlette versions, and a subclass overriding a method the installed
version no longer calls adds no header and raises no error — it would fail exactly
as silently as the bug it is meant to fix.

`no-cache`, not `no-store`: the browser still revalidates with its ETag and still
gets a 304, so `map/data/alignment.js` (8.8 MB) is re-downloaded only when it has
actually changed. `no-store` would re-fetch it on every page load.

⚠️ This does NOT replace the `?v=9` cache-buster. Both are in. For a bug that has
now bitten twice, belt and braces is the right number of mechanisms.

⚠️ NOT VERIFIED AT RUNTIME. FastAPI cannot be booted in the sandbox and Starlette
is stubbed, so the subclass is asserted at source level only. **After deploying,
check the response headers on /map/ in DevTools → Network.** If `Cache-Control`
is absent, tell me and I will switch the mechanism to a middleware.


-----------------------------------------------------------------------------------
5. THE VERSION HANDSHAKE — v9, in all three places
-----------------------------------------------------------------------------------
    map/ipt_segments.js   window.IPT_SEGMENTS_VERSION = 'v9'
    map/index.html        const IPT_INDEX_VERSION = 'v9'
    map/index.html        <script src="ipt_segments.js?v=9">

All three are in this zip and all three are asserted. Missing any one reproduces
the 2026-08-30 fault where the map half-worked silently for a day. If the sidebar
shows a red version-mismatch line after deploying, one of the two map files did
not upload.


-----------------------------------------------------------------------------------
6. TESTS — 1,476, 0 failed
-----------------------------------------------------------------------------------
    parse_map.js       284   (was 263)      test_phase5a.py     182   (was 180)
    test_phase2.py     142                  test_phase3.py      154
    test_phase4.py     202                  test_phase25a.py    104
    test_phase45.py    127                  test_tenant_audit.py 22
    parse_frontend.js  119                  test_ipt_overlay.js 140

⚠️ The five other python harnesses are in this zip for ONE reason: they stubbed
`StaticFiles` as a lambda, and `main.py` now subclasses it, so `class X(lambda)`
is a TypeError and every one of them crashed on import. They are otherwise
unchanged. If you skip them, five suites stop running.

Two existing assertions were UPDATED, not deleted, per the standing rule:
"hidden below zoom 11" now reads "ticks from zoom 8", and the labels one moved
13 -> 11. The palette assertions were narrowed to palette C's hexes rather than
removed — the property being guarded (six distinct hue families) is unchanged.

Three deliberate regressions were introduced to check the new guards bite:
round caps (1 fail), an undashed casing (1), a missing cache-buster (2).

🔴 WHAT IS NOT TESTED. Read this before quoting 1,476.
**Nothing in this delivery has been seen rendered on a real basemap.** There is no
Mapbox in the sandbox — no npm, no PyPI. Every claim here is about the NUMBERS in
the layer definitions, not the pixels. Specifically unverified:
  * whether the dots read as dots at zoom 7–9 (§2);
  * whether the two casings visibly clash at zoom 11–13 where routes run
    parallel (§2);
  * whether palette C's six bands are tellable apart on the satellite basemap;
  * whether the /map/ no-cache header actually lands (§4);
  * whether alternate labelling is enough for the three Pärnu boundaries at 11–12.


-----------------------------------------------------------------------------------
7. WHAT TO LOOK AT AFTER DEPLOYING
-----------------------------------------------------------------------------------
  1. Hard refresh once. After this deploy you should not need to again.
  2. /map/ at zoom 7–9: are the routes dotted, or has the gap aliased to solid?
  3. Zoom 11–13 with two parallel routes: do the white casings clash?
  4. Zoom 11: arrows AND work-section labels both switch on here. It is the
     busiest threshold in the new design and the one thing the tests cannot see.
  5. Six bands, six colours, on satellite and on the light basemap.
  6. Sites are on the sites; gates are separate grey dots from zoom 10 (§5a fix).
  7. DevTools → Network → /map/ → response headers → `Cache-Control: no-cache`.

Still owed, unchanged by this delivery:
  * the E6 gate walkthrough in `claude/open-questions.md`;
  * the E1 HERE probe, which blocks the laydown-areas phase;
  * `claude/roadmap.md` was rewritten on 2026-08-31 from a pre-5a copy and now
    says 5a is unbuilt. Corrected in the project notes this session.

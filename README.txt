RBE Alliance 1 — 5a fixes + 5b map changes + map polish
=======================================================
Delivered 2026-09-01.  Zip: rbe-5a-fix-5b-map-v2.zip

⚠️ THIS SUPERSEDES BOTH `rbe-phase5a-fix.zip` AND `rbe-5a-fix-5b-map.zip`.
   Neither of those was deployed. Apply THIS one only.

Extract over the repo root. Eleven files. **No database migration, no backup
needed, nothing to delete.** Requires `rbe-phase5a.zip`, which is already live.

⚠️ The IPT version handshake stays at **v9** — `ipt_segments.js` is byte-identical
to the previous zip, so bumping index.html alone would trigger a FALSE mismatch
error in the sidebar. Both files are in here regardless; extract the whole zip.


-----------------------------------------------------------------------------------
WHAT CHANGED SINCE THE LAST ZIP (four things)
-----------------------------------------------------------------------------------
1. Gap bridges drawn SOLID — the corridor is continuous.
2. Routes another ~15% thinner.
3. Directional arrows REMOVED.
4. 🔴 A pre-existing filter bug, found while doing (3).

Everything else — palette C, the dotted routes, WS ticks from zoom 8, the gate
layer, the site-marker fix, the /map/ and GET / cache headers — is unchanged from
the previous zip and is described in its README, reproduced below where it still
matters.


-----------------------------------------------------------------------------------
1. SOLID GAP BRIDGES — and the thing you are trading away
-----------------------------------------------------------------------------------
You chose full continuity over the 30%-opacity version. Done: the `is_bridge`
exclusion is gone from both the civil alignment layer and the IPT 6 underlay, so
bridges paint at full opacity in the band's own colour and the corridor reads
unbroken end to end.

🔴 **WHAT THIS COSTS, PLAINLY.** A bridge is a straight line across a stretch
where `alignment.js` has NO surveyed Main Track — **31 stretches over 300 m,
~60 km in total, the longest 7.0 km.** The map now draws those identically to
surveyed track, on a client-visible surface. Nothing in the picture separates
measured geometry from an interpolation.

Because that is a real risk on a client map, the caveat had to go somewhere. Three
places now carry it, and all three are asserted so none can quietly disappear:

  * **Click the line.** A bridge stretch gets an amber panel: "No surveyed
    alignment on this stretch. The line here is interpolated across a hole in the
    survey file... It is not measured geometry."
  * **The sidebar legend note was CORRECTED.** It said "Breaks in the line = no
    surveyed Main Track... not a rendering fault" — which is now false, because
    there are no breaks. It now says the line is drawn continuously and ~60 km is
    interpolated, and tells you to click.
  * **The work-section boundary labels** still say "⚠ no surveyed alignment here"
    on the three ticks that sit on these stretches.

⚠️ **`applyIptFilter()` had to change too.** It rebuilds the same filter expression
when you tick an IPT checkbox. Left alone, ticking a box would have silently
reinstated the gaps the layer definition had just closed. Asserted.

**To go back to faint bridges** — if this reads wrong on the real basemap — it is
one `line-opacity` expression on `rail-alignment`. The comment at that layer says
so. This has now flipped four times (phantom lines → cut → 30% bridges → gone →
solid), which is why the assertion was reversed rather than deleted.


-----------------------------------------------------------------------------------
2. ROUTES THINNER — and why the dash pattern had to change with them
-----------------------------------------------------------------------------------
                  before        now
    zoom 7         2.0          1.7
    zoom 12        6.0          5.0
    zoom 16       10.0          8.5

The casing keeps the fixed 1.25× ratio (2.13 / 6.25 / 10.6) — it has to, or the
two dash arrays stop drawing the same period on screen.

⚠️ **Thinner lines make the GAP smaller**, because `line-dasharray` is in multiples
of the line's own width. At 2.0 px the zoom-7 gap was 0.67 px; at 1.7 px it would
have been **0.57 px**, which is where a dashed line starts aliasing to solid — at
exactly the zoom this whole change exists for. So the arrays are now **stepped**:

    below zoom 10   core [1, 0.6]    casing [0.96, 0.32]    -> 1.02 px gap at z7
    from zoom 10    core [1, 0.333]  casing [0.96, 0.107]   -> the agreed 3:1

Both step at the same zoom, which is asserted — if they stepped at different
levels the two patterns would diverge for two zoom levels and the halo would
detach from the dots.

⭐ **A side benefit worth knowing.** At zoom 12 the two casings are now 6.25 px
with centres 8 px apart, clearing by 1.75 px. Before the thinning they cleared by
0.5 px, which I had flagged as the risk of the 1.25× ratio. That risk is gone.


-----------------------------------------------------------------------------------
3. DIRECTIONAL ARROWS REMOVED
-----------------------------------------------------------------------------------
Both `inbound-arrows` and `outbound-arrows` layers are deleted.

⚠️ **NOTHING ON THIS MAP NOW INDICATES WHICH WAY A ROUTE RUNS.** A dot does not
point. The arrows were the entire direction channel and they shipped in the nine
quick fixes precisely because the solid lines did not read as two flows. What is
left is **colour** (green laden-out, amber empty-back, both named in the sidebar
legend) and the **±4 px offset**, which separates the two carriageways without
saying which is which.

I have done what you asked rather than argue with it — you have seen it live and I
have not. Saying it once so it is a decision and not an oversight: if a client asks
"which way is the loaded truck going", the answer is now only in the legend.

⭐ Putting them back costs nothing. The layers were self-contained and are in git;
the removal comment sits where they were and says to restore that block rather than
invent a new mechanism.


-----------------------------------------------------------------------------------
4. 🔴 A BUG I FOUND WHILE REMOVING THEM
-----------------------------------------------------------------------------------
`applyFilters()` set a filter on `inbound-lines` and `outbound-lines` — and never
on `inbound-lines-casing` / `outbound-lines-casing`.

So filtering the map to one origin hid the coloured cores and **left every other
route's white casing drawn.** On a light basemap a solid white line under nothing
is easy to miss, which is presumably how it survived since Phase 2.5a. It would
not have stayed easy to miss: the casings are dashed now, so a filtered-out route
would have left a trail of white dots across the map.

**Pre-existing, not caused by the restyle. Fixed and asserted.** Worth a look when
you next use the origin/destination filters — this is the one change here that
alters behaviour you may already have got used to.


-----------------------------------------------------------------------------------
5. TESTS — 1,483, 0 failed
-----------------------------------------------------------------------------------
    parse_map.js       291   (was 284)      test_phase5a.py     182
    test_phase2.py     142                  test_phase3.py      154
    test_phase4.py     202                  test_phase25a.py    104
    test_phase45.py    127                  test_tenant_audit.py 22
    parse_frontend.js  119                  test_ipt_overlay.js 140

⚠️ The five other python harnesses are in this zip because `main.py` subclasses
`StaticFiles` for the /map/ cache fix and they stubbed it as a lambda — `class
X(lambda)` is a TypeError and each one crashed on import. Otherwise unchanged.
Skip them and five suites stop running.

Assertions were REVERSED, not deleted, for both bridges and arrows, per the
standing rule. Both have now flipped more than once and each flip has to be
deliberate rather than a diff nobody questioned.

Two deliberate regressions confirmed the new guards bite: reinstating the bridge
exclusion (1 fail), dropping the casing filter again (1 fail).

🔴 **NOTHING IN THIS DELIVERY HAS BEEN SEEN RENDERED ON A REAL BASEMAP.** No Mapbox
in the sandbox. Every claim is about the numbers in the layer definitions, not the
pixels.


-----------------------------------------------------------------------------------
6. WHAT TO LOOK AT AFTER DEPLOYING
-----------------------------------------------------------------------------------
  1. The corridor should be continuous end to end. Click a stretch between
     115+900 and 122+900 (the 7.0 km hole) — you should get the amber
     "no surveyed alignment" panel.
  2. Zoom 7–9: are the routes still dotted, or has the gap aliased to solid? This
     is the one I could not test and the one the thinning puts most at risk.
  3. Zoom 11–13 with two parallel routes: the casings should now clear
     comfortably.
  4. Filter to a single origin — no white ghost lines should remain (item 4).
  5. Confirm you are content that direction of travel is now legend-only.
  6. Still from the previous zip: sites on the sites, gates as grey dots from
     zoom 10, and `Cache-Control: no-cache` on /map/ in DevTools → Network.

Still owed, unchanged: the E6 gate walkthrough, and the E1 HERE probe which blocks
the laydown phase.

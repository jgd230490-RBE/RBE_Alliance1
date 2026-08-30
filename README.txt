rbe-ipt-overlay-v8.zip — the key now matches the map, and the stale-file trap is closed
2026-08-30. Extract over the repo root. Supersedes every earlier overlay zip except
rbe-ipt-overlay.zip, which you still apply first.

FILES (4, all replacements)
  map/ipt_segments.js
  map/index.html
  backend/tests/parse_map.js         269 assertions
  backend/tests/test_ipt_overlay.js  140 assertions
  Full suite 1,254 assertions, 0 failed.

=====================================================================
1. THE VERSION BANNER - you were probably right that you uploaded it
=====================================================================
The banner reads window.IPT_SEGMENTS_VERSION at load. It said "no version
stamp", which I attributed to a missing upload. There is a likelier cause I
should have named first:

  ** A BROWSER CACHES A .js FAR MORE STUBBORNLY THAN IT REVALIDATES THE .html
     THAT LOADS IT. ** Both files can be uploaded correctly and the browser will
     still run the OLD ipt_segments.js under the NEW index.html - which produces
     exactly the symptom, and is indistinguishable from not uploading it.

Closed properly rather than explained away: the script tag is now

    <script src="ipt_segments.js?v=8"></script>

and a test asserts that number matches the version the page expects, so it can
never be bumped in one place only. A browser cannot serve a stale copy across a
version change again.

The banner now tells you to hard-reload FIRST (Ctrl/Cmd + Shift + R) and only
then to re-upload, because that is the right order of likelihood.

=====================================================================
2. THE KEY DID NOT MATCH THE MAP - two bugs, both mine
=====================================================================
(a) ** The swatch beside "Rail alignment (by IPT)" was a hard-coded gradient of
    the FIRST palette **, written straight into the HTML:

        #039E86, #003787, #0E7490, #C6841D, #BF2E55

    It was never touched by any palette change, so three palettes later the
    sidebar was still advertising inbound-teal, brand-navy and selection-crimson
    as IPT colours. Now generated from the band table at render time, so it
    cannot drift again - and asserted, along with a check that no reserved route
    colour appears in the IPT controls at all.

(b) ** IPT 6's legend row showed the UNDERLAY colour against the band's name. **
    IPT 6 is two things on this map: a civil band in the north drawn in plum, and
    the corridor-wide superstructure underlay drawn in lavender. One row was
    showing lavender against the label "IPT 6" while the map drew plum for the
    same name.

    IPT 6 now gets TWO rows:
      IPT 6 · WS7, WS8, WS9, WS10, WS11        plum, no checkbox (always drawn)
      IPT 6 · Superstructure (corridor underlay)  lavender, checkbox

    The band row has no checkbox because that band is always shown - an asymmetry
    that was decided deliberately and is now visible rather than hidden.

=====================================================================
3. THE INFO BOX
=====================================================================
You said it now reports the correct work section. Noted - but be aware the v7
popup DERIVES the section from the band table when the feature does not carry
it, so a correct popup does not by itself prove the data file is current. The
version banner is the test that does.

TO ACTION
  Nothing by hand. No data file touched.

AFTER UPLOADING
  Hard reload, then check the console:  window.IPT_SEGMENTS_VERSION  -> "v8"
  and no red banner in the sidebar. Then: does the swatch beside "Rail alignment
  (by IPT)" show the six current colours, and do the legend swatches match the
  lines on the map?

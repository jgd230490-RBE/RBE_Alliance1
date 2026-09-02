// ============================================================================
//  EVR rail network - Tallinn - Rapla - Lelle          map/data/evr_rail.js
//  Week 1, Task E. 2026-09-01.
// ============================================================================
//
//  WHAT THIS IS
//  ------------
//  A PROVISIONAL first cut of the existing Eesti Raudtee corridor, so the railhead
//  highlight has something real to bold. It is NOT the Rail Baltica alignment - that
//  is map/data/alignment.js and it is a different railway on a different gauge.
//
//  SOURCE: Natural Earth 10m railroads, public domain, roughly 1:10 000 000.
//  It is NOT OpenStreetMap and it is NOT survey data. The build list asked for an OSM
//  first cut; Overpass, openstreetmap.org, Geofabrik and every Estonian portal are
//  unreachable from the build sandbox, and GitHub raw is the only open source of
//  vector data there. Natural Earth was the best available and its error was measured
//  rather than assumed.
//
//  MEASURED POSITIONAL ERROR, against known station coordinates:
//      Rapla   0.3 km      Saku    0.5 km      Nomme   2.2 km
//      LELLE   6.4 km      <-- Natural Earth puts the junction at ~24.98E; Lelle
//                              station is at 24.8236E.
//
//  🔴 THAT LAST ONE MATTERS. Lelle is one of the railheads this feature exists to
//  serve. Read the line, not the pixel: it shows WHICH corridor a haul relates to,
//  never where the track is. Every feature carries provisional: true and its own
//  accuracy_note, the map popup repeats them, and backend/tests/test_week1.py asserts
//  all three are present so this file cannot quietly start looking authoritative.
//
//  HOW TO REPLACE IT - one file, no code change
//  -------------------------------------------
//  Run this at https://overpass-turbo.eu (paste, Run, then Export > GeoJSON):
//
//      [out:json][timeout:60];
//      area["ISO3166-1"="EE"][admin_level=2]->.ee;
//      way(area.ee)["railway"="rail"]["usage"="main"];
//      out geom;
//
//  Then, for each feature you keep, set:
//      properties.heads          array of location NAMES this line serves
//      properties.source         where it came from
//      properties.provisional    false, once it is real survey or OSM
//      properties.accuracy_note  what its error actually is
//  and drop it in here as window.evr_rail_data. Nothing else changes: map/index.html
//  reads only `heads`, `name`, `source`, `provisional` and `accuracy_note`.
//
//  MATCHING. `heads` is matched case-insensitively and by SUBSTRING in both
//  directions, so a location called "Rapla railhead" matches the head "Rapla".
//  head_ids is reserved for exact location ids and is empty until somebody fills it.
//
//  NOT INCLUDED: the Muuga harbour branch. Natural Earth's nearest line comes no
//  closer than 4.1 km to Muuga and could not be identified as the branch with any
//  confidence, so it was left out rather than guessed at.
// ============================================================================
window.evr_rail_data = {"type":"FeatureCollection","features":[{"type":"Feature","properties":{"id":"EVR-TLN-RAP","name":"Tallinn - Rapla","operator":"Eesti Raudtee (EVR)","heads":["Tallinn","Nomme","Saku","Kohila","Rapla"],"head_ids":[],"source":"Natural Earth 10m railroads (public domain, ~1:10 000 000), nvkelso/natural-earth-vector @ master, extracted 2026-09-01","provisional":true,"length_km":50.2,"accuracy_note":"Measured against known station coordinates: Rapla 0.3 km, Saku 0.5 km, Nomme 2.2 km. Good enough to read as the corridor, NOT survey."},"geometry":{"type":"LineString","coordinates":[[24.70056,59.40611],[24.69833,59.40056],[24.69666,59.39139],[24.695,59.38222],[24.69528,59.37389],[24.69555,59.36556],[24.69583,59.35722],[24.69611,59.34889],[24.69541,59.34718],[24.69278,59.34083],[24.68778,59.33361],[24.68278,59.32639],[24.67611,59.32028],[24.66972,59.31417],[24.66472,59.30722],[24.65972,59.3],[24.65639,59.29194],[24.65305,59.28361],[24.65139,59.27445],[24.65167,59.26611],[24.65389,59.25861],[24.65611,59.25111],[24.65833,59.24361],[24.6625,59.23694],[24.66667,59.23056],[24.67083,59.22389],[24.67694,59.21806],[24.67694,59.21806],[24.68111,59.21139],[24.68722,59.20528],[24.69333,59.19944],[24.6975,59.19278],[24.70361,59.18694],[24.70972,59.18139],[24.71583,59.17528],[24.72,59.16861],[24.72417,59.16222],[24.72833,59.15555],[24.73445,59.14944],[24.73667,59.14222],[24.74083,59.13556],[24.74278,59.12806],[24.745,59.12028],[24.74722,59.11278],[24.75139,59.10611],[24.75361,59.09889],[24.75972,59.09278],[24.76389,59.08611],[24.77,59.08028],[24.77611,59.07472],[24.78222,59.06889],[24.79,59.06361],[24.79806,59.05861],[24.80416,59.05278],[24.80639,59.04528],[24.80667,59.03694],[24.805,59.02778],[24.80166,59.01972],[24.79667,59.01278],[24.79333,59.00444],[24.79347,59.00024],[24.79347,59.00024],[24.79347,59.00022],[24.79347,59.00021],[24.79348,59],[24.79348,59.0],[24.79361,58.99611],[24.7975,58.98944],[24.80361,58.98361],[24.81361,58.97944],[24.81528,58.97861]]}},{"type":"Feature","properties":{"id":"EVR-RAP-LEL","name":"Rapla - Lelle","operator":"Eesti Raudtee (EVR)","heads":["Rapla","Lelle"],"head_ids":[],"source":"Natural Earth 10m railroads (public domain, ~1:10 000 000), nvkelso/natural-earth-vector @ master, extracted 2026-09-01","provisional":true,"length_km":16.6,"accuracy_note":"PROVISIONAL AND WRONG AT THE LELLE END BY ~6.4 km. Natural Earth places the junction south-east of Rapla at about 24.98E; Lelle station is at 24.8236E. Anything that reads a Lelle railhead off this line's geometry will be wrong by that much. Replace before using it for anything but orientation."},"geometry":{"type":"LineString","coordinates":[[24.81528,58.97861],[24.82167,58.97444],[24.83167,58.97028],[24.8386,58.96739],[24.84167,58.96611],[24.85167,58.96195],[24.85945,58.95667],[24.86944,58.95278],[24.8775,58.9475],[24.88556,58.9425],[24.89333,58.9375],[24.89944,58.93167],[24.90555,58.92583],[24.90972,58.91917],[24.91556,58.91333],[24.92167,58.9075],[24.92778,58.90167],[24.93584,58.89667],[24.94167,58.89083],[24.94972,58.88583],[24.95583,58.88],[24.96361,58.87472],[24.96911,58.86948],[24.96972,58.86889],[24.97778,58.86389],[24.98278,58.85889]]}}]};

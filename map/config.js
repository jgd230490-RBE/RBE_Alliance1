// ============================================================
//  RBE Alliance 1 Logistics — Map configuration
//  Edit values here; no need to touch index.html.
// ============================================================
window.CONFIG = {
  // Mapbox public token. This is a browser (pk.) token and is safe to expose,
  // but you SHOULD restrict it by URL in your Mapbox account, and can rotate it here.
  MAPBOX_TOKEN: "pk.eyJ1IjoiamdkMjMwNDE5OTAiLCJhIjoiY21xbnJzaTRrMDYyOTJxcXowczRxNTlxdyJ9.xujuSc3O8RcgKIitWNGIWg",

  // Where the forecast API lives. "/api" = same server that serves this map.
  API_BASE: "/api",

  // Forecast horizon (must match the backend). Month 1 = Jan of START_YEAR.
  START_YEAR: 2026,
  MONTH_COUNT: 60,

  // Unit the map paints forecasts in: "vehicles" | "t" | "m3"
  FORECAST_UNIT: "vehicles",

  // Brand palette (Rail Baltica). Change a hex here to restyle the map.
  COLORS: {
    brand:             "#003787",  // primary navy
    brandDark:         "#0A1446",  // deep navy (headings)
    forecast:          "#3398DB",  // forecast route highlight (core)
    forecastCasing:    "#0A1446",  // darker outline behind forecast routes
    forecastLabelHalo: "#003787",  // halo behind forecast labels
    selection:         "#BF2E55",  // a clicked/selected route
    peak:              "#BF2E55",  // alerts / peak values (theme red)
    green:             "#039E86"   // success / confirmations
  }
};

"""
Look-ahead v2, slice 4 (2026-09-09) — the commit-week export. Browser download, no
email, no upload.

Both builders take the page dict from lookahead.page() and return bytes. They read the
same numbers the screen shows; nothing is recomputed here.

    build_xlsx(page)   sheet 1  day × line (the export mock's columns + € where set)
                       sheet 2  stock at week end
                       sheet 3  clashes
    build_pdf(page)    LANDSCAPE, as many pages as it takes (10 Sep, the human): the
                       week's routes on a map, then ONE ROW PER LINE with Mon…Fri as
                       five separate columns (no collapse rule any more), origin and
                       destination with their coordinates, a week total, flags (not a
                       stop) and the honest-gap footer lines. Header row repeats on
                       every page. Payloads are planning figures; km from HERE unless
                       marked ‡.

2026-09-10, on the human's feedback: **Mon–Fri only** (weekend rows are dropped from
both files — the days still exist at 0 in the database), **no stockpile section** (the
sheet is for the supplier; stock belongs on a look-ahead dashboard, not built), and a
**route map** on the PDF in the stock section's place. The map is Mapbox's Static
Images API when MAPBOX_TOKEN is set and the fetch succeeds (never tested from the
sandbox, which is offline), otherwise a schematic drawn from the baked geometry —
the PDF says which it is. Tark Tee flags come from the STORED per-route check, so the
export is as fast as the page.

openpyxl and reportlab are runtime dependencies of these two functions ONLY — added to
requirements.txt; the app boots without them and the export endpoints say so if they
are missing.
"""
import datetime
import io
import json
import os
import urllib.parse
import urllib.request

import db

MISSING = {}
try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
except Exception as e:                                   # pragma: no cover
    openpyxl = None
    MISSING["xlsx"] = str(e)
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, KeepTogether, LongTable,
                                    PageTemplate, Paragraph, Spacer, TableStyle)
except Exception as e:                                   # pragma: no cover
    rl_canvas = None
    MISSING["pdf"] = str(e)

import config

NAVY = "0B1B45"
NAVY_RGB = (0x0B / 255, 0x1B / 255, 0x45 / 255)
RED_RGB = (0xBF / 255, 0x2E / 255, 0x55 / 255)
BLUE_RGB = (0x1D / 255, 0x4E / 255, 0xD8 / 255)
GREY = (0x64 / 255, 0x74 / 255, 0x8B / 255)
BAND = (0xEF / 255, 0xF6 / 255, 0xFF / 255)

FOOTER = ("€ from the contract rate typed on the route, or the target rate on Config where the "
          "route has none (marked 'target'); not printed where neither exists. € + BAF applies the "
          "fuel surcharge (EU Weekly Oil Bulletin diesel vs the locked base × fuel share) and never "
          "replaces the quote.",
          "Vignette is time-based in Estonia and is not on this sheet. Payloads are planning "
          "figures, not plated. Km from the baked HERE route unless marked ‡.",
          "Mon–Fri only, one row per line; each day cell is qty / trips · vehicles; † = a typed day "
          "that no longer follows the week ÷ 5. Coordinates are "
          "the location's own (WGS84). Road restrictions are Tark Tee's stored check as of its own "
          "date, not re-read for this sheet.",
          )


def _n(v, nd=0):
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if nd == 0:
        return f"{int(round(f)):,}".replace(",", " ")
    return f"{f:,.{nd}f}".replace(",", " ")


def _day_label(iso):
    d = datetime.date.fromisoformat(iso)
    return d.strftime("%a %-d") if hasattr(d, "strftime") else iso


def _week_title(page):
    cw = page.get("commit_week") or {}
    if not cw.get("from"):
        return "no commit week"
    d = datetime.date.fromisoformat(cw["from"])
    return f"week of {d.day} {d.strftime('%b %Y')}"


def _is_weekday(iso):
    return datetime.date.fromisoformat(iso).weekday() <= 4


def _line_rows(page, weekdays_only=True):
    """Flatten commit lines into (line, day) rows in the export mock's column order.
    Mon–Fri only by default (10 Sep): the weekend days exist at 0 and are not printed."""
    rows = []
    for l in page.get("commit", {}).get("lines", []):
        c = l.get("context") or {}
        for d in l.get("days", []):
            if weekdays_only and not _is_weekday(d["day_date"]):
                continue
            f = d.get("derived") or {}
            rows.append({
                "date": d["day_date"], "origin": c.get("origin_name") or c.get("origin_id"),
                "dest": c.get("dest_name") or c.get("dest_id"), "ipt": c.get("ipt") or "",
                "ws": c.get("section_id") or "", "discipline": c.get("discipline") or "",
                "material": c.get("material_type") or "", "vehicle": c.get("vehicle_short") or c.get("vehicle_type") or "",
                "qty": d.get("planned_qty"), "unit": c.get("unit"),
                "tonnes": f.get("tonnes"), "trips": f.get("trips"), "veh": f.get("vehicles"),
                "km_trip": c.get("km_trip"), "km_day": f.get("km_day"), "tonne_km": f.get("tonne_km"),
                "eur": f.get("eur"), "rate_source": c.get("rate_source") or "",
                "eur_adj": f.get("eur_adj"), "cycle_min": c.get("cycle_min"),
                "cycle_mark": c.get("cycle_mark") or ("" if c.get("baked") else "—"),
                "baked": c.get("baked"), "status": d.get("status"),
                "days_ne_week": l.get("days_ne_week"), "week_qty": (l.get("week") or {}).get("planned_qty"),
                "route_id": l.get("route_id"), "line": l, "day": d,
            })
    return rows


# --------------------------------------------------------------------------- #
#  XLSX                                                                        #
# --------------------------------------------------------------------------- #
XLSX_COLS = [("Date", "date"), ("Route", "route_id"), ("Origin", "origin"), ("Destination", "dest"),
             ("IPT", "ipt"), ("WS", "ws"), ("Discipline", "discipline"), ("Material", "material"),
             ("Vehicle", "vehicle"), ("Qty", "qty"), ("Unit", "unit"), ("Tonnes", "tonnes"),
             ("Trips", "trips"), ("Vehicles", "veh"), ("km/trip", "km_trip"), ("km/day", "km_day"),
             ("t·km", "tonne_km"), ("€", "eur"), ("€ source", "rate_source"), ("€ + BAF", "eur_adj"),
             ("Cycle min", "cycle_min"), ("Cycle source", "cycle_mark"),
             ("Day status", "status"), ("Week qty", "week_qty"), ("Days ≠ week", "days_ne_week")]


def build_xlsx(page):
    if openpyxl is None:
        raise RuntimeError("openpyxl is not installed: " + MISSING.get("xlsx", ""))
    wb = openpyxl.Workbook()
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor=NAVY)

    def sheet(ws, cols, rows):
        for j, (title, _) in enumerate(cols, 1):
            c = ws.cell(row=1, column=j, value=title)
            c.font, c.fill = head, fill
            c.alignment = Alignment(horizontal="center")
        for i, r in enumerate(rows, 2):
            for j, (_, key) in enumerate(cols, 1):
                v = r.get(key)
                if isinstance(v, bool):
                    v = "yes" if v else ""
                ws.cell(row=i, column=j, value=v)
        for j, (title, _) in enumerate(cols, 1):
            ws.column_dimensions[get_column_letter(j)].width = max(10, min(28, len(title) + 6))
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "Commit week"
    rows = _line_rows(page)
    sheet(ws, XLSX_COLS, rows)
    # no Stock sheet (10 Sep): the sheet is for the supplier; stock is the planner's
    # 10 Sep evening: the fuel index and settings every € + BAF above was computed from
    ws_f = wb.create_sheet("Fuel")
    cost = page.get("costing") or {}
    fu = cost.get("fuel") or {}
    for i, (k, v) in enumerate((
            ("Diesel index €/L", cost.get("index_eur_per_l")),
            ("Bulletin date", cost.get("index_bulletin_date")),
            ("Index source", cost.get("index_source")),
            ("Attribution", "EU Weekly Oil Bulletin via EuroOilWatch" if cost.get("index_source") else ""),
            ("BAF base €/L", fu.get("baf_base_eur_per_l")),
            ("BAF base bulletin date", fu.get("baf_base_bulletin_date")),
            ("Fuel share %", fu.get("share_pct")),
            ("BAF %", (round(cost["baf_pct"] * 100, 2) if cost.get("baf_pct") is not None else None)),
            ("BAF not applied because", cost.get("baf_reason") or ""),
            # the yard price and the target rates are the planner's own numbers and are
            # NOT on the supplier's sheet — only the public index and the BAF terms
            ("Lines priced at the target rate", (page.get("commit", {}).get("totals") or {}).get("eur_target_lines"))), 1):
        ws_f.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws_f.cell(row=i, column=2, value=v)
    ws_f.column_dimensions["A"].width = 34
    ws_f.column_dimensions["B"].width = 40
    ws3 = wb.create_sheet("Clashes")
    sheet(ws3, [("Code", "code"), ("Route", "route_id"), ("IPT", "ipt"), ("WS", "section_id"),
                ("Day", "day_date"), ("Detail", "text")],
          (page.get("clashes") or {}).get("flags") or [])
    ws4 = wb.create_sheet("About")
    about = [
        ("Sheet", f"Alliance 1 · {_week_title(page)} · commit week"),
        ("Generated", datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"),
        ("Bucket", page.get("bucket")),
        ("Lines", (page.get("commit", {}).get("totals") or {}).get("lines")),
        ("Unbaked lines", (page.get("commit", {}).get("totals") or {}).get("unbaked_lines")),
        ("Tark Tee", ((page.get("clashes") or {}).get("sources") or {}).get("tark_tee")),
        ("Tark Tee checked", ((page.get("clashes") or {}).get("sources") or {}).get("tark_tee_checked_at")),
        ("Days", "Mon–Fri only; Sat/Sun are 0 and not listed"),
    ] + [("Note", f) for f in FOOTER]
    for i, (k, v) in enumerate(about, 1):
        ws4.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws4.cell(row=i, column=2, value=v)
    ws4.column_dimensions["A"].width = 16
    ws4.column_dimensions["B"].width = 100
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
#  The route map (2026-09-10)                                                  #
# --------------------------------------------------------------------------- #
def _encode_polyline(coords, precision=5):
    """Google encoded polyline of [(lon, lat), …] — what Mapbox's static path takes."""
    out, last_lat, last_lon, f = [], 0, 0, 10 ** precision
    for lon, lat in coords:
        ilat, ilon = int(round(lat * f)), int(round(lon * f))
        for v in (ilat - last_lat, ilon - last_lon):
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        last_lat, last_lon = ilat, ilon
    return "".join(out)


def _thin(coords, n=120):
    if len(coords) <= n:
        return coords
    step = (len(coords) - 1) / (n - 1)
    return [coords[int(round(i * step))] for i in range(n)]


def route_geometries(page):
    """[(route_id, origin_name, dest_name, [(lon, lat), …])] for the lines on the page —
    the loaded alt-0 geometry for the line's own vehicle, else any baked profile. Since
    10 Sep this is lookahead.week_geometry(), the same read the Commit view's map makes
    (three queries for the whole page, not two per route). Unbaked routes are absent."""
    import lookahead
    lines = page.get("commit", {}).get("lines", [])
    out = []
    for g in lookahead.week_geometry([l.get("route_id") for l in lines],
                                     {l.get("route_id"): (l.get("context") or {}).get("vehicle_type") for l in lines}):
        if g.get("geometry") and len(g["geometry"]) >= 2:
            out.append((g["route_id"], g["origin"]["name"], g["dest"]["name"],
                        [(float(p[0]), float(p[1])) for p in g["geometry"]]))
    return out


def mapbox_static_png(routes, size="1200x600", timeout=12, token=None):
    """
    A PNG of the routes over Mapbox's light style, or None. Uses config.mapbox_token()
    — MAPBOX_TOKEN on Render when set, else the same public token the browser map
    uses (until 10 Sep this read only the env var, which Render never had, so every
    PDF fell back to the schematic). Needs a network path to api.mapbox.com; the build
    sandbox has none, so the fetch itself is exercised only through a stub.
    """
    token = config.mapbox_token() if token is None else token
    if not token or not routes:
        return None
    overlays = []
    for i, (rid, o, d, coords) in enumerate(routes):
        thin = _thin(coords)
        overlays.append("path-3+0B1B45-0.9(" + urllib.parse.quote(_encode_polyline(thin), safe="") + ")")
        overlays.append(f"pin-s-{chr(97 + (i % 26))}+0B1B45({thin[0][0]:.5f},{thin[0][1]:.5f})")
        overlays.append(f"pin-s-{chr(97 + (i % 26))}+BF2E55({thin[-1][0]:.5f},{thin[-1][1]:.5f})")
    url = ("https://api.mapbox.com/styles/v1/mapbox/light-v11/static/" + ",".join(overlays)
           + f"/auto/{size}@2x?padding=40&access_token=" + urllib.parse.quote(token))
    if len(url) > 8000:                       # the API's URL limit; drop pins, thin harder
        overlays = ["path-3+0B1B45-0.9(" + urllib.parse.quote(_encode_polyline(_thin(c, 60)), safe="") + ")"
                    for (_, _, _, c) in routes]
        url = ("https://api.mapbox.com/styles/v1/mapbox/light-v11/static/" + ",".join(overlays)
               + f"/auto/{size}@2x?padding=40&access_token=" + urllib.parse.quote(token))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RBE-Alliance1/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        return data if data[:8] == b"\x89PNG\r\n\x1a\n" else None
    except Exception:
        return None


def _draw_schematic(c, routes, x, y, w, h):
    """The fallback: every route as a line scaled into the box, ends labelled."""
    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.setLineWidth(0.5)
    c.rect(x, y, w, h, stroke=1, fill=0)
    pts = [p for (_, _, _, cs) in routes for p in cs]
    if not pts:
        c.setFont("Helvetica", 8); c.setFillColorRGB(*GREY)
        c.drawString(x + 3 * mm, y + h / 2, "no baked route to draw")
        return
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    lo_x, hi_x, lo_y, hi_y = min(lons), max(lons), min(lats), max(lats)
    import math
    kx = math.cos(math.radians((lo_y + hi_y) / 2)) or 1.0    # lon degrees are shorter than lat degrees
    span_x, span_y = max((hi_x - lo_x) * kx, 1e-6), max(hi_y - lo_y, 1e-6)
    pad = 6 * mm
    scale = min((w - 2 * pad) / span_x, (h - 2 * pad) / span_y)
    ox = x + (w - span_x * scale) / 2
    oy = y + (h - span_y * scale) / 2
    def P(p):
        return ox + (p[0] - lo_x) * kx * scale, oy + (p[1] - lo_y) * scale
    for i, (rid, o, d, cs) in enumerate(routes):
        c.setStrokeColorRGB(*NAVY_RGB); c.setLineWidth(1.4)
        path = c.beginPath()
        path.moveTo(*P(cs[0]))
        for p in cs[1:]:
            path.lineTo(*P(p))
        c.drawPath(path, stroke=1, fill=0)
        sx, sy = P(cs[0]); ex, ey = P(cs[-1])
        c.setFillColorRGB(*NAVY_RGB); c.circle(sx, sy, 1.6, stroke=0, fill=1)
        c.setFillColorRGB(*RED_RGB); c.circle(ex, ey, 1.6, stroke=0, fill=1)
        c.setFont("Helvetica", 6.5); c.setFillColorRGB(*NAVY_RGB)
        c.drawString(sx + 2, sy + 2, f"{o}"[:24])
        c.setFillColorRGB(*RED_RGB)
        c.drawString(ex + 2, ey - 6, f"{d} ({rid})"[:30])


def draw_route_map(c, page, x, y, w, h):
    """The map block: Mapbox static when it can be had, else the schematic. Returns
    which one was drawn, so the caption can say so."""
    routes = route_geometries(page)
    png = mapbox_static_png(routes)
    if png:
        try:
            c.drawImage(ImageReader(io.BytesIO(png)), x, y, width=w, height=h,
                        preserveAspectRatio=True, anchor="c")
            return "mapbox"
        except Exception:
            pass
    _draw_schematic(c, routes, x, y, w, h)
    return "schematic"


# --------------------------------------------------------------------------- #
#  PDF — landscape, one row per line, Mon…Fri as columns, as many pages as needed #
# --------------------------------------------------------------------------- #
class _MapFlowable(Flowable):
    """The route map as a platypus flowable: Mapbox static when it can be had, else the
    schematic. Records which one it drew in `kind` for the caption that follows it."""
    def __init__(self, page, width, height):
        Flowable.__init__(self)
        self.page, self.width, self.height, self.kind = page, width, height, None

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        self.kind = draw_route_map(self.canv, self.page, 0, 0, self.width, self.height)


def _coord(lat, lon):
    return f"{lat:.5f}, {lon:.5f}" if lat is not None and lon is not None else "no coordinates"


def _pdf_rows(page):
    """One entry per line: the context, the five weekday cells (Mon..Fri in order), and
    the week's totals. Sorted by origin, then route, then WS — the supplier reads by
    where the material leaves from."""
    import lookahead
    lines = page.get("commit", {}).get("lines", [])
    ends = {g["route_id"]: g for g in lookahead.week_geometry(
        [l.get("route_id") for l in lines],
        {l.get("route_id"): (l.get("context") or {}).get("vehicle_type") for l in lines})}
    out = []
    for l in lines:
        c = l.get("context") or {}
        wd = sorted([d for d in l.get("days", []) if _is_weekday(d["day_date"])], key=lambda d: d["day_date"])
        g = ends.get(l.get("route_id")) or {}
        out.append({"line": l, "ctx": c, "days": wd, "geo": g,
                    "origin": c.get("origin_name") or c.get("origin_id") or "—",
                    "dest": c.get("dest_name") or c.get("dest_id") or "—",
                    "wk": l.get("week_derived") or {}})
    out.sort(key=lambda r: (r["origin"], r["line"].get("route_id") or "", r["ctx"].get("section_id") or ""))
    return out


def build_pdf(page):
    if rl_canvas is None:
        raise RuntimeError("reportlab is not installed: " + MISSING.get("pdf", ""))
    buf = io.BytesIO()
    W, H = landscape(A4)
    lm = rm = 10 * mm
    top, bottom = 20 * mm, 12 * mm
    usable = W - lm - rm
    lines = page.get("commit", {}).get("lines", [])
    confirmed_all = bool(lines) and all(((l.get("week") or {}).get("status") == "confirmed") for l in lines)
    title = f"Alliance 1 · {_week_title(page)}"

    def on_page(c, doc):
        c.saveState()
        c.setStrokeColorRGB(*NAVY_RGB); c.setLineWidth(2)
        c.line(lm, H - 8 * mm, W - rm, H - 8 * mm)
        c.setFont("Helvetica-Bold", 14); c.setFillColorRGB(*NAVY_RGB)
        c.drawString(lm, H - 14 * mm, title)
        c.setFont("Helvetica-Bold", 9)
        c.setFillColorRGB(*(NAVY_RGB if confirmed_all else RED_RGB))
        c.drawRightString(W - rm, H - 14 * mm, "CONFIRMED" if confirmed_all else "DRAFT — not confirmed")
        c.setFont("Helvetica", 7.5); c.setFillColorRGB(*GREY)
        c.drawString(lm, H - 18 * mm, "Commitment sheet · not a delivery note · planning payloads, not plated · Mon–Fri only")
        c.drawRightString(W - rm, 7 * mm, f"page {doc.page}")
        c.restoreState()

    doc = BaseDocTemplate(buf, pagesize=(W, H), leftMargin=lm, rightMargin=rm, topMargin=top,
                          bottomMargin=bottom, title=title, pageCompression=0)
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(lm, bottom, usable, H - top - bottom, id="f",
                                                                leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)],
                                       onPage=on_page)])

    st = ParagraphStyle("b", fontName="Helvetica", fontSize=7.5, leading=9)
    st_sm = ParagraphStyle("s", parent=st, fontSize=6.5, leading=7.5, textColor=colors.Color(*GREY))
    st_h = ParagraphStyle("h", parent=st, fontName="Helvetica-Bold", fontSize=7, leading=8.5, textColor=colors.white)
    st_hc = ParagraphStyle("hc", parent=st_h, alignment=1)
    st_r = ParagraphStyle("r", parent=st, alignment=2)
    st_c = ParagraphStyle("c", parent=st, alignment=1)
    st_grey = ParagraphStyle("g", parent=st, fontSize=8, leading=10, textColor=colors.Color(*GREY))
    st_navy = ParagraphStyle("n", parent=st, fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=colors.Color(*NAVY_RGB))
    st_red = ParagraphStyle("rd", parent=st, fontSize=8, leading=10, textColor=colors.Color(*RED_RGB))

    def P(txt, style=st):
        return Paragraph(str(txt), style)

    def E(v):
        """Data into markup: escape it. The markup itself (<b>, <br/>, <font>) is ours."""
        return str(v if v is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story = []
    # 1. the map, full width
    map_h = 92 * mm
    mp = _MapFlowable(page, usable, map_h)
    story.append(mp)
    story.append(_Caption(page, mp, st_grey))
    story.append(Spacer(1, 3 * mm))

    # 2. the table
    rows = _pdf_rows(page)
    dates = sorted({d["day_date"] for r in rows for d in r["days"]})[:5]
    priced = (page.get("commit", {}).get("totals") or {}).get("eur") is not None
    today = page.get("today")
    head = [P("ROUTE", st_h), P("ORIGIN<br/><font size=6>name · lat, lon</font>", st_h),
            P("DESTINATION<br/><font size=6>name · lat, lon</font>", st_h), P("IPT / WS", st_h),
            P("MATERIAL", st_h), P("VEHICLE", st_h)]
    for iso in dates:
        d = datetime.date.fromisoformat(iso)
        head.append(P(f"{d.strftime('%a %-d %b').upper()}{' · TODAY' if iso == today else ''}"
                      f"<br/><font size=6>qty / trips · veh</font>", st_hc))
    head += [P("WEEK<br/><font size=6>qty · trips · t·km</font>", st_hc), P("KM/TRIP", st_hc)]
    if priced:
        head.append(P("€ WEEK", st_hc))
    data = [head]
    tot = {"t": 0.0, "trips": 0, "tkm": 0.0, "eur": 0.0, "eur_any": False}
    for r in rows:
        c, g, wk, l = r["ctx"], r["geo"], r["wk"], r["line"]
        o, d = g.get("origin") or {}, g.get("dest") or {}
        by_date = {x["day_date"]: x for x in r["days"]}
        row = [P(f"<b>{E(l.get('route_id'))}</b>", st),
               P(f"<b>{E(r['origin'])}</b><br/><font size=6.5 color='#64748B'>{_coord(o.get('lat'), o.get('lon'))}</font>", st),
               P(f"<b>{E(r['dest'])}</b><br/><font size=6.5 color='#64748B'>{_coord(d.get('lat'), d.get('lon'))}</font>", st),
               P(f"{E(c.get('ipt') or '—')}<br/>{E(c.get('section_id') or '')}", st),
               P(E(c.get("material_type") or "—"), st),
               P(f"{E(c.get('vehicle_short') or c.get('vehicle_type') or '—')}"
                 + ("" if c.get("baked") else "<br/><font size=6 color='#B45309'>not baked</font>"), st)]
        for iso in dates:
            x = by_date.get(iso)
            if not x:
                row.append(P("—", st_c)); continue
            f = x.get("derived") or {}
            qty = f"{_n(x.get('planned_qty'))} {c.get('unit') or ''}".strip()
            sub = (f"{_n(f.get('trips'))} tr · {_n(f.get('vehicles')) if c.get('baked') else '—'} veh"
                   if x.get("planned_qty") else "0")
            mark = " †" if x.get("status") == "edited" else ""       # Helvetica has no ✎
            row.append(P(f"<b>{qty}</b>{mark}<br/><font size=6.5 color='#64748B'>{sub}</font>", st_c))
        wq = (l.get("week") or {}).get("planned_qty")
        row.append(P(f"<b>{_n(wq)} {c.get('unit') or ''}</b><br/><font size=6.5 color='#64748B'>{_n(wk.get('trips'))} tr · "
                     f"{_n(wk.get('tonne_km')) if c.get('baked') else '—'} t·km</font>", st_c))
        row.append(P((_n(c.get("km_trip")) + (c.get("cycle_mark") or "")) if c.get("baked") else "—", st_c))
        if priced:
            if wk.get("eur") is None:
                row.append(P("—", st_c))
            else:
                tail = ("<br/><font size=6 color='#64748B'>target</font>" if c.get("rate_source") == "target" else "")
                if wk.get("eur_adj") is not None:
                    tail += f"<br/><font size=6.5 color='#64748B'>+BAF {_n(wk['eur_adj'])}</font>"
                row.append(P(_n(wk.get("eur")) + tail, st_c))
        data.append(row)
        tot["t"] += float(wk.get("tonnes") or 0)
        tot["trips"] += int(wk.get("trips") or 0)
        tot["tkm"] += float(wk.get("tonne_km") or 0)
        if wk.get("eur") is not None:
            tot["eur"] += float(wk["eur"]); tot["eur_any"] = True
        if wk.get("eur_adj") is not None:
            tot["eur_adj"] = tot.get("eur_adj", 0.0) + float(wk["eur_adj"])

    if rows:
        n_fixed = 6
        ndays = len(dates)
        day_w = 19 * mm
        widths = [13 * mm, 33 * mm, 33 * mm, 15 * mm, 22 * mm, 18 * mm] + [day_w] * ndays + [27 * mm, 13 * mm]
        if priced:
            # wider when a + BAF line sits under the quote, so "+BAF 9 896" does not wrap
            widths.append(20 * mm if (page.get("costing") or {}).get("baf_pct") is not None else 14 * mm)
        # whatever is left after the fixed columns goes to origin / destination
        spare = usable - sum(widths)
        widths[1] += spare / 2; widths[2] += spare / 2
        total_row = [P("<b>TOTAL</b>", st), P(f"{len(rows)} line(s)", st), "", "", "", ""] + [""] * ndays + [
            P(f"<b>{_n(tot['t'])} t</b><br/><font size=6.5 color='#64748B'>{_n(tot['trips'])} tr · {_n(tot['tkm'])} t·km</font>", st_c), ""]
        if priced:
            adj = (f"<br/><font size=6.5 color='#64748B'>+BAF {_n(tot['eur_adj'])}</font>"
                   if tot.get("eur_adj") is not None else "")
            total_row.append(P((f"<b>{_n(tot['eur'])}</b>" + adj) if tot["eur_any"] else "—", st_c))
        data.append(total_row)
        t = LongTable(data, colWidths=widths, repeatRows=1)
        style = [("BACKGROUND", (0, 0), (-1, 0), colors.Color(*NAVY_RGB)),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.Color(0.85, 0.87, 0.9)),
                 ("LINEAFTER", (0, 0), (-2, -1), 0.3, colors.Color(0.9, 0.92, 0.95)),
                 ("BOX", (0, 0), (-1, -1), 0.6, colors.Color(0.75, 0.78, 0.83)),
                 ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                 ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                 ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.Color(0.97, 0.98, 0.99)]),
                 ("BACKGROUND", (0, -1), (-1, -1), colors.Color(*BAND)),
                 ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.Color(*NAVY_RGB))]
        if today in dates:
            ci = n_fixed + dates.index(today)
            style.append(("BACKGROUND", (ci, 1), (ci, -2), colors.Color(0.94, 0.97, 1.0)))
        t.setStyle(TableStyle(style))
        story.append(t)
    else:
        story.append(P("No approved forecast line in this commit week.", st_grey))
    story.append(Spacer(1, 4 * mm))

    # 3. flags
    flags = (page.get("clashes") or {}).get("flags") or []
    fl = [P("Flags (not a stop)", st_navy)]
    if flags:
        for f in flags[:12]:
            fl.append(P(E(f"{f['code']}: {f['text']}"[:160]), st_red))
        if len(flags) > 12:
            fl.append(P(f"+{len(flags) - 12} more on the XLSX", st_red))
    else:
        tt = ((page.get("clashes") or {}).get("sources") or {}).get("tark_tee")
        fl.append(P("none" + (" · Tark Tee unavailable, restrictions not checked" if tt == "unavailable" else ""), st_grey))
    story.append(KeepTogether(fl))
    story.append(Spacer(1, 3 * mm))

    # 4. footer lines + the confirmation stamp
    stamp = None
    for l in lines:
        if (l.get("week") or {}).get("confirmed_at"):
            stamp = l["week"]["confirmed_at"]
    ft = [P(E(f), st_grey) for f in FOOTER]
    ft.append(P(E(_fuel_line(page)), st_grey))
    ft.append(P((f"Confirmed {stamp}. " if stamp else "Not yet confirmed. ") + "Re-open the week in Look-ahead to change the plan.", st_grey))
    story.append(KeepTogether(ft))
    doc.build(story)
    return buf.getvalue()


def _fuel_line(page):
    """One footer line naming the diesel index the sheet's € + BAF used — or that none did."""
    cost = page.get("costing") or {}
    fu = cost.get("fuel") or {}
    if cost.get("index_eur_per_l") is None:
        return "Diesel index: not available (EU Weekly Oil Bulletin via EuroOilWatch not reached, nothing typed)."
    s = (f"Diesel {cost.get('fuel', {}).get('country') or 'EE'} €{cost['index_eur_per_l']:.3f}/L, "
         f"bulletin {cost.get('index_bulletin_date') or '—'} (EU Weekly Oil Bulletin via EuroOilWatch"
         + (", typed" if cost.get("index_source") == "manual" else "") + ").")
    if cost.get("baf_pct") is not None:
        s += (f" BAF {cost['baf_pct'] * 100:+.2f} % = (index / base €{fu.get('baf_base_eur_per_l'):.3f} of "
              f"{fu.get('baf_base_bulletin_date') or '—'} − 1) × {fu.get('share_pct'):g} % fuel share.")
    else:
        s += f" No BAF applied ({cost.get('baf_reason') or 'not set up'})."
    return s


class _Caption(Flowable):
    """The line under the map — written AFTER the map has drawn, so it can say which
    map it was (Mapbox tiles or the schematic)."""
    def __init__(self, page, map_flowable, style):
        Flowable.__init__(self)
        self.page, self.mp, self.style = page, map_flowable, style
        self.height = 4.5 * mm

    def wrap(self, aw, ah):
        self.width = aw
        return aw, self.height

    def draw(self):
        n_routes = len({l.get("route_id") for l in self.page.get("commit", {}).get("lines", [])})
        kind = self.mp.kind
        txt = f"Routes this week: {n_routes}" + (
            " · schematic from the baked geometry (no map tiles — Mapbox could not be reached)"
            if kind == "schematic" else " · map © Mapbox © OpenStreetMap")
        self.canv.setFont("Helvetica", 7.5)
        self.canv.setFillColorRGB(*GREY)
        self.canv.drawString(0, 1 * mm, txt)

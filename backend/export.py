"""
Look-ahead v2, slice 4 (2026-09-09) — the commit-week export. Browser download, no
email, no upload.

Both builders take the page dict from lookahead.page() and return bytes. They read the
same numbers the screen shows; nothing is recomputed here.

    build_xlsx(page)   sheet 1  day × line (the export mock's columns + € where set)
                       sheet 2  stock at week end
                       sheet 3  clashes
    build_pdf(page)    the one-pager: grouped by origin (carrier is not a field yet —
                       brief item 7 — so the "then by carrier" grouping is by origin
                       only), the collapse rule where every weekday of a line is
                       identical, flags (not a stop), stock, and the three honest-gap
                       footer lines. Payloads are planning figures; km from HERE unless
                       marked ‡.

openpyxl and reportlab are runtime dependencies of these two functions ONLY — added to
requirements.txt; the app boots without them and the export endpoints say so if they
are missing.
"""
import datetime
import io

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
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas
except Exception as e:                                   # pragma: no cover
    rl_canvas = None
    MISSING["pdf"] = str(e)

NAVY = "0B1B45"
NAVY_RGB = (0x0B / 255, 0x1B / 255, 0x45 / 255)
RED_RGB = (0xBF / 255, 0x2E / 255, 0x55 / 255)
BLUE_RGB = (0x1D / 255, 0x4E / 255, 0xD8 / 255)
GREY = (0x64 / 255, 0x74 / 255, 0x8B / 255)
BAND = (0xEF / 255, 0xF6 / 255, 0xFF / 255)

FOOTER = ("€ not printed where no contract rate is typed on the route.",
          "Vignette is time-based in Estonia and is not on this sheet. Payloads are planning "
          "figures, not plated. Km from the baked HERE route unless marked ‡.",
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


def _line_rows(page):
    """Flatten commit lines into (line, day) rows in the export mock's column order."""
    rows = []
    for l in page.get("commit", {}).get("lines", []):
        c = l.get("context") or {}
        for d in l.get("days", []):
            f = d.get("derived") or {}
            rows.append({
                "date": d["day_date"], "origin": c.get("origin_name") or c.get("origin_id"),
                "dest": c.get("dest_name") or c.get("dest_id"), "ipt": c.get("ipt") or "",
                "ws": c.get("section_id") or "", "discipline": c.get("discipline") or "",
                "material": c.get("material_type") or "", "vehicle": c.get("vehicle_short") or c.get("vehicle_type") or "",
                "qty": d.get("planned_qty"), "unit": c.get("unit"),
                "tonnes": f.get("tonnes"), "trips": f.get("trips"), "veh": f.get("vehicles"),
                "km_trip": c.get("km_trip"), "km_day": f.get("km_day"), "tonne_km": f.get("tonne_km"),
                "eur": f.get("eur"), "cycle_min": c.get("cycle_min"),
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
             ("t·km", "tonne_km"), ("€", "eur"), ("Cycle min", "cycle_min"), ("Cycle source", "cycle_mark"),
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
    ws2 = wb.create_sheet("Stock")
    sheet(ws2, [("Pile", "name"), ("Unit", "unit"), ("Capacity", "capacity_qty"), ("Opening", "opening"),
                ("Inbound planned", "inbound_planned"), ("Consume", "consume"), ("Forecast", "forecast"),
                ("Remaining", "remaining"), ("Over", "over"), ("Over by", "over_by")],
          page.get("stock") or [])
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
#  PDF one-pager                                                               #
# --------------------------------------------------------------------------- #
def _collapse(days_rows):
    """
    The collapse rule: when every WEEKDAY row of a line is identical in qty/trips/veh,
    return that one row; else None. Weekend rows (0) are printed separately.
    """
    wd = [r for r in days_rows if datetime.date.fromisoformat(r["date"]).weekday() <= 4]
    if len(wd) < 2:
        return None
    sig = {(r["qty"], r["trips"], r["veh"]) for r in wd}
    return wd[0] if len(sig) == 1 else None


def build_pdf(page):
    if rl_canvas is None:
        raise RuntimeError("reportlab is not installed: " + MISSING.get("pdf", ""))
    buf = io.BytesIO()
    W, H = A4
    c = rl_canvas.Canvas(buf, pagesize=A4, pageCompression=0)
    c.setTitle(f"Alliance 1 · {_week_title(page)}")
    lm, rm = 16 * mm, W - 16 * mm
    y = H - 14 * mm

    def line_h(n=1):
        nonlocal y
        y -= 4.6 * mm * n
        if y < 22 * mm:
            c.showPage()
            y = H - 16 * mm

    def text(x, s, size=9, bold=False, rgb=(0, 0, 0)):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.setFillColorRGB(*rgb)
        c.drawString(x, y, s)

    def rtext(x, s, size=9, bold=False, rgb=(0, 0, 0)):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.setFillColorRGB(*rgb)
        c.drawRightString(x, y, s)

    # top rule + title
    c.setStrokeColorRGB(*NAVY_RGB)
    c.setLineWidth(2)
    c.line(lm, H - 8 * mm, rm, H - 8 * mm)
    confirmed_all = all(((l.get("week") or {}).get("status") == "confirmed")
                        for l in page.get("commit", {}).get("lines", [])) and page.get("commit", {}).get("lines")
    text(lm, f"Alliance 1 · {_week_title(page)}", 15, True, NAVY_RGB)
    rtext(rm, "CONFIRMED" if confirmed_all else "DRAFT — not confirmed", 9, True, NAVY_RGB if confirmed_all else RED_RGB)
    line_h(1.3)
    text(lm, "Commitment sheet · not a delivery note · planning payloads, not plated", 9, False, GREY)
    line_h(2)

    cols = [("DATE", lm, "l"), ("IPT / WS", lm + 22 * mm, "l"), ("DEST", lm + 48 * mm, "l"),
            ("MATERIAL", lm + 82 * mm, "l"), ("QTY", lm + 118 * mm, "r"), ("TRIPS", lm + 132 * mm, "r"),
            ("VEH", lm + 144 * mm, "r"), ("KM/TRIP", lm + 160 * mm, "r"), ("t·km", rm, "r")]
    if (page.get("commit", {}).get("totals") or {}).get("eur") is not None:
        cols = cols[:-1] + [("t·km", lm + 172 * mm, "r"), ("€", rm, "r")]

    rows = _line_rows(page)
    by_origin = {}
    for r in rows:
        by_origin.setdefault(r["origin"] or "—", []).append(r)

    if not rows:
        text(lm, "No approved forecast line in this commit week.", 10, False, GREY)
        line_h()

    for origin, rs in by_origin.items():
        text(lm, f"Origin: {origin}", 11, False, NAVY_RGB)
        line_h()
        text(lm, "Forward this block to the railhead / haulier.", 8.5, False, GREY)
        line_h(1.2)
        # header band
        c.setFillColorRGB(*NAVY_RGB)
        c.rect(lm, y - 1.5 * mm, rm - lm, 5.5 * mm, stroke=0, fill=1)
        for title, x, al in cols:
            (rtext if al == "r" else text)(x, title, 7.5, False, (1, 1, 1))
        line_h(1.3)
        # per line within the origin
        by_line = {}
        for r in rs:
            by_line.setdefault(r["route_id"] + "|" + r["ws"] + "|" + r["discipline"], []).append(r)
        tot = {"t": 0.0, "trips": 0, "veh": 0, "tkm": 0.0, "eur": 0.0, "eur_any": False}
        for key, drs in by_line.items():
            drs.sort(key=lambda r: r["date"])
            col = _collapse(drs)
            wk = [r for r in drs if datetime.date.fromisoformat(r["date"]).weekday() <= 4]
            we = [r for r in drs if datetime.date.fromisoformat(r["date"]).weekday() > 4]
            printed = ([("MON–FRI EACH DAY", col)] if col else [(_day_label(r["date"]), r) for r in wk])
            for lab, r in printed:
                if col:
                    c.setFillColorRGB(*NAVY_RGB)
                    c.rect(lm, y - 1.5 * mm, rm - lm, 5.5 * mm, stroke=0, fill=1)
                    text(lm, lab, 7.5, True, (1, 1, 1))
                    prov = "HERE" if r["baked"] and not r["cycle_mark"] else ("‡" if r["baked"] else "not baked")
                    text(lm + 48 * mm, f"{r['dest']} · {r['material']} · {_n(r['qty'])} {r['unit']} · "
                                       f"{_n(r['trips'])} trips · {_n(r['veh'])} veh · {_n(r['km_trip'])} km · {prov}",
                         7.5, False, (1, 1, 1))
                else:
                    today = page.get("today")
                    rgb = BLUE_RGB if r["date"] == today else (0, 0, 0)
                    text(lm, lab, 8.5, False, rgb)
                    text(lm + 22 * mm, f"{r['ipt']} · {r['ws']}", 8.5)
                    text(lm + 48 * mm, (r["dest"] or "")[:22], 8.5)
                    text(lm + 82 * mm, (r["material"] or "")[:22], 8.5)
                    rtext(lm + 118 * mm, f"{_n(r['qty'])} {r['unit'] or ''}", 8.5)
                    rtext(lm + 132 * mm, _n(r["trips"]), 8.5)
                    rtext(lm + 144 * mm, _n(r["veh"]), 8.5)
                    rtext(lm + 160 * mm, (_n(r["km_trip"]) + (r["cycle_mark"] or "")) if r["baked"] else "—", 8.5)
                    rtext(cols[-2][1] if len(cols) == 10 else rm, _n(r["tonne_km"]) if r["baked"] else "—", 8.5)
                    if len(cols) == 10:
                        rtext(rm, _n(r["eur"]) if r["eur"] is not None else "—", 8.5)
                line_h()
            for r in we:
                text(lm, _day_label(r["date"]), 8.5, False, GREY)
                rtext(lm + 118 * mm, _n(r["qty"]), 8.5, False, GREY)
                line_h()
            for r in drs:
                tot["t"] += float(r["tonnes"] or 0)
                tot["trips"] += int(r["trips"] or 0)
                tot["veh"] = max(tot["veh"], int(r["veh"] or 0))
                tot["tkm"] += float(r["tonne_km"] or 0)
                if r["eur"] is not None:
                    tot["eur"] += float(r["eur"]); tot["eur_any"] = True
        # total band
        c.setFillColorRGB(*BAND)
        c.rect(lm, y - 1.5 * mm, rm - lm, 5.5 * mm, stroke=0, fill=1)
        s = (f"{origin} total  {_n(tot['t'])} t · {_n(tot['trips'])} trips · {tot['veh']} veh peak · "
             f"{_n(tot['tkm'])} t·km" + (f" · € {_n(tot['eur'])}" if tot["eur_any"] else ""))
        text(lm + 2 * mm, s, 9, False, NAVY_RGB)
        line_h(2)

    # flags
    flags = (page.get("clashes") or {}).get("flags") or []
    text(lm, "Flags (not a stop)", 11)
    line_h()
    if flags:
        c.setFillColorRGB(1, 0.95, 0.95)
        c.rect(lm, y - (len(flags[:8]) * 4.6 - 3) * mm, rm - lm, (len(flags[:8]) * 4.6 + 1.5) * mm, stroke=0, fill=1)
        for f in flags[:8]:
            text(lm + 2 * mm, f"{f['code']}: {f['text']}"[:120], 8.5, False, RED_RGB)
            line_h()
        if len(flags) > 8:
            text(lm + 2 * mm, f"+{len(flags) - 8} more on the XLSX", 8, False, RED_RGB)
            line_h()
    else:
        tt = ((page.get("clashes") or {}).get("sources") or {}).get("tark_tee")
        text(lm, "none" + (" · Tark Tee unavailable, restrictions not checked" if tt == "unavailable" else ""), 8.5, False, GREY)
        line_h()
    line_h(0.6)

    # stock
    stock = page.get("stock") or []
    text(lm, "Stock at week end (forecast)", 11)
    line_h()
    if not stock:
        text(lm, "no storage location with a recorded capacity", 8.5, False, GREY)
        line_h()
    for s in stock:
        if s.get("capacity_qty") is None:
            txt = f"{s['name']}  {_n(s['forecast'])} {s['unit']} · no capacity recorded"
            rgb = GREY
        elif s.get("over"):
            txt = f"{s['name']}  {_n(s['forecast'])} / {_n(s['capacity_qty'])} {s['unit']}  OVER {_n(s['over_by'])} {s['unit']}"
            rgb = RED_RGB
        else:
            txt = f"{s['name']}  {_n(s['forecast'])} / {_n(s['capacity_qty'])} {s['unit']} remaining {_n(s['remaining'])}"
            rgb = (0, 0, 0)
        text(lm, txt, 9, False, rgb)
        line_h()
    line_h(0.6)
    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.setLineWidth(0.5)
    c.line(lm, y + 2 * mm, rm, y + 2 * mm)
    line_h(0.4)
    for f in FOOTER:
        text(lm, f, 8, False, GREY)
        line_h()
    stamp = None
    for l in page.get("commit", {}).get("lines", []):
        w = l.get("week") or {}
        if w.get("confirmed_at"):
            stamp = w.get("confirmed_at")
    text(lm, (f"Confirmed {stamp}. " if stamp else "Not yet confirmed. ")
         + "Re-open the week in Look-ahead to change the plan.", 8, False, GREY)
    c.showPage()
    c.save()
    return buf.getvalue()

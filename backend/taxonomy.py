"""
Phase 2 — the discipline taxonomy.

Three layers sit above a movement:

    1. discipline          senior managers, programme reporting   (new, this module)
    2. material category   planning: density, payload, emissions  (factors.json)
    3. material item       the engineer's own words               (forecasts.material_description)

Discipline and material category are MANY-TO-MANY, not a tree. Ballast serves both
substructure (sub-ballast) and superstructure (track ballast); nest it under one and the
copies drift.

A discipline never enters routing. Route validity stays origin.supplies n dest.receives on
material categories — the discipline layer sits above that and only ever derives a
destination's `receives`.
"""
import db

# --------------------------------------------------------------------------- #
#  Disciplines                                                                 #
# --------------------------------------------------------------------------- #
# sort_order is "programme order", which was a stated requirement with no values behind
# it. The order below is a construction-sequence reading -- diversions and construction
# bases before bulk earth moves, track last, stations last of all -- and is MINE, not
# sourced from a project document. Confirm it before anyone reports off it. Gaps of ten
# so a discipline can be slotted between two others without renumbering.
DISCIPLINES = [
    # (id, label, sort_order, in_scope, scope_note)
    ("utilities", "Utilities", 10, True,
     "Third-party diversions and protections - HV, telecom, water, gas. Standalone "
     "because lead times run 2-3 years and it gates everything downstream."),
    ("temporary_works", "Temporary Works", 20, True,
     "Construction bases, hardstanding, access roads, site facilities."),
    ("earthworks", "Earthworks", 30, True,
     "Bulk fill, cut, soil improvement, slope stabilisation. Kept separate from "
     "substructure by user decision."),
    ("substructure", "Substructure", 40, True,
     "Formation beneath the ballast, drainage, culverts, cable channels, ducts and "
     "chambers. Drainage is civils here, not MEP."),
    ("structures", "Structures", 50, True,
     "Bridges, viaducts, retaining walls, noise barriers."),
    ("superstructure", "Superstructure", 60, True,
     "Ballast, sleepers, rails, fastenings, switches and crossings, earthing and "
     "bonding. The project's own defined term for trackwork."),
    ("stations", "Stations", 70, True,
     "Terminals, platforms, depots, IMF buildings, architectural - and station MEP, "
     "until the mep discipline is switched on."),
    # Reserved. Seeded now so bringing one into scope is one UPDATE, not a migration.
    ("mep", "MEP", 80, False,
     "Station buildings are design-yes, construct-no today. May come into scope in a "
     "future segment - switching it on also means migrating the `stations` rows that "
     "were really MEP."),
    ("ene", "Energy / Electrification", 90, False,
     "OCS, substations, SCADA. Procured separately by RB Rail. A1 builds only the "
     "containment, which is substructure."),
    ("ccs", "Control, Command & Signalling", 100, False,
     "ETCS L2, interlocking, FRMCS. Procured separately by RB Rail."),
]

# The authoritative seed for discipline_materials, not an illustration. A destination's
# derived `receives` is exactly this list for the disciplines it serves.
DISCIPLINE_MATERIALS = {
    "earthworks": ["Earthworks / soil", "Small aggregate"],
    "substructure": ["Small aggregate", "Large aggregate / ballast",
                     "Precast / concrete", "General / imported"],
    "utilities": ["General / imported", "Precast / concrete"],
    "structures": ["Precast / concrete", "Steel / rail", "General / imported"],
    "superstructure": ["Large aggregate / ballast", "Steel / rail", "Precast / concrete"],
    "stations": ["Precast / concrete", "Steel / rail", "General / imported"],
    "temporary_works": ["Small aggregate", "Large aggregate / ballast", "General / imported"],
    # mep / ene / ccs carry no material rows until they come into scope.
}

# --------------------------------------------------------------------------- #
#  IPTs                                                                        #
# --------------------------------------------------------------------------- #
# Seeded PRE-MERGE and literally, by decision: store the original IPT, no merge
# modelling. IPT 1 and IPT 2 have merged in reality, so the UI will show work sections
# owned by IPT 2, a team that no longer exists. That is the accepted trade -- adding
# ipts.merged_into later is additive and needs no data migration.
#
# manager stays NULL. The only names anywhere are in claude/ipt-matrix.md, which is
# self-declared unverified AI output. Do not populate this from there.
IPTS = [
    ("IPT1", "IPT 1"),
    ("IPT2", "IPT 2"),
    ("IPT3", "IPT 3"),
    ("IPT4", "IPT 4"),
    ("IPT5", "IPT 5"),
    ("IPT6", "IPT 6"),
]

# --------------------------------------------------------------------------- #
#  Design sections                                                             #
# --------------------------------------------------------------------------- #
# Context only, never a scope gate. km_from / km_to stay NULL: the scope diagram carries
# two chainage notations that do not reconcile (top axis 110.310-141.930 km vs inline
# markers 105+480-137+685, which would put WS 1 about 5 km before DS3 begins), and the
# stated lengths do not match their own spans. Seeding a number here would be inventing
# one.
DESIGN_SECTIONS = [
    ("DS2", "Design Section 2", "Mainline is superstructure-only. Contains WS 8 "
                                "(Ulemiste Terminal), WS 9 (RSMD depot) and WS 10/11 "
                                "(Soodevahe IMF and base), where A1's scope is fuller."),
    ("DS1", "Design Section 1", "Mainline is superstructure-only. A1 depends on other "
                                "contractors for formation handover before track can be laid."),
    ("DS3", "Design Section 3", "Full scope - A1 designs and builds substructure and "
                                "superstructure."),
]


# --------------------------------------------------------------------------- #
#  Work sections                                                               #
# --------------------------------------------------------------------------- #
# ⭐ SEEDED 2026-09-01, from the build list the human sent via Grok. That list is what
# closed open-questions §A3, which had blocked the rest of Phase 2:
#
#   * WS 10 and WS 11 are SEPARATE rows (Soodevahe Construction Base / Soodevahe IMF),
#     and so are WS 14 and WS 15 (Parnu Construction Base / Parnu IMF). The standing
#     "one row or two?" question is answered: two, each time.
#   * WS 14 and WS 15 are owned by IPT 6. That was previously asserted and NOT in
#     evidence -- both carried ipt: null, provisional: true in the map overlay rather
#     than a guess. It is now a human answer, not an inference.
#   * WS 6 is IN SCOPE. Only its construct-in-Phase-1 status is open, and that has no
#     column, so it lives in scope_note.
#
# ⚠️ THE DATUM. km_from / km_to below are LOCAL DS3 chainage, 0+000 = Tootsi. The
#    public map's IPT/WS overlay ticks GLOBAL GIS chainage and the two do not
#    reconcile -- see DESIGN_SECTIONS' note on the scope diagram's two notations. They
#    must never be compared, added, or shown in one unlabelled column, which is why
#    every row carrying a km value repeats the warning in scope_note. WS 8 introduces
#    a THIRD 0+000 (the terminal datum, -2+061 to 0+000) and is deliberately seeded
#    with NULL km rather than a number in some fourth frame.
#
# ⚠️ primary_discipline is left NULL on every row. open-questions §A4 -- "is the
#    section -> discipline mapping right?" -- is still unanswered, and the build list
#    carries no discipline column. WS 7 is named "Superstructure" and it would be easy
#    to write 'superstructure' into it; that is reading a label, not a sourced fact,
#    and this table is what the pickers and any future report read.
#
# ⚠️ Every row is in_scope=True, active=True. The instruction said "except where
#    noted" -- nothing in the fifteen notes actually withdraws a section from scope.
#    WS 6 is full scope; WS 9 and WS 11 are "partial", which is a scope QUANTITY, not
#    in_scope=False. If any of these should be out of scope it is one UPDATE.
#
# ⚠️ WS 15's ownership is SPLIT in reality -- track IPT 6, ditch and gas IPT 3, the
#    rest RBE PTO -- and one ipt_id column cannot hold that. IPT 6 is stored because
#    that is what the build list says; the split is recorded verbatim in scope_note so
#    the loss is visible rather than silent.
_DS3_DATUM = ("km_from/km_to are LOCAL DS3 chainage, 0+000 = Tootsi. The public-map "
              "IPT/WS overlay still ticks GLOBAL GIS chainage; the two datums do not "
              "reconcile and must not be mixed in one column.")

WORK_SECTIONS = [
    # (section_id, name, ipt_id, design_section_id, km_from, km_to,
    #  parent_section_id, note)
    ("WS1", "Tootsi to Timmermanni", "IPT1", "DS3", 0.000, 11.7978, None,
     "Mainline band. Phase 1 construct yes."),
    ("WS2", "Timmermanni to Orasselja Viaduct", "IPT2", "DS3", 11.7978, 24.546, None,
     "Mainline band."),
    ("WS3", "Timmermanni to Parnu Papiniidu Bridge BR2032", "IPT3", "DS3",
     24.546, 29.9094, None,
     "Official name from Scope v1.2, kept verbatim. Note the name begins "
     "'Timmermanni' while the band begins at the Orasselja Viaduct (24.546) where WS2 "
     "ends -- that is the official name, not a transcription error."),
    ("WS4", "Parnu Papiniidu Bridge BR2032", "IPT4", "DS3", 29.9094, 30.2656, None, ""),
    ("WS5", "Parnu Passenger Terminal area", "IPT5", "DS3", 30.2656, 32.350, None,
     "Phase 1 construct yes unless the programme reinstates 31+507. 31+507 is NOT "
     "seeded as a map tick."),
    ("WS6", "Pedestrian underpass BR2236 to A1/A2 boundary", "IPT4", "DS3",
     32.350, 36.663, None,
     "Full scope in Scope v1.2. Construct-in-Phase-1 is OPEN -- in_scope stays true "
     "because scope and phasing are different questions and there is no phasing column."),
    ("WS7", "Superstructure - full route", "IPT6", None, None, None, None,
     "Corridor-wide underlay, one row. Extent is GLOBAL 0+000-142+130 -- a different "
     "datum from the local DS3 chainage on WS1-WS6, which is why km_from/km_to are "
     "NULL here rather than 0 and 142.130. The scope diagram subdivides this into "
     "7.1-7.4; that subdivision is not modelled."),
    ("WS8", "Ulemiste Terminal", "IPT6", "DS2", None, None, None,
     "Site. The terminal has its own datum, -2+061 to 0+000 -- a THIRD 0+000. Do not "
     "add it to DS3 chainage; km left NULL deliberately."),
    ("WS9", "Ulemiste RSMD", "IPT6", "DS2", None, None, None,
     "Site. A1's scope here is partial."),
    ("WS10", "Soodevahe Construction Base", "IPT6", "DS2", None, None, None,
     "Temporary compound. Its own row -- NOT merged with WS11 as 'WS10/11'."),
    ("WS11", "Soodevahe IMF", "IPT6", "DS2", None, None, None,
     "IMF, partial scope. Its own row -- NOT merged with WS10."),
    ("WS12", "Tootsi Local Stop", "IPT1", "DS3", None, None, "WS1",
     "A point inside WS1, not a band of its own."),
    ("WS13", "Urge Halt", "IPT2", "DS3", None, None, "WS2",
     "A point inside WS2. IPT 2, not IPT 1 -- an earlier version of this fact shipped "
     "to the client-visible map as IPT 1 on 2026-08-28 and was corrected in overlay v5."),
    ("WS14", "Parnu Construction Base", "IPT6", "DS3", None, None, None,
     "Temporary compound. Its own row -- NOT merged with WS15."),
    ("WS15", "Parnu IMF", "IPT6", "DS3", None, None, None,
     "Ownership is SPLIT and this column cannot hold it: track = IPT 6 (stored), "
     "ditch and gas = IPT 3, the rest RBE PTO. IPT 6 is what the build list says; the "
     "split is recorded here so it is visible rather than lost."),
]


def _ws_scope_note(note, has_km):
    """The row's own note, with the datum warning appended wherever a km value exists."""
    parts = [p for p in ((note or "").strip(),) if p]
    if has_km:
        parts.append(_DS3_DATUM)
    return " ".join(parts).strip() or None


def seed_taxonomy():
    """
    Insert the taxonomy if it isn't already there. Idempotent and additive: it never
    updates or deletes an existing row, so an edit made in the database survives a
    redeploy.

    work_sections IS now seeded -- fifteen rows, from the 2026-09-01 build list. It was
    held empty until then because its key space was unresolved (whether WS 10/11 and
    WS 14 & 15 were one row or two) and seeding it wrong would have fired false
    out-of-scope warnings on A1's own DS2 facility sections. See WORK_SECTIONS above
    for what the answer was and what is still open.
    """
    counts = {"disciplines": 0, "discipline_materials": 0, "ipts": 0,
              "design_sections": 0, "work_sections": 0}

    # Every read and write below is scoped to the current tenant, so "is it already
    # there" is asked of this tenant's rows only. A second tenant seeds its own copy of
    # the taxonomy rather than inheriting the first one's — and an edit one client makes
    # to a discipline label cannot show up in another's picker.
    tenant = db.current_tenant()

    have = {r["id"] for r in db.query("SELECT id FROM disciplines WHERE tenant_id = ?",
                                      (tenant,))}
    for did, label, order, in_scope, note in DISCIPLINES:
        if did in have:
            continue
        db.execute(
            "INSERT INTO disciplines (tenant_id, id, label, sort_order, in_scope, "
            "scope_note, active) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tenant, did, label, order, bool(in_scope), note, True),
        )
        counts["disciplines"] += 1

    have_dm = {(r["discipline_id"], r["material_category"])
               for r in db.query("SELECT discipline_id, material_category FROM "
                                 "discipline_materials WHERE tenant_id = ?", (tenant,))}
    for did, cats in DISCIPLINE_MATERIALS.items():
        for cat in cats:
            if (did, cat) in have_dm:
                continue
            db.execute(
                "INSERT INTO discipline_materials (tenant_id, discipline_id, "
                "material_category) VALUES (?, ?, ?)",
                (tenant, did, cat),
            )
            counts["discipline_materials"] += 1

    have_i = {r["id"] for r in db.query("SELECT id FROM ipts WHERE tenant_id = ?",
                                        (tenant,))}
    for iid, label in IPTS:
        if iid in have_i:
            continue
        db.execute(
            "INSERT INTO ipts (tenant_id, id, label, manager, active, merged_into) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tenant, iid, label, None, True, None),
        )
        counts["ipts"] += 1

    have_ds = {r["id"] for r in db.query("SELECT id FROM design_sections WHERE tenant_id = ?",
                                         (tenant,))}
    for dsid, label, note in DESIGN_SECTIONS:
        if dsid in have_ds:
            continue
        db.execute(
            "INSERT INTO design_sections (tenant_id, id, label, km_from, km_to, scope_note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tenant, dsid, label, None, None, note),
        )
        counts["design_sections"] += 1

    # Work sections. Additive like everything above: a section already in the table is
    # skipped, never rewritten, so a km value or a scope_note corrected in the database
    # survives the next redeploy. Correcting one of these rows in code therefore does
    # NOT reach a database that already has it -- edit the row, or delete it and let
    # this re-seed it.
    have_ws = {r["section_id"] for r in db.query(
        "SELECT section_id FROM work_sections WHERE tenant_id = ?", (tenant,))}
    for sid, name, ipt_id, ds_id, km_from, km_to, parent, note in WORK_SECTIONS:
        if sid in have_ws:
            continue
        db.execute(
            "INSERT INTO work_sections (tenant_id, section_id, parent_section_id, "
            "design_section_id, ipt_id, name, primary_discipline, in_scope, active, "
            "km_from, km_to, scope_note, receives_override) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tenant, sid, parent, ds_id, ipt_id, name,
             # primary_discipline stays NULL -- open-questions §A4. See WORK_SECTIONS.
             None, True, True, km_from, km_to,
             _ws_scope_note(note, km_from is not None or km_to is not None),
             None),
        )
        counts["work_sections"] += 1

    return counts


# --------------------------------------------------------------------------- #
#  Reads                                                                       #
# --------------------------------------------------------------------------- #
def list_disciplines(include_out_of_scope=False):
    """In programme order. Out-of-scope disciplines are hidden from pickers by default."""
    rows = db.query(
        "SELECT id, label, sort_order, in_scope, scope_note, active FROM disciplines "
        "WHERE tenant_id = ? ORDER BY sort_order, id",
        (db.current_tenant(),)
    )
    by_disc = {}
    for m in db.query("SELECT discipline_id, material_category FROM discipline_materials "
                      "WHERE tenant_id = ? ORDER BY material_category",
                      (db.current_tenant(),)):
        by_disc.setdefault(m["discipline_id"], []).append(m["material_category"])

    out = []
    for r in rows:
        r["in_scope"] = bool(r["in_scope"])
        r["active"] = bool(r["active"])
        # the categories this discipline moves, so the submission UI can advise without
        # a second request. Advisory only — the join is deliberately permissive.
        r["materials"] = by_disc.get(r["id"], [])
        if not r["active"]:
            continue
        if not r["in_scope"] and not include_out_of_scope:
            continue
        out.append(r)
    return out


def materials_for(discipline_id):
    """The material categories one discipline moves."""
    return [r["material_category"] for r in db.query(
        "SELECT material_category FROM discipline_materials "
        "WHERE tenant_id = ? AND discipline_id = ? "
        "ORDER BY material_category", (db.current_tenant(), discipline_id))]


def derive_receives(discipline_ids):
    """
    A destination's `receives` = the union of the material categories of every discipline
    delivered to it. This replaces hand-authoring a material list per work section -- 90
    checkboxes that silently drift.

    It replaces only the `receives` half. network.backfill_supplies_receives() also fills
    `supplies`, and route validity is origin.supplies n dest.receives, so removing that
    function outright breaks route authoring for origins. Keep its supplies half.
    """
    seen, out = set(), []
    for did in discipline_ids or []:
        for cat in materials_for(did):
            if cat not in seen:
                seen.add(cat)
                out.append(cat)
    return out


def list_ipts(active_only=True):
    rows = db.query("SELECT id, label, manager, active, merged_into FROM ipts "
                    "WHERE tenant_id = ? ORDER BY id", (db.current_tenant(),))
    for r in rows:
        r["active"] = bool(r["active"])
    return [r for r in rows if r["active"]] if active_only else rows


def list_work_sections(in_scope_only=False):
    rows = db.query(
        # km_from / km_to joined the SELECT when the fifteen rows were seeded. They are
        # LOCAL DS3 chainage and every row carrying one repeats that in scope_note —
        # anything that displays them must display the datum with them.
        "SELECT section_id, parent_section_id, design_section_id, ipt_id, name, "
        "primary_discipline, in_scope, active, km_from, km_to, scope_note "
        "FROM work_sections WHERE tenant_id = ? ORDER BY section_id",
        (db.current_tenant(),)
    )
    for r in rows:
        r["in_scope"] = bool(r["in_scope"])
        r["active"] = bool(r["active"])
    # ⚠️ Ordered in Python, not by the SQL. `ORDER BY section_id` is lexicographic on
    # both backends, and with fifteen rows that puts WS1, WS10, WS11 … WS15, WS2, WS3
    # in the Submit Forecast picker — which reads as a bug and hides WS2 below WS15.
    # Sorting on the numeric tail fixes it without assuming every id is WSn: an id
    # with no digits keeps its place alphabetically after the numbered ones.
    rows.sort(key=_ws_sort_key)
    if in_scope_only:
        rows = [r for r in rows if r["in_scope"] and r["active"]]
    return rows


def _ws_sort_key(row):
    sid = row.get("section_id") or ""
    head = sid.rstrip("0123456789")
    tail = sid[len(head):]
    return (head, 0, int(tail)) if tail else (head, 1, 0)

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


def seed_taxonomy():
    """
    Insert the taxonomy if it isn't already there. Idempotent and additive: it never
    updates or deletes an existing row, so an edit made in the database survives a
    redeploy.

    work_sections is deliberately NOT seeded. Its key space is unresolved -- whether
    WS 10/11 and WS 14 & 15 are one row or two, and whether WS 7 needs a parent row --
    and that has to be settled against Appendix E, not guessed. The table exists and is
    empty; seeding it wrong would fire false out-of-scope warnings on A1's own DS2
    facility sections on day one.
    """
    counts = {"disciplines": 0, "discipline_materials": 0, "ipts": 0, "design_sections": 0}

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
        "SELECT section_id, parent_section_id, design_section_id, ipt_id, name, "
        "primary_discipline, in_scope, active, scope_note FROM work_sections "
        "WHERE tenant_id = ? ORDER BY section_id",
        (db.current_tenant(),)
    )
    for r in rows:
        r["in_scope"] = bool(r["in_scope"])
        r["active"] = bool(r["active"])
    if in_scope_only:
        rows = [r for r in rows if r["in_scope"] and r["active"]]
    return rows

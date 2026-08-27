"""
Tiny database layer.

- On Render (or anywhere DATABASE_URL is set) it uses PostgreSQL, so forecasts
  persist across restarts.
- With no DATABASE_URL it falls back to a local SQLite file, which is handy for
  quick local testing.

The rest of the app calls query()/execute() and never worries which backend is
in use. SQL is written with '?' placeholders and translated to '%s' for Postgres.
"""
import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = bool(DATABASE_URL)

if IS_PG:
    import psycopg2
    import psycopg2.extras
    # psycopg2 wants the postgresql:// scheme
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    _SQLITE_PATH = os.path.join(os.path.dirname(__file__), "forecasts_local.db")


def get_conn():
    if IS_PG:
        return psycopg2.connect(DATABASE_URL)
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _adapt(sql):
    """SQLite uses ?, Postgres uses %s."""
    return sql.replace("?", "%s") if IS_PG else sql


def query(sql, params=()):
    """Run a SELECT and return a list of plain dicts."""
    conn = get_conn()
    try:
        if IS_PG:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cur = conn.cursor()
        cur.execute(_adapt(sql), params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def execute(sql, params=()):
    """Run an INSERT/UPDATE/DELETE and commit."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_adapt(sql), params)
        conn.commit()
    finally:
        conn.close()


FORECASTS_DDL = """
    CREATE TABLE IF NOT EXISTS forecasts (
        id                   TEXT PRIMARY KEY,
        route_id             TEXT NOT NULL,
        month_index          INTEGER NOT NULL,
        discipline           TEXT NOT NULL DEFAULT '',
        section_id           TEXT NOT NULL DEFAULT '',
        quantity             REAL NOT NULL,
        unit                 TEXT NOT NULL,
        material_type        TEXT,
        material_description TEXT,
        vehicle_type         TEXT,
        submitted_by         TEXT,
        status               TEXT NOT NULL DEFAULT 'Pending',
        reject_reason        TEXT,
        UNIQUE (route_id, month_index, discipline, section_id)
    )
"""


def init_db():
    """
    Create the forecasts table, or replace a pre-Phase-2 one.

    The Phase 2 key is (route_id, month_index, discipline, section_id), so one route
    can carry ballast for superstructure and fill for substructure in the same month.

    discipline and section_id are NOT NULL DEFAULT '' rather than nullable on purpose:
    NULLs compare distinct inside a UNIQUE constraint in both Postgres and SQLite, so a
    nullable column would leave the row unconstrained, ON CONFLICT would never fire, and
    re-saving a matrix row would insert a duplicate instead of updating it.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(FORECASTS_DDL)
        conn.commit()
        _migrate_forecasts_to_phase2(conn, cur)
    finally:
        conn.close()


def _migrate_forecasts_to_phase2(conn, cur):
    """
    Replace a pre-Phase-2 forecasts table.

    Phase 2 is a clean rebuild: the 69 legacy forecasts are keyed on legacy route ids
    that have no geometry in the routing network, and are being re-authored rather than
    migrated. So this does NOT translate the old rows.

    It does not destroy them either. The old table is renamed to forecasts_legacy and
    left on disk — invisible to every endpoint, which is what was asked for, but
    recoverable if something turns out to have been needed. Drop it yourself once you
    are happy:  DROP TABLE forecasts_legacy;

    Idempotent: the presence of the 'discipline' column is the flag for 'already done'.
    """
    if _has_column(cur, "forecasts", "discipline"):
        return

    n = 0
    try:
        cur.execute("SELECT COUNT(*) FROM forecasts")
        n = (cur.fetchone() or [0])[0]
    except Exception:
        conn.rollback()

    try:
        cur.execute("DROP TABLE IF EXISTS forecasts_legacy")
        cur.execute("ALTER TABLE forecasts RENAME TO forecasts_legacy")
        cur.execute(FORECASTS_DDL)
        conn.commit()
        print(f"Phase 2: forecasts rebuilt on the widened key. "
              f"{n} legacy row(s) moved to forecasts_legacy — drop that table when ready.")
    except Exception as e:
        conn.rollback()
        print("Phase 2 forecasts migration skipped:", e)


def count_forecasts():
    return query("SELECT COUNT(*) AS n FROM forecasts")[0]["n"]


def init_network_db():
    """Tables for the dynamic routing platform: locations, routes, cached geometry."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS locations (
                id        TEXT PRIMARY KEY,
                name      TEXT NOT NULL,
                loc_type  TEXT,
                role      TEXT,
                materials TEXT,
                supplies  TEXT,
                receives  TEXT,
                lat       REAL NOT NULL,
                lon       REAL NOT NULL,
                material  TEXT
            )
            """
        )
        conn.commit()
        # migrate pre-existing tables: add the newer columns if they're missing.
        # network.seed_network() writes role/materials on the very first boot, so
        # these have to exist before the seed runs, not lazily on first edit.
        for col, typ in (
            ("role", "TEXT"),
            ("materials", "TEXT"),
            ("supplies", "TEXT"),
            ("receives", "TEXT"),
        ):
            try:
                if IS_PG:
                    cur.execute(f"ALTER TABLE locations ADD COLUMN IF NOT EXISTS {col} {typ}")
                else:
                    cur.execute(f"ALTER TABLE locations ADD COLUMN {col} {typ}")
                conn.commit()
            except Exception:
                conn.rollback()  # column already present — fine
        # gate points: the actual access/egress coordinate HERE should route to, when it
        # differs from the node centre. Nullable — most sites don't have one recorded yet,
        # and routing falls back to lat/lon until they do. Filling one in later needs no
        # migration, just an UPDATE.
        # Phase 2 additions.
        #   default_section_id — a convenience pre-fill for the submission matrix only.
        #     Work sections OVERLAP (Tootsi Station is in WS 12, WS 1 and WS 7.3 at once),
        #     so this can never be the authoritative section — that lives on the forecast
        #     line. Left NULL until someone sets it deliberately; see the README note on
        #     why the a1_data.js work-section tags were NOT used to seed it.
        #   vendor / detail — salvaged from a1_data.js before that file was retired.
        #     Commercial data recorded nowhere else.
        for col, typ in (("default_section_id", "TEXT"), ("vendor", "TEXT"), ("detail", "TEXT")):
            try:
                if IS_PG:
                    cur.execute(f"ALTER TABLE locations ADD COLUMN IF NOT EXISTS {col} {typ}")
                else:
                    cur.execute(f"ALTER TABLE locations ADD COLUMN {col} {typ}")
                conn.commit()
            except Exception:
                conn.rollback()  # column already present — fine
        for col, typ in (("gate_lat", "REAL"), ("gate_lon", "REAL")):
            try:
                if IS_PG:
                    cur.execute(f"ALTER TABLE locations ADD COLUMN IF NOT EXISTS {col} {typ}")
                else:
                    cur.execute(f"ALTER TABLE locations ADD COLUMN {col} {typ}")
                conn.commit()
            except Exception:
                conn.rollback()  # column already present — fine
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS routes (
                id                TEXT PRIMARY KEY,
                origin_id         TEXT,
                dest_id           TEXT,
                long_route_id     TEXT,
                material_category TEXT,
                ipt               TEXT,
                origin_temp_km    REAL DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS route_geometry (
                route_id        TEXT NOT NULL,
                vehicle_profile TEXT NOT NULL,
                leg             TEXT NOT NULL DEFAULT 'loaded',
                alt_index       INTEGER NOT NULL DEFAULT 0,
                geometry        TEXT,
                distance_km     REAL,
                duration_hr     REAL,
                computed_at     TEXT,
                error           TEXT,
                PRIMARY KEY (route_id, vehicle_profile, leg, alt_index)
            )
            """
        )
        conn.commit()
        _migrate_route_geometry_key(conn, cur)
        # Phase 3: which zones were avoided when this leg was baked, as a comma-separated
        # list of zone ids ('' = baked with no zones in force). Nullable, and NULL means
        # "baked before zones existed, provenance unknown" — zones.invalidate() treats
        # those rows differently and less precisely, so the distinction has to survive.
        # Plain ADD COLUMN on both backends: no key change, so SQLite needs no rebuild.
        # Phase 4: what a haul road did to this leg. Recorded rather than recomputed,
        # because once the assigned haul speed has been substituted into duration_hr it
        # cannot be backed out of that number alone.
        #   haul_zones       comma-separated haul-road zone ids threaded into this leg.
        #                    '' = baked with none, NULL = baked before Phase 4. Same
        #                    three-state convention as zones_applied, same reason: the
        #                    difference between "none applied" and "we don't know".
        #   haul_km          how much of this leg runs on drawn haul road
        #   duration_hr_here HERE's own duration for the leg BEFORE substitution.
        #                    duration_hr holds the ADJUSTED figure, because that is what
        #                    route_analysis() reads and the point of assigning a speed is
        #                    that cycle time moves. Keeping the raw figure is what makes
        #                    the adjustment auditable rather than a number that appeared.
        for col, typ in (("zones_applied", "TEXT"), ("haul_zones", "TEXT"),
                         ("haul_km", "REAL"), ("duration_hr_here", "REAL")):
            try:
                if IS_PG:
                    cur.execute(f"ALTER TABLE route_geometry ADD COLUMN IF NOT EXISTS {col} {typ}")
                else:
                    cur.execute(f"ALTER TABLE route_geometry ADD COLUMN {col} {typ}")
                conn.commit()
            except Exception:
                conn.rollback()  # column already present — fine
    finally:
        conn.close()


def _migrate_route_geometry_key(conn, cur):
    """
    Widen route_geometry's key from (route_id, vehicle_profile) to
    (route_id, vehicle_profile, leg, alt_index).

    Step B routes each pair twice — 'loaded' out and 'return' back, the return leg at
    tare weight so different weight limits apply — and keeps up to three HERE
    alternatives per leg. Pre-Step-B rows were all laden first-choice routes, so they
    migrate to leg='loaded', alt_index=0 and stay valid.

    Postgres can redefine the constraint in place. SQLite cannot drop a primary key at
    all, so there the table is rebuilt and copied. Both paths are idempotent: the
    presence of the 'leg' column is the flag for 'already migrated'.
    """
    if _has_column(cur, "route_geometry", "leg"):
        return

    if IS_PG:
        for stmt in (
            "ALTER TABLE route_geometry ADD COLUMN IF NOT EXISTS leg TEXT NOT NULL DEFAULT 'loaded'",
            "ALTER TABLE route_geometry ADD COLUMN IF NOT EXISTS alt_index INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE route_geometry DROP CONSTRAINT IF EXISTS route_geometry_pkey",
            "ALTER TABLE route_geometry ADD PRIMARY KEY (route_id, vehicle_profile, leg, alt_index)",
        ):
            try:
                cur.execute(stmt)
                conn.commit()
            except Exception:
                conn.rollback()
        return

    # SQLite: rebuild, copy, swap. Old rows become laden first-choice routes.
    try:
        cur.execute("ALTER TABLE route_geometry RENAME TO route_geometry_old")
        cur.execute(
            """
            CREATE TABLE route_geometry (
                route_id        TEXT NOT NULL,
                vehicle_profile TEXT NOT NULL,
                leg             TEXT NOT NULL DEFAULT 'loaded',
                alt_index       INTEGER NOT NULL DEFAULT 0,
                geometry        TEXT,
                distance_km     REAL,
                duration_hr     REAL,
                computed_at     TEXT,
                error           TEXT,
                PRIMARY KEY (route_id, vehicle_profile, leg, alt_index)
            )
            """
        )
        cur.execute(
            "INSERT INTO route_geometry "
            "(route_id, vehicle_profile, leg, alt_index, geometry, distance_km, duration_hr, computed_at, error) "
            "SELECT route_id, vehicle_profile, 'loaded', 0, geometry, distance_km, duration_hr, computed_at, error "
            "FROM route_geometry_old"
        )
        cur.execute("DROP TABLE route_geometry_old")
        conn.commit()
    except Exception:
        conn.rollback()


def _has_column(cur, table, column):
    """True if `table` already has `column`. Used to make migrations idempotent."""
    try:
        if IS_PG:
            cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, column),
            )
            return cur.fetchone() is not None
        cur.execute(f"PRAGMA table_info({table})")
        return any(r[1] == column for r in cur.fetchall())
    except Exception:
        return False


def count_locations():
    return query("SELECT COUNT(*) AS n FROM locations")[0]["n"]


# --------------------------------------------------------------------------- #
#  Phase 2 — discipline taxonomy                                               #
# --------------------------------------------------------------------------- #
def init_taxonomy_db():
    """
    Discipline / IPT / section tables.

    Two flags on both disciplines and work_sections, and they are NOT the same question:
      active   — does this exist in the current org configuration? (IPT 2 after a merge: no)
      in_scope — does A1 haul to it? (mep, ene, ccs today: no; WS 6 reportedly design-only)
    Conflating them means you cannot express "we track this, but someone else hauls it".

    Nothing is ever deleted — scope arrives in segments and IPTs merge and may un-merge,
    so a historical forecast referencing a since-merged IPT must still resolve.

    There is deliberately no design_section_disciplines table. "Superstructure only"
    is a MAINLINE rule, not a design-section rule — DS2 also contains the Ulemiste
    terminal, the RSMD depot and the Soodevahe IMF, where A1's scope is fuller. Scope
    varies by work section, which is why in_scope sits on work_sections.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS disciplines (
                id         TEXT PRIMARY KEY,
                label      TEXT NOT NULL,
                sort_order INTEGER,
                in_scope   BOOLEAN DEFAULT TRUE,
                scope_note TEXT,
                active     BOOLEAN DEFAULT TRUE
            )
            """
        )
        # many-to-many on purpose: 'Large aggregate / ballast' belongs to BOTH
        # substructure (sub-ballast) and superstructure (track ballast). Nest it under
        # one discipline and the copies drift.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS discipline_materials (
                discipline_id     TEXT NOT NULL,
                material_category TEXT NOT NULL,
                PRIMARY KEY (discipline_id, material_category)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ipts (
                id          TEXT PRIMARY KEY,
                label       TEXT,
                manager     TEXT,
                active      BOOLEAN DEFAULT TRUE,
                merged_into TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS design_sections (
                id         TEXT PRIMARY KEY,
                label      TEXT,
                km_from    REAL,
                km_to      REAL,
                scope_note TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS work_sections (
                section_id         TEXT PRIMARY KEY,
                parent_section_id  TEXT,
                design_section_id  TEXT,
                ipt_id             TEXT,
                name               TEXT,
                primary_discipline TEXT,
                in_scope           BOOLEAN DEFAULT TRUE,
                active             BOOLEAN DEFAULT TRUE,
                km_from            REAL,
                km_to              REAL,
                scope_note         TEXT,
                receives_override  TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  Phase 3 — zones (geofencing + disruptions, one table)                       #
# --------------------------------------------------------------------------- #
def init_zones_db():
    """
    One table for both halves of Phase 3.

    Phases 3 and 3.5 were planned separately — geofencing that steers HERE away from an
    area, and curated disruptions drawn on the map with a date range. Built separately
    that is two polygon editors, two tables, and no answer to the only question anyone
    actually asks: "this closure shuts a road — does it change my routing?". One table
    with an `affects_routing` flag answers it.

      affects_routing = TRUE   the bbox goes to HERE as avoid[areas]; baked geometry
                               that crosses it is invalidated and re-routed
      affects_routing = FALSE  drawn on the map, ignored by the router — a works area
                               planners should see but that does not close a road

    `geometry` is a GeoJSON *geometry* object as text (Polygon or LineString), not a
    Feature. The full shape is stored even though HERE only accepts bounding boxes,
    because the map draws the real polygon and only the router reduces it — see
    zones.bbox_of() for why that reduction over-blocks.

    Dates are TEXT 'YYYY-MM-DD', matching the rest of the codebase's ISO-string habit
    rather than introducing a DATE type that SQLite would not enforce anyway. NULL at
    either end means open-ended: a zone with no starts_on has always applied.

    `active` is not in the original Phase 3 sketch. It is here for the same reason the
    taxonomy tables have it: a zone that stops applying is history someone may need to
    explain a past bake, and deleting it destroys that. Deactivating leaves the record
    and drops it out of routing.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS zones (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                kind            TEXT,
                geometry        TEXT,
                affects_routing BOOLEAN DEFAULT TRUE,
                starts_on       TEXT,
                ends_on         TEXT,
                note            TEXT,
                active          BOOLEAN DEFAULT TRUE,
                created_at      TEXT,
                updated_at      TEXT
            )
            """
        )
        conn.commit()
        # Phase 4: two columns that only mean anything for kind = 'haul_road'.
        #   speed_kph   the assigned running speed on this road. HERE will not accept a
        #               custom speed, so it is applied afterwards by substitution — see
        #               haul.py. NULL means "no assigned speed", and a haul road with
        #               NULL is routed through but its duration is left as HERE gave it.
        #   haul_mode   'splice' or 'via'. Which of the two ways of making a route use
        #               this road applies. See haul.MODES — the choice depends on
        #               whether HERE's map knows the road exists, which is a fact about
        #               the world, not a preference.
        for col, typ in (("speed_kph", "REAL"), ("haul_mode", "TEXT")):
            try:
                if IS_PG:
                    cur.execute(f"ALTER TABLE zones ADD COLUMN IF NOT EXISTS {col} {typ}")
                else:
                    cur.execute(f"ALTER TABLE zones ADD COLUMN {col} {typ}")
                conn.commit()
            except Exception:
                conn.rollback()  # column already present — fine
        # Phase 4: which routes use which haul road.
        #
        # Deliberately NOT inferred from proximity. A haul road near a route is not the
        # same as a haul road that route uses, and getting that wrong is expensive in
        # exactly the way this codebase is trying to avoid: every wrong guess re-bakes a
        # leg and spends a HERE call. It is also what the user asked for — a haul road is
        # attached ON the route, not drawn and left to find its own traffic.
        #
        # `seq` orders multiple haul roads along one route, because a route that uses two
        # of them must enter them in the right order or the splice produces a line that
        # doubles back. The return leg walks the same list in reverse.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS route_haul_roads (
                route_id   TEXT NOT NULL,
                zone_id    TEXT NOT NULL,
                seq        INTEGER NOT NULL DEFAULT 0,
                created_at TEXT,
                PRIMARY KEY (route_id, zone_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def count_zones():
    return query("SELECT COUNT(*) AS n FROM zones")[0]["n"]


def count_haul_links():
    return query("SELECT COUNT(*) AS n FROM route_haul_roads")[0]["n"]

"""
Tiny database layer.

- On Render (or anywhere DATABASE_URL is set) it uses PostgreSQL, so forecasts
  persist across restarts.
- With no DATABASE_URL it falls back to a local SQLite file, which is handy for
  quick local testing.

The rest of the app calls query()/execute() and never worries which backend is
in use. SQL is written with '?' placeholders and translated to '%s' for Postgres.
"""
import contextvars
import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = bool(DATABASE_URL)

# --------------------------------------------------------------------------- #
#  Phase 4.5 — the tenant key                                                  #
# --------------------------------------------------------------------------- #
# One tenant, no auth. What this phase buys is the DATA MODEL: every table
# carries tenant_id, it is part of every primary key, and every query filters on
# it. Behaviour is unchanged, because with one tenant the filter is a no-op —
# which is exactly why the pre-existing assertions are the proof it is safe.
#
# ⚠️ A tenant column with no filter is worse than no column: it looks like
#    isolation and is not. backend/tests/test_tenant_audit.py parses every SQL
#    literal in backend/*.py and fails if a read or write of a tenanted table has
#    no tenant_id predicate. That file, not this comment, is what keeps the
#    promise true after the next edit.
#
# ⚠️ tenant_id is in the PRIMARY KEY, not merely a column beside it. Ids here are
#    human-meaningful and client-supplied — locations are 'C01', routes are
#    'R001'. Two clients will both want 'C01'. With a global primary key the
#    second tenant's seed fails on a key violation and the app looks broken;
#    the column would be present and the isolation absent. That is why eleven
#    tables get a key rebuild rather than a plain ADD COLUMN.

TENANT_DEFAULT = os.environ.get("TENANT_ID", "default").strip() or "default"

# The twelve tenanted tables. test_tenant_audit.py cross-checks this set against
# every CREATE TABLE in this file, so it cannot drift from the schema.
TENANTED_TABLES = {
    "forecasts", "locations", "routes", "route_geometry",
    "disciplines", "discipline_materials", "ipts",
    "design_sections", "work_sections", "zones", "route_haul_roads",
    # Phase 5a. Registered in the same edit that wrote the table, not after:
    # test_tenant_audit.py fails on a CREATE TABLE this set does not name, which is
    # what stops a table shipping with the column present and the isolation absent.
    "location_gates",
    # Week 1, 2026-09-01. Same rule, same edit. forecast_weeks hangs off a forecast
    # LINE and stockpile_weeks off a location; both are per-client data and both are
    # written by endpoints a second tenant would reach.
    "forecast_weeks", "stockpile_weeks",
}

# A contextvar rather than a module global, so Phase 6 can make the tenant
# request-scoped by setting it in one middleware WITHOUT touching any of the 139
# call sites this phase threads. Every one of them asks current_tenant(); none of
# them takes a tenant argument. That is the whole point of resolving it here.
_TENANT = contextvars.ContextVar("tenant_id", default=None)


def current_tenant():
    """The tenant every query filters on. One value today; per-request in Phase 6."""
    return _TENANT.get() or TENANT_DEFAULT


def set_current_tenant(tenant_id):
    """Set the active tenant for this context. Returns the token to reset with."""
    return _TENANT.set((tenant_id or "").strip() or TENANT_DEFAULT)


def reset_current_tenant(token):
    _TENANT.reset(token)

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


# FORECASTS_DDL was here. Phase 4.5 made _TENANT_DDL["forecasts"] the single source
# for this table, so a second copy of the DDL could only ever drift out of it — and a
# drifted copy without tenant_id is precisely the "column present, isolation absent"
# failure this phase exists to prevent. Use _create_tenanted(cur, "forecasts").


def _create_tenanted(cur, table):
    """
    Create one tenanted table from the single-source DDL in _TENANT_DDL.

    A FRESH database is born with tenant_id already in the key, so it never needs
    the Phase 4.5 migration at all. init_tenant() exists for databases that
    predate 4.5 — including the live Postgres — where IF NOT EXISTS makes this a
    no-op and the migration does the work instead.
    """
    cur.execute(_TENANT_DDL[table].replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))


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
        _create_tenanted(cur, "forecasts")
        conn.commit()
        _migrate_forecasts_to_phase2(conn, cur)
        # Task F: the line's IPT. Plain ADD COLUMN on both backends; in _TENANT_DDL too.
        try:
            if IS_PG:
                cur.execute("ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS ipt TEXT")
            else:
                cur.execute("ALTER TABLE forecasts ADD COLUMN ipt TEXT")
            conn.commit()
        except Exception:
            conn.rollback()  # column already present - fine
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
        _create_tenanted(cur, "forecasts")
        conn.commit()
        print(f"Phase 2: forecasts rebuilt on the widened key. "
              f"{n} legacy row(s) moved to forecasts_legacy — drop that table when ready.")
    except Exception as e:
        conn.rollback()
        print("Phase 2 forecasts migration skipped:", e)


def count_forecasts():
    return query("SELECT COUNT(*) AS n FROM forecasts WHERE tenant_id = ?",
                 (current_tenant(),))[0]["n"]


def init_network_db():
    """Tables for the dynamic routing platform: locations, routes, cached geometry."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        _create_tenanted(cur, "locations")
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
        _create_tenanted(cur, "routes")
        _create_tenanted(cur, "route_geometry")
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
    return query("SELECT COUNT(*) AS n FROM locations WHERE tenant_id = ?",
                 (current_tenant(),))[0]["n"]


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
        _create_tenanted(cur, "disciplines")
        # many-to-many on purpose: 'Large aggregate / ballast' belongs to BOTH
        # substructure (sub-ballast) and superstructure (track ballast). Nest it under
        # one discipline and the copies drift.
        _create_tenanted(cur, "discipline_materials")
        _create_tenanted(cur, "ipts")
        _create_tenanted(cur, "design_sections")
        _create_tenanted(cur, "work_sections")
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
        _create_tenanted(cur, "zones")
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
        _create_tenanted(cur, "route_haul_roads")
        conn.commit()
    finally:
        conn.close()


def count_zones():
    return query("SELECT COUNT(*) AS n FROM zones WHERE tenant_id = ?",
                 (current_tenant(),))[0]["n"]


def count_haul_links():
    return query("SELECT COUNT(*) AS n FROM route_haul_roads WHERE tenant_id = ?",
                 (current_tenant(),))[0]["n"]


def init_gates_db():
    """
    Phase 5a. The gate table, and the two columns on `routes` that point into it.

    Runs after init_network_db() (routes must exist to be ALTERed) and BEFORE
    init_tenant(), for the reason spelled out on _TENANT_DDL: the SQLite rebuild
    copies the intersection of the live table's columns with the DDL, so a column
    added by ALTER after that rebuild would survive, but one added before it and
    missing from the DDL would be silently dropped. Both new route columns are in
    _TENANT_DDL["routes"] as well as here.

    The migration of the old single (gate_lat, gate_lon) pair into a real gate row
    is NOT here — it needs id allocation and lives in gates.migrate_legacy_gates(),
    called from the lifespan after the tenant migration has run.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        _create_tenanted(cur, "location_gates")
        conn.commit()
        for col, typ in (("origin_gate_id", "TEXT"), ("dest_gate_id", "TEXT")):
            try:
                if IS_PG:
                    cur.execute(f"ALTER TABLE routes ADD COLUMN IF NOT EXISTS {col} {typ}")
                else:
                    cur.execute(f"ALTER TABLE routes ADD COLUMN {col} {typ}")
                conn.commit()
            except Exception:
                conn.rollback()  # column already present — fine
    finally:
        conn.close()


def count_gates():
    return query("SELECT COUNT(*) AS n FROM location_gates WHERE tenant_id = ?",
                 (current_tenant(),))[0]["n"]


def init_weeks_db():
    """
    Week 1, 2026-09-01. The four-week look-ahead, typed actuals, and stockpile storage.

    Two new tables plus three columns on `locations`. Runs after init_network_db()
    (locations must exist to be ALTERed) and BEFORE init_tenant(), for the reason
    spelled out on _TENANT_DDL and learned in Phase 5a: the SQLite tenant rebuild
    copies the INTERSECTION of the live table's columns with the DDL, so a column
    added by ALTER after that rebuild survives, but one added before it and missing
    from the DDL is silently dropped. All three are in _TENANT_DDL["locations"] as
    well as here.

    ⚠️ capacity_unit gets no DEFAULT in the DDL. The build list says the default is
    't', but a column default would make an unset capacity_unit indistinguishable from
    one somebody chose — and 'no capacity recorded' has to stay distinguishable,
    because that is the case where `remaining` reports null rather than a number. The
    't' default is applied when a capacity is WRITTEN, in stockpiles.set_capacity().
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        for col, typ in (("capacity_qty", "REAL"), ("capacity_unit", "TEXT"),
                         ("opening_qty", "REAL")):
            try:
                if IS_PG:
                    cur.execute(f"ALTER TABLE locations ADD COLUMN IF NOT EXISTS {col} {typ}")
                else:
                    cur.execute(f"ALTER TABLE locations ADD COLUMN {col} {typ}")
                conn.commit()
            except Exception:
                conn.rollback()  # column already present — fine
        _create_tenanted(cur, "forecast_weeks")
        _create_tenanted(cur, "stockpile_weeks")
        conn.commit()
        # 2026-09-02: 'Rail head' -> 'Railhead', one word, everywhere. The build list's
        # one-line migration, verbatim. Idempotent — a second boot matches nothing.
        # Deliberately unscoped by tenant: this is a spelling fix to a type value, not a
        # row operation on one client's data, and every tenant gets the same rename.
        try:
            cur.execute("UPDATE locations SET loc_type = 'Railhead' "
                        "WHERE loc_type = 'Rail head'")
            conn.commit()
        except Exception:
            conn.rollback()
    finally:
        conn.close()


def count_forecast_weeks():
    return query("SELECT COUNT(*) AS n FROM forecast_weeks WHERE tenant_id = ?",
                 (current_tenant(),))[0]["n"]


# --------------------------------------------------------------------------- #
#  Phase 4.5 — tenant migration                                                #
# --------------------------------------------------------------------------- #
# Each entry is the table AS IT SHOULD END UP: every column it has after all the
# earlier ALTER-added ones, plus tenant_id, with tenant_id first in the primary
# key. The SQLite rebuild creates the target from this DDL and copies the
# INTERSECTION of the old table's real columns with it, so a database that never
# received one of the later ADD COLUMNs still migrates rather than erroring.
_TENANT_DDL = {
    "forecasts": """
        CREATE TABLE forecasts (
            tenant_id            TEXT NOT NULL DEFAULT 'default',
            id                   TEXT NOT NULL,
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
            -- 2026-09-02 (Task F): which IPT this LINE belongs to. 'IPT1'..'IPT6'.
            -- The source of truth for who can see the line - not routes.ipt, which
            -- reads "IPT 3 / IPT 6" on a shared route, and not the section. NULL on
            -- every line written before this shipped; those are visible to planners
            -- and admins only until somebody sets it. Listed here AND ALTERed in
            -- init_db(), or the SQLite tenant rebuild drops it.
            ipt                  TEXT,
            PRIMARY KEY (tenant_id, id),
            UNIQUE (tenant_id, route_id, month_index, discipline, section_id)
        )
    """,
    "locations": """
        CREATE TABLE locations (
            tenant_id          TEXT NOT NULL DEFAULT 'default',
            id                 TEXT NOT NULL,
            name               TEXT NOT NULL,
            loc_type           TEXT,
            role               TEXT,
            materials          TEXT,
            supplies           TEXT,
            receives           TEXT,
            lat                REAL NOT NULL,
            lon                REAL NOT NULL,
            material           TEXT,
            default_section_id TEXT,
            vendor             TEXT,
            detail             TEXT,
            gate_lat           REAL,
            gate_lon           REAL,
            -- Week 1 (Task D2): storage ON a location. NOT the haul/gate capacity
            -- model, which is Phase 5a/5b and lives on routes and gates.
            --   capacity_qty  max live stock. NULL = not recorded, and the balance
            --                 still computes — `remaining` reports null, not 0.
            --   capacity_unit 'm3' | 't', default 't'.
            --   opening_qty   stock at the start of month_index 1.
            -- Listed here as well as ALTERed in init_weeks_db(), or the SQLite tenant
            -- rebuild copies only the intersection and drops them — the same trap
            -- Phase 5a's two route gate columns had to avoid.
            capacity_qty       REAL,
            capacity_unit      TEXT,
            opening_qty        REAL,
            PRIMARY KEY (tenant_id, id)
        )
    """,
    "routes": """
        CREATE TABLE routes (
            tenant_id         TEXT NOT NULL DEFAULT 'default',
            id                TEXT NOT NULL,
            origin_id         TEXT,
            dest_id           TEXT,
            long_route_id     TEXT,
            material_category TEXT,
            ipt               TEXT,
            origin_temp_km    REAL DEFAULT 0,
            -- Phase 5a: which gate this route uses at each end. NULL = "no explicit
            -- choice", which resolves to the location's default gate for the
            -- direction being travelled, and to lat/lon when it has no gates at all.
            -- Must be listed here as well as ALTERed in init_gates_db(), or the
            -- SQLite tenant rebuild copies only the intersection and drops them.
            origin_gate_id    TEXT,
            dest_gate_id      TEXT,
            PRIMARY KEY (tenant_id, id)
        )
    """,
    "route_geometry": """
        CREATE TABLE route_geometry (
            tenant_id        TEXT NOT NULL DEFAULT 'default',
            route_id         TEXT NOT NULL,
            vehicle_profile  TEXT NOT NULL,
            leg              TEXT NOT NULL DEFAULT 'loaded',
            alt_index        INTEGER NOT NULL DEFAULT 0,
            geometry         TEXT,
            distance_km      REAL,
            duration_hr      REAL,
            computed_at      TEXT,
            error            TEXT,
            zones_applied    TEXT,
            haul_zones       TEXT,
            haul_km          REAL,
            duration_hr_here REAL,
            PRIMARY KEY (tenant_id, route_id, vehicle_profile, leg, alt_index)
        )
    """,
    "disciplines": """
        CREATE TABLE disciplines (
            tenant_id  TEXT NOT NULL DEFAULT 'default',
            id         TEXT NOT NULL,
            label      TEXT NOT NULL,
            sort_order INTEGER,
            in_scope   BOOLEAN DEFAULT TRUE,
            scope_note TEXT,
            active     BOOLEAN DEFAULT TRUE,
            PRIMARY KEY (tenant_id, id)
        )
    """,
    "discipline_materials": """
        CREATE TABLE discipline_materials (
            tenant_id         TEXT NOT NULL DEFAULT 'default',
            discipline_id     TEXT NOT NULL,
            material_category TEXT NOT NULL,
            PRIMARY KEY (tenant_id, discipline_id, material_category)
        )
    """,
    "ipts": """
        CREATE TABLE ipts (
            tenant_id   TEXT NOT NULL DEFAULT 'default',
            id          TEXT NOT NULL,
            label       TEXT,
            manager     TEXT,
            active      BOOLEAN DEFAULT TRUE,
            merged_into TEXT,
            PRIMARY KEY (tenant_id, id)
        )
    """,
    "design_sections": """
        CREATE TABLE design_sections (
            tenant_id  TEXT NOT NULL DEFAULT 'default',
            id         TEXT NOT NULL,
            label      TEXT,
            km_from    REAL,
            km_to      REAL,
            scope_note TEXT,
            PRIMARY KEY (tenant_id, id)
        )
    """,
    "work_sections": """
        CREATE TABLE work_sections (
            tenant_id          TEXT NOT NULL DEFAULT 'default',
            section_id         TEXT NOT NULL,
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
            receives_override  TEXT,
            PRIMARY KEY (tenant_id, section_id)
        )
    """,
    "zones": """
        CREATE TABLE zones (
            tenant_id       TEXT NOT NULL DEFAULT 'default',
            id              TEXT NOT NULL,
            name            TEXT NOT NULL,
            kind            TEXT,
            geometry        TEXT,
            affects_routing BOOLEAN DEFAULT TRUE,
            starts_on       TEXT,
            ends_on         TEXT,
            note            TEXT,
            active          BOOLEAN DEFAULT TRUE,
            created_at      TEXT,
            updated_at      TEXT,
            speed_kph       REAL,
            haul_mode       TEXT,
            PRIMARY KEY (tenant_id, id)
        )
    """,
    "route_haul_roads": """
        CREATE TABLE route_haul_roads (
            tenant_id  TEXT NOT NULL DEFAULT 'default',
            route_id   TEXT NOT NULL,
            zone_id    TEXT NOT NULL,
            seq        INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            PRIMARY KEY (tenant_id, route_id, zone_id)
        )
    """,
    # ----------------------------------------------------------------------- #
    #  Phase 5a — gates                                                        #
    # ----------------------------------------------------------------------- #
    # A location may have several gates. Until 5a the access point was a single
    # nullable (gate_lat, gate_lon) pair on `locations`, which cannot express the
    # thing every real site does: come in at one gate and leave by another.
    #
    #   direction   'access' (entry only) | 'egress' (exit only) | 'both'
    #               This is the whole reason the pair on `locations` had to go —
    #               a one-way gate system makes the leg swap in _bake_leg wrong.
    #   safety_minutes
    #               B3: induction is PER GATE and applies to whatever arrives.
    #               No per-vehicle override — offered and declined. NULL/0 means
    #               no induction time, which is what every migrated gate gets, so
    #               no existing cycle time moves on the day this ships.
    #   internal_travel_minutes
    #               B5(c): the FLAT fallback for gate -> working face travel, used
    #               only when no drawn haul road already carries those minutes in
    #               route_geometry.duration_hr. Phase 4 already puts internal travel
    #               there; adding a flat figure on top of a drawn road counts the
    #               same minutes twice. Drawn road wins — see network._turnaround_parts.
    #   is_default  which gate a route with no explicit selection gets, per direction.
    #   active      a deactivated gate is history, not a delete. B2: a route that
    #               names one refuses to bake and says which gate.
    "location_gates": """
        CREATE TABLE location_gates (
            tenant_id               TEXT NOT NULL DEFAULT 'default',
            id                      TEXT NOT NULL,
            location_id             TEXT NOT NULL,
            name                    TEXT NOT NULL,
            direction               TEXT NOT NULL DEFAULT 'both',
            lat                     REAL NOT NULL,
            lon                     REAL NOT NULL,
            safety_minutes          REAL,
            internal_travel_minutes REAL,
            is_default              BOOLEAN NOT NULL DEFAULT FALSE,
            active                  BOOLEAN NOT NULL DEFAULT TRUE,
            note                    TEXT,
            created_at              TEXT,
            updated_at              TEXT,
            PRIMARY KEY (tenant_id, id)
        )
    """,
    # ----------------------------------------------------------------------- #
    #  Week 1 (Task C/D) — the four-week look-ahead                            #
    # ----------------------------------------------------------------------- #
    # ⭐ This sits ON the month line; it does NOT widen `forecasts`. The key is the
    # forecast line's own key plus week_index, so a week row can only ever exist for a
    # line that exists, and the monthly UNIQUE constraint is untouched. Adding a
    # week_index to `forecasts` instead would have multiplied every existing row by
    # four and broken the public map's monthly aggregation.
    #
    #   discipline / section_id are NOT NULL DEFAULT '' for the same reason they are on
    #   `forecasts`: NULLs compare distinct inside a key in both backends, so a
    #   nullable column would leave the row unconstrained and the upsert would silently
    #   insert duplicates instead of updating.
    #
    #   status      'derived' | 'edited' | 'confirmed'. A `derived` week is refreshed
    #               when its parent month is re-approved; the other two are not. That
    #               is the whole reason the column exists rather than a boolean.
    #   weather / wetness / traffic / other
    #               free TEXT, optional, empty string allowed. NO scores, no APIs, no
    #               colour-as-logic — they are notes a person types at confirm time.
    #   actual_*    ⭐ Task D's columns, created HERE with the table rather than added
    #               a day later. A second ALTER against the live Postgres is a second
    #               chance to get a migration wrong, for no benefit.
    "forecast_weeks": """
        CREATE TABLE forecast_weeks (
            tenant_id    TEXT NOT NULL DEFAULT 'default',
            route_id     TEXT NOT NULL,
            month_index  INTEGER NOT NULL,
            discipline   TEXT NOT NULL DEFAULT '',
            section_id   TEXT NOT NULL DEFAULT '',
            week_index   INTEGER NOT NULL,
            planned_qty  REAL,
            unit         TEXT,
            status       TEXT NOT NULL DEFAULT 'derived',
            -- ⚠️ NOT in the build list's column list, and here on purpose. "If the
            -- parent month qty changes and is re-approved ... show 'parent month
            -- changed'" cannot be answered without it: an `edited` week always differs
            -- from parent/4 (that is what editing means), so comparing the two would
            -- flag every edited week forever. This holds the parent quantity AS AT the
            -- moment the row was last written, so parent_changed is an exact equality
            -- test. Refreshing a `derived` row re-stamps it; an `edited` or `confirmed`
            -- row keeps its old value, which is what raises the flag, and editing or
            -- confirming again re-stamps it, which is what clears it.
            parent_qty   REAL,
            weather      TEXT,
            wetness      TEXT,
            traffic      TEXT,
            other        TEXT,
            confirmed_by TEXT,
            confirmed_at TEXT,
            actual_qty   REAL,
            actual_note  TEXT,
            actual_by    TEXT,
            actual_at    TEXT,
            created_at   TEXT,
            updated_at   TEXT,
            PRIMARY KEY (tenant_id, route_id, month_index, discipline, section_id,
                         week_index)
        )
    """,
    # ----------------------------------------------------------------------- #
    #  Week 1 (Task D2) — typed weekly consumption from a stockpile            #
    # ----------------------------------------------------------------------- #
    # Consumption only. The BALANCE is a read model computed in stockpiles.py and is
    # deliberately not stored: inbound comes from forecast_weeks.actual_qty, which
    # changes whenever someone types an actual, and a stored balance would be a second
    # copy that drifts the moment it does.
    #
    # Same week buckets as forecast_weeks. Typed only — there is no file, no importer
    # and no upload endpoint anywhere in this system.
    "stockpile_weeks": """
        CREATE TABLE stockpile_weeks (
            tenant_id    TEXT NOT NULL DEFAULT 'default',
            location_id  TEXT NOT NULL,
            month_index  INTEGER NOT NULL,
            week_index   INTEGER NOT NULL,
            consumed_qty REAL,
            unit         TEXT,
            note         TEXT,
            updated_by   TEXT,
            updated_at   TEXT,
            PRIMARY KEY (tenant_id, location_id, month_index, week_index)
        )
    """,
}

# The primary key each table ends up with, for the Postgres in-place path.
_TENANT_PK = {
    "forecasts": "(tenant_id, id)",
    "locations": "(tenant_id, id)",
    "routes": "(tenant_id, id)",
    "route_geometry": "(tenant_id, route_id, vehicle_profile, leg, alt_index)",
    "disciplines": "(tenant_id, id)",
    "discipline_materials": "(tenant_id, discipline_id, material_category)",
    "ipts": "(tenant_id, id)",
    "design_sections": "(tenant_id, id)",
    "work_sections": "(tenant_id, section_id)",
    "zones": "(tenant_id, id)",
    "route_haul_roads": "(tenant_id, route_id, zone_id)",
    "location_gates": "(tenant_id, id)",
    "forecast_weeks": "(tenant_id, route_id, month_index, discipline, section_id, "
                      "week_index)",
    "stockpile_weeks": "(tenant_id, location_id, month_index, week_index)",
}

# Extra UNIQUE constraints that must also take tenant_id. Only forecasts has one.
_TENANT_UNIQUE = {
    "forecasts": "(tenant_id, route_id, month_index, discipline, section_id)",
}


def _columns_of(cur, table):
    """The columns a table really has right now, in order."""
    try:
        if IS_PG:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (table,),
            )
            return [r[0] for r in cur.fetchall()]
        cur.execute(f"PRAGMA table_info({table})")
        return [r[1] for r in cur.fetchall()]
    except Exception:
        return []


def _ddl_columns(ddl):
    """
    The column names declared in one of the _TENANT_DDL blocks.

    🔴 THE `--` COMMENTS HAVE TO GO FIRST, AND THAT IS NOT COSMETIC.
    ---------------------------------------------------------------
    This splits the body on top-level commas and takes the first token of each
    piece. Several of the DDL blocks carry `-- …` comments explaining a column,
    and prose contains commas. One comma inside a comment splits a fragment in
    the middle of that prose, so the piece that actually declares the NEXT column
    begins with comment text and its first token is an English word. The column
    is then absent from the returned list.

    That list is `want` in _migrate_table_to_tenant(), and the SQLite rebuild
    copies only `[c for c in want if c in have]`. A column missing from it is
    DROPPED, with its data, silently.

    ⚠️ FOUND 2026-09-01, AND IT WAS ALREADY LIVE. `routes.origin_gate_id` —
    Phase 5a — had been invisible to this parser since the day it shipped, while
    `dest_gate_id` survived because it happened to start its own fragment. A
    SQLite database migrating through 4.5 would have lost a route's ORIGIN gate
    selection and kept its destination one. Nothing would have errored:
    gates.resolve() falls back to the location's default gate and then to the
    legacy lat/lon pair, so the route would simply have started routing from the
    wrong side of the site. "A fallback chain is what makes a schema change
    invisible", exactly as roadmap.md warns.

    ⚠️ The LIVE Postgres was never exposed: the Postgres branch of
    _migrate_table_to_tenant() uses ADD COLUMN and constraint swaps and never
    calls this. And 4.5 ran there on 2026-08-30, before 5a added the columns.
    The exposure was local SQLite, and any database that still needs the 4.5
    migration.

    test_week1.py asserts that every column literally declared in every
    _TENANT_DDL block comes back from here, so a future comment cannot re-open it.
    """
    cols = []
    body = ddl[ddl.index("(") + 1:ddl.rindex(")")]
    # strip `-- …` to end of line before anything else looks at a comma
    body = "\n".join(l.split("--", 1)[0] for l in body.splitlines())
    depth = 0
    line = ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            cols.append(line.strip())
            line = ""
        else:
            line += ch
    cols.append(line.strip())
    out = []
    for c in cols:
        first = c.split()[0] if c.split() else ""
        if first.upper() in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"):
            continue
        if first:
            out.append(first)
    return out


def _pg_constraint_names(cur, table, kind):
    """Constraint names of a given kind ('p' primary, 'u' unique) on a table."""
    try:
        cur.execute(
            "SELECT con.conname FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "WHERE rel.relname = %s AND con.contype = %s",
            (table, kind),
        )
        return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


def _migrate_table_to_tenant(conn, cur, table):
    """
    Give one table its tenant_id column and fold it into the primary key.

    Idempotent: the presence of tenant_id is the flag for 'already done'.

    Postgres redefines the constraints in place. SQLite cannot drop a primary key
    at all — the same wall `_migrate_route_geometry_key()` hit — so there the
    table is rebuilt, copied and swapped. Existing rows are stamped with
    TENANT_DEFAULT, which is what makes this a no-op for the single tenant that
    exists today.
    """
    if _has_column(cur, table, "tenant_id"):
        return "already"

    ddl = _TENANT_DDL[table]
    want = _ddl_columns(ddl)

    if IS_PG:
        try:
            cur.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id "
                f"TEXT NOT NULL DEFAULT '{TENANT_DEFAULT}'"
            )
            conn.commit()
        except Exception:
            conn.rollback()
            return "failed"
        # the old unique constraint has to go before the new one can be added,
        # or a two-tenant insert fails on a constraint nobody remembers existing
        for kind, target in (("p", _TENANT_PK[table]), ("u", _TENANT_UNIQUE.get(table))):
            if not target:
                continue
            for name in _pg_constraint_names(cur, table, kind):
                try:
                    cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
                    conn.commit()
                except Exception:
                    conn.rollback()
            try:
                if kind == "p":
                    cur.execute(f"ALTER TABLE {table} ADD PRIMARY KEY {target}")
                else:
                    cur.execute(f"ALTER TABLE {table} ADD UNIQUE {target}")
                conn.commit()
            except Exception:
                conn.rollback()
        return "migrated"

    # SQLite: rebuild, copy, swap.
    have = _columns_of(cur, table)
    if not have:
        return "missing"
    carry = [c for c in want if c in have and c != "tenant_id"]
    cols = ", ".join(carry)
    try:
        cur.execute(f"DROP TABLE IF EXISTS {table}_pre45")
        cur.execute(f"ALTER TABLE {table} RENAME TO {table}_pre45")
        cur.execute(ddl)
        cur.execute(
            f"INSERT INTO {table} (tenant_id, {cols}) "
            f"SELECT '{TENANT_DEFAULT}', {cols} FROM {table}_pre45"
        )
        cur.execute(f"DROP TABLE {table}_pre45")
        conn.commit()
        return "migrated"
    except Exception as e:
        conn.rollback()
        print(f"Phase 4.5: {table} tenant migration failed:", e)
        try:
            # put the original back rather than leaving the app with no table
            cur.execute(f"DROP TABLE IF EXISTS {table}")
            cur.execute(f"ALTER TABLE {table}_pre45 RENAME TO {table}")
            conn.commit()
        except Exception:
            conn.rollback()
        return "failed"


def init_tenant():
    """
    Phase 4.5. Runs AFTER every other init_*, because the SQLite rebuild copies
    the columns a table really has, and the earlier inits are what add several of
    them by ALTER. Run it first and those columns would be silently dropped.

    Safe to call on every boot; each table's migration is a no-op once done.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        done = {}
        for table in sorted(TENANTED_TABLES):
            done[table] = _migrate_table_to_tenant(conn, cur, table)
        moved = [t for t, r in done.items() if r == "migrated"]
        if moved:
            print(f"Phase 4.5: tenant key added to {len(moved)} table(s): {', '.join(moved)}")
        bad = [t for t, r in done.items() if r == "failed"]
        if bad:
            print(f"⚠️  Phase 4.5: tenant migration FAILED on: {', '.join(bad)}")
        return done
    finally:
        conn.close()

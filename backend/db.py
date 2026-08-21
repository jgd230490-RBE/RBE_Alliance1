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


def init_db():
    """Create the forecasts table if it doesn't exist, and add any new columns."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS forecasts (
                id                   TEXT PRIMARY KEY,
                route_id             TEXT NOT NULL,
                month_index          INTEGER NOT NULL,
                quantity             REAL NOT NULL,
                unit                 TEXT NOT NULL,
                material_type        TEXT,
                material_description TEXT,
                vehicle_type         TEXT,
                vehicle_type_2       TEXT,
                split_pct            INTEGER DEFAULT 100,
                submitted_by         TEXT,
                status               TEXT NOT NULL DEFAULT 'Pending',
                reject_reason        TEXT,
                UNIQUE (route_id, month_index)
            )
            """
        )
        conn.commit()
        # migrate pre-existing tables: add the newer columns if they're missing
        for col, typ in (
            ("material_description", "TEXT"),
            ("vehicle_type_2", "TEXT"),
            ("split_pct", "INTEGER DEFAULT 100"),
            ("submitted_by", "TEXT"),
        ):
            try:
                if IS_PG:
                    cur.execute(f"ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS {col} {typ}")
                else:
                    cur.execute(f"ALTER TABLE forecasts ADD COLUMN {col} {typ}")
                conn.commit()
            except Exception:
                conn.rollback()  # column already present — fine
    finally:
        conn.close()


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

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

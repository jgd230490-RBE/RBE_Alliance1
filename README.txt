rbe-lookahead-hotfix3-db-pool-0909.zip
======================================
Delivered 2026-09-09 (night). Extract over the repo root, ON TOP of slices 3-6,
hotfix 1 (blank page) and hotfix 2 (Tark Tee). Four files. No schema change.
No new dependency (psycopg2's own pool).

⚠️ hotfix 2's backend/main.py was NOT on the running server when I checked
(/api/forecast-weeks/tark-tee returned 404). Make sure hotfix 2 landed too.

WHAT WAS MEASURED, FROM YOUR OWN SESSION
----------------------------------------
Signed in on the browser pane on your machine:
  /api/forecast-days                    200 in 17.7 s   (ONE approved line)
  /api/lookahead?tark_tee=0             still running at 25 s (aborted)
So the freeze is not (only) Tark Tee. It is the ordinary read.

THE CAUSE
---------
db.query() and db.execute() each opened a NEW Postgres connection
(psycopg2.connect: TLS handshake + auth) for ONE statement, then closed it.
The one-line /api/forecast-days read issues 91 statements -- 46 reads and 45
WRITES, because the lazy materialisation rewrote every derived week and day it
had just read, on every read. 91 x ~190 ms per connect = 17.3 s. Yours: 17.7 s.
SQLite in the sandbox does the same read in 85 ms, so no test saw it. Every
endpoint has always paid this; the Look-ahead is the first to make enough
statements for it to become tens of seconds. This is the freeze.

THE FIX
-------
1. backend/db.py: Postgres connections come from a small pool
   (psycopg2.pool.ThreadedConnectionPool, size DB_POOL_MAX, default 6) and are
   RETURNED on close() -- every existing call site is unchanged. A connection
   returned mid-transaction is rolled back first; one the server dropped while
   idle is discarded and the statement retried once on a fresh connection. A
   statement error is never retried. DB_POOL=0 (env) restores the old
   one-connection-per-statement behaviour if anything about the pool misbehaves.
2. backend/weeks.py, backend/days.py: a derived week/day is rewritten ONLY when
   its figure moved. A second read of a settled week now makes 0 writes (was 45
   for three lines). A week whose month moved still refreshes -- asserted.

Expected effect: the same reads drop from ~90 network round trips WITH a
connect each to ~46 (days) / ~102 (page) round trips WITHOUT one. On Render's
internal network that should be well under 2 s. If it is still slow, it is
now query count, and I will batch the remaining reads next.

WHAT WAS NOT TESTED
-------------------
psycopg2 is not installed in the sandbox, so the pool has NOT run against a
real Postgres. The proxy (return-to-pool, rollback-before-reuse, dead-
connection discard) and the retry (once, only on a dead connection, never on
bad SQL) are exercised against stand-in objects -- 14 assertions. The first
real run is your deploy. Watch the boot log for "db: Postgres connection pool
ready"; if it says "connection pool unavailable" instead, the app still works
the old (slow) way and I need that log line.

FILES
-----
  backend/db.py                    the pool, _PooledConn, _run() with retry
  backend/weeks.py                 no-op refresh skipped (+ reads the fields it compares)
  backend/days.py                  no-op refresh skipped
  backend/tests/test_lookahead.py  191 -> 205: statement budget, 0 writes on a
                                   second read, the pool proxy + retry on stand-ins
  README.txt

SUITE
-----
2,522 / 0 on a fresh clone with all four zips. Eight deliberate regressions,
eight caught -- one only after wrapping an assertion that crashed instead of
failing (lesson 13, third time tonight).

LANDING GREPS
-------------
  grep -c "ThreadedConnectionPool" backend/db.py   -> 1
  grep -c "def _run" backend/db.py                  -> 1
  grep -c "planned_qty, unit, parent_qty FROM forecast_weeks" backend/weeks.py -> 1

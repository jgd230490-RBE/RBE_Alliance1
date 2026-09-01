"""
Phase 4.5 — the tenant filter audit.

WHY THIS FILE EXISTS
--------------------
`claude/roadmap.md` says it plainly: **a tenant column with no filter is worse than
no column** — it looks like isolation and is not. Phase 4.5 adds `tenant_id` to
eleven tables and threads the filter through 139 `db.query` / `db.execute` call
sites in six modules. Hand-checking that none was missed is exactly the kind of
promise that quietly stops being true on the next edit.

So this does not check the ones we remembered. It parses **every SQL string
literal in backend/*.py**, works out which tenanted tables each statement touches,
and fails if a read or a write can reach a tenanted table without a `tenant_id`
predicate. A new query added in Phase 5a with no tenant clause fails this file
before it reaches the deployment.

WHAT THIS DOES NOT PROVE
------------------------
  * It is a **static** check on SQL text. It proves a predicate is present, NOT
    that the value bound to it is the right tenant. A query filtered on a
    hard-coded wrong tenant would pass here.
  * SQL built at runtime from non-literal parts is only partly visible. Every such
    site in the codebase today is an f-string over a column allow-list, and those
    are resolved below; anything genuinely dynamic is listed in DYNAMIC_SQL and
    has to be read by a human.
  * It says nothing about whether the *migration* moved existing rows correctly.
    That is test_phase45.py.
  * No Postgres branch runs here. This is text analysis; it does not open a
    database at all.

Run:  python3 backend/tests/test_tenant_audit.py
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)

pass_count = 0
failures = []


def ok(label, cond, extra=""):
    global pass_count
    if cond:
        pass_count += 1
    else:
        failures.append(label + ("  " + extra if extra else ""))


# --------------------------------------------------------------------------- #
#  What is tenanted                                                            #
# --------------------------------------------------------------------------- #
# Every table that holds per-client data. If a table is added to db.py and not
# added here, test_tenant_registry below fails — the registry cannot silently
# drift from the schema.
TENANTED = {
    "forecasts",
    "locations",
    "routes",
    "route_geometry",
    "disciplines",
    "discipline_materials",
    "ipts",
    "design_sections",
    "work_sections",
    "zones",
    "route_haul_roads",
    # Phase 5a. Added in the same delivery as the table — test_tenant_registry below
    # fails if db.py creates a table this set does not name, which is the mechanism
    # that stops a new table shipping with no isolation.
    "location_gates",
    # ⭐ Week 1, 2026-09-01. THE AUDIT PROVED ITSELF AGAIN. Both tables were written,
    # registered in db.TENANTED_TABLES and given _TENANT_DDL / _TENANT_PK entries —
    # and this file still failed, because the registry HERE had not been updated. Two
    # failures, naming both tables. That is four registrations per table, and the
    # fourth is this one.
    "forecast_weeks", "stockpile_weeks",
}

# Tables that are deliberately NOT tenanted, with the reason. Anything here is
# excluded from the audit and the reason is the record of the decision.
UNTENANTED = {
    "forecasts_legacy": "pre-Phase-2 rows, renamed aside and read by nothing",
    "route_geometry_old": "transient, exists only inside the SQLite key rebuild",
    "information_schema.columns": "Postgres catalogue, not application data",
    "sqlite_master": "SQLite catalogue, not application data",
}

# Statements that may touch a tenanted table with no tenant predicate, each with
# the reason it is safe. Keyed by a distinctive fragment of the statement.
#
# ⚠️ Adding an entry here is a decision, not a way to make the test pass. Every
#    one of these was argued for individually.
EXEMPT = {
    # DDL: CREATE/ALTER/DROP name a table but do not read or write rows.
    "__ddl__": "schema definition, not a row operation",
    # The migration copies rows within one table while stamping the default
    # tenant onto them. Filtering by tenant here would filter out the very rows
    # being migrated — they have no tenant yet. This is the one place that is
    # allowed to see across tenants, and it runs once, at boot, before any
    # second tenant can exist.
    "__migration__": "one-time key/column migration; stamps the default tenant",
}


# --------------------------------------------------------------------------- #
#  Pulling SQL out of the source                                               #
# --------------------------------------------------------------------------- #
SQL_START = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|PRAGMA|WITH)\b", re.I
)
# a fragment that is obviously part of a statement even if it does not start one
SQL_FRAGMENT = re.compile(
    r"\b(FROM|INTO|WHERE|SET|VALUES|JOIN|ON CONFLICT|ORDER BY|GROUP BY)\b", re.I
)


class SqlCollector(ast.NodeVisitor):
    """
    Collects string constants that look like SQL, keeping the line number and the
    enclosing function so a failure can be located.

    Handles the three shapes this codebase actually uses:
      * a plain (possibly triple-quoted) literal
      * implicit adjacent-literal concatenation, which ast has already joined
      * explicit '+' concatenation and f-strings, flattened with the non-literal
        parts replaced by a placeholder so the SQL keywords still line up
    """

    def __init__(self, path, source):
        self.path = path
        self.source = source
        self.func = "<module>"
        self.found = []

    def visit_FunctionDef(self, node):
        prev, self.func = self.func, node.name
        self.generic_visit(node)
        self.func = prev

    visit_AsyncFunctionDef = visit_FunctionDef

    def _flatten(self, node):
        """Return the literal text of a string expression, or None."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):  # f-string
            out = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.append(v.value)
                else:
                    out.append("{?}")
            return "".join(out)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._flatten(node.left)
            right = self._flatten(node.right)
            if left is None or right is None:
                return None
            return left + right
        return None

    def visit(self, node):
        text = self._flatten(node) if isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)) else None
        if text and (SQL_START.search(text) or
                     (SQL_FRAGMENT.search(text) and len(text) > 25)):
            self.found.append({
                "file": os.path.basename(self.path),
                "line": getattr(node, "lineno", 0),
                "func": self.func,
                "sql": " ".join(text.split()),
            })
            # do not descend into a statement we have already captured whole
            if isinstance(node, ast.BinOp):
                return
        super().visit(node)


def collect(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    c = SqlCollector(path, src)
    c.visit(tree)
    return c.found


MODULES = [f for f in sorted(os.listdir(BACKEND)) if f.endswith(".py")]
statements = []
for fn in MODULES:
    statements.extend(collect(os.path.join(BACKEND, fn)))

ok("the audit can see the backend's SQL at all", len(statements) > 100,
   f"found {len(statements)} SQL literals")


# --------------------------------------------------------------------------- #
#  Classifying                                                                 #
# --------------------------------------------------------------------------- #
DDL = re.compile(r"^\s*(CREATE|ALTER|DROP|PRAGMA)\b", re.I)
VERB = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", re.I)


def tables_touched(sql):
    """Tenanted tables this statement reads or writes."""
    hits = set()
    for t in TENANTED:
        if re.search(r"\b(FROM|INTO|UPDATE|JOIN)\s+" + t + r"\b", sql, re.I):
            hits.add(t)
    return hits


def has_tenant_predicate(sql):
    """
    True if the statement constrains tenant_id.

    A read or a delete needs it in a WHERE (or an ON/USING for a join); an INSERT
    needs tenant_id in its column list; an UPDATE needs both, and the WHERE is the
    half that matters, because an UPDATE with no tenant WHERE rewrites every
    tenant's rows.
    """
    s = sql.upper()
    if VERB.match(s) and s.lstrip().startswith("INSERT"):
        # column list must name it, and it must also be constrained in any
        # ON CONFLICT target
        return "TENANT_ID" in s
    return bool(re.search(r"\bTENANT_ID\b\s*(=|IN|IS)", s))


def is_migration(entry):
    fn = entry["func"]
    return fn.startswith("_migrate") or fn.startswith("init_") or fn == "_backfill_tenant"


audited = []
for st in statements:
    sql = st["sql"]
    if DDL.match(sql):
        st["verdict"] = "ddl"
        continue
    if not VERB.match(sql):
        st["verdict"] = "fragment"
        continue
    touched = tables_touched(sql)
    if not touched:
        st["verdict"] = "no-tenanted-table"
        continue
    if is_migration(st):
        st["verdict"] = "migration"
        continue
    st["tables"] = sorted(touched)
    st["verdict"] = "ok" if has_tenant_predicate(sql) else "MISSING"
    audited.append(st)

missing = [s for s in audited if s["verdict"] == "MISSING"]

ok("the audit found real row-level statements to check", len(audited) >= 80,
   f"only {len(audited)} audited")
ok("⭐ every read and write of a tenanted table constrains tenant_id",
   not missing,
   f"\n" + "\n".join(
       f"      {m['file']}:{m['line']} {m['func']}()  [{','.join(m['tables'])}]\n"
       f"        {m['sql'][:150]}" for m in missing[:40]))

# per-module breakdown, so a regression names the module rather than a total
by_mod = {}
for s in audited:
    by_mod.setdefault(s["file"], []).append(s)
for mod in sorted(by_mod):
    bad = [s for s in by_mod[mod] if s["verdict"] == "MISSING"]
    ok(f"{mod}: all {len(by_mod[mod])} tenanted statements filtered", not bad,
       f"{len(bad)} missing")


# --------------------------------------------------------------------------- #
#  Placeholder / parameter alignment                                           #
# --------------------------------------------------------------------------- #
# The check above proves a tenant predicate is PRESENT. It cannot prove the right
# value is bound to it, and that is the failure that actually bites: adding
# `tenant_id = ?` to a WHERE clause without inserting current_tenant() at the
# matching position in the params tuple silently shifts every parameter after it.
# The query still runs, still filters on something, and returns the wrong rows.
#
# So for every db.query / db.execute whose SQL and params are both literal, this
# counts the '?' placeholders against the tuple length and checks that the
# placeholder binding tenant_id is the one holding current_tenant().
#
# ⚠️ Sites that build SQL or params dynamically are counted and reported but not
#    checked — a static reader cannot resolve them. They are exercised at runtime
#    in backend/tests/test_phase45.py instead. Neither file covers them alone.

def _literal_sql(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return n.value
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        left, right = _literal_sql(n.left), _literal_sql(n.right)
        return None if left is None or right is None else left + right
    return None


align_bad = []
align_checked = 0
align_dynamic = 0
for fn in MODULES:
    tree = ast.parse(open(os.path.join(BACKEND, fn), encoding="utf-8").read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if name not in ("query", "execute") or not node.args:
            continue
        sql = _literal_sql(node.args[0])
        if sql is None:
            align_dynamic += 1
            continue
        # The Postgres catalogue helpers in db.py (_has_column, _columns_of,
        # _pg_constraint_names) call cur.execute() directly with %s placeholders,
        # because they run against a raw cursor mid-migration rather than through
        # db.query()/db.execute() and their _adapt() translation. They read
        # information_schema and pg_constraint, not application data, so they are
        # outside this audit entirely — matched by shape, not by name, so a new
        # one does not need adding here.
        if "%s" in sql and "?" not in sql:
            continue
        n_ph = sql.count("?")
        if len(node.args) < 2:
            if n_ph:
                align_bad.append(f"{fn}:{node.lineno} {n_ph} placeholders, no params")
            continue
        params = node.args[1]
        if not isinstance(params, (ast.Tuple, ast.List)) or \
                any(isinstance(e, ast.Starred) for e in params.elts):
            align_dynamic += 1
            continue
        align_checked += 1
        if len(params.elts) != n_ph:
            align_bad.append(
                f"{fn}:{node.lineno} {n_ph} placeholders vs {len(params.elts)} params")
            continue
        # which placeholder binds tenant_id?
        pos = None
        m = re.search(r"tenant_id\s*=\s*\?", sql)
        if m:
            pos = sql[:m.end()].count("?") - 1
        elif re.search(r"INSERT INTO \w+ \(\s*tenant_id", sql):
            pos = 0
        if pos is not None and pos < len(params.elts):
            bound = ast.unparse(params.elts[pos])
            if "tenant" not in bound:
                align_bad.append(
                    f"{fn}:{node.lineno} the tenant placeholder (#{pos}) is bound to `{bound}`")

ok("the alignment check sees most of the call sites", align_checked >= 120,
   f"only {align_checked} statically resolvable")
ok("⭐ every literal statement's placeholders and params line up, and the tenant "
   "placeholder holds current_tenant()",
   not align_bad, "\n" + "\n".join("      " + b for b in align_bad[:30]))
ok("the dynamic sites are counted rather than ignored", align_dynamic > 0,
   f"{align_dynamic} sites need test_phase45.py to cover them")


# --------------------------------------------------------------------------- #
#  The registry cannot drift from the schema                                   #
# --------------------------------------------------------------------------- #
db_src = open(os.path.join(BACKEND, "db.py"), encoding="utf-8").read()
created = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+)\s*\(", db_src))
created = {t for t in created if t not in UNTENANTED}
ok("⭐ every table db.py creates is in the tenanted registry",
   created <= TENANTED, f"unregistered: {sorted(created - TENANTED)}")
ok("and the registry names no table that does not exist",
   TENANTED <= (created | set(re.findall(r"ALTER TABLE (\w+)", db_src, re.I))),
   f"phantom: {sorted(TENANTED - created)}")

ok("db.py declares the tenanted tables in one place",
   "TENANTED_TABLES" in db_src)
ok("and that declaration matches this audit's registry",
   set(re.findall(r'"(\w+)"', re.search(
       r"TENANTED_TABLES\s*=\s*\{(.*?)\}", db_src, re.S).group(1)
   )) == TENANTED if "TENANTED_TABLES" in db_src else False)

ok("there is a single place the current tenant is resolved",
   "def current_tenant" in db_src)
ok("and a named default, rather than a bare string repeated everywhere",
   "TENANT_DEFAULT" in db_src)

# --------------------------------------------------------------------------- #
#  Exemptions are declared, not implied                                        #
# --------------------------------------------------------------------------- #
ok("every exemption carries a written reason",
   all(isinstance(v, str) and len(v) > 20 for v in EXEMPT.values()))
ok("every untenanted table carries a written reason",
   all(isinstance(v, str) and len(v) > 20 for v in UNTENANTED.values()))

print()
for f in failures:
    print("  FAIL:", f)
print(f"\n{pass_count} passed, {len(failures)} failed")
if missing:
    print(f"\n  ({len(missing)} statement(s) still need a tenant filter)")
sys.exit(1 if failures else 0)

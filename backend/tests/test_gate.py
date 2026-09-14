"""
The /map/ and /help/ gate, 2026-09-14.

WHAT IS ASSERTED
----------------
The whole policy, because the whole policy is one pure function. Every combination of
(scope, valid code, cookie level, password configured, gate on/off) is walked; the
cookie is round-tripped, forged, expired, tampered with and re-keyed; and the decision
that matters most — no password configured means CLOSED, not open — is asserted
directly, because that is where ADMIN_TOKEN got it wrong (open question C11).

Then, at source level: that main.py actually calls it, that a staff sign-in sets the
cookie the iframe needs, and that the refusal pages are NOT filed under map/ or
frontend/help/ — a password page inside the thing it guards could never be shown.

WHAT THIS DOES NOT PROVE
------------------------
  * The HTTP layer is stubbed. The middleware is never executed, no real request is
    routed, `response.set_cookie` is never called and no `Set-Cookie` header is ever
    produced. Everything below reads main.py as TEXT for that half.
  * Nothing in a browser. The password page's fetch() has never run.
  * Nothing proves Render sends the cookie back over HTTPS — `secure: True` is asserted
    as a value in a dict, not observed on a wire.

Run:  python3 backend/tests/test_gate.py
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
ROOT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)

import gate  # noqa: E402  — stdlib only, no stubbing needed

PASS = 0
FAIL = []


def ok(label, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{label} {extra}".strip())


def env(**kw):
    """Set or clear env vars. gate.py reads os.environ on every call — nothing caches,
    which is what lets a password rotation invalidate live cookies."""
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def clean(password="corridor-2026", admin="tok", gate_on=True, secret=None):
    env(MAP_PASSWORD=password, ADMIN_TOKEN=admin, GATE_SECRET=secret,
        MAP_GATE=None if gate_on else "off")


# ---------------------------------------------------------------- 1. scope_for
clean()
for p in ("/map", "/map/", "/map/index.html", "/map/data/alignment.js", "/map/?v=123"):
    ok(f"scope_for({p!r}) is the map", gate.scope_for(p) == "map")
for p in ("/help", "/help/", "/help/index.html", "/help/media/S04.png"):
    ok(f"scope_for({p!r}) is help", gate.scope_for(p) == "help")
for p in ("/api/public/month-kpis", "/api/public/route-forecasts?from=1&to=4",
          "/api/zones", "/api/restrictions/layers", "/api/routes/restrictions",
          "/api/streetview", "/api/streetview/meta"):
    ok(f"scope_for({p!r}) is map — the map's DATA is gated, not just its page",
       gate.scope_for(p) == "map")

ok("🔴 /api/meta is NOT gated — the staff app fetches it on mount, before sign-in; "
   "gating it would empty the login screen",
   gate.scope_for("/api/meta") is None)
ok("🔴 /api/health is NOT gated — Render polls it", gate.scope_for("/api/health") is None)
for p in ("/", "/api/auth", "/api/map-auth", "/api/forecasts", "/api/costing/lines",
          "/api/lookahead", "/mapping", "/helper", "/api/metadata"):
    ok(f"scope_for({p!r}) is ungated", gate.scope_for(p) is None,
       f"got {gate.scope_for(p)!r}")
ok("a path without a leading slash still resolves", gate.scope_for("map/index.html") == "map")
ok("scope_for(None) and scope_for('') are safe",
   gate.scope_for(None) is None and gate.scope_for("") is None)

# ---------------------------------------------------------------- 2. sign / verify
clean()
for lvl in (gate.LEVEL_STAFF, gate.LEVEL_MAP):
    tok = gate.sign(lvl)
    ok(f"a fresh {lvl} cookie verifies as {lvl}", gate.verify(tok) == lvl)
    ok(f"the {lvl} cookie is level.expiry.signature", len(tok.split(".")) == 3)

tok = gate.sign(gate.LEVEL_MAP)
lvl, exp, sig = tok.split(".")
ok("a forged signature is refused", gate.verify(f"{lvl}.{exp}.{'0' * len(sig)}") is None)
ok("🔴 the expiry cannot be extended — it is inside the signature",
   gate.verify(f"{lvl}.{int(exp) + 86400}.{sig}") is None)
ok("🔴 the level cannot be upgraded map -> staff",
   gate.verify(f"staff.{exp}.{sig}") is None)
ok("an expired cookie is refused",
   gate.verify(gate.sign(gate.LEVEL_MAP, now=time.time() - 10, ttl_s=5)) is None)
ok("a cookie expiring exactly now is refused (not >=)",
   gate.verify(gate.sign(gate.LEVEL_MAP, now=1000, ttl_s=0), now=1000) is None)
for junk in (None, "", "garbage", "a.b", "a.b.c.d", "staff.notanumber.ff", 12345, []):
    ok(f"malformed cookie {junk!r} is refused", gate.verify(junk) is None)
ok("an unknown level is refused", gate.verify("admin.9999999999.ff") is None)
try:
    gate.sign("admin")
    ok("sign() refuses an unknown level", False)
except ValueError:
    ok("sign() refuses an unknown level", True)

# rotation
tok_before = gate.sign(gate.LEVEL_MAP)
clean(password="a-new-password")
ok("🔴 rotating MAP_PASSWORD invalidates every cookie issued under the old one — "
   "that is how you revoke someone", gate.verify(tok_before) is None)
clean()
ok("...and restoring the old password makes it valid again (same derivation, no state)",
   gate.verify(tok_before) == gate.LEVEL_MAP)

env(GATE_SECRET="an-explicit-secret")
ok("GATE_SECRET overrides the derived key", gate.verify(tok_before) is None)
tok_secret = gate.sign(gate.LEVEL_MAP)
env(MAP_PASSWORD="anything-else")
ok("...and with GATE_SECRET set, rotating the password does NOT sign people out",
   gate.verify(tok_secret) == gate.LEVEL_MAP)

env(GATE_SECRET=None, MAP_PASSWORD=None, ADMIN_TOKEN=None)
ok("🔴 with nothing configured there is no key, so sign() yields nothing and no cookie "
   "can ever satisfy the gate", gate.sign(gate.LEVEL_MAP) == "" and gate.verify("x.1.y") is None)

# ---------------------------------------------------------------- 3. password_ok
clean(password="corridor-2026")
ok("the right password is accepted", gate.password_ok("corridor-2026"))
ok("surrounding whitespace is tolerated", gate.password_ok("  corridor-2026 "))
ok("the wrong password is refused", not gate.password_ok("corridor-2025"))
ok("a prefix of the password is refused", not gate.password_ok("corridor"))
ok("an empty attempt is refused", not gate.password_ok("") and not gate.password_ok(None))
clean(password=None)
ok("🔴 with no password configured, NOTHING matches — an unset password is not a "
   "password that matches everything",
   not gate.password_ok("") and not gate.password_ok("anything"))

# ---------------------------------------------------------------- 4. decide()
clean()
staff_c = gate.sign(gate.LEVEL_STAFF)
map_c = gate.sign(gate.LEVEL_MAP)

ok("ungated path: allowed with nothing at all",
   gate.decide("/api/costing/lines") == gate.ALLOW)

# the map
ok("map, no credential of any kind -> the password page",
   gate.decide("/map/") == gate.NEED_MAP_PASSWORD)
ok("map, valid access code (the staff app's own fetches) -> allowed",
   gate.decide("/map/", has_valid_code=True) == gate.ALLOW)
ok("⭐ map, STAFF cookie -> allowed. This is the iframe on the staff dashboard: a "
   "document request carries cookies but not the X-Access-Code header",
   gate.decide("/map/", cookie=staff_c) == gate.ALLOW)
ok("map, MAP cookie -> allowed", gate.decide("/map/", cookie=map_c) == gate.ALLOW)
ok("map data endpoint behaves the same as the map page",
   gate.decide("/api/public/month-kpis", cookie=map_c) == gate.ALLOW
   and gate.decide("/api/public/month-kpis") == gate.NEED_MAP_PASSWORD)

# help
ok("🔴 help, MAP cookie -> refused. The map password does not open the staff guide",
   gate.decide("/help/", cookie=map_c) == gate.NEED_STAFF)
ok("help, no credential -> refused", gate.decide("/help/") == gate.NEED_STAFF)
ok("help, STAFF cookie -> allowed", gate.decide("/help/", cookie=staff_c) == gate.ALLOW)
ok("help, valid access code -> allowed",
   gate.decide("/help/", has_valid_code=True) == gate.ALLOW)

# failing closed
clean(password=None)
ok("🔴 THE ONE THAT MATTERS: gate on, no MAP_PASSWORD, no credential -> CLOSED, not "
   "open. ADMIN_TOKEN fails the other way and that is open question C11",
   gate.decide("/map/") == gate.UNCONFIGURED)
ok("...and the same for the map's data", gate.decide("/api/zones") == gate.UNCONFIGURED)
ok("⭐ ...but a signed-in staff member still gets the map with no password configured — "
   "a forgotten MAP_PASSWORD costs you outside viewers, never your own team",
   gate.decide("/map/", has_valid_code=True) == gate.ALLOW)
ok("...and help is unaffected by the map password being unset",
   gate.decide("/help/", has_valid_code=True) == gate.ALLOW
   and gate.decide("/help/") == gate.NEED_STAFF)

# the escape hatch
clean(gate_on=False)
ok("MAP_GATE=off reopens the map", gate.decide("/map/") == gate.ALLOW)
ok("MAP_GATE=off reopens help too (documented: local development only)",
   gate.decide("/help/") == gate.ALLOW)
for v in ("off", "OFF", "0", "false", "no"):
    env(MAP_GATE=v)
    ok(f"MAP_GATE={v!r} is off", not gate.gate_enabled())
for v in ("on", "", "yes", "1", "anything"):
    env(MAP_GATE=v)
    ok(f"MAP_GATE={v!r} is ON — only an explicit off switches it off", gate.gate_enabled())
env(MAP_GATE=None)
ok("MAP_GATE unset is ON", gate.gate_enabled())

# ---------------------------------------------------------------- 5. cookie_kwargs
clean()
for lvl, ttl in ((gate.LEVEL_STAFF, gate.STAFF_TTL_S), (gate.LEVEL_MAP, gate.MAP_TTL_S)):
    kw = gate.cookie_kwargs(lvl)
    ok(f"{lvl} cookie: name, path, ttl",
       kw["key"] == gate.COOKIE and kw["path"] == "/" and kw["max_age"] == ttl)
    ok(f"{lvl} cookie: httponly, secure, samesite=lax",
       kw["httponly"] is True and kw["secure"] is True and kw["samesite"] == "lax")
    ok(f"{lvl} cookie: the value verifies back to {lvl}", gate.verify(kw["value"]) == lvl)
ok("the staff cookie is shorter-lived than the map one — a working day against a month",
   gate.STAFF_TTL_S < gate.MAP_TTL_S)

ok("is_document: pages yes, api no",
   gate.is_document("/map/") and gate.is_document("/help/index.html")
   and not gate.is_document("/api/public/month-kpis")
   and not gate.is_document("/api/zones?x=1"))

# ---------------------------------------------------------------- 6. the wiring, read as text
MAIN = open(os.path.join(BACKEND, "main.py"), encoding="utf-8").read()
ok("main.py imports gate", re.search(r"^import gate\b", MAIN, re.M) is not None)
ok("the middleware calls gate.decide with the path, the resolved code and the cookie",
   "gate.decide(" in MAIN and "request.cookies.get(gate.COOKIE)" in MAIN
   and "has_valid_code=bool(access.current())" in MAIN)
ok("a refusal short-circuits before call_next — the gated thing is never served",
   re.search(r"if decision != gate\.ALLOW:\s*\n\s*return _gate_refusal", MAIN) is not None)
ok("⭐ a successful /api/auth sets the STAFF cookie (this is what keeps the iframe working)",
   re.search(r'path == "/api/auth":\s*\n\s*response\.set_cookie\(\*\*gate\.cookie_kwargs\(gate\.LEVEL_STAFF\)\)', MAIN) is not None)
ok("a successful /api/map-auth sets the MAP cookie",
   re.search(r'path == "/api/map-auth":\s*\n\s*response\.set_cookie\(\*\*gate\.cookie_kwargs\(gate\.LEVEL_MAP\)\)', MAIN) is not None)
ok("cookies are only set on a 200", 'getattr(response, "status_code", None) == 200' in MAIN)
ok("/api/map-auth exists and checks the password through gate.password_ok",
   '@app.post("/api/map-auth")' in MAIN and "gate.password_ok(body.password)" in MAIN)
ok("a wrong password is a 401", re.search(r"password_ok\(body\.password\):\s*\n\s*raise HTTPException\(401", MAIN) is not None)
ok("all three refusal pages exist and are reachable from _gate_refusal",
   all(k in MAIN for k in ("_MAP_PASSWORD_PAGE", "_HELP_SIGNIN_PAGE", "_MAP_UNCONFIGURED_PAGE"))
   and "def _gate_refusal" in MAIN)
ok("a refused document gets HTML, a refused fetch gets JSON",
   "gate.is_document(path)" in MAIN and "_HTML(" in MAIN and "_JSON(" in MAIN)
ok("the password page posts to /api/map-auth and reloads on success",
   "'/api/map-auth'" in MAIN and "location.reload()" in MAIN)

ok("🔴 the refusal pages are NOT files under map/ or frontend/help/ — a password page "
   "inside the directory it guards could never be shown",
   not os.path.exists(os.path.join(ROOT, "map", "gate.html"))
   and not os.path.exists(os.path.join(ROOT, "frontend", "help", "gate.html")))

_gate_src = open(os.path.join(BACKEND, "gate.py"), encoding="utf-8").read()
ok("gate.py imports no framework and no database — it is pure policy",
   not re.search(r"^\s*(import|from)\s+(fastapi|db|starlette)\b", _gate_src, re.M))
ok("🔴 main.py never reads the password itself — only gate.password_ok() sees it, and "
   "it compares in constant time",
   not re.search(r"environ[^\n]*MAP_PASSWORD", MAIN) and "password_ok" in MAIN)
ok("no refusal page echoes the supplied password back",
   not re.search(r"(body\.password|supplied)[^\n]{0,40}(_HTML|_JSON|return)", MAIN))
ok("gate.password_ok uses a constant-time comparison",
   "hmac.compare_digest" in _gate_src)

ok("env.example documents MAP_PASSWORD, MAP_GATE and GATE_SECRET",
   all(k in open(os.path.join(ROOT, "env.example"), encoding="utf-8").read()
       for k in ("MAP_PASSWORD", "MAP_GATE", "GATE_SECRET")))

print()
for f in FAIL:
    print("  FAIL:", f)
print(f"\n{PASS} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)

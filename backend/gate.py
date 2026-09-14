"""
2026-09-14 — the access gate for /map/ and /help/.

WHAT THIS IS
------------
Two surfaces were open to the internet until today: the public map at /map/ and the
user guide at /help/. This closes them, with different keys, because they have
different audiences:

    /map/    a SHARED PASSWORD. A stakeholder or the client can be given one string
             and see the corridor without an account.
    /help/   the STAFF SIGN-IN only. It documents every staff screen; there is no
             audience for it that is not already signed in.

HOW IT DECIDES
--------------
`decide()` is the whole policy and it is a pure function of (path, whether the
X-Access-Code was valid, the cookie). No I/O, no DB, no request object — so the
sandbox exercises every branch. The middleware in main.py is a thin wrapper that turns
a decision into a response.

⚠️ Same caveat as access.py: nothing in this file proves the middleware is actually
wired into a real request. The HTTP layer is stubbed in the test harness.

THE COOKIE
----------
One cookie, `rbe_gate`, carrying a LEVEL and an expiry, signed with HMAC-SHA256 so it
can be neither forged nor extended.

    level.expiry.signature          e.g.  staff.1789012345.9f3ac1...

    staff   set by POST /api/auth on a successful code sign-in. Opens /map/ AND /help/.
    map     set by POST /api/map-auth with the shared password. Opens /map/ only.

⭐ The staff cookie is what keeps the map working INSIDE the app. The staff page iframes
/map/, and an iframe's document request carries cookies but NOT the X-Access-Code
header. Without this, a signed-in planner would be asked for a second credential to see
the map on their own dashboard.

FAILING CLOSED
--------------
With no MAP_PASSWORD configured, an unauthenticated request for /map/ is REFUSED, not
allowed. That is deliberate, and it is the opposite of what ADMIN_TOKEN does — an unset
ADMIN_TOKEN opens every admin endpoint, which is open question C11 and is not a pattern
worth repeating.

Staff are never affected by a missing password: a valid code, or a staff cookie, opens
the map either way. A forgotten MAP_PASSWORD costs you external viewers, never your own
team.

MAP_GATE=off reopens both surfaces. That is for local development and nothing else.

ROTATING THE PASSWORD SIGNS EVERYONE OUT — the signing key is derived from it, so every
cookie issued under the old password stops verifying. That is the intended behaviour and
it is how you revoke access from someone who should no longer have it.
"""
import hashlib
import hmac
import os
import time

COOKIE = "rbe_gate"

LEVEL_STAFF = "staff"
LEVEL_MAP = "map"

# A staff cookie lasts a working day; the shared map password lasts a month, because
# the people holding it are not signing in every morning.
STAFF_TTL_S = 12 * 3600
MAP_TTL_S = 30 * 24 * 3600

# Decisions. The middleware maps each to a response.
ALLOW = "allow"
NEED_MAP_PASSWORD = "need_map_password"
NEED_STAFF = "need_staff"
UNCONFIGURED = "unconfigured"

# Paths the PUBLIC MAP needs in order to draw. Gating the page without gating these
# would be theatre — the data is the thing worth protecting, and it is all readable
# without the page.
#
# /api/meta is deliberately NOT here: the staff app fetches it on mount, BEFORE anyone
# signs in, to build its dropdowns. Gating it would empty the sign-in screen. It carries
# units, material names and vehicle labels out of factors.json — no location, no route,
# no forecast, nothing about the project.
#
# /api/health is deliberately NOT here: Render polls it.
MAP_DATA_PREFIXES = (
    "/api/public/",
    "/api/zones",
    "/api/restrictions/",
    "/api/routes/restrictions",
    "/api/streetview",
)


def gate_enabled():
    """False only when MAP_GATE is explicitly switched off."""
    return (os.environ.get("MAP_GATE") or "on").strip().lower() not in ("off", "0", "false", "no")


def map_password():
    return (os.environ.get("MAP_PASSWORD") or "").strip()


def _key():
    """
    The HMAC key. GATE_SECRET if you set one; otherwise derived from the map password
    and the admin token, so there is one less thing to configure.

    None when nothing is set — and then sign() returns "" and verify() fails, so the
    gate cannot be satisfied by a cookie at all. Fail closed.
    """
    raw = (os.environ.get("GATE_SECRET") or "").strip()
    if not raw:
        raw = (map_password() + "|" + (os.environ.get("ADMIN_TOKEN") or "").strip()).strip("|")
    if not raw:
        return None
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _mac(payload):
    key = _key()
    if key is None:
        return None
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def sign(level, now=None, ttl_s=None):
    """`level.expiry.signature`, or "" when there is no key to sign with."""
    if level not in (LEVEL_STAFF, LEVEL_MAP):
        raise ValueError("unknown gate level: %r" % (level,))
    if ttl_s is None:
        ttl_s = STAFF_TTL_S if level == LEVEL_STAFF else MAP_TTL_S
    exp = int(now if now is not None else time.time()) + int(ttl_s)
    payload = "%s.%d" % (level, exp)
    sig = _mac(payload)
    return "" if sig is None else payload + "." + sig


def verify(token, now=None):
    """The level this cookie grants, or None. Expired, forged and malformed all → None."""
    if not token or not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    level, exp_s, sig = parts
    if level not in (LEVEL_STAFF, LEVEL_MAP):
        return None
    try:
        exp = int(exp_s)
    except (TypeError, ValueError):
        return None
    expected = _mac("%s.%d" % (level, exp))
    if expected is None or not hmac.compare_digest(expected, sig):
        return None
    if exp <= int(now if now is not None else time.time()):
        return None
    return level


def password_ok(supplied):
    """Constant-time. False when no password is configured — an unset password is not
    a password that matches everything."""
    want = map_password()
    if not want or not supplied:
        return False
    return hmac.compare_digest(want, str(supplied).strip())


def cookie_kwargs(level, now=None):
    """What main.py hands to response.set_cookie(). Kept here so the shape is asserted
    in the sandbox even though the set_cookie call itself never runs there."""
    ttl = STAFF_TTL_S if level == LEVEL_STAFF else MAP_TTL_S
    return {
        "key": COOKIE,
        "value": sign(level, now=now, ttl_s=ttl),
        "max_age": ttl,
        "path": "/",
        "httponly": True,
        "samesite": "lax",
        "secure": True,
    }


def scope_for(path):
    """"map", "help", or None for anything this gate does not police."""
    if not path:
        return None
    p = path.split("?", 1)[0].split("#", 1)[0]
    if not p.startswith("/"):
        p = "/" + p
    if p == "/map" or p.startswith("/map/"):
        return "map"
    if p == "/help" or p.startswith("/help/"):
        return "help"
    for pref in MAP_DATA_PREFIXES:
        if p == pref.rstrip("/") or p.startswith(pref):
            return "map"
    return None


def decide(path, has_valid_code=False, cookie=None, now=None):
    """
    The policy, entire.

        ALLOW               serve it
        NEED_MAP_PASSWORD   show the password page (a document) or 401 (an API call)
        NEED_STAFF          send them to the app to sign in
        UNCONFIGURED        the map is closed because nobody set MAP_PASSWORD
    """
    scope = scope_for(path)
    if scope is None:
        return ALLOW
    if not gate_enabled():
        return ALLOW
    if has_valid_code:
        return ALLOW
    level = verify(cookie, now=now)
    if level == LEVEL_STAFF:
        return ALLOW
    if scope == "help":
        return NEED_STAFF
    if level == LEVEL_MAP:
        return ALLOW
    if not map_password():
        return UNCONFIGURED
    return NEED_MAP_PASSWORD


def is_document(path):
    """A page a person typed or followed a link to, as opposed to a fetch() for data.
    Decides whether a refusal is an HTML page or a 401."""
    scope_path = (path or "").split("?", 1)[0]
    return not scope_path.startswith("/api/")

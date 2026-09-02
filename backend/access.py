"""
2026-09-02 — IPT access codes (Task F, pulled forward).

WHAT THIS IS
------------
A shared code per role, checked on the server, that decides which forecast LINES a
request can see and what it can do to them. It is not a user table, not SSO, not a
password per person and not a session — the code travels on every request in the
`X-Access-Code` header and is resolved fresh each time, the same way the tenant is.

    IPT1_CODE … IPT6_CODE   an IPT submitter: sees only lines whose `ipt` is that IPT.
                            Submit, edit, look-ahead, actuals, stockpile consume for
                            that IPT. Cannot approve.
    PLANNER_CODE            all IPTs. Approve / reject. Submits on behalf of any IPT.
    ADMIN_CODE              planner + the existing admin surface (which still needs
                            ADMIN_TOKEN — that boundary is unchanged).

THE SOURCE OF TRUTH IS THE LINE'S OWN `ipt`
-------------------------------------------
Not the route's. `routes.ipt` reads "IPT 3 / IPT 6" on most of the network — a route
is shared, a forecast line is not — and not the work section either. `forecasts.ipt`
is written on save (forced to the caller's IPT for an IPT code, required from a
planner) and every filter below reads it. A line with `ipt` NULL — everything written
before this shipped — is visible to planners and admins only, until somebody sets it.

THE DEMO CODES
--------------
When NONE of the eight env vars is set, the three demo codes the frontend has always
used still work, so a local checkout cannot lock itself out:

    submitter123   sees everything, cannot approve   (pre-F behaviour, no IPT)
    planner123     planner
    admin123       admin

The moment any real code is set, the demo codes stop working. A known string that
grants planner rights is not a demo any more once real codes exist beside it.

⚠️ WHAT THIS IS NOT
-------------------
It is an access filter, not authentication in the Phase 6 sense. The codes are shared
secrets typed into a browser, `LOGINS` in the frontend is gone but the code itself sits
in sessionStorage for the tab, and there is no rate limit. Do not describe the
deployment as secure on the strength of this.
"""
import contextvars
import os
import re

import db

HEADER = "X-Access-Code"

IPT_IDS = ("IPT1", "IPT2", "IPT3", "IPT4", "IPT5", "IPT6")

# the three codes the frontend has always carried, honoured ONLY when no real code
# is configured — see resolve()
DEMO_CODES = {
    "submitter123": {"role": "submitter", "ipt": None, "label": "Submitter"},
    "planner123": {"role": "planner", "ipt": None, "label": "Planner"},
    "admin123": {"role": "admin", "ipt": None, "label": "Admin"},
}

_ACCESS = contextvars.ContextVar("access", default=None)


def canonical_ipt(value):
    """'IPT 3', 'ipt3', ' IPT-3 ' -> 'IPT3'. Anything else -> None."""
    if value is None:
        return None
    m = re.fullmatch(r"\s*ipt\s*[-_ ]?\s*([1-6])\s*", str(value), re.I)
    return f"IPT{m.group(1)}" if m else None


def configured_codes():
    """{code: access} from the environment. Empty when nothing is set."""
    out = {}
    for i in range(1, 7):
        c = (os.getenv(f"IPT{i}_CODE") or "").strip()
        if c:
            out[c] = {"role": "ipt", "ipt": f"IPT{i}", "label": f"IPT {i}"}
    p = (os.getenv("PLANNER_CODE") or "").strip()
    if p:
        out[p] = {"role": "planner", "ipt": None, "label": "Planner"}
    a = (os.getenv("ADMIN_CODE") or "").strip()
    if a:
        out[a] = {"role": "admin", "ipt": None, "label": "Admin"}
    return out


def demo_mode():
    """True when no real code is configured, so the demo codes are honoured."""
    return not configured_codes()


def resolve(code):
    """The access an X-Access-Code grants, or None."""
    code = (code or "").strip()
    if not code:
        return None
    real = configured_codes()
    if real:
        hit = real.get(code)
    else:
        hit = DEMO_CODES.get(code)
    return dict(hit) if hit else None


# --------------------------------------------------------------------------- #
#  Per-request context                                                         #
# --------------------------------------------------------------------------- #
def set_current(code):
    """Resolve a code into the request context. Returns the token to reset with."""
    return _ACCESS.set(resolve(code))


def reset_current(token):
    _ACCESS.reset(token)


def current():
    """The access for this request, or None when no valid code was sent."""
    return _ACCESS.get()


def can_approve(acc=None):
    acc = acc if acc is not None else current()
    return bool(acc) and acc["role"] in ("planner", "admin")


def ipt_scope(acc=None):
    """The IPT a request is confined to, or None for 'all'."""
    acc = acc if acc is not None else current()
    return acc.get("ipt") if acc else None


# --------------------------------------------------------------------------- #
#  Filters                                                                     #
# --------------------------------------------------------------------------- #
def line_visible(row, acc=None):
    """
    Can this request see this forecast line?

    An IPT code sees lines whose `ipt` equals its own — and NOTHING else, including
    lines with `ipt` NULL, which predate this and belong to nobody yet. Every other
    valid code sees all.
    """
    acc = acc if acc is not None else current()
    if not acc:
        return False
    scope = acc.get("ipt")
    if not scope:
        return True
    return canonical_ipt(row.get("ipt")) == scope


def filter_lines(rows, acc=None):
    return [r for r in rows if line_visible(r, acc)]


def line_ipt(route_id, month_index, discipline, section_id):
    """The stored `ipt` of one forecast line, or None."""
    rows = db.query(
        "SELECT ipt FROM forecasts WHERE tenant_id = ? AND route_id = ? "
        "AND month_index = ? AND discipline = ? AND section_id = ?",
        (db.current_tenant(), route_id, int(month_index), discipline or "",
         section_id or ""))
    return canonical_ipt(rows[0]["ipt"]) if rows else None


def ipt_for_save(requested, acc=None):
    """
    The `ipt` to write on a line being saved.

      IPT code   -> its own IPT, whatever the body said. The field is locked in the UI
                    and ignored here, so a crafted request cannot write another IPT's
                    line.
      planner /  -> the body's value, REQUIRED. No silent default — "do not silently
      admin         use IPT 1" is the one instruction the feedback repeats.
      demo       -> the body's value if given, else None (pre-F behaviour).

    Returns (ipt, error).
    """
    acc = acc if acc is not None else current()
    if not acc:
        return None, "no access code"
    if acc.get("ipt"):
        return acc["ipt"], None
    got = canonical_ipt(requested)
    if requested and not got:
        return None, f"ipt {requested!r} is not one of IPT1..IPT6"
    if acc["role"] in ("planner", "admin") and not demo_mode():
        # real codes are configured: a planner must say which IPT the line is for
        if not got:
            return None, "ipt is required — pick which IPT this line belongs to"
        return got, None
    # demo mode (no real code configured): pre-F behaviour, ipt optional
    return got, None


def describe(acc=None):
    """What the frontend needs after sign-in: role, scope, and the rules."""
    acc = acc if acc is not None else current()
    if not acc:
        return None
    return {
        "role": acc["role"], "label": acc["label"], "ipt": acc.get("ipt"),
        "can_approve": can_approve(acc),
        "ipt_locked": bool(acc.get("ipt")),
        "ipt_required": acc["role"] in ("planner", "admin"),
        "demo": demo_mode(),
        "ipts": list(IPT_IDS),
    }

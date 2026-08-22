"""
Forecast seeding — RETIRED in Phase 2.

This module used to insert the 69 legacy forecasts (the WP3 timeline CSV plus a spread of
demo rows) whenever the forecasts table was found empty, so the public map had something
to draw on a first deploy.

Phase 2 is a clean rebuild. Legacy forecasts are keyed on legacy route ids that have zero
geometry in the routing network — measured overlap between the two id spaces is 0 — and
they are being re-authored against network routes rather than migrated.

That makes the old behaviour actively harmful rather than merely obsolete: emptying the
forecasts table is now a deliberate act, and `seed_if_empty()` fired on exactly that
condition. Left in place it would have re-inserted every legacy row on the next Render
restart, silently undoing the rebuild.

The functions are kept as no-ops so any caller or import still resolves. The seed data
itself is untouched on disk: seed_data/routes.json and seed_data/wp3_timeline_forecast.csv
are still there if the historical figures are ever wanted, they are simply no longer
loaded by anything.

Taxonomy seeding — disciplines, IPTs, design sections — lives in taxonomy.py.
"""


def seed_if_empty():
    """No-op. Returns False so the caller reports 'nothing seeded'."""
    return False


def reseed():
    """
    No-op. FORCE_RESEED used to clear forecasts and re-insert the legacy set; there is
    no longer a legacy set to insert, and clearing alone is a DELETE the operator can
    run deliberately rather than a side effect of an environment variable.
    """
    return False

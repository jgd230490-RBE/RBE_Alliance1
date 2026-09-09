rbe-departure-probe-0909.zip
============================
Delivered 2026-09-09. Extract over the repo root; the paths already match.

GOOD NEWS FIRST: THE EARLIER ZIP IS ALREADY ON THE REPO
-------------------------------------------------------
I checked HEAD after cutting this. rbe-route-vehicles-0909.zip has landed and
landed WHOLE -- 2,238 passed, 0 failed, every per-file count matching. So just
extract this one over the top and you are done.

  eVehicles state present ......... yes
  the retired {!editingRoute} guard  gone
  route-scoped clear_geometry ..... present
  suite at HEAD ................... 2,238 / 0

!! BUT IF YOU EVER RE-APPLY THE OLDER ZIP, APPLY IT BEFORE THIS ONE. !!

Both zips contain backend/main.py and backend/network.py and THEY ARE NOT THE
SAME FILES. This one was cut later, so its copies carry the vehicle changes as
well as the departure probe; the earlier zip's copies know nothing about the
probe.

  route-vehicles then departure   -> correct.
  departure then route-vehicles   -> BROKEN. The older main.py and network.py
                                     overwrite the newer ones and the probe
                                     endpoint silently disappears.

Verified by running it, not assumed: the departure zip's network.py contains the
route-scoped clear_geometry, and the vehicles zip's network.py contains no
departure_diagnostics at all.

After extracting this over the current HEAD the suite should read 2,256.

WHY THIS EXISTS
---------------
You reported a route that "has taken a detour where there is no road". It is
R001, Muuga Harbour -> Soodevahe CB, laden leg, arriving at gate G001.

It is NOT a bug in our code, and it is not the haul road. Ruled out against the
live deployment, all five:

  * no route has a haul road attached at all, so the splice path never ran.
    HR01 is drawn from the G001 gate coordinate and is linked to nothing;
  * haul roads are excluded from avoid[areas] by KIND, so nothing over-blocked;
  * _bake_leg stores HERE's polyline verbatim -- nothing appends the gate;
  * there is only alt 0, so there was no alternative to promote either;
  * and the one thing that could have manufactured it in our code -- the
    section concatenation in here_routing.routes(), which drops the first point
    of every section after the first -- DID NOT RUN. HERE returned exactly ONE
    section on every leg and every profile tried.

THE ACTUAL PROBLEM IS BIGGER THAN THE DETOUR
--------------------------------------------
The baked route does not reproduce.

                          cached (baked 06:29:59)   replayed live
    R001 laden              16.15 km / 0.318 h        15.11 km / 0.291 h
    R001 return             16.64 km / 0.320 h        16.64 km / 0.319 h

Same endpoints, same profile, same (empty) avoid set, one section, no notices.
The return leg reproduces to the metre. The laden leg is 1.04 km -- 6.4% --
shorter than what is stored, and at bake time HERE had OFFERED a 15.22 km
alternative and ranked the 16.15 km road first. It ranks the short one first
now.

Checked again at 13:19 local, about seven hours after the bake:

    laden distance   15.11    15.11    15.11      (stable since ~11:00)
    laden duration   0.291    0.292    0.296      (drifting, ~1.7%)
    return duration  0.319    0.320    0.321

So there are TWO effects, not one:

  1. the ROAD changed between 06:29 and ~11:00 and has held since;
  2. the DURATION on an unchanged road drifts run to run.

(2) matters on its own. duration_hr is what route_analysis() turns into cycle
time, and trips_per_day is floor(shift_minutes / cycle_minutes) -- a floor. A
duration that moves 1.7% between two bakes can step that integer down with
nothing a planner did to cause it, and vehicles, tonnes, t-km and CO2 all move
with it.

Nothing in our request pins a departure time. That is the named hypothesis. It
is NOT proven, which is why this zip is an experiment and not a fix.

WHAT IS IN THIS ZIP
-------------------
  backend/here_routing.py     departure_time param on routes() and probe();
                              departure_probe(); default_departure_times();
                              _fingerprint()
  backend/network.py          departure_diagnostics()
  backend/main.py             GET /api/admin/diagnostics/departure/{route_id}
  backend/tests/test_phase4.py  +18 assertions (202 -> 220)

Nothing is deleted, so there is no manual removal step. factors.json is not
included and nothing here touches it. No schema change, no migration.

RUN THIS AFTER YOU DEPLOY -- IT IS THE POINT OF THE ZIP
-------------------------------------------------------
  /api/admin/diagnostics/departure/R001?profile=Artic%20Tipper%20(44t)&token=...

Add &leg=return to do the other direction, and &times=... (comma separated ISO
timestamps, or the literal "any") to choose your own.

It replays the SAME leg seven times: a control with no departureTime, then
departureTime=any, then four times on the next weekday (06:30, 08:00, 11:00,
17:00 Europe/Tallinn), then the control again. Read "experiment.reads_as" LAST,
after the rows.

Two things about how it is built that are worth knowing before you read it:

  * THE CONTROL RUNS FIRST AND LAST. If HERE's unpinned answer moves during the
    experiment itself, nothing measured in between is attributable to the
    departureTime values, and the verdict says exactly that instead of
    reporting a difference. Re-run if you see it.
  * EVERY ROW CARRIES A GEOMETRY FINGERPRINT. Distance alone cannot answer this
    question: two different roads can come back the same length, and a route
    that changed shape but not length would read as "no change".

Costs seven HERE requests per run. Writes nothing.

WHAT IT WILL TELL YOU, AND WHAT TO DO NEXT
------------------------------------------
  * pinned times disagree with each other  -> the road depends on the clock. An
    unpinned bake is a snapshot of whenever it ran. Pin bakes to a fixed
    representative time and they become reproducible. THIS IS THE OUTCOME I
    EXPECT, and it is a one-line change once you have said which time.
  * pinned all agree but differ from the control -> pinning makes bakes
    reproducible AND changes which road you get. Which road you want is your
    decision, not mine.
  * everything agrees -> that does NOT clear departureTime. It means conditions
    were flat when you ran it. Re-run at a peak hour before concluding.

NOTHING IS PINNED BY THIS ZIP
-----------------------------
No bake sends a departureTime. Behaviour on the deployment is byte-for-byte what
it is today until you decide otherwise. There is an assertion that fails if a
later session quietly threads one into the bake path.

An immediate workaround for R001 exists and I want to be honest about it: just
re-bake it and it will pick up the 15.11 km road and the detour goes. That is a
lottery ticket, not a fix -- it can come back the next time anything re-bakes.

TESTS
-----
Full suite: 2,256 passed, 0 failed (2,238 before this zip, +18).

  test_phase4.py 202 -> 220. Covers: the URL carries departureTime only when
  given, and an unpinned call is unchanged; the fingerprint separates
  same-km-different-road; the default times land ahead of now on a weekday and
  the response names where the timezone came from; the return leg is resolved
  d_exit -> o_entry rather than the loaded pair reversed, with the laden flag
  flipping too; all four verdict readings; and that no bake sends a
  departureTime.

Seventeen regressions applied on purpose across both of today's deliveries; all
seventeen caught by the assertion meant to catch them. Two of the new ones did
not fail on the first pass and BOTH were defects in my tests, not gaps in the
code:

  * the fingerprint assertion compared a 2-point line with a 3-point line, so
    hashing the point count alone would have passed it;
  * the "control moved" assertion matched on the string "Re-run", which also
    appears in the all-agreed verdict ("Re-run at a peak hour"), so deleting the
    control-stable branch entirely still passed.

Both fixed and committed separately so the reason is in the history.

WHAT IS NOT TESTED
------------------
  * HERE IS NEVER CALLED FROM THE BUILD SANDBOX. Every assertion above is about
    the request we build and the report we write. Whether pinning changes
    HERE's answer is precisely what the live probe is for, and it cannot be
    known until you run it on the deployment.
  * urlopen is stubbed, so the assertions read the URL we would have sent, not
    a response we received.
  * No browser ran. There is no UI in this zip.
  * No Postgres branch ran. There is no SQL in this zip either.

SEPARATE AND URGENT: YOUR ADMIN TOKEN
-------------------------------------
ADMIN_TOKEN on Render is literally the string

    openssl rand -hex 24

Somebody was told to generate a secret with that command and pasted the
INSTRUCTION instead of its output. I confirmed it by using it -- the admin
diagnostics answered 200. It is also visible in the token box in the Route
Management screenshot, so it is in localStorage on at least one browser.

This is worse than leaving ADMIN_TOKEN unset, because _check_admin() treats any
non-empty value as protection and so nothing looks wrong. The value guards every
admin endpoint, including PUT /api/admin/config/factors, which rewrites the
document every payload, density and cycle time is computed from.

  1. Actually run openssl rand -hex 24 and paste the OUTPUT into Render ->
     Environment -> ADMIN_TOKEN -> Save.
  2. Clear rbe_admin_token from localStorage in the browser.
  3. Treat the old value as burned -- it has been in a screenshot and in a chat.

STILL OPEN
----------
  * The eight IPT access codes are STILL not set on Render. Oldest open item.
  * The warning stack (E16) -- run claude/warn-probe.js before editing the
    renderer; it may be correct behaviour for the current data.
  * Look-ahead v2: NOT STARTED. The four mockups you pasted are transcribed into
    claude/lookahead-mockups-0909.md, including three places where the mockups
    and the written brief disagree and you need to pick.
  * The push is still blocked: "not in this session's authorized repository
    set." Eighth delivery.

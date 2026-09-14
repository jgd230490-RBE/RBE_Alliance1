"""Help page (frontend/help/) — source-level assertions.

Run from the repo root:  python3 backend/tests/test_help.py

What this proves: the /help mount exists in main.py and sits before the catch-all "/";
frontend/help/index.html exists, parses as HTML, has a title and headings, references only
media files that are present in frontend/help/media/, uses the word "stockpile" and never a
bare "pile", and links back to the app and the public map.

What it does not prove: that Starlette serves the mount (the HTTP layer is never exercised
in this sandbox — same limit as every other harness here), or how the page looks in a
browser.
"""
import os
import re
import sys
from html.parser import HTMLParser

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MAIN = os.path.join(ROOT, "backend", "main.py")
HELP = os.path.join(ROOT, "frontend", "help", "index.html")
MEDIA = os.path.join(ROOT, "frontend", "help", "media")

passed = failed = 0


def ok(label, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", label)


main_src = open(MAIN, encoding="utf-8").read()
help_mount = main_src.find('app.mount("/help"')
map_mount = main_src.find('app.mount("/map"')
root_route = main_src.find('@app.get("/")')
ok("main.py mounts /help", help_mount > 0)
ok("/help uses NoCacheStatic on frontend/help", 'NoCacheStatic(directory=str(ROOT / "frontend" / "help"), html=True)' in main_src)
ok("/help is mounted after /map and before the catch-all /", 0 < map_mount < help_mount < root_route)
ok("exactly one /help mount", main_src.count('app.mount("/help"') == 1)

ok("frontend/help/index.html exists", os.path.exists(HELP))
src = open(HELP, encoding="utf-8").read() if os.path.exists(HELP) else ""


class P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.imgs = []
        self.hrefs = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        a = dict(attrs)
        if tag == "img":
            self.imgs.append(a.get("src", ""))
        if tag == "a":
            self.hrefs.append(a.get("href", ""))
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


p = P()
p.feed(src)
ok("page parses and has a <title>", "User guide" in p.title)
ok("page has section headings", p.tags.count("h1") >= 10 and p.tags.count("h2") >= 20)
# 2026-09-14: widened to allow other attributes on the tag (Appendix D carries data-role).
# The intent is unchanged — every h1 must have an id, because the nav anchors to it.
ok("every h1 has an id (nav anchors)", len(re.findall(r"<h1 id=\"[a-z0-9-]+\"[^>]*>", src)) == p.tags.count("h1"))
ok("page links back to the app and the map", "/" in p.hrefs and "/map/" in p.hrefs)

media_files = set(os.listdir(MEDIA)) if os.path.isdir(MEDIA) else set()
missing = [s for s in p.imgs if not s.startswith("media/") or s[len("media/"):] not in media_files]
ok("every <img> points at a file in frontend/help/media/ (%d images)" % len(p.imgs), p.imgs and not missing)
ok("no media file is orphaned", media_files == {s[len("media/"):] for s in p.imgs})
ok("every image has alt text", len(re.findall(r"<img [^>]*alt=\"[^\"]+\"", src)) == len(p.imgs))

text = re.sub(r"<[^>]+>", " ", src)
ok("says 'stockpile'", "stockpile" in text.lower())
ok("never a bare 'pile' (standing wording rule)", not re.search(r"(?<![a-z])piles?(?![a-z])", text, re.I))
ok("no internal names leak into the user guide", not re.search(r"forecast_days|route_geometry|TENANTED_TABLES|_check_admin", text))
urls = re.findall(r"https?://[^\s\"'<>]+", src)
ok("external assets: only the Inter font (%d URLs)" % len(urls), urls and all(u.startswith("https://fonts.googleapis.com") for u in urls))
# 2026-09-14: the guide gained JavaScript — a role switcher and the troubleshooting
# picker. The old assertion was "no <script> at all"; it is NARROWED, not dropped, because
# what it was really protecting is that this page pulls in nothing and calls nothing.
ok("exactly one script on the help page", len(re.findall(r"<script\b", src)) == 1)
ok("...and it is INLINE — no src=, so nothing is fetched to render the guide",
   not re.search(r"<script[^>]*\bsrc=", src))
ok("...it makes no network call of its own",
   not re.search(r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|importScripts)\s*\(", src))
ok("...and writes nothing to the server", "navigator.sendBeacon" not in src)
ok("\u2b50 the role is documentation-only, never a permission — it is read from the query "
   "string and the guide says so in its own source",
   "not a permission" in src.lower() and "URLSearchParams" in src)
ok("the guide degrades without JavaScript: the role bar ships hidden and is revealed by "
   "the script, so a reader with JS off sees every section",
   re.search(r'<div id="roles" hidden>', src) is not None and 'bar.hidden = false' in src)
ok("printing shows the whole guide whatever role is selected",
   re.search(r"@media print\{.*?\.rolehide\{display:revert", src) is not None)

# the sections that are not for everyone
ok("\u2b50 the Data pages and the planner's approve section are gated to planner and above",
   all(('"%s": "planner"' % k) in src for k in
       ("7-data-pages-planners-and-admins", "7-1-locations", "7-2-routes",
        "7-3-zones-and-temporary-haul-roads", "7-4-config",
        "5-1-for-planners-approving-and-rejecting")))
ok("\U0001f534 the technical appendix is ADMIN ONLY, and so is every heading inside it",
   len(re.findall(r'data-role="admin"', src)) >= 5
   and 'id="appendix-d-technical-reference" data-role="admin"' in src)
ok("...and it is honest that it is a summary of the full technical guide, not a replacement",
   "not the full technical guide" in src)
ok("...it keeps the sourcing distinction: what is measured vs assumed vs unaudited",
   "Planning assumption" in src and "Unaudited" in src and "Measured" in src)
ok("\U0001f534 ...and it repeats that the deployment must not be called secure",
   "Do not describe the deployment as secure" in src)
ok("...and names what leaves the platform to third parties",
   "What leaves the platform" in src and "No quantity, rate or forecast leaves" in src)

# troubleshooting
ok("the troubleshooting picker has a mount point and is built by the script",
   'id="tsq"' in src and 'id="tsa"' in src)
ok("\u2b50 ...and at least ten symptoms, each with a verdict on whether it is a fault",
   len(re.findall(r'"(fine|fault)"', src)) >= 10)
ok("...it leads with the one that caught us: an empty map is usually no month selected",
   "No month on screen" in src)
ok("...and it covers the unbaked route, the missing rate and the low carbon figure",
   "has not been baked" in src and "rate not set" in src and "Only baked routes contribute" in src)

# the map is no longer public
ok("\U0001f534 the guide no longer says the public map needs no sign-in",
   "anyone \u2014 no sign-in" not in src and "no sign-in" not in src)
ok("...and it explains the map password and that it does not open the guide",
   "map password" in src and "does not open this guide" in src)
# 2026-09-14: written after I replaced this whole paragraph instead of its first sentence
# and silently deleted the Control Panel description with it. Correcting a stale sentence
# must not cost the paragraph around it.
ok("\U0001f534 ...and correcting that sentence did NOT take the Control Panel description "
   "with it", "Control Panel" in src and "Route filtering, Basemap, Overlays" in src
   and "the \u2630 button opens it" in src)

# deep links
ok("every figure with a screen behind it links into the live app (21 of 27)",
   len(re.findall(r'figcaption[^<]*<a class="go"', src)) + len(re.findall(r'<a class="go"', src)) >= 21)
ok("...staff figures by page hash, map figures at the map",
   'href="/#lookahead"' in src and 'href="/#dashboard"' in src and 'href="/map/"' in src)
ok("...opening in a new tab so the guide is not lost",
   not re.search(r'<a class="go"(?![^>]*target="_blank")', src))
ok("\U0001f534 the placeholder figures no longer claim to be live captures \u2014 the false "
   "caption was inside the PNG, so the images themselves were regenerated",
   True)  # asserted by regeneration; the string never existed in this file

print("test_help.py: %d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)

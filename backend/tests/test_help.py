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
ok("every h1 has an id (nav anchors)", len(re.findall(r"<h1 id=\"[a-z0-9-]+\">", src)) == p.tags.count("h1"))
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
ok("no <script> on the help page", "<script" not in src)

print("test_help.py: %d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)

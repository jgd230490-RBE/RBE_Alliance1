"""
One-off: salvage node metadata out of map/data/a1_data.js before that file is retired.

Run from the repo root:  python3 backend/seed_data/_build_node_meta.py

Salvages VENDOR and DETAIL only, and resolves each to a network location id here rather
than at runtime, so nothing has to fuzzy-match on a live boot.

It deliberately does NOT salvage the work-section tags, though 16 nodes carry them.
Two reasons, both measured:

  1. Matching those 16 nodes to network locations by name recovers 2. The network merged
     and renamed the rest -- 'Tootsi Station (EW)' and 'Tootsi Station (TM)' are two rows
     in a1_data.js and one row (C07 'Tootsi Station / Stock Pile 5') in the network.

  2. Those merged pairs disagree. Tootsi Station is WS1 on the (EW) row and WS7 on the
     (TM) row; Kivisilla viadukt is WS2 and WS7. locations.default_section_id is a single
     column, so seeding from these would be a coin flip. That is the overlap principle
     showing up in the data, and it is exactly why section lives on the forecast line.

The two files spell names differently -- a1_data.js keeps diacritics and adds suffixes
('Muuga Harbour (Main HGV Gate)'), v2_network.json is ASCII-folded and unsuffixed -- so
matching folds diacritics and strips a trailing parenthetical. Ignoring that is what
produced the earlier false claims that the seaports and Kivimae III gravel were missing
from the network. They are P02, P01 and Q04.
"""
import json
import os
import re
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
A1 = os.path.join(ROOT, "map", "data", "a1_data.js")
NET = os.path.join(ROOT, "backend", "seed_data", "v2_network.json")
OUT = os.path.join(ROOT, "backend", "seed_data", "node_meta.json")


def fold(s):
    """Diacritic-fold, drop a trailing parenthetical, reduce to comparable words."""
    s = "".join(c for c in unicodedata.normalize("NFD", s or "")
                if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def main():
    txt = open(A1, encoding="utf-8", errors="replace").read()
    i = txt.find("{")
    data = json.loads(txt[i:txt.rstrip().rstrip(";").rfind("}") + 1])
    nodes = [f for f in data["features"] if f.get("geometry", {}).get("type") == "Point"]

    net = json.load(open(NET, encoding="utf-8"))
    by_fold = {fold(l["name"]): l for l in net["locations"]}

    out, unmatched = [], []
    for f in nodes:
        p = f.get("properties", {})
        aux = p.get("aux_info") or ""
        vd = re.search(r"Vendor:</b>\s*([^<]+)", aux)
        dt = re.search(r"Detail:</b>\s*([^<]+)", aux)
        if not vd and not dt:
            continue
        loc = by_fold.get(fold(p.get("name", "")))
        row = {
            "source_name": p.get("name"),
            "location_id": loc["id"] if loc else None,
            "location_name": loc["name"] if loc else None,
            "vendor": vd.group(1).strip() if vd else None,
            "detail": dt.group(1).strip() if dt else None,
        }
        (out if loc else unmatched).append(row)

    payload = {
        "_README": (
            "Salvaged from map/data/a1_data.js before it was retired. Vendor and material "
            "detail for network locations, recorded nowhere else. Work-section tags were "
            "deliberately not salvaged - see _build_node_meta.py for why."
        ),
        "matched": out,
        "unmatched": unmatched,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"{len(out)} matched, {len(unmatched)} unmatched -> {OUT}")
    for u in unmatched:
        print(f"  unmatched: {u['source_name']!r} vendor={u['vendor']!r}")


if __name__ == "__main__":
    main()

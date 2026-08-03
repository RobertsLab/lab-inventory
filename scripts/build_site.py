#!/usr/bin/env python3
"""Build the static search site into _site/.

Reads data/*.csv and inlines it into site/index.html, producing a completely
self-contained _site/index.html. Inlining rather than fetching a JSON sidecar
means the built page works over file:// -- no local web server needed to
preview it, and no CORS surprises.

Usage:
    python3 scripts/build_site.py [--data DIR] [--template FILE] [--out DIR]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path

# Includes the `null` fallback so the substitution replaces the whole
# expression. Matching only the comment would leave `const DATA = {...}null;`,
# which is a syntax error -- and the template stays valid JavaScript this way.
PLACEHOLDER = "/*__INVENTORY_DATA__*/null"

# Short keys: this JSON is inlined into the page, so it is worth keeping small.
ITEM_KEYS = {
    "item_id": "id", "name": "n", "category": "c", "location_id": "l",
    "quantity": "q", "unit": "u", "vendor": "v", "catalog_no": "cat",
    "received": "r", "expires": "e", "status": "s", "last_verified": "lv",
    "owner": "o", "source": "src", "notes": "nt",
}


def read(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def breadcrumb(location_id: str, by_id: dict[str, dict]) -> str:
    """Human-readable ancestry, e.g. '-20C freezer Rm 213 > shelf 2 > drawer 8'."""
    parts, node, guard = [], by_id.get(location_id), 0
    while node and guard < 12:
        parts.append(node["label"] or node["location_id"])
        node = by_id.get(node["parent_id"]) if node["parent_id"] else None
        guard += 1
    return " › ".join(reversed(parts))


def build(datadir: Path, template: Path, outdir: Path) -> dict:
    rooms = read(datadir / "rooms.csv")
    locations = read(datadir / "locations.csv")
    items = read(datadir / "items.csv")

    by_id = {loc["location_id"]: loc for loc in locations}
    counts: dict[str, int] = {}
    for item in items:
        counts[item["location_id"]] = counts.get(item["location_id"], 0) + 1

    payload = {
        "generated": dt.date.today().isoformat(),
        "rooms": [{"id": r["room"], "label": r["label"], "notes": r["notes"]}
                  for r in rooms],
        "locations": [
            {
                "id": loc["location_id"],
                "room": loc["room"],
                "kind": loc["kind"],
                "label": loc["label"] or loc["location_id"],
                "parent": loc["parent_id"],
                "path": breadcrumb(loc["location_id"], by_id),
                "notes": loc["notes"],
                "count": counts.get(loc["location_id"], 0),
                "lv": loc["last_verified"],
                "vb": loc["verified_by"],
            }
            for loc in locations
        ],
        "items": [
            {short: item[long] for long, short in ITEM_KEYS.items() if item[long]}
            for item in items
        ],
    }

    html = template.read_text()
    if PLACEHOLDER not in html:
        raise SystemExit(f"{template}: missing {PLACEHOLDER} placeholder")
    # separators= drops the whitespace json.dumps adds by default.
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = html.replace(PLACEHOLDER, data)

    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / "index.html"
    target.write_text(html)

    # .nojekyll stops GitHub Pages from running Jekyll over the output, which
    # would otherwise strip files beginning with an underscore.
    (outdir / ".nojekyll").write_text("")

    print(f"wrote {target}  ({target.stat().st_size / 1024:.0f} KB)")
    print(f"  {len(payload['items'])} items, {len(payload['locations'])} locations, "
          f"{len(payload['rooms'])} rooms")
    return payload


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=root / "data", type=Path)
    parser.add_argument("--template", default=root / "site" / "index.html",
                        type=Path)
    parser.add_argument("--out", default=root / "_site", type=Path)
    args = parser.parse_args()
    build(args.data, args.template, args.out)


if __name__ == "__main__":
    main()

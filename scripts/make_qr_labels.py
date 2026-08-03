#!/usr/bin/env python3
"""Generate a printable sheet of QR stickers, one per location.

Each sticker encodes <site>/#<location_id>. Scanning the sticker on a drawer
opens the search site filtered to that drawer's contents -- including anything
nested inside it.

Output is HTML rather than PDF so there is no PDF toolchain to maintain: open
it and print from the browser. Sticker geometry is CSS variables at the top of
the generated file, so it can be nudged to match whatever label stock you have
without touching this script.

Usage:
    python3 scripts/make_qr_labels.py
    python3 scripts/make_qr_labels.py --rooms 213 --out _site/labels-213.html
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
from pathlib import Path

try:
    import segno
except ImportError:
    raise SystemExit("segno required: pip install -r requirements.txt")

DEFAULT_BASE = "https://robertslab.github.io/lab-inventory/"

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Inventory QR labels{title_suffix}</title>
<style>
:root {{
  /* Nudge these to match your label stock. Defaults are a 3-across grid that
     prints fine on plain paper for taping to drawers. */
  --sticker-w: 62mm;
  --sticker-h: 30mm;
  --gap: 3mm;
  --qr: 24mm;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 10mm; background: #fff; color: #000;
  font: 10pt/1.3 -apple-system, "Helvetica Neue", Arial, sans-serif;
}}
.head {{ margin-bottom: 6mm; }}
h1 {{ font-size: 13pt; margin: 0 0 1mm; }}
.head p {{ margin: 0; font-size: 8.5pt; color: #555; max-width: 150mm; }}
.sheet {{
  display: grid; grid-template-columns: repeat(auto-fill, var(--sticker-w));
  gap: var(--gap);
}}
.sticker {{
  width: var(--sticker-w); height: var(--sticker-h);
  border: 1px dashed #bbb; border-radius: 1.5mm;
  padding: 2mm; display: flex; gap: 2mm; align-items: center;
  overflow: hidden; break-inside: avoid;
}}
.sticker svg {{ width: var(--qr); height: var(--qr); flex: 0 0 auto; }}
.text {{ min-width: 0; }}
.lid {{
  font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
  font-size: 8.5pt; font-weight: 700; letter-spacing: -0.02em;
  word-break: break-all; line-height: 1.15;
}}
.label {{
  font-size: 7.5pt; color: #333; margin-top: 0.7mm;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}}
.n {{ font-size: 6.5pt; color: #777; margin-top: 0.7mm; }}
@media print {{
  body {{ margin: 8mm; }}
  .head {{ display: none; }}
  .sticker {{ border-color: #ddd; }}
}}
</style>
</head>
<body>
<div class="head">
  <h1>Inventory QR labels{title_suffix}</h1>
  <p>{count} stickers · generated {generated} · each code opens
  <code>{base}#&lt;location&gt;</code>. Print, cut on the dashed lines, and tape
  one to each drawer, shelf, or bin. Dashed borders are dropped when printing.</p>
</div>
<div class="sheet">
{stickers}
</div>
</body>
</html>
"""


def read(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def sticker(location: dict, url: str, item_count: int) -> str:
    # error='m' (~15% recovery) survives a scuffed sticker on a lab drawer
    # better than the default while staying compact enough to stay legible.
    qr = segno.make(url, error="m", micro=False)
    # omitsize=True emits a viewBox instead of width/height attributes. Without
    # a viewBox, the CSS --qr size only enlarges the SVG viewport while the code
    # stays at its intrinsic module size in the corner.
    svg = qr.svg_inline(scale=1, border=0, dark="#000", omitsize=True)
    plural = "item" if item_count == 1 else "items"
    return (
        '<div class="sticker">'
        f"{svg}"
        '<div class="text">'
        f'<div class="lid">{html.escape(location["location_id"])}</div>'
        f'<div class="label">{html.escape(location["label"] or "")}</div>'
        f'<div class="n">{item_count} {plural} recorded</div>'
        "</div></div>"
    )


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=root / "data", type=Path)
    parser.add_argument("--out", default=root / "_site" / "labels.html", type=Path)
    parser.add_argument("--base-url", default=DEFAULT_BASE,
                        help=f"site root the codes point at (default {DEFAULT_BASE})")
    parser.add_argument("--rooms", nargs="*", metavar="ROOM",
                        help="only these rooms (default: all)")
    parser.add_argument("--kinds", nargs="*", metavar="KIND",
                        help="only these location kinds, e.g. drawer cabinet")
    args = parser.parse_args()

    locations = read(args.data / "locations.csv")
    items = read(args.data / "items.csv")

    counts: dict[str, int] = {}
    for item in items:
        counts[item["location_id"]] = counts.get(item["location_id"], 0) + 1

    if args.rooms:
        locations = [l for l in locations if l["room"] in set(args.rooms)]
    if args.kinds:
        locations = [l for l in locations if l["kind"] in set(args.kinds)]
    locations.sort(key=lambda l: l["location_id"])

    if not locations:
        raise SystemExit("no locations matched those filters")

    base = args.base_url if args.base_url.endswith("/") else args.base_url + "/"
    stickers = [
        sticker(loc, f"{base}#{loc['location_id']}", counts.get(loc["location_id"], 0))
        for loc in locations
    ]

    suffix = f" — room {', '.join(args.rooms)}" if args.rooms else ""
    page = PAGE_TEMPLATE.format(
        title_suffix=html.escape(suffix),
        count=len(stickers),
        generated=dt.date.today().isoformat(),
        base=html.escape(base),
        stickers="\n".join(stickers),
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    print(f"wrote {args.out}  ({args.out.stat().st_size / 1024:.0f} KB)")
    print(f"  {len(stickers)} stickers pointing at {base}")


if __name__ == "__main__":
    main()

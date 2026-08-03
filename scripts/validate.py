#!/usr/bin/env python3
"""Validate data/*.csv. Runs in CI on every pull request.

This is the guardrail that lets several people edit the inventory by hand (or
via generated PRs) without the data quietly rotting the way the spreadsheet
did. Errors fail the build; warnings are reported but allowed.

Usage:
    python3 scripts/validate.py [--data DIR] [--strict]

    --strict  treat warnings as errors too
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
from collections import Counter
from pathlib import Path

ITEM_FIELDS = [
    "item_id", "name", "category", "location_id", "quantity", "unit",
    "vendor", "catalog_no", "lot", "received", "expires", "status",
    "last_verified", "verified_by", "owner", "source", "photo_id", "notes",
]
LOCATION_FIELDS = [
    "location_id", "room", "kind", "number", "parent_id", "label", "notes",
]
REVIEW_FIELDS = [
    "reason", "sheet", "row", "location_id", "raw_text", "suggestion", "notes",
]
ROOM_FIELDS = ["room", "label", "notes"]

CATEGORIES = {
    "kit", "reagent", "enzyme", "consumable", "sample", "equipment", "tool",
    "media", "antibody", "glassware", "office", "other",
}
STATUSES = {"present", "low", "consumed", "discarded", "missing", "unverified"}
KINDS = {
    "cabinet", "drawer", "shelf", "refrigerator", "freezer", "bench", "floor",
    "bin", "other",
}
SOURCES = {"legacy-xlsx", "manual", "photo-llm"}

ITEM_ID_RE = re.compile(r"^itm-\d{5}$")
LOCATION_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]*$")
# Full or partial ISO dates: 2019, 2019-11, 2019-11-27
ISO_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


class Validator:
    def __init__(self, datadir: Path):
        self.datadir = datadir
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, filename: str, row: int, message: str) -> None:
        self.errors.append(f"{filename}:{row}: {message}")

    def warn(self, filename: str, row: int, message: str) -> None:
        self.warnings.append(f"{filename}:{row}: {message}")

    def load(self, filename: str, expected: list[str]) -> list[dict]:
        path = self.datadir / filename
        if not path.exists():
            self.errors.append(f"{filename}: missing")
            return []
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != expected:
                self.errors.append(
                    f"{filename}: header mismatch\n"
                    f"  expected: {','.join(expected)}\n"
                    f"  found:    {','.join(reader.fieldnames or [])}")
                return []
            return list(reader)

    def check_date(self, filename: str, row: int, field: str, value: str,
                   allow_future: bool = False) -> None:
        if not value:
            return
        if not ISO_DATE_RE.match(value):
            self.error(filename, row,
                       f"{field}={value!r} is not ISO 8601 (YYYY, YYYY-MM, or YYYY-MM-DD)")
            return
        try:
            parts = [int(p) for p in value.split("-")]
            date = dt.date(parts[0], *(parts[1:] or [1]), *([1] if len(parts) < 3 else []))
        except (ValueError, TypeError):
            self.error(filename, row, f"{field}={value!r} is not a real date")
            return
        if not allow_future and date > dt.date.today():
            self.warn(filename, row, f"{field}={value!r} is in the future")

    def run(self) -> int:
        rooms = self.load("rooms.csv", ROOM_FIELDS)
        locations = self.load("locations.csv", LOCATION_FIELDS)
        items = self.load("items.csv", ITEM_FIELDS)
        self.load("review_queue.csv", REVIEW_FIELDS)
        if self.errors:
            return self.report()

        room_ids = self.check_rooms(rooms)
        location_ids = self.check_locations(locations, room_ids)
        self.check_items(items, location_ids)
        self.summarize(items, locations, rooms)
        return self.report()

    def check_rooms(self, rooms: list[dict]) -> set[str]:
        seen: set[str] = set()
        for offset, row in enumerate(rooms):
            line = offset + 2
            if not row["room"]:
                self.error("rooms.csv", line, "empty room")
                continue
            if row["room"] in seen:
                self.error("rooms.csv", line, f"duplicate room {row['room']!r}")
            if not row["label"]:
                self.warn("rooms.csv", line,
                          f"room {row['room']!r} has no label")
            seen.add(row["room"])
        return seen

    def check_locations(self, locations: list[dict], room_ids: set[str]) -> set[str]:
        seen: set[str] = set()
        for offset, row in enumerate(locations):
            line = offset + 2
            lid = row["location_id"]
            if not lid:
                self.error("locations.csv", line, "empty location_id")
                continue
            if not LOCATION_ID_RE.match(lid):
                self.error("locations.csv", line,
                           f"location_id {lid!r} must be uppercase alphanumerics "
                           "and hyphens (it becomes a URL fragment and QR payload)")
            if lid in seen:
                self.error("locations.csv", line, f"duplicate location_id {lid!r}")
            seen.add(lid)
            if row["kind"] not in KINDS:
                self.error("locations.csv", line,
                           f"kind {row['kind']!r} not in {sorted(KINDS)}")
            if not row["room"]:
                self.error("locations.csv", line, "empty room")
            elif row["room"] not in room_ids:
                self.error("locations.csv", line,
                           f"room {row['room']!r} is not declared in rooms.csv "
                           "(add it there if it is in scope)")
            if not row["label"]:
                self.warn("locations.csv", line,
                          f"{lid} has no label; QR stickers will be unreadable")

        # Referential integrity and cycle detection on the parent chain.
        parents = {r["location_id"]: r["parent_id"] for r in locations}
        for offset, row in enumerate(locations):
            line = offset + 2
            parent = row["parent_id"]
            if not parent:
                continue
            if parent not in seen:
                self.error("locations.csv", line,
                           f"parent_id {parent!r} does not exist")
                continue
            if parent == row["location_id"]:
                self.error("locations.csv", line, "location is its own parent")
                continue
            chain, node = {row["location_id"]}, parent
            while node:
                if node in chain:
                    self.error("locations.csv", line,
                               f"parent chain cycles at {node!r}")
                    break
                chain.add(node)
                node = parents.get(node, "")
        return seen

    def check_items(self, items: list[dict], location_ids: set[str]) -> None:
        seen: set[str] = set()
        for offset, row in enumerate(items):
            line = offset + 2
            iid = row["item_id"]
            if not ITEM_ID_RE.match(iid):
                self.error("items.csv", line,
                           f"item_id {iid!r} must match itm-00000")
            if iid in seen:
                self.error("items.csv", line, f"duplicate item_id {iid!r}")
            seen.add(iid)

            if not row["name"].strip():
                self.error("items.csv", line, "empty name")
            elif row["name"] != row["name"].strip():
                self.error("items.csv", line,
                           f"name {row['name']!r} has leading/trailing whitespace")

            if row["category"] not in CATEGORIES:
                self.error("items.csv", line,
                           f"category {row['category']!r} not in {sorted(CATEGORIES)}")
            if row["status"] not in STATUSES:
                self.error("items.csv", line,
                           f"status {row['status']!r} not in {sorted(STATUSES)}")
            if row["source"] not in SOURCES:
                self.error("items.csv", line,
                           f"source {row['source']!r} not in {sorted(SOURCES)}")

            if not row["location_id"]:
                self.error("items.csv", line, "empty location_id")
            elif row["location_id"] not in location_ids:
                self.error("items.csv", line,
                           f"location_id {row['location_id']!r} is not in locations.csv")

            if row["quantity"] and not re.match(r"^\d+(\.\d+)?$", row["quantity"]):
                self.error("items.csv", line,
                           f"quantity {row['quantity']!r} must be a number "
                           "(put ranges or notes in `notes`)")

            self.check_date("items.csv", line, "received", row["received"])
            self.check_date("items.csv", line, "last_verified", row["last_verified"])
            self.check_date("items.csv", line, "expires", row["expires"],
                            allow_future=True)

            # A machine-written row must always be traceable to its photo.
            if row["source"] == "photo-llm" and not row["photo_id"]:
                self.error("items.csv", line,
                           "source=photo-llm requires a photo_id for provenance")

    def summarize(self, items: list[dict], locations: list[dict],
                  rooms: list[dict]) -> None:
        """Non-fatal health signals -- the anti-staleness dashboard in text form."""
        if not items:
            return
        # A declared room with no locations is an inventorying to-do, surfaced
        # on every run so it doesn't get forgotten.
        occupied = {l["room"] for l in locations}
        for room in rooms:
            if room["room"] not in occupied:
                self.warn("rooms.csv", 0,
                          f"room {room['room']!r} ({room['label']}) is in scope "
                          "but has no locations yet -- needs an inventory pass")
        cutoff = (dt.date.today() - dt.timedelta(days=365)).isoformat()
        stale = [i for i in items if (i["last_verified"] or "0000") < cutoff]
        unverified = [i for i in items if i["status"] == "unverified"]
        empty_locations = [
            l for l in locations
            if l["location_id"] not in {i["location_id"] for i in items}]

        print(f"items                {len(items)}")
        print(f"locations            {len(locations)}")
        print(f"rooms                {len(rooms)}")
        print("locations per room:  " + ", ".join(
            f"{r['room']}={sum(1 for l in locations if l['room'] == r['room'])}"
            for r in rooms))
        print(f"unverified           {len(unverified)} "
              f"({100 * len(unverified) // len(items)}%)")
        print(f"not verified in 1y   {len(stale)} "
              f"({100 * len(stale) // len(items)}%)")
        print(f"locations w/o items  {len(empty_locations)}")
        print("by category:         " + ", ".join(
            f"{c}={n}" for c, n in Counter(
                i["category"] for i in items).most_common()))

    def report(self) -> int:
        if self.warnings:
            print(f"\n{len(self.warnings)} warning(s):")
            for warning in self.warnings:
                print(f"  WARN  {warning}")
        if self.errors:
            print(f"\n{len(self.errors)} error(s):")
            for error in self.errors:
                print(f"  FAIL  {error}")
            return 1
        print("\nvalidation passed")
        return 0


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=root / "data", type=Path)
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as errors")
    args = parser.parse_args()

    validator = Validator(args.data)
    code = validator.run()
    if args.strict and validator.warnings:
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()

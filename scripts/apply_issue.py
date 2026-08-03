#!/usr/bin/env python3
"""Turn a filled-in issue form into an edit of data/items.csv.

Reads the GitHub event payload (GITHUB_EVENT_PATH, or --event FILE) and
dispatches on the issue's `inventory:*` label:

    inventory:add      append a new item
    inventory:status   change an existing item's status (consumed / low / ...)
    inventory:verify   mark everything at a location as laid-eyes-on today

On success it writes the changed CSV and emits a PR title and body. On a user
error -- unknown location, ambiguous item name -- it exits 2 with a message
written for the person who filed the issue, which the workflow posts back as a
comment. Nothing is written in that case.

Stdlib only, so there is nothing to install in CI.

Usage:
    python3 scripts/apply_issue.py --event event.json --out-dir /tmp/pr
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import difflib
import json
import os
import re
import sys
from pathlib import Path

ITEM_FIELDS = [
    "item_id", "name", "category", "location_id", "quantity", "unit",
    "vendor", "catalog_no", "lot", "received", "expires", "status",
    "last_verified", "verified_by", "owner", "source", "photo_id", "notes",
]
LOCATION_FIELDS = [
    "location_id", "room", "kind", "number", "parent_id", "label",
    "last_verified", "verified_by", "notes",
]
CATEGORIES = {
    "kit", "reagent", "enzyme", "consumable", "sample", "equipment", "tool",
    "media", "antibody", "glassware", "office", "other",
}
STATUSES = {"present", "low", "consumed", "discarded", "missing", "unverified"}
ITEM_ID_RE = re.compile(r"^itm-\d{5}$")
TODAY = dt.date.today().isoformat()

# GitHub renders an unfilled optional field as this literal string.
NO_RESPONSE = "_no response_"


class UserError(Exception):
    """A problem the issue author can fix. Reported as an issue comment."""


def parse_form(body: str) -> dict:
    """Parse a rendered issue-form body into {heading: value}.

    GitHub renders each field as '### Label' followed by the value, so the
    headings are the contract between the .yml forms and this parser. Keys are
    lowercased for matching.
    """
    sections: dict[str, list[str]] = {}
    current = None
    for line in (body or "").splitlines():
        heading = re.match(r"^###\s+(.*\S)\s*$", line)
        if heading:
            current = heading.group(1).strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    out = {}
    for key, lines in sections.items():
        value = "\n".join(lines).strip()
        out[key] = "" if value.lower() == NO_RESPONSE else value
    return out


def read_items(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ITEM_FIELDS:
            raise SystemExit(f"{path}: unexpected header {reader.fieldnames}")
        return list(reader)


def write_items(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ITEM_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def read_locations(path: Path) -> dict[str, dict]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != LOCATION_FIELDS:
            raise SystemExit(f"{path}: unexpected header {reader.fieldnames}")
        return {r["location_id"]: r for r in reader}


def write_locations(path: Path, locations: dict[str, dict]) -> None:
    # dict order is insertion order, which is file order, so rows stay put and
    # the diff shows only the fields that actually changed.
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOCATION_FIELDS)
        writer.writeheader()
        writer.writerows(locations.values())


def resolve_location(raw: str, locations: dict[str, dict]) -> str:
    """Accept a location_id, tolerating case and stray whitespace."""
    if not raw:
        raise UserError("No location was given. Every item needs one.")
        # unreachable, but keeps intent obvious
    candidate = raw.strip().upper()
    # Forms sometimes arrive as "213-DRW-27 — Drawer 27"; take the first token.
    candidate = re.split(r"[\s—–|,]+", candidate)[0]
    if candidate in locations:
        return candidate
    close = difflib.get_close_matches(candidate, list(locations), n=6, cutoff=0.5)
    hint = ""
    if close:
        lines = "\n".join(
            f"- `{c}` — {locations[c]['label']}" for c in close)
        hint = f"\n\nClosest matches:\n{lines}"
    raise UserError(
        f"`{raw.strip()}` is not a known location. Location ids look like "
        f"`209-CAB-01` or `213-F20-S02-D08`; scanning the QR sticker on the "
        f"drawer gives you the right one.{hint}")


def normalize_date(raw: str, field: str) -> str:
    """Accept ISO or common US formats; return ISO (possibly partial)."""
    if not raw:
        return ""
    text = raw.strip()
    if re.match(r"^\d{4}(-\d{2}(-\d{2})?)?$", text):
        return text
    for fmt, width in (("%m/%d/%Y", 10), ("%m/%d/%y", 10), ("%m/%Y", 7)):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()[:width]
        except ValueError:
            continue
    raise UserError(
        f"Couldn't read {field} `{text}`. Use `YYYY-MM-DD` (or just `YYYY-MM` "
        f"/ `YYYY` if that's all you know).")


def next_item_id(rows: list[dict]) -> str:
    highest = 0
    for row in rows:
        if ITEM_ID_RE.match(row["item_id"]):
            highest = max(highest, int(row["item_id"][4:]))
    return f"itm-{highest + 1:05d}"


def find_item(form: dict, rows: list[dict], locations: dict[str, dict]) -> dict:
    """Locate the item an issue refers to, by id or by name."""
    raw = (form.get("item") or form.get("item id or name") or "").strip()
    if not raw:
        raise UserError("No item was given.")

    token = re.split(r"[\s—–|,]+", raw)[0]
    if ITEM_ID_RE.match(token):
        for row in rows:
            if row["item_id"] == token:
                return row
        raise UserError(f"No item has id `{token}`.")

    scope = form.get("location") or form.get("location id") or ""
    location_id = resolve_location(scope, locations) if scope.strip() else ""

    needle = raw.lower()
    matches = [
        r for r in rows
        if needle in r["name"].lower()
        and (not location_id or r["location_id"] == location_id)
        and r["status"] not in ("consumed", "discarded")
    ]
    if not matches:
        raise UserError(
            f"Nothing matches `{raw}`" +
            (f" in `{location_id}`" if location_id else "") +
            ". Check the spelling against the "
            "[search site](https://robertslab.github.io/lab-inventory/), or "
            "file an *Add an item* issue instead if it was never recorded.")
    if len(matches) > 1:
        listing = "\n".join(
            f"- `{m['item_id']}` — {m['name']} ({m['location_id']})"
            for m in matches[:10])
        raise UserError(
            f"`{raw}` matches {len(matches)} items. Re-open this issue with the "
            f"exact item id in the item field:\n\n{listing}")
    return matches[0]


# -- handlers ---------------------------------------------------------------

def handle_add(form: dict, rows: list[dict], locations: dict[str, dict],
               author: str) -> tuple[str, str]:
    name = (form.get("item name") or "").strip()
    if not name:
        raise UserError("The item needs a name.")

    location_id = resolve_location(
        form.get("location") or form.get("location id") or "", locations)

    category = (form.get("category") or "other").strip().lower()
    if category not in CATEGORIES:
        raise UserError(f"`{category}` is not a category. Pick one of: "
                        f"{', '.join(sorted(CATEGORIES))}.")

    quantity = (form.get("quantity") or "").strip()
    if quantity and not re.match(r"^\d+(\.\d+)?$", quantity):
        raise UserError(
            f"Quantity `{quantity}` needs to be a plain number. Put anything "
            f"else (\"about half a box\") in the notes field.")

    row = {field: "" for field in ITEM_FIELDS}
    row.update({
        "item_id": next_item_id(rows),
        "name": name,
        "category": category,
        "location_id": location_id,
        "quantity": quantity,
        "unit": (form.get("unit") or "").strip(),
        "vendor": (form.get("vendor") or "").strip(),
        "catalog_no": (form.get("catalog number") or "").strip(),
        "lot": (form.get("lot") or "").strip(),
        "received": normalize_date(form.get("date received") or "", "date received"),
        "expires": normalize_date(form.get("expires") or "", "expires"),
        # Someone physically holding the thing is the strongest verification
        # signal the system ever gets.
        "status": "present",
        "last_verified": TODAY,
        "verified_by": author,
        "owner": (form.get("owner") or "").strip(),
        "source": "manual",
        "notes": (form.get("notes") or "").strip().replace("\r\n", "\n"),
    })
    rows.append(row)

    label = locations[location_id]["label"]
    return (
        f"Add {name} to {location_id}",
        f"Adds one item from the issue form.\n\n"
        f"| field | value |\n|---|---|\n"
        f"| name | {name} |\n"
        f"| item_id | `{row['item_id']}` |\n"
        f"| category | {category} |\n"
        f"| location | `{location_id}` — {label} |\n"
        f"| quantity | {quantity or '—'} |\n"
        f"| received | {row['received'] or '—'} |\n"
        f"| notes | {row['notes'] or '—'} |\n\n"
        f"Recorded as `status=present`, `last_verified={TODAY}`, "
        f"`verified_by={author}` — whoever added it had it in their hands.")


def handle_status(form: dict, rows: list[dict], locations: dict[str, dict],
                  author: str) -> tuple[str, str]:
    item = find_item(form, rows, locations)
    status = (form.get("new status") or "").strip().lower()
    # Forms send "consumed — all gone"; keep the leading word.
    status = re.split(r"[\s—–(]+", status)[0]
    if status not in STATUSES:
        raise UserError(f"`{status}` is not a status. Pick one of: "
                        f"{', '.join(sorted(STATUSES))}.")

    was = item["status"]
    item["status"] = status
    item["last_verified"] = TODAY
    item["verified_by"] = author
    note = (form.get("notes") or "").strip().replace("\r\n", "\n")
    if note:
        item["notes"] = f"{item['notes']}; {note}".strip("; ") if item["notes"] else note

    return (
        f"Mark {item['name']} as {status}",
        f"Status change from the issue form.\n\n"
        f"| field | value |\n|---|---|\n"
        f"| item | {item['name']} |\n"
        f"| item_id | `{item['item_id']}` |\n"
        f"| location | `{item['location_id']}` |\n"
        f"| status | `{was}` → `{status}` |\n"
        f"| notes added | {note or '—'} |\n\n"
        f"Consumed and discarded items keep their row rather than being "
        f"deleted, so the history stays readable.")


def handle_verify(form: dict, rows: list[dict], locations: dict[str, dict],
                  author: str) -> tuple[str, str]:
    location_id = resolve_location(
        form.get("location") or form.get("location id") or "", locations)

    # Exact location only, never descendants -- verifying a freezer shelf is
    # not verifying the drawers inside it, and claiming otherwise would put
    # false confidence into the data.
    at_location = [r for r in rows if r["location_id"] == location_id]

    # The location row is stamped whether or not it holds anything. "I opened
    # this drawer and it is still empty" is real information, and before
    # locations carried their own last_verified it had nowhere to live -- which
    # made the 46 empty locations the one thing nobody could log.
    location = locations[location_id]
    location["last_verified"] = TODAY
    location["verified_by"] = author

    missing_raw = (form.get("anything missing") or "").strip()
    missing_names = [
        line.strip("-* \t") for line in missing_raw.splitlines()
        if line.strip("-* \t")
    ] if missing_raw else []

    marked_missing, verified = [], []
    for row in at_location:
        hit = any(m.lower() in row["name"].lower() for m in missing_names)
        row["status"] = "missing" if hit else "present"
        row["last_verified"] = TODAY
        row["verified_by"] = author
        (marked_missing if hit else verified).append(row)

    unmatched = [
        m for m in missing_names
        if not any(m.lower() in r["name"].lower() for r in at_location)
    ]

    body = [
        f"Verification pass from the issue form.\n",
        f"| field | value |\n|---|---|",
        f"| location | `{location_id}` — {location['label']} |",
        f"| confirmed present | {len(verified)} |",
        f"| marked missing | {len(marked_missing)} |",
        f"| verified_by | {author} |",
        f"| last_verified | {TODAY} |\n",
    ]
    if not at_location:
        body.append(
            "**Confirmed still empty.** Nothing was recorded here and nothing "
            "was found, so only the location's own `last_verified` moved. This "
            "is a real result, not a no-op — it retires a location from the "
            "unknown pile.\n")
    if marked_missing:
        body.append("Marked missing:\n" + "\n".join(
            f"- `{r['item_id']}` {r['name']}" for r in marked_missing) + "\n")
    if unmatched:
        body.append(
            "**These lines didn't match anything recorded here** and were "
            "left alone — check the spelling, or they may never have been "
            "recorded:\n" + "\n".join(f"- {m}" for m in unmatched) + "\n")

    # Surfaced in the PR rather than left in the issue: things found that were
    # never recorded are the most useful output of a verification pass, and a
    # reviewer looking only at the diff would never see them.
    unlisted = (form.get("anything there that isn't listed")
                or form.get("notes") or "").strip()
    if unlisted:
        body.append(
            "**Found here but not recorded** — needs *Add an item* issues, or "
            "add the rows to this PR directly:\n\n"
            + "\n".join(f"> {line}" for line in unlisted.splitlines()) + "\n")

    return (f"Verify {location_id} ({len(at_location)} items)", "\n".join(body))


HANDLERS = {
    "inventory:add": handle_add,
    "inventory:status": handle_status,
    "inventory:verify": handle_verify,
}

# Which heading identifies each form, if the labels aren't there to tell us.
# GitHub issue forms only apply labels that ALREADY EXIST in the repo -- a
# label named in the form's `labels:` that hasn't been created is silently
# dropped. That once made every filed form a no-op with no error anywhere: the
# workflow's label gate just skipped, and the person who filed it saw nothing.
# So the label is a hint, not the contract; the body is the contract.
BODY_SIGNATURES = [
    ("inventory:status", "new status"),
    ("inventory:verify", "anything missing"),
    ("inventory:add", "item name"),
]


def infer_action(labels: set[str], form: dict) -> str | None:
    """Resolve the action from labels, falling back to the form's own shape."""
    tagged = labels & set(HANDLERS)
    if len(tagged) == 1:
        return tagged.pop()
    if len(tagged) > 1:
        raise SystemExit(f"issue carries conflicting labels: {sorted(tagged)}")
    for action, heading in BODY_SIGNATURES:
        if heading in form:
            return action
    return None


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path,
                        default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--data", default=root / "data", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("."),
                        help="where to write pr_title.txt / pr_body.md")
    args = parser.parse_args()

    if not args.event:
        raise SystemExit("no event payload: pass --event or set GITHUB_EVENT_PATH")

    event = json.loads(Path(args.event).read_text())
    issue = event.get("issue") or {}
    labels = {l["name"] for l in issue.get("labels", [])}
    author = issue.get("user", {}).get("login", "unknown")
    number = issue.get("number", 0)

    form = parse_form(issue.get("body", ""))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    action = infer_action(labels, form)
    if action is None:
        # An ordinary issue, not one of our forms. Exit 3 so the workflow can
        # treat it as a clean no-op rather than a failure.
        print("not an inventory form issue; nothing to do")
        sys.exit(3)
    # The workflow reads this to backfill the label when the form couldn't.
    (args.out_dir / "action.txt").write_text(action)

    items_path = args.data / "items.csv"
    rows = read_items(items_path)
    locations = read_locations(args.data / "locations.csv")
    try:
        title, body = HANDLERS[action](form, rows, locations, author)
    except UserError as err:
        (args.out_dir / "error.md").write_text(
            f"{err}\n\n<sub>Nothing was changed. Edit this issue to fix it and "
            f"the check will run again.</sub>\n")
        print(f"user error: {err}", file=sys.stderr)
        sys.exit(2)

    write_items(items_path, rows)
    # Rewritten unconditionally: only the verify handler touches locations, and
    # an unchanged rewrite is byte-identical, so git shows no diff.
    write_locations(args.data / "locations.csv", locations)
    (args.out_dir / "pr_title.txt").write_text(title)
    (args.out_dir / "pr_body.md").write_text(
        f"{body}\n\nFiled by @{author} in #{number}.\n\n"
        f"<sub>Editing that issue rebuilds this branch from scratch, so any "
        f"commits pushed here by hand would be discarded — make corrections in "
        f"the issue, or merge and follow up separately.</sub>\n\n"
        f"Closes #{number}\n")
    print(title)


if __name__ == "__main__":
    main()

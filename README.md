# Roberts Lab Inventory

Searchable, version-controlled inventory of lab reagents, kits, consumables, and equipment — replacing the spreadsheet in [`legacy/`](legacy/).

Development plan and architecture: **[PLAN.md](PLAN.md)** · Photo rules (public repo): **[PHOTO_POLICY.md](PHOTO_POLICY.md)**

## Status

**Phase 1 complete** — the spreadsheet has been migrated to canonical CSVs with CI validation. Search UI, add-item forms, and photo intake are still to come; see [PLAN.md §5](PLAN.md#5-phases).

| | |
|---|---|
| Items | 499 |
| Locations | 173 |
| Verified since 2021 | **0%** — everything carries `status: unverified` |
| Rows needing a human pass | 16, in [`data/review_queue.csv`](data/review_queue.csv) |

## The data

| File | What it is |
|---|---|
| [`data/items.csv`](data/items.csv) | One row per item. The inventory. |
| [`data/locations.csv`](data/locations.csv) | Controlled vocabulary of physical places, nested via `parent_id`. |
| [`data/review_queue.csv`](data/review_queue.csv) | Legacy rows too ambiguous to migrate automatically. Each has a stated reason. |

`location_id` is the stable key — `209-CAB-01`, `213-F20-S02-D08`, `209-FRIDGE-DOOR-SHELF-1`. It's built to be short because it becomes a URL fragment and a QR-sticker payload.

Two fields carry more weight than they look like they do:

- **`last_verified`** — every migrated row says `2021-08-29`, because that's what the spreadsheet's own sheet names claim. This makes five years of staleness a queryable fact instead of a caveat.
- **`source`** — `legacy-xlsx`, `manual`, or `photo-llm`. Permanent provenance, so an audit can always ask which rows a model wrote.

## Working with it

```bash
pip install -r requirements.txt
```

Validate before opening a PR (CI runs the same thing):

```bash
python3 scripts/validate.py
```

Regenerate the CSVs from the frozen spreadsheet:

```bash
python3 scripts/migrate_legacy.py
```

Note that re-running the migration **overwrites `data/`**, discarding hand edits. It's a one-time tool kept for reproducibility and for re-tuning the item categorizer; once real edits start landing, `items.csv` is the source of truth and the spreadsheet is history.

## Conventions

- Dates are ISO 8601, and partial dates are allowed where that's the honest precision: `2019`, `2019-11`, `2019-11-27`.
- `category`, `status`, `kind`, and `source` are closed sets, enforced in CI. Adding a value means editing `scripts/validate.py` in the same PR.
- `quantity` is a number or empty. Ranges, guesses, and "about half a box" go in `notes`.
- Don't block an entry on details you don't have. A name and a location is a useful row; the rest can be filled in later.

## Maintainers

[@sr320](https://github.com/sr320) and [@kubu4](https://github.com/kubu4) — either can review and merge.

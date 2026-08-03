# Roberts Lab Inventory

Searchable, version-controlled inventory of lab reagents, kits, consumables, and equipment — replacing the spreadsheet in [`legacy/`](legacy/).

Development plan and architecture: **[PLAN.md](PLAN.md)** · Photo rules (public repo): **[PHOTO_POLICY.md](PHOTO_POLICY.md)**

## Status

**Phases 1–3 complete** — the spreadsheet is migrated to validated CSVs, there's a searchable site with printable QR stickers, and you can add or update items from a form without touching git. Photo intake with LLM descriptions (Phase 4) is still to come; see [PLAN.md §5](PLAN.md#5-phases).

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
| [`data/rooms.csv`](data/rooms.csv) | The declared scope: rooms 209, 213, 228, 230, and the -80˚C room. |
| [`data/review_queue.csv`](data/review_queue.csv) | Legacy rows too ambiguous to migrate automatically. Each has a stated reason. |

Coverage today is uneven — `209=70, 213=99, 228=0, 230=2, M80=2` locations. **Room 228 is in scope but appears nowhere in the legacy spreadsheet**, so it has nothing to migrate and needs an inventory pass; `validate.py` warns about it on every run until it does.

`location_id` is the stable key — `209-CAB-01`, `213-F20-S02-D08`, `209-FRIDGE-DOOR-SHELF-1`. It's built to be short because it becomes a URL fragment and a QR-sticker payload.

Two fields carry more weight than they look like they do:

- **`last_verified`** — every migrated row says `2021-08-29`, because that's what the spreadsheet's own sheet names claim. This makes five years of staleness a queryable fact instead of a caveat.
- **`source`** — `legacy-xlsx`, `manual`, or `photo-llm`. Permanent provenance, so an audit can always ask which rows a model wrote.

## Adding or changing something

You don't need git, and you don't need to know the file layout. File an issue:

| | |
|---|---|
| [Add an item](../../issues/new?template=add-item.yml) | Something new in the lab |
| [Mark consumed / low / missing](../../issues/new?template=update-status.yml) | You used the last of it, or it isn't where we say |
| [I verified a location](../../issues/new?template=verify-location.yml) | You opened a drawer and checked. **The most valuable thing you can file.** |

A bot turns the form into a pull request for a maintainer to merge. If something's wrong with the form — unknown location, a name matching five items — it comments on your issue and changes nothing.

The fastest path is from the shelf itself: **scan the QR sticker → tap "Add an item here"**, and the location arrives already filled in.

Only the first three fields of the add form are required. A name and a location is already a useful record — don't stall on catalog numbers.

## The site

Search UI and QR stickers deploy from `main` to GitHub Pages. Build and preview locally:

```bash
python3 scripts/build_site.py && python3 scripts/make_qr_labels.py
```

That writes `_site/index.html` (self-contained — just open it, no server needed) and `_site/labels.html`, a printable sheet of QR stickers. Each sticker encodes `<site>/#<location_id>`, so scanning the one on a drawer shows that drawer's contents, including anything nested inside it.

Stickers for just part of the lab:

```bash
python3 scripts/make_qr_labels.py --rooms 213 --kinds drawer cabinet
```

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

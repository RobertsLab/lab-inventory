# Roberts Lab Inventory — Development Plan

Status: **draft for review** · Author: planning pass, 2026-08-03

---

## 1. Where we are

The legacy system is `legacy/Roberts Lab Inventory.xlsx`, five sheets:

| Sheet | Rows | Columns | Notes |
|---|---|---|---|
| `DrawersCabinets updated 82921` | 144 | Room, Drawer/Cabinet, Number, *(unnamed contents col)* | 11 rows have a location but no contents |
| `Refrigerator Rm 209` | 145 | Shelf #, Contents, Recieved | most granular sheet; one item per row |
| `Shelf Rm 209 updated 9521` | 15 | Shelf #, Contents, Date | |
| `Shelf RM 213 updated 82921` | 50 | Shelf #, Contents, Date Rec'd, Notes | |
| `-20˚ Rm 213 updated 82921` | 16 | Shelf #, Drawer #, Contents | densest lists — up to 629 chars/cell |

Measured problems, in priority order:

1. **Stale.** Sheet names say last update Aug/Sep 2021. Five years of purchases, consumption, and moves are unrecorded. Any new system that doesn't make *re-inventorying* cheap will be stale again by 2028.
2. **Not item-granular.** ~370 location rows contain ~500 individual items (an early estimate of ~880 double-counted comma-separated fragments). One `-20˚` cell lists 11 kits with parenthetical dates. You cannot search, count, or expire-check a semicolon-delimited blob.
3. **No controlled vocabulary.** `Cabinet` / `Cabinet ` / `Cabinets`; rooms as `209.0`, `213.0`, `-80˚C room`. Location strings can't be joined or filtered reliably.
4. **Write-hostile.** One binary file, no concurrent edits, no history, no way to know who put the ZR-Duet kit on shelf 1 or whether it's still there.
5. **Dates unvalidated.** Received dates range 2005-06-27 → 2026-06-02; the latter is almost certainly a typo or a mis-entered expiration.

---

## 2. Design principles

These are the tie-breakers for every decision below.

- **Read is the hot path.** The dominant query is "do we have X, and where is it?" asked from a phone while standing in the lab. Optimize search hard; accept slightly more friction on writes.
- **Adding an item must take under 30 seconds.** If it takes longer than writing on a Post-it, people will write on a Post-it.
- **Plain text, in git, is the source of truth.** Diffable, greppable, scriptable, restorable, and readable in 20 years without a vendor. Matches how this lab already works.
- **The LLM drafts; a human commits.** Vision models are excellent at "this drawer contains pipette tips, a P1000, and three Falcon tubes" and untrustworthy on catalog numbers and lot IDs. Every machine-generated row lands in a reviewable PR, never straight into the canonical file.
- **No build toolchain that can rot.** Static HTML + vendored JS + Python stdlib where possible. A tool nobody can rebuild in 2029 is a tool that's gone.

---

## 3. Recommended architecture

**Git-backed flat files + a static search site + GitHub Actions for the write and photo paths.**

```
lab-inventory/
├── data/
│   ├── locations.csv          # controlled vocabulary of places
│   ├── items.csv              # canonical inventory, one row per item
│   └── vendors.csv            # optional lookup
├── photos/
│   ├── inbox/                 # drop zone; Action consumes and clears
│   └── 209-CAB-01_20260803.jpg
├── descriptions/
│   └── 209-CAB-01_20260803.md # LLM description + provenance, per photo
├── site/                      # GitHub Pages: search UI (generated + static)
├── scripts/
│   ├── migrate_legacy.py      # xlsx -> csv, one-time
│   ├── validate.py            # CI gate on every PR
│   ├── build_site.py          # csv -> inventory.json + location pages
│   ├── describe_photos.py     # Claude vision -> descriptions + candidate rows
│   └── make_qr_labels.py      # printable QR stickers per location
└── .github/
    ├── ISSUE_TEMPLATE/        # add-item / consumed-item / found-something forms
    └── workflows/             # validate, build-site, describe-photos, expiry-scan
```

Why this over the alternatives:

| Option | Verdict |
|---|---|
| **Git flat files + static site** (recommended) | Free, versioned, no auth to build (GitHub accounts already exist), LLM step is a natural Action, survives personnel turnover. Cost: lab members must tolerate a web form that opens a GitHub issue. |
| Another spreadsheet (Sheets + Apps Script) | Lowest friction to start, but reproduces every problem in §1. Sheets has no real review step, so LLM output would land unchecked. |
| Airtable / Notion | Genuinely easy, native mobile app, attachments and image fields for free. But ~$20/user/mo, data lives in a vendor, and versioning/provenance is weak. **Pick this instead if the lab flatly won't touch git** — the LLM step still works via automation + API script. |
| Real web app (SQLite + Flask on Fly/Render) | Best UX for writes, but needs hosting, auth, backups, and an owner. Not worth the maintenance load for ~1000 items and ~8 users. |

**Escape hatch:** because the source of truth is CSV, migrating *to* Airtable or a real app later is a 30-line import script. Starting with flat files costs nothing in optionality.

---

## 4. Data model

Concrete, minimal, extensible. Human-readable composite IDs beat surrogate keys in a hand-editable file.

### `data/locations.csv`

```csv
location_id,room,kind,number,parent_id,label,notes
209-CAB-01,209,cabinet,1,,"Cabinet 1, Rm 209",
209-FRIDGE,209,refrigerator,,,"Refrigerator, Rm 209",4C
209-FRIDGE-S1,209,shelf,1,209-FRIDGE,"Fridge shelf 1 (top)",
213-F20,213,freezer,,,"-20C freezer, Rm 213",
213-F20-S2-D8,213,drawer,8,213-F20,"-20C shelf 2, drawer 8",
```

- `location_id` is the stable key and the QR-code payload. Format `ROOM-UNIT[-SUB]`, uppercase, no spaces.
- `kind` is a closed set: `cabinet | drawer | shelf | refrigerator | freezer | bench | floor | bin | other`.
- `parent_id` gives nesting, so "everything in the -20" is one query.

### `data/items.csv`

```csv
item_id,name,category,location_id,quantity,unit,vendor,catalog_no,lot,received,expires,status,last_verified,verified_by,source,photo_id,notes
```

| Field | Purpose |
|---|---|
| `item_id` | `itm-00001`, assigned by script; never reused |
| `name` | free text, but normalized (`DNeasy PowerSoil Kit`) |
| `category` | closed set: `kit \| reagent \| enzyme \| consumable \| sample \| equipment \| tool \| media \| antibody \| other` |
| `quantity` / `unit` | nullable — don't block an entry on counting |
| `received` / `expires` | ISO 8601 `YYYY-MM-DD`, partial dates allowed (`2019-11`) |
| `status` | `present \| low \| consumed \| discarded \| missing \| unverified` |
| `last_verified` / `verified_by` | **the anti-staleness field.** Drives "hasn't been laid eyes on since 2021" reports |
| `source` | `legacy-xlsx \| manual \| photo-llm` — provenance, so we always know what a machine wrote |
| `photo_id` | links to `descriptions/<photo_id>.md` |

Deliberately omitted for now: price, grant/budget code, per-aliquot tracking, chemical hazard/SDS fields. Each is a real need for some labs; none should block v1. `notes` absorbs them until proven necessary.

### `descriptions/<photo_id>.md`

Long LLM prose does not belong in a CSV cell. One markdown file per photo, frontmatter for machines, body for humans:

```markdown
---
photo_id: 209-CAB-01_20260803
photo: photos/209-CAB-01_20260803.jpg
location_id: 209-CAB-01
taken_at: 2026-08-03
taken_by: sr320
model: claude-opus-5
described_at: 2026-08-03T14:22:00Z
review_status: pending      # pending | approved | corrected
reviewed_by:
candidate_items: 7
---

Upper shelf holds four boxes of P1000 filter tips (two open) and a rack of
15 mL Falcon tubes. Lower shelf: a Qiagen DNeasy PowerSoil box, partially
open, and two unlabeled amber bottles...
```

---

## 5. Phases

Each phase ends in something usable on its own. Estimates are working-session hours, not calendar time.

### Phase 0 — Decisions (blocking, ~30 min of your time)
Answer §7. Nothing below can be built without the repo-visibility and photo-privacy calls.

### Phase 1 — Canonical data out of the spreadsheet (~4–6 h)
- `scripts/migrate_legacy.py`: read all 5 sheets, normalize rooms/kinds, mint `location_id`s, **split semicolon- and comma-delimited contents into individual item rows**, pull parenthetical dates (`(11/27/2019)`) into `received`, emit `locations.csv` + `items.csv`.
- Every migrated row gets `source=legacy-xlsx`, `status=unverified`, `last_verified=2021-08-29` (the sheet's own claim). **The 5-year staleness becomes visible data, not a footnote.**
- Splitting is imperfect on strings like `Microcentrifuge tubes, conical screw top tubes, screw caps` — the script flags ambiguous splits in `data/review_queue.csv` for a human pass rather than guessing silently.
- `scripts/validate.py` + a CI workflow: schema check, closed-set enforcement, `location_id` referential integrity, ISO date check, duplicate `item_id` check. This is what keeps the data clean once several people are editing.
- **Done when:** `items.csv` has ~500 rows, validation is green in CI, and the xlsx is never opened again. **✅ Done 2026-08-03: 499 items, 173 locations, 16 review-queue rows.**

### Phase 2 — Make things findable (~6–8 h) — ✅ **Done 2026-08-03**
- `scripts/build_site.py` → a single self-contained `_site/index.html` with the data **inlined**, not fetched as a JSON sidecar. Inlining means the built page works over `file://`, so previewing it needs no local server and there are no CORS surprises.
- Single-page search UI: instant search across name/category/notes/owner/vendor **and location label**, so "fridge door" and "cabinet 3" are queries too. Filters for room, category, and status; match highlighting; mobile-first; dark mode.
- **No search library.** 499 items is small enough that a precomputed lowercase haystack per item plus a scored linear scan beats any index, and there's nothing vendored to keep alive. Scoring: name-prefix > name-substring > any-field, and every query token must match somewhere.
- Location deep links (`/#209-CAB-01`) with **descendant rollup** — scanning the sticker on the -20 freezer shows all 49 items across its shelves and drawers, not the zero items filed directly against the freezer itself.
- `scripts/make_qr_labels.py` → printable sheet of QR stickers, one per location, filterable by room and kind. **HTML, not PDF**, so there's no PDF toolchain to maintain: open and print from the browser. Sticker geometry is CSS variables at the top of the generated file, so it can be nudged to fit label stock without touching the script.
- `.github/workflows/pages.yml` validates, builds, and deploys on every push to `main`. QR payloads take their base URL from `configure-pages` output rather than a hardcoded string, so the codes always point at wherever Pages actually serves the repo.
- **Done when:** a grad student on a phone can answer "do we have EZ DNA Methylation-Gold?" in under 10 seconds. ✅

  **Requires one manual step:** Settings → Pages → Source = **GitHub Actions**. The workflow can't enable Pages for the repo itself.

### Phase 3 — Make writing easy (~4–6 h) — ✅ **Done 2026-08-03**
- Three GitHub Issue Forms: **Add an item**, **Mark something consumed/low/missing**, **I verified a location**.
- `.github/workflows/issue-to-pr.yml` parses the issue → edits `items.csv` → opens a PR → issue auto-closes on merge. Bad input never becomes a PR: the workflow comments the problem back on the issue and stops.
- **Prefill instead of dropdowns.** The original plan was location dropdowns generated from `locations.csv`. Dropped: 173 options is miserable on a phone, and regenerating form YAML from data would mean the repo editing its own workflow files. Instead the site links to `issues/new?template=add-item.yml&location=213-DRW-27` — GitHub prefills by field `id`. Scan the QR sticker → tap **Add an item here** → the location is already filled in. Free-text locations are validated by the script, which suggests close matches on a typo.
- Site gains three actions: **Add an item here** and **I verified this** on the location banner, **used up?** on every item row.
- **Field labels are the contract.** GitHub renders forms as `### <label>` sections, so renaming a label silently stops the parser from seeing that field — no error, the value just vanishes. `scripts/check_forms.py` runs in CI and fails on any form field the parser doesn't read, any handler with no form, and any field missing an `id` (which would break prefill).
- **Done when:** adding an item requires zero git knowledge and takes under 30 seconds. ✅

Two things to know about how this is wired:

- **The generated PR gets no separate check run.** A PR opened with `GITHUB_TOKEN` doesn't trigger other workflows, so `validate.yml` won't fire on it. The issue-to-pr workflow therefore runs `validate.py` itself *before* pushing — bad data never reaches a PR. If you'd rather see a check on the PR, swap in a PAT.
- **Editing the issue rebuilds the branch from scratch** (force-push), so hand-written commits on an `inv/*` branch would be discarded. The generated PR body says so. Correct things in the issue, or merge and follow up separately.

Because the repo is public, the workflow only runs for `OWNER`/`MEMBER`/`COLLABORATOR` authors. Without that, anyone could open issues that spawn branches and PRs. Review still gates merging either way — the author check is about spam, not privilege.

### Phase 4 — Photos + LLM description (~8–10 h) — *see §6*
The re-inventory engine. Optional in the sense that Phases 1–3 stand alone, but this is what actually gets 2021 data refreshed to 2026.

### Phase 5 — Keep it alive (~4 h, incremental)
- Scheduled Action: expiring/expired reagent report → opens a dated issue.
- **Verification campaigns:** monthly Action picks the N locations with the oldest `last_verified` and opens a small, finishable issue ("verify these 3 drawers"). Staleness becomes a slow drip of 5-minute tasks instead of a dreaded annual audit.
- Dashboard panel on the site: items by room, % verified in last 12 months, count expired.
- Optional: catalog-number lookup to auto-fill vendor/product name.

---

## 6. Photo + LLM pipeline (Phase 4, in detail)

### Flow

```
phone photo ──► photos/inbox/  (drag-drop into a PR, or an upload issue)
                     │
                     ▼
        Action: describe-photos.yml
          1. downscale to 1600px long edge, JPEG q80, strip EXIF GPS
          2. derive location_id from filename or issue form field
          3. Claude vision call, structured JSON out
          4. write descriptions/<photo_id>.md
          5. append candidate rows to data/candidates.csv
          6. open PR: "Photo intake: 209-CAB-01 (7 candidate items)"
                     │
                     ▼
        Human reviews PR: edits names, deletes hallucinations,
        sets quantities, ticks review_status: approved
                     │
                     ▼
        Merge ──► rows move into items.csv with source=photo-llm,
                  last_verified = photo date, verified_by = reviewer
```

### The model call

- **Model:** `claude-opus-5` for intake accuracy; `claude-sonnet-5` if volume makes cost matter. Both handle multi-image messages, so a whole drawer can be 3 angles in one call.
- **Structured output:** define a tool/JSON schema (`items[]` with `name`, `category`, `quantity`, `confidence`, `text_visible_on_label`) rather than parsing prose. Ask for a separate free-text `overview` for the description file.
- **Prompt discipline** — this is where quality lives:
  - Pass `locations.csv` context and the existing item list for that location, so the model can say "matches existing item itm-00412" instead of creating a duplicate.
  - Require verbatim transcription of visible label text in `text_visible_on_label`, separate from the inferred `name`. Makes hallucinated catalog numbers obvious in review.
  - Require a per-item `confidence` and an explicit `uncertain: true` for anything partially occluded. Low-confidence rows render as unchecked boxes in the PR.
  - Instruct it **not** to guess lot numbers, expiration dates, or catalog numbers unless legibly visible.
- **Cost:** roughly 1.5–2k input tokens per image plus output. A 200-photo full-lab sweep is a few dollars. Not a budget consideration.
- **Secrets:** `ANTHROPIC_API_KEY` as a repo secret. Workflow must be `pull_request_target`-free and only run on trusted branches so the key isn't exposed to fork PRs.

### Storage

- Downscaled JPEGs (~250 KB) committed directly. 300 photos ≈ 75 MB — comfortably under limits, no Git LFS, no external bucket. Revisit only past ~1 GB.
- Strip GPS EXIF on ingest, always.
- Originals are not kept. If someone wants archival-quality photos later, that's a separate bucket decision.

### Guardrails

- LLM output **never** writes to `items.csv` directly — only to `candidates.csv` inside a PR.
- `source=photo-llm` is permanent provenance; a later audit can always ask "which rows did a model write?"
- A photo whose PR is never reviewed leaves `review_status: pending`, and the Phase 5 report surfaces the backlog.

---

## 7. Decisions

### Resolved (2026-08-03)

1. **Repo visibility: public.** Consequence, and it is not optional: since the repo is public from day 1, the photo guardrails have to carry the whole privacy load themselves. That makes three Phase 4 items **mandatory rather than nice-to-have**:
   - EXIF stripping (all tags, not just GPS) on every ingested image.
   - A committed `PHOTO_POLICY.md` — no notebook pages, no screens, no whiteboards, no people, no printed sample manifests in frame — linked from every photo-intake issue form.
   - Human review of the photo *itself* in the intake PR, not just the LLM's item list. Reviewers are checking for accidentally-captured content, and a photo can be rejected on privacy grounds alone.

   Also note: a merged-then-deleted photo stays in git history forever and will already have been scraped. Removing one means a history rewrite. The review step is the only real control, so it has to happen before merge.

2. **Maintainers: `@sr320` and `@kubu4`**, both with merge rights. Implemented as a `CODEOWNERS` file requiring review from either. Two people is the right number — enough that nobody is a single point of failure, few enough that review actually happens.

3. **Scope is five rooms: 209, 213, 228, 230, and the -80˚C room.** Declared explicitly in `data/rooms.csv` rather than left implicit in whatever the spreadsheet happened to contain, and enforced — `validate.py` fails if a location names an undeclared room.

   **Room 228 appears nowhere in the legacy spreadsheet**, so it starts with zero locations. There is nothing to migrate for it; it gets populated by walking in with a phone. Until then `validate.py` emits a standing warning on every run so an in-scope-but-unrecorded room can't quietly be forgotten:

   ```
   WARN  rooms.csv: room '228' (Room 228) is in scope but has no locations yet -- needs an inventory pass
   ```

   Current coverage: `209=70, 213=99, 228=0, 230=2, M80=2` locations. Rooms 230 and M80 have only 2 each, so they're really placeholders too — the shelves are recorded, their contents barely.

   Room labels are deliberately generic (`Room 228`, not a guessed purpose). Fill them in with something more useful when convenient.

### Still open (not blocking Phase 1)

4. **Freezer boxes: in or out of scope?** Sample-level tracking (-80 boxes, positions, individual DNA extractions) is a genuinely different problem — thousands of rows, needs plate/position coordinates, and usually wants its own tool. I recommend **v1 tracks the box, not the tube**, with a hook to add sample-level later.
5. **Item owners.** The spreadsheet tags several storage bins "- Goetz". An `owner` column is in the schema and populated where the legacy data made it explicit. Worth confirming this is a concept you want to keep tracking.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| **Nobody adopts it; back to Post-its.** The real failure mode. | Phase 2 before Phase 3 — deliver search value before asking for data entry. QR stickers make it tangible. Keep the add-item form to 4 fields. |
| Migration mangles semicolon-delimited contents | Ambiguous splits go to `review_queue.csv`, not silently into the data. Original xlsx stays in `legacy/` forever. |
| LLM invents plausible catalog numbers | Verbatim-label field + confidence + mandatory human PR review. Never auto-merge. |
| Photos leak sensitive content | Private repo (decision 1), EXIF GPS stripping, explicit "no notebooks/screens" rule, review step before merge. |
| CSV merge conflicts with several editors | Rows appended at end, sorted only by a scripted step; conflicts are rare and trivially resolvable. Validation catches damage. |
| Bit rot in the site | No bundler, no npm, vendored JS, Python stdlib. Rebuildable from `scripts/` alone. |

---

## 9. Suggested first commit

Phase 1 is self-contained and unblocks everything: write `migrate_legacy.py`, generate `locations.csv` + `items.csv` + `review_queue.csv`, add `validate.py` and the CI workflow. That single PR turns a stale binary into queryable, version-controlled data — and makes the scale of the re-inventory job concrete before we invest in the photo pipeline.

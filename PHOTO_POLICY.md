# Photo policy

**This repository is public.** Anything committed here is world-readable, permanently, and will be scraped and mirrored within hours. Photos are the only part of this inventory that can leak something you didn't intend, so this policy is the whole privacy control.

## Before you take the photo

Frame the shelf, drawer, or bin — nothing else. Check the edges of the frame for:

- **Lab notebook pages** — open notebooks, loose protocol pages, printouts
- **Screens** — monitors, laptops, tablets, instrument displays, phones
- **Whiteboards and posted notes** — including sticky notes on cabinet doors
- **People** — hands are fine, faces and name badges are not
- **Sample manifests** — printed sample lists, freezer maps, plate maps taped to equipment
- **Anything with a person's name, a subject ID, or an unpublished result on it**

Tube labels and reagent bottle labels are fine and are the point of the exercise. A printed sheet listing 96 sample IDs is not.

## What happens on ingest

The intake pipeline strips **all** EXIF metadata (not just GPS) before anything is committed. Do not rely on that for anything but metadata — it cannot see what's in the frame.

## Review is the only real control

A photo gets reviewed by [@sr320](https://github.com/sr320) or [@kubu4](https://github.com/kubu4) before merge. Reviewers check **the image itself**, not just the item list the model derived from it. A photo can be rejected on privacy grounds alone even if the extracted items are perfect.

**This must happen before merge, because after merge it is too late.** Deleting a photo in a later commit does not remove it — it stays in git history, it stays in every clone and fork, and it has already been indexed. Removing it for real means rewriting history and force-pushing, which breaks everyone's clone and still doesn't recall what was scraped.

If you realize a merged photo shouldn't be public: say so immediately in an issue and tag both maintainers. Don't quietly delete the file — that leaves it in history while making it look handled.

## If you're unsure

Don't commit it. Ask in the issue first, or just retake the photo with the questionable thing out of frame. Retaking a photo costs 30 seconds.

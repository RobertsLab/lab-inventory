#!/usr/bin/env python3
"""Rebuild the pull requests for inventory issues that don't have a good one.

Two situations, one remedy:

  * An issue never became a PR at all. On 2026-08-06, 29 of 46 issues ended up
    here -- Actions stopped turning `issues` events into workflow runs partway
    through a bulk filing session, and roughly half the runs that were created
    never got a runner.
  * An issue has a PR that merge-guard has marked red, because main moved and
    the branch no longer merges into it cleanly.

Both are fixed by re-triggering the issue: issue-to-pr rebuilds the branch from
current main, force-pushes it, and updates the existing PR if there is one. The
branch is entirely derived from the issue, so regenerating is always correct and
always preferable to rebasing one by hand.

Re-triggering means removing and re-adding the `inventory` label, which emits a
`labeled` event without touching anything the issue author wrote. Removing a
label doesn't start a run (`unlabeled` isn't a trigger), so exactly one run
fires, and the action labels the parser reads are never absent.

Pacing is the point of this script, not a nicety. Firing 43 form submissions in
an afternoon is what broke this repo the first time, and the failure is silent:
no run appears, no error is reported anywhere, and the issue still carries the
labels its form template applied. So this waits for each PR to actually
materialise before starting the next one, and says so loudly when one doesn't.

Usage:
    python3 scripts/replay_issues.py --dry-run        # what would it do
    python3 scripts/replay_issues.py                  # replay everything
    python3 scripts/replay_issues.py --limit 5        # a few at a time
    python3 scripts/replay_issues.py --only red       # just the stale PRs

Needs the `gh` CLI, authenticated. Stdlib only otherwise.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

BRANCH_PREFIX = "inv/issue-"
TRIGGER_LABEL = "inventory"

# States an issue can be in, and whether this script acts on it.
NEEDS_PR = "no-pr"        # no open PR at all
STALE = "red"             # PR exists, merge-guard says it won't merge cleanly
PENDING = "pending"       # PR exists, merge-guard hasn't reported yet
OK = "ok"                 # PR exists and is green
ACTIONABLE = (NEEDS_PR, STALE)


def gh(*args: str) -> str:
    """Run gh and return stdout, raising with stderr attached on failure."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def gh_json(*args: str):
    return json.loads(gh(*args) or "null")


def open_issues() -> list[dict]:
    """Open issues filed through an inventory form, oldest first."""
    issues = gh_json(
        "issue", "list", "--state", "open", "--label", TRIGGER_LABEL,
        "--limit", "200", "--json", "number,title",
    ) or []
    return sorted(issues, key=lambda i: i["number"])


def open_bot_prs() -> dict[int, dict]:
    """Open PRs that issue-to-pr created, keyed by the issue they came from."""
    prs = gh_json(
        "pr", "list", "--base", "main", "--state", "open", "--limit", "200",
        "--json", "number,headRefName,headRefOid",
    ) or []
    out = {}
    for pr in prs:
        ref = pr["headRefName"]
        if ref.startswith(BRANCH_PREFIX):
            suffix = ref[len(BRANCH_PREFIX):]
            if suffix.isdigit():
                out[int(suffix)] = pr
    return out


def merge_guard(sha: str) -> str | None:
    """Latest merge-guard state for a commit, or None if it hasn't reported.

    Statuses come back newest-first, and only the newest per context counts --
    a commit can carry several if the guard has re-run.
    """
    for st in gh_json("api", f"repos/{{owner}}/{{repo}}/commits/{sha}/statuses") or []:
        if st.get("context") == "merge-guard":
            return st.get("state")
    return None


def classify(issues: list[dict], prs: dict[int, dict]) -> list[dict]:
    rows = []
    for issue in issues:
        num = issue["number"]
        pr = prs.get(num)
        if pr is None:
            state, detail = NEEDS_PR, "no open PR"
        else:
            guard = merge_guard(pr["headRefOid"])
            if guard == "success":
                state, detail = OK, f"#{pr['number']} green"
            elif guard is None:
                state, detail = PENDING, f"#{pr['number']} no merge-guard status yet"
            else:
                state, detail = STALE, f"#{pr['number']} merge-guard {guard}"
        rows.append({"issue": num, "title": issue["title"],
                     "state": state, "detail": detail, "pr": pr})
    return rows


def trigger(issue: int) -> None:
    """Emit a `labeled` event without editing anything a person wrote."""
    gh("issue", "edit", str(issue), "--remove-label", TRIGGER_LABEL)
    time.sleep(2)
    gh("issue", "edit", str(issue), "--add-label", TRIGGER_LABEL)


def head_sha(issue: int) -> str | None:
    prs = open_bot_prs()
    pr = prs.get(issue)
    return pr["headRefOid"] if pr else None


def wait_for(issue: int, before: str | None, timeout: int, poll: int) -> str | None:
    """Block until the PR exists, or its head moves off `before`.

    Returns the new head SHA, or None if nothing happened before the timeout --
    which is the signature of the silent drop this script exists to survive.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll)
        sha = head_sha(issue)
        if sha and sha != before:
            return sha
        left = int(deadline - time.monotonic())
        print(f"      ... waiting ({left}s left)", flush=True)
    return None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan and change nothing")
    p.add_argument("--only", choices=["no-pr", "red", "both"], default="both",
                   help="which issues to act on (default: both)")
    p.add_argument("--limit", type=int, default=0,
                   help="act on at most N issues (default: no cap)")
    p.add_argument("--skip", default="",
                   help="comma-separated issue numbers to leave alone")
    p.add_argument("--interval", type=int, default=30,
                   help="seconds to pause after each issue lands (default: 30)")
    p.add_argument("--timeout", type=int, default=300,
                   help="seconds to wait for one PR to appear (default: 300)")
    p.add_argument("--poll", type=int, default=15,
                   help="seconds between checks while waiting (default: 15)")
    p.add_argument("--attempts", type=int, default=2,
                   help="tries per issue before giving up (default: 2)")
    args = p.parse_args()

    print("reading issues and open PRs ...", flush=True)
    rows = classify(open_issues(), open_bot_prs())

    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    print("\n  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())) + "\n")

    wanted = ACTIONABLE if args.only == "both" else (
        NEEDS_PR if args.only == "no-pr" else STALE,)
    skip = {int(s) for s in args.skip.replace(",", " ").split()}
    todo = [r for r in rows if r["state"] in wanted and r["issue"] not in skip]
    if skip:
        print(f"  skipping on request: {', '.join(f'#{s}' for s in sorted(skip))}\n")
    if args.limit:
        todo = todo[:args.limit]

    if not todo:
        print("nothing to replay.")
        return

    print(f"{len(todo)} issue(s) to replay:")
    for r in todo:
        print(f"  #{r['issue']:<4} {r['state']:<7} {r['detail']}")

    if args.dry_run:
        print("\n--dry-run: nothing was triggered.")
        return

    # Serial on purpose. Each branch is cut from main as it exists at that
    # moment, so overlapping regenerations would hand out colliding item ids --
    # the exact bug merge-guard was added to catch.
    print()
    done, failed = [], []
    for n, r in enumerate(todo, 1):
        issue = r["issue"]
        print(f"[{n}/{len(todo)}] issue #{issue} -- {r['title']}", flush=True)
        before = r["pr"]["headRefOid"] if r["pr"] else None

        for attempt in range(1, args.attempts + 1):
            if attempt > 1:
                print(f"      retrying ({attempt}/{args.attempts})", flush=True)
            trigger(issue)
            sha = wait_for(issue, before, args.timeout, args.poll)
            if sha:
                print(f"      -> branch at {sha[:9]}", flush=True)
                done.append(issue)
                break
        else:
            # No run, or a run that never got a runner, or apply_issue.py
            # rejected the form and commented instead. The issue itself says
            # which -- this script deliberately doesn't guess.
            print(f"      !! no PR after {args.timeout}s x{args.attempts}; "
                  f"check https://github.com/RobertsLab/lab-inventory/issues/{issue}",
                  flush=True)
            failed.append(issue)

        if n < len(todo):
            time.sleep(args.interval)

    print(f"\nreplayed {len(done)}, gave up on {len(failed)}")
    if failed:
        print("  needs a look: " + ", ".join(f"#{i}" for i in failed))
    print("\nPRs are open for review; merge-guard re-checks them every time main"
          "\nmoves. Re-run with --only red after a batch of merges to regenerate"
          "\nwhatever went stale.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

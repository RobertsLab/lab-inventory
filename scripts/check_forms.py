#!/usr/bin/env python3
"""Check that the issue forms and apply_issue.py still agree.

GitHub renders an issue form as '### <label>' sections, so **field labels are
the contract** between .github/ISSUE_TEMPLATE/*.yml and the parser. Rename a
label and the parser silently stops seeing that field -- no error, the value
just vanishes. This catches that in CI.

Usage:
    python3 scripts/check_forms.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    raise SystemExit("PyYAML required: pip install -r requirements.txt")

ROOT = Path(__file__).resolve().parent.parent
FORMS = ROOT / ".github" / "ISSUE_TEMPLATE"
PARSER = ROOT / "scripts" / "apply_issue.py"

# Each form must carry exactly one of these, and the parser must handle it.
ACTION_LABELS = {"inventory:add", "inventory:status", "inventory:verify"}


def main() -> int:
    source = PARSER.read_text()
    read_keys = set(re.findall(r'form\.get\("([^"]+)"\)', source))
    handled = set(re.findall(r'"(inventory:[a-z]+)":\s*handle_', source))

    errors: list[str] = []
    seen_actions: set[str] = set()

    for path in sorted(FORMS.glob("*.yml")):
        if path.name == "config.yml":
            continue
        form = yaml.safe_load(path.read_text())
        name = path.name

        labels = {l for l in (form.get("labels") or [])}
        actions = labels & ACTION_LABELS
        if len(actions) != 1:
            errors.append(
                f"{name}: needs exactly one of {sorted(ACTION_LABELS)}, "
                f"has {sorted(actions)}")
        else:
            action = actions.pop()
            seen_actions.add(action)
            if action not in handled:
                errors.append(f"{name}: label {action} has no handler in "
                              f"apply_issue.py")
        if "inventory" not in labels:
            errors.append(f"{name}: missing the shared `inventory` label, "
                          f"which the workflow filters on")

        for field in form.get("body") or []:
            if field.get("type") == "markdown":
                continue
            label = (field.get("attributes") or {}).get("label")
            if not label:
                errors.append(f"{name}: a {field.get('type')} field has no label")
                continue
            key = label.strip().lower()
            if key not in read_keys:
                errors.append(
                    f"{name}: field \"{label}\" is never read by "
                    f"apply_issue.py — the value would be silently dropped")
            if not field.get("id"):
                errors.append(
                    f"{name}: field \"{label}\" has no id, so the site cannot "
                    f"prefill it via a query parameter")

    for action in sorted(handled - seen_actions):
        errors.append(f"apply_issue.py handles {action} but no form emits it")

    if errors:
        print(f"{len(errors)} problem(s):")
        for error in errors:
            print(f"  FAIL  {error}")
        return 1
    print(f"issue forms agree with apply_issue.py "
          f"({len(seen_actions)} actions, {len(read_keys)} readable fields)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

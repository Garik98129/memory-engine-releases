"""Says whether the branch `stable` cannot be rewritten or deleted (workflow `stable`).

    gh api repos/OWNER/NAME/rules/branches/stable | python3 -I stable-rules.py

The code that moves `stable` only ever adds a commit, so the branch can be closed to a forced
push and to deletion with no exception — and it is the address every chat reads the rules at.
That closing is a ruleset in the repository's settings, the owner's to make and nobody's to
see being switched off. So the night's run reads it: GitHub's answer on standard input — the
rules of every active ruleset that covers the branch, an array of objects with a `type` — must
hold `deletion` and `non_fast_forward`.

Exit 0 — both are there; 1 — a finding: one is missing; 2 — the answer could not be read.
"""
from __future__ import annotations

import json
import sys

WANTED = {"deletion": "its deletion", "non_fast_forward": "a forced push to it"}


def main() -> int:
    try:
        rules = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(rules, list) or not all(isinstance(rule, dict) for rule in rules):
            raise ValueError
    except (UnicodeDecodeError, ValueError, RecursionError):
        print("RULES ERROR GitHub's answer about the rules of the branch stable is not a list of rules")
        return 2
    have = {rule.get("type") for rule in rules if isinstance(rule.get("type"), str)}
    missing = [what for kind, what in WANTED.items() if kind not in have]
    if missing:
        print(f"RULES finding: no active ruleset of this repository forbids {' or '.join(missing)}: the branch "
              f"stable can be rewritten or removed. Settings → Rules → Rulesets: a branch ruleset for "
              f"«stable», active, with «Restrict deletions» and «Block force pushes» (README.md).")
        return 1
    print("RULES the branch stable is closed to a forced push and to deletion")
    return 0


if __name__ == "__main__":
    sys.exit(main())

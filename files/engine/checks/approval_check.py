"""C7 approval-record: the PR body records what the person agreed to (docs/DESIGN.md §6).

The bot writes the body in this form; the headings are the machine's, the text
under «Change» is what the person was shown, in her language:

    ## Change
    <the «was → is» text exactly as shown to the person, in her language>

    ## Files
    - user/fields/chat.md
    - user/routines/morning.md (full rewrite)

    ## Approved
    2026-10-23T11:08+03:00

Every changed file is named, and nothing else (flagship B23). A master of 40 or
more lines with more than 40 % of its lines changed carries «(full rewrite)»; the
bot tells the person so in her words (flagship B14). The approval time has a UTC offset, is
not in the future and not more than a day older than the PR's head commit. The
check reads presence and form, not truth: it is the bot's self-report, never a
review.

Not applicable to an update PR or to a PR that changes only
user/fields/installed.toml — a machine record, not the person's change (§5).
"""
from __future__ import annotations

import difflib
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import gitio
from .paths_check import PERSON
from .report import Finding

SECTIONS = ("Change", "Files", "Approved")
MACHINE_RECORD = "user/fields/installed.toml"
REWRITE_MARK = "(full rewrite)"
REWRITE_MIN_LINES = 40
REWRITE_SHARE = 0.40
CLOCK_SKEW = timedelta(minutes=10)
APPROVAL_MAX_AGE = timedelta(hours=24)    # the person's yes comes shortly before the change is committed
FILE_LINE_RE = re.compile(r"- (\S+)( \(full rewrite\))?")


def not_applicable(repo: Path, base: str, head: str, kind: str) -> str | None:
    """Why C7 does not apply to this PR, or None if it does."""
    if kind != PERSON:
        return f"{kind} PR"
    paths = {c.path for c in gitio.changes(repo, base, head)}
    if paths == {MACHINE_RECORD.encode()}:
        return "machine record only"
    return None


def sections(body: str) -> tuple[dict[str, str], list[str]]:
    found: dict[str, list[str]] = {}
    problems: list[str] = []
    current: list[str] | None = None
    for line in body.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            if name in found:
                problems.append(f"the PR body has ## {name} twice")
            current = found.setdefault(name, [])
        elif current is not None:
            current.append(line)
    return {k: "\n".join(v).strip() for k, v in found.items()}, problems


def changed_share(old: str, new: str) -> float:
    a, b = old.splitlines(), new.splitlines()
    kept = sum(m.size for m in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return 1 - kept / len(a) if a else 0.0


def check(repo: Path, base: str, head: str, body: str, now: datetime,
          committed: datetime | None = None) -> list[Finding]:
    text, problems = sections(body)
    findings = [Finding("C7", p) for p in problems]
    for name in SECTIONS:
        if name not in text:
            findings.append(Finding("C7", f"the PR body has no ## {name} section"))
        elif not text[name]:
            findings.append(Finding("C7", f"the ## {name} section is empty"))
    if findings:
        return findings

    named: dict[str, bool] = {}
    for line in text["Files"].splitlines():
        if not line.strip():
            continue
        m = FILE_LINE_RE.fullmatch(line.strip())
        if m is None:
            findings.append(Finding("C7", f"## Files: {line.strip()[:60]!r} is not «- path» or «- path {REWRITE_MARK}»"))
            continue
        named[m.group(1)] = m.group(2) is not None

    changed = gitio.changes(repo, base, head)
    changed_paths = {c.path.decode("utf-8", "replace") for c in changed}
    for path in sorted(changed_paths - named.keys()):
        findings.append(Finding("C7", f"{path} is changed but not named under ## Files"))
    for path in sorted(named.keys() - changed_paths):
        findings.append(Finding("C7", f"{path} is named under ## Files but not changed"))

    for c in changed:
        path = c.path.decode("utf-8", "replace")
        if c.status != "M" or not path.endswith(".md") or named.get(path):
            continue
        old_raw, new_raw = gitio.file_at(repo, base, path), gitio.file_at(repo, head, path)
        if old_raw is None or new_raw is None:
            continue
        old = old_raw.decode("utf-8", "replace")
        lines = len(old.splitlines())
        share = changed_share(old, new_raw.decode("utf-8", "replace"))
        if lines >= REWRITE_MIN_LINES and share > REWRITE_SHARE:
            findings.append(Finding("C7", f"{path} changes {share:.0%} of its {lines} lines: "
                                          f"name it with {REWRITE_MARK} and tell the person"))

    stamp = text["Approved"].splitlines()[0].strip()
    try:
        approved = datetime.fromisoformat(stamp)
    except ValueError:
        approved = None
    if approved is None or approved.tzinfo is None:
        findings.append(Finding("C7", f"## Approved: {stamp[:40]!r} is not a time with a UTC offset"))
    elif approved > now + CLOCK_SKEW:
        findings.append(Finding("C7", "## Approved: the time is in the future"))
    elif committed is not None and approved < committed - APPROVAL_MAX_AGE:
        findings.append(Finding("C7", "## Approved: the yes is more than a day older than the change"))
    return findings

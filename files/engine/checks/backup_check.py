"""N2 backup: the Notion copy stopped, or its history was rewritten (docs/DESIGN.md §6).

The source is GitHub's own record of the branch `notion-backup` — the repository
activity log, with server time, force pushes and deletions included — the same record
N1 reads for `main`: a commit date is set by whoever pushes, a run artifact can be
deleted by a collaborator, the log can be neither. Within the window: every change of
the branch is a push by the backup workflow (`github-actions[bot]`); a force push, a
deletion or a push by anyone else is an alarm. The branch head is the log's last
`after`, and that change is younger than the window: the copy writes its stamp every
night, so a quiet Notion still moves the branch. No branch and no record of it yet is
a note, not an alarm: the copy has not run; a deleted branch is in the log.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from . import gitio
from .integrity import BOT, _short, _when
from .report import ALARM, Finding

BRANCH = "notion-backup"
REF = f"refs/heads/{BRANCH}"
WINDOW = timedelta(hours=36)


def _alarm(message: str) -> Finding:
    return Finding("N2", message, ALARM)


def head(repo: Path, refs_prefix: str) -> str | None:
    try:
        return gitio.git(repo, "rev-parse", "--verify", "--quiet", f"{refs_prefix}{BRANCH}^{{commit}}").decode().strip() or None
    except gitio.GitError:
        return None


def check(api, repo: Path, refs_prefix: str, now: datetime) -> tuple[list[Finding], list[str]]:
    found: list[Finding] = []
    notes: list[str] = []
    cutoff = now - WINDOW
    branch = head(repo, refs_prefix)
    latest = None
    changes: list[dict] = []
    for item in api.pages(f"repos/{api.repository}/activity", {"ref": REF, "direction": "desc"}):
        latest = latest or item
        if _when(item["timestamp"]) < cutoff:
            break
        changes.append(item)

    if latest is None:
        if branch is None:
            return found, ["N2 NOTE the Notion copy has not run yet: no notion-backup branch, no record of one"]
        found.append(_alarm(f"notion-backup is at {_short(branch)}, but GitHub has no record of the branch"))
        return found, notes

    for change in changes:
        kind = change.get("activity_type")
        who = (change.get("actor") or {}).get("login", "?")
        when = change.get("timestamp")
        if kind == "force_push":
            found.append(_alarm(f"notion-backup: force_push by {who} at {when} — the copy's history was rewritten"))
        elif kind == "branch_deletion":
            found.append(_alarm(f"notion-backup: branch_deletion by {who} at {when} — the copy was deleted"))
        elif who != BOT:
            found.append(_alarm(f"notion-backup: {kind} by {who} at {when} — not the backup workflow"))

    if branch is None:
        if latest.get("activity_type") != "branch_deletion":
            found.append(_alarm("notion-backup is gone, and GitHub's last record of it is not a deletion"))
        return found, notes
    if latest.get("after") != branch:
        found.append(_alarm(f"notion-backup is at {_short(branch)}, but its last recorded change ends at "
                            f"{_short(str(latest.get('after')))}: a change GitHub did not record"))
    if _when(latest["timestamp"]) < cutoff:
        found.append(_alarm(f"the Notion copy stopped: the last change of notion-backup was at "
                            f"{latest.get('timestamp')}, more than 36 h ago"))
    return found, notes

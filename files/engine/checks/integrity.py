"""N1 integrity: main holds what the gate let in, and nothing else (docs/DESIGN.md §6).

Four parts, each against a named way around the gate; each runs on its own, so a part
that cannot run is reported as an error while the others still report (P5):

- **Tree.** The engine and the workflows equal the release (C1) — N1's own workflow
  included; every other path sits in a known area with no path tricks (C2's rules);
  the person's part passes C3–C6. Catches whatever reached main without a PR.
- **History.** GitHub's own record of main — the repository activity log, with server
  time, force pushes included — back to the change that made the main this run checked
  out, within WINDOW. Every change must be a PR merge made by job `merge`
  (`merged_by` = github-actions[bot]) of a head that holds a successful `gate` from a
  verified run of the checking workflow. A release the trusted contact pushes by hand
  (§5) is an alarm like any push — an expected one, which the trusted contact closes
  (owner, 23.10.2026, in the review of PR #7). Commit dates are not used: a pusher sets
  them. Changes newer than this run's checkout are left to the run they started (every
  push to main starts one).
- **Branches.** No branch but main and notion-backup changes `.github/` against its
  merge-base with main: a workflow file the bot managed to push (§13, question 3). The
  merge-base, not main itself, so an old branch is not blamed for an update main took
  after it was made (Grok Bot, Issue #5).
- **Settings.** main requires `gate` from GitHub Actions — read from the branch and
  from the rulesets, the two places GITHUB_TOKEN can see; the protection settings
  themselves need admin rights it never has, and whether admins may bypass is an
  installation choice (§7), not readable here. A config without protection (GitHub
  Free) gets a NOTE, not a green tick. The trusted contact must still be a collaborator,
  or no alarm reaches them.

Runs inside the gate on every PR check (job `check`, against the main it checked out)
and nightly (owner, 23.10.2026: the nightly run is the supplement for days without
saves). The API is passed in, so the tests run on fixtures (F1).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import (config_check, gitio, limits_check, lock_check, numbers_check, paths_check,
               text_check)
from .github import ApiError
from .report import ALARM, Finding

WINDOW = timedelta(hours=36)
BOT = "github-actions[bot]"
CHECK_WORKFLOW = ".github/workflows/check.yml"
OWN_BRANCHES = frozenset({"main", "notion-backup"})
SERVER = "https://github.com"
GATE_LINE = re.compile(r"^\S+ GATE pr=(\d+) head=([0-9a-f]{40}) base=([0-9a-f]{40}) run=(\d+)\s*$")

Parts = tuple[list[Finding], list[str]]


def _alarm(message: str) -> Finding:
    return Finding("N1", message, ALARM)


def _short(sha: str) -> str:
    return sha[:12]


# ---- tree ----------------------------------------------------------------------------

def tree(repo: Path, main: str, manifest: dict) -> Parts:
    found = []
    found += lock_check.check(repo, main, main, paths_check.PERSON, manifest)
    found += paths_check.tree_findings(repo, main)
    found += config_check.check(repo, main, main, paths_check.PERSON)
    found += numbers_check.check_tree(repo, main)
    found += text_check.check_tree(repo, main)
    found += limits_check.check(repo, main, main)
    return [_alarm(f"{f.check} {f.message}") for f in found], []


# ---- history -------------------------------------------------------------------------

def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def verify_run(api, repo: Path, main: str, run_id: str, pr: int, head: str, before: str) -> str | None:
    """None if run_id is a run of the checking workflow whose job `check` succeeded and
    logged that it checked this PR at this head against `before` — the main the PR then
    merged onto, which is job `merge`'s own condition; else why not. GitHub reports a
    `pull_request_target` run on the PR head (`head_sha` = the head, seen live in the
    review of PR #7), so the run is tied to main through the base it logged, not through
    `head_sha`; a `workflow_dispatch` run is on main itself. The workflow that ran is
    main's check.yml at `before`: an update that changes check.yml is checked by the
    one before it, as designed."""
    run = api.get(f"repos/{api.repository}/actions/runs/{run_id}", allow_404=True)
    if run is None:
        return f"run {run_id} does not exist"
    if run.get("path") != CHECK_WORKFLOW:
        return f"run {run_id} is of {run.get('path')!r}, not {CHECK_WORKFLOW}"
    if (run.get("repository") or {}).get("full_name") != api.repository:
        return f"run {run_id} belongs to another repository"
    event = run.get("event")
    if event == "pull_request_target":
        if run.get("head_sha") != head:
            return f"run {run_id} is a run for {_short(str(run.get('head_sha')))}, not for {_short(head)}"
    elif event == "workflow_dispatch":
        if run.get("head_branch") != "main" or not gitio.is_ancestor(repo, str(run.get("head_sha", "")), main):
            return f"run {run_id} was dispatched on {run.get('head_branch')!r}, not on main"
    else:
        return f"run {run_id} was started by {event!r}"
    jobs = [j for j in api.pages(f"repos/{api.repository}/actions/runs/{run_id}/jobs",
                                 {"filter": "all"}, key="jobs")
            if j.get("name") == "check" and j.get("conclusion") == "success"]
    if not jobs:
        return f"run {run_id} has no successful job «check»"
    for job in jobs:
        try:
            log = api.text(f"repos/{api.repository}/actions/jobs/{job['id']}/logs")
        except ApiError as exc:     # a deleted log is a finding about this merge, not a crash
            return f"the log of run {run_id} cannot be read ({exc})"
        for line in log.splitlines():
            m = GATE_LINE.match(line.lstrip("﻿"))
            if not (m and int(m.group(1)) == pr and m.group(2) == head and m.group(4) == str(run_id)):
                continue
            base = m.group(3)
            if base != before or not gitio.is_ancestor(repo, base, main):
                return (f"run {run_id} checked against {_short(base)}, but the PR merged onto "
                        f"{_short(before)}")
            return None
    return f"run {run_id} did not log a check of PR #{pr} at {_short(head)}"


def verify_merge(api, repo: Path, main: str, sha: str, before: str) -> list[Finding]:
    """The commit sha on main is a PR merge by job `merge` of a head with a verified
    `gate`, checked against `before`, the main it merged onto."""
    prs = [p for p in api.get(f"repos/{api.repository}/commits/{sha}/pulls") or []
           if p.get("merge_commit_sha") == sha and (p.get("base") or {}).get("ref") == "main"]
    if not prs:
        return [_alarm(f"commit {_short(sha)} on main is not the merge of a PR")]
    number = prs[0]["number"]
    pr = api.get(f"repos/{api.repository}/pulls/{number}")
    found = []
    merger = (pr.get("merged_by") or {}).get("login")
    if merger != BOT:
        found.append(_alarm(f"PR #{number} was merged by {merger}, not by job «merge»"))
    head = pr["head"]["sha"]
    url_re = re.compile(rf"^{re.escape(SERVER)}/{re.escape(api.repository)}/actions/runs/(\d+)$")
    reasons = []
    for status in api.pages(f"repos/{api.repository}/commits/{head}/statuses"):
        if (status.get("context") != "gate" or status.get("state") != "success"
                or (status.get("creator") or {}).get("login") != BOT):
            continue
        m = url_re.match(status.get("target_url") or "")
        if not m:
            reasons.append("a «gate» success that names no run of this repository")
            continue
        why = verify_run(api, repo, main, m.group(1), number, head, before)
        if why is None:
            return found
        reasons.append(why)
    detail = "; ".join(dict.fromkeys(reasons)) or "no «gate» success at all"
    found.append(_alarm(f"PR #{number} head {_short(head)} has no verified «gate»: {detail}"))
    return found


def history(api, repo: Path, main: str, now: datetime) -> Parts:
    found: list[Finding] = []
    notes: list[str] = []
    cutoff = now - WINDOW
    # Newest first, until the change that produced this run's main; what came after it
    # has a run of its own (every push to main starts one).
    newer: list[dict] = []
    to_verify: list[dict] = []
    seen_main = False
    latest = None
    for item in api.pages(f"repos/{api.repository}/activity",
                          {"ref": "refs/heads/main", "direction": "desc"}):
        latest = latest or item
        if not seen_main:
            if item.get("after") != main:
                newer.append(item)
                continue
            seen_main = True
        if _when(item["timestamp"]) < cutoff:
            break
        to_verify.append(item)
    if latest is None:
        return found, ["N1 NOTE main has no recorded changes yet"]
    if not seen_main:
        found.append(_alarm(f"main is at {_short(main)}, but its last recorded change ends at "
                            f"{_short(str(latest.get('after')))}: a change GitHub did not record"))
        return found, notes
    if newer:
        notes.append(f"N1 NOTE {len(newer)} change(s) of main after this run's checkout are checked by their own run")

    previous = None
    for change in reversed(to_verify):          # oldest first
        kind = change.get("activity_type")
        who = (change.get("actor") or {}).get("login", "?")
        if previous is not None and change.get("before") != previous:
            found.append(_alarm(f"the record of main has a gap before {change.get('timestamp')}"))
        previous = change.get("after")
        if kind == "pr_merge":
            found += verify_merge(api, repo, main, change["after"], str(change.get("before", "")))
            continue
        found.append(_alarm(f"main: {kind} by {who} at {change.get('timestamp')} — not through the gate"))
    return found, notes


# ---- branches ------------------------------------------------------------------------

def branches(repo: Path, main: str, refs_prefix: str) -> Parts:
    found = []
    out = gitio.git(repo, "for-each-ref", "--format=%(refname)%00%(objectname)", refs_prefix).decode()
    for line in out.splitlines():
        ref, sha = line.split("\0")
        name = ref[len(refs_prefix):].lstrip("/")
        if name in OWN_BRANCHES or name == "HEAD":
            continue
        base = gitio.merge_base(repo, main, sha)
        changed = gitio.git(repo, "diff", "--name-only", "-z", base, sha, "--", ".github").split(b"\0")
        changed = [p.decode("utf-8", "replace") for p in changed if p]
        if changed:
            found.append(_alarm(f"branch {name} changes {', '.join(changed[:3])}"
                                f"{' …' if len(changed) > 3 else ''} — a workflow outside main"))
    return found, []


# ---- settings ------------------------------------------------------------------------

def settings(api, config: dict, owner: str, trusted: str) -> Parts:
    found: list[Finding] = []
    notes: list[str] = []

    mode = config_check.protected_value(config, "github", "protection")
    if mode == "none":
        notes.append("N1 NOTE github.protection = none: main has no branch protection; "
                     "N1 catches mistakes, not a writer who bypasses it (§2a)")
    else:
        actions_id = api.get("apps/github-actions")["id"]
        branch = api.get(f"repos/{api.repository}/branches/main")
        protection = branch.get("protection") or {}
        required = protection.get("required_status_checks") or {}
        classic = protection.get("enabled") and any(
            c.get("context") == "gate" and c.get("app_id") == actions_id
            for c in required.get("checks") or [])
        try:
            rules = api.get(f"repos/{api.repository}/rules/branches/main", allow_404=True) or []
        except ApiError:
            rules = []              # rulesets not available on this plan: the branch decides
        ruleset = any(
            r.get("type") == "required_status_checks" and any(
                c.get("context") == "gate" and c.get("integration_id") == actions_id
                for c in (r.get("parameters") or {}).get("required_status_checks") or [])
            for r in rules)
        if not (classic or ruleset):
            found.append(_alarm("main does not require «gate» from GitHub Actions: "
                                "branch protection is off, or not visible"))

    if not trusted:
        notes.append("N1 NOTE no trusted contact: alarms reach the repository owner only")
    elif trusted.lower() != owner.lower():
        code = api.status(f"repos/{api.repository}/collaborators/{trusted}")
        if code == 404:
            found.append(_alarm(f"the trusted contact {trusted} is not a collaborator: alarms do not reach them"))
        elif code != 204:
            raise ApiError(f"GET collaborators/{trusted}: HTTP {code}")
    return found, notes


def check(api, repo: Path, main: str, manifest: dict, now: datetime, owner: str,
          refs_prefix: str = "refs/remotes/origin/") -> tuple[list[Finding], list[str], list[str]]:
    """(alarms, notes, errors). An error is a part that could not run; the other parts
    still report, and the alarm job treats an error as a night that proved nothing."""
    config = config_check.read(repo, main)
    trusted = str(config_check.protected_value(config, "alarm", "trusted_contact") or "")
    if not config_check.LOGIN_RE.fullmatch(trusted):
        trusted = ""                # C3 in the tree part reports it; never trust an odd string
    parts: list[tuple[str, Callable[[], Parts]]] = [
        ("tree", lambda: tree(repo, main, manifest)),
        ("history", lambda: history(api, repo, main, now)),
        ("branches", lambda: branches(repo, main, refs_prefix)),
        ("settings", lambda: settings(api, config, owner, trusted)),
    ]
    found: list[Finding] = []
    notes: list[str] = []
    errors: list[str] = []
    for name, part in parts:
        try:
            more, more_notes = part()
        except Exception as exc:    # noqa: BLE001 — loud, and the other parts still run (P5)
            errors.append(f"N1 {name} could not run: {type(exc).__name__}: {exc}")
            continue
        found += more
        notes += more_notes
    return found, notes, errors

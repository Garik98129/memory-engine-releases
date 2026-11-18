"""The nightly copy's job, as `.github/workflows/backup.yml` runs it: one step, one call (docs/DESIGN.md §9).

    python -m engine.backup.night worktree   # the copy's branch, as a worktree
    python -m engine.backup.night copy       # read Notion, write the copy
    python -m engine.backup.night push       # commit it and push it to notion-backup
    python -m engine.backup.night alarms     # N3, N6, the copy's own error — also after a failed step

The workflow file holds the schedule, the rights, the environment of the Notion key and the order
of the steps; what a step does is here, in main's code (`engine.checks.gate` says why). The steps
stay apart where the keys differ, and each says so itself: `copy` holds the Notion key and is never
given GitHub's; `push` and `alarms` hold GitHub's and are never given Notion's.

- `worktree` checks out `notion-backup` beside main's checkout; the first night it makes the branch
  with no common history with main, the way every git since 1.7 knows.
- `copy` runs the copy (`engine.backup.run`). Exit 1 is «N3 or N6 found something»: the copy is
  made and goes on to its commit. A crash exits 1 too; the report is the night's last write, made in
  one step, and a failed night exits 2 — so 1 counts only with a non-empty report, and any other end
  stops before the commit.
- `push` commits the worktree and pushes it, the token a header for that one git call. A copy that
  is off (`notion.backup = false`) wrote nothing and says so in its report: no commit, so no branch.
- `alarms` brings the alarm Issues in line with the report, after a failed step too: a copy that
  could not run is an alarm (P5), and nothing was committed for it. A copy that was made but not
  committed or pushed leaves a clean report behind — the step's failure is handed to the alarms, or
  the night would pass for a quiet one.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from ..checks import alarm, gate, gitio
from ..checks.gate import Step
from ..checks.integrity import BOT
from ..checks.night import passed, reported
from . import run as copying

BRANCH = "notion-backup"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
COPY, REPORT = "copy", "report.json"
NOTION = gate.NOTION
NO_WORKTREE = "the copy's branch could not be checked out: the worktree step failed"
NOT_PUSHED = "the copy was made but could not be committed or pushed to notion-backup"


def step_worktree(repo: Path, env: Mapping[str, str]) -> int:
    gate.never_given(env, gate.TOKENS + NOTION, "it only checks out the copy's branch")
    copy = Path(env["RUNNER_TEMP"]) / COPY
    remote = f"refs/remotes/origin/{BRANCH}"
    try:
        gate._git(repo, "rev-parse", "--verify", "--quiet", remote)
    except gitio.GitError:
        gate._git(repo, "worktree", "add", "--detach", str(copy), "HEAD")
        gate._git(copy, "checkout", "--quiet", "--orphan", BRANCH)
        gate._git(copy, "rm", "-r", "-q", "-f", ".")
    else:
        gate._git(repo, "worktree", "add", "--detach", str(copy), remote)
    return 0


def step_copy(repo: Path, env: Mapping[str, str], api=None, now: datetime | None = None) -> int:
    gate.never_given(env, gate.TOKENS, "it holds the Notion key")
    temp = Path(env["RUNNER_TEMP"])
    report = temp / REPORT
    code = reported(lambda: copying.main(["--repo", str(repo), "--main", gate.sha_of(env["GITHUB_SHA"], "the run's commit"),
                                          "--worktree", str(temp / COPY), "--report", str(report)], api=api, now=now))
    return 0 if passed(code, report) else (code or 1)


def off(report: Path) -> bool:
    """Whether the run said the copy is off; a report that cannot be read says nothing."""
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("off") is True


def step_push(repo: Path, env: Mapping[str, str], now: datetime | None = None) -> int:
    gate.never_given(env, NOTION, "it holds GitHub's token")
    temp = Path(env["RUNNER_TEMP"])
    if off(temp / REPORT):
        print("The nightly copy is off: nothing to commit")
        return 0
    copy = temp / COPY
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%MZ")
    gate._git(copy, "add", "-A")
    gate._git(copy, "-c", f"user.name={BOT}", "-c", f"user.email={BOT_EMAIL}",
              "commit", "--quiet", "--allow-empty", "-m", f"Notion copy {stamp}")
    gate._git(copy, "push", "--quiet", "origin", f"HEAD:refs/heads/{BRANCH}", env=gate.auth(env["GH_TOKEN"]))
    return 0


def step_alarms(repo: Path, env: Mapping[str, str], api=None) -> int:
    gate.never_given(env, NOTION, "it holds GitHub's token")
    temp = Path(env["RUNNER_TEMP"])
    errors: list[str] = []
    if env["WORKTREE"] == "failure":
        errors += ["--error", NO_WORKTREE]
    if env["PUSH"] == "failure":
        errors += ["--error", NOT_PUSHED]
    return alarm.main(["--repo", str(repo), "--main", gate.sha_of(env["GITHUB_SHA"], "the run's commit"),
                       "--report", str(temp / REPORT), "--run-url", gate.run_url(env), "--what", "backup", *errors],
                      api=api)


STEPS: dict[str, Step] = {
    "worktree": Step(step_worktree, ()),
    "copy": Step(step_copy, NOTION),
    "push": Step(step_push, ("GH_TOKEN",)),
    "alarms": Step(step_alarms, ("GH_TOKEN", "WORKTREE", "PUSH")),
}


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None, **seams) -> int:
    return gate.main_of(STEPS, __doc__, argv, env, **seams)


if __name__ == "__main__":
    sys.exit(main())

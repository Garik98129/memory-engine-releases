"""The nightly watch's job, as `.github/workflows/watch.yml` runs it: one step, one call (docs/DESIGN.md §6).

    python -m engine.checks.night manifest  # main's release manifest, fetched with no token
    python -m engine.checks.night watch     # the watch of main
    python -m engine.checks.night alarms    # their alarms — also after a failed step

The workflow file holds the schedule, the rights and the order of the steps; what a step does is
here, in main's code (`engine.checks.gate` says why).

- `manifest` fetches main's release manifest from the public repository of releases. It is given
  no token and refuses one: the one request this job sends to a host that is not GitHub's API
  leaves from a step that holds nothing to lose.
- `watch` fetches every branch as data — the token a header for that one git call — and runs the
  checks of main (`engine.checks.watch`). The watch's exit 1 is «found something»: whether that is
  red is the alarms' call — a finding someone closed as known is green. A crash exits 1 too, but
  the report is the watch's last write, made in one step, so 1 counts only with a non-empty
  report; an error (2) or a crash stays red in this step.
- `alarms` brings the alarm Issues in line with the report. It runs after a failed step too: a
  watch that could not run left no report, and that is an alarm (P5).
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from . import alarm, gate, paths_check, watch
from .gate import Step

MANIFEST, REPORT = "manifest.json", "report.json"


def fetch_branches(repo: Path, token: str) -> None:
    gate._git(repo, "fetch", "--no-tags", "--quiet", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*",
              env=gate.auth(token))


def passed(code: int, report: Path) -> bool:
    """Whether a run that writes a report goes on: clean, or «found something» (1) with the
    report it wrote — its last write, so a crash, which exits 1 too, leaves none or an empty one."""
    if code == 0:
        return True
    try:
        return code == 1 and report.stat().st_size > 0
    except OSError:
        return False


def reported(run: Callable[[], int]) -> int:
    """The exit code of a run in this process, as `python3 -m` would end it: a crash is 1."""
    try:
        return run()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:    # noqa: BLE001
        print(f"CRASH {type(exc).__name__}: {gate.one_line(exc)}")
        traceback.print_exc()
        return 1


def step_manifest(repo: Path, env: Mapping[str, str], get: Callable[[str], bytes] = gate.fetch_public) -> int:
    gate.never_given(env, gate.TOKENS + gate.NOTION, "it asks a host that is not GitHub's API")
    main = gate.sha_of(gate._git(repo, "rev-parse", "HEAD"), "the checkout")
    (Path(env["RUNNER_TEMP"]) / MANIFEST).write_bytes(gate.manifest_at(repo, main, main, paths_check.PERSON, get))
    return 0


def step_watch(repo: Path, env: Mapping[str, str], api=None, now: datetime | None = None,
               fetch: Callable[[Path, str], None] = fetch_branches, branches: str | None = None) -> int:
    gate.never_given(env, gate.NOTION, "it holds GitHub's token")
    temp = Path(env["RUNNER_TEMP"])
    fetch(repo, env["GH_TOKEN"])
    main = gate.sha_of(gate._git(repo, "rev-parse", "HEAD"), "the checkout")
    report = temp / REPORT
    where = [] if branches is None else ["--branches", branches]     # the tests' fixtures keep theirs elsewhere
    code = reported(lambda: watch.main(["--repo", str(repo), "--main", main, "--manifest", str(temp / MANIFEST),
                                        "--report", str(report), *where], api=api, now=now))
    return 0 if passed(code, report) else (code or 1)


def step_alarms(repo: Path, env: Mapping[str, str], api=None) -> int:
    gate.never_given(env, gate.NOTION, "it holds GitHub's token")
    temp = Path(env["RUNNER_TEMP"])
    return alarm.main(["--repo", str(repo), "--main", gate.sha_of(env["GITHUB_SHA"], "the run's commit"),
                       "--report", str(temp / REPORT), "--run-url", gate.run_url(env)], api=api)


STEPS: dict[str, Step] = {
    "manifest": Step(step_manifest, ()),
    "watch": Step(step_watch, ("GH_TOKEN",)),
    "alarms": Step(step_alarms, ("GH_TOKEN",)),
}


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None, **seams) -> int:
    return gate.main_of(STEPS, __doc__, argv, env, **seams)


if __name__ == "__main__":
    sys.exit(main())

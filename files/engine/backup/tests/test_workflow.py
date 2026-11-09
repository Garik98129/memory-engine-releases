"""The step «Copy Notion» of engine/workflows/backup.yml, run the way GitHub runs it — its own
text, in `bash -e`, the shell of a step with no `shell:` — with `python3` replaced by a
stand-in that exits as told (Issue #18, item 42). Exit 1 is «N3 or N6 found something» and
goes on to the commit; a crash exits 1 too, so 1 counts only with a non-empty report.
`copy_step` is also what the scenario nights (engine/checks/tests/test_scenarios.py) decide
their commit with; `run_step` runs another step the same way — one whose `run: >-` calls
`python3 -m …` (the nightly watch's step «N1, N5», Issue #18, item 1)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"
WORKFLOW = WORKFLOWS / "backup.yml"

STAND_IN = """#!/bin/sh
# python3 -m engine...: say it ran, write the report if told to, exit as told
echo "$*" > "$RUNNER_TEMP/ran"
report=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--report" ]; then report="$2"; fi
  shift
done
case "$REPORT" in
  full) printf '{"N3": [], "N6": [], "error": [], "skipped": []}' > "$report" ;;
  empty) : > "$report" ;;
esac
exit "$EXIT_CODE"
"""


def step_run(name: str, workflow: Path = WORKFLOW) -> str:
    """The `run: >-` of the step, folded into the one line the shell gets (stdlib only, no
    YAML library: the block holds plain lines, so folding joins them with spaces)."""
    lines = workflow.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {name}")
    step = len(lines[start]) - len(lines[start].lstrip())
    i = start + 1
    while lines[i].strip() != "run: >-":
        if lines[i].strip().startswith("- ") and len(lines[i]) - len(lines[i].lstrip()) <= step:
            raise AssertionError(f"step {name!r} has no `run: >-`")
        i += 1
    key = len(lines[i]) - len(lines[i].lstrip())
    body = []
    for line in lines[i + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= key:
            break
        body.append(line.strip())
    return " ".join(part for part in body if part)


def copy_step(runner_temp: Path, exit_code: int, report: str = "") -> int:
    """The step's exit code — 0: the commit step runs; anything else: the job stops there.
    `report`: «full» or «empty» — the stand-in writes it; «» — what is in `runner_temp` stays."""
    return run_step(runner_temp, exit_code, report)


def run_step(runner_temp: Path, exit_code: int, report: str = "", workflow: Path = WORKFLOW,
             name: str = "Copy Notion", module: str = "engine.backup.run") -> int:
    """The exit code of a step's own line from `workflow`, in `bash -e`, with `python3` a
    stand-in that exits `exit_code` — 0: the step passed; anything else: it failed."""
    stand_in = runner_temp / "bin" / "python3"
    stand_in.parent.mkdir(exist_ok=True)
    stand_in.write_text(STAND_IN, encoding="utf-8")
    stand_in.chmod(0o755)
    env = {"PATH": f"{stand_in.parent}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
           "RUNNER_TEMP": str(runner_temp), "MAIN": "0" * 40, "EXIT_CODE": str(exit_code), "REPORT": report}
    done = subprocess.run(["bash", "-e", "-c", step_run(name, workflow)], env=env, capture_output=True, text=True)
    ran = runner_temp / "ran"
    if not ran.exists() or f"-m {module}" not in ran.read_text():
        raise AssertionError(f"the step did not run the stand-in: {done.stderr}")
    return done.returncode


class CopyStep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.nights = 0

    def tearDown(self):
        self.tmp.cleanup()

    def step(self, exit_code: int, report: str) -> int:
        self.nights += 1
        temp = Path(self.tmp.name) / f"runner-{self.nights}"     # $RUNNER_TEMP is new every job
        temp.mkdir()
        return copy_step(temp, exit_code, report)

    def test_the_step_is_the_one_that_runs_the_copy_and_checks_its_report(self):
        command = step_run("Copy Notion")
        self.assertTrue(command.startswith("python3 -m engine.backup.run "), command)
        self.assertEqual(command.count('"$RUNNER_TEMP/report.json"'), 2)   # the file it writes is the file it checks

    def test_a_clean_night_and_a_night_with_findings_go_on_to_the_commit(self):
        self.assertEqual(self.step(0, "full"), 0)
        self.assertEqual(self.step(1, "full"), 0)

    def test_a_crash_stops_before_the_commit(self):
        # Issue #18, item 42: an uncaught exception exits 1 — with no report, or with the
        # empty file a full disk leaves when a report write fails half-way.
        self.assertNotEqual(self.step(1, ""), 0)
        self.assertNotEqual(self.step(1, "empty"), 0)

    def test_a_copy_that_could_not_be_made_stops_before_the_commit(self):
        for report in ("full", "empty", ""):
            with self.subTest(report=report):
                self.assertNotEqual(self.step(2, report), 0)


if __name__ == "__main__":
    unittest.main()

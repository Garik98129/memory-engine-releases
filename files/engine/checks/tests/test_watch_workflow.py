"""The nightly watch's workflow (engine/workflows/watch.yml), run the way GitHub runs it.

The job was red while every finding was acknowledged — the step «N1, N5»
failed on the watch's exit 1 («found something») whatever the alarm step decided. Now 1 counts
only with a non-empty report, so the alarm step decides the colour; an error (2) or a crash
(1 with no report) stays red in the step itself. The watch and the copy share one
concurrency group, and no waiting run is cancelled by a newer one."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from engine.backup.tests.test_workflow import WORKFLOWS, run_step, step_run

WATCH = WORKFLOWS / "watch.yml"


def watch_step(runner_temp: Path, exit_code: int, report: str = "") -> int:
    return run_step(runner_temp, exit_code, report, WATCH, "N1, N5", "engine.checks.watch")


def concurrency(workflow: Path) -> dict[str, str]:
    """The top-level `concurrency:` block as key → value (comments left out)."""
    lines = workflow.read_text(encoding="utf-8").splitlines()
    start = lines.index("concurrency:")
    out = {}
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break
        m = re.match(r"\s+([a-z-]+):\s*(\S+)", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


class WatchStep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = 0

    def tearDown(self):
        self.tmp.cleanup()

    def step(self, exit_code: int, report: str) -> int:
        self.runs += 1
        temp = Path(self.tmp.name) / f"runner-{self.runs}"          # $RUNNER_TEMP is new every job
        temp.mkdir()
        return watch_step(temp, exit_code, report)

    def test_the_step_runs_the_watch_and_checks_the_report_it_writes(self):
        command = step_run("N1, N5", WATCH)
        self.assertTrue(command.startswith("python3 -m engine.checks.watch "), command)
        self.assertEqual(command.count('"$RUNNER_TEMP/report.json"'), 2)

    def test_clean_and_found_something_leave_the_colour_to_the_alarm_step(self):
        self.assertEqual(self.step(0, "full"), 0)
        self.assertEqual(self.step(1, "full"), 0)

    def test_an_error_or_a_crash_is_red_in_the_step_itself(self):
        self.assertNotEqual(self.step(2, "full"), 0)                  # the checks could not run
        self.assertNotEqual(self.step(1, ""), 0)                      # a crash before the report
        self.assertNotEqual(self.step(1, "empty"), 0)                 # an empty report counts as none


class OneGroup(unittest.TestCase):
    def test_the_copy_and_the_watch_wait_for_each_other_and_a_newer_run_cancels_none(self):
        watch, backup = concurrency(WATCH), concurrency(WORKFLOWS / "backup.yml")
        self.assertEqual(watch, backup)
        self.assertEqual(watch, {"group": "notion-copy", "cancel-in-progress": "false", "queue": "max"})


if __name__ == "__main__":
    unittest.main()

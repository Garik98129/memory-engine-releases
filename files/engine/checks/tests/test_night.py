"""The nightly watch's steps (engine/checks/night.py), as watch.yml calls them, on fixtures (F1).

The job was red while every finding was acknowledged — the step «The watch of main» failed on the watch's
exit 1 («found something») whatever the alarm step decided. So 1 counts only with a non-empty
report, and the alarm step decides the colour; an error (2) or a crash (1 with no report) stays
red in the step itself. The rule was a line of shell in the workflow file; it is `night.passed`
now, and the copy's step shares it."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from engine.checks import gate, night
from engine.checks.tests.fakeapi import REPO, FakeApi
from engine.checks.tests.helpers import Repo

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
FULL = '{"N1": [], "N5": [], "error": [], "skipped": []}'


class Passed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.report = Path(self._tmp.name) / "report.json"

    def test_clean_and_found_something_leave_the_colour_to_the_alarm_step(self):
        self.assertTrue(night.passed(0, self.report), "clean: the report is not asked for")
        self.report.write_text(FULL)
        self.assertTrue(night.passed(0, self.report))
        self.assertTrue(night.passed(1, self.report))

    def test_an_error_or_a_crash_is_red_in_the_step_itself(self):
        self.assertFalse(night.passed(1, self.report), "a crash before the report")
        self.report.write_text("")
        self.assertFalse(night.passed(1, self.report), "an empty report counts as none")
        self.report.write_text(FULL)
        for code in (2, 3, -1, 130):
            self.assertFalse(night.passed(code, self.report), code)

    def test_a_crash_in_this_process_ends_as_python_would_end_it(self):
        def boom():
            raise RuntimeError("boom")

        def usage():
            raise SystemExit(2)

        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(night.reported(boom), 1)
        self.assertEqual(out.getvalue(), "CRASH RuntimeError: boom\n", "one line that names it, for whoever reads the run")
        self.assertIn("RuntimeError: boom", err.getvalue())
        self.assertEqual(night.reported(usage), 2)
        self.assertEqual(night.reported(lambda: 0), 0)
        for code in ("usage", None):                # an exit that names no failure's code: never read as success
            def odd(code=code):
                raise SystemExit(code)
            self.assertEqual(night.reported(odd), 1, code)


class Steps(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.close)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.temp = Path(self._tmp.name)
        self.api = FakeApi()
        self.fetched: list = []

    def env(self, **given) -> dict[str, str]:
        return {"GITHUB_REPOSITORY": REPO, "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "9",
                "GITHUB_SHA": self.repo.base, "GITHUB_OUTPUT": str(self.temp / "output"),
                "RUNNER_TEMP": str(self.temp), "GH_TOKEN": "t", **given}

    def run_step(self, step: str, env: dict | None = None, **seams) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = night.main([step], env=env or self.env(), repo=self.repo.path, **seams)
        return code, out.getvalue()

    def tokenless(self, **given) -> dict[str, str]:
        return {name: value for name, value in self.env(**given).items() if name != "GH_TOKEN" or name in given}

    def manifest(self, url: str) -> bytes:
        self.fetched.append(url)
        return json.dumps(self.repo.manifest(self.repo.base)).encode()

    def watch(self, watched) -> tuple[int, str, list]:
        argv_seen: list = []

        def stand_in(argv, api=None, now=None):
            argv_seen.extend(argv)
            code, report = watched
            if report is not None:
                Path(argv[argv.index("--report") + 1]).write_text(report)
            if isinstance(code, BaseException):
                raise code
            return code

        with mock.patch.object(night.watch, "main", stand_in):
            code, out = self.run_step("watch", api=self.api, now=NOW,
                                      fetch=lambda repo, token: self.fetched.append(("branches", token)))
        return code, out, argv_seen

    def test_mains_manifest_is_fetched_by_a_step_that_holds_no_token_and_refuses_one(self):
        code, out = self.run_step("manifest", self.tokenless(), get=self.manifest)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.fetched), 1)
        self.assertTrue(self.fetched[0].endswith("/refs/tags/v0.1.0/release-manifest.json"), self.fetched[0])
        self.assertEqual(json.loads((self.temp / "manifest.json").read_text()), self.repo.manifest(self.repo.base))
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            self.fetched.clear()
            code, out = self.run_step("manifest", self.tokenless(**{name: "t"}), get=self.manifest)
            self.assertEqual((code, self.fetched), (1, []), out)
            self.assertIn(f"never given {name}", out)

    def test_the_step_fetches_the_branches_then_runs_the_watch_on_the_manifest_it_was_left(self):
        code, out, argv = self.watch((0, FULL))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.fetched, [("branches", "t")], "this step asks nobody but GitHub")
        self.assertEqual(argv[2:], ["--main", self.repo.base, "--manifest", str(self.temp / "manifest.json"),
                                    "--report", str(self.temp / "report.json")])

    def test_its_colour_follows_the_rule(self):
        for watched, red in (((0, FULL), False), ((1, FULL), False), ((2, FULL), True), ((1, None), True),
                             ((1, ""), True), ((RuntimeError("boom"), None), True), ((SystemExit(2), None), True)):
            (self.temp / "report.json").unlink(missing_ok=True)
            code, out, _argv = self.watch(watched)
            self.assertEqual(code != 0, red, f"{watched}: {out}")

    def test_a_manifest_that_cannot_be_fetched_is_a_red_step_and_the_night_leaves_no_report(self):
        def gone(url):
            raise gate.Stop(f"GET {url}: HTTP 404")

        code, out = self.run_step("manifest", self.tokenless(), get=gone)
        self.assertEqual(code, 1, out)
        self.assertIn("release-manifest.json: HTTP 404", out)
        self.assertFalse((self.temp / "manifest.json").exists())
        self.assertFalse((self.temp / "report.json").exists(), "the watch is skipped; the alarm step says no report")

    def test_the_branches_are_fetched_with_the_token_as_a_header_only(self):
        with mock.patch.object(gate.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=b"", stderr=b"")
            night.fetch_branches(self.repo.path, "secret")
        call = run.call_args
        self.assertEqual(call.args[0][3:], ["fetch", "--no-tags", "--quiet", "--prune", "origin",
                                           "+refs/heads/*:refs/remotes/origin/*"])
        self.assertNotIn("secret", " ".join(call.args[0]))
        self.assertIn("eC1hY2Nlc3MtdG9rZW46c2VjcmV0", call.kwargs["env"]["GIT_CONFIG_VALUE_0"])

    def test_the_steps_that_hold_githubs_token_refuse_the_copys_key_and_a_commit_that_is_none(self):
        for step in ("watch", "alarms"):
            code, out = self.run_step(step, self.env(NOTION_TOKEN="n"), api=self.api)
            self.assertEqual(code, 1, out)
            self.assertIn("never given NOTION_TOKEN", out)
        with mock.patch.object(night.alarm, "main", lambda argv, api=None: self.fail("the alarms ran")):
            code, out = self.run_step("alarms", self.env(GITHUB_SHA="main"), api=self.api)
        self.assertEqual(code, 1, out)
        self.assertIn("the run's commit is not a commit", out)

    def test_a_step_run_as_the_workflow_runs_it_ends_the_process_with_its_code(self):
        root = Path(night.__file__).resolve().parents[2]
        clean = {"PATH": os.environ["PATH"], **self.tokenless()}

        def run(*argv, **more):
            done = subprocess.run([sys.executable, "-B", "-m", "engine.checks.night", *argv], cwd=root,
                                  env={**clean, **more}, capture_output=True, text=True)
            return done.returncode, done.stdout

        self.assertEqual(run()[0], 2)
        self.assertEqual(run("watch"), (2, "ERROR the step «watch» was not given GH_TOKEN\n"))
        code, out = run("manifest", GH_TOKEN="t")
        self.assertEqual((code, out.startswith("ERROR this step is never given GH_TOKEN")), (1, True), out)

    def test_the_alarms_run_on_the_runs_commit_with_the_runs_address(self):
        seen: list = []
        with mock.patch.object(night.alarm, "main", lambda argv, api=None: seen.extend(argv) or 1):
            code, out = self.run_step("alarms", api=self.api)
        self.assertEqual(code, 1, out)
        self.assertEqual(seen[2:], ["--main", self.repo.base, "--report", str(self.temp / "report.json"),
                                    "--run-url", f"https://github.com/{REPO}/actions/runs/9"])

    def test_a_night_whose_watch_left_no_report_is_an_alarm(self):
        """After a failed step the alarm step still runs (`if: always()`): no report is an alarm."""
        self.api.lists[f"repos/{REPO}/issues"] = []
        with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY_OWNER": "owner"}):
            code, out = self.run_step("alarms", api=self.api)
        self.assertEqual(code, 1, out)
        [issue] = [body for method, path, body in self.api.writes if path.endswith("/issues")]
        self.assertEqual(issue["title"], "Alarm: the checks of main could not run")
        self.assertIn("no readable report from the checks", issue["body"])


if __name__ == "__main__":
    unittest.main()

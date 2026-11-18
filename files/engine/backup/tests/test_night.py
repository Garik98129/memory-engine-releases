"""The nightly copy's steps (engine/backup/night.py), as backup.yml calls them, on fixtures (F1).

Exit 1 of the copy is «N3 or N6 found something» and goes on to the commit; a crash exits 1 too,
so 1 counts only with a non-empty report — the rule the watch's step has (`night.passed`). The
commit and the push were shell in the workflow file; here they run against a bare repository
that stands in for GitHub. What the fixtures cannot see: the runner's checkout, which brings
`refs/remotes/origin/notion-backup` when the branch exists — a fetch stands in for it."""
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

from engine.backup import night
from engine.checks import gate
from engine.checks.tests.fakeapi import BOT, REPO, FakeApi
from engine.checks.tests.helpers import Repo, _git

NOW = datetime(2026, 10, 23, 1, 11, tzinfo=timezone.utc)
FULL = '{"N3": [], "N6": [], "error": [], "skipped": []}'


class Steps(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.close)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.github = Path(self._tmp.name) / "github.git"
        _git(Path(self._tmp.name), "init", "-q", "--bare", str(self.github))
        _git(self.repo.path, "remote", "add", "origin", str(self.github))
        _git(self.repo.path, "push", "-q", "origin", "main")
        self.nights = 0
        self.job()

    def job(self) -> None:
        """A new job: its own RUNNER_TEMP, and a checkout that has fetched every branch."""
        self.nights += 1
        self.temp = Path(self._tmp.name) / f"runner-{self.nights}"
        self.temp.mkdir()
        _git(self.repo.path, "worktree", "prune")
        _git(self.repo.path, "fetch", "-q", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*")

    def env(self, **given) -> dict[str, str]:
        return {"GITHUB_REPOSITORY": REPO, "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "9",
                "GITHUB_SHA": self.repo.base, "GITHUB_OUTPUT": str(self.temp / "output"),
                "RUNNER_TEMP": str(self.temp), **given}

    def run_step(self, step: str, env: dict[str, str], **seams) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = night.main([step], env=env, repo=self.repo.path, **seams)
        return code, out.getvalue()

    def pushed(self, *args: str) -> str:
        return _git(self.github, *args)


class Worktree(Steps):
    def test_the_first_night_makes_a_branch_with_no_history_and_no_file(self):
        code, out = self.run_step("worktree", self.env())
        self.assertEqual(code, 0, out)
        copy = self.temp / "copy"
        self.assertEqual([p.name for p in copy.iterdir() if p.name != ".git"], [])
        self.assertEqual(_git(copy, "symbolic-ref", "HEAD"), "refs/heads/notion-backup")
        self.assertNotEqual(subprocess.run(["git", "-C", str(copy), "rev-parse", "--verify", "-q", "HEAD"],
                                           capture_output=True).returncode, 0, "no commit yet: nothing shared with main")

    def test_a_later_night_checks_out_the_copy_as_github_holds_it(self):
        self.run_step("worktree", self.env())
        (self.temp / "copy" / "page.md").write_text("one\n")
        self.assertEqual(self.run_step("push", self.env(GH_TOKEN="t"), now=NOW)[0], 0)
        first = self.pushed("rev-parse", "refs/heads/notion-backup")
        self.job()
        code, out = self.run_step("worktree", self.env())
        self.assertEqual(code, 0, out)
        self.assertEqual(_git(self.temp / "copy", "rev-parse", "HEAD"), first)
        self.assertEqual((self.temp / "copy" / "page.md").read_text(), "one\n")

    def test_the_copy_is_checked_out_as_github_holds_it_not_as_a_branch_of_the_checkout_does(self):
        self.run_step("worktree", self.env())
        (self.temp / "copy" / "page.md").write_text("one\n")
        self.run_step("push", self.env(GH_TOKEN="t"), now=NOW)
        on_github = self.pushed("rev-parse", "refs/heads/notion-backup")
        self.job()
        _git(self.repo.path, "update-ref", "refs/heads/notion-backup", self.repo.base)   # a stale branch of the same name
        code, out = self.run_step("worktree", self.env())
        self.assertEqual(code, 0, out)
        self.assertEqual(_git(self.temp / "copy", "rev-parse", "HEAD"), on_github)

    def test_a_step_run_as_the_workflow_runs_it_ends_the_process_with_its_code(self):
        root = Path(night.__file__).resolve().parents[2]
        clean = {"PATH": os.environ["PATH"], **self.env()}

        def run(*argv, **more):
            done = subprocess.run([sys.executable, "-B", "-m", "engine.backup.night", *argv], cwd=root,
                                  env={**clean, **more}, capture_output=True, text=True)
            return done.returncode, done.stdout

        self.assertEqual(run()[0], 2)
        self.assertEqual(run("alarms"), (2, "ERROR the step «alarms» was not given GH_TOKEN, WORKTREE, PUSH\n"))
        code, out = run("copy", NOTION_TOKEN="n", GH_TOKEN="t")
        self.assertEqual((code, out.startswith("ERROR this step is never given GH_TOKEN")), (1, True), out)

    def test_it_holds_no_key(self):
        for name in ("GH_TOKEN", "NOTION_TOKEN"):
            code, out = self.run_step("worktree", self.env(**{name: "t"}))
            self.assertEqual(code, 1, out)
            self.assertIn(f"never given {name}", out)


class Copy(Steps):
    def copy(self, made, **given) -> tuple[int, str, list]:
        argv_seen: list = []

        def stand_in(argv, api=None, now=None):
            argv_seen.extend(argv)
            code, report = made
            if report is not None:
                Path(argv[argv.index("--report") + 1]).write_text(report)
            if isinstance(code, BaseException):
                raise code
            return code

        with mock.patch.object(night.copying, "main", stand_in):
            code, out = self.run_step("copy", self.env(NOTION_TOKEN="n", **given), now=NOW)
        return code, out, argv_seen

    def test_the_step_runs_the_copy_on_the_runs_commit_into_the_worktree(self):
        code, out, argv = self.copy((0, FULL))
        self.assertEqual(code, 0, out)
        self.assertEqual(argv[2:], ["--main", self.repo.base, "--worktree", str(self.temp / "copy"),
                                    "--report", str(self.temp / "report.json")], "the file it writes is the file it checks")

    def test_a_clean_night_and_a_night_with_findings_go_on_to_the_commit(self):
        self.assertEqual(self.copy((0, FULL))[0], 0)
        self.assertEqual(self.copy((1, FULL))[0], 0)

    def test_a_crash_stops_before_the_commit(self):
        # An uncaught exception exits 1 — with no report, or with the empty file a full disk
        # leaves when a report write fails half-way.
        for made in ((1, None), (1, ""), (RuntimeError("boom"), None), (RuntimeError("boom"), ""),
                     (SystemExit(2), FULL)):
            (self.temp / "report.json").unlink(missing_ok=True)
            self.assertNotEqual(self.copy(made)[0], 0, made)

    def test_a_copy_that_could_not_be_made_stops_before_the_commit(self):
        for report in (FULL, "", None):
            (self.temp / "report.json").unlink(missing_ok=True)
            self.assertNotEqual(self.copy((2, report))[0], 0, report)

    def test_the_step_that_holds_the_notion_key_is_never_given_githubs(self):
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            code, out, argv = self.copy((0, FULL), **{name: "t"})
            self.assertEqual((code, argv), (1, []), out)
            self.assertIn(f"never given {name}", out)


class Push(Steps):
    def setUp(self):
        super().setUp()
        self.run_step("worktree", self.env())
        (self.temp / "copy" / "page.md").write_text("one\n")
        (self.temp / "report.json").write_text(FULL)

    def test_the_copy_is_committed_by_the_workflow_and_pushed_to_its_branch(self):
        code, out = self.run_step("push", self.env(GH_TOKEN="t"), now=NOW)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.pushed("log", "-1", "--format=%an <%ae>|%cn|%s|%P", "refs/heads/notion-backup"),
                         f"{BOT} <41898282+github-actions[bot]@users.noreply.github.com>|{BOT}|"
                         f"Notion copy 2026-10-23T01:11Z|")
        self.assertEqual(self.pushed("show", "refs/heads/notion-backup:page.md"), "one")
        self.assertEqual(self.pushed("for-each-ref", "--format=%(refname)"), "refs/heads/main\nrefs/heads/notion-backup")

    def test_a_quiet_notion_still_moves_the_branch(self):
        self.run_step("push", self.env(GH_TOKEN="t"), now=NOW)
        first = self.pushed("rev-parse", "refs/heads/notion-backup")
        self.job()
        self.run_step("worktree", self.env())
        (self.temp / "report.json").write_text(FULL)
        code, out = self.run_step("push", self.env(GH_TOKEN="t"), now=NOW)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.pushed("rev-parse", "refs/heads/notion-backup^"), first, "an empty commit: N2 sees the copy alive")

    def test_an_off_copy_commits_nothing(self):
        (self.temp / "report.json").write_text(json.dumps(
            {"N3": [], "N6": [], "error": [], "skipped": ["N3", "N6"], "off": True}))
        code, out = self.run_step("push", self.env(GH_TOKEN="t"), now=NOW)
        self.assertEqual(code, 0, out)
        self.assertIn("The nightly copy is off: nothing to commit", out)
        self.assertEqual(self.pushed("for-each-ref", "--format=%(refname)"), "refs/heads/main", "no branch appears")

    def test_only_the_runs_own_word_turns_the_commit_off(self):
        for report in (FULL, '{"off": "true"}', '{"off": 1}', '["off"]', "not json", ""):
            (self.temp / "report.json").write_text(report)
            self.assertFalse(night.off(self.temp / "report.json"), report)
        (self.temp / "report.json").unlink()
        self.assertFalse(night.off(self.temp / "report.json"))

    def test_a_push_github_refuses_is_a_red_step(self):
        _git(self.repo.path, "remote", "set-url", "origin", str(self.github) + "-gone")
        code, out = self.run_step("push", self.env(GH_TOKEN="t"), now=NOW)
        self.assertEqual(code, 2, out)
        self.assertTrue(out.startswith("ERROR the step «push» could not run: GitError: git push"), out)

    def test_the_token_is_a_header_for_the_push_only_and_the_notion_key_is_refused(self):
        with mock.patch.object(gate.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=b"", stderr=b"")
            self.run_step("push", self.env(GH_TOKEN="secret"), now=NOW)
        add, commit, push = run.call_args_list
        for call in (add, commit):
            self.assertNotIn("eC1hY2Nlc3MtdG9rZW46c2VjcmV0", " ".join(map(str, call.kwargs["env"].values())))
            self.assertNotIn("secret", " ".join(call.args[0]))
        self.assertEqual(push.args[0][3:], ["push", "--quiet", "origin", "HEAD:refs/heads/notion-backup"])
        self.assertNotIn("secret", " ".join(push.args[0]))
        self.assertIn("eC1hY2Nlc3MtdG9rZW46c2VjcmV0", push.kwargs["env"]["GIT_CONFIG_VALUE_0"])
        code, out = self.run_step("push", self.env(GH_TOKEN="t", NOTION_TOKEN="n"), now=NOW)
        self.assertEqual(code, 1, out)
        self.assertIn("never given NOTION_TOKEN", out)


class Alarms(Steps):
    def alarms(self, **given) -> tuple[int, str, list]:
        seen: list = []
        with mock.patch.object(night.alarm, "main", lambda argv, api=None: seen.extend(argv) or 0):
            code, out = self.run_step("alarms", self.env(GH_TOKEN="t", **given), api=FakeApi())
        return code, out, seen

    def test_a_quiet_night_hands_over_the_report_and_nothing_more(self):
        code, out, argv = self.alarms(WORKTREE="success", PUSH="success")
        self.assertEqual(code, 0, out)
        self.assertEqual(argv[2:], ["--main", self.repo.base, "--report", str(self.temp / "report.json"), "--run-url",
                                    f"https://github.com/{REPO}/actions/runs/9", "--what", "backup"])

    def test_a_step_that_failed_is_handed_to_the_alarms(self):
        """A copy that was made but not committed or pushed leaves a clean report behind: without
        this the night would pass for a quiet one."""
        _c, _o, argv = self.alarms(WORKTREE="failure", PUSH="skipped")
        self.assertEqual(argv[-2:], ["--error", "the copy's branch could not be checked out: the worktree step failed"])
        _c, _o, argv = self.alarms(WORKTREE="success", PUSH="failure")
        self.assertEqual(argv[-2:], ["--error", "the copy was made but could not be committed or pushed to notion-backup"])
        for other in ("success", "skipped", "cancelled", ""):
            self.assertNotIn("--error", self.alarms(WORKTREE="success", PUSH=other)[2], other)
            self.assertNotIn("--error", self.alarms(WORKTREE=other, PUSH="success")[2], other)

    def test_the_alarms_run_on_a_commit_and_nothing_else(self):
        code, out, argv = self.alarms(WORKTREE="success", PUSH="success", GITHUB_SHA="main")
        self.assertEqual((code, argv), (1, []), out)
        self.assertIn("the run's commit is not a commit", out)

    def test_it_is_never_given_the_notion_key(self):
        code, out, argv = self.alarms(WORKTREE="success", PUSH="success", NOTION_TOKEN="n")
        self.assertEqual((code, argv), (1, []), out)


if __name__ == "__main__":
    unittest.main()

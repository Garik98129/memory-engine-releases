"""The nightly copy, off (`notion.backup = false`, docs/DESIGN_PHASE3.md §7): the run reads no
Notion and writes nothing, the workflow commits nothing, the watch skips N2 — each said in a
line, never a silent green (P5). A config without the key keeps the copy on: every
installation made before the key, the pilot's included."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from engine.backup import run
from engine.checks import config_check, watch
from engine.checks.paths_check import PERSON
from engine.checks.tests.fakeapi import REPO, FakeApi
from engine.checks.tests.helpers import BASE_FILES, COMMITTED, VALID_CONFIG, Repo

OFF = VALID_CONFIG + b"\n[notion]\nbackup = false\n"
WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "backup.yml"
NOW = datetime.fromisoformat(COMMITTED) + timedelta(hours=1)


class NoNotion:
    """Any use of Notion fails the test: an off copy reads nothing."""

    def __getattr__(self, name):
        raise AssertionError(f"Notion was used ({name}) while the copy is off")


class Run(unittest.TestCase):
    def test_off_reads_and_writes_nothing_and_says_so(self):
        repo = Repo({**BASE_FILES, "user/config.toml": OFF})
        with tempfile.TemporaryDirectory() as tmp:
            worktree, report = Path(tmp) / "copy", Path(tmp) / "report.json"
            worktree.mkdir()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run.main(["--repo", str(repo.path), "--main", repo.base, "--worktree", str(worktree),
                                 "--report", str(report)], api=NoNotion(), now=NOW)
            repo.close()
            self.assertEqual(code, 0, out.getvalue())
            self.assertEqual(json.loads(report.read_text(encoding="utf-8")),
                             {"N3": [], "N6": [], "error": [], "skipped": ["N3", "N6"], "off": True})
            self.assertEqual(list(worktree.iterdir()), [], "nothing written")
            self.assertIn("NOTE the nightly copy is off (notion.backup = false)", out.getvalue())

    def test_a_revision_it_cannot_read_is_the_night_s_own_error(self):
        """The pre-read of the switch fails like the copy's own settings: exit 2, the error in the
        report — never a crash without one."""
        repo = Repo({**BASE_FILES, "user/config.toml": OFF})
        with tempfile.TemporaryDirectory() as tmp:
            worktree, report = Path(tmp) / "copy", Path(tmp) / "report.json"
            worktree.mkdir()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run.main(["--repo", str(repo.path), "--main", "f" * 40, "--worktree", str(worktree),
                                 "--report", str(report)], api=NoNotion(), now=NOW)
            repo.close()
            self.assertEqual(code, 2, out.getvalue())
            self.assertIn("the copy could not be made", json.loads(report.read_text(encoding="utf-8"))["error"][0])

    def test_without_the_key_the_copy_runs_as_before(self):
        self.assertTrue(config_check.copy_on({"notion": {}}))
        self.assertTrue(config_check.copy_on({}))
        self.assertTrue(config_check.copy_on({"notion": {"backup": "false"}}), "a string is C3's type error, not «off»")
        self.assertFalse(config_check.copy_on({"notion": {"backup": False}}))


def commit_step() -> str:
    """The literal `run: |` of the step «Commit and push the copy»."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "- name: Commit and push the copy")
    i = next(j for j in range(start, len(lines)) if lines[j].strip() == "run: |")
    indent = len(lines[i + 1]) - len(lines[i + 1].lstrip())
    body = []
    for line in lines[i + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body)


class CommitStep(unittest.TestCase):
    def run_step(self, report: dict | None) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            if report is not None:
                (Path(tmp) / "report.json").write_text(json.dumps(report), encoding="utf-8")
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "RUNNER_TEMP": tmp, "GH_TOKEN": "x"}
            return subprocess.run(["bash", "-c", commit_step()], env=env, capture_output=True, text=True)

    def test_an_off_copy_commits_nothing(self):
        done = self.run_step({"N3": [], "N6": [], "error": [], "skipped": ["N3", "N6"], "off": True})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("The nightly copy is off: nothing to commit", done.stdout)

    def test_a_copy_that_ran_goes_on_to_its_commit(self):
        """Not off: the step goes on — here to the worktree, which this test does not make, so it
        fails there; that it got there is the point."""
        for report in ({"N3": [], "N6": [], "error": [], "skipped": []}, {"off": "true"}):
            with self.subTest(report=report):
                done = self.run_step(report)
                self.assertNotEqual(done.returncode, 0)
                self.assertNotIn("nothing to commit", done.stdout)


class C3(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def test_off_only_in_the_trusted_contacts_own_push_and_on_by_anyone(self):
        with_contact = VALID_CONFIG + b"\n[alarm]\ntrusted_contact = \"Contact\"\n"
        main = self.repo.commit({"user/config.toml": with_contact})
        off = self.repo.commit({"user/config.toml": with_contact + b"\n[notion]\nbackup = false\n"})
        check = lambda head, base, actor, before: [f.message for f in config_check.check(
            self.repo.path, base, head, PERSON, actor, before)]
        red = ["notion.backup turns the nightly copy off, and N2, N3 and N6 with it: only in a push of the "
               "trusted contact's own"]
        self.assertEqual(check(off, main, "alex-bot", main), red)
        self.assertEqual(check(off, main, "contact", main), [])
        on = self.repo.commit({"user/config.toml": with_contact + b"\n[notion]\nbackup = true\n"})
        self.assertEqual(check(on, off, "alex-bot", off), [], "turning the copy on asks nobody")
        typo = self.repo.commit({"user/config.toml": with_contact + b"\n[notion]\nbackup = \"no\"\n"})
        self.assertEqual(check(typo, typo, "", ""), ["notion.backup must be bool"])


class Watch(unittest.TestCase):
    def test_n2_is_skipped_and_said(self):
        repo = Repo({**BASE_FILES, "user/config.toml": OFF})
        api = FakeApi()
        api.lists[f"repos/{REPO}/activity"] = [
            {"id": 1, "activity_type": "push", "before": "0" * 40, "after": repo.base,
             "timestamp": "2026-10-01T00:00:00Z", "actor": {"login": "owner"}}]
        api.objects["apps/github-actions"] = {"id": 15368}
        api.objects[f"repos/{REPO}/branches/main"] = {"protection": {
            "enabled": True, "required_status_checks": {
                "enforcement_level": "everyone", "checks": [{"context": "gate", "app_id": 15368}]}}}
        api.objects[f"repos/{REPO}/rules/branches/main"] = []
        api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": repo.base}}
        api.lists[f"repos/{REPO}/issues"] = []
        report = repo.path.parent / f"{repo.path.name}-report.json"
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = watch.main(["--repo", str(repo.path), "--main", repo.base, "--manifest",
                                   str(repo.manifest_file(repo.base)), "--report", str(report),
                                   "--branches", "refs/heads/"], api=api, now=NOW)
            data = json.loads(report.read_text(encoding="utf-8"))
        finally:
            repo.close()
            report.unlink(missing_ok=True)
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("N2", data["skipped"])
        self.assertIn("N2 NOTE the nightly copy is off (notion.backup = false): nothing to watch", out.getvalue())
        self.assertNotIn("the Notion copy has not run yet", out.getvalue())


if __name__ == "__main__":
    unittest.main()

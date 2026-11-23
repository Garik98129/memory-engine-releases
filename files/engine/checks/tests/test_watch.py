import contextlib
import io
import json
import unittest
from datetime import datetime, timedelta

from engine.checks import watch
from engine.checks.tests.fakeapi import REPO, FakeApi
from engine.checks.tests.helpers import COMMITTED, Repo

NOW = datetime.fromisoformat(COMMITTED) + timedelta(hours=1)


class Runner(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.api = FakeApi()
        self.api.lists[f"repos/{REPO}/activity"] = [
            {"id": 1, "activity_type": "push", "before": "0" * 40, "after": self.repo.base,
             "timestamp": "2026-10-01T00:00:00Z", "actor": {"login": "owner"}}]
        self.api.objects["apps/github-actions"] = {"id": 15368}
        self.api.objects[f"repos/{REPO}/branches/main"] = {"protection": {
            "enabled": True, "required_status_checks": {
                "enforcement_level": "everyone", "checks": [{"context": "gate", "app_id": 15368}]}}}
        self.api.objects[f"repos/{REPO}/rules/branches/main"] = []
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": self.repo.base}}
        self.api.lists[f"repos/{REPO}/issues"] = []
        self.report = self.repo.path.parent / f"{self.repo.path.name}-report.json"

    def tearDown(self):
        self.repo.close()
        self.report.unlink(missing_ok=True)

    def call(self, manifest, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = watch.main(["--repo", str(self.repo.path), "--main", self.repo.base,
                               "--manifest", str(manifest), "--report", str(self.report),
                               "--branches", "refs/heads/", *extra], api=self.api, now=NOW)
        return code, out.getvalue(), json.loads(self.report.read_text(encoding="utf-8"))

    def test_a_clean_night(self):
        code, out, report = self.call(self.repo.manifest_file(self.repo.base))
        self.assertEqual(code, 0, out)
        self.assertIn("SUMMARY alarm=0 ran=N1,N2,N4,N5", out)
        self.assertIn("N2 NOTE the Notion copy has not run yet", out)
        self.assertIn("N4 NOTE no open Issue «routine marks»", out)
        self.assertEqual(report, {"N1": [], "N2": [], "N4": [], "N5": [], "error": [], "skipped": [],
                                  "todo": ["field chat: the installed text is not its master — install it and record it",
                                           "field bot: the installed text is not its master — install it and record it"]})
        self.assertIn("TODO field chat: the installed text is not its master", out)
        self.assertEqual(code, 0, "the to-do never turns the run red")

    def test_an_alarm_is_in_the_report(self):
        self.api.objects[f"repos/{REPO}/branches/main"] = {"protection": {"enabled": False}}
        code, out, report = self.call(self.repo.manifest_file(self.repo.base))
        self.assertEqual(code, 1)
        self.assertEqual(len(report["N1"]), 1)

    def test_a_part_that_cannot_run_is_an_error_beside_the_other_findings(self):
        del self.api.objects["apps/github-actions"]
        self.api.lists[f"repos/{REPO}/activity"][0]["actor"] = {"login": "alex"}
        self.api.lists[f"repos/{REPO}/activity"][0]["timestamp"] = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        code, out, report = self.call(self.repo.manifest_file(self.repo.base))
        self.assertEqual(code, 2)
        self.assertEqual(len(report["N1"]), 1)                       # the push is still reported
        self.assertIn("N1 settings could not run: ApiError", report["error"][0])
        self.assertIn("ERROR N1 settings could not run", out)

    def test_a_record_pr_is_not_judged_by_n5(self):
        late = NOW + timedelta(hours=48)              # the fixture fields were never recorded as installed
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = watch.main(["--repo", str(self.repo.path), "--main", self.repo.base, "--manifest",
                               str(self.repo.manifest_file(self.repo.base)), "--report", str(self.report),
                               "--branches", "refs/heads/"], api=self.api, now=late)
        self.assertEqual(code, 1)
        self.assertIn("N5 ALARM the chat field changed", out.getvalue())
        with contextlib.redirect_stdout(out):
            code = watch.main(["--repo", str(self.repo.path), "--main", self.repo.base, "--manifest",
                               str(self.repo.manifest_file(self.repo.base)), "--report", str(self.report),
                               "--branches", "refs/heads/", "--record-pr"], api=self.api, now=late)
        self.assertEqual(code, 0)
        self.assertIn("N5 N/A this PR records an installation", out.getvalue())
        self.assertNotIn("todo", json.loads(self.report.read_text(encoding="utf-8")),
                         "a run that does not judge N5 leaves the to-do out, so its Issue is left alone")
        self.assertIn("SUMMARY alarm=0 ran=N1,N2,N4", out.getvalue())
        self.assertEqual(json.loads(self.report.read_text(encoding="utf-8"))["skipped"], ["N5"])

    def test_a_run_whose_main_moved_on_judges_nothing(self):
        # A re-run of an old run, or main moved since the checkout: the run for the new main judges it.
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": "e" * 40}}
        self.api.objects[f"repos/{REPO}/branches/main"] = {"protection": {"enabled": False}}   # would alarm if judged
        code, out, report = self.call(self.repo.manifest_file(self.repo.base))
        self.assertEqual(code, 0, out)
        self.assertEqual(report["skipped"], ["N1", "N2", "N4", "N5"])
        self.assertEqual((report["N1"], report["N2"], report["N4"], report["N5"], report["error"]), ([], [], [], [], []))
        self.assertIn(f"N1 N/A main is at {'e' * 12} now, this run checked out {self.repo.base[:12]}", out)
        self.assertIn("SUMMARY alarm=0 ran=", out)

    def test_a_missing_manifest_is_an_error(self):
        code, out, report = self.call(self.repo.path / "no-such-manifest.json")
        self.assertEqual(code, 2)
        self.assertIn("the checks could not start", report["error"][0])


if __name__ == "__main__":
    unittest.main()

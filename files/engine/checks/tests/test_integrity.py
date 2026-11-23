import hashlib
import unittest
from datetime import datetime, timedelta, timezone

from engine.checks import integrity
from engine.checks.github import ApiError
from engine.checks.tests.fakeapi import REPO, FakeApi
from engine.checks.tests.helpers import VALID_CONFIG, Repo

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
BOT = "github-actions[bot]"
ACTIONS_ID = 15368
RUN = 555
PR = 7
CONTACT_CONFIG = VALID_CONFIG + b'\n[alarm]\ntrusted_contact = "helper"\n'


def stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def fake_head(number: int) -> str:
    """A PR head that is not in the clone — after a squash merge it never is."""
    return hashlib.sha1(str(number).encode()).hexdigest()


class Base(unittest.TestCase):
    """main = base + one PR squash-merged by job `merge`, with every record GitHub holds:
    the PR head is two commits on a branch that is gone, so it is not on main."""

    def setUp(self):
        self.repo = Repo()
        self.base = self.repo.base
        self.repo.commit({"user/fields/chat.md": b"Who: a draft.\n"})
        self.head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})   # the PR head
        self.repo.checkout(self.base)
        self.main = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})   # its squash merge
        self.manifest = self.repo.manifest(self.main)
        self.api = FakeApi()
        self.records: list[dict] = []
        self.merged(self.base, self.main, 1, head=self.head)
        self.api.objects["apps/github-actions"] = {"id": ACTIONS_ID}
        self.api.objects[f"repos/{REPO}/branches/main"] = {"protection": {
            "enabled": True, "required_status_checks": {
                "enforcement_level": "everyone", "checks": [{"context": "gate", "app_id": ACTIONS_ID}]}}}
        self.api.objects[f"repos/{REPO}/rules/branches/main"] = []

    def tearDown(self):
        self.repo.close()

    # -- the records GitHub keeps -------------------------------------------------------

    def record(self, kind, before, after, hours_ago, who=BOT):
        """One line of the activity log; the log is served newest first."""
        item = {"id": len(self.records) + 1, "activity_type": kind, "before": before, "after": after,
                "timestamp": stamp(hours_ago), "actor": {"login": who}, "ref": "refs/heads/main"}
        self.records.append(item)
        self.api.lists[f"repos/{REPO}/activity"] = sorted(self.records, key=lambda a: a["timestamp"], reverse=True)
        return item

    def merged(self, before, after, hours_ago, number=PR, run=RUN, head=None):
        """A PR squash-merged by job `merge` onto `before`, giving `after`, with its gate run."""
        head = head or fake_head(number)
        self.record("pr_merge", before, after, hours_ago)
        self.api.objects[f"repos/{REPO}/commits/{after}/pulls"] = [
            {"number": number, "merge_commit_sha": after, "base": {"ref": "main"}}]
        self.api.objects[f"repos/{REPO}/pulls/{number}"] = {"merged_by": {"login": BOT}, "head": {"sha": head}}
        self.api.lists[f"repos/{REPO}/commits/{head}/statuses"] = [self.status(run)]
        # GitHub reports a pull_request_target run on the PR head (seen live).
        self.api.objects[f"repos/{REPO}/actions/runs/{run}"] = {
            "path": ".github/workflows/check.yml", "repository": {"full_name": REPO},
            "event": "pull_request_target", "head_sha": head, "head_branch": "bot/change"}
        self.api.lists[f"repos/{REPO}/actions/runs/{run}/jobs"] = [{"id": run * 10, "name": "check", "conclusion": "success"}]
        self.set_log(before, number=number, run=run, head=head)
        return head

    def set_log(self, base, number=PR, run=RUN, head=None):
        head = head or self.head
        self.api.texts[f"repos/{REPO}/actions/jobs/{run * 10}/logs"] = (
            "2026-10-23T08:00:00.1Z ##[group]Run set -euo pipefail\n"
            f"2026-10-23T08:00:01.2Z GATE pr={number} head={head} base={base} run={run}\n")

    @staticmethod
    def status(run, state="success", who=BOT, url=None):
        return {"context": "gate", "state": state, "creator": {"login": who},
                "target_url": url or f"https://github.com/{REPO}/actions/runs/{run}"}

    def run_check(self, main=None, owner="owner"):
        found, notes, errors = integrity.check(self.api, self.repo.path, main or self.main, self.manifest,
                                               NOW, owner, refs_prefix="refs/heads/")
        return [f.line() for f in found], notes, errors


class Clean(Base):
    def test_a_squash_merged_pr_that_came_through_the_gate_is_clean(self):
        self.assertFalse(integrity.gitio.is_ancestor(self.repo.path, self.head, self.main))   # squash: head not on main
        lines, notes, errors = self.run_check()
        self.assertEqual((lines, errors), ([], []))
        self.assertIn("N1 NOTE no trusted contact: alarms reach the repository owner only", notes)

    def test_a_dispatch_run_on_main_counts(self):
        self.api.objects[f"repos/{REPO}/actions/runs/{RUN}"].update(
            {"event": "workflow_dispatch", "head_sha": self.base, "head_branch": "main"})
        self.assertEqual(self.run_check()[0], [])

    def test_the_latest_record_older_than_the_window_is_still_read(self):
        self.records[0]["timestamp"] = stamp(40)
        self.api.lists[f"repos/{REPO}/activity"] = list(self.records)
        self.assertEqual(self.run_check()[0], [])          # nothing in the window; main is where the log ends

    def test_a_record_older_than_the_window_is_not_verified(self):
        self.records[0]["timestamp"] = stamp(40)
        self.api.lists[f"repos/{REPO}/activity"] = list(self.records)
        del self.api.objects[f"repos/{REPO}/pulls/{PR}"]  # would be an error if read
        self.assertEqual(self.run_check()[0:3:2], ([], []))

    def test_a_change_after_this_runs_checkout_is_left_to_its_own_run(self):
        self.record("push", self.main, "d" * 40, 0.1, who="alex")
        lines, notes, _ = self.run_check()
        self.assertEqual(lines, [])
        self.assertIn("N1 NOTE 1 change(s) of main after this run's checkout are checked by their own run", notes)

    def test_an_update_that_changes_check_yml_is_checked_by_the_one_before_it(self):
        self.repo.checkout(self.main)
        updated = self.repo.commit({".github/workflows/check.yml": b"name: check v2\n"})
        self.manifest = self.repo.manifest(updated)
        self.merged(self.main, updated, 0.5, number=8, run=556)
        self.assertEqual(self.run_check(main=updated)[0:3:2], ([], []))


class History(Base):
    def test_a_direct_push_is_an_alarm(self):
        pushed = self.repo.commit({"user/fields/bot.md": b"Who: pushed.\n"})
        self.manifest = self.repo.manifest(pushed)
        self.record("push", self.main, pushed, 0.5, who="alex")
        lines, _, _ = self.run_check(main=pushed)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("N1 ALARM main: push by alex", lines[0])
        self.assertIn("not through the gate", lines[0])

    def test_a_force_push_is_an_alarm(self):
        self.record("force_push", self.main, self.base, 0.5, who="alex")
        lines, _, _ = self.run_check(main=self.base)
        self.assertTrue(any("force_push by alex" in line for line in lines), lines)

    def test_a_merge_by_a_person_is_an_alarm(self):
        self.api.objects[f"repos/{REPO}/pulls/{PR}"]["merged_by"] = {"login": "alex"}
        self.assertEqual(self.run_check()[0], [f"N1 ALARM PR #{PR} was merged by alex, not by job «merge»"])

    def test_a_commit_that_is_no_pr_merge_is_an_alarm(self):
        self.api.objects[f"repos/{REPO}/commits/{self.main}/pulls"] = []
        self.assertEqual(self.run_check()[0], [f"N1 ALARM commit {self.main[:12]} on main is not the merge of a PR"])

    def test_a_gate_from_another_workflow_is_not_trusted(self):
        self.api.objects[f"repos/{REPO}/actions/runs/{RUN}"]["path"] = ".github/workflows/other.yml"
        lines = self.run_check()[0]
        self.assertEqual(len(lines), 1)
        self.assertIn("has no verified «gate»: run 555 is of '.github/workflows/other.yml'", lines[0])

    def test_a_run_for_another_head_is_not_trusted(self):
        self.api.objects[f"repos/{REPO}/actions/runs/{RUN}"]["head_sha"] = self.base
        lines = self.run_check()[0]
        self.assertIn(f"run 555 is a run for {self.base[:12]}, not for {self.head[:12]}", lines[0])

    def test_a_dispatch_run_off_main_is_not_trusted(self):
        self.api.objects[f"repos/{REPO}/actions/runs/{RUN}"].update(
            {"event": "workflow_dispatch", "head_sha": self.head, "head_branch": "bot/change"})
        self.assertIn("was dispatched on 'bot/change', not on main", self.run_check()[0][0])

    def test_a_run_that_checked_against_another_main_is_not_trusted(self):
        self.repo.checkout(self.main)
        later = self.repo.commit({"user/fields/bot.md": b"Who: later.\n"})
        self.manifest = self.repo.manifest(later)
        self.merged(self.main, later, 0.5, number=8, run=556)
        self.set_log(self.base, number=8, run=556, head=fake_head(8))     # checked against an older main
        lines = self.run_check(main=later)[0]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn(f"run 556 checked against {self.base[:12]}, but the PR merged onto {self.main[:12]}", lines[0])

    def test_a_run_that_logged_another_head_does_not_count(self):
        self.set_log(self.base, head="b" * 40)
        self.assertIn(f"run 555 did not log a check of PR #{PR} at {self.head[:12]}", self.run_check()[0][0])

    def test_a_failed_check_job_does_not_count(self):
        self.api.lists[f"repos/{REPO}/actions/runs/{RUN}/jobs"][0]["conclusion"] = "failure"
        self.assertIn("has no successful job «check»", self.run_check()[0][0])

    def test_a_deleted_log_is_a_finding_not_a_crash(self):
        del self.api.texts[f"repos/{REPO}/actions/jobs/{RUN * 10}/logs"]
        lines, _, errors = self.run_check()
        self.assertEqual(errors, [])
        self.assertIn("the log of run 555 cannot be read", lines[0])

    def test_a_gate_set_by_a_person_or_pointing_elsewhere_does_not_count(self):
        self.api.lists[f"repos/{REPO}/commits/{self.head}/statuses"] = [
            self.status(RUN, who="alex"), self.status(RUN, url="https://example.com/x")]
        self.assertIn("a «gate» success that names no run of this repository", self.run_check()[0][0])

    def test_no_gate_at_all(self):
        self.api.lists[f"repos/{REPO}/commits/{self.head}/statuses"] = [self.status(RUN, state="failure")]
        self.assertIn("no «gate» success at all", self.run_check()[0][0])

    def test_a_gap_in_the_record_is_an_alarm(self):
        later = self.repo.commit({"user/fields/bot.md": b"Who: later.\n"})
        self.manifest = self.repo.manifest(later)
        self.record("push", "c" * 40, later, 0.5, who="x")
        lines = self.run_check(main=later)[0]
        self.assertTrue(any("the record of main has a gap" in line for line in lines), lines)

    def test_main_moved_without_a_record(self):
        later = self.repo.commit({"user/fields/bot.md": b"Who: later.\n"})
        self.manifest = self.repo.manifest(later)
        self.assertEqual(self.run_check(main=later)[0],
                         [f"N1 ALARM main is at {later[:12]}, but its last recorded change ends at "
                          f"{self.main[:12]}: a change GitHub did not record"])

    def test_the_trusted_contacts_release_by_hand_is_an_expected_alarm(self):
        # No exception in the code; the trusted contact closes the alarm.
        with_contact = self.repo.commit({"user/config.toml": CONTACT_CONFIG})
        self.merged(self.main, with_contact, 2, number=8, run=556)
        applied = self.repo.commit({".github/workflows/check.yml": b"name: check v2\n",
                                    "user/engine.lock": b'release = "v0.2.0"\n'})
        self.manifest = self.repo.manifest(applied, "v0.2.0")
        self.api.statuses[f"repos/{REPO}/collaborators/helper"] = 204
        self.record("push", with_contact, applied, 1, who="Helper")
        lines, notes, errors = self.run_check(main=applied)
        self.assertEqual(errors, [])
        self.assertTrue(any("push by Helper" in line and "not through the gate" in line for line in lines), lines)
        self.assertFalse(any("release" in n for n in notes), notes)


class Tree(Base):
    def test_a_file_outside_every_area_is_an_alarm(self):
        main = self.repo.commit({"notes/secret.md": b"x\n"})
        self.manifest = self.repo.manifest(main)
        self.merged(self.main, main, 0.5, number=8, run=556)
        self.assertEqual(self.run_check(main=main)[0], ["N1 ALARM C2 notes/secret.md is outside every area"])

    def test_an_engine_file_that_differs_from_the_release_is_an_alarm(self):
        main = self.repo.commit({"engine/rules/core.md": b"Weakened.\n"})
        self.merged(self.main, main, 0.5, number=8, run=556)
        self.assertEqual(self.run_check(main=main)[0], ["N1 ALARM C1 engine/rules/core.md differs from release v0.1.0"])

    def test_numbers_already_on_main_are_found_without_their_digits(self):
        main = self.repo.commit({"user/fields/bot.md": b"Card 4111 1111 1111 1111\n"})
        self.manifest = self.repo.manifest(main)
        self.merged(self.main, main, 0.5, number=8, run=556)
        lines = self.run_check(main=main)[0]
        self.assertEqual(lines, ["N1 ALARM C4 user/fields/bot.md line 1: a card number — payment data never goes into git"])
        self.assertNotIn("4111", lines[0])


class Branches(Base):
    def test_a_branch_that_adds_a_workflow_is_an_alarm(self):
        self.repo.checkout(self.main)
        sneaky = self.repo.commit({".github/workflows/probe.yml": b"name: probe\n"})
        self.repo.branch("bot/probe", sneaky)
        self.assertEqual(self.run_check()[0],
                         ["N1 ALARM branch bot/probe changes .github/workflows/probe.yml — a workflow outside main"])

    def test_an_old_branch_is_not_blamed_for_an_update_main_took_later(self):
        self.repo.checkout(self.main)
        old = self.repo.commit({"user/fields/bot.md": b"Who: a draft.\n"})
        self.repo.branch("bot/draft", old)
        self.repo.checkout(self.main)
        updated = self.repo.commit({".github/workflows/check.yml": b"name: check v2\n"})
        self.manifest = self.repo.manifest(updated)
        self.merged(self.main, updated, 0.5, number=8, run=556)
        self.assertEqual(self.run_check(main=updated)[0], [])


class Settings(Base):
    def with_config(self, config: bytes) -> str:
        main = self.repo.commit({"user/config.toml": config})
        self.manifest = self.repo.manifest(main)
        self.merged(self.main, main, 0.5, number=8, run=556)
        return main

    def test_no_required_gate_is_an_alarm(self):
        self.api.objects[f"repos/{REPO}/branches/main"] = {"protection": {"enabled": False}}
        self.assertEqual(self.run_check()[0], ["N1 ALARM main does not require «gate» from GitHub Actions: "
                                               "branch protection is off, or not visible"])

    def test_a_gate_required_from_any_source_is_not_enough(self):
        self.api.objects[f"repos/{REPO}/branches/main"]["protection"]["required_status_checks"]["checks"] = [
            {"context": "gate", "app_id": None}]
        self.assertEqual(len(self.run_check()[0]), 1)

    def test_whether_admins_may_bypass_is_not_judged_here(self):
        # The branch view's enforcement_level is undocumented and, in GitHub's own example,
        # reads non_admins beside enforce_admins = true; the installation sets this (§7).
        self.api.objects[f"repos/{REPO}/branches/main"]["protection"]["required_status_checks"]["enforcement_level"] = "non_admins"
        self.assertEqual(self.run_check()[0], [])

    def test_rulesets_closed_on_this_plan_leave_the_branch_to_decide(self):
        original = self.api.get

        def get(path, params=None, allow_404=False):
            if path.endswith("/rules/branches/main"):
                raise ApiError("GET rules: HTTP 403")
            return original(path, params, allow_404)

        self.api.get = get
        self.assertEqual(self.run_check()[0], [])
        self.api.objects[f"repos/{REPO}/branches/main"] = {"protection": {"enabled": False}}
        self.assertEqual(len(self.run_check()[0]), 1)

    def test_a_ruleset_that_requires_the_gate_counts(self):
        self.api.objects[f"repos/{REPO}/branches/main"] = {"protection": {"enabled": False}}
        self.api.objects[f"repos/{REPO}/rules/branches/main"] = [{"type": "required_status_checks", "parameters": {
            "required_status_checks": [{"context": "gate", "integration_id": ACTIONS_ID}]}}]
        self.assertEqual(self.run_check()[0], [])

    def test_free_says_so_and_asks_nothing(self):
        main = self.with_config(VALID_CONFIG + b'\n[github]\nprotection = "none"\n')
        del self.api.objects["apps/github-actions"]          # would be an error if asked
        lines, notes, errors = self.run_check(main=main)
        self.assertEqual((lines, errors), ([], []))
        self.assertTrue(any(n.startswith("N1 NOTE github.protection = none") for n in notes), notes)

    def test_a_trusted_contact_who_lost_access_is_an_alarm(self):
        main = self.with_config(CONTACT_CONFIG)
        self.assertEqual(self.run_check(main=main)[0],
                         ["N1 ALARM the trusted contact helper is not a collaborator: alarms do not reach them"])
        self.api.statuses[f"repos/{REPO}/collaborators/helper"] = 204
        self.assertEqual(self.run_check(main=main)[0], [])
        self.api.statuses[f"repos/{REPO}/collaborators/helper"] = 500
        lines, _, errors = self.run_check(main=main)
        self.assertEqual(lines, [])
        self.assertEqual(errors, ["N1 settings could not run: ApiError: GET collaborators/helper: HTTP 500"])

    def test_the_owner_as_trusted_contact_needs_no_lookup(self):
        main = self.with_config(VALID_CONFIG + b'\n[alarm]\ntrusted_contact = "Owner"\n')
        self.assertEqual(self.run_check(main=main)[0], [])


class Errors(Base):
    def test_a_part_that_cannot_run_is_an_error_and_the_others_still_report(self):
        del self.api.objects["apps/github-actions"]                     # settings cannot run
        self.api.objects[f"repos/{REPO}/pulls/{PR}"]["merged_by"] = {"login": "alex"}   # history finds this
        lines, _, errors = self.run_check()
        self.assertEqual(lines, [f"N1 ALARM PR #{PR} was merged by alex, not by job «merge»"])
        self.assertEqual(errors, ["N1 settings could not run: ApiError: GET apps/github-actions: HTTP 404"])


if __name__ == "__main__":
    unittest.main()

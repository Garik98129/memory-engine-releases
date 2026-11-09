import unittest
from datetime import datetime, timedelta, timezone

from engine.checks import backup_check
from engine.checks.tests.fakeapi import BOT, REPO, FakeApi
from engine.checks.tests.helpers import Repo

NOW = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)
ACTIVITY = f"repos/{REPO}/activity"


def stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class N2(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.api = FakeApi()
        self.api.lists[ACTIVITY] = [{"id": 1, "activity_type": "push", "before": "0" * 40, "after": self.repo.base,
                                     "timestamp": stamp(100), "actor": {"login": "owner"}}]   # main's record: not ours

    def tearDown(self):
        self.repo.close()

    def branch(self, text=b"copy\n"):
        self.repo.checkout(self.repo.base)
        sha = self.repo.commit({"copy.json": text})
        self.repo.branch("notion-backup", sha)
        return sha

    def record(self, kind, before, after, who, hours_ago):
        self.api.lists[ACTIVITY].insert(0, {"id": 90, "activity_type": kind, "before": before, "after": after,
                                            "timestamp": stamp(hours_ago), "actor": {"login": who},
                                            "ref": "refs/heads/notion-backup"})

    def judge(self):
        found, notes = backup_check.check(self.api, self.repo.path, "refs/heads/", NOW)
        return [f.message for f in found], notes

    def test_no_copy_yet_is_a_note(self):
        self.assertEqual(self.judge(), ([], ["N2 NOTE the Notion copy has not run yet: no notion-backup branch, no record of one"]))

    def test_a_fresh_push_by_the_workflow_is_clean(self):
        sha = self.branch()
        self.record("branch_creation", "0" * 40, sha, BOT, 5)
        self.assertEqual(self.judge(), ([], []))

    def test_a_stale_copy_is_an_alarm(self):
        sha = self.branch()
        self.record("push", "0" * 40, sha, BOT, 40)
        found, _ = self.judge()
        self.assertEqual(found, [f"the Notion copy stopped: the last change of notion-backup was at {stamp(40)}, more than 36 h ago"])

    def test_a_force_push_or_a_push_by_someone_else_is_an_alarm(self):
        sha = self.branch()
        self.record("push", "0" * 40, "a" * 40, BOT, 20)
        self.record("force_push", "a" * 40, sha, "alex", 10)
        found, _ = self.judge()
        self.assertEqual(found, [f"notion-backup: force_push by alex at {stamp(10)} — the copy's history was rewritten"])
        self.api.lists[ACTIVITY][0]["activity_type"] = "push"
        found, _ = self.judge()
        self.assertEqual(found, [f"notion-backup: push by alex at {stamp(10)} — not the backup workflow"])

    def test_a_head_the_log_does_not_end_at_is_an_alarm(self):
        sha = self.branch()
        self.record("push", "0" * 40, "b" * 40, BOT, 5)
        found, _ = self.judge()
        self.assertEqual(found, [f"notion-backup is at {sha[:12]}, but its last recorded change ends at {'b' * 12}: a change GitHub did not record"])

    def test_a_branch_without_any_record_is_an_alarm(self):
        sha = self.branch()
        found, _ = self.judge()
        self.assertEqual(found, [f"notion-backup is at {sha[:12]}, but GitHub has no record of the branch"])

    def test_a_deleted_branch_is_in_the_log(self):
        self.record("push", "0" * 40, "c" * 40, BOT, 30)
        self.record("branch_deletion", "c" * 40, "0" * 40, "alex", 2)
        found, _ = self.judge()
        self.assertEqual(found, [f"notion-backup: branch_deletion by alex at {stamp(2)} — the copy was deleted"])
        self.api.lists[ACTIVITY].pop(0)
        self.api.lists[ACTIVITY][0]["timestamp"] = stamp(10)
        found, _ = self.judge()
        self.assertEqual(found, ["notion-backup is gone, and GitHub's last record of it is not a deletion"])

    def test_old_records_are_not_judged_again(self):
        sha = self.branch()
        self.record("force_push", "0" * 40, "d" * 40, "alex", 50)        # outside the window
        self.record("push", "d" * 40, sha, BOT, 3)
        self.assertEqual(self.judge(), ([], []))


if __name__ == "__main__":
    unittest.main()

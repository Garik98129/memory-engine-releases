import unittest
from datetime import datetime, timezone

from engine.checks import approval_check
from engine.checks.paths_check import PERSON, UPDATE
from engine.checks.tests.helpers import Repo, body

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)


def lines(n, tag=""):
    return "".join(f"line {i}{tag}\n" for i in range(n)).encode()


class ApprovalRecord(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def run_check(self, head, text, base=None):
        return [f.message for f in approval_check.check(self.repo.path, base or self.repo.base, head, text, NOW)]

    def test_complete_record_passes(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        self.assertEqual(self.run_check(head, body("user/fields/chat.md")), [])

    def test_missing_sections_block(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        self.assertEqual(self.run_check(head, "## Change\nwas → is\n"),
                         ["the PR body has no ## Files section", "the PR body has no ## Approved section"])

    def test_empty_change_blocks(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        self.assertIn("the ## Change section is empty",
                      self.run_check(head, body("user/fields/chat.md", change="")))

    def test_every_changed_file_and_only_those_are_named(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n", "user/routines/r.md": b"New.\n"})
        self.assertEqual(self.run_check(head, body("user/fields/chat.md", "user/fields/bot.md")),
                         ["user/routines/r.md is changed but not named under ## Files",
                          "user/fields/bot.md is named under ## Files but not changed"])

    def test_full_rewrite_must_be_marked(self):
        main = self.repo.commit({"user/routines/r.md": lines(50)})
        head = self.repo.commit({"user/routines/r.md": lines(25) + lines(25, " new")})
        self.assertEqual(self.run_check(head, body("user/routines/r.md"), base=main),
                         ["user/routines/r.md changes 50% of its 50 lines: "
                          "name it with (full rewrite) and tell the person"])
        self.assertEqual(self.run_check(head, body("user/routines/r.md (full rewrite)"), base=main), [])

    def test_small_master_needs_no_mark(self):
        main = self.repo.commit({"user/routines/r.md": lines(39)})
        head = self.repo.commit({"user/routines/r.md": lines(39, " new")})
        self.assertEqual(self.run_check(head, body("user/routines/r.md"), base=main), [])

    def test_approval_time_needs_an_offset_and_not_the_future(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        self.assertEqual(self.run_check(head, body("user/fields/chat.md", approved="yesterday evening")),
                         ["## Approved: 'yesterday evening' is not a time with a UTC offset"])
        self.assertEqual(self.run_check(head, body("user/fields/chat.md", approved="2026-10-23T11:08")),
                         ["## Approved: '2026-10-23T11:08' is not a time with a UTC offset"])
        self.assertEqual(self.run_check(head, body("user/fields/chat.md", approved="2026-10-24T11:08+03:00")),
                         ["## Approved: the time is in the future"])

    def test_a_yes_older_than_a_day_before_the_commit_blocks(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        committed = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
        msgs = [f.message for f in approval_check.check(
            self.repo.path, self.repo.base, head, body("user/fields/chat.md", approved="2020-01-01T00:00+00:00"),
            NOW, committed)]
        self.assertEqual(msgs, ["## Approved: the yes is more than a day older than the change"])
        self.assertEqual(approval_check.check(self.repo.path, self.repo.base, head, body("user/fields/chat.md"),
                                              NOW, committed), [])

    def test_not_applicable_to_updates_and_machine_records(self):
        head = self.repo.commit({"user/fields/installed.toml": b'chat = "0123abcd"\n'})
        self.assertEqual(approval_check.not_applicable(self.repo.path, self.repo.base, head, PERSON),
                         "machine record only")
        self.assertEqual(approval_check.not_applicable(self.repo.path, self.repo.base, head, UPDATE),
                         "update PR")
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        self.assertIsNone(approval_check.not_applicable(self.repo.path, self.repo.base, head, PERSON))


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timedelta

from engine.checks import drift
from engine.checks.limits_check import assemble
from engine.checks.tests.helpers import BASE_FILES, COMMITTED, Repo

CHANGED = datetime.fromisoformat(COMMITTED)        # every fixture commit is made at this time
LATE = CHANGED + timedelta(hours=37)
SOON = CHANGED + timedelta(hours=10)


def field_sha(name: str) -> str:
    engine = BASE_FILES[f"engine/rules/fields/{name}.md"].decode()
    person = BASE_FILES[f"user/fields/{name}.md"].decode()
    return drift.digest(assemble(engine, person))


def installed(**extra: str) -> bytes:
    text = f'[fields.chat]\nsha256 = "{field_sha("chat")}"\n\n[fields.bot]\nsha256 = "{field_sha("bot")}"\n'
    for name, sha in extra.items():
        text += f'\n[routines.{name}]\nsha256 = "{sha}"\n'
    return text.encode()


class Drift(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def lines(self, main, now=LATE):
        return [f.line() for f in drift.check(self.repo.path, main, now)]

    def test_installed_fields_and_routines_are_clean(self):
        routine = b"\nEvery morning: the calendar.\n\n"
        main = self.repo.commit({"user/routines/morning.md": routine,
                                 "user/fields/installed.toml": installed(morning=drift.digest(drift.routine_text(routine)))})
        self.assertEqual(self.lines(main), [])

    def test_a_field_never_installed_is_an_alarm_after_the_grace(self):
        self.assertEqual(self.lines(self.repo.base, now=SOON), [])
        lines = self.lines(self.repo.base)
        self.assertEqual(lines, [
            "N5 ALARM the chat field changed more than 36 h ago and is not reinstalled: user/fields/installed.toml does not match it",
            "N5 ALARM the bot field changed more than 36 h ago and is not reinstalled: user/fields/installed.toml does not match it",
        ])
        self.assertEqual(lines, self.lines(self.repo.base, now=LATE + timedelta(hours=50)))   # the same drift reads the same

    def test_a_changed_master_with_an_old_record_is_an_alarm(self):
        main = self.repo.commit({"user/fields/installed.toml": installed()})
        main = self.repo.commit({"user/fields/bot.md": b"Who: changed.\n"})
        self.assertEqual(self.lines(main), [
            "N5 ALARM the bot field changed more than 36 h ago and is not reinstalled: user/fields/installed.toml does not match it"])

    def test_an_engine_update_of_a_field_counts_too(self):
        main = self.repo.commit({"user/fields/installed.toml": installed()})
        main = self.repo.commit({"engine/rules/fields/chat.md": b"Chat safety rules, v2.\n"})
        self.assertEqual(len(self.lines(main)), 1)

    def test_a_routine_not_reinstalled_is_an_alarm(self):
        main = self.repo.commit({"user/routines/morning.md": b"Morning.\n",
                                 "user/fields/installed.toml": installed(morning="0" * 64)})
        self.assertEqual(self.lines(main), [
            "N5 ALARM routine morning changed more than 36 h ago and is not reinstalled: user/fields/installed.toml does not match it"])

    def test_a_routine_whose_master_is_gone_is_an_alarm(self):
        main = self.repo.commit({"user/fields/installed.toml": installed(evening="0" * 64)})
        lines = self.lines(main)
        self.assertEqual(len(lines), 1)
        self.assertIn("routine evening is recorded as installed, but its master has been gone for more than 36 h", lines[0])

    def test_a_broken_record_is_an_alarm(self):
        main = self.repo.commit({"user/fields/installed.toml": b"[fields\n"})
        lines = self.lines(main, now=SOON)
        self.assertEqual(len(lines), 1)
        self.assertIn("user/fields/installed.toml does not parse", lines[0])



class RecordPR(unittest.TestCase):
    """Issue #18, item 33: a PR that writes the read-back record is checked against the masters
    at once (C2), not by N5's alarm 36 h later. In PR #11 of the first installation the bot
    hashed a routine with its last newline: N5 strips the outer newlines (`routine_text`)."""

    ROUTINE = b"\nEvery morning: the calendar.\n"

    def setUp(self):
        self.repo = Repo()
        self.base = self.repo.commit({"user/routines/morning.md": self.ROUTINE})

    def tearDown(self):
        self.repo.close()

    def lines(self, record: bytes, base=None):
        head = self.repo.commit({"user/fields/installed.toml": record})
        return [f.line() for f in drift.record_check(self.repo.path, base or self.base, head)]

    def test_a_record_that_matches_its_masters_passes(self):
        right = drift.digest(drift.routine_text(self.ROUTINE))
        self.assertEqual(self.lines(installed(morning=right)), [])

    def test_a_routine_hashed_with_its_newlines_is_blocked_at_once(self):
        wrong = drift.digest(self.ROUTINE.decode())                     # what the bot did in PR #11
        lines = self.lines(installed(morning=wrong))
        self.assertEqual(len(lines), 1)
        self.assertIn("C2", lines[0])
        self.assertIn("records routine morning with a sha256 that is not of its master", lines[0])

    def test_a_routine_with_no_master_is_blocked_and_the_fields_stay_n5s(self):
        # The chat field's sha256 comes from the trusted contact by hand (REPO-3): a slip in
        # its form is N5's alarm, not a block on the whole record.
        right = drift.digest(drift.routine_text(self.ROUTINE))
        record = installed(morning=right, evening="0" * 64).replace(field_sha("chat").encode(), field_sha("chat").upper().encode())
        self.assertEqual(self.lines(record),
                         ["C2 BLOCK user/fields/installed.toml records routine evening, but user/routines/evening.md is not on main"])

    def test_an_entry_without_a_sha256_or_with_an_odd_name_is_blocked(self):
        right = drift.digest(drift.routine_text(self.ROUTINE))
        cases = {
            installed(morning=right) + b"\n[routines.ghost]\nnote = 1\n": "records routine ghost without a sha256",
            installed(morning=right).replace(b"[fields.chat]", b"[routines]\nghost = \"abc\"\n\n[fields.chat]"):
                "records routine ghost without a sha256",
            installed(morning=right) + b'\n[routines."a\\nSUMMARY block=0 ran=C1"]\nsha256 = "0"\n':
                "records routine 'a\\nSUMMARY block=0 ran=C1': not the name of a file in user/routines/",
            installed(morning=right) + b'\n[routines."../fields/bot"]\nsha256 = "0"\n': "records routine '../fields/bot'",
        }
        for record, wanted in cases.items():
            with self.subTest(wanted=wanted):
                self.repo.checkout(self.base)
                lines = self.lines(record)
                self.assertEqual(len(lines), 1, lines)
                self.assertIn(wanted, lines[0])
                self.assertEqual(lines[0].count("\n"), 0)                       # one line in the log, whatever the key

    def test_an_entry_the_pr_leaves_as_it_was_is_held_to_its_master_too(self):
        # Kimi, review of PR #25: item 33 compares every routines.* entry at once — N5 is
        # skipped on a record PR, so a stale entry left in it would land green.
        stale = installed(morning="0" * 64)                              # already wrong on main
        base = self.repo.commit({"user/fields/installed.toml": stale})
        lines = self.lines(stale + b"\n", base=base)                     # the record rewritten, the entry not
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("records routine morning with a sha256 that is not of its master", lines[0])

    def test_a_routine_with_no_entry_is_not_this_checks(self):
        right = drift.digest(drift.routine_text(self.ROUTINE))
        head_base = self.repo.commit({"user/routines/evening.md": b"Every evening.\n"})
        self.assertEqual(self.lines(installed(morning=right), base=head_base), [])   # evening unrecorded: N5's

    def test_a_broken_record_is_blocked_and_a_pr_without_one_is_not_asked(self):
        self.assertIn("user/fields/installed.toml does not parse", self.lines(b"[fields\n")[0])
        on_main = self.repo.commit({"user/fields/installed.toml": b"[fields\n"})    # N5 says that one
        head = self.repo.commit({"user/fields/chat.md": b"Who: later.\n"})        # a PR that writes no record
        self.assertEqual(drift.record_check(self.repo.path, on_main, head), [])


if __name__ == "__main__":
    unittest.main()

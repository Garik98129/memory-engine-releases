import unittest
from datetime import datetime, timedelta, timezone

from engine.checks import routine_check
from engine.checks.tests.fakeapi import BOT, REPO, FakeApi

NOW = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)
ISSUES = f"repos/{REPO}/issues"


def stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class N4(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.api.lists[ISSUES] = []
        self.config = {"person": {"github_login": "alex"}}

    def issue(self, number=7, title="routine marks", state="open", who="owner"):
        self.api.lists[ISSUES].append({"number": number, "title": title, "state": state, "user": {"login": who}})
        self.api.objects[f"{ISSUES}/{number}"] = self.api.lists[ISSUES][-1]
        self.api.lists[f"{ISSUES}/{number}/comments"] = []
        return number

    def mark(self, number, hours_ago, scopes="repo", who="alex", body=None):
        self.api.lists[f"{ISSUES}/{number}/comments"].append({
            "user": {"login": who}, "created_at": stamp(hours_ago),
            "body": body if body is not None else f"ROUTINE-MARK {stamp(hours_ago)} scopes={scopes}"})

    def judge(self, config=None):
        found, notes = routine_check.check(self.api, self.config if config is None else config, NOW)
        return [f.message for f in found], notes

    def test_no_issue_before_installation_is_a_note(self):
        found, notes = self.judge()
        self.assertEqual(found, [])
        self.assertIn("N4 NOTE no open Issue «routine marks»", notes[0])

    def test_a_fresh_mark_with_repo_only_is_clean(self):
        n = self.issue()
        self.mark(n, 30)
        self.mark(n, 6)
        self.assertEqual(self.judge(), ([], []))

    def test_an_issue_without_a_mark_is_an_alarm(self):
        self.issue()
        self.assertEqual(self.judge()[0], ["no mark from the bot's routine by alex in Issue #7 «routine marks»"])

    def test_a_stale_mark_is_an_alarm_with_a_stable_line(self):
        n = self.issue()
        self.mark(n, 40)
        found, _ = self.judge()
        self.assertEqual(found, [f"the bot's daily routine stopped: its last mark is from {stamp(40)}, more than 36 h ago"])

    def test_a_token_that_grew_is_an_alarm(self):
        n = self.issue()
        self.mark(n, 1, scopes="repo,workflow,admin:org")
        found, _ = self.judge()
        self.assertEqual(found, ["the bot's token has the scope(s) admin:org, workflow — more than repo (reported by alex)"])

    def test_unknown_or_empty_scopes_are_an_alarm(self):
        n = self.issue()
        self.mark(n, 1, scopes="unknown")
        self.assertIn("names no token scopes (scopes=unknown)", self.judge()[0][0])
        self.mark(n, 0.5, scopes="")
        self.assertIn("names no token scopes (scopes=empty)", self.judge()[0][0])

    def test_marks_by_other_accounts_do_not_count_when_the_login_is_set(self):
        n = self.issue()
        self.mark(n, 1, who="owner")
        self.mark(n, 1, who=BOT)
        self.assertEqual(self.judge()[0], ["no mark from the bot's routine by alex in Issue #7 «routine marks»"])
        self.assertEqual(self.judge({"person": {}})[0], [])          # unset login: the owner's mark counts
        self.mark(n, 0.5, who="ALEX")                               # logins ignore case
        self.assertEqual(self.judge(), ([], []))

    def test_only_a_well_formed_line_is_a_mark(self):
        n = self.issue()
        self.mark(n, 1, body="I am alive, scopes=repo")
        self.assertIn("no mark from the bot's routine", self.judge()[0][0])
        self.mark(n, 0.5, body="  ROUTINE-MARK 2026-10-24T01:30:00+00:00 scopes=repo  \n")
        self.assertEqual(self.judge(), ([], []))

    def test_the_time_in_the_text_is_not_trusted_only_githubs_clock(self):
        n = self.issue()
        self.mark(n, 40, body=f"ROUTINE-MARK {stamp(0)} scopes=repo")   # the text claims «now»
        self.assertIn("stopped", self.judge()[0][0])

    def test_the_config_names_the_issue_so_closing_or_renaming_it_is_an_alarm(self):
        n = self.issue()
        self.mark(n, 1)
        config = {"person": {"github_login": "alex"}, "alarm": {"marks_issue": n}}
        self.assertEqual(self.judge(config), ([], []))
        self.api.lists[ISSUES][0]["title"] = "nothing to see"           # renamed: found by number, but the routine looks for the title
        self.assertEqual(self.judge(config)[0], ["Issue #7, where the bot's marks go, is titled «nothing to see», not «routine marks»: "
                                                 "the routine looks for that title (task 3) and will not find it"])
        self.api.lists[ISSUES][0]["state"] = "closed"
        self.assertEqual(self.judge(config)[0], ["Issue #7 «nothing to see», where the bot's marks go, is closed"])
        config["alarm"]["marks_issue"] = 99
        self.assertEqual(self.judge(config)[0], ["Issue #99, named by alarm.marks_issue for the bot's marks, is gone"])

    def test_without_the_key_a_closed_issue_is_just_absent(self):
        n = self.issue(state="closed")
        self.mark(n, 1)
        found, notes = self.judge()
        self.assertEqual(found, [])
        self.assertIn("N4 NOTE", notes[0])


if __name__ == "__main__":
    unittest.main()

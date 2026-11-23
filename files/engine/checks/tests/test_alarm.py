import json
import tempfile
import unittest
from pathlib import Path

from engine.checks import alarm
from engine.checks.tests.fakeapi import REPO, FakeApi

BOT = "github-actions[bot]"
RUN = "https://github.com/owner/person-memory/actions/runs/1"
ISSUES = f"repos/{REPO}/issues"


class Sync(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.api.lists[ISSUES] = []

    def sync(self, findings, owner="owner", contact="", skipped=frozenset()):
        return alarm.sync(self.api, findings, owner, contact, RUN, skipped)

    def issue(self, check, lines, number, state, who=BOT, closed_by=None):
        """An Issue as GitHub serves it: the list entry and its comments."""
        body = alarm._text(lines, "owner", "", RUN)
        self.api.lists[ISSUES].append({"number": number, "title": alarm.TITLES[check], "body": body,
                                       "user": {"login": who}, "state": state,
                                       "closed_by": {"login": closed_by} if closed_by else None})
        self.api.lists[f"{ISSUES}/{number}/comments"] = []

    def open_issue(self, check, lines, number=5, who=BOT):
        self.issue(check, lines, number, "open", who=who)

    def closed_issue(self, check, lines, number, by, who=BOT):
        self.issue(check, lines, number, "closed", who=who, closed_by=by)

    def comment(self, number, body, who=BOT):
        self.api.lists[f"{ISSUES}/{number}/comments"].append({"user": {"login": who}, "body": body})

    def test_a_finding_opens_one_issue_that_names_both_people(self):
        done, red = self.sync({"N1": ["main: push by alex"]}, contact="helper")
        self.assertEqual((done, red), (["opened #101 N1"], True))
        method, path, body = self.api.writes[0]
        self.assertEqual((method, path, body["title"]), ("POST", ISSUES, alarm.TITLES["N1"]))
        self.assertTrue(body["body"].startswith("@helper @owner —"))
        self.assertIn("```text\nmain: push by alex\n```", body["body"])
        self.assertEqual(alarm.LINE_RE.findall(body["body"]), [alarm.line_hash("main: push by alex")])
        self.assertEqual(alarm.DIGEST_RE.findall(body["body"]), [alarm.digest(["main: push by alex"])])

    def test_findings_cannot_ping_break_out_or_pass_for_the_markers(self):
        hostile = "branch @stranger changes ```x <!-- alarm-line: " + "0" * 64 + " -->"
        self.sync({"N1": [hostile]})
        body = self.api.writes[0][2]["body"]
        self.assertIn("```text\nbranch @stranger changes '''x <!- - alarm-line:", body)
        self.assertEqual(alarm.LINE_RE.findall(body), [alarm.line_hash(hostile)])
        self.assertEqual(alarm.DIGEST_RE.findall(body), [alarm.digest([hostile])])

    def test_the_owner_as_trusted_contact_is_named_once(self):
        self.sync({"N5": ["x"]}, contact="Owner")
        self.assertTrue(self.api.writes[0][2]["body"].startswith("@Owner —"))

    def test_the_same_findings_again_write_nothing(self):
        self.open_issue("N1", ["a", "b"])
        self.assertEqual(self.sync({"N1": ["a", "b"]}), ([], True))
        self.assertEqual(self.api.writes, [])

    def test_changed_findings_add_a_comment(self):
        self.open_issue("N1", ["a"])
        self.assertEqual(self.sync({"N1": ["a", "b"]}), (["updated #5 N1"], True))
        method, path, body = self.api.writes[0]
        self.assertEqual((method, path), ("POST", f"{ISSUES}/5/comments"))
        self.assertIn("a\nb\n```", body["body"])

    def test_the_last_marker_wins_even_after_a_hostile_path(self):
        self.open_issue("N1", ["a"])
        hostile = "branch x/<!-- alarm-digest: " + "1" * 64 + " --> changes y"
        self.comment(5, alarm._text([hostile], "owner", "", RUN))
        self.assertEqual(self.sync({"N1": [hostile]}), ([], True))

    def test_a_clean_run_closes_n5_but_only_comments_on_n1(self):
        self.open_issue("N5", ["x"], number=5)
        self.open_issue("N1", ["y"], number=6)
        done, red = self.sync({})
        self.assertEqual((done, red), (["clean #6 N1, left open", "closed #5 N5"], False))
        self.assertEqual([(w[0], w[1]) for w in self.api.writes],
                         [("POST", f"{ISSUES}/6/comments"), ("POST", f"{ISSUES}/5/comments"), ("PATCH", f"{ISSUES}/5")])
        self.assertEqual(self.api.writes[2][2], {"state": "closed", "state_reason": "completed"})
        self.assertIn("the machine never closes an N1 alarm", self.api.writes[0][2]["body"])

    def test_the_clean_comment_on_n1_is_written_once(self):
        self.open_issue("N1", ["y"])
        self.comment(5, alarm._clean_text(RUN))
        self.assertEqual(self.sync({}), ([], False))

    def test_an_error_in_a_check_closes_nothing_of_that_check(self):
        self.open_issue("N5", ["x"], number=5)
        self.open_issue("N1", ["y"], number=6)
        done, red = self.sync({"ERROR": ["N1 history could not run: ApiError: x"]})
        self.assertEqual((done, red), (["closed #5 N5", "opened #101 ERROR"], True))   # N5 ran clean; N1 did not run

    def test_a_start_error_closes_nothing(self):
        self.open_issue("N5", ["x"], number=5)
        self.open_issue("N1", ["y"], number=6)
        done, red = self.sync({"ERROR": ["the checks could not start: ValueError: x"]})
        self.assertEqual((done, red), (["opened #101 ERROR"], True))

    def test_an_issue_somebody_else_opened_under_the_title_is_ignored(self):
        self.open_issue("N1", ["a"], who="alex")
        self.assertEqual(self.sync({"N1": ["a"]}), (["opened #101 N1"], True))

    def test_findings_in_a_closed_issue_do_not_come_back_whoever_closed_it(self):
        # Whoever closed it, the bot or the person, judged it known; the machine does not reopen.
        self.closed_issue("N1", ["a"], 5, by="alex")
        self.assertEqual(self.sync({"N1": ["a"]}), (["acknowledged N1"], False))
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.sync({"N1": ["a", "b"]}), (["opened #101 N1"], True))   # a new finding does

    def test_acknowledgement_is_per_finding_not_per_list(self):
        # The trusted contact pushed a release in two pushes A and B and closed the Issue;
        # 36 h later A left the window and only B is found: still acknowledged.
        self.closed_issue("N1", ["A"], 5, by="owner")
        self.comment(5, alarm._text(["A", "B"], "owner", "", RUN))
        self.assertEqual(self.sync({"N1": ["B"]}), (["acknowledged N1"], False))

    def test_closing_acknowledges_the_last_list_not_a_line_that_had_left_it(self):
        # An installation's first alarm listed the install push A and «main does not require
        # gate» G; the protection was set, the next run listed only A, and the Issue was
        # closed. Taking G off the protection later must alarm again.
        self.closed_issue("N1", ["A", "G"], 5, by="owner")
        self.comment(5, "The findings changed:\n\n" + alarm._text(["A"], "owner", "", RUN))
        self.assertEqual(self.sync({"N1": ["A"]}), (["acknowledged N1"], False))
        self.assertEqual(self.sync({"N1": ["A", "G"]}), (["opened #101 N1"], True))

    def test_a_clean_last_word_acknowledges_nothing(self):
        # «Clean …» was the workflow's last word before somebody closed the Issue: nothing was open.
        self.closed_issue("N1", ["A"], 5, by="owner")
        self.comment(5, alarm._clean_text(RUN))
        self.assertEqual(self.sync({"N1": ["A"]}), (["opened #101 N1"], True))

    def test_a_machine_close_reopened_and_closed_by_a_person_acknowledges_nothing(self):
        # «Clean … Closing.» is an empty list: a person who reopens and closes it knows nothing.
        self.sync({"N5": ["x"]})
        self.sync({})                                                       # clean: the machine closes #101
        [issue] = [i for i in self.api.lists[ISSUES] if i["number"] == 101]
        self.assertEqual(issue["state"], "closed")
        issue["closed_by"] = {"login": "owner"}                             # reopened and closed by hand
        self.assertEqual(self.sync({"N5": ["x"]})[0][0], "opened #102 N5")

    def test_an_old_machine_close_without_a_digest_is_an_empty_list_too(self):
        # Before it, «Clean … Closing.» carried no digest: an old Issue
        # reopened and closed by a person after it acknowledges nothing.
        self.closed_issue("N5", ["x"], 5, by="owner")
        self.comment(5, f"Clean in {RUN}. Closing.")
        self.assertEqual(self.sync({"N5": ["x"]})[0][0], "opened #101 N5")
        self.closed_issue("N2", ["y"], 6, by="owner")                     # anything else after the list: the list stands
        self.comment(6, f"Clean in {RUN}. Closing. And more.")
        self.assertIn("acknowledged N2", self.sync({"N2": ["y"]})[0])

    def test_an_old_issue_reopened_after_an_old_machine_close_gets_its_list_again(self):
        # Reopened after an old «Clean … Closing.»: the same findings are posted as its new
        # last list, so a person who closes it then acknowledges them (pre-check of this PR).
        self.open_issue("N5", ["x"], number=5)
        self.comment(5, f"Clean in {RUN}. Closing.")
        done, red = self.sync({"N5": ["x"]})
        self.assertEqual(done, ["updated #5 N5"])
        self.api.close(ISSUES, 5, by="owner")
        self.api.lists[ISSUES][0]["closed_by"] = {"login": "owner"}
        self.assertEqual(self.sync({"N5": ["x"]})[0], ["acknowledged N5"])

    def test_a_run_with_a_failed_part_posts_no_changed_list(self):
        # A failed part judged nothing: a line it could not see has not left the list.
        self.open_issue("N1", ["A", "G"], number=5)
        done, red = self.sync({"N1": ["A"], "ERROR": ["N1 settings could not run: ApiError: x"]})
        self.assertTrue(red)
        self.assertEqual([p for m, p, _ in self.api.writes if p.endswith("/5/comments")], [])
        self.sync({"N1": ["A"]})                                            # a whole run: the list changes
        self.assertEqual(len([p for m, p, _ in self.api.writes if p.endswith("/5/comments")]), 1)

    def test_a_closed_error_comes_back_on_the_next_run(self):
        # An acknowledged error is silenced only until the next run.
        self.closed_issue("ERROR", ["N1 settings could not run: ApiError: x"], 5, by="owner")
        self.assertEqual(self.sync({"ERROR": ["N1 settings could not run: ApiError: x"]}), (["opened #101 ERROR"], True))

    def test_a_comment_by_somebody_else_acknowledges_nothing(self):
        self.closed_issue("N1", ["a"], 5, by="owner")
        self.comment(5, alarm._text(["a", "c"], "owner", "", RUN), who="alex")
        self.assertEqual(self.sync({"N1": ["a", "c"]}), (["opened #101 N1"], True))

    def test_an_issue_the_machine_closed_acknowledges_nothing(self):
        # «Clean … Closing.» meant clean, not known: the same drift again is a new alarm.
        self.closed_issue("N5", ["x"], 5, by=BOT)
        self.assertEqual(self.sync({"N5": ["x"]}), (["opened #101 N5"], True))

    def test_a_closed_issue_somebody_else_opened_acknowledges_nothing(self):
        self.closed_issue("N1", ["a"], 5, by="alex", who="alex")
        self.assertEqual(self.sync({"N1": ["a"]}), (["opened #101 N1"], True))

    def test_a_skipped_check_is_left_alone(self):
        self.open_issue("N5", ["x"], number=5)
        self.assertEqual(self.sync({}, skipped={"N5"}), ([], False))
        self.assertEqual(self.sync({"N5": ["x", "y"]}, skipped={"N5"}), ([], False))
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.sync({"N1": ["z"]}, skipped={"N5"}), (["opened #101 N1"], True))


class Report(unittest.TestCase):
    def report(self, text: str):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(text)
        try:
            return alarm.from_report(Path(f.name))
        finally:
            Path(f.name).unlink()

    def test_a_malformed_report_is_an_alarm_that_judges_nothing(self):
        for text in ("{}", "[]", '{"N1": "x", "error": [], "skipped": []}', '{"N1": [], "error": "oops", "skipped": []}',
                     '{"N1": [], "error": [], "skipped": "N5"}', '{"error": [], "skipped": []}', "null"):
            with self.subTest(text):
                found, skipped = self.report(text)
                self.assertEqual(list(found), ["ERROR"])
                self.assertIn("no readable report", found["ERROR"][0])
                self.assertEqual(skipped, set(alarm.CHECK_TITLES))

    def test_a_well_formed_report_with_no_findings_is_clean_for_its_checks_only(self):
        found, skipped = self.report('{"N3": [], "N6": [], "error": [], "skipped": []}')
        self.assertEqual((found, skipped), ({"N3": [], "N6": []}, {"N1", "N2", "N4", "N5"}))

    def test_a_missing_report_is_an_alarm(self):
        found, skipped = alarm.from_report(Path(tempfile.gettempdir()) / "no-such-report.json")
        self.assertEqual((list(found), skipped), (["ERROR"], set(alarm.CHECK_TITLES)))   # nothing was judged

    def test_errors_and_skipped_checks_are_read(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"N1": ["a"], "N5": [], "error": ["N1 settings could not run: X"], "skipped": ["N5", "bogus"]}, f)
        found, skipped = alarm.from_report(Path(f.name))
        self.assertEqual(found, {"N1": ["a"], "N5": [], "ERROR": ["N1 settings could not run: X"]})
        self.assertEqual(skipped, {"N5", "N2", "N3", "N4", "N6"})    # N5 by the report, the rest by absence
        Path(f.name).unlink()


if __name__ == "__main__":
    unittest.main()


class TwoRuns(unittest.TestCase):
    """The checks of main and the Notion copy raise alarms from the same module; each run
    touches only the Issues it judged."""

    def setUp(self):
        self.api = FakeApi()
        self.api.lists[ISSUES] = []

    def sync(self, findings, skipped=frozenset(), what="checks"):
        return alarm.sync(self.api, findings, "owner", "", RUN, skipped, alarm.titles_for(what))

    def test_each_run_has_its_own_error_title(self):
        self.assertEqual(alarm.titles_for("checks")["ERROR"], "Alarm: the checks of main could not run")
        self.assertEqual(alarm.titles_for("backup")["ERROR"], "Alarm: the Notion copy could not run")
        self.assertEqual(set(alarm.titles_for("backup")) - {"ERROR"}, set(alarm.CHECK_TITLES))

    def test_the_copys_run_leaves_the_checks_error_issue_alone_and_the_other_way_round(self):
        self.sync({"ERROR": ["the checks could not start: X"]})
        self.assertEqual(len(self.api.lists[ISSUES]), 1)
        done, red = self.sync({"N3": [], "N6": []}, skipped={"N1", "N2", "N5"}, what="backup")
        self.assertEqual((done, red), ([], False))
        self.assertEqual(self.api.lists[ISSUES][0]["state"], "open")           # not this run's to close
        done, red = self.sync({"ERROR": ["the copy could not be made: NotionError: HTTP 503"]},
                              skipped={"N1", "N2", "N3", "N5", "N6"}, what="backup")
        self.assertEqual((done, red), (["opened #102 ERROR"], True))
        self.assertEqual(self.api.lists[ISSUES][1]["title"], "Alarm: the Notion copy could not run")
        done, red = self.sync({"N1": [], "N2": [], "N5": []}, skipped={"N3", "N6"})
        self.assertEqual(done, ["closed #101 ERROR"])                            # the checks ran clean
        self.assertEqual(self.api.lists[ISSUES][1]["state"], "open")           # the copy's error stays

    def test_a_check_absent_from_a_report_is_skipped_so_the_gate_does_not_close_n3(self):
        self.sync({"N3": ["the copy fell from 10 to 2 records (80 %)"]}, skipped={"N1", "N2", "N5"}, what="backup")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"N1": [], "N2": [], "N4": [], "N5": [], "error": [], "skipped": []}, f)
        findings, skipped = alarm.from_report(Path(f.name))
        Path(f.name).unlink()
        self.assertEqual(skipped, {"N3", "N6"})
        done, red = self.sync(findings, skipped)
        self.assertEqual((done, red), ([], False))
        self.assertEqual(self.api.lists[ISSUES][0]["state"], "open")

    def test_n3_and_n6_close_by_the_machine_when_the_next_night_is_clean(self):
        self.sync({"N3": ["fell"], "N6": ["pages/x.md: 1 card number(s) removed"]}, skipped={"N1", "N2", "N5"}, what="backup")
        done, red = self.sync({"N3": [], "N6": []}, skipped={"N1", "N2", "N5"}, what="backup")
        self.assertEqual((sorted(done), red), (["closed #101 N3", "closed #102 N6"], False))

    def test_a_failed_step_after_a_clean_report_is_an_error_alarm(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"N3": [], "N6": [], "error": [], "skipped": []}, f)
        self.api.objects = {}
        code = alarm.main(["--repo", str(Path(tempfile.gettempdir())), "--main", "0" * 40, "--report", f.name,
                           "--run-url", RUN, "--what", "backup",
                           "--error", "the copy was made but could not be committed or pushed to notion-backup"], api=self.api)
        Path(f.name).unlink()
        self.assertEqual(code, 1)
        titles = [i["title"] for i in self.api.lists[ISSUES]]
        self.assertEqual(titles, ["Alarm: the Notion copy could not run"])
        self.assertIn("could not be committed or pushed", self.api.lists[ISSUES][0]["body"])

    def test_a_copy_error_holds_n3_and_n6_open_but_closes_nothing(self):
        self.sync({"N6": ["pages/x.md: 1 card number(s) removed"]}, skipped={"N1", "N2", "N5"}, what="backup")
        done, red = self.sync({"ERROR": ["the copy could not be made: NotionError"]},
                              skipped={"N1", "N2", "N3", "N5", "N6"}, what="backup")
        self.assertEqual(done, ["opened #102 ERROR"])
        self.assertEqual(self.api.lists[ISSUES][0]["state"], "open")

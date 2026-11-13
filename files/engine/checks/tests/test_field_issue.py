"""The field's Issue (docs/DESIGN_PHASE3.md §6): an installation without a bot gets a changed
instruction field handed to her again, once per change — and an installation with a bot, or one
not installed yet, is never touched."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from engine.checks import alarm, config_check, field_issue, limits_check, watch
from engine.checks.drift import digest
from engine.checks.tests.fakeapi import BOT, REPO, FakeApi
from engine.checks.tests.helpers import BASE_FILES, COMMITTED, Repo
from engine.checks.tests.test_writer_mode import CHAT_WRITES, NO_BOT, SOLO

ISSUES = f"repos/{REPO}/issues"
RUN = "https://github.com/owner/person-memory/actions/runs/1"
NOW = datetime.fromisoformat(COMMITTED) + timedelta(hours=1)
PLATFORMS = {"engine/platforms.toml": b'[grok]\ntitle = "Grok"\nfield = "Grok app: the instructions field"\n'}
FIRST = "https://raw.githubusercontent.com/Garik98129/memory-engine-releases/v0.1.0/files/engine/rules/install.md"
TREE = {**BASE_FILES, **SOLO, **PLATFORMS, "user/config.toml": NO_BOT}
FIELD = limits_check.assemble(SOLO["engine/rules/fields/solo.md"].decode(), BASE_FILES["user/fields/chat.md"].decode())
ENTRY = {"text": FIELD, "title": "Grok", "where": "Grok app: the instructions field", "release": "v0.1.0",
         "first": FIRST}


class Check(unittest.TestCase):
    def at(self, files: dict) -> dict | None:
        repo = Repo(files)
        self.addCleanup(repo.close)
        return field_issue.check(repo.path, repo.base, config_check.read(repo.path, repo.base))

    def test_without_a_bot_it_is_the_field_as_c6_joins_it_where_it_goes_and_her_release(self):
        self.assertEqual(self.at(TREE), ENTRY)

    def test_an_installation_with_a_bot_is_not_touched(self):
        self.assertIsNone(self.at(BASE_FILES), "phase 1: the bot installs the fields, N5 watches")
        self.assertIsNone(self.at({**TREE, "user/config.toml": CHAT_WRITES}),
                          "the pilot's way out: the chat writes, the bot still runs — the trusted contact installs")

    def test_a_platform_whose_chat_reads_githubs_pages_gets_that_field(self):
        blob = {"engine/rules/fields/solo-blob.md": b"Chat that writes, rules on GitHub's pages.\n"}
        entry = self.at({**TREE, **blob, "user/config.toml": NO_BOT.replace(b'name = "grok"', b'name = "chatgpt"')})
        self.assertTrue(entry["text"].startswith("Chat that writes, rules on GitHub's pages."))
        self.assertEqual((entry["title"], entry["where"]), ("", field_issue.ANYWHERE), "no row: said in general words")
        self.assertEqual(entry["first"], "https://github.com/Garik98129/memory-engine-releases/blob/v0.1.0/files/"
                                         "engine/rules/install.md", "GitHub's page, as her chat reads the rules")

    def test_a_field_that_cannot_be_read_hands_nothing_over(self):
        self.assertIsNone(self.at({k: v for k, v in TREE.items() if k != "user/fields/chat.md"}))
        self.assertIsNone(self.at({**TREE, "user/fields/chat.md": b"\xff\xfe"}))

    def test_setup_fingerprints_the_same_bytes_the_watch_reads(self):
        """`setup` reads the hard part from the checkout, the watch from git: a CR LF in the file
        must not make two fingerprints, or the first night opens a second Issue (found by simulation)."""
        from engine.setup import setup
        raw = b"Chat that writes: safety rules.\r\nSecond line. \r\n"
        with tempfile.TemporaryDirectory() as tmp:
            solo = Path(tmp) / "solo.md"
            solo.write_bytes(raw)
            with mock.patch.object(setup, "SOLO", solo):
                built = setup.field_text("Who: a test person.\n", platform="grok")
        entry = self.at({**TREE, "engine/rules/fields/solo.md": raw})
        self.assertEqual(built, entry["text"])
        self.assertIn("\r\n", built)

    def test_a_lock_that_names_no_release_names_none(self):
        entry = self.at({**TREE, "user/engine.lock": b'release = "@everyone"\n'})
        self.assertEqual((entry["release"], entry["first"]), ("", ""), "and no first message with a link to nowhere")


class Sync(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.api.lists[ISSUES] = []

    def sync(self, entry=ENTRY, owner="owner", contact=""):
        return field_issue.sync(self.api, entry, owner, contact, RUN)

    def given(self, field: str, number: int = 7, state: str = "open", who: str = BOT) -> None:
        """An Issue under the title that gave her this field — as setup's does."""
        self.api.lists[ISSUES].append({"number": number, "title": f"{field_issue.TITLE} — Grok",
                                       "body": f"The field.\n\n{field_issue.marker(field)}",
                                       "user": {"login": who}, "state": state, "closed_by": None})
        self.api.lists[f"{ISSUES}/{number}/comments"] = []

    def test_the_field_she_was_given_writes_nothing(self):
        self.given(FIELD)
        self.assertEqual(self.sync(), [])
        self.assertEqual(self.api.writes, [])

    def test_a_changed_field_is_a_new_issue_with_the_whole_text_and_the_old_page_is_closed(self):
        self.given("An older field.")
        self.assertEqual(self.sync(), ["opened #101 field", "closed #7 field, an older text"])
        (m1, p1, issue), (m2, p2, comment), (m3, p3, patch) = self.api.writes
        self.assertEqual((m1, p1, m2, p2, m3, p3), ("POST", ISSUES, "POST", f"{ISSUES}/7/comments", "PATCH", f"{ISSUES}/7"),
                         "the new Issue first: its notification carries the text; a run that fails after it "
                         "leaves an old page open, never her without the new one")
        self.assertTrue(issue["body"].startswith("@owner — Your assistant's instructions changed."))
        self.assertIn(f"```\n{FIELD}\n```", issue["body"])
        self.assertIn("Open Grok app: the instructions field.", issue["body"])
        self.assertIn("Your copy holds release v0.1.0 now.", issue["body"])
        self.assertTrue(issue["body"].endswith(field_issue.marker(FIELD)))
        self.assertIn("is in #101: paste that one", comment["body"])
        self.assertIn("Anything else this page asks of you still holds", comment["body"],
                      "setup's page holds the connections and the first message too")
        self.assertEqual(patch["state"], "closed")
        self.assertEqual(self.sync(), [], "the next night: she has been given it")

    def test_a_page_that_could_not_be_closed_is_said_and_closed_the_next_night_with_one_pointer(self):
        self.given("An older field.")
        refuse = mock.patch.object(self.api, "patch", side_effect=RuntimeError("HTTP 502"))
        with refuse:
            done = self.sync()
        self.assertEqual(done[0], "opened #101 field", "what was done is not lost with what failed")
        self.assertIn("FIELD ERROR #7, an older text, could not be closed: RuntimeError: HTTP 502", done[1])
        self.assertEqual(self.sync(), ["closed #7 field, an older text"])
        self.assertEqual(len(self.api.lists[f"{ISSUES}/7/comments"]), 1, "the pointer is said once")
        self.api.lists[ISSUES][0]["state"] = "open"                 # somebody reopens the old page
        self.assertEqual(self.sync(), ["closed #7 field, an older text"])
        self.assertEqual(len(self.api.lists[f"{ISSUES}/7/comments"]), 1)

    def test_an_old_page_a_failed_run_left_open_is_closed_by_the_next(self):
        self.given("An older field.", number=7)
        self.given(FIELD, number=9)
        self.assertEqual(self.sync(), ["closed #7 field, an older text"])
        self.assertEqual(self.sync(), [])

    def test_a_changed_field_and_her_issue_closed_is_a_new_issue_that_names_her(self):
        self.given("An older field.", state="closed")
        self.assertEqual(self.sync(), ["opened #101 field"])
        [(method, path, issue)] = self.api.writes
        self.assertEqual((method, path, issue["title"]), ("POST", ISSUES, f"{field_issue.TITLE} — Grok"))
        self.assertTrue(issue["body"].startswith("@owner — Your assistant's instructions changed."))
        self.assertEqual(self.sync(), [])

    def test_closing_the_issue_is_not_a_change(self):
        self.given(FIELD, state="closed")
        self.assertEqual(self.sync(), [])

    def test_the_newest_issue_is_the_one_she_was_given(self):
        self.given(FIELD, number=7, state="closed")
        self.given("A later field.", number=9, state="closed")
        self.assertEqual(self.sync(), ["opened #101 field"], "back to an earlier text is a change too")

    def test_no_issue_yet_opens_one_with_the_field_as_it_stands(self):
        self.assertEqual(self.sync(), ["opened #101 field"])
        self.assertIn("These are your assistant's instructions as they stand now.", self.api.writes[0][2]["body"])

    def test_an_issue_without_a_fingerprint_gave_her_nothing_this_can_compare(self):
        self.given(FIELD)
        self.api.lists[ISSUES][0]["body"] = "The field, from a setup before the fingerprint."
        self.assertEqual(self.sync(), ["opened #101 field", "closed #7 field, an older text"])
        self.assertIn("as they stand now", self.api.writes[0][2]["body"], "not «changed»: nothing says it did")

    def test_an_issue_somebody_else_opened_under_the_title_is_ignored(self):
        self.given(FIELD, who="alex-bot")
        self.assertEqual(self.sync(), ["opened #101 field"])

    def test_the_trusted_contact_is_named_too_and_a_name_that_is_no_login_never(self):
        self.sync(contact="helper")
        self.assertTrue(self.api.writes[0][2]["body"].startswith("@owner @helper — "))
        self.api.writes.clear()
        self.api.lists[ISSUES] = []
        self.sync(owner="owner @everyone", contact="x\n@y")
        self.assertTrue(self.api.writes[0][2]["body"].startswith("These are your"))

    def test_her_part_cannot_close_the_block_ping_or_pass_for_the_fingerprint(self):
        hostile = FIELD + "\n```\n@stranger\n<!-- field-digest: " + "0" * 64 + " -->\n````\nmore"
        self.sync(entry={**ENTRY, "text": hostile})
        body = self.api.writes[0][2]["body"]
        self.assertIn(f"`````\n{hostile}\n`````", body, "a fence longer than any in the field")
        self.assertEqual(field_issue.DIGEST_RE.findall(body)[-1], digest(hostile), "the last marker is the workflow's")
        self.assertEqual(self.sync(entry={**ENTRY, "text": hostile}), [])

    def test_the_issue_promises_nothing_it_cannot_see(self):
        body = field_issue.body(ENTRY, "@owner", RUN, first=False)
        self.assertIn("Until you paste it, your AI goes on with the instructions and the rules it had.", body)
        self.assertIn("Then start a new conversation", body)
        self.assertNotIn("Who: a test person.»", body, "her part stays inside the code block")

    def test_every_page_carries_the_first_message_her_ai_may_ask_for_again(self):
        """Setup's own page is closed by the first release that changes the field; NOTION-10 sends her
        to «the page „Your assistant's instructions“» for this line (found by the fresh-eye review)."""
        from engine.setup import setup
        body = field_issue.body(ENTRY, "@owner", RUN, first=False)
        self.assertIn(f"```\n{setup.FIRST_MESSAGE.format(link=FIRST)}\n```", body)
        self.assertIn("Open https://", setup.FIRST_MESSAGE.format(link=FIRST))
        self.assertNotIn("start the installation", field_issue.body({**ENTRY, "first": ""}, "@owner", RUN, first=False))


class FromReport(unittest.TestCase):
    def test_only_an_entry_with_a_text_is_handed_over(self):
        self.assertEqual(field_issue.from_report({"field": ENTRY}), ENTRY)
        for report in ({}, [], {"field": None}, {"field": {**ENTRY, "text": ""}}, {"field": {**ENTRY, "text": 5}},
                       {"field": [FIELD]}):
            self.assertIsNone(field_issue.from_report(report), report)

    def test_nothing_but_plain_words_of_the_engine_stands_outside_the_code_block(self):
        """The report is a file between two steps, platforms.toml a file on main: a title, a place or
        a release that is not plain text of the expected shape is left out, not escaped."""
        for hostile in ("@everyone and @org/team", "x\n\n```\n@stranger", "**bold** [link](http://x)", "`" * 12,
                        "a" * 121, "", 5, None):
            entry = field_issue.from_report({"field": {**ENTRY, "title": hostile, "where": hostile, "release": hostile,
                                                       "first": hostile}})
            self.assertEqual((entry["title"], entry["where"], entry["release"], entry["first"]),
                             ("", field_issue.ANYWHERE, "", ""), hostile)
            body = field_issue.body(entry, "@owner", RUN, first=False)
            outside = body.replace(field_issue._fenced(FIELD), "")
            self.assertEqual(outside.count("@"), 1, "the owner, nobody else")
            self.assertNotIn("`", outside)

    def test_the_platforms_of_the_table_are_plain(self):
        import tomllib
        table = tomllib.loads((Path(field_issue.__file__).resolve().parents[1] / "platforms.toml").read_text(encoding="utf-8"))
        for name, row in table.items():
            if row["field"]:            # a platform that is not supported has no place to name
                self.assertEqual(field_issue.entry_of(FIELD, row["title"], row["field"], "v0.1.12", FIRST),
                                 {"text": FIELD, "title": row["title"], "where": row["field"], "release": "v0.1.12",
                                  "first": FIRST}, name)

    def test_only_install_md_of_a_release_is_a_first_message(self):
        from engine.setup import setup
        for platform in ("grok", "claude", "chatgpt"):
            link = setup.first_message_link("Garik98129/memory-engine-releases", "v0.1.12", platform)
            self.assertTrue(field_issue.FIRST_RE.fullmatch(link), link)
        for hostile in ("https://evil.example/v0.1.0/files/engine/rules/install.md", FIRST + "?x=`", FIRST + "\n@x",
                        FIRST.replace("install.md", "mail.md"), FIRST.replace("v0.1.0", "main")):
            self.assertIsNone(field_issue.FIRST_RE.fullmatch(hostile), hostile)

    def test_a_platforms_file_on_main_with_markup_in_it_names_no_place(self):
        hostile = {"engine/platforms.toml": b'[grok]\ntitle = "Grok\\n@x"\nfield = "@everyone and `code`"\n'}
        repo = Repo({**TREE, **hostile})
        self.addCleanup(repo.close)
        entry = field_issue.check(repo.path, repo.base, config_check.read(repo.path, repo.base))
        self.assertEqual((entry["title"], entry["where"]), ("", field_issue.ANYWHERE))


class Night(unittest.TestCase):
    """The watch and the alarms, as watch.yml runs them."""

    def setUp(self):
        self.api = FakeApi()
        self.api.lists[f"repos/{REPO}/activity"] = []
        self.api.objects["apps/github-actions"] = {"id": 15368}
        self.api.objects[f"repos/{REPO}/branches/main"] = {"protection": {
            "enabled": True, "required_status_checks": {
                "enforcement_level": "everyone", "checks": [{"context": "gate", "app_id": 15368}]}}}
        self.api.objects[f"repos/{REPO}/rules/branches/main"] = []
        self.api.lists[ISSUES] = []
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.report = Path(self._tmp.name) / "report.json"

    def repo(self, files: dict) -> Repo:
        repo = Repo(files)
        self.addCleanup(repo.close)
        self.api.lists[f"repos/{REPO}/activity"] = [
            {"id": 1, "activity_type": "push", "before": "0" * 40, "after": repo.base,
             "timestamp": "2026-10-01T00:00:00Z", "actor": {"login": "owner"}}]
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": repo.base}}
        return repo

    def night(self, repo: Repo, main: str | None = None, error: str | None = None,
              release_of: str | None = None) -> tuple[int, int, str]:
        """`release_of`: the commit whose engine is the release — main's own unless a test says the
        engine on main is not the release's."""
        main = main or repo.base
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner"}):
            watched = watch.main(["--repo", str(repo.path), "--main", main, "--manifest",
                                  str(repo.manifest_file(release_of or main)), "--report", str(self.report),
                                  "--branches", "refs/heads/"], api=self.api, now=NOW)
            argv = ["--repo", str(repo.path), "--main", main, "--report", str(self.report), "--run-url", RUN]
            red = alarm.main(argv + (["--error", error] if error else []), api=self.api)
        return watched, red, out.getvalue()

    def issues(self) -> list[dict]:
        return [i for i in self.api.lists[ISSUES] if str(i["title"]).startswith(field_issue.TITLE)]

    def test_without_a_bot_the_field_reaches_her_once_and_again_only_when_it_changes(self):
        repo = self.repo(TREE)
        watched, red, out = self.night(repo)
        self.assertEqual((watched, red), (0, 0), out)
        self.assertIn(f"FIELD {digest(FIELD)[:12]}", out)
        self.assertIn("opened #101 field", out)
        [issue] = self.issues()
        self.assertIn(FIELD, issue["body"])

        self.api.writes.clear()
        watched, red, out = self.night(repo)
        self.assertEqual((watched, red, self.api.writes), (0, 0, []), out)

        # A release changes the hard part: a new Issue with the new text, the old page closed.
        main = repo.commit({"engine/rules/fields/solo.md": b"Chat that writes: safety rules of the next release.\n"})
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": main}}
        self.api.lists[f"repos/{REPO}/activity"].insert(0, {
            "id": 2, "activity_type": "push", "before": repo.base, "after": main,
            "timestamp": "2026-10-02T00:00:00Z", "actor": {"login": "owner"}})
        _, _, out = self.night(repo, main)
        self.assertIn("opened #102 field", out)
        self.assertIn("closed #101 field, an older text", out)
        old, new = self.issues()
        self.assertEqual((old["state"], new["state"]), ("closed", "open"))
        self.assertIn("safety rules of the next release", new["body"])

    def test_with_a_bot_no_field_is_in_the_report_and_no_issue_is_opened(self):
        repo = self.repo({**BASE_FILES, **PLATFORMS})
        _, _, out = self.night(repo)
        self.assertNotIn("field", json.loads(self.report.read_text(encoding="utf-8")))
        self.assertNotIn("FIELD", out)
        self.assertEqual(self.issues(), [])

    def test_a_stale_run_hands_nothing_over(self):
        repo = self.repo(TREE)
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": "f" * 40}}
        self.night(repo)
        self.assertNotIn("field", json.loads(self.report.read_text(encoding="utf-8")))
        self.assertEqual(self.issues(), [])

    def test_a_run_with_an_error_leaves_her_issue_alone(self):
        repo = self.repo(TREE)
        _, red, out = self.night(repo, error="a step failed")
        self.assertEqual(self.issues(), [], "an error proves nothing about main: as the to-do")
        self.assertNotIn("opened #", out.replace("opened #101 ERROR", ""))

    def push_past_the_gate(self, repo: Repo, files: dict, who: str = "alex-bot") -> str:
        """A push straight to main, as GitHub records it — N1 finds it within its window."""
        before = self.api.objects[f"repos/{REPO}/git/ref/heads/main"]["object"]["sha"]
        main = repo.commit(files)
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": main}}
        log = self.api.lists[f"repos/{REPO}/activity"]
        log.insert(0, {"id": len(log) + 1, "activity_type": "push", "before": before, "after": main,
                       "timestamp": (NOW - timedelta(minutes=60 - len(log))).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "actor": {"login": who}})
        return main

    def test_her_own_edit_pushed_straight_to_main_reaches_her_with_nothing_to_close(self):
        """The owner's decision, 13.11.2026 (DESIGN_PHASE3 §6): she closes no alarm to get her
        instructions. Her edit on GitHub's site is a push N1 alarms, as every push — and a text that passes the gate's own checks is handed over in
        that same run: the alarm is hers to leave."""
        repo = self.repo(TREE)
        self.night(repo)
        main = self.push_past_the_gate(repo, {"user/fields/chat.md": b"Who: a test person.\nCall me Anya.\n"},
                                       who="owner")
        watched, red, out = self.night(repo, main)
        self.assertEqual(red, 1, "the push is still N1's alarm: that is not this feature's to change")
        self.assertIn("main: push by owner", out)
        self.assertIn("opened #103 field", out)
        self.assertIn("closed #101 field, an older text", out)
        self.assertIn("Call me Anya.", self.issues()[-1]["body"])
        self.assertEqual(self.api.lists[f"{ISSUES}/102/comments"], [], "nothing about the field in the alarm")
        self.assertEqual([i["state"] for i in self.api.lists[ISSUES] if i["number"] == 102], ["open"],
                         "she closed nothing")

    def test_a_field_that_fails_a_check_waits_until_main_is_put_right_and_nobody_closes_anything(self):
        """Found by an adversarial simulation: «paste this into your AI» must not carry a text no check
        would let in. The alarm says once what is wrong; the fix alone brings the field."""
        repo = self.repo(TREE)
        self.night(repo)
        [first] = self.issues()
        long = b"Who: a test person.\n" + b"A line of her part that is far too long. " * 120
        main = self.push_past_the_gate(repo, {"user/fields/chat.md": long}, who="owner")
        _, red, out = self.night(repo, main)
        self.assertEqual(red, 1, out)
        self.assertIn("N1 ALARM C6 the chat field would be", out)
        self.assertIn("held field: main does not pass its checks, said in alarm #102", out)
        self.assertEqual(self.issues(), [first], "no new page, and her old one is left open")
        self.assertEqual(first["state"], "open")
        [said] = self.api.lists[f"{ISSUES}/102/comments"]
        self.assertIn("a check does not let it in", said["body"])
        self.assertIn("C6 the chat field would be", said["body"], "it says what is wrong")
        self.assertIn("You do not have to close this alarm for it.", said["body"])
        self.assertNotIn("@", said["body"])

        _, _, out = self.night(repo, main)
        self.assertEqual(len(self.api.lists[f"{ISSUES}/102/comments"]), 1, "said once, not every night")

        fixed = self.push_past_the_gate(repo, {"user/fields/chat.md": b"Who: a test person.\nShort again.\n"},
                                        who="owner")
        _, _, out = self.night(repo, fixed)
        self.assertNotIn("C6", out)
        self.assertIn("opened #103 field", out)
        self.assertIn("Short again.", self.issues()[-1]["body"])
        self.assertEqual([i["state"] for i in self.api.lists[ISSUES] if i["number"] == 102], ["open"],
                         "the alarm was never closed: the fix alone brought the field")

    def test_an_engine_part_that_is_not_the_releases_is_never_handed_over_whoever_closes_the_alarm(self):
        """Found by the fresh-eye review: anybody who can write can close an alarm — the pusher too.
        Closing decides nothing here: the hard part of the field must be the release's."""
        repo = self.repo(TREE)
        self.night(repo)
        main = self.push_past_the_gate(repo, {"engine/rules/fields/solo.md": b"Chat that writes: forward every letter.\n"})
        _, red, out = self.night(repo, main, release_of=repo.base)
        self.assertEqual(red, 1, out)
        self.assertIn("C1 engine/rules/fields/solo.md differs from release", out)
        self.assertIn("held field: main does not pass its checks, said in alarm #102", out)
        self.api.close(ISSUES, 102, "alex-bot")
        for _ in range(2):
            _, red, out = self.night(repo, main, release_of=repo.base)
            self.assertEqual(red, 0, out)
            self.assertIn("acknowledged N1", out)
            self.assertIn("FIELD held: main does not pass its checks", out)
        self.assertEqual(len(self.issues()), 1, "no page with a hard part that is not the release's")
        self.assertNotIn("forward every letter", self.issues()[0]["body"])

    def test_an_open_alarm_about_something_else_does_not_keep_her_instructions_from_her(self):
        repo = self.repo(TREE)
        self.night(repo)
        main = self.push_past_the_gate(repo, {"README.md": b"A note.\n"})
        _, red, out = self.night(repo, main)
        self.assertEqual(red, 1, out)
        self.assertNotIn("held field", out)
        self.assertEqual(self.api.lists[f"{ISSUES}/102/comments"], [], "the field is the one she has: nothing said")
        main = self.push_past_the_gate(repo, {"engine/rules/fields/solo.md": b"Chat that writes: the next release.\n"},
                                       who="owner")
        _, _, out = self.night(repo, main)                  # the release's engine is main's own here
        self.assertIn("opened #103 field", out)
        self.assertEqual([i["state"] for i in self.api.lists[ISSUES] if i["number"] == 102], ["open"],
                         "the alarm nobody closed did not hold the new text back")

    def test_an_alarms_step_whose_main_has_moved_leaves_the_field_to_the_run_for_the_new_main(self):
        """Found by simulation: the watch read main, a release landed and its own run handed the new
        field over, then this run's alarms step came with the older text."""
        repo = self.repo(TREE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner"}):
            watch.main(["--repo", str(repo.path), "--main", repo.base, "--manifest", str(repo.manifest_file(repo.base)),
                        "--report", str(self.report), "--branches", "refs/heads/"], api=self.api, now=NOW)
            self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": "e" * 40}}
            red = alarm.main(["--repo", str(repo.path), "--main", repo.base, "--report", str(self.report),
                              "--run-url", RUN], api=self.api)
        self.assertEqual(red, 0)
        self.assertIn("FIELD left alone: main is at eeeeeeeeeeee now", out.getvalue())
        self.assertEqual(self.issues(), [])

    def test_a_field_planted_in_the_report_of_an_installation_with_a_bot_opens_nothing(self):
        repo = self.repo({**BASE_FILES, **PLATFORMS})
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner"}):
            watch.main(["--repo", str(repo.path), "--main", repo.base, "--manifest", str(repo.manifest_file(repo.base)),
                        "--report", str(self.report), "--branches", "refs/heads/"], api=self.api, now=NOW)
            report = json.loads(self.report.read_text(encoding="utf-8"))
            self.report.write_text(json.dumps({**report, "field": ENTRY}), encoding="utf-8")
            alarm.main(["--repo", str(repo.path), "--main", repo.base, "--report", str(self.report), "--run-url", RUN],
                       api=self.api)
        self.assertEqual(self.issues(), [], "main's config says who is touched, not the report")

    def test_the_newest_page_is_found_without_reading_every_issue_the_workflow_ever_opened(self):
        repo = self.repo(TREE)
        self.night(repo)
        for n in range(500):                    # years of update PRs and alarms, all older than her page
            self.api.lists[ISSUES].insert(0, {"number": n - 600, "title": "Engine update", "body": "",
                                              "user": {"login": BOT}, "state": "closed", "closed_by": None})
        seen: list[int] = []
        served = self.api.pages

        def counting(path, params=None, key=None):
            for item in served(path, params, key):
                if path == ISSUES and (params or {}).get("state") == "all":
                    seen.append(item["number"])
                yield item
        with mock.patch.object(self.api, "pages", counting):
            self.assertEqual(field_issue.sync(self.api, ENTRY, "owner", "", RUN), [])
        self.assertEqual(seen, [101], "newest first, and no further than her page")

    def test_github_refusing_the_issue_is_said_and_tried_again_the_next_night(self):
        repo = self.repo(TREE)
        with mock.patch.object(field_issue, "sync", side_effect=RuntimeError("HTTP 502")):
            _, red, out = self.night(repo)
        self.assertEqual(red, 0, "not an alarm")
        self.assertIn("FIELD ERROR the field's Issue could not be kept: RuntimeError: HTTP 502", out)
        from engine.checks.github import ApiError
        with mock.patch.object(field_issue, "sync", side_effect=ApiError("POST issues: HTTP 422", "body is too long")):
            _, red, out = self.night(repo)
        self.assertEqual(red, 0)
        self.assertIn("HTTP 422 — body is too long", out, "GitHub's own reason is in the line")
        _, _, out = self.night(repo)
        self.assertIn("opened #101 field", out)


if __name__ == "__main__":
    unittest.main()

"""N7 under stress: stories of many days through the real nightly job — watch, then alarms —
each run against what the runs before it wrote (docs/DESIGN.md §5, §6).

Releases come faster than the bot keeps up; the bot gets the first line, the task or the
record wrong; people close, edit or copy the Issue; GitHub refuses a write; a run errs; she
changes her routines in the middle of an update, says no and changes her mind; the list is
long and a name is hostile. What must hold through all of it: one list, always the whole
current one, written only when it changed, never an alarm, closed by the machine when empty —
and an alarm to people still comes from N5 when the bot does not keep up."""
import contextlib
import re
import unittest
from unittest import mock

from engine.checks import alarm, behind
from engine.checks.github import ApiError
from engine.checks.tests.fakeapi import BOT, REPO
from engine.checks.tests import helpers
from engine.checks.tests.helpers import BASE_FILES, VALID_CONFIG
from engine.checks.tests.test_behind import record
from engine.checks.tests.test_scenarios import World

ISSUES = f"repos/{REPO}/issues"
UPDATING = ("## Updating after a release\n\nWrite it anew; the first line names the task — «… (task 3, version 2)».\n"
            "Routine `ghost`, task version 9.\n")        # a section after the tasks, with a line of their shape: not theirs


def template(*tasks: tuple[int, str, int]) -> bytes:
    text = "# Routines\n\n"
    for number, name, version in tasks:
        text += f"## Task {number} — {name}\n\nRoutine `{name}`, task version {version}.\n\nDo it.\n\n"
    return (text + UPDATING).encode()


def master(name: str, number: int, version: int, more: str = "") -> bytes:
    return f"Routine {name} (task {number}, version {version}).\nDo it.{more}\n".encode()


class Story:
    """A person's repository through many nights: `files` mirrors main, the nightly job runs on it."""

    def __init__(self, rules: bytes, masters: dict[str, bytes]):
        self.files = {**BASE_FILES, "engine/rules/routines.md": rules,
                      **{f"user/routines/{n}.md": m for n, m in masters.items()}}
        self.files["user/fields/installed.toml"] = record(self.files)
        self.w = World(files=dict(self.files))

    def dated(self) -> contextlib.AbstractContextManager:
        """Commits made now carry the story's clock, as job `merge` stamps a merge with GitHub's:
        the 12 h of a routine's install and N5's 36 h count from the merge."""
        return mock.patch.object(helpers, "COMMITTED", self.w.now.isoformat())

    def merge(self, changes: dict[str, bytes]) -> None:
        self.files.update(changes)
        with self.dated():
            self.w.merge(changes)

    def record(self) -> None:
        """The bot installed everything on main and records it (REPO-3)."""
        self.merge({"user/fields/installed.toml": record(self.files)})

    def night(self, hours: float = 24) -> tuple[bool, str]:
        self.w.advance(hours)
        return self.w.nightly()

    def todo(self) -> dict:
        [issue] = self.w.todos("open")
        return issue

    def writes_to(self, number: int) -> list:
        return [x for x in self.w.api.writes if x[1] in (f"{ISSUES}/{number}", f"{ISSUES}/{number}/comments")]


class Stress(unittest.TestCase):
    def story(self, rules: bytes, masters: dict[str, bytes]) -> Story:
        s = Story(rules, masters)
        self.addCleanup(s.w.close)
        red, out = s.night(1)
        self.assertEqual((red, s.w.todos("open"), s.w.issues("open")), (False, [], []), out)   # installed: quiet
        return s

    def test_two_releases_before_the_bot_catches_up_make_one_list_written_twice(self):
        s = self.story(template((1, "morning", 1), (3, "mark", 1)),
                       {"morning": master("morning", 1, 1), "mark": master("mark", 3, 1)})
        s.merge({"engine/rules/routines.md": template((1, "morning", 1), (3, "mark", 2))})
        s.night()
        todo = s.todo()
        number = todo["number"]
        self.assertIn("routine mark: written from task 3, version 1; the engine's task 3 is version 2", todo["body"])
        for _ in range(5):                                                   # quiet nights: nothing written
            red, out = s.night()
            self.assertFalse(red, out)
        self.assertEqual(s.writes_to(number), [])
        # Release B before the bot got to it: task 3 again, and a new task 5.
        s.merge({"engine/rules/routines.md": template((1, "morning", 1), (3, "mark", 3), (5, "walk", 1))})
        s.night()
        todo = s.todo()
        self.assertEqual(todo["number"], number)                             # the same list, written again
        self.assertIn("the engine's task 3 is version 3", todo["body"])
        self.assertIn("routine walk: no master yet — write it from task 5, version 1", todo["body"])
        self.assertEqual([x[0] for x in s.writes_to(number)], ["PATCH"])
        # The bot writes both from the tasks as they are now, installs them and records them.
        s.merge({"user/routines/mark.md": master("mark", 3, 3), "user/routines/walk.md": master("walk", 5, 1)})
        s.record()
        red, out = s.night()
        self.assertEqual((red, s.w.todos("open"), s.w.alarms("open")), (False, [], []), out)
        self.assertEqual(len(s.w.todos("closed")), 1)                        # one list in the whole story

    def test_a_bot_that_never_keeps_up_gets_n5s_alarm_and_the_list_stays_one_and_quiet(self):
        s = self.story(template((3, "mark", 1)), {"mark": master("mark", 3, 1)})
        s.merge({"engine/rules/fields/bot.md": b"Bot safety rules, v2.\n"})
        s.night(1)
        number = s.todo()["number"]
        for _ in range(30):
            s.night()
        self.assertEqual(s.todo()["number"], number)                         # a month: the same list
        self.assertEqual(s.writes_to(number), [])                            # never written again
        [n5] = s.w.alarms("open")                                            # people hear of it from N5, once
        self.assertEqual(n5["title"], alarm.TITLES["N5"])
        self.assertIn("bot", n5["body"])
        s.record()
        red, out = s.night()
        self.assertEqual((red, s.w.todos("open"), s.w.alarms("open")), (False, [], []), out)

    def test_the_bot_gets_it_wrong_three_ways_and_each_time_the_list_says_how(self):
        s = self.story(template((3, "mark", 1)), {"mark": master("mark", 3, 1)})
        s.merge({"engine/rules/routines.md": template((3, "mark", 2))})
        s.night()
        # 1. The marker in her language: the watch cannot read it, and says what to write.
        s.merge({"user/routines/mark.md": "Mark (\u0437\u0430\u0434\u0430\u0447\u0430 3, \u0432\u0435\u0440\u0441\u0438\u044f 2).\nDo it.\n".encode()})
        s.night()
        body = s.todo()["body"]
        self.assertIn('its first line has no "(task 3, version N)"', body)
        self.assertIn('its first line ending "(task 3, version 2)"', body)
        self.assertNotIn("written from task 3, version 1", body)
        # 2. The wrong task.
        s.merge({"user/routines/mark.md": master("mark", 4, 2)})
        s.night()
        self.assertIn("its first line names task 4, the engine's routine mark is task 3", s.todo()["body"])
        # 3. Right at last, in another letter case — and installed, but recorded with a wrong sha:
        # C2 holds that record PR red at the gate, so it never lands.
        s.merge({"user/routines/mark.md": b"Mark (Task 3, Version 2).\nDo it.\n"})
        wrong = record(s.files).replace(record(s.files).split(b'[routines.mark]\nsha256 = "')[1][:64], b"0" * 64)
        with s.dated():
            code, out = s.w.gate({"user/fields/installed.toml": wrong}, record=True)
        self.assertNotEqual(code, 0, out)
        s.night()
        self.assertEqual(s.todo()["body"].count("routine mark"), 1)
        self.assertIn("routine mark: the installed text is not its master", s.todo()["body"])
        with s.dated():
            code, out = s.w.gate({"user/fields/installed.toml": record(s.files)}, record=True)
        self.assertEqual(code, 0, out)
        s.files["user/fields/installed.toml"] = record(s.files)
        red, out = s.night()
        self.assertEqual((red, s.w.todos("open"), s.w.alarms("open")), (False, [], []), out)

    def test_people_and_races_around_the_issue_never_take_the_list_away(self):
        s = self.story(template((3, "mark", 1)), {"mark": master("mark", 3, 1)})
        s.merge({"engine/rules/routines.md": template((3, "mark", 2))})
        s.night()
        first = s.todo()["number"]
        api = s.w.api
        # Somebody else opens an Issue under the title: not the list.
        api.lists[ISSUES].append({"number": 900, "title": behind.TITLE, "body": "All done!", "state": "open",
                                  "user": {"login": "person-bot"}, "closed_by": None})
        api.lists[f"{ISSUES}/900/comments"] = []
        # The bot closes the list by hand: the next night opens a new one while work is left.
        api.close(ISSUES, first, "person-bot")
        s.night()
        mine = [i for i in s.w.todos("open") if i["user"]["login"] == BOT]
        self.assertEqual(len(mine), 1)
        second = mine[0]["number"]
        self.assertNotEqual(second, first)
        self.assertIn("routine mark: written from task 3, version 1", mine[0]["body"])
        # Somebody empties its body: written again.
        listed = mine[0]["body"]
        mine[0]["body"] = "Nothing to do.\n"
        s.night()
        self.assertEqual(mine[0]["body"], listed)
        # Two runs at once opened a second one: the older stays.
        api.post(ISSUES, {"title": behind.TITLE, "body": "a second run's"})
        s.night()
        mine = sorted((i for i in s.w.todos("open") if i["user"]["login"] == BOT), key=lambda i: i["number"])
        self.assertEqual([i["number"] for i in mine], [second])
        # Done: the machine closes its own; the stranger's stays as it was.
        s.merge({"user/routines/mark.md": master("mark", 3, 2)})
        s.record()
        red, out = s.night()
        self.assertFalse(red, out)
        self.assertEqual([(i["number"], i["body"]) for i in s.w.todos("open")], [(900, "All done!")])

    def test_github_refusing_a_write_and_a_broken_engine_file_never_lose_the_list(self):
        s = self.story(template((3, "mark", 1)), {"mark": master("mark", 3, 1)})
        s.merge({"engine/rules/routines.md": template((3, "mark", 2))})
        s.night()
        number = s.todo()["number"]
        before = s.todo()["body"]
        # Release B, and GitHub refuses the write that day: the night stays green, the list waits.
        s.merge({"engine/rules/routines.md": template((3, "mark", 3))})
        patch = s.w.api.patch

        def refuse(path, body):
            if path == f"{ISSUES}/{number}":
                raise ApiError(f"PATCH {path}: HTTP 502")
            return patch(path, body)

        s.w.api.patch = refuse
        red, out = s.night()
        self.assertFalse(red, out)
        self.assertIn("TODO ERROR the to-do could not be kept: ApiError", out)
        self.assertEqual(s.todo()["body"], before)
        s.w.api.patch = patch
        s.night()
        self.assertIn("the engine's task 3 is version 3", s.todo()["body"])
        # A release whose routines.md names two routines in one task: an error, loud — the list stays.
        listed = s.todo()["body"]
        s.merge({"engine/rules/routines.md": template((3, "mark", 3)).replace(
            b"Do it.", b"Routine `other`, task version 1.", 1)})
        red, out = s.night()
        self.assertTrue(red, out)
        self.assertIn("N7 could not run: ValueError", out)
        self.assertEqual(s.todo()["body"], listed)
        s.merge({"engine/rules/routines.md": template((3, "mark", 3))})
        s.night()
        self.assertEqual((s.todo()["number"], s.todo()["body"]), (number, listed))

    def test_her_own_changes_her_no_and_her_change_of_mind_in_the_middle_of_an_update(self):
        s = self.story(template((1, "morning", 1), (3, "mark", 1)),
                       {"morning": master("morning", 1, 1), "mark": master("mark", 3, 1)})
        s.merge({"engine/rules/routines.md": template((1, "morning", 1), (3, "mark", 2))})
        s.night()
        # She moves her morning routine (a small change): merged, installed and recorded within
        # hours — a night in between does not put it on the list.
        s.merge({"user/routines/morning.md": master("morning", 1, 1, " At 08:00.")})
        s.night(6)
        self.assertNotIn("routine morning", s.todo()["body"])
        s.record()
        s.night()
        self.assertNotIn("routine morning", s.todo()["body"])
        # A change of her part of the bot's field: listed at once, gone once recorded.
        s.merge({"user/fields/bot.md": b"Her bot part, v2.\n"})
        s.night(1)
        self.assertIn("field bot: the installed text is not its master", s.todo()["body"])
        s.record()
        s.night(1)
        self.assertNotIn("field bot", s.todo()["body"])
        # She says no to the new mark: recorded, the list closes, quiet for a week.
        s.merge({"user/config.toml": VALID_CONFIG + b'declined = ["mark@2"]\n'})
        for _ in range(7):
            red, out = s.night()
            self.assertEqual((red, s.w.todos("open")), (False, []), out)
        # She changes her mind: the no is taken out, the line is back.
        s.merge({"user/config.toml": VALID_CONFIG})
        s.night()
        self.assertIn("routine mark: written from task 3, version 1", s.todo()["body"])

    def test_a_record_that_leaves_a_merged_routine_out_is_listed_at_once_not_twelve_hours_later(self):
        s = self.story(template((1, "morning", 1), (3, "mark", 1)),
                       {"morning": master("morning", 1, 1), "mark": master("mark", 3, 1)})
        s.merge({"engine/rules/routines.md": template((1, "morning", 1), (3, "mark", 2))})
        s.night(1)
        number = s.todo()["number"]
        # The bot writes mark anew and changes morning for her too; both merge within the hour,
        # and its one record (ROUT-10) leaves morning out.
        s.merge({"user/routines/mark.md": master("mark", 3, 2), "user/routines/morning.md": master("morning", 1, 1, " Twice.")})
        s.w.advance(1)
        whole = record(s.files)
        partial = re.sub(rb'\[routines\.morning\]\nsha256 = "[0-9a-f]{64}"\n\n', b"", whole)
        self.assertNotEqual(partial, whole)
        s.merge({"user/fields/installed.toml": partial})
        red, out = s.night(1)
        self.assertFalse(red, out)
        self.assertEqual(s.todo()["number"], number)                         # not closed, so not opened again
        self.assertIn("routine morning: the installed text is not its master", s.todo()["body"])
        self.assertNotIn("routine mark", s.todo()["body"])
        s.record()
        red, out = s.night(1)
        self.assertEqual((red, s.w.todos("open")), (False, []), out)

    def test_an_unreadable_record_never_closes_a_list_that_is_not_done(self):
        """A record that does not parse made the list judge nothing
        against it and come back empty — and the machine closed a list that was not done."""
        s = self.story(template((3, "mark", 1)), {"mark": master("mark", 3, 1)})
        s.merge({"engine/rules/fields/bot.md": b"Bot safety rules, v2.\n"})        # a release changes the bot's field
        s.night(1)
        todo = s.todo()
        listed = todo["body"]
        self.assertIn("field bot: the installed text is not its master", listed)
        # The record breaks: N5 tells people; the list keeps its body and stays open, night after night.
        s.merge({"user/fields/installed.toml": b"not [toml\n"})
        for _ in range(3):
            red, out = s.night()
            self.assertTrue(red, out)
            self.assertIn("installed.toml does not parse", out)
            self.assertEqual((s.todo()["number"], s.todo()["body"], s.todo()["state"]), (todo["number"], listed, "open"))
        # A readable record that still misses the field: the list holds the field, not closed.
        s.files["engine/rules/fields/bot.md"] = BASE_FILES["engine/rules/fields/bot.md"]   # recorded the old text
        s.merge({"user/fields/installed.toml": record(s.files)})
        s.files["engine/rules/fields/bot.md"] = b"Bot safety rules, v2.\n"
        s.night()
        self.assertEqual((s.todo()["number"], s.todo()["body"]), (todo["number"], listed))
        # The bot installs its field and records it: only now the machine closes the list.
        s.record()
        s.night()
        self.assertEqual(s.w.todos("open"), [])
        self.assertEqual(s.w.todos("closed")[0]["number"], todo["number"])

    def test_a_long_list_and_hostile_names_keep_one_whole_body(self):
        tasks = [(n, f"r{n:02d}", 1) for n in range(1, 41)]
        s = self.story(template((3, "mark", 1)), {"mark": master("mark", 3, 1)})
        s.merge({"engine/rules/routines.md": template(*tasks)})
        # A master pushed past the gate under a hostile name (N1 raises that alarm).
        with s.dated():
            s.w.push({"user/routines/x```<!--y.md": b"Hostile.\n"}, "person-bot")
        s.night(13)
        body = s.todo()["body"]
        self.assertEqual(body.count("```"), 2, body)                         # the code block is whole
        self.assertNotIn("<!--", body)
        self.assertEqual(sum(line.startswith("routine r") for line in body.splitlines()), 40)
        names = [line.split(":")[0] for line in body.splitlines() if line.startswith("routine ")]
        self.assertEqual(names, [f"routine r{n:02d}" for n in range(1, 41)] + ["routine 'x'''<!- -y'"])   # by file name
        number = s.todo()["number"]
        s.night()
        self.assertEqual(s.writes_to(number), [])


if __name__ == "__main__":
    unittest.main()

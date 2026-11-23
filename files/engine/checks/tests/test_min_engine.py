"""N7 without a bot — the engine against the rules on `stable` (engine.checks.min_engine), on fixtures (F1).

The repository of releases is a bare git repository of the test's own: a branch `stable` with the
index the publisher's code writes, and tags whose manifests say when they were published. The
user's repository is a fixture with the one text for everybody. Told here: an engine not older
than the rules is []; older within its grace is not judged; older past it is one line; a missing
`stable`, an unreadable repository, an index without the line — not judged, said; an installation
with a bot is not this check's. Then the page: opened once, kept, changed with a comment, closed
when the engine caught up, and a page the user closed stays closed for the same two releases."""
from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine.checks import alarm, limits_check, min_engine, watch
from engine.checks.tests.fakeapi import BOT, REPO, FakeApi
from engine.checks.tests.helpers import BASE_FILES, VALID_CONFIG, Repo, _git
from engine.setup import stable, topics

ROOT = Path(__file__).resolve().parents[3]
TOPICS = (ROOT / "engine" / "rules" / "topics.toml").read_bytes()
RELEASES = "grafyla/releases"
NO_BOT = VALID_CONFIG.replace(b"read_budget_bytes = 16384\n",
                              b'read_budget_bytes = 16384\nwriter = "chat"\nbot = false\n').replace(
    b'name = "grok"', b'name = "chatgpt"')
WITH_BOT = VALID_CONFIG.replace(b"read_budget_bytes = 16384\n", b'read_budget_bytes = 16384\nwriter = "chat"\n')
PUBLISHED = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
ISSUES = f"repos/{REPO}/issues"


def topics_for(least: str) -> bytes:
    """The template's own topics, with the minimal engine set."""
    out, n = re.subn(rb'(?m)^min_engine = "v[0-9.]+"', f'min_engine = "{least}"'.encode(), TOPICS)
    assert n == 1
    return out


class Releases:
    """A bare repository of releases: tags with manifests, a branch `stable` with an index."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name) / "work"
        self.bare = Path(self._tmp.name) / "releases.git"
        self.work.mkdir()
        _git(self.work, "init", "-q", "-b", "main")
        _git(Path(self._tmp.name), "init", "-q", "--bare", str(self.bare))
        _git(self.work, "commit", "-q", "--allow-empty", "-m", "start")
        _git(self.work, "push", "-q", str(self.bare), "main")

    def publish(self, tag: str, least: str, published: datetime = PUBLISHED, index: str | None = None) -> None:
        manifest = {"release": tag, "published": published.strftime("%Y-%m-%dT%H:%M:%SZ"), "files": {}}
        (self.work / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        text = index if index is not None else stable.index_text(topics_for(least), RELEASES, tag)
        (self.work / "rules-index.md").write_text(text, encoding="utf-8")
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-q", "-m", tag)
        _git(self.work, "tag", tag)
        _git(self.work, "push", "-q", str(self.bare), "main", tag)

    def move_stable(self, tag: str) -> None:
        _git(self.work, "push", "-q", "-f", str(self.bare), f"{tag}^{{commit}}:refs/heads/stable")

    def close(self) -> None:
        self._tmp.cleanup()


class Check(unittest.TestCase):
    def setUp(self):
        self.releases = Releases()
        self.addCleanup(self.releases.close)
        self.repo = Repo({**BASE_FILES, "user/config.toml": NO_BOT, "user/engine.lock": b'release = "v0.1.12"\n',
                          "engine/rules/fields/fixed.md": b"The one text.\n"})
        self.addCleanup(self.repo.close)

    def judge(self, now: datetime, config: bytes = NO_BOT, url: str | None = None):
        main = self.repo.base if config == NO_BOT else self.repo.commit({"user/config.toml": config})
        cfg = limits_check.config_at(self.repo.path, main)
        return min_engine.check(self.repo.path, main, cfg, now, url=url or str(self.releases.bare))

    def test_the_line_is_read_in_every_text_an_index_has_had(self):
        for tag, least in (("v0.1.13", "v0.1.12"), ("v2.10.0", "v1.9.30")):
            texts = stable.index_texts(topics_for(least), RELEASES, tag)
            self.assertEqual(len(texts), len(stable.INDEX_TEXTS))
            for text in texts:
                self.assertEqual(min_engine.least(text), least)
                self.assertEqual(min_engine.index_release(text), tag)

    def test_its_names_are_the_publisher_s(self):
        self.assertEqual(min_engine.STABLE, stable.BRANCH)
        self.assertEqual(min_engine.INDEX, topics.INDEX_FILE)

    def test_an_index_with_the_line_twice_or_not_at_all_names_nothing(self):
        text = stable.index_text(topics_for("v0.1.12"), RELEASES, "v0.1.13")
        line = next(x for x in text.splitlines() if x.startswith("These rules are written"))
        self.assertIsNone(min_engine.least(text.replace(line, "")))
        self.assertIsNone(min_engine.least(text + "\n" + line + "\n"))

    def test_no_branch_stable_yet_is_not_judged_and_said(self):
        self.releases.publish("v0.1.13", "v0.1.13")
        lines, notes = self.judge(PUBLISHED + timedelta(days=60))
        self.assertIsNone(lines)
        self.assertEqual(notes, [f"N7 NOTE {RELEASES} has no branch stable yet: no rules to compare the engine with"])

    def test_a_repository_that_cannot_be_read_is_not_judged_and_said(self):
        lines, notes = self.judge(PUBLISHED, url=str(self.releases.bare) + "-gone")
        self.assertIsNone(lines)
        self.assertTrue(notes[0].startswith("N7 NOTE the rules on stable could not be read, the engine is not compared"),
                        notes)
        self.assertNotIn("\n", notes[0], "a note is one line of the run's log")

    def test_an_engine_not_older_than_the_rules_is_clean(self):
        for least, tag in (("v0.1.12", "v0.1.13"), ("v0.1.11", "v0.1.14")):
            with self.subTest(least=least):
                self.releases.publish(tag, least)
                self.releases.move_stable(tag)
                state, notes = self.judge(PUBLISHED + timedelta(days=60))
                self.assertEqual(state, {"engine": "v0.1.12", "needs": least, "of": tag, "late": False})
                self.assertIn("is not older than the rules on stable need", notes[0])

    def test_an_older_engine_is_not_judged_within_its_grace_and_is_one_line_after_it(self):
        self.releases.publish("v0.1.13", "v0.1.13")
        self.releases.publish("v0.1.14", "v0.1.13", published=PUBLISHED + timedelta(days=7))
        self.releases.move_stable("v0.1.14")
        due = PUBLISHED + timedelta(days=7) + min_engine.GRACE          # the stable channel: seven days
        state, notes = self.judge(due - timedelta(minutes=1))
        self.assertEqual(state, {"engine": "v0.1.12", "needs": "v0.1.13", "of": "v0.1.14", "late": False})
        self.assertIn("its update has until", notes[0])
        state, notes = self.judge(due + timedelta(minutes=1))
        self.assertEqual(state, {"engine": "v0.1.12", "needs": "v0.1.13", "of": "v0.1.14", "late": True})
        self.assertEqual(min_engine.line(state), "the engine v0.1.12 is older than v0.1.13, the oldest the rules on "
                                                 "stable (release v0.1.14) are written for")
        self.assertEqual(notes, [])

    def test_the_grace_counts_from_the_minimum_s_own_release_and_the_user_s_channel(self):
        self.releases.publish("v0.1.13", "v0.1.13")
        self.releases.move_stable("v0.1.13")
        canary = NO_BOT.replace(b'channel = "stable"', b'channel = "canary"')
        self.assertTrue(self.judge(PUBLISHED + min_engine.GRACE + timedelta(minutes=1), config=canary)[0]["late"])
        self.assertFalse(self.judge(PUBLISHED + min_engine.GRACE + timedelta(minutes=1))[0]["late"])

    def test_an_index_without_the_line_is_not_judged(self):
        self.releases.publish("v0.1.13", "v0.1.13", index="# Topic rules — release v0.1.13\n\nNothing.\n")
        self.releases.move_stable("v0.1.13")
        lines, notes = self.judge(PUBLISHED + timedelta(days=60))
        self.assertIsNone(lines)
        self.assertIn("names no minimal engine this code reads", notes[0])

    def test_a_lock_this_code_cannot_compare_is_not_judged(self):
        self.releases.publish("v0.1.13", "v0.1.13")
        self.releases.move_stable("v0.1.13")
        for lock in (b'release = "v0.1.\xd9\xa1"\n', b"release = [\n", b"release = 12\n", b"other = 1\n"):
            with self.subTest(lock=lock):
                main = self.repo.commit({"user/engine.lock": lock})
                cfg = limits_check.config_at(self.repo.path, main)
                state, notes = min_engine.check(self.repo.path, main, cfg, PUBLISHED + timedelta(days=60),
                                                url=str(self.releases.bare))
                self.assertIsNone(state)
                self.assertIn("user/engine.lock names no release", notes[0])

    def test_a_stalled_repository_is_unread_not_a_hung_watch(self):
        import subprocess
        from unittest import mock

        real = subprocess.run

        def stalled(*args, **kwargs):
            if not {"ls-remote", "fetch"} & set(args[0]):
                return real(*args, **kwargs)        # the local git of the fixture
            self.assertEqual(kwargs.get("timeout"), min_engine.TIMEOUT)
            self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        with mock.patch.object(min_engine.subprocess, "run", stalled):
            state, notes = self.judge(PUBLISHED)
        self.assertIsNone(state)
        self.assertIn(f"took more than {min_engine.TIMEOUT} s", notes[0])

    def test_the_grace_is_eight_days_and_its_last_minute_is_late(self):
        self.assertEqual(min_engine.GRACE, timedelta(days=8))
        self.releases.publish("v0.1.13", "v0.1.13")
        self.releases.move_stable("v0.1.13")
        due = PUBLISHED + timedelta(days=7) + timedelta(days=8)
        self.assertTrue(self.judge(due)[0]["late"])
        self.assertFalse(self.judge(due - timedelta(seconds=1))[0]["late"])

    def test_defaults_that_name_no_repository_or_a_bad_one_are_not_judged(self):
        self.releases.publish("v0.1.13", "v0.1.13")
        self.releases.move_stable("v0.1.13")
        for defaults in (b"[release]\n", b'[release]\nmanifests = "--upload-pack=x/y"\n',
                         b'[release]\nmanifests = "a b/c"\n', b"[release\n"):
            with self.subTest(defaults=defaults):
                main = self.repo.commit({"engine/defaults.toml": defaults})
                cfg = limits_check.config_at(self.repo.path, main)
                state, notes = min_engine.check(self.repo.path, main, cfg, PUBLISHED + timedelta(days=60),
                                                url=str(self.releases.bare))
                self.assertIsNone(state)
                self.assertIn("names no repository of releases", notes[0])

    def test_a_manifest_at_the_tag_that_names_another_release_is_unread(self):
        self.releases.publish("v0.1.13", "v0.1.13")
        self.releases.move_stable("v0.1.13")
        (self.releases.work / "release-manifest.json").write_text(json.dumps(
            {"release": "v0.1.9", "published": "2026-11-01T09:00:00Z", "files": {}}), encoding="utf-8")
        _git(self.releases.work, "commit", "-q", "-am", "forged")
        _git(self.releases.work, "tag", "-f", "v0.1.13")
        _git(self.releases.work, "push", "-q", "-f", str(self.releases.bare), "refs/tags/v0.1.13")
        state, notes = self.judge(PUBLISHED + timedelta(days=60))
        self.assertIsNone(state)
        self.assertIn("the manifest at v0.1.13 has no time it was published", notes[0])

    def test_a_stable_without_an_index_is_unread(self):
        self.releases.publish("v0.1.13", "v0.1.13")
        _git(self.releases.work, "rm", "-q", "rules-index.md")
        _git(self.releases.work, "commit", "-q", "-m", "no index")
        _git(self.releases.work, "push", "-q", "-f", str(self.releases.bare), "HEAD:refs/heads/stable")
        state, notes = self.judge(PUBLISHED + timedelta(days=60))
        self.assertIsNone(state)
        self.assertIn("stable has no rules-index.md", notes[0])

    def test_an_installation_with_a_bot_is_not_this_check_s(self):
        self.releases.publish("v0.1.13", "v0.1.13")
        self.releases.move_stable("v0.1.13")
        self.assertEqual(self.judge(PUBLISHED + timedelta(days=60), config=WITH_BOT), (None, []))

    def test_the_watch_reports_it_and_says_what_n5_is_not(self):
        self.releases.publish("v0.1.13", "v0.1.13")
        self.releases.move_stable("v0.1.13")
        api = FakeApi()
        api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": self.repo.base}}
        api.lists[ISSUES] = []
        report = self.repo.path.parent / f"{self.repo.path.name}-report.json"
        self.addCleanup(report.unlink, missing_ok=True)
        old = min_engine.ORIGIN
        min_engine.ORIGIN = lambda repository: str(self.releases.bare)
        self.addCleanup(setattr, min_engine, "ORIGIN", old)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            watch.main(["--repo", str(self.repo.path), "--main", self.repo.base, "--manifest",
                        str(self.repo.manifest_file(self.repo.base)), "--report", str(report),
                        "--branches", "refs/heads/"], api=api, now=PUBLISHED + timedelta(days=60))
        written = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(written["rules"], {"engine": "v0.1.12", "needs": "v0.1.13", "of": "v0.1.13", "late": True})
        self.assertIn("RULES the engine v0.1.12 is older than v0.1.13", out.getvalue())
        self.assertIn("N4 NOTE N4 and N5 do not run: this installation has no bot (platform.bot = false); its AI "
                      "holds the one text for everybody", out.getvalue())
        self.assertNotIn("N5 and N7 do not run", out.getvalue())

    def watch_without(self, check):
        """The watch with min_engine.check replaced: its report and its output."""
        api = FakeApi()
        api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": self.repo.base}}
        api.lists[ISSUES] = []
        report = self.repo.path.parent / f"{self.repo.path.name}-report.json"
        self.addCleanup(report.unlink, missing_ok=True)
        old = min_engine.check
        min_engine.check = check
        self.addCleanup(setattr, min_engine, "check", old)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = watch.main(["--repo", str(self.repo.path), "--main", self.repo.base, "--manifest",
                               str(self.repo.manifest_file(self.repo.base)), "--report", str(report),
                               "--branches", "refs/heads/"], api=api, now=PUBLISHED + timedelta(days=60))
        return code, json.loads(report.read_text(encoding="utf-8")), out.getvalue()

    def test_a_watch_that_did_not_judge_it_leaves_it_out_of_the_report(self):
        code, report, out = self.watch_without(lambda *a, **k: (None, ["N7 NOTE nothing read"]))
        self.assertNotIn("rules", report, "the alarms must not read «not judged» as «caught up»")
        self.assertIn("N7 NOTE nothing read", out)

    def test_a_check_that_crashes_is_an_error_line_of_the_run(self):
        def crash(*a, **k):
            raise RuntimeError("boom")
        code, report, out = self.watch_without(crash)
        self.assertIn("N7 could not run: RuntimeError: boom", report["error"])
        self.assertEqual(code, 2)


def state(engine="v0.1.12", needs="v0.1.13", of="v0.1.14", late=True) -> dict:
    return {"engine": engine, "needs": needs, "of": of, "late": late}


class Page(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.api.lists[ISSUES] = []

    def sync(self, st):
        return min_engine.sync(self.api, st, "@owner", "https://run/1")

    def mine(self):
        return [i for i in self.api.lists[ISSUES] if i["title"] == min_engine.TITLE]

    def test_it_opens_once_and_names_the_user_in_plain_words(self):
        self.assertEqual(self.sync(state()), ["opened #101 rules"])
        self.assertEqual(self.sync(state()), [])
        page = self.mine()[0]
        self.assertTrue(page["body"].startswith("@owner — Your AI reads its rules"))
        self.assertIn("yours is v0.1.12, they need v0.1.13 or newer", page["body"])
        self.assertIn("You do not need to paste or send anything to your AI.", page["body"])
        self.assertNotIn("trusted contact", page["body"])

    def test_within_its_grace_nothing_opens(self):
        self.assertEqual(self.sync(state(late=False)), [])
        self.assertEqual(self.mine(), [])

    def test_it_closes_when_the_engine_caught_up_and_opens_again_if_it_falls_behind(self):
        self.sync(state())
        self.assertEqual(self.sync(state(engine="v0.1.13", late=False)), ["closed #101 rules, caught up"])
        self.assertEqual(self.mine()[0]["state"], "closed")
        self.assertIn(min_engine.CAUGHT_UP, self.mine()[0]["body"])
        self.assertEqual(self.sync(state(engine="v0.1.13", late=False)), [])
        self.assertEqual(self.sync(state()), ["opened #102 rules"])

    def test_a_page_the_engine_outgrew_closes_while_a_newer_minimum_is_in_its_grace(self):
        # Found by the hostile pre-check: caught up with v0.1.13 while `stable` moved on to rules for
        # v0.1.14 — the open page would say «they need v0.1.13» for the week of the new grace.
        self.sync(state())
        done = self.sync(state(engine="v0.1.13", needs="v0.1.14", of="v0.1.15", late=False))
        self.assertEqual(done, ["closed #101 rules, caught up with what it said"])
        self.assertEqual(self.mine()[0]["state"], "closed")

    def test_a_page_still_true_stays_while_a_newer_minimum_is_in_its_grace(self):
        self.sync(state())
        self.assertEqual(self.sync(state(needs="v0.1.14", of="v0.1.15", late=False)), [])
        self.assertEqual(self.mine()[0]["state"], "open")

    def test_a_page_the_user_closed_stays_closed_for_the_same_releases_only(self):
        self.sync(state())
        self.mine()[0]["state"] = "closed"
        self.assertEqual(len(self.sync(state())), 1)
        self.assertEqual(len(self.mine()), 1)
        self.assertEqual(self.sync(state(needs="v0.1.14")), ["opened #102 rules"])

    def test_catching_up_ends_what_the_user_s_close_held_back(self):
        self.sync(state())
        self.mine()[0]["state"] = "closed"
        self.assertEqual(self.sync(state(engine="v0.1.13", late=False)), [])
        self.assertIn(min_engine.CAUGHT_UP, self.mine()[0]["body"])
        self.assertEqual(self.sync(state()), ["opened #102 rules"], "the lock went back: a new page")

    def test_a_second_page_the_workflow_closed_holds_nothing_back(self):
        # Found by the hostile pre-check: a duplicate closed by the machine read as the user's close.
        self.sync(state())
        self.api.post(ISSUES, {"title": min_engine.TITLE, "body": "<!-- rules-behind: v0.1.12 v0.1.13 -->"})
        self.assertEqual(self.sync(state()), ["closed #102 rules, a second one"])
        self.sync(state(engine="v0.1.13", late=False))
        self.assertEqual(self.sync(state()), ["opened #103 rules"])

    def test_new_releases_on_an_open_page_rewrite_it_and_say_so_in_a_comment(self):
        self.sync(state())
        self.assertEqual(self.sync(state(needs="v0.1.14")), ["updated #101 rules"])
        self.assertIn("they need v0.1.14 or newer", self.mine()[0]["body"])
        comments = [w for w in self.api.writes if w[0] == "POST" and w[1].endswith("/101/comments")]
        self.assertIn("the rules need v0.1.14 or newer", comments[-1][2]["body"])

    def test_a_page_somebody_else_opened_under_the_title_counts_for_nothing(self):
        self.api.lists[ISSUES].append({"number": 7, "title": min_engine.TITLE, "state": "open",
                                       "user": {"login": "someone"}, "body": "<!-- rules-behind: v0.1.12 v0.1.13 -->"})
        self.assertEqual(self.sync(state()), ["opened #101 rules"])
        self.assertEqual(self.sync(state(engine="v0.1.13", late=False)), ["closed #101 rules, caught up"])
        self.assertEqual(self.api.lists[ISSUES][0]["state"], "open")

    def test_a_pull_request_under_the_title_counts_for_nothing(self):
        self.api.lists[ISSUES].append({"number": 9, "title": min_engine.TITLE, "state": "open", "pull_request": {},
                                       "user": {"login": BOT}, "body": "<!-- rules-behind: v0.1.12 v0.1.13 -->"})
        self.assertEqual(self.sync(state()), ["opened #101 rules"])

    def test_a_page_closed_early_does_not_say_the_engine_caught_up_with_the_rules(self):
        # Found by the second pass: the engine reached the page's minimum, the rules need a newer one still.
        self.sync(state())
        self.sync(state(engine="v0.1.13", needs="v0.1.14", of="v0.1.15", late=False))
        comment = [w for w in self.api.writes if w[0] == "POST" and w[1].endswith("/101/comments")][-1][2]["body"]
        self.assertIn("has reached what this page asked for", comment)
        self.assertNotIn("caught up", comment)

    def test_releases_the_user_closed_a_page_for_do_not_come_back_on_another_page(self):
        # Found by the second pass: an open page for other releases was rewritten to the closed pair.
        self.sync(state())
        self.mine()[0]["state"] = "closed"
        self.sync(state(needs="v0.1.14"))
        self.assertEqual(self.sync(state()), ["closed #102 rules, its releases are a page the user closed"])
        self.assertEqual([p["state"] for p in self.mine()], ["closed", "closed"])
        self.assertNotIn("they need v0.1.13", self.mine()[1]["body"])

    def test_a_page_the_user_reopened_closes_while_the_engine_is_caught_up(self):
        # Found by the second pass: the caught-up branch skipped every marked page, open ones too.
        self.sync(state())
        self.sync(state(engine="v0.1.13", late=False))
        self.mine()[0]["state"] = "open"
        self.assertEqual(self.sync(state(engine="v0.1.13", late=False)), ["closed #101 rules, caught up"])
        self.assertEqual(self.mine()[0]["body"].count(min_engine.CAUGHT_UP), 1)

    def test_a_mark_somebody_broke_is_no_mark(self):
        # Found by the second pass: «v0.1 v0.1» read as a mark and broke every run in the grace.
        self.sync(state())
        self.mine()[0]["body"] = "<!-- rules-behind: v0.1 v0.1 -->"
        self.assertEqual(self.sync(state(needs="v0.1.14", late=False)), ["closed #101 rules, its mark does not read"])
        # Tolik's reading of #64: such a close must not say the engine reached what the page asked for.
        comment = [w for w in self.api.writes if w[0] == "POST" and w[1].endswith("/101/comments")][-1][2]["body"]
        self.assertIn("no longer reads", comment)
        self.assertNotIn("has reached", comment)
        self.assertEqual(self.sync(state(needs="v0.1.14")), ["opened #102 rules"], "after the week, a new page")

    def test_the_user_s_reason_for_closing_stays(self):
        self.sync(state())
        self.mine()[0]["state"] = "closed"
        self.sync(state(engine="v0.1.13", late=False))
        last = [w for w in self.api.writes if w[0] == "PATCH" and w[1].endswith("/101")][-1][2]
        self.assertEqual(set(last), {"body"})

    def test_a_report_s_state_is_held_to_its_shape(self):
        self.assertEqual(min_engine.from_report(state()), state())
        for bad in (None, [], {**state(), "x": 1}, {**state(), "late": "yes"}, {**state(), "engine": "v0.1.\u0661"},
                    {**state(), "needs": "v1"}, {**state(), "of": "`@x`"}):
            with self.subTest(bad=bad):
                self.assertIsNone(min_engine.from_report(bad))


class Alarms(unittest.TestCase):
    """The alarms step keeps the page only for an installation without a bot, by main's own config."""

    def run_alarms(self, config: bytes, rules, moved: bool = False, error=(), pages=(), api=None):
        repo = Repo({**BASE_FILES, "user/config.toml": config})
        self.addCleanup(repo.close)
        api = api or FakeApi()
        api.lists[ISSUES] = list(pages)
        api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": "f" * 40 if moved else repo.base}}
        report = repo.path.parent / f"{repo.path.name}-report.json"
        self.addCleanup(report.unlink, missing_ok=True)
        written = {"N1": [], "N5": [], "error": list(error), "skipped": ["N4", "N5"]}
        if rules is not None:
            written["rules"] = rules
        report.write_text(json.dumps(written), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = alarm.main(["--repo", str(repo.path), "--main", repo.base, "--report", str(report),
                               "--run-url", "https://run/1"], api=api)
        return code, out.getvalue(), [i for i in api.lists[ISSUES] if i["title"] == min_engine.TITLE]

    def test_it_opens_the_page_and_the_run_stays_green(self):
        code, out, pages = self.run_alarms(NO_BOT, state())
        self.assertEqual(code, 0, out)
        self.assertIn("opened #101 rules", out)
        self.assertEqual(len(pages), 1)

    def test_an_installation_with_a_bot_gets_no_page_whatever_the_report_says(self):
        code, out, pages = self.run_alarms(WITH_BOT, state())
        self.assertEqual(pages, [])

    def test_a_main_that_moved_since_the_watch_gets_no_page_from_its_old_lock(self):
        # Found by the hostile pre-check: an update merged between the watch and the alarms.
        code, out, pages = self.run_alarms(NO_BOT, state(), moved=True)
        self.assertEqual(pages, [])
        self.assertIn("RULES left alone: main is at ffffffffffff now", out)

    def test_an_error_elsewhere_in_the_run_does_not_hold_the_page_back(self):
        code, out, pages = self.run_alarms(NO_BOT, state(), error=["N2 could not run: x"])
        self.assertEqual(len(pages), 1, out)

    def test_a_run_that_did_not_judge_it_leaves_an_open_page_open(self):
        page = {"number": 5, "title": min_engine.TITLE, "state": "open", "user": {"login": BOT},
                "body": "<!-- rules-behind: v0.1.12 v0.1.13 -->"}
        code, out, pages = self.run_alarms(NO_BOT, None, pages=[page])
        self.assertEqual(pages[0]["state"], "open")

    def test_the_page_names_the_trusted_contact_too(self):
        config = NO_BOT + b'\n[alarm]\ntrusted_contact = "helper-1"\n'
        code, out, pages = self.run_alarms(config, state())
        self.assertRegex(pages[0]["body"].split(" — ")[0], r"^(@\S+ )?@helper-1$|^@helper-1 @\S+$", out)

    def test_github_refusing_the_page_leaves_the_run_green(self):
        class Refusing(FakeApi):
            def post(self, path, body):
                raise RuntimeError("refused")
        code, out, pages = self.run_alarms(NO_BOT, state(), api=Refusing())
        self.assertEqual(code, 0)
        self.assertIn("RULES ERROR the page of the rules could not be kept: RuntimeError: refused", out)

    def test_a_report_whose_state_is_not_of_its_shape_opens_nothing(self):
        code, out, pages = self.run_alarms(NO_BOT, ["the engine v0.1.12 is older than v0.1.13, x"])
        self.assertEqual((code, pages), (0, []))


if __name__ == "__main__":
    unittest.main()


class Story(unittest.TestCase):
    """Night after night through the real entry points — `watch.main`, then `alarm.main` on its report — with a
    repository of releases of the test's own: before `stable`, a release in its grace, an update that stopped,
    the user closing the page, a newer minimum, an update merged between the watch and the alarms, and the
    engine catching up. What the fixtures cannot see: GitHub's own git host and its speed, the schedule."""

    def setUp(self):
        self.releases = Releases()
        self.addCleanup(self.releases.close)
        self.repo = Repo({**BASE_FILES, "user/config.toml": NO_BOT, "user/engine.lock": b'release = "v0.1.12"\n',
                          "engine/rules/fields/fixed.md": b"The one text.\n"})
        self.addCleanup(self.repo.close)
        self.api = FakeApi()
        self.api.lists[ISSUES] = []
        self.main = self.repo.base
        old = min_engine.ORIGIN
        min_engine.ORIGIN = lambda repository: str(self.releases.bare)
        self.addCleanup(setattr, min_engine, "ORIGIN", old)
        self.report = self.repo.path.parent / f"{self.repo.path.name}-report.json"
        self.addCleanup(self.report.unlink, missing_ok=True)

    def night(self, day: int, moved_to: str | None = None) -> str:
        """The watch on self.main, then the alarms; `moved_to` — main moved between the two steps."""
        now = PUBLISHED + timedelta(days=day)
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": self.main}}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            watch.main(["--repo", str(self.repo.path), "--main", self.main, "--manifest",
                        str(self.repo.manifest_file(self.main)), "--report", str(self.report),
                        "--branches", "refs/heads/"], api=self.api, now=now)
            if moved_to:
                self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": moved_to}}
            code = alarm.main(["--repo", str(self.repo.path), "--main", self.main, "--report", str(self.report),
                               "--run-url", f"https://run/{day}"], api=self.api)
        self.assertIn(code, (0, 1, 2), out.getvalue())
        return out.getvalue()

    def pages(self):
        """The states of the rules pages, oldest first (alarm Issues share the numbers)."""
        return [i["state"] for i in self.api.lists[ISSUES] if i["title"] == min_engine.TITLE]

    def page(self, n: int) -> dict:
        return [i for i in self.api.lists[ISSUES] if i["title"] == min_engine.TITLE][n]

    def test_a_season_of_nights(self):
        self.releases.publish("v0.1.13", "v0.1.13")
        out = self.night(1)
        self.assertIn("has no branch stable yet", out)
        self.assertEqual(self.pages(), [])

        self.releases.move_stable("v0.1.13")
        self.night(10)                                  # due on day 7 + 8 = 15: the update may still come
        self.assertEqual(self.pages(), [])
        self.night(16)
        self.assertEqual(self.pages(), ["open"])
        self.night(17)
        self.night(17)                                  # a re-run of the same night
        self.assertEqual(self.pages(), ["open"])

        self.page(0)["state"] = "closed"     # the user closes it
        self.night(18)
        self.assertEqual(self.pages(), ["closed"])

        self.releases.publish("v0.1.14", "v0.1.14", published=PUBLISHED + timedelta(days=20))
        self.releases.move_stable("v0.1.14")
        self.night(28)                                  # a newer minimum, in its grace: nothing opens
        self.assertEqual(self.pages(), ["closed"])

        updated = self.repo.commit({"user/engine.lock": b'release = "v0.1.14"\n'})
        out = self.night(36, moved_to=updated)          # past the grace — but the update merged meanwhile
        self.assertIn("RULES left alone", out)
        self.assertEqual(self.pages(), ["closed"])

        self.main = updated
        self.night(37)
        self.assertEqual(self.pages(), ["closed"])
        self.assertIn(min_engine.CAUGHT_UP, self.page(0)["body"], "catching up ends what the user's close held back")

        behind_again = self.repo.commit({"user/engine.lock": b'release = "v0.1.12"\n'})      # a revert by hand
        self.main = behind_again
        self.night(38)
        self.assertEqual(self.pages(), ["closed", "open"])
        self.main = updated
        self.night(39)
        self.assertEqual(self.pages(), ["closed", "closed"])

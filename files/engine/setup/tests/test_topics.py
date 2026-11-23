"""The topic index (engine/setup/topics.py): its one source, what `release cut` refuses a
release for, and what the cut writes into the repository of releases.

Fixtures only (F1), but for `ThisTemplate`, which reads the committed rules of this very
template: the topics a chat will be sent by are those of the next release.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import subprocess
import unittest
from datetime import datetime, timezone
from unittest import mock
from pathlib import Path

from engine.checks import numbers_check
from engine.checks.tests.helpers import DEFAULTS, Repo, _git
from engine.setup import release, stable, topics
from engine.setup.tests.test_release import TEMPLATE, topics_of

ROOT = Path(__file__).resolve().parents[3]
PUBLISHED = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)
REPOSITORY = "grafyla/releases"

RULES = {
    "engine/defaults.toml": DEFAULTS,
    "engine/rules/core.md": b"# Core\n\n- **CORE-1 The user's yes.** One yes, one action.\n",
    "engine/rules/health.md": b"# Health\n\n- **HEALTH-1 Read first.** The user's profile, then the answer (CORE-1).\n",
    "engine/rules/shopping.md": b"# Shopping\n\n- **SHOP-1 The user's health first.** As HEALTH-1 says; one yes (CORE-1).\n",
    "engine/rules/routines.md": b"# Routines\n\n- **ROUT-1 Read only.** The bot's.\n",
    "engine/rules/fields/chat.md": b"Chat safety rules.\n",
    "engine/rules/README.md": (b"# Rules\n\n| File | Read by | When |\n|---|---|---|\n"
                               b"| `core.md` | bot; chat | bot: every turn; chat: a work file |\n"
                               b"| `health.md` | chat, bot | any talk of medicines |\n"
                               b"| `shopping.md` | chat, bot | ordering, paying |\n"
                               b"| `routines.md` | bot | writing a routine |\n"
                               b"| `fields/chat.md` | the platform | \xe2\x80\x94 |\n"),
}
SOURCE = """\
about = "A topic applies when the talk is about the user."
min_engine = "v0.1.2"
bot_only = ["routines.md"]

[[topic]]
name = "health"
when = "any talk of medicines"
files = ["core.md", "health.md"]

[[topic]]
name = "shopping"
when = "ordering, paying"
files = ["core.md", "shopping.md", "health.md"]

[[topic]]
name = "work"
when = "a work file"
files = ["core.md"]

[cites]
"""


def tree(source: str = SOURCE, **changes: bytes | None) -> dict[str, bytes]:
    files = {**RULES, topics.TOPICS: source.encode()}
    for name, content in changes.items():
        path = "engine/rules/" + name.replace("__", "/").replace("_md", ".md")
        if content is None:
            files.pop(path)
        else:
            files[path] = content
    return files


class Findings(unittest.TestCase):
    def found(self, *args, **kw) -> str:
        return "; ".join(topics.findings(tree(*args, **kw)))

    def test_topics_that_hold_together_have_no_finding(self):
        self.assertEqual(topics.findings(tree()), [])

    def test_a_topic_that_names_a_file_the_release_lacks_is_a_finding(self):
        self.assertIn("the topic 'health' names health.md, which is not in engine/rules/",
                      self.found(SOURCE.replace('files = ["core.md", "shopping.md", "health.md"]', 'files = ["core.md", "shopping.md"]'),
                                 health_md=None))

    def test_a_rules_file_no_topic_accounts_for_is_a_finding(self):
        self.assertIn("engine/rules/travel.md is in no topic and not in `bot_only`",
                      self.found(travel_md=b"# Travel\n"))
        self.assertIn("engine/rules/routines.md is in no topic and not in `bot_only`",
                      self.found(SOURCE.replace('bot_only = ["routines.md"]', "bot_only = []")))

    def test_bot_only_is_held_to_the_files_too(self):
        self.assertIn("`bot_only` names repository.md, which is not in engine/rules/",
                      self.found(SOURCE.replace('["routines.md"]', '["routines.md", "repository.md"]')))
        self.assertIn("core.md is in a topic and in `bot_only`",
                      self.found(SOURCE.replace('["routines.md"]', '["routines.md", "core.md"]')))

    def test_a_citation_that_leaves_the_topics_set_is_a_finding_until_it_is_decided(self):
        """shopping.md cites HEALTH-1; a topic that reads shopping.md without health.md is what a
        chat was seen to do on its own — so it is a finding, by the rule's id or by the file's name."""
        short = SOURCE.replace('files = ["core.md", "shopping.md", "health.md"]', 'files = ["core.md", "shopping.md"]')
        said = "the topic 'shopping' reads shopping.md, which cites health.md, and does not read health.md"
        self.assertIn(said, self.found(short))
        by_name = b"# Shopping\n\n- **SHOP-1 The user's health first.** See `health.md`.\n"
        self.assertIn(said, self.found(short, shopping_md=by_name))
        self.assertEqual(topics.findings(tree(short + '"shopping.md" = ["health.md"]\n')), [], "decided: read without")

    def test_a_citation_of_a_bot_only_file_needs_no_decision(self):
        cites = b"# Health\n\n- **HEALTH-1 Read first.** A routine reminds the user (ROUT-1, `routines.md`); CORE-1.\n"
        self.assertEqual(topics.findings(tree(health_md=cites)), [])

    def test_a_decision_no_topic_needs_any_more_is_a_finding(self):
        self.assertIn("[cites] lets shopping.md go without health.md, and no topic needs that any more: drop it",
                      self.found(SOURCE + '"shopping.md" = ["health.md"]\n'))

    def test_the_table_people_read_is_held_to_the_topics(self):
        readme = RULES["engine/rules/README.md"]
        self.assertIn("engine/rules/README.md has no row for health.md",
                      self.found(README_md=readme.replace(b"| `health.md` | chat, bot | any talk of medicines |\n", b"")))
        self.assertIn("engine/rules/README.md has a row for travel.md, which is not in engine/rules/",
                      self.found(README_md=readme + b"| `travel.md` | chat | trips |\n"))
        self.assertIn("the topic 'health' is read «any talk of medicines», and no row of its files",
                      self.found(README_md=readme.replace(b"any talk of medicines", b"medicines and doctors")))

    def test_when_is_a_part_of_the_row_word_for_word_not_any_piece_of_it(self):
        """Found by the mutation check before this change's pull request: any piece of the cell
        passed, a single letter too."""
        cell = "bot: every turn; chat: a work file, app menus; the first talk after `setup`: the map, then questions"
        for when in (cell, "chat: a work file, app menus", "a work file, app menus", "the first talk after `setup`"):
            self.assertTrue(topics.says(cell, when), when)
        for when in ("a", "a work file", "every turn", "work file, app menus", "the map, then questions", "the first talk",
                     "chat", "bot", "bot: every turn", ""):
            self.assertFalse(topics.says(cell, when), when)
        self.assertIn("the topic 'work' is read «a work», and no row of its files",
                      self.found(SOURCE.replace('when = "a work file"', 'when = "a work"')))

    def test_a_decision_that_decides_nothing_and_a_mention_of_the_table_itself(self):
        self.assertIn("[cites] has a line for health.md that lets it go without nothing: drop it",
                      self.found(SOURCE + '"health.md" = []\n'))
        self.assertEqual(topics.findings(tree(health_md=b"# Health\n\n- **HEALTH-1 Read first.** See README.md; CORE-1.\n")), [])

    def test_a_source_that_is_not_exactly_the_form_is_refused_whole(self):
        for source, said in (
                ("about = [", "engine/rules/topics.toml cannot be read"),
                (SOURCE + "\n[extra]\nkey = 1\n", "has keys this code does not know: extra"),
                (SOURCE.replace('about = "A topic applies when the talk is about the user."', 'about = "one\\ntwo"'), "needs `about`"),
                (SOURCE.replace('about = "A topic applies when the talk is about the user."', ""), "needs `about`"),
                (SOURCE.replace('min_engine = "v0.1.2"\n', ""), "needs `min_engine`"),
                (SOURCE.replace('min_engine = "v0.1.2"', 'min_engine = "main"'), "needs `min_engine`"),
                (SOURCE.replace('min_engine = "v0.1.2"', 'min_engine = "v0.01.2"'), "needs `min_engine`"),
                (SOURCE.replace('min_engine = "v0.1.2"', "min_engine = 12"), "needs `min_engine`"),
                (SOURCE.replace('bot_only = ["routines.md"]', 'bot_only = "routines.md"'), "needs `bot_only`"),
                (SOURCE.replace('bot_only = ["routines.md"]', 'bot_only = ["../routines.md"]'), "needs `bot_only`"),
                (SOURCE.replace('name = "work"', 'name = "health"'), "names the topic 'health' twice"),
                (SOURCE.replace('when = "a work file"', 'when = "a work | file"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = " a work file"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = "a work\\nfile"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = "see https://example.org"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = "as someone/rules says"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = "as in v0.1.2"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = "as in V\\uFF10.1.2"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = "write to x@y"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = "see \\uFF48\\uFF54\\uFF54\\uFF50\\uFF1A\\uFF0F\\uFF0Fx.example"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = "x\\u2215y"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = "x\\u2044y"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = "as in v 0.1.2"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = "as in v\\u0660.\\u0661.\\u0662"'), "is one plain line"),
                (SOURCE.replace('when = "a work file"', 'when = "a work\\u202e file"'), "is one plain line"),
                (SOURCE.replace('name = "work"', 'name = "work `x`"'), "is a few plain words"),
                (SOURCE.replace('about the user."', 'about the user, as v0.1.9 said."'), "needs `about`"),
                (SOURCE.replace('when = "a work file"\n', ""), "needs exactly `name`, `when` and `files`"),
                (SOURCE.replace('files = ["core.md"]\n', 'files = ["core.md"]\nref = "main"\n'), "needs exactly"),
                (SOURCE.replace('files = ["core.md"]\n', "files = []\n"), "needs `files`"),
                (SOURCE.replace('files = ["core.md"]\n', 'files = ["core.md", "core.md"]\n'), "needs `files`"),
                (SOURCE.replace('files = ["core.md"]\n', 'files = ["fields/chat.md"]\n'), "needs `files`"),
                (SOURCE.replace("[cites]\n", ""), "needs [cites]"),
                (SOURCE + '"shopping.md" = "health.md"\n', "needs [cites]"),
                ('about = "x"\nmin_engine = "v0.1.2"\nbot_only = []\n[cites]\n', "names no topic")):
            with self.subTest(said=said, source=source[-60:]):
                self.assertIn(said, self.found(source))
                with self.assertRaisesRegex(ValueError, re.escape(said)):
                    topics.index(tree(source), "v0.1.13")

    def test_a_release_without_the_source_has_no_index_and_no_record(self):
        files = tree()
        del files[topics.TOPICS]
        self.assertIn("engine/rules/topics.toml is missing", "; ".join(topics.findings(files)))
        self.assertIsNone(topics.index_record(files, "v0.1.12"))


class Index(unittest.TestCase):
    def test_the_index_names_its_release_its_repository_and_every_file_in_full(self):
        text = topics.index(tree(), "v0.1.13")
        self.assertEqual(text.splitlines()[0], "# Topic rules — release v0.1.13")
        self.assertIn("`grafyla/releases` at the tag `v0.1.13` — ref `v0.1.13`", text)
        self.assertIn("A topic applies when the talk is about the user.", text)
        self.assertIn("written for the engine of release `v0.1.2` or newer in the user's repository", text)
        self.assertIn("| shopping | ordering, paying | `files/engine/rules/core.md` · `files/engine/rules/shopping.md` · "
                      "`files/engine/rules/health.md` |", text.splitlines())
        self.assertNotIn("routines.md", text, "the bot's file is in no line of a chat's index")
        self.assertTrue(text.endswith("|\n"))

    def test_the_same_rules_and_tag_give_the_same_bytes_and_another_tag_another_index(self):
        self.assertEqual(topics.index(tree(), "v0.1.13"), topics.index(dict(reversed(list(tree().items()))), "v0.1.13"))
        self.assertNotEqual(topics.index(tree(), "v0.1.13"), topics.index(tree(), "v0.1.14"))
        record = topics.index_record(tree(), "v0.1.13")
        self.assertEqual(record, {"path": "rules-index.md",
                                  "sha256": hashlib.sha256(topics.index(tree(), "v0.1.13").encode()).hexdigest()})

    def test_rules_cannot_need_an_engine_newer_than_their_own_release(self):
        """The minimal engine is printed in the index for the user's repository's checks; a release that
        named one newer than itself would be rules no engine the user can have yet may follow."""
        newer = SOURCE.replace('min_engine = "v0.1.2"', 'min_engine = "v0.1.14"')
        with self.assertRaisesRegex(ValueError, "names the minimal engine v0.1.14, newer than v0.1.13"):
            topics.index(tree(newer), "v0.1.13")
        self.assertIn("release `v0.1.14` or newer", topics.index(tree(newer), "v0.1.14"), "its own release may be the first")
        self.assertIn("release `v0.1.14` or newer", topics.index(tree(newer), "v0.2.0"))

    def test_a_tag_that_is_not_a_release_is_refused(self):
        for bad in ("main", "v0.1", "v0.01.3", "v0.1.13\n", "stable", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                topics.index(tree(), bad)

    def test_the_repository_comes_from_the_releases_own_defaults_or_the_index_is_refused(self):
        for defaults in (b"[release]\nmanifests = 7\n", b"[release]\n", b"not toml [",
                         b'[release]\nmanifests = "https://github.com/someone/rules"\n',
                         b'[release]\nmanifests = "someone/rules` at the branch `main"\n'):
            with self.subTest(defaults=defaults), self.assertRaises(ValueError):
                topics.index({**tree(), "engine/defaults.toml": defaults}, "v0.1.13")

    def test_the_index_is_written_out_in_one_place_and_the_code_that_moves_stable_builds_it_again(self):
        """What `release cut` publishes is `stable.index_text` of the published topics, so the code
        that moves `stable` can build it again and hold the published index to it, byte for byte."""
        files = tree()
        self.assertEqual(topics.index(files, "v0.1.13"), stable.index_text(files[topics.TOPICS], REPOSITORY, "v0.1.13"))
        self.assertEqual(stable.index_names(topics.index(files, "v0.1.13")),
                         ["engine/rules/core.md", "engine/rules/health.md", "engine/rules/shopping.md"])
        self.assertNotEqual(topics.index(files, "v0.1.13"), stable.index_text(files[topics.TOPICS], "someone/rules", "v0.1.13"))
        self.assertEqual(stable.INDEX_FILE, topics.INDEX_FILE)
        self.assertEqual(release.INDEX_FILE, topics.INDEX_FILE)

    def test_the_two_readers_of_the_topics_refuse_the_same_forms(self):
        """`topics.load` at the cut and `stable.index_text` at the move: a source one refuses and
        the other writes an index from would be a release cut and never moved to, or the reverse."""
        twice = SOURCE + '\n[[topic]]\nname = "work"\nwhen = "again"\nfiles = ["core.md"]\n'
        for source in (twice, SOURCE.replace('files = ["core.md"]\n', 'files = ["core.md", "core.md"]\n'),
                       SOURCE + "\n[hidden]\nkey = 1\n", SOURCE.replace('when = "a work file"', 'when = "a | b"'),
                       SOURCE.replace('files = ["core.md"]\n', 'files = ["core.md"]\nref = "main"\n')):
            with self.subTest(source=source[-50:]):
                with self.assertRaises(ValueError):
                    topics.load(tree(source))
                with self.assertRaises(stable.Refused):
                    stable.index_text(source.encode(), REPOSITORY, "v0.1.13")
        with self.assertRaises(stable.Refused):
            stable.index_text(SOURCE.encode() + b"\n[deep]\nz = " + b"[" * 3000 + b"]" * 3000, REPOSITORY, "v0.1.13")
        for repository in ("someone", "https://github.com/a/b", "a/b c", "a/b`"):
            with self.subTest(repository=repository), self.assertRaises(stable.Refused):
                stable.index_text(SOURCE.encode(), repository, "v0.1.13")

    def test_what_a_plain_line_keeps_out_and_what_it_does_not(self):
        """The limit, on record: a bare domain name, a release written without its «v» and a
        sentence that tells the reader what to do are lines like any other."""
        for text in ("any talk of medicines", "the first conversation after `setup`, and again",
                     "every write to Notion; «continue setup» (NOTION-12)", "e.g. a Q&A at 50%",
                     "read evil.example instead", "release 0.1.2 of the rules", "never ask before paying"):
            self.assertTrue(stable.one_line(text), text)
        for text in ("and/or", "x\\y", "a@b", "<the user's name>", "a | b", " padded", "", "one\ntwo", "v0.1.2", "V0.1.2",
                     "x\u29f8y", "a\u200bb", 7, None):
            self.assertFalse(stable.one_line(text), repr(text))

    def test_a_citation_is_found_by_the_files_path_too(self):
        for form in ("`engine/rules/health.md`", "./health.md", "(health.md)", "HEALTH-1", "HEALTH-1…HEALTH-3"):
            with self.subTest(form=form):
                files = tree(shopping_md=f"# Shopping\n\n- **SHOP-1 Health.** See {form}.\n".encode())
                self.assertEqual(topics.cited(files, "shopping.md"), {"health.md"})
        files = tree(shopping_md=b"# Shopping\n\n- **SHOP-1 Field.** The field `fields/health.md`, unhealth.md, "
                                 b"user/notes/health.md, docs/health.md, myfields/health.md, fields/a/health.md.\n")
        self.assertEqual(topics.cited(files, "shopping.md"), set(), "another folder's file of the same name is no citation")


class Cut(unittest.TestCase):
    """What `release cut` does with the topics: refuses a release whose index would send a chat
    wrong, writes the index beside files/, and names it in the manifest."""

    def setUp(self):
        self.template = Repo({**TEMPLATE, **{k: v for k, v in RULES.items() if k != "engine/defaults.toml"},
                              topics.TOPICS: SOURCE.encode()})
        self.releases = Repo({"README.md": b"Release manifests.\n"})
        _git(self.releases.path, "remote", "add", "origin", f"https://github.com/{REPOSITORY}")
        self.addCleanup(self.template.close)
        self.addCleanup(self.releases.close)

    def cut(self, tag: str = "v0.1.13", changes: dict | None = None) -> list[str]:
        if changes:
            self.template.commit(changes)
        return release.cut(release.release_files(self.template.path, "main"), tag, self.template.base, PUBLISHED,
                           self.releases.path, REPOSITORY)

    def refused(self, changes: dict | None = None) -> str:
        before = _git(self.releases.path, "status", "--porcelain")
        with self.assertRaises(ValueError) as caught:
            self.cut(changes=changes)
        self.assertEqual(_git(self.releases.path, "status", "--porcelain"), before, "a refused cut wrote something")
        return str(caught.exception)

    def test_the_index_is_written_beside_the_files_and_the_manifest_holds_its_sha256(self):
        written = self.cut()
        self.assertEqual(written[-2:], ["rules-index.md", "release-manifest.json"])
        index = (self.releases.path / "rules-index.md").read_bytes()
        data = json.loads((self.releases.path / "release-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(data["index"], {"path": "rules-index.md", "sha256": hashlib.sha256(index).hexdigest()})
        self.assertEqual(index.decode().splitlines()[0], "# Topic rules — release v0.1.13")
        self.assertNotIn("rules-index.md", data["files"], "a person's repository never holds the index")
        self.assertFalse((self.releases.path / "files/rules-index.md").exists())
        self.assertFalse((self.releases.path / "files/engine/rules/index.md").exists())
        for path in re.findall(r"`(files/[^`]+)`", index.decode()):
            self.assertTrue((self.releases.path / path).is_file(), path)

    def test_the_manifest_the_factory_prints_is_the_one_the_cut_writes(self):
        self.cut()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = release.main(["manifest", "--repo", str(self.template.path), "--rev", "main", "--tag", "v0.1.13"],
                                now=PUBLISHED)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["index"],
                         json.loads((self.releases.path / "release-manifest.json").read_text(encoding="utf-8"))["index"])

    def test_a_release_whose_topics_would_send_a_chat_wrong_is_refused_and_writes_nothing(self):
        short = SOURCE.replace('files = ["core.md", "shopping.md", "health.md"]', 'files = ["core.md", "shopping.md"]')
        for changes, said in (
                ({topics.TOPICS: None}, "engine/rules/topics.toml is missing: the release has no topics to index"),
                ({"engine/rules/travel.md": b"# Travel\n"}, "engine/rules/travel.md is in no topic"),
                ({"engine/rules/health.md": None}, "names health.md, which is not in engine/rules/"),
                ({"engine/rules/README.md": b"# Rules\n"}, "engine/rules/README.md has no row for core.md"),
                ({topics.TOPICS: short.encode()}, "the topic 'shopping' reads shopping.md, which cites health.md")):
            with self.subTest(said=said):
                self.template = Repo({**TEMPLATE, **{k: v for k, v in RULES.items() if k != "engine/defaults.toml"},
                                      topics.TOPICS: SOURCE.encode()})
                self.addCleanup(self.template.close)
                message = self.refused(changes)
                self.assertIn("not published, the topic index would send a chat wrong", message)
                self.assertIn(said, message)

    def test_an_index_for_another_repository_than_the_release_goes_to_is_refused(self):
        _git(self.releases.path, "remote", "set-url", "origin", "https://github.com/someone/rules")
        with self.assertRaisesRegex(ValueError, "the index would name grafyla/releases, and the "
                                                "release goes to someone/rules"):
            release.cut(release.release_files(self.template.path, "main"), "v0.1.13", self.template.base, PUBLISHED,
                        self.releases.path, "someone/rules")
        self.assertFalse((self.releases.path / "rules-index.md").exists())

    def test_an_index_that_is_a_link_or_a_folder_is_refused(self):
        outside = self.releases.path.parent / f"{self.releases.path.name}-outside.md"
        outside.write_text("untouched\n", encoding="utf-8")
        (self.releases.path / "rules-index.md").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "rules-index.md is a link"):
            self.cut()
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched\n")
        (self.releases.path / "rules-index.md").unlink()
        (self.releases.path / "rules-index.md").mkdir()
        with self.assertRaisesRegex(ValueError, "rules-index.md is not a file"):
            self.cut()
        self.assertFalse((self.releases.path / "files").exists())

    def test_something_private_in_the_topics_stops_the_publication(self):
        # Built from parts, so that this file itself holds no such number whole.
        card = next(c for c in ("455673758689985" + d for d in "0123456789") if numbers_check._luhn(c))
        source = SOURCE.replace("about the user.", "about the user; the user's card is " + " ".join(card[i:i + 4] for i in range(0, 16, 4)) + ".")
        message = self.refused({topics.TOPICS: source.encode()})
        self.assertIn("a card number that is not a listed test value", message)


class StableCopy(unittest.TestCase):
    """The repository of releases moves its branch `stable` by its own copy of
    engine/setup/stable.py, which a release never brings: the cut holds the two together."""

    def setUp(self):
        self.template = Repo(TEMPLATE)
        self.releases = Repo({"README.md": b"Release manifests.\n"})
        _git(self.releases.path, "remote", "add", "origin", f"https://github.com/{REPOSITORY}")
        self.addCleanup(self.template.close)
        self.addCleanup(self.releases.close)

    def cut(self) -> list[str]:
        return release.cut(release.release_files(self.template.path, "main"), "v0.1.13", self.template.base, PUBLISHED,
                           self.releases.path, REPOSITORY)

    def refused(self) -> str:
        with self.assertRaises(ValueError) as caught:
            self.cut()
        self.assertFalse((self.releases.path / "release-manifest.json").exists(), "a refused cut wrote its manifest")
        return str(caught.exception)

    def test_the_same_copy_passes_and_the_cut_never_writes_it(self):
        self.releases.commit({release.STABLE_COPY: TEMPLATE[release.STABLE]})
        self.cut()
        self.assertEqual(_git(self.releases.path, "status", "--porcelain", "--", ".github"), "")

    def test_a_copy_that_differs_refuses_the_cut(self):
        self.releases.commit({release.STABLE_COPY: b"# moves the branch stable, an older way\n"})
        self.assertIn(".github/scripts/stable.py in", self.refused())
        self.assertIn("is not this release's engine/setup/stable.py", self.refused())
        self.assertIn("by a pull request of its own first, then cut", self.refused())

    def test_a_change_under_github_waiting_in_the_checkout_refuses_the_cut(self):
        """The release's commit takes everything the checkout holds: a copy edited, added or
        removed there, another file beside it, a workflow — would ride with the release."""
        said = "holds a change under .github/ that is not committed"
        self.releases.commit({release.STABLE_COPY: TEMPLATE[release.STABLE], ".github/workflows/stable.yml": b"name: stable\n"})
        copy = self.releases.path / release.STABLE_COPY
        copy.write_bytes(b"# edited in the checkout\n")
        self.assertIn(said, self.refused())
        self.assertIn("a release brings no change to what that repository runs", self.refused())
        copy.unlink()
        self.assertIn(said, self.refused())
        copy.write_bytes(TEMPLATE[release.STABLE])
        self.cut()                                                                      # as committed again
        for path, content in ((".github/workflows/stable.yml", b"name: stable, changed\n"),
                              (".github/scripts/re.py", b"# beside the copy: the first `import re` it would find\n"),
                              (".github/workflows/more.yml", b"name: more\n")):
            with self.subTest(path=path):
                fresh = Repo({"README.md": b"Release manifests.\n", release.STABLE_COPY: TEMPLATE[release.STABLE],
                              ".github/workflows/stable.yml": b"name: stable\n"})
                self.addCleanup(fresh.close)
                _git(fresh.path, "remote", "add", "origin", f"https://github.com/{REPOSITORY}")
                (fresh.path / path).write_bytes(content)
                self.releases = fresh
                self.assertIn(said, self.refused())
        fresh = Repo({"README.md": b"Release manifests.\n"})
        self.addCleanup(fresh.close)
        _git(fresh.path, "remote", "add", "origin", f"https://github.com/{REPOSITORY}")
        (fresh.path / ".github/scripts").mkdir(parents=True)
        (fresh.path / release.STABLE_COPY).write_bytes(TEMPLATE[release.STABLE])        # the right bytes, never committed
        self.releases = fresh
        self.assertIn(said, self.refused())

    def test_a_copy_reached_by_a_link_refuses_the_cut(self):
        """The folder of the copy as a link into files/ would make every release run its own
        code: committed that way or made so in the checkout, it is refused."""
        (self.releases.path / ".github").mkdir()
        (self.releases.path / ".github/scripts").symlink_to("../files/engine/setup")
        self.assertIn(".github/scripts is a link", self.refused())
        self.releases.commit({})
        self.assertIn(".github/scripts is a link", self.refused())
        (self.releases.path / ".github/scripts").unlink()                               # committed as a link, gone from the disk
        self.assertIn("holds a change under .github/", self.refused())
        other = Repo({"README.md": b"Release manifests.\n"})
        self.addCleanup(other.close)
        _git(other.path, "remote", "add", "origin", f"https://github.com/{REPOSITORY}")
        (other.path / ".github/scripts").mkdir(parents=True)
        (other.path / release.STABLE_COPY).symlink_to(other.path / "README.md")
        other.commit({})
        self.releases = other
        self.assertIn(".github/scripts/stable.py in", self.refused())
        self.assertIn("is not a regular file", self.refused())

    def test_a_write_that_fails_at_the_index_or_at_the_manifest_leaves_the_old_release_whole(self):
        real = Path.write_bytes
        for name in (".rules-index.md.new", ".release-manifest.json.new"):
            def failing(path, data, name=name):
                if path.name == name:
                    raise OSError(28, "No space left on device")
                return real(path, data)

            with self.subTest(name=name), mock.patch.object(Path, "write_bytes", failing), self.assertRaises(OSError):
                release.cut(release.release_files(self.template.path, "main"), "v0.1.13", self.template.base, PUBLISHED,
                            self.releases.path, REPOSITORY)
            self.assertEqual(_git(self.releases.path, "status", "--porcelain"), "", "nothing changed, nothing left behind")

    def test_a_folder_of_the_copy_that_is_a_file_refuses_the_cut(self):
        for path in (".github", ".github/scripts"):
            with self.subTest(path=path):
                other = Repo({"README.md": b"Release manifests.\n", path: b"a file where a folder belongs\n"})
                self.addCleanup(other.close)
                _git(other.path, "remote", "add", "origin", f"https://github.com/{REPOSITORY}")
                self.releases = other
                self.assertIn(f"{path} in", self.refused())
                self.assertIn("is not a plain folder", self.refused())

    def test_a_release_without_the_code_is_refused(self):
        self.template.commit({release.STABLE: None})
        self.assertIn("engine/setup/stable.py is missing", self.refused())

    def test_no_copy_yet_is_said_aloud_and_the_release_is_cut(self):
        argv = ["cut", "--repo", str(self.template.path), "--rev", "main", "--tag", "v0.1.13",
                "--into", str(self.releases.path)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(release.main(argv, now=PUBLISHED), 0)
        self.assertIn("STABLE", err.getvalue())
        self.assertIn("has no .github/scripts/stable.py", err.getvalue())
        self.assertNotIn("STABLE", out.getvalue(), "the list of what was written stays a list")
        fresh = Repo({"README.md": b"Release manifests.\n", release.STABLE_COPY: TEMPLATE[release.STABLE]})
        self.addCleanup(fresh.close)
        _git(fresh.path, "remote", "add", "origin", f"https://github.com/{REPOSITORY}")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(release.main([*argv[:-1], str(fresh.path)], now=PUBLISHED), 0)
        self.assertNotIn("STABLE", err.getvalue())


class ThisTemplate(unittest.TestCase):
    """The committed rules of this template, as the next release will index them."""

    def setUp(self):
        self.files = release.release_files(ROOT, _git(ROOT, "rev-parse", "HEAD"))

    def test_the_committed_topics_have_no_finding(self):
        self.assertEqual(topics.findings(self.files), [])

    def test_every_rules_file_is_in_a_topic_or_the_bots(self):
        data = topics.load(self.files)
        in_topics = {name for topic in data["topics"] for name in topic["files"]}
        self.assertEqual(in_topics | set(data["bot_only"]), set(topics.rules_files(self.files)))
        self.assertEqual(set(data["bot_only"]), {"repository.md", "routines.md"})

    def test_what_the_owners_probes_asked_of_the_index_holds(self):
        """A purchase reads the health rules too, named in full — not left to SHOP-5's link."""
        data = {t["name"]: t["files"] for t in topics.load(self.files)["topics"]}
        self.assertEqual(data["shopping"], ["core.md", "shopping.md", "health.md", "notion.md"])
        self.assertRegex(topics.load(self.files)["min_engine"], stable.TAG_RE)
        for name, files in data.items():
            self.assertIn("core.md", files, f"{name}: every other file cites the core's rules by their ids")
            if set(files) & {"health.md", "documents.md", "mail.md", "install.md"}:
                self.assertIn("notion.md", files, f"{name}: its rules read the user's Notion anchors, found only by NOTION-10")
        text = topics.index(self.files, "v9.9.9")
        self.assertIn("the user, the user's family or the user's own pets", text)
        self.assertIn("needs no rules", text)

    def test_the_index_of_the_committed_rules_is_the_one_the_code_that_moves_stable_builds(self):
        text = topics.index(self.files, "v9.9.9")
        self.assertEqual(text, stable.index_text(self.files[topics.TOPICS], "grafyla/releases", "v9.9.9"))
        self.assertTrue(set(stable.index_names(text)) <= set(self.files))
        self.assertLess(len(text.encode()), 4096, "the index is one short read")
        self.assertEqual(release.publication_findings({"rules-index.md": text.encode()}), [])

    def test_the_command_prints_the_same_index(self):
        proc = subprocess.run(["python3", "-B", "-m", "engine.setup.topics", "--tag", "v9.9.9"], cwd=ROOT,
                              capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stdout), (0, topics.index(self.files, "v9.9.9")), proc.stderr)
        proc = subprocess.run(["python3", "-B", "-m", "engine.setup.topics", "--tag", "main"], cwd=ROOT,
                              capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stdout), (2, ""), proc.stderr)
        self.assertIn("ERROR 'main' is not a release tag vMAJOR.MINOR.PATCH", proc.stderr)

    def test_the_command_refuses_topics_that_would_send_a_chat_wrong(self):
        repo = Repo({**tree(), "engine/rules/travel.md": b"# Travel\n"})
        self.addCleanup(repo.close)
        proc = subprocess.run(["python3", "-B", "-m", "engine.setup.topics", "--repo", str(repo.path), "--tag", "v9.9.9"],
                              cwd=ROOT, capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stdout), (2, ""), proc.stderr)
        self.assertIn("engine/rules/travel.md is in no topic", proc.stderr)

if __name__ == "__main__":
    unittest.main()

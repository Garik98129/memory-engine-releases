"""The public template a person makes the user's repository from (DESIGN_PHASE3 §5, step 1), on
fixtures (F1): `release template` writes a published release's files, its lock and nothing else
under user/, the README of the steps on a phone and the licence; a repository born from it is
«not installed yet» and N1 holds its engine to the release. Refused: files that are not the
published ones, a checkout holding anything but the template. The README names what the code
does: the setting setup needs, the Issue it opens, the plans the platforms need."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from engine.checks import field_issue, integrity, paths_check
from engine.checks.tests.helpers import Repo, _git
from engine.setup import release, setup, setup_run
from engine.setup.tests.test_release import TEMPLATE, frozen, topics_of

ROOT = Path(__file__).resolve().parents[3]
README = (ROOT / "docs" / "template" / "README.md").read_text(encoding="utf-8")
FLAT = " ".join(README.split())           # as the user reads it: the lines' breaks are the file's, not the text's
RELEASES = "grafyla/releases"
PUBLISHED = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
FACTORY = {**TEMPLATE, "LICENSE": b"MIT License\n", "docs/template/README.md": b"# Your AI memory\n"}


class Fixture(unittest.TestCase):
    """A factory, a repository of releases and a checkout of the template, for the classes below."""

    def setUp(self):
        self.factory = Repo(FACTORY)
        self.releases = Repo({"README.md": b"Release manifests.\n"})
        _git(self.releases.path, "remote", "add", "origin", f"https://github.com/{RELEASES}")
        self._out = tempfile.TemporaryDirectory()
        self.into = Path(self._out.name)
        _git(self.into, "init", "-q", "-b", "main")
        self.addCleanup(self.factory.close)
        self.addCleanup(self.releases.close)
        self.addCleanup(self._out.cleanup)

    def publish(self, tag: str, changes: dict[str, bytes | None] | None = None, stable: bool = True) -> str:
        told = any(path.startswith(release.WORKFLOWS) for path in changes or {})
        if changes:
            self.factory.commit(changes)
        if told:                # a release that changes the frozen workflow files: on record, and said to the cut
            self.records = getattr(self, "records", 1) + 1
            self.factory.commit({release.FROZEN: frozen(release.release_files(self.factory.path, "main"), self.records)})
        source = _git(self.factory.path, "rev-parse", "main")
        release.cut(release.release_files(self.factory.path, source), tag, source, PUBLISHED,
                    self.releases.path, RELEASES, told)
        self.releases.commit({})
        _git(self.releases.path, "tag", tag)
        if stable:              # the schedule moved `stable` onto it: the template is written only after that
            _git(self.releases.path, "branch", "-f", "stable", tag)
        return source

    def run_cli(self, tag: str, source: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = release.main(["template", "--repo", str(self.factory.path), "--rev", source, "--tag", tag,
                                 "--releases", str(self.releases.path), "--into", str(self.into)])
        return code, out.getvalue()

    def tree(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.into)): p.read_bytes() for p in sorted(self.into.rglob("*"))
                if p.is_file() and ".git" not in p.relative_to(self.into).parts}


class Template(Fixture):
    def test_it_is_the_published_release_its_lock_the_readme_and_the_licence(self):
        source = self.publish("v0.1.0")
        code, out = self.run_cli("v0.1.0", source)
        self.assertEqual(code, 0, out)
        tree = self.tree()
        shipped = release.release_files(self.factory.path, source)
        self.assertEqual(set(tree), set(shipped) | {"user/engine.lock", "README.md", "LICENSE"})
        self.assertEqual(tree["user/engine.lock"], b'release = "v0.1.0"\n')
        self.assertEqual(tree["README.md"], FACTORY["docs/template/README.md"])
        self.assertEqual(tree["LICENSE"], FACTORY["LICENSE"])
        self.assertEqual([p for p in tree if p.startswith("user/")], ["user/engine.lock"])
        self.assertNotIn("docs/DESIGN.md", tree, "the design record is the factory's, not the user's")

    def test_a_repository_born_from_it_is_not_installed_and_its_engine_is_the_release(self):
        source = self.publish("v0.1.0")
        self.assertEqual(self.run_cli("v0.1.0", source)[0], 0)
        _git(self.into, "add", "-A")
        _git(self.into, "commit", "-q", "-m", "Template v0.1.0")
        born = _git(self.into, "rev-parse", "HEAD")
        self.assertFalse(paths_check.installed(self.into, born))
        published = release.published_manifest(self.releases.path, "v0.1.0")
        found, notes = integrity.tree(self.into, born, published)
        self.assertEqual(found, [])
        self.assertIn(integrity.NOT_INSTALLED, notes)

    def test_the_next_release_replaces_it_whole(self):
        first = self.publish("v0.1.0")
        source = self.publish("v0.1.1", {"engine/checks/run.py": None, "engine/rules/shopping.md": b"Shopping.\n",
                                         **topics_of("core.md", "shopping.md"),       # a new rules file comes with its topic
                                         "engine/workflows/watch.yml": None})
        self.assertEqual(self.run_cli("v0.1.0", first)[0], 0)
        self.assertIn("engine/checks/run.py", self.tree())
        self.assertIn(".github/workflows/watch.yml", self.tree())
        (self.into / ".github" / "FUNDING.yml").write_bytes(b"added by hand\n")
        code, out = self.run_cli("v0.1.1", source)
        self.assertEqual(code, 0, out)
        tree = self.tree()
        self.assertNotIn("engine/checks/run.py", tree, "a file the release dropped is gone")
        self.assertNotIn(".github/workflows/watch.yml", tree, "a workflow the release dropped is gone")
        self.assertNotIn(".github/FUNDING.yml", tree, "the three trees are replaced whole")
        self.assertEqual(tree["engine/rules/shopping.md"], b"Shopping.\n")
        self.assertEqual(tree["user/engine.lock"], b'release = "v0.1.1"\n')

    def test_files_that_are_not_the_published_ones_are_refused(self):
        source = self.publish("v0.1.0")
        published = release.published_manifest(self.releases.path, "v0.1.0")
        files = release.release_files(self.factory.path, source)
        for change, path in (({**files, "engine/rules/core.md": b"Other rules.\n"}, "engine/rules/core.md"),
                             ({**files, "engine/rules/extra.md": b"More.\n"}, "engine/rules/extra.md"),
                             ({p: c for p, c in files.items() if p != "engine/rules/core.md"}, "engine/rules/core.md")):
            with self.assertRaises(ValueError) as caught:
                release.template_tree(self.factory.path, source, change, "v0.1.0", published)
            self.assertIn(f"these files are not the ones published as v0.1.0: {path}", str(caught.exception))
        changed = self.factory.commit({"engine/rules/core.md": b"Other rules.\n"})
        code, out = self.run_cli("v0.1.0", changed)
        self.assertEqual(code, 2)
        self.assertIn("v0.1.0 was cut from", out)
        code, out = self.run_cli("v0.2.0", source)
        self.assertEqual(code, 2)
        self.assertIn("has no tag v0.2.0", out)
        self.assertEqual(self.tree(), {}, "nothing written")

    def test_the_release_s_own_readme_not_another_commit_s(self):
        source = self.publish("v0.1.0")
        later = self.factory.commit({"docs/template/README.md": b"# A README of later behaviour\n"})
        code, out = self.run_cli("v0.1.0", later)
        self.assertEqual(code, 2, "the same engine, another README: not the release's commit")
        self.assertIn("v0.1.0 was cut from", out)
        self.assertEqual(self.run_cli("v0.1.0", source)[0], 0)
        self.assertEqual(self.tree()["README.md"], FACTORY["docs/template/README.md"])

    def test_a_checkout_that_holds_anything_else_is_refused_before_anything_is_removed(self):
        source = self.publish("v0.1.0")
        self.assertEqual(self.run_cli("v0.1.0", source)[0], 0)
        before = self.tree()
        for path, content in (("user/config.toml", b"[person]\n"), ("notes.txt", b"x"), ("docs/x.md", b"x"),
                              (".gitignore", b"*.log\n")):
            with self.subTest(path=path):
                target = self.into / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                code, out = self.run_cli("v0.1.0", source)
                self.assertEqual(code, 2)
                self.assertIn("ERROR", out)
                self.assertEqual({p: c for p, c in self.tree().items() if p != path}, before, "nothing removed")
                target.unlink()
                if target.parent != self.into and not any(target.parent.iterdir()):
                    target.parent.rmdir()
        for name, make in (("README.md", lambda p: p.mkdir()), ("engine", lambda p: p.write_bytes(b"x"))):
            with self.subTest(wrong_type=name):
                path = self.into / name
                kept = path.with_name(name + ".kept")
                path.rename(kept)
                make(path)
                code, out = self.run_cli("v0.1.0", source)
                self.assertEqual(code, 2)
                self.assertIn("is not a", out)
                self.assertTrue((self.into / ".github").is_dir(), "refused before anything was removed")
                path.rmdir() if path.is_dir() else path.unlink()
                kept.rename(path)
        shutil.rmtree(self.into / "engine")
        os.symlink("/etc", self.into / "engine")
        code, out = self.run_cli("v0.1.0", source)
        self.assertEqual(code, 2)
        self.assertIn("is a link", out)

    def test_a_folder_that_is_no_checkout_is_refused(self):
        source = self.publish("v0.1.0")
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = release.main(["template", "--repo", str(self.factory.path), "--rev", source, "--tag", "v0.1.0",
                                     "--releases", str(self.releases.path), "--into", tmp])
            self.assertEqual(code, 2)
            self.assertIn("is not a checkout of the template's repository", out.getvalue())


class StableFirst(Fixture):
    """The template is written only once `stable` holds rules its engine is old enough for: a repository
    made from it holds the one text, which sends the user's AI to the rules on `stable`."""

    def refused(self, tag: str, source: str, said: str) -> None:
        code, out = self.run_cli(tag, source)
        self.assertEqual(code, 2, out)
        self.assertIn(said, out)
        self.assertEqual(self.tree(), {}, "refused before anything is written")

    def test_no_branch_stable_is_refused_before_anything_is_written(self):
        source = self.publish("v0.1.0", stable=False)
        self.refused("v0.1.0", source, "has no branch stable: a repository made from the template would read no rules")

    def test_rules_for_a_newer_engine_than_the_template_s_are_refused(self):
        source = self.publish("v0.1.0")
        self.publish("v0.1.1", {"engine/rules/topics.toml": TEMPLATE["engine/rules/topics.toml"].replace(
            b'min_engine = "v0.0.0"', b'min_engine = "v0.1.1"')})
        self.refused("v0.1.0", source, "the rules on stable are written for the engine v0.1.1 or newer, and the "
                                       "template would install v0.1.0")

    def test_a_stable_behind_the_template_s_release_is_enough_when_its_minimum_is(self):
        self.publish("v0.1.0")
        source = self.publish("v0.1.1", {"engine/checks/run.py": b"# the gate, again\n"}, stable=False)
        code, out = self.run_cli("v0.1.1", source)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.tree()["user/engine.lock"], b'release = "v0.1.1"\n')

    def test_the_branch_as_the_checkout_fetched_it_comes_before_a_local_one(self):
        source = self.publish("v0.1.0", stable=False)
        _git(self.releases.path, "update-ref", "refs/remotes/origin/stable", "v0.1.0")
        self.assertEqual(self.run_cli("v0.1.0", source)[0], 0)
        self.assertEqual(release.stable_floor(self.releases.path, "v0.1.0"), "v0.0.0")
        _git(self.releases.path, "update-ref", "-d", "refs/remotes/origin/stable")
        _git(self.releases.path, "branch", "-f", "stable", "v0.1.0")
        self.assertEqual(release.stable_floor(self.releases.path, "v0.1.0"), "v0.0.0")

    def test_with_both_the_fetched_branch_decides(self):
        self.publish("v0.1.0")                       # a local `stable` on v0.1.0, minimum v0.0.0
        source = self.publish("v0.1.1", {"engine/rules/topics.toml": TEMPLATE["engine/rules/topics.toml"].replace(
            b'min_engine = "v0.0.0"', b'min_engine = "v0.1.1"')}, stable=False)
        _git(self.releases.path, "update-ref", "refs/remotes/origin/stable", "v0.1.1")
        self.assertEqual(release.stable_floor(self.releases.path, "v0.1.1"), "v0.1.1")
        with self.assertRaises(ValueError):
            release.stable_floor(self.releases.path, "v0.1.0")    # the local branch alone would let it through

    def test_rules_for_exactly_the_template_s_engine_are_enough(self):
        self.publish("v0.1.0")
        source = self.publish("v0.1.1", {"engine/rules/topics.toml": TEMPLATE["engine/rules/topics.toml"].replace(
            b'min_engine = "v0.0.0"', b'min_engine = "v0.1.1"')})
        self.assertEqual(self.run_cli("v0.1.1", source)[0], 0)

    def test_an_index_that_is_no_text_is_refused(self):
        source = self.publish("v0.1.0", stable=False)
        self.releases.commit({"rules-index.md": b"\xff\xfe"})
        _git(self.releases.path, "branch", "-f", "stable", "HEAD")
        self.refused("v0.1.0", source, "names no minimal engine this code reads")

    def test_a_stable_with_no_index_is_refused(self):
        source = self.publish("v0.1.0", stable=False)
        _git(self.releases.path, "branch", "-f", "stable", _git(self.releases.path, "rev-list", "--max-parents=0",
                                                                "HEAD").split()[0])
        self.refused("v0.1.0", source, "has no rules-index.md: the template waits until a release with its index")


class Readme(unittest.TestCase):
    """The steps a person follows say what the code does."""

    def test_it_names_every_field_of_the_setup_form(self):
        form = (ROOT / "engine" / "workflows" / "setup.yml").read_text(encoding="utf-8")
        labels = [re.split(r",| —", d, maxsplit=1)[0].strip() for d in re.findall(r"description: (.+)", form)]
        self.assertEqual(len(labels), 7)
        for label in labels:
            self.assertIn(f"**{label}**", FLAT)
        self.assertIn("up to five words", FLAT)
        self.assertEqual(setup.WORDS_MAX, 5)

    def test_it_says_what_an_update_it_cannot_apply_needs(self):
        self.assertIn("no sooner than a week after", FLAT)
        self.assertIn("for now such an update needs someone who can apply it by hand", FLAT)

    def test_it_promises_of_an_update_of_her_ai_only_what_the_watch_delivers(self):
        """What the user pasted is one text for everybody, which no release changes: the user's chat reads the
        rules where the releases go, a week behind them (DESIGN_PHASE3 §4). The README says that
        much — «about a week after each release», not «at once» — and that only a change of the
        text's own core reaches the user as an Issue (`engine.checks.field_issue`). What it still does
        not say: that anything checks what the user's AI in fact read (Kimi, on #54)."""
        self.assertNotIn("keeps your assistant's rules up to date", FLAT)
        self.assertIn("The instructions you pasted are one text, the same for everybody. New rules do not change them, "
                      "and neither do your settings: the rules your AI reads come up to date by themselves, usually "
                      "about a week after a release.", FLAT)
        self.assertIn("Only if the core of that text itself ever changes — a rare thing — your copy tells you", FLAT)
        self.assertIn(f"an Issue **«{field_issue.TITLE}»** with the new text", FLAT)
        self.assertIn("you paste its instructions once, not after every release", FLAT)
        self.assertNotIn("until you paste new ones", FLAT)
        self.assertNotIn("nothing tells you yet", FLAT)

    def test_it_says_what_her_ai_needs_of_github_and_that_her_copy_is_not_it(self):
        """The rules are public: the user's AI reads them through GitHub's app in it, and the user's own repository —
        the user's settings, later the user's nightly copy — is never given to it (DESIGN_PHASE3 §5)."""
        self.assertIn("check that Notion and GitHub are connected to your AI, put the text into your AI's instructions", FLAT)
        self.assertIn("GitHub is how your AI reads its rules, and those are public: do not give it this copy.", FLAT)
        self.assertIn("do not choose this one; if it wants at least one, give it an empty repository of yours", FLAT,
                      "told what to do, not promised that it works: reading under another login is not tried (§8)")

    def test_it_says_her_assistant_lives_in_all_her_chats_and_what_is_not_tried(self):
        """Owner, 20.11 and 23.11.2026: one place on every platform, the field for all the user's chats — the
        choice of 16.11.2026 between that and a project is gone; and only ChatGPT was seen reading the rules
        this way."""
        self.assertIn("Your assistant lives in all your chats: the text goes into your AI's field for standing "
                      "instructions, and in each new chat your AI looks at your Notion before its first answer.", FLAT)
        self.assertNotIn("project", FLAT, "no platform's text goes into a project (owner, 20.11 and 23.11.2026)")
        self.assertIn("On Grok and Claude this way of reading the rules has not been tried yet; the Issue says so.", FLAT)
        # Owner, 17.11.2026 (the first post in X): ChatGPT Plus is the first path, Claude and Grok in beta.
        # ChatGPT keeps its «beta» too (Tolik on PR #63): the first path is still a test.
        self.assertIn("ChatGPT (the Plus plan) — the first path, in beta; Claude (the Pro plan) or Grok (the Plus plan) — in beta too.", FLAT)
        self.assertIn("does not fit the free ChatGPT plan: its Custom Instructions box holds 1 500 characters", FLAT)
        self.assertIn("and check its facts when it helps you with those", FLAT,
                      "the fact check is read with a topic's rules now, not held in every answer (DESIGN_PHASE3 §4)")
        self.assertEqual([name for name, row in setup.platforms().items() if row.get("field_all")], [])
        for name in ("grok", "claude"):
            self.assertEqual(setup.platforms()[name]["status"], "beta", name)

    def test_it_says_how_she_hears_of_new_instructions_and_that_the_choice_is_hers(self):
        """Said at installation, not asked in the form (DESIGN_PHASE3 §5): GitHub's e-mail or its app."""
        self.assertIn("How GitHub tells you is yours to choose: by e-mail — in GitHub's notification settings — or on "
                      "your phone — in the GitHub app: Profile, the gear, Notifications, «Direct mentions».", FLAT)
        self.assertIn("With neither, the page still waits in your copy.", FLAT)
        self.assertIn("by e-mail — in GitHub's notification settings — or on your phone — in the GitHub app: Profile, "
                      "the gear, Notifications, «Direct mentions». With neither, the page still waits in your copy.",
                      setup.LATER, "setup's Issue names the same two ways")

    def test_every_language_of_it_names_what_the_screen_shows(self):
        """English, Russian and Hebrew (owner, 13.11.2026). GitHub's pages and the form are English, so
        each language names the buttons, the form's fields, the setting and the Issue as they appear."""
        russian, hebrew = "\u0420\u0443\u0441\u0441\u043a\u0438\u0439", "\u05e2\u05d1\u05e8\u05d9\u05ea"          # the two headings, in their own letters
        parts = re.split(rf"^## ({russian}|{hebrew})$", README, flags=re.M)
        self.assertEqual(parts[1::2], [russian, hebrew], "the three languages, in this order")
        sections = {"English": parts[0], "Russian": parts[2], "Hebrew": parts[4]}
        self.assertIn(f"**English** · [{russian}](#{russian.lower()}) · [{hebrew}](#{hebrew})", parts[0])
        form = (ROOT / "engine" / "workflows" / "setup.yml").read_text(encoding="utf-8")
        labels = [re.split(r",| —", d, maxsplit=1)[0].strip() for d in re.findall(r"description: (.+)", form)]
        setting = re.search(r"«([^»]+)»", setup_run.SETTING).group(1)
        for name, text in sections.items():
            flat = " ".join(text.split())
            for label in labels:
                self.assertIn(f"**{label}**", flat, name)
            for shown in ("«Use this template»", "«Create a new repository»", "**Private**",
                          "«Create repository from template»", "**Settings**", "**Actions**", "**General**",
                          "«Workflow permissions»", f"**«{setting}»**", "**Save**", "**setup**", "**Run workflow**",
                          f"**«{setup_run.ISSUE_TITLE}»**", "«Direct mentions»", "`my-memory`", "`en,he`"):
                self.assertIn(shown, flat, f"{name}: {shown}")
            for code in ("`ru`", "`IL`", "`Asia/Jerusalem`"):
                self.assertIn(code, flat, name)
            titles = [row["title"] for row in setup.platforms().values() if row.get("status") in setup.INSTALLABLE]
            for title in titles:
                self.assertIn(title, flat, name)
            self.assertEqual(flat.count("**Run workflow**"), 5,
                             f"{name}: the step, the button, the way to start again, and twice for the user's settings")
            self.assertIn("**settings**", flat, name)
            self.assertIn("«keep as it is»", flat, name)
        self.assertIn('<div dir="rtl">', sections["Hebrew"], "Hebrew reads from the right")
        self.assertTrue(README.rstrip().endswith("</div>"))
        self.assertNotRegex(README, r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069]", "no invisible direction marks")

    def test_it_says_her_settings_change_by_a_form_where_everything_stands_on_keep(self):
        """DESIGN_PHASE3 §6, `engine.setup.setup_run.change`: the form `settings`, a new Issue in minutes."""
        self.assertIn("To change your settings — your AI, language, country, time zone, name, the grammar or your work "
                      "languages: **Actions** → **settings** → **Run workflow**. Every answer there already stands on "
                      "«keep as it is» — the lists on their first item, the text fields empty: change what you need, "
                      "all of it in one run, and press **Run workflow**.", FLAT)
        self.assertEqual(setup_run.KEEP, "keep as it is")
        self.assertIn(f"Usually within a few minutes — at the latest after the night's check — a new Issue "
                      f"**«{setup_run.ISSUE_TITLE}»** brings one message to send to your AI — you paste nothing again, "
                      "unless it is your AI itself you changed: then the Issue has the steps for the new one. Answers "
                      "that are your settings already bring no new Issue, only a note on the one you have. You edit no "
                      "file.", FLAT,
                      "no more is promised than the run does: «waiting» and «nothing to change» are green runs too")
        self.assertIn("The run's own page under **Actions** says what it did; if no Issue has come by the next day, "
                      "look there, then run it again.", FLAT, "the job summary: setup_run.tell")

    def test_it_names_the_setting_setup_needs_as_setup_names_it(self):
        label = re.search(r"«([^»]+)»", setup_run.SETTING).group(1)
        self.assertIn(f"**«{label}»**", FLAT)
        for part in ("**Settings**", "**Actions**", "**General**", "«Workflow permissions»", "**Save**"):
            self.assertIn(part, FLAT)

    def test_it_names_the_workflow_and_the_issue_setup_opens(self):
        self.assertIn("**Actions** → **setup** → **Run workflow**", FLAT)
        self.assertIn(f"**«{setup_run.ISSUE_TITLE}»**", FLAT)
        self.assertIn("«Use this template»", FLAT)
        self.assertIn("**Private**", FLAT)
        self.assertIn("not an organisation", FLAT)

    def test_it_says_add_nothing_before_setup(self):
        self.assertIn("Add nothing to it before step 3: the first setup runs only on a fresh copy", FLAT)
        self.assertIn("start it again with **Run workflow**", FLAT, "a re-run of an old run is refused")
        self.assertIn("«Create repository from template»", FLAT)

    def test_the_plans_it_names_are_the_platform_table_s(self):
        table = setup.platforms()
        installable = {n: r for n, r in table.items() if r.get("status") in setup.INSTALLABLE}
        line = next(line for line in README.splitlines() if line.startswith("- one of:"))
        for name, row in installable.items():
            self.assertIn(row["title"], line)
            if row["plan"] != "any":
                self.assertIn(f"{row['title']} (the {row['plan']} plan", line)
        for name, row in table.items():
            if name not in installable:
                self.assertNotIn(row["title"], FLAT)

    def test_its_english_part_is_english_and_no_language_of_it_names_the_factory(self):
        """Three languages since the owner's decision of 13.11.2026 (DESIGN_PHASE3 §5); the English part
        stays English but for the one line that leads to the other two."""
        english = README.split("\n---\n", 1)[0]
        english = "\n".join(line for line in english.splitlines() if not line.startswith("**English** ·"))
        self.assertIsNone(re.search(r"[\u0400-\u04ff\u0590-\u05ff]", english), "no Cyrillic, no Hebrew")
        self.assertEqual(README.count("\n---\n"), 2, "English, Russian, Hebrew")
        for word in ("DESIGN", "engine/", "C1", "N1", "Claude Code", "Garik98129"):
            self.assertNotIn(word, FLAT)


if __name__ == "__main__":
    unittest.main()

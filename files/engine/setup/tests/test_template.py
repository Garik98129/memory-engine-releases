"""The public template a person makes her repository from (DESIGN_PHASE3 §5, step 1), on
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
from engine.setup.tests.test_release import TEMPLATE

ROOT = Path(__file__).resolve().parents[3]
README = (ROOT / "docs" / "template" / "README.md").read_text(encoding="utf-8")
FLAT = " ".join(README.split())           # as she reads it: the lines' breaks are the file's, not the text's
RELEASES = "Garik98129/memory-engine-releases"
PUBLISHED = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
FACTORY = {**TEMPLATE, "LICENSE": b"MIT License\n", "docs/template/README.md": b"# Your AI memory\n"}


class Template(unittest.TestCase):
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

    def publish(self, tag: str, changes: dict[str, bytes | None] | None = None) -> str:
        if changes:
            self.factory.commit(changes)
        source = _git(self.factory.path, "rev-parse", "main")
        release.cut(release.release_files(self.factory.path, source), tag, source, PUBLISHED,
                    self.releases.path, RELEASES)
        self.releases.commit({})
        _git(self.releases.path, "tag", tag)
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
        self.assertNotIn("docs/DESIGN.md", tree, "the design record is the factory's, not hers")

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
        self.publish("v0.1.0")
        source = self.publish("v0.1.1", {"engine/checks/run.py": None, "engine/rules/shopping.md": b"Shopping.\n",
                                         "engine/workflows/watch.yml": None})
        self.assertEqual(self.run_cli("v0.1.0", _git(self.factory.path, "rev-parse", "main~1"))[0], 0)
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
        """A release changes her copy, not the field she pasted: the watch hands her the new text in
        an Issue (DESIGN_PHASE3 §6, `engine.checks.field_issue`) and she pastes it — the README says
        that much, and not that her AI is brought up to date for her (Kimi, on #54)."""
        self.assertNotIn("keeps your assistant's rules up to date", FLAT)
        self.assertIn("Your AI keeps the instructions you pasted until you paste new ones", FLAT)
        self.assertIn(f"an Issue **«{field_issue.TITLE}»** with the new text", FLAT)
        self.assertIn("until you do, your AI goes on with the rules of the release it has", FLAT)
        self.assertNotIn("nothing tells you yet", FLAT)

    def test_it_says_how_she_hears_of_new_instructions_and_that_the_choice_is_hers(self):
        """Said at installation, not asked in the form (DESIGN_PHASE3 §5): GitHub's e-mail or its app."""
        self.assertIn("How GitHub tells you is yours to choose: by e-mail — in GitHub's notification settings — or on "
                      "your phone — in the GitHub app: Profile, the gear, Notifications, «Direct mentions».", FLAT)
        self.assertIn("With neither, the page still waits in your copy.", FLAT)
        self.assertIn("How GitHub tells you is yours to choose", setup.LATER, "setup's Issue says the same")

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
                             f"{name}: the step, the button, the way to start again, and twice for her settings")
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
                      f"**«{setup_run.ISSUE_TITLE}»** brings the text to paste; answers that are your settings already "
                      "bring no new Issue, only a note on the one you have. You edit no file.", FLAT,
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

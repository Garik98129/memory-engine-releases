import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

from engine.checks import lock_check, numbers_check, paths_check
from engine.checks.paths_check import PERSON
from engine.checks.tests.helpers import DEFAULTS, VALID_CONFIG, Repo, _git
from engine.setup import release

PUBLISHED = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[3]

TEMPLATE = {
    "README.md": b"The template.\n",
    "docs/DESIGN.md": b"Design.\n",
    ".github/workflows/factory.yml": b"name: factory\n",
    "engine/defaults.toml": DEFAULTS,
    "engine/rules/core.md": b"Core rules.\n",
    "engine/rules/fields/chat.md": b"Chat safety rules.\n",
    "engine/rules/fields/bot.md": b"Bot safety rules.\n",
    "engine/checks/run.py": b"# the gate\n",
    "engine/workflows/check.yml": b"name: check\n",
    "engine/workflows/watch.yml": b"name: watch\n",
}

PERSON_PART = {
    "README.md": b"A person's memory.\n",
    "user/config.toml": VALID_CONFIG,
    "user/fields/chat.md": b"Who: a test person.\n",
    "user/fields/bot.md": b"Who: a test person, for the bot.\n",
}


def messages(findings):
    return [f.message for f in findings]


class ReleaseFiles(unittest.TestCase):
    def setUp(self):
        self.template = Repo(TEMPLATE)

    def tearDown(self):
        self.template.close()

    def test_the_release_is_the_engine_plus_its_workflows_where_github_runs_them(self):
        files = release.release_files(self.template.path, self.template.base)
        expected = {p for p in TEMPLATE if p.startswith("engine/")} | {
            ".github/workflows/check.yml", ".github/workflows/watch.yml"}
        self.assertEqual(set(files), expected)
        self.assertEqual(files[".github/workflows/check.yml"], TEMPLATE["engine/workflows/check.yml"])

    def test_the_factory_ci_and_the_docs_never_ship(self):
        files = release.release_files(self.template.path, self.template.base)
        self.assertNotIn(".github/workflows/factory.yml", files)
        self.assertNotIn("docs/DESIGN.md", files)
        self.assertNotIn("README.md", files)

    def test_an_uncommitted_or_untracked_file_does_not_ship(self):
        self.template.write("engine/rules/core.md", b"Edited, not committed.\n")
        self.template.write("engine/rules/stray.md", b"Never added.\n")
        files = release.release_files(self.template.path, self.template.base)
        self.assertEqual(files["engine/rules/core.md"], b"Core rules.\n")
        self.assertNotIn("engine/rules/stray.md", files)

    def test_an_executable_file_is_refused_by_name(self):
        rev = self.template.chmod_exec("engine/checks/run.py")
        with self.assertRaisesRegex(ValueError, r"engine/checks/run\.py has mode 100755"):
            release.release_files(self.template.path, rev)

    def test_a_symlink_is_refused(self):
        rev = self.template.symlink("engine/rules/link.md", "core.md")
        with self.assertRaisesRegex(ValueError, r"engine/rules/link\.md has mode 120000"):
            release.release_files(self.template.path, rev)

    def test_a_workflow_in_a_subfolder_is_refused(self):
        rev = self.template.commit({"engine/workflows/more/extra.yml": b"name: extra\n"})
        with self.assertRaisesRegex(ValueError, "subfolder"):
            release.release_files(self.template.path, rev)

    def test_a_commit_without_the_engine_is_refused(self):
        empty = Repo({"README.md": b"x\n"})
        try:
            with self.assertRaisesRegex(ValueError, "no engine/"):
                release.release_files(empty.path, empty.base)
        finally:
            empty.close()


class Manifest(unittest.TestCase):
    def setUp(self):
        self.template = Repo(TEMPLATE)
        self.files = release.release_files(self.template.path, self.template.base)

    def tearDown(self):
        self.template.close()

    def test_the_manifest_is_what_c1_reads(self):
        data = release.manifest(self.files, "v0.1.0", self.template.base, PUBLISHED)
        path = self.template.path.parent / f"{self.template.path.name}-m.json"
        path.write_text(release.dumps(data), encoding="utf-8")
        loaded = lock_check.load_manifest(path)
        self.assertEqual(loaded["release"], "v0.1.0")
        self.assertEqual(loaded["source"], self.template.base)
        self.assertEqual(loaded["published"], "2026-10-24T20:00:00Z")
        self.assertEqual(loaded["files"]["engine/rules/core.md"], hashlib.sha256(b"Core rules.\n").hexdigest())
        self.assertEqual(set(loaded["files"]), set(self.files))

    def test_the_same_commit_gives_the_same_bytes(self):
        one = release.dumps(release.manifest(self.files, "v0.1.0", "abc", PUBLISHED))
        two = release.dumps(release.manifest(dict(reversed(list(self.files.items()))), "v0.1.0", "abc", PUBLISHED))
        self.assertEqual(one, two)

    def test_a_tag_that_is_not_vmajor_minor_patch_is_refused(self):
        for bad in ("0.1.0", "v0.1", "v0.1.0-rc1", "v1/../../x", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                release.manifest(self.files, bad, "abc", PUBLISHED)

    def test_a_publish_time_without_its_offset_is_refused(self):
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            release.manifest(self.files, "v0.1.0", "abc", datetime(2026, 10, 24, 20, 0))

    def test_a_local_publish_time_is_written_in_utc(self):
        local = datetime.fromisoformat("2026-10-24T23:00:00+03:00")
        self.assertEqual(release.manifest(self.files, "v0.1.0", "abc", local)["published"], "2026-10-24T20:00:00Z")


class Install(unittest.TestCase):
    def setUp(self):
        self.template = Repo(TEMPLATE)
        self.files = release.release_files(self.template.path, self.template.base)
        self.person = Repo(PERSON_PART)

    def tearDown(self):
        self.template.close()
        self.person.close()

    def test_an_installed_repository_passes_c1_and_c2s_tree_rules(self):
        written = release.install(self.files, "v0.1.0", self.person.path)
        self.assertIn("user/engine.lock", written)
        main = self.person.commit({})
        manifest = release.manifest(self.files, "v0.1.0", self.template.base, PUBLISHED)
        self.assertEqual(lock_check.check(self.person.path, main, main, PERSON, manifest), [])
        self.assertEqual(paths_check.tree_findings(self.person.path, main), [])
        self.assertEqual(lock_check.lock_release(self.person.path, main), "v0.1.0")

    def test_one_changed_byte_after_installation_is_red(self):
        release.install(self.files, "v0.1.0", self.person.path)
        main = self.person.commit({})
        manifest = release.manifest(self.files, "v0.1.0", self.template.base, PUBLISHED)
        head = self.person.commit({".github/workflows/check.yml": b"name: check \n"})
        self.assertIn(".github/workflows/check.yml differs from release v0.1.0",
                      messages(lock_check.check(self.person.path, main, head, PERSON, manifest)))

    def test_the_persons_part_is_left_as_it_was(self):
        release.install(self.files, "v0.1.0", self.person.path)
        for path, content in PERSON_PART.items():
            self.assertEqual((self.person.path / path).read_bytes(), content)

    def test_a_second_installation_is_refused_and_writes_nothing(self):
        release.install(self.files, "v0.1.0", self.person.path)
        self.person.commit({})
        (self.person.path / "engine/rules/core.md").write_bytes(b"Local.\n")
        with self.assertRaisesRegex(ValueError, "exists"):
            release.install(self.files, "v0.1.0", self.person.path)
        self.assertEqual((self.person.path / "engine/rules/core.md").read_bytes(), b"Local.\n")

    def test_an_existing_github_folder_or_lock_is_refused(self):
        for taken in (".github/dependabot.yml", "user/engine.lock"):
            with self.subTest(taken=taken), tempfile.TemporaryDirectory() as d:
                (Path(d) / taken).parent.mkdir(parents=True, exist_ok=True)
                (Path(d) / taken).write_bytes(b"x\n")
                with self.assertRaisesRegex(ValueError, "exists"):
                    release.install(self.files, "v0.1.0", Path(d))
                self.assertFalse((Path(d) / "engine").exists())

    def test_a_bad_tag_installs_nothing(self):
        with self.assertRaises(ValueError):
            release.install(self.files, "latest", self.person.path)
        self.assertFalse((self.person.path / "engine").exists())


class Cut(unittest.TestCase):
    """The release as the public repository of releases holds it: the manifest and the
    files, so an update can fetch the engine without a token."""

    REPOSITORY = "Garik98129/memory-engine-releases"

    def setUp(self):
        self.template = Repo(TEMPLATE)
        self.files = release.release_files(self.template.path, self.template.base)
        self.releases = Repo({"README.md": b"Release manifests.\n"})
        _git(self.releases.path, "remote", "add", "origin", f"https://github.com/{self.REPOSITORY}")

    def tearDown(self):
        self.template.close()
        self.releases.close()

    def cut(self, tag="v0.1.6", files=None, into=None):
        return release.cut(files or self.files, tag, self.template.base, PUBLISHED, into or self.releases.path,
                           self.REPOSITORY)

    def test_the_files_are_byte_equal_to_the_manifest(self):
        written = self.cut()
        self.assertEqual(written[-1], "release-manifest.json")
        data = json.loads((self.releases.path / "release-manifest.json").read_text(encoding="utf-8"))
        on_disk = {str(p.relative_to(self.releases.path / "files")): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in (self.releases.path / "files").rglob("*") if p.is_file()}
        self.assertEqual(on_disk, data["files"])
        self.assertEqual(data["release"], "v0.1.6")
        self.assertFalse((self.releases.path / ".files.new").exists())

    def test_workflows_sit_under_files_where_github_never_runs_them(self):
        self.cut()
        self.assertTrue((self.releases.path / "files/.github/workflows/check.yml").is_file())
        self.assertFalse((self.releases.path / ".github").exists())

    def test_a_file_the_release_dropped_is_gone(self):
        stale = self.releases.path / "files/engine/old.py"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"# dropped\n")
        self.cut()
        self.assertFalse(stale.exists())

    def keep(self) -> Path:
        kept = self.releases.path / "files/engine/kept.py"
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(b"# kept\n")
        return kept

    def assert_nothing_written(self, kept: Path):
        self.assertTrue(kept.exists())
        self.assertFalse((self.releases.path / "release-manifest.json").exists())

    def test_a_bad_tag_writes_nothing(self):
        kept = self.keep()
        for tag in ("v0.1", "v0.01.6"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.cut(tag)
        self.assert_nothing_written(kept)

    def test_a_folder_that_is_not_a_checkout_of_the_releases_repository_writes_nothing(self):
        kept = self.keep()
        _git(self.releases.path, "remote", "set-url", "origin", "https://github.com/someone/other-repo")
        with self.assertRaisesRegex(ValueError, "is not a checkout of Garik98129/memory-engine-releases"):
            self.cut()
        self.assert_nothing_written(kept)
        plain = self.releases.path.parent / f"{self.releases.path.name}-plain"
        plain.mkdir()
        with self.assertRaisesRegex(ValueError, "is not a checkout"):
            self.cut(into=plain)
        self.assertEqual(list(plain.iterdir()), [])

    def test_a_manifest_or_files_that_link_out_of_the_checkout_are_refused(self):
        outside = self.releases.path.parent / f"{self.releases.path.name}-outside.json"
        outside.write_text("untouched\n", encoding="utf-8")
        (self.releases.path / "release-manifest.json").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "is a link"):
            self.cut()
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched\n")

    def test_a_number_an_address_or_a_token_that_is_not_a_test_value_stops_the_publication(self):
        kept = self.keep()
        # Built from parts, so that this file itself holds none of them whole.
        card = next(c for c in ("455673758689985" + d for d in "0123456789") if numbers_check._luhn(c))
        for text, kind in (("card " + " ".join(card[i:i + 4] for i in range(0, 16, 4)), "a card number"),
                           ("write to someone" + "@" + "gmail.com", "an e-mail address"),
                           ("token " + "ghp" + "_" + "abcdefghijklmnopqrstuvwxyz0123", "what looks like a token")):
            text = (text + "\n").encode()
            with self.subTest(kind=kind):
                files = {**self.files, "engine/rules/extra.md": text}
                with self.assertRaisesRegex(ValueError, f"engine/rules/extra.md line 1: {kind}"):
                    self.cut(files=files)
        self.assert_nothing_written(kept)
        self.cut(files={**self.files, "engine/rules/extra.md": b"card 4111 1111 1111 1111, t@example.invalid\n"})

    def test_a_national_number_that_is_not_a_test_value_stops_the_publication(self):
        kept = self.keep()
        # Built from parts, so that this file itself holds none of them whole.
        ssn = "-".join(("123", "45", "6788"))
        idnr = next(c for c in ("1234567892" + d for d in "0123456789") if numbers_check._de_idnr_ok(c))
        for text, kind in (("SSN " + ssn, "a US Social Security number"), ("IdNr " + idnr, "a German tax ID")):
            with self.subTest(kind=kind):
                files = {**self.files, "engine/rules/extra.md": (text + "\n").encode()}
                with self.assertRaisesRegex(ValueError, f"engine/rules/extra.md line 1: {kind}"):
                    self.cut(files=files)
        self.assert_nothing_written(kept)

    def test_a_file_that_cannot_be_checked_stops_the_publication(self):
        # A file that is not UTF-8 was passed over unchecked.
        kept = self.keep()
        with self.assertRaisesRegex(ValueError, "engine/rules/latin1.md: not valid UTF-8, so it cannot be checked"):
            self.cut(files={**self.files, "engine/rules/latin1.md": "addressed to caf\u00e9 ".encode("latin-1")})
        self.assert_nothing_written(kept)

    def test_a_write_that_fails_half_way_leaves_the_old_release(self):
        kept = self.keep()
        real = Path.write_bytes
        calls = []

        def failing(path, data):
            calls.append(path)
            if len(calls) == 3:
                raise OSError(28, "No space left on device")
            return real(path, data)

        with mock.patch.object(Path, "write_bytes", failing), self.assertRaises(OSError):
            self.cut()
        self.assert_nothing_written(kept)
        self.assertFalse((self.releases.path / ".files.new").exists())

    def test_the_committed_engine_has_nothing_that_may_not_go_public(self):
        head = _git(ROOT, "rev-parse", "HEAD")
        self.assertEqual(release.publication_findings(release.release_files(ROOT, head)), [])


class ChatLink(unittest.TestCase):
    """The chat field links the public rules at one release; the cut holds that link to the
    release's own files: the chat never reads rules the person's release
    does not have."""

    REPOSITORY = "Garik98129/memory-engine-releases"
    BASE = "https://raw.githubusercontent.com/Garik98129/memory-engine-releases/{tag}/files/engine/rules/"

    def setUp(self):
        self.template = Repo({**TEMPLATE, "engine/rules/health.md": b"# Health\n",
                              "engine/rules/shopping.md": b"# Shopping\n"})
        self.releases = Repo({"README.md": b"Release manifests.\n"})
        _git(self.releases.path, "remote", "add", "origin", f"https://github.com/{self.REPOSITORY}")

    def tearDown(self):
        self.template.close()
        self.releases.close()

    def files(self, line: str) -> dict[str, bytes]:
        head = self.template.commit({"engine/rules/fields/chat.md": f"Rules.\n{line}\nMore.\n".encode()})
        return release.release_files(self.template.path, head)

    def link(self, tag: str, names: str = "health.md, shopping.md") -> str:
        return f"Topic rules, read before answering — {names}: {self.BASE.format(tag=tag)}"

    def publish(self, tag: str, health: bytes = b"# Health\n"):
        """A release already in the public repository: its files under files/, tagged."""
        self.releases.commit({"files/engine/rules/health.md": health,
                              "files/engine/rules/shopping.md": b"# Shopping\n"})
        _git(self.releases.path, "tag", tag)

    def cut(self, files, tag="v0.1.9"):
        return release.cut(files, tag, self.template.base, PUBLISHED, self.releases.path, self.REPOSITORY)

    def refused(self, files, tag="v0.1.9") -> str:
        with self.assertRaises(ValueError) as caught:
            self.cut(files, tag)
        self.assertFalse((self.releases.path / "release-manifest.json").exists(), "a refused cut wrote its manifest")
        self.assertFalse((self.releases.path / "files/engine/rules/fields/chat.md").exists(), "a refused cut wrote files")
        return str(caught.exception)

    def test_the_link_is_read_with_the_files_its_line_names(self):
        self.assertEqual(release.chat_rules(self.files(self.link("v0.1.9"))),
                         (self.REPOSITORY, "v0.1.9", ["health.md", "shopping.md"]))
        self.assertIsNone(release.chat_rules(self.files("No link here.")))

    def test_a_link_to_the_release_being_cut_is_right(self):
        self.assertEqual(self.cut(self.files(self.link("v0.1.9")))[-1], "release-manifest.json")

    def test_a_link_to_an_older_release_with_the_same_files_is_right(self):
        self.publish("v0.1.8")
        self.assertEqual(self.cut(self.files(self.link("v0.1.8")))[-1], "release-manifest.json")

    def test_a_file_changed_since_the_linked_release_is_refused(self):
        self.publish("v0.1.8", health=b"# Health, older\n")
        message = self.refused(self.files(self.link("v0.1.8")))
        self.assertIn("engine/rules/health.md changed since v0.1.8", message)
        self.assertIn("link v0.1.9", message)

    def test_a_linked_tag_missing_from_the_checkout_is_refused_not_passed(self):
        self.assertIn("is not in", self.refused(self.files(self.link("v0.1.8"))))

    def test_a_link_to_a_newer_release_is_refused(self):
        self.assertIn("newer than v0.1.9", self.refused(self.files(self.link("v0.2.0"))))

    def test_a_link_to_another_repository_is_refused(self):
        line = self.link("v0.1.9").replace("Garik98129/memory-engine-releases", "someone/rules")
        self.assertIn("links someone/rules", self.refused(self.files(line)))

    def test_a_link_that_is_not_a_release_tag_is_refused(self):
        self.assertIn("not a release tag", self.refused(self.files(self.link("main"))))

    def test_a_named_file_the_release_lacks_is_refused(self):
        self.assertIn("names travel.md", self.refused(self.files(self.link("v0.1.9", "health.md, travel.md"))))

    def test_a_link_naming_no_file_is_refused(self):
        self.assertIn("names no rules file", self.refused(self.files(self.link("v0.1.9", "the topic files"))))

    def test_the_tag_form_with_refs_tags_is_read_too(self):
        line = self.link("v0.1.9").replace("/v0.1.9/", "/refs/tags/v0.1.9/")
        self.assertEqual(release.chat_rules(self.files(line))[1], "v0.1.9")

    def test_a_link_in_another_form_is_refused_not_skipped(self):
        for line in (self.link("v0.1.8")[:-1],                                  # no trailing slash
                     self.link("v0.1.8").replace("https://", "http://"),
                     "Topic rules: health.md at https://github.com/Garik98129/memory-engine-releases/tree/v0.1.8/files/engine/rules"):
            with self.subTest(line=line):
                self.assertIn("not as https://raw.githubusercontent.com", self.refused(self.files(line)))

    def test_a_broken_mention_beside_a_readable_link_is_refused_in_either_order(self):
        good = self.link("v0.1.9")
        broken = "Also: " + self.BASE.format(tag="v0.1.9")[:-1]          # the same folder, no trailing slash
        for line in (good + "\n" + broken, broken + "\n" + good):
            with self.subTest(order=line.split(":")[0]):
                self.assertIn("not as https://raw.githubusercontent.com", self.refused(self.files(line)))

    def test_a_capitalised_file_name_is_a_finding_not_ignored(self):
        self.assertIn("names Health.md", self.refused(self.files(self.link("v0.1.9", "Health.md, shopping.md"))))

    def test_two_links_are_refused(self):
        line = self.link("v0.1.9") + " and " + self.BASE.format(tag="v0.1.8")
        with self.assertRaises(ValueError):
            release.chat_rules(self.files(line))


class Cli(unittest.TestCase):
    def setUp(self):
        self.template = Repo(TEMPLATE)

    def tearDown(self):
        self.template.close()

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = release.main(list(args), now=PUBLISHED)
        return code, out.getvalue(), err.getvalue()

    def test_manifest_prints_json_for_the_named_commit(self):
        later = self.template.commit({"engine/rules/core.md": b"Changed later.\n"})
        code, out, _ = self.call("manifest", "--repo", str(self.template.path),
                                 "--rev", self.template.base, "--tag", "v0.1.0")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["source"], self.template.base)
        self.assertNotEqual(data["source"], later)
        self.assertEqual(data["files"]["engine/rules/core.md"], hashlib.sha256(b"Core rules.\n").hexdigest())

    def test_a_branch_name_is_resolved_to_its_commit(self):
        code, out, _ = self.call("manifest", "--repo", str(self.template.path), "--rev", "main", "--tag", "v0.1.0")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["source"], self.template.base)

    def test_install_lists_what_it_wrote(self):
        person = Repo(PERSON_PART)
        try:
            code, out, _ = self.call("install", "--repo", str(self.template.path), "--rev", "main",
                                     "--tag", "v0.1.0", "--into", str(person.path))
            self.assertEqual(code, 0)
            self.assertIn(".github/workflows/check.yml", out.splitlines())
            self.assertEqual(out.splitlines()[-1], "user/engine.lock")
        finally:
            person.close()

    def test_cut_lists_what_it_wrote(self):
        releases = Repo({"README.md": b"x\n"})
        _git(releases.path, "remote", "add", "origin", "https://github.com/Garik98129/memory-engine-releases.git")
        try:
            code, out, _ = self.call("cut", "--repo", str(self.template.path), "--rev", "main",
                                     "--tag", "v0.1.6", "--into", str(releases.path))
            self.assertEqual(code, 0)
            self.assertIn("files/.github/workflows/check.yml", out.splitlines())
            self.assertEqual(out.splitlines()[-1], "release-manifest.json")
        finally:
            releases.close()

    def test_cut_with_a_chat_link_older_than_its_files_is_exit_two_and_writes_nothing(self):
        """Through the command the factory runs: the chat field links v0.1.8, health.md changed
        since, and v0.1.9 is cut — refused, with nothing written into the releases checkout."""
        link = "Topic rules — health.md: https://raw.githubusercontent.com/Garik98129/memory-engine-releases/v0.1.8/files/engine/rules/ + name."
        self.template.commit({"engine/rules/health.md": b"# Health, new\n",
                              "engine/rules/fields/chat.md": f"Rules.\n{link}\n".encode()})
        releases = Repo({"README.md": b"x\n", "files/engine/rules/health.md": b"# Health, old\n"})
        _git(releases.path, "tag", "v0.1.8")
        _git(releases.path, "remote", "add", "origin", "https://github.com/Garik98129/memory-engine-releases.git")
        try:
            code, out, err = self.call("cut", "--repo", str(self.template.path), "--rev", "main",
                                       "--tag", "v0.1.9", "--into", str(releases.path))
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertIn("engine/rules/health.md changed since v0.1.8", err)
            self.assertFalse((releases.path / "release-manifest.json").exists())
            self.assertEqual((releases.path / "files/engine/rules/health.md").read_bytes(), b"# Health, old\n")
        finally:
            releases.close()

    def test_an_unknown_commit_or_a_bad_tag_is_exit_two_and_prints_no_manifest(self):
        for args in (("--rev", "no-such-rev", "--tag", "v0.1.0"), ("--rev", "main", "--tag", "v0.1")):
            with self.subTest(args=args):
                code, out, err = self.call("manifest", "--repo", str(self.template.path), *args)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("ERROR", err)


class ThisTemplate(unittest.TestCase):
    """The template as committed can be released: C1 needs every shipped file regular,
    and the gate needs the public repository of releases named in the defaults."""

    def test_the_committed_template_is_releasable(self):
        head = _git(ROOT, "rev-parse", "HEAD")
        files = release.release_files(ROOT, head)
        for name in ("check.yml", "watch.yml", "backup.yml"):
            self.assertIn(f".github/workflows/{name}", files)
        self.assertIn(b'manifests = "Garik98129/memory-engine-releases"', files["engine/defaults.toml"])

    def test_the_chat_fields_link_names_the_releases_repository_and_files_it_ships(self):
        files = release.release_files(ROOT, _git(ROOT, "rev-parse", "HEAD"))
        linked_repo, tag, names = release.chat_rules(files)
        self.assertEqual(linked_repo, "Garik98129/memory-engine-releases")
        self.assertRegex(tag, release.PUBLISHED_TAG_RE)
        self.assertTrue(names)
        for name in names:
            self.assertIn(release.RULES + name, files)


if __name__ == "__main__":
    unittest.main()

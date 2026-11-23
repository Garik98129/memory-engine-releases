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
from engine.setup import release, topics

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
    # the one text for everybody: the cut holds its address, and refuses a release without it
    "engine/rules/fields/fixed.md": (
        b"Rules: the repository `grafyla/releases`; without an app, at "
        b"https://raw.githubusercontent.com/grafyla/releases/ + branch or tag + / + path. "
        b"First read `rules-index.md` at the branch `stable`.\n"),
    "engine/checks/run.py": b"# the gate\n",
    "engine/workflows/check.yml": b"name: check\n",
    "engine/workflows/watch.yml": b"name: watch\n",
}



def frozen(tree: dict, version: int = 1) -> bytes:
    """The record of a tree's shipped workflow files, as engine/frozen.toml keeps it."""
    rows = "".join(f'"{path[len(release.WORKFLOWS):]}" = "{hashlib.sha256(content).hexdigest()}"\n'
                   for path, content in sorted(tree.items()) if path.startswith(release.WORKFLOWS) and content is not None)
    return f"[workflows]\nversion = {version}\n\n[workflows.sha256]\n{rows}".encode()


TEMPLATE[release.FROZEN] = frozen(TEMPLATE)


def topics_of(*names: str) -> dict[str, bytes]:
    """The topics of a tree whose rules files are `names`, and the table people read beside them,
    as `release cut` asks for both: one topic a file, read «when <its name>»."""
    source = 'about = "A topic applies when the talk is about the user."\nmin_engine = "v0.0.0"\nbot_only = []\n\n[cites]\n'
    table = "# Rules\n\n| File | Read by | When |\n|---|---|---|\n"
    for name in names:
        source += f'\n[[topic]]\nname = "{name[:-3]}"\nwhen = "when {name[:-3]}"\nfiles = ["{name}"]\n'
        table += f"| `{name}` | chat | when {name[:-3]} |\n"
    return {topics.TOPICS: source.encode(), topics.README: table.encode()}


TEMPLATE.update(topics_of("core.md"))
TEMPLATE[release.STABLE] = b"# moves the branch stable\n"

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

    REPOSITORY = "grafyla/releases"

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
        with self.assertRaisesRegex(ValueError, "is not a checkout of grafyla/releases"):
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
        self.cut(files={**self.files, "engine/rules/extra.md": b"card 4111 1111 1111 1111, t@example.invalid\n",
                        **topics_of("core.md", "extra.md")})

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


class FrozenWorkflows(unittest.TestCase):
    """The workflow files are frozen (engine/frozen.toml): the cut refuses a release whose
    workflow files differ from those of the release before, unless it is told so and the
    record's version rose — every installed repository applies such a release by hand."""

    REPOSITORY = "grafyla/releases"

    def setUp(self):
        self.template = Repo(TEMPLATE)
        self.releases = Repo({"README.md": b"Release manifests.\n"})
        _git(self.releases.path, "remote", "add", "origin", f"https://github.com/{self.REPOSITORY}")
        self.addCleanup(self.template.close)
        self.addCleanup(self.releases.close)

    def cut(self, tag: str, changes: dict | None = None, told: bool = False, version: int | None = None,
            tagged: bool = True) -> list[str]:
        """The factory commits `changes` — with the record brought up to date when `version` is
        given —, cuts `tag`, and the releases repository commits and tags it."""
        if changes:
            self.template.commit(changes)
        if version is not None:
            self.template.commit({release.FROZEN: frozen(release.release_files(self.template.path, "main"), version)})
        written = release.cut(release.release_files(self.template.path, "main"), tag, self.template.base, PUBLISHED,
                              self.releases.path, self.REPOSITORY, told)
        self.releases.commit({})
        if tagged:
            _git(self.releases.path, "tag", tag)
        return written

    def refused(self, *args, **kw) -> str:
        before = _git(self.releases.path, "status", "--porcelain")
        with self.assertRaises(ValueError) as caught:
            self.cut(*args, **kw)
        self.assertEqual(_git(self.releases.path, "status", "--porcelain"), before, "a refused cut wrote something")
        return str(caught.exception)

    def test_a_release_that_leaves_the_workflow_files_alone_is_cut_as_ever(self):
        self.cut("v0.1.0")
        written = self.cut("v0.1.1", {"engine/rules/core.md": b"Core rules, v0.1.1.\n", "engine/checks/gate.py": b"# new\n"})
        self.assertIn("files/engine/frozen.toml", written)
        self.assertIn("files/engine/checks/gate.py", written)

    def test_a_changed_workflow_file_is_refused_until_the_cut_is_told_and_the_version_rises(self):
        self.cut("v0.1.0")
        change = {"engine/workflows/check.yml": b"name: check\n# a comment put right\n"}
        self.assertIn("engine/frozen.toml does not hold the workflow files of this release: check.yml",
                      self.refused("v0.1.1", change))
        message = self.refused("v0.1.1", version=1)
        self.assertIn("v0.1.1 changes the workflow files against v0.1.0: check.yml", message)
        self.assertIn("each waits for somebody to apply it by hand", message)
        self.assertIn("--changes-workflows", message)
        self.assertIn("`version` in engine/frozen.toml is 1, not above v0.1.0's 1: raise it",
                      self.refused("v0.1.1", told=True))
        self.assertEqual(self.cut("v0.1.1", told=True, version=2)[-1], "release-manifest.json")
        self.assertEqual(self.cut("v0.1.2", {"engine/rules/core.md": b"v0.1.2\n"})[-1], "release-manifest.json",
                         "the release after it is an ordinary one again")

    def test_an_added_or_a_dropped_workflow_file_is_a_change_too(self):
        self.cut("v0.1.0")
        self.assertIn("against v0.1.0: more.yml", self.refused("v0.1.1", {"engine/workflows/more.yml": b"name: more\n"},
                                                               version=2))
        self.template.commit({"engine/workflows/more.yml": None})
        self.assertIn("against v0.1.0: watch.yml", self.refused("v0.1.1", {"engine/workflows/watch.yml": None}, version=2))

    def test_the_flag_is_refused_where_nothing_differs(self):
        self.cut("v0.1.0")
        self.assertIn("--changes-workflows was given, but the workflow files of v0.1.1 are those of v0.1.0",
                      self.refused("v0.1.1", {"engine/rules/core.md": b"v0.1.1\n"}, told=True))
        fresh = Repo({"README.md": b"Release manifests.\n"})
        self.addCleanup(fresh.close)
        _git(fresh.path, "remote", "add", "origin", f"https://github.com/{self.REPOSITORY}")
        with self.assertRaises(ValueError) as caught:
            release.cut(release.release_files(self.template.path, "main"), "v0.1.0", self.template.base, PUBLISHED,
                        fresh.path, self.REPOSITORY, True)
        self.assertIn("there is no release before this one", str(caught.exception))

    def test_a_record_that_names_a_file_the_release_does_not_ship_is_refused(self):
        record = frozen(TEMPLATE).decode() + '"ghost.yml" = "' + "0" * 64 + '"\n'
        self.assertIn("does not hold the workflow files of this release: ghost.yml",
                      self.refused("v0.1.0", {release.FROZEN: record.encode()}))

    def test_the_record_of_the_release_before_is_read_or_the_cut_stops(self):
        """The version must rise above the one on record there: a record that cannot be read, or a
        version that is no whole number, is no «nothing to rise above»."""
        change = {"engine/workflows/check.yml": b"name: check, changed\n"}
        for text, said in ((b"[workflows\n", "files/engine/frozen.toml at the tag v0.1.0 cannot be read"),
                           (b"version = 1\n", "files/engine/frozen.toml at the tag v0.1.0 cannot be read"),
                           (b'[workflows]\nversion = "1"\n', "not above v0.1.0's 1: raise it"),
                           (b"[workflows]\nversion = true\n", "not above v0.1.0's True: raise it")):
            releases = Repo({"README.md": b"Release manifests.\n"})
            self.addCleanup(releases.close)
            _git(releases.path, "remote", "add", "origin", f"https://github.com/{self.REPOSITORY}")
            releases.commit({"files/.github/workflows/check.yml": b"name: check\n", "files/engine/frozen.toml": text})
            _git(releases.path, "tag", "v0.1.0")
            self.releases = releases
            self.template.commit(change)
            self.assertIn(said, self.refused("v0.1.1", told=True, version=5), text)

    def test_a_record_that_is_missing_or_wrong_is_refused(self):
        self.assertIn("engine/frozen.toml is missing", self.refused("v0.1.0", {release.FROZEN: None}))
        for text, said in ((b"[workflows]\nversion = 0\n[workflows.sha256]\n", "a whole `version` of 1 or more"),
                           (b"[workflows]\nversion = true\n[workflows.sha256]\n", "a whole `version` of 1 or more"),
                           (b"[workflows]\nversion = 1\n", "a `sha256` table"),
                           (b"version = 1\n", "needs [workflows]"), (b"[workflows\n", "cannot be read")):
            self.assertIn(said, self.refused("v0.1.0", {release.FROZEN: text}), text)

    def test_the_release_before_is_the_newest_older_tag_and_no_tag_is_not_the_first_release(self):
        self.cut("v0.1.0")
        self.cut("v0.1.1", {"engine/workflows/check.yml": b"name: check, v0.1.1\n"}, told=True, version=2)
        self.assertEqual(release.release_before(self.releases.path, "v0.1.2"), "v0.1.1")
        self.assertEqual(release.release_before(self.releases.path, "v0.1.1"), "v0.1.0")
        self.assertIsNone(release.release_before(self.releases.path, "v0.1.0"))
        _git(self.releases.path, "tag", "v0.1.10")
        for other in ("latest", "v0.1", "v0.1.99-rc1", "v01.5.0", "version-9"):       # tags, but no releases
            _git(self.releases.path, "tag", other)
        self.assertEqual(release.release_before(self.releases.path, "v0.2.0"), "v0.1.10", "by the release's number")
        for tag in ("v0.1.0", "v0.1.1", "v0.1.10"):
            _git(self.releases.path, "tag", "-d", tag)
        self.assertIn("held v0.1.1 last, and its newest tag older than v0.1.2 is none: fetch its tags",
                      self.refused("v0.1.2", {"engine/rules/core.md": b"v0.1.2\n"}))

    def test_a_checkout_whose_newest_tag_was_never_fetched_is_refused_not_believed(self):
        """The tag of a release is set on GitHub after the push that brings it: the checkout it was
        cut in lacks that tag until somebody fetches. Compared with the tag before it, a release
        that undoes the last change of the workflow files would pass as «nothing differs», and one
        that changes nothing would be refused until the flag was given."""
        self.cut("v0.1.0")
        self.cut("v0.1.1", {"engine/workflows/check.yml": b"name: check, v0.1.1\n"}, told=True, version=2, tagged=False)
        back = {"engine/workflows/check.yml": TEMPLATE["engine/workflows/check.yml"]}
        said = "held v0.1.1 last, and its newest tag older than v0.1.2 is v0.1.0: fetch its tags"
        self.assertIn(said, self.refused("v0.1.2", back, version=3))             # v0.1.0's files again: «nothing differs»
        self.assertIn(said, self.refused("v0.1.2", told=True))
        self.template.commit({"engine/workflows/check.yml": b"name: check, v0.1.1\n"})
        self.assertIn(said, self.refused("v0.1.2", version=2))                   # v0.1.1's own files: nothing to tell
        _git(self.releases.path, "tag", "v0.1.1")                                # fetched
        self.assertEqual(self.cut("v0.1.2")[-1], "release-manifest.json")

    def test_a_cut_made_again_before_its_tag_is_still_held_to_the_release_before(self):
        self.cut("v0.1.0")
        change = {"engine/workflows/check.yml": b"name: check, v0.1.1\n"}
        self.cut("v0.1.1", change, told=True, version=2, tagged=False)           # committed, and cut once more
        self.assertEqual(release.release_held(self.releases.path, "v0.1.1"), "v0.1.0")
        self.assertIn("changes the workflow files against v0.1.0", self.refused("v0.1.1"))
        self.assertEqual(self.cut("v0.1.1", told=True)[-1], "release-manifest.json")

    def test_published_files_with_no_manifest_and_no_tag_are_not_the_first_release(self):
        self.releases.commit({"files/engine/rules/core.md": b"Core rules.\n"})
        self.assertIn("holds a release but no tag older than v0.1.0: fetch its tags", self.refused("v0.1.0"))

    def test_a_manifest_in_the_history_that_cannot_be_read_stops_the_cut(self):
        self.releases.commit({"release-manifest.json": b"not json"})
        self.assertIn("release-manifest.json at", self.refused("v0.1.0"))

    def test_a_release_cut_before_the_files_were_frozen_has_no_version_to_rise_above(self):
        self.releases.commit({"files/.github/workflows/check.yml": b"name: check, as it was\n",
                              "files/engine/rules/core.md": b"Core rules.\n"})
        _git(self.releases.path, "tag", "v0.1.12")
        self.assertIn("changes the workflow files against v0.1.12: check.yml, watch.yml", self.refused("v0.1.13"))
        self.assertEqual(self.cut("v0.1.13", told=True)[-1], "release-manifest.json")

    def test_the_command_says_it_aloud(self):
        self.cut("v0.1.0")
        self.template.commit({"engine/workflows/check.yml": b"name: check, v0.1.1\n"})
        self.template.commit({release.FROZEN: frozen(release.release_files(self.template.path, "main"), 2)})
        argv = ["cut", "--repo", str(self.template.path), "--rev", "main", "--tag", "v0.1.1",
                "--into", str(self.releases.path)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(release.main(argv, now=PUBLISHED), 2)
        self.assertIn("ERROR not published: v0.1.1 changes the workflow files against v0.1.0: check.yml", err.getvalue())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(release.main([*argv, "--changes-workflows"], now=PUBLISHED), 0)
        self.assertIn("HANDS v0.1.1 changes the workflow files against v0.1.0: check.yml", err.getvalue())
        self.assertNotIn("HANDS", out.getvalue(), "the list of what was written stays a list")


class TheOneTextsAddress(unittest.TestCase):
    """`fixed.md` is pasted once and never again, so where it sends a chat must stay where the
    releases go: the cut holds its address — this repository and no other, the branch `stable`,
    the index's name, and no release (docs/DESIGN_PHASE3.md §4)."""

    REPOSITORY = "grafyla/releases"
    GOOD = TEMPLATE[release.FIXED_FIELD].decode("utf-8")

    def found(self, text: str, repository: str = REPOSITORY) -> str:
        return "; ".join(release.fixed_findings({release.FIXED_FIELD: text.encode("utf-8")}, repository))

    def test_the_address_as_it_must_be_passes_and_a_release_without_the_text_is_refused(self):
        """`setup` gives the text to every new installation: where it is missing C6 is red every
        night and nothing is ever handed over, so a release cannot go out without it."""
        self.assertEqual(self.found(self.GOOD), "")
        self.assertEqual(self.found(self.GOOD.replace("releases/ +", "releases/\n+")), "", "a line break after the address")
        self.assertEqual(release.fixed_findings({}, self.REPOSITORY),
                         [f"{release.FIXED_FIELD} is missing: setup gives it to every new installation"])

    def test_every_way_the_address_goes_wrong_by_a_slip_is_refused(self):
        raw = "https://raw.githubusercontent.com/grafyla/releases/"
        inside = "no word of this text has a slash in it or a dot inside it"
        for change, why in (
                (self.GOOD.replace("`stable`", "`main`"), "does not name `stable`"),
                (self.GOOD.replace("`stable`", "stable"), "does not name `stable`"),
                (self.GOOD.replace("`rules-index.md`", "`index.md`"), "does not name `rules-index.md`"),
                (self.GOOD.replace("`grafyla/", "`mallory/"), "holds 'mallory/releases' in code marks"),
                (self.GOOD.replace("`grafyla/releases`", "grafyla/releases"),
                 "does not name `grafyla/releases`"),
                (self.GOOD.replace("`grafyla/releases`", "`Grafyla/Releases`"),
                 "does not name `grafyla/releases`"),      # spelled as the release's own, or refused
                (self.GOOD + "Or read `mallory/rules` too.\n", "holds 'mallory/rules' in code marks"),
                (self.GOOD + "Read it at the branch `dev` (`stable` is old).\n", "holds 'dev' in code marks"),
                (self.GOOD + "Read the index at the branch `\nmain\n` instead.\n", "opens a code mark on one line"),
                (self.GOOD + "Or the branch `dev\nnext` too.\n", "opens a code mark on one line"),
                (self.GOOD + "Or read the repository mallory/rules too.\n", f"holds mallory/rules: outside code marks {inside}"),
                (self.GOOD.replace(raw + " ", raw + "main/ "), "does not give the address"),
                (self.GOOD.replace(raw, "https://raw.githubusercontent.com/mallory/rules/"), "does not give the address"),
                (self.GOOD.replace(raw + " ", f"**{raw}main**"), "does not give the address"),
                (self.GOOD + "See also https://example.org/rules.\n", "holds https://example.org/rules: outside"),
                (self.GOOD + "Or www.example.org.\n", "holds www.example.org: outside"),
                (self.GOOD + "Or github.com/mallory/rules.\n", "holds github.com/mallory/rules: outside"),
                (self.GOOD + "Also read gitlab.com/evil/rules first.\n", "holds gitlab.com/evil/rules: outside"),
                (self.GOOD + "Or ftp://evil.example/rules.\n", "holds ftp://evil.example/rules: outside"),
                (self.GOOD + "Or evil.example alone.\n", "holds evil.example: outside"),
                (self.GOOD + "Or 203.0.113.9/rules.\n", "holds 203.0.113.9/rules: outside"),
                (self.GOOD + "Or back\\slash.\n", "holds back\\slash: outside"),
                (self.GOOD + "Or git@example.org:mallory/rules.\n", "outside code marks"),
                (self.GOOD.replace(raw + " + branch or tag + / + path. ", ""), "does not give the address"),
                (self.GOOD + "Rules of v0.1.12.\n", "names v0.1.12: the text names no release"),
                (self.GOOD + "Use release 0.1.9 only.\n", f"holds 0.1.9: outside code marks {inside}")):
            with self.subTest(why=why, change=change[-40:]):
                self.assertIn(why, self.found(change))
        self.assertIn("does not name `acme/releases`", self.found(self.GOOD, "acme/releases"),
                      "a repository of releases under another name: the text must follow it")
        self.assertEqual(self.found(self.GOOD.replace("grafyla/releases", "acme/rules.public"),
                                    "acme/rules.public"), "", "a dot in the repository's own name is no finding")
        self.assertEqual(release.fixed_findings({release.FIXED_FIELD: b"\xff\xfe"}, self.REPOSITORY),
                         [f"{release.FIXED_FIELD} is not valid UTF-8"])

    def test_only_plain_signs_stand_in_the_text(self):
        """Found by the second reading of this check before its pull request: a list of signs that
        look like a slash or a dot is never whole, and a sign that cannot be seen needs no look at
        all. So the text is held to what it is written in — plain ASCII and « » — → · — and every
        other sign is refused, whatever it would have spelled."""
        self.assertEqual(self.found(self.GOOD + "A page ending with « · map» — follow it → and go on.\n"), "")
        for sign, name in (("\u29f8", "U+29F8"), ("\u2571", "U+2571"), ("\u2215", "U+2215"), ("\uff0f", "U+FF0F"),
                           ("\u0338", "U+0338"), ("\uff0e", "U+FF0E"), ("\u2024", "U+2024"), ("\u3002", "U+3002"),
                           ("\u200b", "U+200B"), ("\ufeff", "U+FEFF"), ("\u2060", "U+2060"), ("\u202e", "U+202E"),
                           ("\uff40", "U+FF40"), ("\u00a0", "U+00A0"), ("\t", "U+0009"), ("\r", "U+000D"),
                           ("\u201c", "U+201C"), ("\u2013", "U+2013")):
            with self.subTest(sign=name):
                found = self.found(self.GOOD + f"Or rules{sign}evil{sign}com here.\n")
                self.assertIn(f"holds {name}: the text is plain ASCII and the signs", found)

    def test_it_is_strict_on_purpose_says_what_to_do_and_is_never_slow(self):
        """A slash in a word is how an owner and a name are written, a dot inside one how a host
        is: «and/or», «e.g.» and «3.5» are refused too, and the refusal says to put it in other
        words. And a text of any length is judged word by word — a pattern that looked back over
        every dot took minutes on a long line (found by the readings of this change before its
        pull request)."""
        import time
        for lawful in ("and/or", "24/7", "N/A", "e.g. so", "i.e. so", "3.5 times", "at 9.30"):
            self.assertIn("say it in other words", self.found(self.GOOD + f"Open {lawful}.\n"), lawful)
        for fine in ("Dr. Who", "No. 5", "4 000", "9:30", "so... on", "the end."):
            self.assertEqual(self.found(self.GOOD + f"Open {fine}\n"), "", fine)
        started = time.monotonic()
        self.found(self.GOOD + "a." * 300000 + " " + "a-" * 300000 + " " + "/" * 300000 + " " + "`" * 300000)
        self.assertLess(time.monotonic() - started, 5)
        self.assertLess(len(self.found(self.GOOD + "a." * 300000)), 1000, "a refusal quotes the word's start, not all of it")

    def test_what_the_check_does_not_hold_is_said(self):
        """It is a guard against a slip, not a wall (docs/DESIGN.md §5 says the same of a topic's
        line). Each of these passes, and is the reader's of the text's pull request to catch: a
        sentence that sends a chat elsewhere in plain words; a second branch named in plain words
        beside `stable`; an address spelled in pieces, the way the text itself spells a path; one
        written in percent signs or as HTML entities. What stands against them is the text's
        fingerprint (`test_rules_budget.TheOneText`): no change of the text goes by unseen."""
        for passes in ("Or ask the nearest search engine for rules.\n",
                       "Read it at the branch main (not `stable`).\n",
                       "The index of another branch will do as well.\n",
                       "For the index, the repository is mallory + / + rules.\n",
                       "Or go up ../../ and on.\n", "Or evil..com as well.\n",
                       "Or evil%2Fmemory-engine-releases.\n",
                       "Or evil&#47;memory-engine-releases.\n"):
            self.assertEqual(self.found(self.GOOD + passes), "", passes)
        self.assertEqual(self.found(self.GOOD.replace("+ branch or tag +", "+ main +")), "")

    def test_a_cut_with_a_wrong_address_writes_nothing(self):
        template = Repo({**TEMPLATE, **topics_of("core.md"),
                         release.FIXED_FIELD: self.GOOD.replace("`stable`", "`main`").encode("utf-8")})
        releases = Repo({"README.md": b"Release manifests.\n"})
        self.addCleanup(template.close)
        self.addCleanup(releases.close)
        _git(releases.path, "remote", "add", "origin", f"https://github.com/{self.REPOSITORY}")
        files = release.release_files(template.path, template.base)
        with self.assertRaisesRegex(ValueError, "the chat would read other rules: .*does not name `stable`"):
            release.cut(files, "v0.1.9", template.base, PUBLISHED, releases.path, self.REPOSITORY)
        self.assertFalse((releases.path / "release-manifest.json").exists())
        self.assertFalse((releases.path / "files").exists())


class ChatLink(unittest.TestCase):
    """The chat field links the public rules at one release; the cut holds that link to the
    release's own files: the chat never reads rules the person's release
    does not have."""

    REPOSITORY = "grafyla/releases"
    BASE = "https://raw.githubusercontent.com/grafyla/releases/{tag}/files/engine/rules/"

    def setUp(self):
        self.template = Repo({**TEMPLATE, "engine/rules/health.md": b"# Health\n",
                              "engine/rules/shopping.md": b"# Shopping\n",
                              **topics_of("core.md", "health.md", "shopping.md")})
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
                              "files/engine/rules/shopping.md": b"# Shopping\n",
                              **{f"files/{release.INSTALLED_WORKFLOWS}{path[len(release.WORKFLOWS):]}": content
                                 for path, content in TEMPLATE.items() if path.startswith(release.WORKFLOWS)}})
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
        line = self.link("v0.1.9").replace("grafyla/releases", "someone/rules")
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
                     "Topic rules: health.md at https://github.com/grafyla/releases/tree/v0.1.8/files/engine/rules"):
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
        _git(releases.path, "remote", "add", "origin", "https://github.com/grafyla/releases.git")
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
        link = "Topic rules — health.md: https://raw.githubusercontent.com/grafyla/releases/v0.1.8/files/engine/rules/ + name."
        self.template.commit({"engine/rules/health.md": b"# Health, new\n",
                              "engine/rules/fields/chat.md": f"Rules.\n{link}\n".encode(),
                              **topics_of("core.md", "health.md")})
        releases = Repo({"README.md": b"x\n", "files/engine/rules/health.md": b"# Health, old\n"})
        _git(releases.path, "tag", "v0.1.8")
        _git(releases.path, "remote", "add", "origin", "https://github.com/grafyla/releases.git")
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
        self.assertIn(b'manifests = "grafyla/releases"', files["engine/defaults.toml"])

    def test_the_one_text_of_this_template_passes_the_cut(self):
        files = release.release_files(ROOT, _git(ROOT, "rev-parse", "HEAD"))
        if release.FIXED_FIELD in files:            # committed with this change; a run before the commit has none
            self.assertEqual(release.fixed_findings(files, "grafyla/releases"), [])
        text = (ROOT / release.FIXED_FIELD).read_bytes()
        self.assertEqual(release.fixed_findings({release.FIXED_FIELD: text}, "grafyla/releases"), [])

    def test_the_chat_fields_link_names_the_releases_repository_and_files_it_ships(self):
        files = release.release_files(ROOT, _git(ROOT, "rev-parse", "HEAD"))
        linked_repo, tag, names = release.chat_rules(files)
        self.assertEqual(linked_repo, "grafyla/releases")
        self.assertRegex(tag, release.PUBLISHED_TAG_RE)
        self.assertTrue(names)
        for name in names:
            self.assertIn(release.RULES + name, files)


if __name__ == "__main__":
    unittest.main()

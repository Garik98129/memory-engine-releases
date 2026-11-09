"""Scenarios: a release from the factory to a person's gate, on fixtures (F1).

Each test is a story docs/DESIGN.md §5 tells. The factory cuts a release with the same
command the owner's word starts (`engine.setup.release manifest`) and publishes it to a
stand-in of the public repository of releases; installation runs `engine.setup.release
install`; the gate runs the two calls of the workflow — `engine.checks.run --manifest-url`,
a fetch of that URL that fails like `curl -f` on anything not published, then
`engine.checks.run --manifest`. The assertions are what the gate would show on the PR.
"""
from __future__ import annotations

import contextlib
import io
import re
import unittest
from datetime import datetime, timedelta, timezone

from engine.checks import gitio, run
from engine.checks.tests.helpers import Repo, body
from engine.setup import release
from engine.setup.tests.test_release import PERSON_PART, TEMPLATE

NOW = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)
WEEK = timedelta(days=7)                    # a release reaches `stable`, the person's channel, a week after it is cut
RELEASES = "Garik98129/memory-engine-releases"
URL_RE = re.compile(r"https://raw\.githubusercontent\.com/([^/]+/[^/]+)/refs/tags/([^/]+)/release-manifest\.json")


class PublicReleases:
    """The public repository of releases: one manifest per tag, as raw.githubusercontent
    serves it. Anything not published is an error, as `curl -f` makes it."""

    def __init__(self):
        self.tags: dict[str, str] = {}
        self.fetched: list[str] = []

    def publish(self, tag: str, text: str) -> None:
        self.tags[tag] = text

    def fetch(self, url: str) -> str:
        self.fetched.append(url)
        m = URL_RE.fullmatch(url)
        if m is None or m.group(1) != RELEASES or m.group(2) not in self.tags:
            raise LookupError(f"404 {url}")
        return self.tags[m.group(2)]


def quiet(fn, *args, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fn(*args, **kw)
    return code, out.getvalue(), err.getvalue()


class World:
    def __init__(self):
        self.factory = Repo(TEMPLATE)
        self.releases = PublicReleases()
        self.person = Repo(PERSON_PART)
        self.main = self.person.base
        self.now = NOW                      # the gate's clock; the factory cuts at NOW

    def close(self):
        self.factory.close()
        self.person.close()

    def cut(self, tag: str, rev: str | None = None) -> str:
        code, out, err = quiet(release.main, ["manifest", "--repo", str(self.factory.path),
                                              "--rev", rev or "main", "--tag", tag], now=NOW)
        assert code == 0, err
        self.releases.publish(tag, out)
        return out

    def install(self, tag: str) -> None:
        code, _out, err = quiet(release.main, ["install", "--repo", str(self.factory.path), "--rev", "main",
                                               "--tag", tag, "--into", str(self.person.path)])
        assert code == 0, err
        self.main = self.person.commit({})

    def update_files(self, tag: str, rev: str = "main") -> dict[str, bytes | None]:
        """What an update PR to `tag` writes: the release over engine/ and .github/,
        files the release dropped removed, the lock moved."""
        new = release.release_files(self.factory.path, rev)
        old = [p for p in gitio.tree(self.person.path, self.main, "engine", ".github")]
        changes: dict[str, bytes | None] = {p.decode(): None for p in old if p.decode() not in new}
        changes.update(new)
        changes["user/engine.lock"] = f'release = "{tag}"\n'.encode()
        return changes

    def pr(self, changes: dict[str, bytes | None]) -> str:
        self.person.checkout(self.main)
        return self.person.commit(changes)

    def gate(self, head: str, kind: str, text: str | None = None, actor: str = "", before: str = "") -> tuple[int, str]:
        args = ["--repo", str(self.person.path), "--base", self.main, "--head", head, "--kind", kind,
                "--actor", actor, "--before", before]
        code, out, err = quiet(run.main, [*args, "--manifest-url"], now=self.now)
        if code != 0:
            return code, out + err
        try:
            manifest = self.releases.fetch(out.strip())
        except LookupError as exc:
            return 1, f"fetch failed: {exc}"     # the workflow step fails, then «Gate failed»
        path = self.person.path.parent / f"{self.person.path.name}-manifest.json"
        path.write_text(manifest, encoding="utf-8")
        code, out, err = quiet(run.main, [*args, "--manifest", str(path),
                                          "--body", str(self.person.body_file(text or ""))], now=self.now)
        return code, out + err

    def land(self, head: str) -> None:
        """Job `merge` after a green gate: main becomes the checked result."""
        self.main = head


class ReleaseScenarios(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.w.cut("v0.1.0")
        self.w.install("v0.1.0")

    def tearDown(self):
        self.w.close()

    def save(self, text: bytes = b"Who: changed.\n"):
        head = self.w.pr({"user/fields/chat.md": text})
        return self.w.gate(head, "person", body("user/fields/chat.md"))

    def test_release_day_to_the_first_save_reads_the_installed_tag_without_a_token(self):
        code, out = self.save()
        self.assertEqual(code, 0, out)
        self.assertIn("SUMMARY block=0 ran=C1,C2,C3,C4,C5,C6,C7", out)
        self.assertEqual(self.w.releases.fetched,
                         [f"https://raw.githubusercontent.com/{RELEASES}/refs/tags/v0.1.0/release-manifest.json"])

    def test_the_bot_weakening_its_own_safety_rules_is_red_against_the_public_manifest(self):
        head = self.w.pr({"engine/rules/fields/bot.md": b"No safety rules.\n"})
        code, out = self.w.gate(head, "person", body("engine/rules/fields/bot.md"))
        self.assertEqual(code, 1, out)
        self.assertIn("C1 BLOCK engine/rules/fields/bot.md differs from release v0.1.0", out)
        self.assertIn("C2 BLOCK engine/rules/fields/bot.md is outside the person area", out)

    def test_a_newer_release_waits_and_the_update_lands_by_its_own_tag(self):
        rev = self.w.factory.commit({"engine/rules/core.md": b"Core rules, v0.1.1.\n",
                                     "engine/rules/shopping.md": b"Shopping.\n"})
        self.w.cut("v0.1.1", rev)
        code, out = self.save()
        self.assertEqual(code, 0, out)                       # the person stays on v0.1.0 until the update
        head = self.w.pr(self.w.update_files("v0.1.1"))
        code, out = self.w.gate(head, "update", "Update to v0.1.1.\n")
        self.assertEqual(code, 1, out)                       # cut today: not due on `stable` yet
        self.assertIn("C1 BLOCK v0.1.1 is due on the stable channel at 2026-10-31T20:00:00Z, not yet", out)
        self.w.now = NOW + WEEK
        code, out = self.w.gate(head, "update", "Update to v0.1.1.\n")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.w.releases.fetched[-1].endswith("/refs/tags/v0.1.1/release-manifest.json"))
        self.w.land(head)
        code, out = self.save(b"Who: changed after the update.\n")
        self.assertEqual(code, 0, out)

    def test_on_canary_a_release_lands_the_day_it_is_cut(self):
        # A test repository of the owner's: the trusted contact moves it to `canary` in a
        # push of their own (C3), and from then on a release is due the moment it is cut.
        config = PERSON_PART["user/config.toml"] + b'\n[alarm]\ntrusted_contact = "helper"\n'
        self.w.land(self.w.pr({"user/config.toml": config}))               # installation writes the contact
        head = self.w.pr({"user/config.toml": config.replace(b'channel = "stable"', b'channel = "canary"')})
        code, out = self.w.gate(head, "person", body("user/config.toml"), actor="helper", before=self.w.main)
        self.assertEqual(code, 0, out)
        self.w.land(head)
        self.w.cut("v0.1.1", self.w.factory.commit({"engine/rules/core.md": b"Core rules, v0.1.1.\n"}))
        code, out = self.w.gate(self.w.pr(self.w.update_files("v0.1.1")), "update", "Update to v0.1.1.\n")
        self.assertEqual(code, 0, out)

    def test_an_update_that_leaves_out_a_new_file_is_red(self):
        self.w.factory.commit({"engine/rules/shopping.md": b"Shopping.\n"})
        self.w.cut("v0.1.1")
        changes = self.w.update_files("v0.1.1")
        del changes["engine/rules/shopping.md"]
        code, out = self.w.gate(self.w.pr(changes), "update", "Update to v0.1.1.\n")
        self.assertEqual(code, 1, out)
        self.assertIn("C1 BLOCK engine/rules/shopping.md is missing", out)

    def test_a_manifest_published_under_the_wrong_tag_is_red(self):
        self.w.factory.commit({"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
        self.w.releases.publish("v0.1.1", self.w.releases.tags["v0.1.0"])     # a mix-up at the factory
        code, out = self.w.gate(self.w.pr(self.w.update_files("v0.1.1")), "update", "Update to v0.1.1.\n")
        self.assertEqual(code, 1, out)
        self.assertIn("C1 BLOCK user/engine.lock names v0.1.1, the manifest is v0.1.0", out)

    def test_the_bot_cannot_choose_which_manifest_its_own_pr_is_held_to(self):
        rev = self.w.factory.commit({"engine/rules/core.md": b"Core rules, weakened in a release.\n"})
        self.w.cut("v0.1.1", rev)
        changes = self.w.update_files("v0.1.1")
        code, out = self.w.gate(self.w.pr(changes), "person", body(*sorted(changes)))
        self.assertEqual(code, 1, out)
        self.assertTrue(self.w.releases.fetched[-1].endswith("/refs/tags/v0.1.0/release-manifest.json"))
        self.assertIn("C1 BLOCK engine/rules/core.md differs from release v0.1.0", out)
        self.assertIn("C2 BLOCK user/engine.lock is outside the person area", out)

    def test_a_release_nobody_published_is_red_not_skipped(self):
        changes = self.w.update_files("v0.1.2")
        code, out = self.w.gate(self.w.pr(changes), "update", "Update to v0.1.2.\n")
        self.assertEqual(code, 1, out)
        self.assertIn("fetch failed: 404", out)

    def test_a_rollback_to_an_old_manifest_that_is_still_public_is_red(self):
        rev = self.w.factory.commit({"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
        self.w.cut("v0.1.1", rev)
        head = self.w.pr(self.w.update_files("v0.1.1"))
        self.w.now = NOW + WEEK
        self.assertEqual(self.w.gate(head, "update", "Update.\n")[0], 0)
        self.w.land(head)
        back = self.w.pr(self.w.update_files("v0.1.0", self.w.factory.base))
        code, out = self.w.gate(back, "update", "Back to v0.1.0.\n")
        self.assertEqual(code, 1, out)
        self.assertIn("C1 BLOCK update to v0.1.0 is older than the installed v0.1.1", out)


class GuardedSettings(unittest.TestCase):
    """The pre-installation run on the pilot's tree (25.10.2026): settings through which the
    bot could loosen a check in a PR of its own — C4's patterns in two PRs, a base out of the
    Notion copy, the account N4 counts — pass only in the trusted contact's own push."""

    def setUp(self):
        self.w = World()
        self.w.cut("v0.1.0")
        self.w.install("v0.1.0")
        self.config = PERSON_PART["user/config.toml"] + b'\n[alarm]\ntrusted_contact = "helper"\n'
        self.w.land(self.w.pr({"user/config.toml": self.config}))          # installation writes the contact

    def tearDown(self):
        self.w.close()

    def bot_pr(self, changes: dict[str, bytes]) -> tuple[int, str]:
        head = self.w.pr(changes)
        return self.w.gate(head, "person", body(*sorted(changes)), actor="alex-bot", before=self.w.main)

    def test_country_first_then_the_number_is_stopped_at_the_first_pr(self):
        moved = self.config.replace(b'country = "IL"', b'country = "US"')
        code, out = self.bot_pr({"user/config.toml": moved})
        self.assertEqual(code, 1, out)
        self.assertIn("C3 BLOCK C4 would stop looking for il_id, il_phone", out)
        code, out = self.bot_pr({"user/fields/chat.md": b"Who: ID 000000018.\n"})   # the number alone, in IL
        self.assertEqual(code, 1, out)
        self.assertIn("C4 BLOCK user/fields/chat.md line 1: an Israeli ID number", out)

    def test_she_moved_the_trusted_contact_writes_it(self):
        head = self.w.pr({"user/config.toml": self.config.replace(b'country = "IL"', b'country = "US"')})
        code, out = self.w.gate(head, "person", body("user/config.toml"), actor="helper", before=self.w.main)
        self.assertEqual(code, 0, out)
        code, out = self.w.gate(head, "person", body("user/config.toml"))              # opened or dispatched: nobody trusted
        self.assertEqual(code, 1, out)

    def test_the_bot_cannot_take_documents_out_of_the_copy(self):
        code, out = self.bot_pr({"user/config.toml": self.config + b'\n[notion]\nbackup_exclude = ["documents-base"]\n'})
        self.assertEqual(code, 1, out)
        self.assertIn("C3 BLOCK notion.backup_exclude leaves a new page out of the copy", out)

    def test_the_bot_cannot_point_the_marks_watch_elsewhere(self):
        code, out = self.bot_pr({"user/config.toml": self.config.replace(
            b'timezone = "Asia/Jerusalem"\n', b'timezone = "Asia/Jerusalem"\ngithub_login = "someone-else"\n')})
        self.assertEqual(code, 1, out)
        self.assertIn("C3 BLOCK person.github_login changes only in a push of the trusted contact's own", out)

    def test_tightening_needs_no_one(self):
        abroad = self.config.replace(b'country = "IL"', b'country = "US"')
        self.w.land(self.w.pr({"user/config.toml": abroad}))               # she lived abroad when installed
        code, out = self.bot_pr({"user/config.toml": self.config})           # back to IL: C4 looks for more
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()

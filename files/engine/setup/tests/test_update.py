"""The engine update (docs/DESIGN.md §5; step 3 of the update plan), on fixtures (F1).

The factory cuts each release with `release cut` into a checkout of the public repository
of releases and tags the commit, as the workflow `tag` does there. The person's repository
has the release installed; its GitHub is a bare repository the update pushes to and a
FakeApi for the PRs, the Issues and the dispatch. Where the update opens a PR, the gate
runs on it as `check.yml` would: `engine.checks.run --kind update` against the manifest
at the tag.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from engine.checks import gitio, run
from engine.checks.tests.fakeapi import BOT, REPO, FakeApi
from engine.checks.tests.helpers import Repo, _git
from engine.setup import release, update
from engine.setup.tests.test_release import PERSON_PART, TEMPLATE

NOW = datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc)
WEEK = timedelta(days=7)
RELEASES = "Garik98129/memory-engine-releases"
PULLS = f"repos/{REPO}/pulls"
ISSUES = f"repos/{REPO}/issues"
DISPATCH = f"repos/{REPO}/actions/workflows/check.yml/dispatches"


def quiet(fn, *args, **kw) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = fn(*args, **kw)
    return code, out.getvalue()


class World:
    """The factory, the public repository of releases, and the person's repository with its GitHub."""

    def __init__(self):
        self.factory = Repo(TEMPLATE)
        self.releases = Repo({"README.md": b"Release manifests.\n"})
        _git(self.releases.path, "remote", "add", "origin", f"https://github.com/{RELEASES}")
        self._bare = tempfile.TemporaryDirectory()
        self.github = Path(self._bare.name)
        _git(self.github, "init", "-q", "--bare")
        self.api = FakeApi()
        self.api.lists[PULLS] = []
        self.api.lists[ISSUES] = []
        self.api.head_of = self.pushed              # GitHub shows a PR at its branch's head
        self.sleeps: list[float] = []
        self.person = Repo(PERSON_PART)
        self.publish("v0.1.0", published=NOW - 4 * WEEK)
        release.install(release.release_files(self.factory.path, "main"), "v0.1.0", self.person.path)
        self.main = self.person.commit({})

    def close(self) -> None:
        for r in (self.factory, self.releases, self.person):
            r.close()
        self._bare.cleanup()

    def publish(self, tag: str, changes: dict[str, bytes | None] | None = None, published: datetime = NOW) -> None:
        """The factory commits `changes`, cuts `tag` and the workflow `tag` tags the commit."""
        if changes:
            self.factory.commit(changes)
        source = _git(self.factory.path, "rev-parse", "main")
        release.cut(release.release_files(self.factory.path, "main"), tag, source, published,
                    self.releases.path, RELEASES)
        self.releases.commit({})
        _git(self.releases.path, "tag", tag)

    def origin(self, repository: str) -> str:
        return {RELEASES: str(self.releases.path), REPO: str(self.github)}[repository]

    def run(self, now: datetime = NOW) -> tuple[int, str]:
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner", "GH_TOKEN": ""}):
            return quiet(update.main, ["--repo", str(self.person.path), "--main", self.main, "--run-url", "https://run/1"],
                         now=now, api=self.api, origin=self.origin, sleep=self.sleeps.append)

    def pushed(self, branch: str) -> str | None:
        out = subprocess.run(["git", "-C", str(self.github), "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
                             capture_output=True, text=True)
        return out.stdout.strip() or None

    def gate(self, head: str, tag: str, now: datetime) -> tuple[int, str]:
        """The gate on the update PR, as check.yml runs it: kind update, the manifest at the tag."""
        _git(self.person.path, "fetch", "-q", str(self.github), f"refs/heads/{update.BRANCH_PREFIX}{tag}")
        manifest = self.person.path.parent / f"{self.person.path.name}-manifest.json"
        manifest.write_bytes(gitio.file_at(self.releases.path, tag, release.MANIFEST_FILE))
        body = self.person.body_file(self.api.lists[PULLS][-1]["body"])
        return quiet(run.main, ["--repo", str(self.person.path), "--base", self.main, "--head", head,
                                "--kind", "update", "--manifest", str(manifest), "--body", str(body)], now=now)

    def writes(self, method: str, path: str) -> list[dict]:
        return [body for m, p, body in self.api.writes if m == method and p == path]


class Update(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.close)

    def test_nothing_newer_is_none_and_writes_nothing(self):
        code, out = self.w.run()
        self.assertEqual(code, 0, out)
        self.assertIn("NONE v0.1.0 is the newest release due on the stable channel", out)
        self.assertEqual(self.w.api.writes, [])

    def test_a_release_waits_a_week_on_stable_then_comes_as_a_pr_the_gate_lets_in(self):
        self.w.publish("v0.1.1", {"engine/rules/core.md": b"Core rules, v0.1.1.\n",
                                  "engine/rules/shopping.md": b"Shopping.\n",
                                  "engine/checks/run.py": None})
        code, out = self.w.run(NOW + WEEK - timedelta(seconds=1))
        self.assertEqual((code, self.w.api.writes), (0, []), out)
        self.assertIn("NONE v0.1.0 is the newest release due on the stable channel", out)

        code, out = self.w.run(NOW + WEEK)
        self.assertEqual(code, 0, out)
        head = self.w.pushed("engine-update/v0.1.1")
        self.assertIn(f"UPDATE v0.1.1 branch=engine-update/v0.1.1 commit={head} pr=#", out)
        repo = self.w.person.path
        self.assertEqual(gitio.git(repo, "rev-parse", f"{head}^").decode().strip(), self.w.main)
        self.assertEqual(gitio.file_at(repo, head, "engine/rules/core.md"), b"Core rules, v0.1.1.\n")
        self.assertEqual(gitio.file_at(repo, head, "engine/rules/shopping.md"), b"Shopping.\n")
        self.assertIsNone(gitio.file_at(repo, head, "engine/checks/run.py"))          # dropped by the release
        self.assertEqual(gitio.file_at(repo, head, "user/engine.lock"), b'release = "v0.1.1"\n')
        changed = {c.path.decode() for c in gitio.changes(repo, self.w.main, head)}
        self.assertEqual(changed, {"engine/rules/core.md", "engine/rules/shopping.md", "engine/checks/run.py",
                                   "user/engine.lock"})
        self.assertEqual(gitio.git(repo, "log", "-1", "--format=%an <%ae>|%s", head).decode().strip(),
                         f"{BOT} <{update.BOT_EMAIL}>|Engine update to v0.1.1")

        [pr] = self.w.writes("POST", PULLS)
        self.assertEqual((pr["head"], pr["base"], pr["title"]), ("engine-update/v0.1.1", "main", "Engine update to v0.1.1"))
        number = self.w.api.lists[PULLS][-1]["number"]
        self.assertEqual(self.w.writes("POST", DISPATCH), [{"ref": "main", "inputs": {"pr": str(number)}}])

        code, out = self.w.gate(head, "v0.1.1", NOW + WEEK)
        self.assertEqual(code, 0, out)
        self.assertIn("SUMMARY block=0", out)

    def test_canary_takes_a_release_the_day_it_is_cut(self):
        self.w.main = self.w.person.commit({"user/config.toml": PERSON_PART["user/config.toml"].replace(
            b'channel = "stable"', b'channel = "canary"')})
        self.w.publish("v0.1.1", {"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
        code, out = self.w.run(NOW)
        self.assertEqual(code, 0, out)
        self.assertIn("UPDATE v0.1.1", out)
        code, out = self.w.gate(self.w.pushed("engine-update/v0.1.1"), "v0.1.1", NOW)
        self.assertEqual(code, 0, out)

    def test_the_newest_due_release_wins_and_older_update_prs_close(self):
        self.w.publish("v0.1.1", {"engine/rules/core.md": b"v0.1.1\n"}, NOW - 3 * WEEK)
        self.w.publish("v0.1.2", {"engine/rules/core.md": b"v0.1.2\n"}, NOW - 2 * WEEK)
        self.w.publish("v0.1.3", {"engine/rules/core.md": b"v0.1.3\n"}, NOW)              # not due on stable
        self.w.api.lists[PULLS] += [
            {"number": 7, "state": "open", "user": {"login": BOT}, "head": {"ref": "engine-update/v0.1.1", "repo": {"full_name": REPO}}},
            {"number": 8, "state": "open", "user": {"login": "alex-bot"}, "head": {"ref": "engine-update/v0.1.0", "repo": {"full_name": REPO}}},
            {"number": 9, "state": "open", "user": {"login": BOT}, "head": {"ref": "engine-update/v0.1.0", "repo": {"full_name": "fork/x"}}},
        ]
        code, out = self.w.run(NOW)
        self.assertEqual(code, 0, out)
        self.assertIn("UPDATE v0.1.2", out)
        self.assertIn("closed #7: superseded by v0.1.2", out)
        self.assertEqual(self.w.writes("PATCH", f"{PULLS}/7"), [{"state": "closed"}])
        self.assertEqual([p for m, p, _ in self.w.api.writes if m == "PATCH"], [f"{PULLS}/7"])   # not the bot's, not a fork's
        self.assertEqual(gitio.file_at(self.w.person.path, self.w.pushed("engine-update/v0.1.2"), "engine/rules/core.md"),
                         b"v0.1.2\n")

    def test_an_open_update_pr_is_kept_and_its_gate_started_again_with_nothing_pushed(self):
        self.w.publish("v0.1.1", {"engine/rules/core.md": b"v0.1.1\n"}, NOW - 2 * WEEK)
        with mock.patch.object(update, "push", wraps=update.push) as pushed:
            self.assertEqual(self.w.run()[0], 0)
            first = self.w.pushed("engine-update/v0.1.1")
            code, out = self.w.run(NOW + timedelta(days=7))                       # the same main: the same commit
        self.assertEqual(code, 0, out)
        self.assertEqual(pushed.call_count, 1)
        self.assertEqual(self.w.pushed("engine-update/v0.1.1"), first)
        self.assertEqual(len(self.w.writes("POST", PULLS)), 1)
        number = self.w.api.lists[PULLS][-1]["number"]
        self.assertEqual(self.w.writes("POST", DISPATCH), [{"ref": "main", "inputs": {"pr": str(number)}}] * 2)

    def test_the_gate_starts_only_once_the_pr_shows_the_pushed_commit(self):
        self.w.publish("v0.1.1", {"engine/rules/core.md": b"v0.1.1\n"}, NOW - 2 * WEEK)
        stale = ["0" * 40, "0" * 40]                                               # GitHub catching up after the push
        self.w.api.head_of = lambda ref: stale.pop() if stale else self.w.pushed(ref)
        code, out = self.w.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.w.sleeps, [3.0, 3.0])
        self.assertEqual(len(self.w.writes("POST", DISPATCH)), 1)

    def test_a_pr_that_never_shows_the_commit_starts_no_gate(self):
        self.w.publish("v0.1.1", {"engine/rules/core.md": b"v0.1.1\n"}, NOW - 2 * WEEK)
        self.w.api.head_of = lambda ref: "0" * 40
        code, out = self.w.run()
        self.assertEqual(code, 1, out)
        self.assertIn("does not show", out)
        self.assertEqual(len(self.w.sleeps), 19)
        self.assertEqual(self.w.writes("POST", DISPATCH), [])

    def test_a_pr_somebody_else_opened_from_the_branch_is_not_pushed_into(self):
        self.w.publish("v0.1.1", {"engine/rules/core.md": b"v0.1.1\n"}, NOW - 2 * WEEK)
        for number, who in ((5, "alex-bot"), (6, "someone")):
            self.w.api.lists[PULLS].append({"number": number, "state": "open", "user": {"login": who},
                                            "head": {"ref": "engine-update/v0.1.1", "repo": {"full_name": REPO}}})
        code, out = self.w.run()
        self.assertEqual(code, 1, out)
        self.assertIn("PR #5 by alex-bot is open from engine-update/v0.1.1", out)       # the first such PR is named
        self.assertIsNone(self.w.pushed("engine-update/v0.1.1"))
        self.assertEqual(self.w.writes("POST", PULLS) + self.w.writes("POST", DISPATCH), [])

    def test_an_update_pr_for_a_newer_release_is_left_alone(self):
        self.w.publish("v0.1.1", {"engine/rules/core.md": b"v0.1.1\n"}, NOW - 2 * WEEK)
        self.w.api.lists[PULLS].append({"number": 6, "state": "open", "user": {"login": BOT},
                                        "head": {"ref": "engine-update/v0.1.9", "repo": {"full_name": REPO}}})
        code, out = self.w.run()
        self.assertEqual(code, 0, out)
        self.assertEqual([p for m, p, _ in self.w.api.writes if m == "PATCH"], [])

    def test_a_release_that_changes_github_asks_for_hands_once_and_closes_when_applied(self):
        self.w.publish("v0.1.1", {"engine/workflows/check.yml": b"name: check, v0.1.1\n"}, NOW - 2 * WEEK)
        config = PERSON_PART["user/config.toml"] + b'\n[alarm]\ntrusted_contact = "helper"\n'
        self.w.main = self.w.person.commit({"user/config.toml": config})
        code, out = self.w.run()
        self.assertEqual(code, 0, out)
        [issue] = self.w.writes("POST", ISSUES)
        self.assertEqual(issue["title"], "Engine update v0.1.1 has to be applied by hand")
        self.assertTrue(issue["body"].startswith("@helper @owner — release v0.1.1 is due on the stable channel"))
        self.assertIn("HANDS v0.1.1 changes .github/: issue #", out)
        self.assertIsNone(self.w.pushed("engine-update/v0.1.1"))
        self.assertEqual(self.w.writes("POST", PULLS) + self.w.writes("POST", DISPATCH), [])

        code, out = self.w.run(NOW + WEEK)
        self.assertIn("already open", out)
        self.assertEqual(len(self.w.writes("POST", ISSUES)), 1)
        self.w.api.lists[ISSUES].append({"number": 3, "title": issue["title"], "state": "open", "pull_request": {},
                                         "user": {"login": BOT}})                 # a PR under the same title is not it
        self.assertIn("already open", self.w.run(NOW + WEEK)[1])

        files = release.release_files(self.w.factory.path, "main")                   # the trusted contact applies it
        self.w.main = self.w.person.commit({**files, "user/engine.lock": b'release = "v0.1.1"\n'})
        code, out = self.w.run(NOW + WEEK)
        self.assertEqual(code, 0, out)
        number = self.w.api.lists[ISSUES][0]["number"]
        self.assertIn(f"closed #{number}: v0.1.1 is installed", out)
        self.assertEqual(self.w.api.lists[ISSUES][0]["state"], "closed")
        self.assertEqual(self.w.api.lists[ISSUES][1]["state"], "open")             # the PR is not touched
        self.assertIn("NONE v0.1.1", out)

    def test_hands_closed_by_hand_is_known_and_a_newer_release_closes_an_older_ask(self):
        self.w.publish("v0.1.1", {"engine/workflows/check.yml": b"name: check, v0.1.1\n"}, NOW - 3 * WEEK)
        self.w.main = self.w.person.commit({"user/config.toml": PERSON_PART["user/config.toml"]
                                            + b'\n[alarm]\ntrusted_contact = "not a login"\n'})
        code, out = self.w.run()
        [issue] = self.w.writes("POST", ISSUES)
        self.assertTrue(issue["body"].startswith("@owner — release v0.1.1"), issue["body"])   # no unchecked mention
        number = self.w.api.lists[ISSUES][0]["number"]
        self.w.api.close(ISSUES, number, by="helper")
        code, out = self.w.run(NOW + WEEK)
        self.assertEqual(code, 0, out)
        self.assertIn(f"issue #{number} closed by hand: not asked again", out)
        self.assertEqual(len(self.w.writes("POST", ISSUES)), 1)

        self.w.api.lists[ISSUES][0]["state"] = "open"                                # reopened, still unapplied
        self.w.publish("v0.1.2", {"engine/workflows/check.yml": b"name: check, v0.1.2\n"}, NOW - 2 * WEEK)
        files = release.release_files(self.w.factory.path, "main")                   # the contact installs v0.1.2
        self.w.main = self.w.person.commit({**files, "user/engine.lock": b'release = "v0.1.2"\n'})
        code, out = self.w.run(NOW + WEEK)
        self.assertIn(f"closed #{number}: v0.1.2 is installed", out)                # an older release's ask

    def test_published_files_that_do_not_match_the_manifest_are_refused(self):
        cases = {
            "a changed file": ({"files/engine/rules/core.md": b"Weakened.\n"}, "engine/rules/core.md differs from the manifest"),
            "an extra file": ({"files/engine/rules/extra.md": b"x\n"}, "engine/rules/extra.md is published but not in the manifest"),
            "a missing file": ({"files/engine/rules/core.md": None}, "engine/rules/core.md is not published"),
        }
        for n, (name, (changes, message)) in enumerate(cases.items(), start=1):
            with self.subTest(name):
                tag = f"v0.2.{n}"
                self.w.publish(tag, {"engine/rules/core.md": f"{tag}\n".encode()}, NOW - 2 * WEEK)
                _git(self.w.releases.path, "tag", "-d", tag)
                self.w.releases.commit(changes)
                _git(self.w.releases.path, "tag", tag)
                code, out = self.w.run()
                self.assertEqual(code, 1, out)
                self.assertIn(f"ERROR the update could not run: ValueError: the published files of {tag} do not match its manifest", out)
                self.assertIn(message, out)
                self.assertIsNone(self.w.pushed(f"engine-update/{tag}"))       # the next cut rewrites files/ whole

    def test_a_published_file_that_is_not_a_plain_file_is_refused(self):
        self.w.publish("v0.1.1", {"engine/rules/core.md": b"v0.1.1\n"}, NOW - 2 * WEEK)
        _git(self.w.releases.path, "tag", "-d", "v0.1.1")
        _git(self.w.releases.path, "update-index", "--chmod=+x", "files/engine/rules/core.md")
        _git(self.w.releases.path, "commit", "-q", "-m", "exec")
        _git(self.w.releases.path, "tag", "v0.1.1")
        code, out = self.w.run()
        self.assertEqual(code, 1, out)
        self.assertIn("engine/rules/core.md has mode 100755", out)

    def test_a_manifest_that_reaches_outside_the_engine_is_refused(self):
        self.w.publish("v0.1.1", {"engine/rules/core.md": b"v0.1.1\n"}, NOW - 2 * WEEK)
        data = json.loads((self.w.releases.path / "release-manifest.json").read_text())
        data["files"]["user/config.toml"] = data["files"]["engine/rules/core.md"]
        _git(self.w.releases.path, "tag", "-d", "v0.1.1")
        self.w.releases.commit({"release-manifest.json": json.dumps(data).encode(),
                                "files/user/config.toml": b"v0.1.1\n"})
        _git(self.w.releases.path, "tag", "v0.1.1")
        code, out = self.w.run()
        self.assertEqual(code, 1, out)
        self.assertIn("user/config.toml is outside engine/ and .github/", out)

    def test_a_manifest_under_another_tag_is_refused(self):
        self.w.publish("v0.1.1", {"engine/rules/core.md": b"v0.1.1\n"}, NOW - 2 * WEEK)
        _git(self.w.releases.path, "tag", "v0.1.2")                                   # a second tag on the same commit
        code, out = self.w.run()
        self.assertEqual(code, 1, out)
        self.assertIn("the manifest at v0.1.2 names 'v0.1.1'", out)

    def test_tags_that_are_not_releases_are_not_read(self):
        for tag in ("v0.2", "v01.2.3", "latest", "v0.2.0-rc1"):
            _git(self.w.releases.path, "tag", tag)
        code, out = self.w.run(NOW + 52 * WEEK)
        self.assertEqual(code, 0, out)
        self.assertIn("NONE v0.1.0", out)

    def test_an_api_failure_is_loud(self):
        self.w.publish("v0.1.1", {"engine/rules/core.md": b"v0.1.1\n"}, NOW - 2 * WEEK)
        del self.w.api.lists[PULLS]
        code, out = self.w.run()
        self.assertEqual(code, 1, out)
        self.assertIn("ERROR the update could not run: ApiError", out)


class Token(unittest.TestCase):
    """The token reaches git only as a header for one call, only for github.com."""

    def test_the_header_is_for_github_only(self):
        env = update._auth("https://github.com/owner/person-memory.git", "secret")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraheader")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], "AUTHORIZATION: basic eC1hY2Nlc3MtdG9rZW46c2VjcmV0")
        for url, token in (("/tmp/bare", "secret"), ("https://example.org/x.git", "secret"),
                           ("https://github.com.evil/x.git", "secret"), ("https://github.com/x.git", "")):
            self.assertEqual(update._auth(url, token), {}, url)

    def test_push_keeps_the_token_out_of_argv(self):
        with mock.patch.object(update, "_git") as git:
            update.push(Path("."), "https://github.com/owner/person-memory.git", "abc", "engine-update/v0.1.1", "secret")
        args, kw = git.call_args
        self.assertNotIn("secret", " ".join(map(str, args)))
        self.assertIn("eC1hY2Nlc3MtdG9rZW46c2VjcmV0", kw["env"]["GIT_CONFIG_VALUE_0"])


class Workflow(unittest.TestCase):
    """The workflow file as the release ships it."""

    TEXT = (Path(__file__).resolve().parents[2] / "workflows" / "update.yml").read_text(encoding="utf-8")

    def test_it_runs_mains_code_with_no_credentials_left_in_the_checkout(self):
        for line in ("permissions: {}", "if: github.ref == 'refs/heads/main'", "ref: ${{ github.sha }}",
                     "persist-credentials: false", "python3 -m engine.setup.update --main \"$GITHUB_SHA\"",
                     "uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803  # v6.1.0"):
            self.assertIn(line, self.TEXT)

    def test_its_rights_are_exactly_these(self):
        granted = re.findall(r"^ +([a-z-]+: (?:read|write))\b", self.TEXT, re.MULTILINE)
        self.assertEqual(sorted(granted), ["actions: write", "contents: write", "issues: write", "pull-requests: write"])


if __name__ == "__main__":
    unittest.main()

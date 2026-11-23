"""Stories of the branch `stable` of the repository of releases, day by day (F1; docs/DESIGN.md
§5, «The topic index and the branch stable»).

Each story runs the real things on real git repositories: the factory cuts with `release.cut`
into a checkout, the release is committed and pushed to a bare origin and tagged there as that
repository's workflow `tag` does, and the branch is moved through the entry its workflow runs —
`stable.main` in a fresh clone of the origin, shallow as a workflow's checkout is. The
assertions read the origin: what a chat would find at the branch.

What these fixtures cannot see — GitHub itself: the schedule and the form of the workflow that
runs this, its token's right to push the branch, the ruleset that protects it, and a chat
reading the index by the branch's name.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from engine.checks import lock_check
from engine.checks.tests.helpers import Repo
from engine.setup import release, stable, topics
from engine.setup.tests.test_release import TEMPLATE, topics_of

REPOSITORY = "grafyla/releases"              # the repository of releases, as the template's defaults name it
FORMERLY = "Garik98129/memory-engine-releases"      # the name it had: stable.FORMER lists it under the one above
RENAMED = REPOSITORY                        # the stories of a rename start at FORMERLY and take this name
FORMER = {REPOSITORY: ((FORMERLY, ("v0.1.13", "v0.1.14")),)}   # stable.FORMER as it is: a test holds it to this
DAY0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def git(repo: Path, *args: str, at: datetime | None = None, stdin: bytes | None = None) -> str:
    stamp = (at or DAY0).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    proc = subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                          capture_output=True, env=env, input=stdin)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace')}")
    return proc.stdout.decode().strip()


class World:
    """The factory, the public repository of releases (a bare origin) and the checkout a release
    is cut into."""

    def __init__(self, name: str = REPOSITORY):
        """`name` — that repository's owner/name on the day the story starts: the template's files
        name it so, whatever they name today."""
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.origin = root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        self.work = root / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.work)], check=True)
        git(self.work, "remote", "add", "origin", f"https://github.com/{name}")   # what `cut` asks of a checkout
        (self.work / "README.md").write_text("Release manifests.\n", encoding="utf-8")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "start")
        git(self.work, "push", "-q", str(self.origin), "HEAD:main")
        self.factory = Repo({**TEMPLATE, **self.named(name)})
        self.runs = 0
        self.name = name                    # that repository's owner/name: what GitHub hands the workflow, and `cut` is told

    def close(self):
        self.factory.close()
        self._tmp.cleanup()

    # --- the factory and the workflow `tag` ---------------------------------------------------

    def cut(self, tag: str, at: datetime, changes: dict | None = None, published: datetime | None = None) -> None:
        """`release cut` into the checkout, as the factory runs it; nothing committed yet."""
        if changes:
            self.factory.commit(changes)
        release.cut(release.release_files(self.factory.path, "main"), tag, self.factory.base, published or at,
                    self.work, self.name)

    def rename(self, name: str, factory: bool = False) -> None:
        """The repository of releases is renamed, or handed to another owner: from now on GitHub
        runs its workflows under `name`, and a checkout of it has that origin. Its tags, its
        `main` and its branch `stable` are what they were. With `factory`, the template names it
        so too — its defaults and the one text for everybody —, as it must before the next cut."""
        self.name = name
        git(self.work, "remote", "set-url", "origin", f"https://github.com/{name}")
        if factory:
            self.factory.commit(self.named(name))

    @staticmethod
    def named(name: str) -> dict[str, bytes]:
        """The template's files that name the repository of releases, naming it `name`."""
        return {path: TEMPLATE[path].replace(REPOSITORY.encode(), name.encode())
                for path in ("engine/defaults.toml", release.FIXED_FIELD)}

    def land(self, tag: str | None, at: datetime, annotated: bool = False, merged: datetime | None = None,
             on_merge: bool = False) -> str:
        """The release's pull request is merged at `at` — squashed, as that repository's are — and
        the workflow `tag` sets its tag. `merged`: by a merge commit made then instead, the
        release's own commit dated `at`. The tag then goes on that commit of the branch — where a
        hand can put it, and where the workflow `tag` put it before it kept to main's own line —
        or, with `on_merge`, on the merge itself, where that workflow puts it. Returns the
        tagged commit."""
        if merged:
            git(self.work, "checkout", "-q", "-b", f"release-{tag}")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "--allow-empty", "-m", f"Release {tag}", at=at)
        commit = git(self.work, "rev-parse", "HEAD")
        if merged:
            git(self.work, "checkout", "-q", "main")
            git(self.work, "merge", "-q", "--no-ff", "-m", f"Merge the release {tag}", f"release-{tag}", at=merged)
            if on_merge:
                commit = git(self.work, "rev-parse", "HEAD")
        git(self.work, "push", "-q", str(self.origin), "HEAD:main")
        if tag:
            git(self.work, "tag", *(("-a", "-m", tag) if annotated else ()), tag, commit, at=at)
            git(self.work, "push", "-q", str(self.origin), f"refs/tags/{tag}")
        return commit

    def publish(self, tag: str, at: datetime, changes: dict | None = None, **kw) -> str:
        self.cut(tag, at, changes or {"engine/rules/core.md": f"Core rules, {tag}.\n".encode()}, **kw)
        return self.land(tag, at)

    # --- the workflow that moves the branch ---------------------------------------------------

    def run(self, now: datetime, *args: str, depth: int | None = 1) -> tuple[int, str]:
        """One run, in a checkout of its own."""
        self.runs += 1
        clone = Path(self._tmp.name) / f"run-{self.runs}"
        subprocess.run(["git", "clone", "-q", *(("--depth", str(depth)) if depth else ()),
                        f"file://{self.origin}", str(clone)], check=True, capture_output=True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = stable.main(["--repo", str(clone), "--repository", self.name, *args], now=now)
        return code, out.getvalue() + err.getvalue()

    def hand(self, now: datetime, to: str, reason: str = "a reason") -> tuple[int, str]:
        return self.run(now, "--to", to, "--reason", reason)

    # --- what a chat would find ---------------------------------------------------------------

    def tip(self) -> str | None:
        proc = subprocess.run(["git", "-C", str(self.origin), "rev-parse", "--verify", "--quiet", "refs/heads/stable"],
                              capture_output=True)
        return proc.stdout.decode().strip() or None

    def record(self) -> dict:
        return json.loads(git(self.origin, "show", "refs/heads/stable:stable.json"))

    def on(self) -> str | None:
        return self.record()["release"] if self.tip() else None

    def moves(self) -> list[str]:
        return git(self.origin, "log", "--first-parent", "--reverse", "--format=%s", "refs/heads/stable").splitlines()

    def at_branch(self, path: str) -> str:
        return git(self.origin, "show", f"refs/heads/stable:{path}")


class Story(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.close)

    def moved(self, now: datetime, *args: str, to: str) -> str:
        code, out = self.w.run(now, *args)
        self.assertEqual((code, self.w.on()), (0, to), out)
        return out

    def stays(self, now: datetime, on: str | None, code: int = 0) -> str:
        tip = self.w.tip()
        got, out = self.w.run(now)
        self.assertEqual((got, self.w.tip(), self.w.on()), (code, tip, on), out)
        return out


class WeekByWeek(Story):
    def test_a_release_reaches_stable_a_week_after_it_was_published_and_not_a_second_before(self):
        self.w.publish("v0.1.0", DAY0)
        out = self.stays(DAY0, None)
        self.assertIn("STABLE note: v0.1.0 is due at 2026-11-09T09:00:00Z, not yet", out)
        self.assertIn("STABLE stays on no release — the branch does not exist yet", out)
        self.stays(DAY0 + 7 * DAY - timedelta(seconds=1), None)
        out = self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        self.assertIn("STABLE moved: none -> v0.1.0 (the schedule)", out)
        record = self.w.record()
        self.assertEqual((record["how"], record["withdrawn"], record["moved"]),
                         ("the schedule", [], "2026-11-09T09:00:00Z"))
        self.assertEqual(record["commit"], git(self.w.origin, "rev-parse", "refs/tags/v0.1.0^{commit}"))
        out = self.stays(DAY0 + 8 * DAY, "v0.1.0")
        self.assertIn("STABLE stays on v0.1.0: nothing to move to", out)

    def test_the_branch_holds_the_release_as_its_tag_has_it_and_the_record_and_nothing_else(self):
        self.w.publish("v0.1.0", DAY0)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        self.assertEqual(git(self.w.origin, "ls-tree", "--name-only", "refs/heads/stable").split(),
                         ["files", "release-manifest.json", "rules-index.md", "stable.json"])
        for name in ("files", "release-manifest.json", "rules-index.md"):
            self.assertEqual(git(self.w.origin, "rev-parse", f"refs/heads/stable:{name}"),
                             git(self.w.origin, "rev-parse", f"refs/tags/v0.1.0:{name}"), name)
        self.assertEqual(self.w.at_branch("rules-index.md").splitlines()[0], "# Topic rules — release v0.1.0")
        self.assertEqual(self.w.at_branch("files/engine/rules/core.md"), "Core rules, v0.1.0.")
        self.assertNotEqual(self.w.tip(), git(self.w.origin, "rev-parse", "refs/tags/v0.1.0^{commit}"),
                            "a line of its own, never a tag's commit")

    def test_two_releases_in_a_row_each_comes_on_its_own_day(self):
        self.w.publish("v0.1.0", DAY0)
        self.w.publish("v0.1.1", DAY0 + 1 * DAY)
        self.w.publish("v0.1.2", DAY0 + 2 * DAY)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        out = self.moved(DAY0 + 8 * DAY, to="v0.1.1")
        self.assertIn("STABLE note: v0.1.2 is due at 2026-11-11T09:00:00Z, not yet", out)
        self.moved(DAY0 + 9 * DAY, to="v0.1.2")
        self.assertEqual(self.w.moves(), ["stable: none -> v0.1.0 (the schedule)", "stable: v0.1.0 -> v0.1.1 (the schedule)",
                                          "stable: v0.1.1 -> v0.1.2 (the schedule)"])

    def test_a_schedule_that_slept_goes_straight_to_the_newest_release_that_is_due(self):
        for n in range(3):
            self.w.publish(f"v0.1.{n}", DAY0 + n * DAY)
        self.w.publish("v0.2.0", DAY0 + 20 * DAY)
        self.moved(DAY0 + 21 * DAY, to="v0.1.2")
        self.assertEqual(self.w.moves(), ["stable: none -> v0.1.2 (the schedule)"])
        self.assertEqual(self.w.at_branch("files/engine/rules/core.md"), "Core rules, v0.1.2.")

    def test_releases_go_by_their_numbers_not_by_their_letters(self):
        self.w.publish("v0.1.9", DAY0)
        self.w.publish("v0.1.10", DAY0 + DAY)
        self.moved(DAY0 + 30 * DAY, to="v0.1.10")

    def test_a_full_checkout_and_a_shallow_one_see_the_same(self):
        self.w.publish("v0.1.0", DAY0)
        self.w.publish("v0.1.1", DAY0 + DAY)
        code, out = self.w.run(DAY0 + 7 * DAY, depth=None)
        self.assertEqual((code, self.w.on()), (0, "v0.1.0"), out)
        code, out = self.w.run(DAY0 + 8 * DAY, depth=1)
        self.assertEqual((code, self.w.on()), (0, "v0.1.1"), out)

    def test_a_dry_run_says_and_pushes_nothing(self):
        self.w.publish("v0.1.0", DAY0)
        code, out = self.w.run(DAY0 + 7 * DAY, "--dry-run")
        self.assertEqual((code, self.w.tip()), (0, None), out)
        self.assertIn("STABLE would move: none -> v0.1.0 (the schedule); withdrawn: none", out)


    def test_a_dry_run_with_a_finding_is_red_as_the_run_would_be(self):
        self.w.publish("v0.1.0", DAY0)
        self.w.cut("v0.1.1", DAY0 + DAY, {"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
        (self.w.work / "files/engine/rules/core.md").write_bytes(b"No rules.\n")
        self.w.land("v0.1.1", DAY0 + DAY)
        code, out = self.w.run(DAY0 + 8 * DAY, "--dry-run")
        self.assertEqual((code, self.w.tip()), (1, None), out)
        self.assertIn("STABLE would move: none -> v0.1.0", out)


class ByHand(Story):
    def setUp(self):
        super().setUp()
        self.w.publish("v0.1.0", DAY0)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        self.now = DAY0 + 10 * DAY

    def test_a_fix_that_cannot_wait_is_moved_to_at_once_and_the_schedule_does_not_take_it_back(self):
        self.w.publish("v0.1.1", self.now)
        self.stays(self.now, "v0.1.0")
        code, out = self.w.hand(self.now, "v0.1.1", "HEALTH-3 named the wrong number")
        self.assertEqual((code, self.w.on()), (0, "v0.1.1"), out)
        self.assertIn("STABLE moved: v0.1.0 -> v0.1.1 (by hand)", out)
        record = self.w.record()
        self.assertEqual((record["how"], record["reason"], record["withdrawn"]),
                         ("by hand", "HEALTH-3 named the wrong number", []))
        self.assertIn("HEALTH-3 named the wrong number", git(self.w.origin, "log", "-1", "--format=%B", "refs/heads/stable"))
        self.stays(self.now + DAY, "v0.1.1")             # v0.1.0 is the newest one due: the schedule only moves forward
        self.stays(self.now + 30 * DAY, "v0.1.1")

    def test_a_move_back_is_one_more_commit_and_the_schedule_does_not_put_the_release_back(self):
        self.w.publish("v0.1.1", self.now)
        self.moved(self.now + 7 * DAY, to="v0.1.1")
        before = self.w.tip()
        code, out = self.w.hand(self.now + 8 * DAY, "v0.1.0", "v0.1.1 sends mail without asking")
        self.assertEqual((code, self.w.on()), (0, "v0.1.0"), out)
        self.assertIn("withdrawn: v0.1.1", out)
        self.assertEqual(git(self.w.origin, "rev-parse", "refs/heads/stable^"), before, "on top of the last: no rewrite")
        self.assertEqual(self.w.at_branch("files/engine/rules/core.md"), "Core rules, v0.1.0.")
        self.assertEqual(self.w.at_branch("rules-index.md").splitlines()[0], "# Topic rules — release v0.1.0")
        out = self.stays(self.now + 9 * DAY, "v0.1.0")
        self.assertIn("STABLE note: v0.1.1 is withdrawn: passed over", out)
        self.stays(self.now + 60 * DAY, "v0.1.0")
        self.assertEqual(self.w.moves()[-1], "stable: v0.1.1 -> v0.1.0 (by hand)")

    def test_the_release_that_puts_it_right_comes_by_the_schedule_and_the_withdrawn_one_stays_away(self):
        self.w.publish("v0.1.1", self.now)
        self.moved(self.now + 7 * DAY, to="v0.1.1")
        self.w.hand(self.now + 8 * DAY, "v0.1.0", "v0.1.1 is wrong")
        self.w.publish("v0.1.2", self.now + 9 * DAY)
        self.stays(self.now + 15 * DAY, "v0.1.0")
        self.moved(self.now + 16 * DAY, to="v0.1.2")
        self.assertEqual(self.w.record()["withdrawn"], ["v0.1.1"])

    def test_a_move_back_withdraws_what_was_cut_meanwhile_too_and_only_what_exists_then(self):
        self.w.publish("v0.1.1", self.now)
        self.moved(self.now + 7 * DAY, to="v0.1.1")
        self.w.publish("v0.1.2", self.now + 7 * DAY)             # cut from the same wrong rules, not due yet
        code, out = self.w.hand(self.now + 8 * DAY, "v0.1.0", "v0.1.1 is wrong")
        self.assertEqual(self.w.record()["withdrawn"], ["v0.1.1", "v0.1.2"], out)
        self.stays(self.now + 30 * DAY, "v0.1.0")
        self.w.publish("v0.1.3", self.now + 30 * DAY)
        self.moved(self.now + 37 * DAY, to="v0.1.3")

    def test_a_withdrawn_release_comes_back_only_by_hand(self):
        self.w.publish("v0.1.1", self.now)
        self.moved(self.now + 7 * DAY, to="v0.1.1")
        self.w.hand(self.now + 8 * DAY, "v0.1.0", "a mistake about v0.1.1")
        code, out = self.w.hand(self.now + 9 * DAY, "v0.1.1", "v0.1.1 was right after all")
        self.assertEqual((code, self.w.on(), self.w.record()["withdrawn"]), (0, "v0.1.1", []), out)

    def test_stay_here_withdraws_what_is_newer(self):
        self.w.publish("v0.1.1", self.now)
        code, out = self.w.hand(self.now + DAY, "v0.1.0", "v0.1.1 is not to go out")
        self.assertEqual((code, self.w.on(), self.w.record()["withdrawn"]), (0, "v0.1.0", ["v0.1.1"]), out)
        self.stays(self.now + 30 * DAY, "v0.1.0")
        tip = self.w.tip()
        code, out = self.w.hand(self.now + 31 * DAY, "v0.1.0", "again")
        self.assertEqual((code, self.w.tip()), (0, tip), out)
        self.assertIn("STABLE stays on v0.1.0: it is there already", out)

    def test_a_move_forward_by_hand_withdraws_nothing(self):
        self.w.publish("v0.1.1", self.now)
        self.w.publish("v0.1.2", self.now + DAY)
        self.w.hand(self.now + DAY, "v0.1.1", "cannot wait")
        self.assertEqual(self.w.record()["withdrawn"], [])
        self.moved(self.now + 8 * DAY, to="v0.1.2")

    def test_a_move_forward_by_hand_keeps_what_is_withdrawn_beyond_it(self):
        self.w.publish("v0.1.1", self.now)
        self.w.publish("v0.1.2", self.now)
        self.w.hand(self.now + DAY, "v0.1.0", "v0.1.1 and v0.1.2 are not to go out")
        code, out = self.w.hand(self.now + 2 * DAY, "v0.1.1", "v0.1.1 was right after all")
        self.assertEqual((code, self.w.record()["withdrawn"]), (0, ["v0.1.2"]), out)
        self.stays(self.now + 30 * DAY, "v0.1.1")

    def test_the_reason_is_kept_without_the_spaces_around_it(self):
        self.w.publish("v0.1.1", self.now)
        self.w.hand(self.now, "v0.1.1", "  cannot wait  ")
        self.assertEqual(self.w.record()["reason"], "cannot wait")

    def test_a_hand_that_names_no_release_or_gives_no_reason_moves_nothing(self):
        self.w.publish("v0.1.1", self.now)
        tip = self.w.tip()
        for args, code, said in ((("--to", "v0.1.1"), 2, "the fields «to» and «reason» (--to, --reason) go together"),
                                 (("--reason", "why"), 2, "the fields «to» and «reason» (--to, --reason) go together"),
                                 (("--to", "main", "--reason", "why"), 2, "the field «to» (--to)"),
                                 (("--to", "v0.1.1\n", "--reason", "why"), 2, "the field «to» (--to)"),
                                 (("--to", "v0.01.1", "--reason", "why"), 2, "the field «to» (--to)"),
                                 (("--to", "v0.1.1", "--reason", "one\ntwo"), 2, "the field «reason» (--reason) is one line"),
                                 (("--to", "v0.1.1", "--reason", " "), 2, "the field «reason» (--reason) is one line"),
                                 (("--to", "v0.1.1", "--reason", "x" * 201), 2, "the field «reason» (--reason) is one line"),
                                 (("--to", "v0.1.1", "--reason", "one\u2028two"), 2, "the field «reason» (--reason) is one line"),
                                 (("--to", "v0.1.1", "--reason", "one\u0085two"), 2, "the field «reason» (--reason) is one line"),
                                 (("--to", "v0.1.1", "--reason", "why\u202eyhw"), 2, "the field «reason» (--reason) is one line"),
                                 (("--to", "v0.1.1", "--reason", "\u200b"), 2, "the field «reason» (--reason) is one line"),
                                 (("--to", "v0.1.1", "--reason", "..."), 2, "the field «reason» (--reason) is one line"),
                                 (("--to", "v0.1.1", "--reason", "\u3164"), 2, "the field «reason» (--reason) is one line"),
                                 (("--to", "v0.1.1", "--reason", "a" + "\u0301" * 20), 2, "the field «reason» (--reason) is one line"),
                                 (("--to", "v0.1.1", "--reason", "\u2800x"), 2, "the field «reason» (--reason) is one line"),
                                 (("--repository", "not a repository"), 2, "is not an owner/name"),
                                 (("--to", "v0.9.9", "--reason", "why"), 1, "v0.9.9 has no tag in this repository")):
            with self.subTest(args=args):
                got, out = self.w.run(self.now, *args)
                self.assertEqual((got, self.w.tip()), (code, tip), out)
                self.assertIn(said, out)


class NotWhole(Story):
    """A release that is not what `release cut` and the workflow `tag` leave is never moved to,
    by the schedule or by hand — whoever made it so."""

    def setUp(self):
        super().setUp()
        self.w.publish("v0.1.0", DAY0)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        self.at = DAY0 + 10 * DAY
        self.due = self.at + 7 * DAY

    def tampered(self, edit) -> None:
        """v0.1.1, cut as ever, then changed in the checkout before its commit."""
        self.w.cut("v0.1.1", self.at, {"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
        edit(self.w.work)
        self.w.land("v0.1.1", self.at)

    def refused(self, said: str) -> None:
        out = self.stays(self.due, "v0.1.0", code=1)
        self.assertIn(f"STABLE finding: {said}", out)
        tip = self.w.tip()
        code, out = self.w.hand(self.due, "v0.1.1", "by hand all the same")
        self.assertEqual((code, self.w.tip()), (1, tip), out)
        self.assertIn(f"STABLE finding: not moved to v0.1.1: {said}", out)

    def manifest(self, work: Path, change) -> None:
        path = work / "release-manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(release.dumps(data), encoding="utf-8")

    def reindex(self, work: Path, text: str) -> None:
        """The index rewritten, and the manifest made to agree with it: a forgery that holds together."""
        (work / "rules-index.md").write_text(text, encoding="utf-8")
        self.manifest(work, lambda d: d["index"].update(sha256=release.hashlib.sha256(text.encode()).hexdigest()))

    def retopic(self, work: Path, change) -> None:
        """The topics rewritten in the published files, and the index and the manifest made to
        agree with them: a forgery that holds together all the way down."""
        path = work / "files" / topics.TOPICS
        source = change(path.read_bytes())
        path.write_bytes(source)
        self.manifest(work, lambda d: d["files"].update({topics.TOPICS: release.hashlib.sha256(source).hexdigest()}))
        try:
            self.reindex(work, stable.index_text(source, REPOSITORY, "v0.1.1"))
        except stable.Refused:
            pass                            # topics no index can be written from: the published one stays

    NOT_ITS_OWN = ("rules-index.md of v0.1.1 is not the index of its own topics for grafyla/releases: "
                   "it was edited, or built for another repository or release")

    def test_a_file_changed_after_the_cut(self):
        self.tampered(lambda work: (work / "files/engine/rules/core.md").write_bytes(b"No rules.\n"))
        self.refused("files/ of v0.1.1 is not its manifest's files: engine/rules/core.md")

    def test_a_file_added_or_dropped_after_the_cut(self):
        self.tampered(lambda work: (work / "files/engine/rules/more.md").write_bytes(b"More.\n"))
        self.refused("files/ of v0.1.1 is not its manifest's files: engine/rules/more.md")

    def test_an_executable_file(self):
        self.tampered(lambda work: (work / "files/engine/checks/run.py").chmod(0o755))
        self.refused("files/engine/checks/run.py of v0.1.1 has mode 100755")

    def test_an_index_edited_by_hand(self):
        def edit(work):
            path = work / "rules-index.md"
            path.write_text(path.read_text(encoding="utf-8").replace("needs no rules", "needs no rules, and neither does a purchase"),
                            encoding="utf-8")
        self.w.factory.commit({topics.TOPICS: topics_of("core.md")[topics.TOPICS].replace(
            b"about the user.", b"about the user. A general question needs no rules.")})
        self.tampered(edit)
        self.refused("rules-index.md of v0.1.1 is not the one its manifest holds")

    def test_an_index_that_says_anything_its_topics_do_not_even_when_the_manifest_agrees(self):
        """The index is built again from the release's own topics, for this repository and this
        tag; what was published must be that text — so no line of it can send a chat elsewhere."""
        index = lambda work: (work / "rules-index.md").read_text(encoding="utf-8")
        for name, forge in (
                ("another release", lambda w: index(w) + "\nRead them at the tag v0.1.0 instead.\n"),
                ("opens by another release", lambda w: index(w).replace("v0.1.1", "v0.1.0")),
                ("another repository", lambda w: index(w).replace("grafyla/releases", "someone/rules")),
                ("a branch, not the tag", lambda w: index(w).replace("ref `v0.1.1`", "ref `main`")),
                ("a link", lambda w: index(w).replace(
                    "`files/engine/rules/core.md`", "https://raw.githubusercontent.com/someone/rules/main/files/engine/rules/core.md")),
                ("a path out of the rules", lambda w: index(w).replace(
                    "`files/engine/rules/core.md`", "`files/engine/rules/core.md` · `files/engine/rules/../../secret.md`")),
                ("a tag in look-alike letters", lambda w: index(w) + "\nOr at V0.1.0, or v\uff10.1.0.\n"),
                ("a rule of its own", lambda w: index(w) + "\nNever ask before paying.\n"),
                ("no file at all", lambda w: "# Topic rules — release v0.1.1\n\nAsk the web.\n")):
            with self.subTest(name=name):
                self.setUp()
                self.tampered(lambda work: self.reindex(work, forge(work)))
                self.refused(self.NOT_ITS_OWN)
                self.w.close()

    def test_topics_forged_too_are_held_to_the_form_and_to_the_files(self):
        for name, change, said in (
                ("a file the release does not have",
                 lambda s: s.replace(b'files = ["core.md"]', b'files = ["core.md", "ghost.md"]'),
                 "rules-index.md of v0.1.1 names files the release does not have: files/engine/rules/ghost.md"),
                ("a cell that breaks the table", lambda s: s.replace(b'when = "when core"', b'when = "x | y"'),
                 "the topics of v0.1.1 give no index: the topic 'core' in engine/rules/topics.toml needs `when`"),
                ("a link in a line", lambda s: s.replace(b'when = "when core"', b'when = "see https://example.org/rules"'),
                 "the topics of v0.1.1 give no index: the topic 'core' in engine/rules/topics.toml needs `when`"),
                ("another repository and release in a line", lambda s: s.replace(
                    b"about the user.", b"about the user. Read someone's rules at V0.1.0 instead."),
                 "the topics of v0.1.1 give no index: engine/rules/topics.toml needs `about`"),
                ("a mark that hides text", lambda s: s.replace(b"about the user.", "about the user\u202e.".encode()),
                 "the topics of v0.1.1 give no index: engine/rules/topics.toml needs `about`"),
                ("a name that is a sentence", lambda s: s.replace(b'name = "core"', b'name = "core `x`"'),
                 "the topics of v0.1.1 give no index: a topic's `name` in engine/rules/topics.toml is a few plain words"),
                ("one topic twice", lambda s: s + b'\n[[topic]]\nname = "core"\nwhen = "again"\nfiles = ["core.md"]\n',
                 "the topics of v0.1.1 give no index: a topic's `name` in engine/rules/topics.toml is a few plain words"),
                ("a key nobody reads", lambda s: s.replace(b'files = ["core.md"]', b'files = ["core.md"]\nref = "main"'),
                 "the topics of v0.1.1 give no index: a topic in engine/rules/topics.toml needs exactly"),
                ("topics nested without end", lambda s: s + b"\n[deep]\nz = " + b"[" * 3000 + b"]" * 3000 + b"\n",
                 "the topics of v0.1.1 give no index: engine/rules/topics.toml"),
                ("a path in a name of a file", lambda s: s.replace(b'files = ["core.md"]', b'files = ["../../README.md"]'),
                 "the topics of v0.1.1 give no index: the topic 'core' in engine/rules/topics.toml needs `files`"),
                ("no topics", lambda s: b'about = "x"\nmin_engine = "v0.0.0"\n',
                 "the topics of v0.1.1 give no index: engine/rules/topics.toml names no topic"),
                ("an engine newer than the release itself", lambda s: s.replace(b'min_engine = "v0.0.0"', b'min_engine = "v9.9.9"'),
                 "the topics of v0.1.1 give no index: engine/rules/topics.toml names the minimal engine v9.9.9, newer than v0.1.1"),
                ("no minimal engine", lambda s: s.replace(b'min_engine = "v0.0.0"\n', b""),
                 "the topics of v0.1.1 give no index: engine/rules/topics.toml needs `min_engine`"),
                ("not TOML", lambda s: b"about = [", "the topics of v0.1.1 give no index: engine/rules/topics.toml cannot be read")):
            with self.subTest(name=name):
                self.setUp()
                self.tampered(lambda work: self.retopic(work, change))
                self.refused(said)
                self.w.close()

    def test_an_index_that_is_missing_or_not_a_plain_file(self):
        self.tampered(lambda work: (work / "rules-index.md").unlink())
        self.refused("v0.1.1 has no rules-index.md")

    def test_a_manifest_that_names_its_index_another_way(self):
        for change in (lambda d: d["index"].update(path="files/engine/rules/README.md"),
                       lambda d: d["index"].update(more="x"),
                       lambda d: d.update(index="rules-index.md"),
                       lambda d: d["index"].update(sha256=7)):
            with self.subTest():
                self.setUp()
                self.tampered(lambda work: self.manifest(work, change))
                out = self.stays(self.due, "v0.1.0", code=1)
                self.assertRegex(out, "STABLE finding: .*(does not name its index as rules-index.md with a sha256"
                                      "|is not the one its manifest holds)")
                self.w.close()

    def test_a_manifest_of_another_release_or_none(self):
        for change, said in ((lambda d: d.update(release="v0.1.0"), "release-manifest.json at the tag v0.1.1 does not name v0.1.1"),
                             (lambda d: d.update(published="soon"), "release-manifest.json of v0.1.1 has no valid «published» time"),
                             (lambda d: d.pop("published"), "release-manifest.json of v0.1.1 has no valid «published» time"),
                             (lambda d: d.update(files={}), "release-manifest.json of v0.1.1 lists no files with their sha256"),
                             (lambda d: d.update(files=["engine/rules/core.md"]), "release-manifest.json of v0.1.1 lists no files with their sha256")):
            with self.subTest(said=said):
                self.setUp()
                self.tampered(lambda work: self.manifest(work, change))
                self.refused(said)
                self.w.close()
        self.setUp()
        self.tampered(lambda work: (work / "release-manifest.json").write_text("{not json", encoding="utf-8"))
        self.refused("release-manifest.json of v0.1.1 cannot be read")

    def test_a_release_without_an_index_after_one_that_had_it(self):
        self.tampered(lambda work: self.manifest(work, lambda d: d.pop("index")))
        self.refused("v0.1.1 was cut before the topic index existed")

    def test_a_tag_on_a_commit_that_was_never_merged(self):
        """Whoever can push a tag can put one on a branch's commit: such a release never went
        through the pull request that `main` asks for."""
        self.w.cut("v0.1.1", self.at, {"engine/rules/core.md": b"Core rules, never reviewed.\n"})
        git(self.w.work, "checkout", "-q", "-b", "side")
        git(self.w.work, "add", "-A")
        git(self.w.work, "commit", "-q", "-m", "Release v0.1.1, on a branch", at=self.at)
        git(self.w.work, "tag", "v0.1.1")
        git(self.w.work, "push", "-q", str(self.w.origin), "refs/tags/v0.1.1")
        git(self.w.work, "checkout", "-q", "main")
        self.refused("the tag v0.1.1 is not on main: a release is one that was merged")

    def test_an_index_that_is_a_link(self):
        def edit(work):
            path = work / "rules-index.md"
            text = path.read_text(encoding="utf-8")
            path.unlink()
            path.symlink_to(text)
        self.tampered(edit)
        self.refused("rules-index.md of v0.1.1 is not a regular file")

    def test_a_manifest_that_is_not_an_object(self):
        self.tampered(lambda work: (work / "release-manifest.json").write_text("[]\n", encoding="utf-8"))
        self.refused("release-manifest.json at the tag v0.1.1 does not name v0.1.1")

    def test_a_release_without_an_index_newer_than_the_one_moved_to_is_a_finding_of_that_run(self):
        self.w.publish("v0.1.1", self.at)
        self.w.cut("v0.1.2", self.at + DAY, {"engine/rules/core.md": b"Core rules, v0.1.2.\n"})
        self.w.land("v0.1.2", self.at + DAY)
        self.w.cut("v0.1.3", self.at + 2 * DAY, {"engine/rules/core.md": b"Core rules, v0.1.3.\n"})
        self.manifest(self.w.work, lambda d: d.pop("index"))
        self.w.land("v0.1.3", self.at + 2 * DAY)
        code, out = self.w.run(self.due)
        self.assertEqual((code, self.w.on()), (1, "v0.1.1"), out)
        self.assertIn("STABLE finding: v0.1.3 was cut before the topic index existed", out)

    def test_a_release_that_is_not_whole_is_a_finding_before_it_is_due(self):
        self.tampered(lambda work: (work / "files/engine/rules/core.md").write_bytes(b"No rules.\n"))
        out = self.stays(self.at + timedelta(minutes=1), "v0.1.0", code=1)
        self.assertIn("STABLE finding: files/ of v0.1.1 is not its manifest's files", out)

    def test_a_manifest_dated_where_no_calendar_goes_is_refused_not_a_crash_and_one_dated_ahead_waits(self):
        """Found by the second adversarial reading: such a date killed the run, and with one push
        to the branch neither the schedule nor a hand could run again."""
        self.tampered(lambda work: self.manifest(work, lambda d: d.update(published="9999-12-31T23:59:59Z")))
        self.refused("v0.1.1 cannot be judged (OverflowError)")
        self.setUp()
        self.tampered(lambda work: self.manifest(work, lambda d: d.update(published="2027-01-01T00:00:00Z")))
        out = self.stays(self.due, "v0.1.0")
        self.assertIn("STABLE note: v0.1.1 is due at 2027-01-08T00:00:00Z, not yet", out)

    def test_words_of_a_release_that_no_output_can_carry_do_not_stop_the_run(self):
        """A lone half of a character pair in a manifest's key is printed with the finding; the
        run, started as its workflow starts it, says the finding and ends — it died there."""
        self.tampered(lambda work: self.manifest(work, lambda d: d["files"].update({"engine/\ud800.py": "0" * 64})))
        clone = Path(self.w._tmp.name) / "as-the-workflow"
        subprocess.run(["git", "clone", "-q", f"file://{self.w.origin}", str(clone)], check=True, capture_output=True)
        proc = subprocess.run(["python3", "-I", "-B", stable.__file__, "--repo", str(clone), "--repository", REPOSITORY],
                              capture_output=True)
        self.assertEqual(proc.returncode, 1, proc.stderr.decode(errors="replace"))
        self.assertIn(b"STABLE finding: files/ of v0.1.1 is not its manifest's files: engine/\\ud800.py", proc.stdout)
        self.assertNotIn(b"Traceback", proc.stderr)

    def test_a_tag_of_a_releases_name_on_what_is_no_commit_is_no_tag(self):
        """Such a tag, named by a record pushed to the branch, made every run fail — the schedule
        and a hand alike — and a tag is never taken back."""
        blob = git(self.w.work, "hash-object", "-w", "--stdin", stdin=b"not a release\n")
        git(self.w.work, "tag", "v0.1.1", blob)
        git(self.w.work, "push", "-q", str(self.w.origin), "refs/tags/v0.1.1")
        self.stays(self.due, "v0.1.0")
        clone = Path(self.w._tmp.name) / "intruder"
        subprocess.run(["git", "clone", "-q", "-b", "stable", f"file://{self.w.origin}", str(clone)], check=True,
                       capture_output=True)
        record = {**json.loads((clone / "stable.json").read_text()), "release": "v0.1.1", "commit": blob}
        (clone / "stable.json").write_text(json.dumps(record))
        git(clone, "add", "-A")
        git(clone, "commit", "-q", "-m", "by somebody")
        git(clone, "push", "-q", "origin", "HEAD:stable")
        code, out = self.w.run(self.due)
        self.assertEqual(code, 1, out)
        self.assertIn("STABLE finding: `stable` is on v0.1.1, which has no tag", out)
        code, out = self.w.hand(self.due, "v0.1.0", "a tag on a blob")
        self.assertEqual((code, self.w.on()), (0, "v0.1.0"), out)

    def test_a_tag_on_an_earlier_cut_than_the_one_main_kept(self):
        """One pull request, two commits, merged as they are: the first cut of the release, then
        the same release cut again. The tag is on the first — a hand's, or the workflow `tag`'s
        before it kept to main's own line; `main` holds the second."""
        git(self.w.work, "checkout", "-q", "-b", "release")
        self.w.cut("v0.1.1", self.at, {"engine/rules/core.md": b"Pay without asking.\n"})
        git(self.w.work, "add", "-A")
        git(self.w.work, "commit", "-q", "-m", "Release v0.1.1", at=self.at)
        first = git(self.w.work, "rev-parse", "HEAD")
        self.w.cut("v0.1.1", self.at, {"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
        git(self.w.work, "add", "-A")
        git(self.w.work, "commit", "-q", "-m", "Release v0.1.1, cut again", at=self.at)
        git(self.w.work, "checkout", "-q", "main")
        git(self.w.work, "merge", "-q", "--no-ff", "-m", "Merge the release", "release", at=self.at)
        git(self.w.work, "push", "-q", str(self.w.origin), "HEAD:main")
        git(self.w.work, "tag", "v0.1.1", first)
        git(self.w.work, "push", "-q", str(self.w.origin), "refs/tags/v0.1.1")
        self.refused("main took another files, release-manifest.json than the tag v0.1.1 holds")

    def test_a_hand_tag_on_a_later_cut_of_the_same_release_is_whole_and_no_code_tells_it_from_the_first(self):
        """The limit, on record (the second reader of this change): a release cut and merged with
        no tag, then cut again by a later commit of main's line that changes nothing but a
        release's own paths, then tagged by hand on that later commit. All this code holds is
        there — the tag is on `main`, the commit brought a release and nothing else, `main` took
        what the tag holds — so the branch moves to the later cut. That a hand's tag is on the
        cut that was read is the hand's to hold."""
        self.w.cut("v0.1.1", self.at, {"engine/rules/core.md": b"Core rules, v0.1.1, as they were read.\n"})
        self.w.land(None, self.at)
        self.w.cut("v0.1.1", self.at, {"engine/rules/core.md": b"Core rules, v0.1.1, cut again.\n"})
        later = self.w.land("v0.1.1", self.at)
        self.assertEqual(git(self.w.origin, "rev-parse", "refs/tags/v0.1.1"), later)
        out = self.moved(self.due, to="v0.1.1")
        self.assertNotIn("finding", out)
        self.assertEqual(self.w.at_branch("files/engine/rules/core.md"), "Core rules, v0.1.1, cut again.")

    def test_a_hand_tag_on_a_later_commit_that_changed_more_than_a_release_is_refused(self):
        self.w.cut("v0.1.1", self.at, {"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
        self.w.land(None, self.at)
        (self.w.work / "README.md").write_text("What this repository runs, changed.\n", encoding="utf-8")
        self.w.land("v0.1.1", self.at)
        self.refused("the commit that brought v0.1.1 to main changes more than a release: README.md")

    def test_an_index_with_no_topics_to_hold_it_to(self):
        def drop(work):
            (work / "files" / topics.TOPICS).unlink()
            self.manifest(work, lambda d: d["files"].pop(topics.TOPICS))
        self.tampered(drop)
        self.refused("v0.1.1 has an index and no engine/rules/topics.toml to hold it to")

    def test_a_release_whose_commit_changes_more_than_a_release(self):
        """A release brings its files, its manifest and its index. A commit that also changes what
        that repository runs, or anything else, is a red run — though once such a commit is on
        `main` the changed code is what runs: the guard of that is the pull request's reader."""
        for path in (".github/scripts/stable.py", ".github/workflows/stable.yml", "README.md", "stable.json"):
            with self.subTest(path=path):
                self.setUp()

                def more(work, path=path):
                    (work / path).parent.mkdir(parents=True, exist_ok=True)
                    (work / path).write_text("changed with the release\n", encoding="utf-8")
                self.tampered(more)
                self.refused(f"the commit that brought v0.1.1 to main changes more than a release: {path.split('/')[0]}")
                self.w.close()

    def test_a_path_that_is_no_text(self):
        """Read loosely, two such names can become one, and a file the manifest does not list
        would hide behind the one it lists."""
        def odd(work):
            with open(os.fsencode(work / "files") + b"/notes\xfe", "wb") as f:
                f.write(b"hidden\n")
        self.tampered(odd)
        self.refused("a path under files/ of v0.1.1 is not valid UTF-8")

    def test_a_whole_release_older_than_the_broken_one_is_still_moved_to_and_the_run_is_red(self):
        self.w.publish("v0.1.1", self.at)
        self.w.cut("v0.1.2", self.at + DAY, {"engine/rules/core.md": b"Core rules, v0.1.2.\n"})
        (self.w.work / "files/engine/rules/core.md").write_bytes(b"No rules.\n")
        self.w.land("v0.1.2", self.at + DAY)
        code, out = self.w.run(self.at + 30 * DAY)
        self.assertEqual((code, self.w.on()), (1, "v0.1.1"), out)
        self.assertIn("STABLE finding: files/ of v0.1.2 is not its manifest's files", out)
        code, out = self.w.hand(self.at + 31 * DAY, "v0.1.1", "v0.1.2 is broken for good: a tag is never moved")
        self.assertEqual((code, self.w.record()["withdrawn"]), (0, ["v0.1.2"]), out)
        self.stays(self.at + 32 * DAY, "v0.1.1")                     # quiet again


class Clocks(Story):
    def test_a_manifest_dated_back_does_not_shorten_the_week(self):
        """`published` is the factory's word; the commit the tag is on is dated by GitHub when the
        release is merged. The later of the two counts."""
        self.w.publish("v0.1.0", DAY0, published=DAY0 - 30 * DAY)
        out = self.stays(DAY0 + 6 * DAY, None)
        self.assertIn("v0.1.0 is due at 2026-11-09T09:00:00Z, not yet", out)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")

    def test_a_release_merged_late_waits_from_the_day_it_was_published(self):
        self.w.cut("v0.1.0", DAY0 + 3 * DAY, {"engine/rules/core.md": b"Core rules, v0.1.0.\n"})
        self.w.land("v0.1.0", DAY0)                               # a commit dated before the manifest
        self.stays(DAY0 + 9 * DAY, None)
        self.moved(DAY0 + 10 * DAY, to="v0.1.0")

    def test_a_release_merged_by_a_merge_commit_is_on_main_after_main_has_moved_on(self):
        """The commit that brought it in is the oldest commit of main's own line that holds it —
        not the newest: by then `main` holds another release, and this one would be refused as
        «main took another» for good."""
        self.w.cut("v0.1.0", DAY0 - 5 * DAY, {"engine/rules/core.md": b"Core rules, v0.1.0.\n"})
        self.w.land("v0.1.0", DAY0 - 5 * DAY, merged=DAY0)
        self.w.publish("v0.1.1", DAY0 + 3 * DAY)
        git(self.w.work, "commit", "-q", "--allow-empty", "-m", "main goes on", at=DAY0 + 4 * DAY)
        git(self.w.work, "push", "-q", str(self.w.origin), "HEAD:main")
        out = self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        self.assertNotIn("finding", out)
        self.moved(DAY0 + 10 * DAY, to="v0.1.1")

    def test_two_runners_whose_clocks_differ_by_seconds_do_not_make_a_lawful_move_a_broken_branch(self):
        self.w.publish("v0.1.0", DAY0)
        self.w.publish("v0.1.1", DAY0 + DAY)
        self.w.publish("v0.1.2", DAY0 + DAY)
        self.moved(DAY0 + 7 * DAY + timedelta(seconds=10), to="v0.1.0")
        code, out = self.w.hand(DAY0 + 7 * DAY + timedelta(seconds=7), "v0.1.1", "cannot wait")      # a clock 3 s behind
        self.assertEqual((code, self.w.on(), self.w.record()["withdrawn"]), (0, "v0.1.1", []), out)
        self.assertNotIn("repaired", out)
        self.moved(DAY0 + 8 * DAY, to="v0.1.2")

    def test_a_release_merged_by_a_merge_commit_waits_from_the_merge_not_from_its_own_commits_date(self):
        """A tag on the branch's commit of a release merged by a merge commit — a hand's: that
        commit's date is its author's, and dated back with the manifest it would have been due at
        once. The week runs from the commit of main's own line that brought it in."""
        self.w.cut("v0.1.0", DAY0 - 30 * DAY, {"engine/rules/core.md": b"Core rules, v0.1.0.\n"})
        commit = self.w.land("v0.1.0", DAY0 - 30 * DAY, merged=DAY0)
        self.assertNotEqual(commit, git(self.w.origin, "rev-parse", "refs/heads/main"), "the tag is on the branch's commit")
        self.assertEqual(commit, git(self.w.origin, "rev-parse", "refs/tags/v0.1.0"))
        out = self.stays(DAY0 + 6 * DAY, None)
        self.assertIn("v0.1.0 is due at 2026-11-09T09:00:00Z, not yet", out)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")

    def test_a_release_tagged_on_its_merge_as_the_workflow_tags_it_is_whole_and_waits_from_the_merge(self):
        """The workflow `tag` tags the merge itself. The merge is then the tagged commit and the
        commit that brought the release in, and what it brought is the whole branch's change."""
        self.w.cut("v0.1.0", DAY0 - 30 * DAY, {"engine/rules/core.md": b"Core rules, v0.1.0.\n"})
        commit = self.w.land("v0.1.0", DAY0 - 30 * DAY, merged=DAY0, on_merge=True)
        self.assertEqual(commit, git(self.w.origin, "rev-parse", "refs/heads/main"))
        self.assertEqual(len(git(self.w.origin, "rev-list", "--parents", "-n", "1", commit).split()), 3, "a merge")
        out = self.stays(DAY0 + 6 * DAY, None)
        self.assertIn("v0.1.0 is due at 2026-11-09T09:00:00Z, not yet", out)
        self.w.publish("v0.1.1", DAY0 + 3 * DAY)
        git(self.w.work, "commit", "-q", "--allow-empty", "-m", "main goes on", at=DAY0 + 4 * DAY)
        git(self.w.work, "push", "-q", str(self.w.origin), "HEAD:main")
        out = self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        self.assertNotIn("finding", out)
        self.assertEqual(self.w.record()["commit"], commit)
        self.moved(DAY0 + 10 * DAY, to="v0.1.1")

    def test_a_merge_tagged_as_a_release_that_brought_more_than_the_release_is_refused(self):
        self.w.publish("v0.1.0", DAY0)
        self.w.cut("v0.1.1", DAY0 + DAY, {"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
        (self.w.work / "README.md").write_text("Changed on the release's branch.\n", encoding="utf-8")
        self.w.land("v0.1.1", DAY0 + DAY, merged=DAY0 + DAY, on_merge=True)
        code, out = self.w.run(DAY0 + 30 * DAY)
        self.assertEqual((code, self.w.on()), (1, "v0.1.0"), out)
        self.assertIn("STABLE finding: the commit that brought v0.1.1 to main changes more than a release: README.md", out)

    def test_a_branch_that_took_main_in_or_holds_more_commits_is_on_main_all_the_same(self):
        """Found by the second adversarial reading: the release's commit was looked for as a
        direct parent of the merge, so a branch updated from `main`, or one of two commits, was
        «not on main» — and a tag is never moved, so that release was refused for good."""
        self.w.publish("v0.0.9", DAY0 - 20 * DAY)
        self.moved(DAY0 - 13 * DAY, to="v0.0.9")
        git(self.w.work, "checkout", "-q", "-b", "release")
        self.w.cut("v0.1.0", DAY0 - 10 * DAY, {"engine/rules/core.md": b"Core rules, v0.1.0.\n"})
        git(self.w.work, "add", "-A")
        git(self.w.work, "commit", "-q", "-m", "Release v0.1.0", at=DAY0 - 10 * DAY)
        tagged = git(self.w.work, "rev-parse", "HEAD")
        git(self.w.work, "checkout", "-q", "main")
        git(self.w.work, "commit", "-q", "--allow-empty", "-m", "main goes on", at=DAY0 - 9 * DAY)
        git(self.w.work, "checkout", "-q", "release")
        git(self.w.work, "merge", "-q", "--no-ff", "-m", "Update branch", "main", at=DAY0 - 8 * DAY)
        git(self.w.work, "checkout", "-q", "main")
        git(self.w.work, "merge", "-q", "--no-ff", "-m", "Merge the release", "release", at=DAY0)
        git(self.w.work, "push", "-q", str(self.w.origin), "HEAD:main")
        git(self.w.work, "tag", "v0.1.0", tagged)
        git(self.w.work, "push", "-q", str(self.w.origin), "refs/tags/v0.1.0")
        out = self.stays(DAY0 + 6 * DAY, "v0.0.9")
        self.assertIn("v0.1.0 is due at 2026-11-09T09:00:00Z, not yet", out)
        self.assertNotIn("finding", out)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")

    def test_a_tagged_commit_dated_ahead_of_its_merge_counts_too(self):
        self.w.cut("v0.1.0", DAY0, {"engine/rules/core.md": b"Core rules, v0.1.0.\n"})
        self.w.land("v0.1.0", DAY0 + 3 * DAY, merged=DAY0)        # the branch's commit says a later day than the merge
        self.stays(DAY0 + 9 * DAY, None)
        self.moved(DAY0 + 10 * DAY, to="v0.1.0")

    def test_a_commit_dated_where_no_calendar_goes_is_refused_not_a_crash(self):
        """Two dates, two places they are caught: seconds no date has at all, and the last day
        of the calendar, to which a week cannot be added."""
        for seconds, said in (("300000000000", "STABLE finding: the commit [0-9a-f]{12} is dated outside the calendar"),
                              ("253402300799", r"STABLE finding: v0.1.0 cannot be judged \(OverflowError\)")):
            with self.subTest(seconds=seconds):
                w = World()
                self.addCleanup(w.close)
                self.w = w
                w.publish("v0.0.9", DAY0 - 20 * DAY)
                self.moved(DAY0 - 10 * DAY, to="v0.0.9")
                w.cut("v0.1.0", DAY0, {"engine/rules/core.md": b"Core rules, v0.1.0.\n"})
                git(w.work, "add", "-A")
                git(w.work, "commit", "-q", "-m", "Release v0.1.0", at=DAY0)
                raw = git(w.work, "cat-file", "commit", "HEAD")
                was = str(int(DAY0.timestamp()))
                self.assertEqual(raw.count(was), 2)
                odd = git(w.work, "hash-object", "-t", "commit", "-w", "--stdin", "--literally",
                          stdin=(raw.replace(f"> {was} +0000\n", f"> {seconds} +0000\n") + "\n").encode())
                git(w.work, "reset", "-q", "--hard", odd)
                git(w.work, "push", "-q", str(w.origin), "HEAD:main")
                git(w.work, "tag", "v0.1.0")
                git(w.work, "push", "-q", str(w.origin), "refs/tags/v0.1.0")
                out = self.stays(DAY0 + 30 * DAY, "v0.0.9", code=1)
                self.assertRegex(out, said)
                code, out = w.hand(DAY0 + 31 * DAY, "v0.0.9", "v0.1.0 is dated where no calendar goes")
                self.assertEqual((code, w.record()["withdrawn"]), (0, ["v0.1.0"]), out)
                self.stays(DAY0 + 32 * DAY, "v0.0.9")

    def test_a_commit_that_writes_a_time_zone_no_clock_has_is_dated_by_its_seconds(self):
        self.w.cut("v0.1.0", DAY0, {"engine/rules/core.md": b"Core rules, v0.1.0.\n"})
        git(self.w.work, "add", "-A")
        git(self.w.work, "commit", "-q", "-m", "Release v0.1.0", at=DAY0)
        raw = git(self.w.work, "cat-file", "commit", "HEAD")
        self.assertIn(" +0000\n", raw)
        odd = git(self.w.work, "hash-object", "-t", "commit", "-w", "--stdin", "--literally",
                  stdin=(raw.replace(" +0000\n", " +2400\n") + "\n").encode())
        git(self.w.work, "reset", "-q", "--hard", odd)
        git(self.w.work, "push", "-q", str(self.w.origin), "HEAD:main")
        git(self.w.work, "tag", "v0.1.0")
        git(self.w.work, "push", "-q", str(self.w.origin), "refs/tags/v0.1.0")
        self.stays(DAY0 + 6 * DAY, None)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")


class Tags(Story):
    def test_an_annotated_tag_is_read_by_the_commit_it_names(self):
        self.w.cut("v0.1.0", DAY0, {"engine/rules/core.md": b"Core rules, v0.1.0.\n"})
        self.w.land("v0.1.0", DAY0, annotated=True)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")

    def test_tags_that_are_no_releases_are_not_looked_at(self):
        self.w.publish("v0.1.0", DAY0)
        for name in ("v1.0", "latest", "v01.2.3", "v0.1.0-rc1", "stable", "v9.9.9.9"):
            git(self.w.work, "tag", name)
            git(self.w.work, "push", "-q", str(self.w.origin), f"refs/tags/{name}")
        out = self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        self.assertNotIn("finding", out)

    def test_releases_cut_before_the_index_existed_are_passed_over_quietly_until_the_first_with_one(self):
        self.w.factory.commit({topics.TOPICS: None})
        files = release.release_files(self.w.factory.path, "main")
        for n in range(2):                                         # as v0.1.12 and before: no topics, no index
            data = release.manifest(files, f"v0.0.{n}", self.w.factory.base, DAY0)
            self.assertNotIn("index", data)
            for path, content in files.items():
                (self.w.work / "files" / path).parent.mkdir(parents=True, exist_ok=True)
                (self.w.work / "files" / path).write_bytes(content)
            (self.w.work / "release-manifest.json").write_text(release.dumps(data), encoding="utf-8")
            self.w.land(f"v0.0.{n}", DAY0)
        out = self.stays(DAY0 + 30 * DAY, None)
        self.assertIn("STABLE note: v0.0.1 was cut before the topic index existed", out)
        self.assertNotIn("finding", out)
        self.w.factory.commit(topics_of("core.md"))
        self.w.publish("v0.1.0", DAY0 + 30 * DAY)
        out = self.moved(DAY0 + 37 * DAY, to="v0.1.0")
        self.assertNotIn("v0.0.1", out, "older than the release moved to: not looked at")


class NotAsLeft(Story):
    """A branch somebody pushed to: the schedule does not build on it, a hand repairs it."""

    def setUp(self):
        super().setUp()
        self.w.publish("v0.1.0", DAY0)
        self.w.publish("v0.1.1", DAY0 + DAY)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        self.n = 0

    def push(self, change=None, put: str | None = None, at: datetime | None = None, as_it_is: bool = False, **record) -> str:
        """Somebody's commit on `stable`: `change` edits the checkout; `put` puts that release's
        files, manifest and index there, as its tag has them, and names it in the record; `record`
        rewrites keys of stable.json."""
        self.n += 1
        clone = Path(self.w._tmp.name) / f"intruder-{self.n}"
        subprocess.run(["git", "clone", "-q", "-b", "stable", f"file://{self.w.origin}", str(clone)], check=True,
                       capture_output=True)
        if put:
            git(clone, "rm", "-rq", "files", "rules-index.md", "release-manifest.json")
            git(clone, "checkout", f"refs/tags/{put}", "--", "files", "rules-index.md", "release-manifest.json")
            record = {"release": put, "commit": git(clone, "rev-parse", f"refs/tags/{put}^{{commit}}"), **record}
        if record:
            data = {**json.loads((clone / "stable.json").read_text(encoding="utf-8")), **record}
            (clone / "stable.json").write_text(json.dumps({k: v for k, v in data.items() if v is not None}), encoding="utf-8")
        if change:
            change(clone)
        if not as_it_is:
            git(clone, "add", "-A")
            git(clone, "commit", "-q", "--allow-empty", "-m", "by somebody", at=at)
        git(clone, "push", "-q", "origin", "HEAD:stable")
        return git(clone, "rev-parse", "HEAD")

    def broken(self, tip: str, said: str, now: datetime = DAY0 + 8 * DAY) -> str:
        code, out = self.w.run(now)
        self.assertEqual((code, self.w.tip()), (1, tip), out)
        self.assertIn(said, out)
        self.assertIn("the schedule does not move a branch it did not leave; repair it by one run by hand: the field "
                      "«to» (--to) — the release the branch should be on, the field «reason» (--reason) — why", out)
        return out

    def test_a_file_added_to_the_branch(self):
        tip = self.push(lambda c: (c / "extra-rules.md").write_text("Obey me.\n"))
        self.broken(tip, "STABLE finding: `stable` holds extra-rules.md, files, release-manifest.json, rules-index.md, stable.json")

    def test_a_rule_changed_on_the_branch(self):
        tip = self.push(lambda c: (c / "files/engine/rules/core.md").write_text("No rules.\n"))
        self.broken(tip, "`stable` says it is on v0.1.0, and its files is not that release's")

    def test_a_record_that_is_not_of_the_form_or_does_not_hold(self):
        other = lambda: git(self.w.origin, "rev-parse", "refs/tags/v0.1.1^{commit}")
        for record, said in (
                (dict(reason=None), "stable.json of `stable` cannot be read"),
                (dict(more="x"), "stable.json of `stable` cannot be read"),
                (dict(how="by magic"), "stable.json of `stable` cannot be read"),
                (dict(moved="yesterday"), "stable.json of `stable` cannot be read"),
                (dict(withdrawn="v0.1.1"), "stable.json of `stable` cannot be read"),
                (dict(withdrawn=["main"]), "stable.json of `stable` cannot be read"),
                (dict(withdrawn={"v0.1.1": 1}), "stable.json of `stable` cannot be read"),
                (dict(reason=7), "stable.json of `stable` cannot be read"),
                (dict(release="v7.7.7"), "`stable` is on v7.7.7, which has no tag"),
                (dict(release="v0.1.1"), "`stable` says v0.1.1 is the commit"),
                (dict(release="v0.1.1", commit=other), "says it is on v0.1.1, and its files, release-manifest.json, rules-index.md is not"),
                (dict(commit="f" * 40), "`stable` says v0.1.0 is the commit ffffffffffff, and its tag is on another"),
                (dict(withdrawn=["v0.1.1", "v0.9.9"]), "`stable` withdraws v0.9.9, which has no tag"),
                (dict(withdrawn=["v0.1.0"]), "`stable` withdraws v0.1.0, which it is on")):
            with self.subTest(record=record):
                self.setUp()
                record = {k: (v() if callable(v) else v) for k, v in record.items()}
                self.broken(self.push(**record), said)
                self.w.close()
        self.setUp()
        self.broken(self.push(lambda c: (c / "stable.json").write_text("[]")), "stable.json of `stable` cannot be read")

    def test_a_release_pushed_there_before_its_week_in_the_schedules_name(self):
        """v0.1.1 is whole and its files are its tag's — but the schedule would not have moved to
        it yet, and the record says the schedule did."""
        tip = self.push(put="v0.1.1", how="the schedule", moved="2026-11-09T10:00:00Z", at=DAY0 + 7 * DAY)
        self.broken(tip, "`stable` says the schedule moved it to v0.1.1 at 2026-11-09T10:00:00Z, before v0.1.1 was due "
                         "at 2026-11-10T09:00:00Z", now=DAY0 + 7 * DAY + timedelta(hours=2))

    def test_a_move_in_the_schedules_name_dated_ahead_or_on_what_is_no_move_or_with_another_list(self):
        """Each is a push the record's own words give away: a time still to come (found by the
        second adversarial reading — the record's date was trusted, so a release pushed there a
        day early with its due time written in passed), a commit under it that is no move, and
        a newer release with the withdrawn list emptied."""
        due = "2026-11-10T09:00:00Z"
        tip = self.push(put="v0.1.1", how="the schedule", moved=due)
        self.broken(tip, f"`stable` says it was moved to v0.1.1 at {due}, a time still to come", now=DAY0 + 7 * DAY + timedelta(hours=2))
        self.setUp()
        self.push(lambda c: (c / "NOTE.md").write_text("a note\n"))
        tip = self.push(lambda c: (c / "NOTE.md").unlink(), put="v0.1.1", how="the schedule", moved=due)
        self.broken(tip, "`stable` says the schedule moved it on top of a commit that is not a move", now=DAY0 + 9 * DAY)
        self.setUp()
        self.w.hand(DAY0 + 7 * DAY + timedelta(hours=1), "v0.1.0", "v0.1.1 is not to go out")
        self.w.publish("v0.1.2", DAY0 + 2 * DAY)
        tip = self.push(put="v0.1.2", how="the schedule", moved="2026-11-11T09:00:00Z", withdrawn=[])
        self.broken(tip, "`stable` says the schedule moved it from v0.1.0 to v0.1.2, which the schedule does not do",
                    now=DAY0 + 10 * DAY)

    def test_a_withdrawn_release_let_back_in_or_a_move_backwards_in_the_schedules_name(self):
        self.moved(DAY0 + 8 * DAY, to="v0.1.1")
        self.w.hand(DAY0 + 9 * DAY, "v0.1.0", "v0.1.1 is wrong")
        self.assertEqual(self.w.record()["withdrawn"], ["v0.1.1"])
        tip = self.push(withdrawn=[], how="the schedule")
        self.broken(tip, "`stable` says the schedule moved it from v0.1.0 to v0.1.0, which the schedule does not do",
                    now=DAY0 + 10 * DAY)

    def test_a_branch_put_on_a_tag_that_was_never_merged(self):
        self.w.cut("v0.2.0", DAY0 + 2 * DAY, {"engine/rules/core.md": b"Core rules, never reviewed.\n"})
        git(self.w.work, "checkout", "-q", "-b", "side")
        git(self.w.work, "add", "-A")
        git(self.w.work, "commit", "-q", "-m", "Release v0.2.0, on a branch")
        git(self.w.work, "tag", "v0.2.0")
        git(self.w.work, "push", "-q", str(self.w.origin), "refs/tags/v0.2.0")
        tip = self.push(put="v0.2.0", how="by hand", reason="trust me")
        self.broken(tip, "`stable` is on v0.2.0, which is not a whole release: the tag v0.2.0 is not on main")
        self.assertEqual(self.w.at_branch("files/engine/rules/core.md"), "Core rules, never reviewed.")
        code, out = self.w.hand(DAY0 + 9 * DAY, "v0.1.0", "somebody pushed an unmerged release")
        self.assertEqual((code, self.w.on(), self.w.at_branch("files/engine/rules/core.md")),
                         (0, "v0.1.0", "Core rules, v0.1.0."), out)

    def test_a_merge_on_the_branch(self):
        def merge(clone):
            git(clone, "merge", "-q", "--no-ff", "--allow-unrelated-histories", "-s", "ours", "-m", "merged", "origin/main")
        tip = self.push(merge, as_it_is=True)
        self.assertEqual(len(git(self.w.origin, "rev-list", "--parents", "-n", "1", tip).split()), 3)
        self.broken(tip, "`stable` is on a merge")

    def test_what_a_pushed_record_puts_on_a_hand_cannot_be_told_from_a_hand(self):
        """The limit, on record: a whole release pushed there with a record that says «by hand» is
        what a move by hand leaves. Who may push to the branch is the ruleset's to hold."""
        tip = self.push(put="v0.1.1", how="by hand", reason="not the workflow")
        code, out = self.w.run(DAY0 + 7 * DAY + timedelta(hours=1))
        self.assertEqual((code, self.w.tip(), self.w.on()), (0, tip, "v0.1.1"), out)

    def test_a_hand_repairs_it_on_top_of_what_was_pushed_and_nothing_newer_goes_out_by_itself(self):
        tip = self.push(lambda c: (c / "files/engine/rules/core.md").write_text("No rules.\n"))
        code, out = self.w.hand(DAY0 + 8 * DAY, "v0.1.0", "somebody pushed to stable")
        self.assertEqual((code, self.w.on(), self.w.record()["withdrawn"]), (0, "v0.1.0", ["v0.1.1"]), out)
        self.assertIn("STABLE note: `stable` says it is on v0.1.0, and its files is not that release's — not as this "
                      "code left it; the move asked for repairs it if it is made, and withdraws every release newer "
                      "than v0.1.0", out)
        self.assertIn("STABLE moved: a branch this code did not leave -> v0.1.0 (by hand)", out)
        self.assertEqual(git(self.w.origin, "rev-parse", "refs/heads/stable^"), tip, "no rewrite, even of that")
        self.assertEqual(self.w.at_branch("files/engine/rules/core.md"), "Core rules, v0.1.0.")
        self.stays(DAY0 + 9 * DAY, "v0.1.0")
        code, out = self.w.hand(DAY0 + 9 * DAY, "v0.1.1", "v0.1.1 is whole and due: on")
        self.assertEqual((code, self.w.on(), self.w.record()["withdrawn"]), (0, "v0.1.1", []), out)

    def test_a_repair_back_from_a_release_somebody_put_there_early_withdraws_it(self):
        """Found by the second adversarial reading: taken for a repair to «the same» release, the
        move withdrew nothing, and the schedule moved to that release the next day."""
        self.push(lambda c: (c / "stable.json").write_text("{}"), put="v0.1.1")
        code, out = self.w.hand(DAY0 + 7 * DAY + timedelta(hours=1), "v0.1.0", "v0.1.1 was pushed early: back")
        self.assertEqual((code, self.w.on(), self.w.record()["withdrawn"]), (0, "v0.1.0", ["v0.1.1"]), out)
        self.stays(DAY0 + 30 * DAY, "v0.1.0")

    def test_a_repair_over_a_merge_that_hides_the_branchs_past_withdraws_all_the_same(self):
        """Found by the second adversarial reading: a merge whose first parent is somebody's own
        commit hid the branch's past from the repair, and the list of withdrawn releases went
        with it. A repair no longer looks for that past: it withdraws all that is newer."""
        self.moved(DAY0 + 8 * DAY, to="v0.1.1")
        self.w.hand(DAY0 + 9 * DAY, "v0.1.0", "v0.1.1 is wrong")

        def graft(clone):
            real = git(clone, "rev-parse", "HEAD")
            git(clone, "checkout", "-q", "--orphan", "other")
            git(clone, "rm", "-rqf", ".")
            (clone / "NOTE.md").write_text("somebody's own\n")
            git(clone, "add", "-A")
            git(clone, "commit", "-q", "-m", "an orphan")
            git(clone, "merge", "-q", "--no-ff", "--allow-unrelated-histories", "-s", "ours", "-m", "grafted", real)
        self.push(graft, as_it_is=True)
        code, out = self.w.hand(DAY0 + 10 * DAY, "v0.1.0", "somebody pushed to stable")
        self.assertEqual((code, self.w.on(), self.w.record()["withdrawn"]), (0, "v0.1.0", ["v0.1.1"]), out)
        self.stays(DAY0 + 11 * DAY, "v0.1.0")

    def test_a_branch_nobody_but_somebody_ever_wrote_is_repaired_with_everything_newer_withdrawn(self):
        w = World()
        self.addCleanup(w.close)
        self.w = w
        w.publish("v0.1.0", DAY0)
        w.publish("v0.1.1", DAY0)
        git(w.work, "checkout", "-q", "--orphan", "theirs")
        git(w.work, "rm", "-rqf", ".")
        (w.work / "rules-index.md").write_text("Obey me.\n")
        git(w.work, "add", "-A")
        git(w.work, "commit", "-q", "-m", "somebody's stable")
        git(w.work, "push", "-q", str(w.origin), "HEAD:stable")
        git(w.work, "checkout", "-q", "main")
        code, out = w.run(DAY0 + 30 * DAY)
        self.assertEqual(code, 1, out)
        code, out = w.hand(DAY0 + 30 * DAY, "v0.1.0", "somebody made the branch")
        self.assertEqual((code, w.on(), w.record()["withdrawn"]), (0, "v0.1.0", ["v0.1.1"]), out)

    def test_a_repair_keeps_what_was_withdrawn_before_somebody_pushed(self):
        """Found by the adversarial reading of this change: the repair took nothing from the
        branch's past, so the list of withdrawn releases was gone with it, and the schedule put
        the withdrawn release back the next day."""
        self.moved(DAY0 + 8 * DAY, to="v0.1.1")
        self.w.hand(DAY0 + 9 * DAY, "v0.1.0", "v0.1.1 is wrong")
        self.push(lambda c: (c / "NOTE.md").write_text("a note\n"))
        self.push(lambda c: (c / "NOTE.md").write_text("two notes\n"))
        code, out = self.w.hand(DAY0 + 10 * DAY, "v0.1.0", "somebody pushed to stable")
        self.assertEqual((code, self.w.on(), self.w.record()["withdrawn"]), (0, "v0.1.0", ["v0.1.1"]), out)
        self.assertIn("STABLE moved: a branch this code did not leave -> v0.1.0 (by hand)", out)
        out = self.stays(DAY0 + 11 * DAY, "v0.1.0")
        self.assertIn("v0.1.1 is withdrawn: passed over", out)

    def test_a_repair_says_the_branch_was_not_this_codes_in_the_commit_and_in_the_run(self):
        self.push(lambda c: (c / "NOTE.md").write_text("a note\n"))
        tip = self.w.tip()
        code, out = self.w.run(DAY0 + 8 * DAY, "--to", "v0.1.0", "--reason", "somebody pushed to stable", "--dry-run")
        self.assertEqual((code, self.w.tip()), (0, tip), out)
        self.assertIn("STABLE would move: a branch this code did not leave -> v0.1.0 (by hand); withdrawn: v0.1.1", out)
        note = ("STABLE note: `stable` holds NOTE.md, files, release-manifest.json, rules-index.md, stable.json, not "
                "files, release-manifest.json, rules-index.md, stable.json — not as this code left it; the move asked "
                "for repairs it if it is made, and withdraws every release newer than ")
        self.assertIn(note + "v0.1.0", out)
        self.assertNotIn("STABLE moved", out, "a dry run repaired nothing, and says nothing was moved")
        code, out = self.w.hand(DAY0 + 8 * DAY, "v0.9.9", "to a release that is not there")
        self.assertEqual((code, self.w.tip()), (1, tip), out)
        self.assertEqual(out.splitlines(), [note + "v0.9.9", "STABLE finding: not moved to v0.9.9: v0.9.9 has no tag in "
                                                             "this repository"], "a refused move repaired nothing")
        for dies in (stable.Failed("git push --quiet origin: refused by a rule"), TypeError("bad")):
            with self.subTest(dies=dies):       # found by the reading of what was rewritten: «repaired», then a refused push
                with mock.patch.object(stable, "move", side_effect=dies):
                    code, out = self.w.hand(DAY0 + 8 * DAY, "v0.1.0", "somebody pushed to stable")
                self.assertEqual((code, self.w.tip()), (2, tip), out)
                self.assertEqual(out.splitlines()[0], note + "v0.1.0")
                self.assertTrue(out.splitlines()[1].startswith("ERROR "), out)
                self.assertEqual(len(out.splitlines()), 2, out)
        with mock.patch.object(stable, "by_hand", side_effect=stable.Failed("git cat-file: gone")):
            code, out = self.w.hand(DAY0 + 8 * DAY, "v0.1.0", "somebody pushed to stable")
        self.assertEqual((code, out.splitlines()[0]), (2, note + "v0.1.0"), "the run says the branch is not its own even "
                                                                              "when it fails before the move")
        code, out = self.w.hand(DAY0 + 8 * DAY, "v0.1.0", "somebody pushed to stable")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.w.moves()[-1], "stable: a branch this code did not leave -> v0.1.0 (by hand)")
        self.assertNotIn("none -> v0.1.0", out)

    def test_the_first_move_of_all_still_comes_from_none_by_hand_too(self):
        w = World()
        self.addCleanup(w.close)
        w.publish("v0.1.0", DAY0)
        code, out = w.hand(DAY0, "v0.1.0", "cannot wait its week")
        self.assertEqual(code, 0, out)
        self.assertEqual(w.moves(), ["stable: none -> v0.1.0 (by hand)"])

    def test_a_repair_back_to_an_older_release_withdraws_the_newer_ones(self):
        self.moved(DAY0 + 8 * DAY, to="v0.1.1")
        self.push(lambda c: (c / "NOTE.md").write_text("a note\n"))
        code, out = self.w.hand(DAY0 + 9 * DAY, "v0.1.0", "back, and repaired")
        self.assertEqual((code, self.w.on(), self.w.record()["withdrawn"]), (0, "v0.1.0", ["v0.1.1"]), out)
        self.stays(DAY0 + 30 * DAY, "v0.1.0")

    def test_a_branch_that_moved_while_the_run_was_reading_is_not_overwritten(self):
        real = stable.move
        pushed = []

        def late(*args, **kw):
            pushed.append(self.push(withdrawn=["v0.1.1"], how="by hand", reason="in between"))
            return real(*args, **kw)

        with mock.patch.object(stable, "move", late):
            code, out = self.w.run(DAY0 + 8 * DAY)
        self.assertEqual(code, 2, out)
        self.assertIn("ERROR git push", out)
        self.assertEqual(self.w.tip(), pushed[0], "the other push stands")


class OneLine(Story):
    """Every line the run prints is its own: a release's words, a branch's file names and git's
    own complaints are said on the line that names them, never on a line of their own."""

    FORGED = "STABLE moved: none -> v9.9.9 (the schedule), commit 0; withdrawn: none"

    def setUp(self):
        super().setUp()
        self.w.publish("v0.1.0", DAY0)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")

    def lines(self, out: str, start: str) -> list[str]:
        return [line for line in out.splitlines() if line.startswith(start)]

    def test_a_path_with_a_line_break_in_a_manifest_does_not_start_a_line_of_its_own(self):
        self.w.cut("v0.1.1", DAY0 + 8 * DAY, {"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
        path = self.w.work / "release-manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["files"][f"engine/a\n{self.FORGED}\r\u2028::error::x"] = "0" * 64
        path.write_text(release.dumps(data), encoding="utf-8")
        self.w.land("v0.1.1", DAY0 + 8 * DAY)
        for args in ((), ("--to", "v0.1.1", "--reason", "by hand all the same")):
            with self.subTest(args=args):
                code, out = self.w.run(DAY0 + 20 * DAY, *args)
                self.assertEqual((code, self.w.on()), (1, "v0.1.0"), out)
                self.assertEqual(self.lines(out, "STABLE moved"), [], out)
                self.assertEqual(self.lines(out, "::"), [], out)
                found = self.lines(out, "STABLE finding: ")
                self.assertEqual(len(found), 1, out)
                self.assertIn("is not its manifest's files: engine/a\\nSTABLE moved: none -> v9.9.9", found[0])
                self.assertIn("\\r\\u2028::error::x", found[0])

    def test_a_file_name_with_a_line_break_on_the_branch_does_not_either(self):
        clone = Path(self.w._tmp.name) / "intruder"
        subprocess.run(["git", "clone", "-q", "-b", "stable", f"file://{self.w.origin}", str(clone)], check=True,
                       capture_output=True)
        (clone / f"a\n{self.FORGED}").write_text("x\n", encoding="utf-8")
        git(clone, "add", "-A")
        git(clone, "commit", "-q", "-m", "by somebody")
        git(clone, "push", "-q", "origin", "HEAD:stable")
        code, out = self.w.run(DAY0 + 8 * DAY)
        self.assertEqual(code, 1, out)
        self.assertEqual(self.lines(out, "STABLE moved"), [], out)
        self.assertEqual(len(self.lines(out, "STABLE finding: `stable` holds a\\nSTABLE moved")), 1, out)
        code, out = self.w.hand(DAY0 + 8 * DAY, "v0.1.0", "somebody pushed to stable")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.lines(out, "STABLE moved")), 1, out)
        self.assertEqual(len(self.lines(out, "STABLE note: `stable` holds a\\nSTABLE moved")), 1, out)

    def test_what_the_command_line_is_refused_for_is_one_line_too(self):
        """argparse repeats an argument it does not know as it came (found by the reading of the
        documents against the code). The workflow never passes one; whoever runs the file may."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stopped:
            stable.main(["--repository", REPOSITORY, "--no-such=a\nSTABLE finding: made up"])
        self.assertEqual((stopped.exception.code, out.getvalue()), (2, ""), err.getvalue())
        self.assertEqual(err.getvalue(), "ERROR unrecognized arguments: --no-such=a\\nSTABLE finding: made up\n")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stopped:
            stable.main([])
        self.assertEqual(stopped.exception.code, 2)

    def test_a_failed_run_ends_with_2_whichever_stream_is_closed(self):
        """Found by the reading of what was rewritten: saying the error on a stream that is not
        there raised, and the run ended with Python's 1 — the code of a finding."""
        clone = Path(self.w._tmp.name) / "closed"
        subprocess.run(["git", "clone", "-q", f"file://{self.w.origin}", str(clone)], check=True, capture_output=True)
        git(clone, "remote", "set-url", "origin", str(Path(self.w._tmp.name) / "gone.git"))
        run = ["python3", "-I", "-B", stable.__file__, "--repo", str(clone), "--repository", REPOSITORY]
        for closed in (">&-", "2>&-", ">&- 2>&-"):
            for args, name in (([], "the origin is gone"), (["--no-such"], "an argument it does not know")):
                with self.subTest(closed=closed, name=name):
                    proc = subprocess.run(["bash", "-c", f'exec "$@" {closed}', "bash", *run, *args], capture_output=True, text=True)
                    self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                    self.assertNotIn("Traceback", proc.stdout + proc.stderr)
        proc = subprocess.run([*run], capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stdout), (2, ""), proc.stderr)
        self.assertEqual(len(proc.stderr.splitlines()), 1, proc.stderr)

    def test_what_git_says_in_several_lines_is_one_line_of_the_error(self):
        clone = Path(self.w._tmp.name) / "no-origin"
        subprocess.run(["git", "clone", "-q", f"file://{self.w.origin}", str(clone)], check=True, capture_output=True)
        git(clone, "remote", "set-url", "origin", str(Path(self.w._tmp.name) / "gone.git"))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = stable.main(["--repo", str(clone), "--repository", REPOSITORY], now=DAY0 + 8 * DAY)
        self.assertEqual((code, out.getvalue()), (2, ""), err.getvalue())
        self.assertEqual(len(err.getvalue().splitlines()), 1, err.getvalue())
        self.assertRegex(err.getvalue(), r"^ERROR git ls-remote origin refs/tags/v\*: .+\\n")


class Died(Story):
    """A run that died judged nothing: it ends with 2, the code of a run that failed, never with
    1, the code of a finding — the workflow tells a person to withdraw a release on a 1."""

    def test_a_run_that_dies_on_what_the_code_did_not_expect_is_a_failed_run_not_a_finding(self):
        self.w.publish("v0.1.0", DAY0)
        for dies in ("origin", "state", "by_schedule", "move"):
            with self.subTest(dies=dies):
                boom = RuntimeError("no such thing\nSTABLE finding: made up")
                with mock.patch.object(stable, dies, side_effect=boom):
                    code, out = self.w.run(DAY0 + 7 * DAY)
                self.assertEqual((code, self.w.tip()), (2, None), out)
                self.assertEqual([line for line in out.splitlines() if line.startswith("STABLE finding")], [], out)
                self.assertRegex(out, r"ERROR this code died on what it did not expect \(RuntimeError: no such thing"
                                      r"\\nSTABLE finding: made up, at \S+\.py line \d+\): the run failed")
                self.assertEqual(len([line for line in out.splitlines() if line.startswith("ERROR")]), 1, out)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")         # and the next run, whole again, goes on

    def test_started_as_its_workflow_starts_it_such_a_run_ends_with_2_where_python_alone_would_end_with_1(self):
        self.w.publish("v0.1.0", DAY0)
        source = Path(stable.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("\ndef by_schedule("), 1)
        copy = Path(self.w._tmp.name) / "stable.py"
        copy.write_text(source.replace("\ndef by_schedule(", "\ndef by_schedule(*a, **k):\n    raise KeyError('gone')\n\n\n"
                                                             "def _by_schedule("), encoding="utf-8")
        clone = Path(self.w._tmp.name) / "as-the-workflow"
        subprocess.run(["git", "clone", "-q", f"file://{self.w.origin}", str(clone)], check=True, capture_output=True)
        proc = subprocess.run(["python3", "-I", "-B", str(copy), "--repo", str(clone), "--repository", REPOSITORY],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertRegex(proc.stderr, r"ERROR this code died on what it did not expect \(KeyError: 'gone', at "
                                      r"stable\.py line \d+\)")
        self.assertEqual(self.w.tip(), None)

    def test_in_the_one_log_the_workflow_keeps_the_error_stands_below_what_the_run_said_before_it(self):
        """The workflow sends both streams to one file. A stream to a file keeps its lines until
        it is full, the error stream does not: unflushed, the error stood above the finding it
        calls «said before this line» (found by the adversarial reading of this change)."""
        self.w.publish("v0.1.0", DAY0)
        self.w.cut("v0.1.1", DAY0, {"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
        (self.w.work / "files/engine/rules/core.md").write_bytes(b"No rules.\n")
        self.w.land("v0.1.1", DAY0)
        source = Path(stable.__file__).read_text(encoding="utf-8")
        for name, dies in (("died", "raise KeyError('gone')"), ("failed", "raise Failed('the push was refused')")):
            with self.subTest(name=name):
                self.assertEqual(source.count("\ndef move("), 1)
                copy = Path(self.w._tmp.name) / f"stable-{name}.py"
                copy.write_text(source.replace("\ndef move(", f"\ndef move(*a, **k):\n    {dies}\n\n\ndef _move("),
                                encoding="utf-8")
                clone = Path(self.w._tmp.name) / f"as-the-workflow-{name}"
                subprocess.run(["git", "clone", "-q", f"file://{self.w.origin}", str(clone)], check=True, capture_output=True)
                log = Path(self.w._tmp.name) / f"log-{name}"
                with open(log, "wb") as said:               # > "$said" 2>&1, as move-stable.sh has it
                    proc = subprocess.run(["python3", "-I", "-B", str(copy), "--repo", str(clone), "--repository", REPOSITORY],
                                          stdout=said, stderr=subprocess.STDOUT, env={**os.environ, "TZ": "UTC"})
                lines = log.read_text(encoding="utf-8").splitlines()
                self.assertEqual(proc.returncode, 2, lines)
                self.assertEqual([line.split(":")[0].split(" ")[0] for line in lines], ["STABLE", "ERROR"], lines)
                self.assertTrue(lines[0].startswith("STABLE finding: files/ of v0.1.1 is not its manifest's files"), lines)
                self.assertEqual(self.w.tip(), None)

    def test_a_finding_said_before_the_run_died_does_not_make_it_a_finding(self):
        self.w.publish("v0.1.0", DAY0)
        self.w.cut("v0.1.1", DAY0 + DAY, {"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
        (self.w.work / "files/engine/rules/core.md").write_bytes(b"No rules.\n")
        self.w.land("v0.1.1", DAY0 + DAY)
        with mock.patch.object(stable, "move", side_effect=TypeError("bad")):
            code, out = self.w.run(DAY0 + 8 * DAY)
        self.assertEqual((code, self.w.tip()), (2, None), out)
        self.assertIn("STABLE finding: files/ of v0.1.1 is not its manifest's files", out)
        self.assertIn("what it said before this line is not its answer", out)


class IndexTexts(Story):
    """A release keeps the words its index was cut in. The code that moves `stable` builds the
    index again and wants it byte for byte, so the words of every release that is out stay in
    the code for good; new words come as a new text, beside the old."""

    SOURCE = b'''about = "Rules for what is about her. A general question needs no rules."
min_engine = "v0.1.0"
bot_only = ["bot.md"]

[[topic]]
name = "health"
when = "health, a symptom, a medicine"
files = ["core.md", "health.md", "notion.md"]

[[topic]]
name = "a purchase"
when = "before paying"
files = ["core.md", "shopping.md"]

[cites]
'''
    # A second source, for what the first does not reach: blanks around `about`, one topic of one
    # file, letters beyond ASCII, a minimal engine that is the release itself.
    SOURCE_2 = '''about = "  Rules for what is about her — her café, her naïve question, 東京. A general question needs none.  "
min_engine = "v0.1.3"
bot_only = []

[[topic]]
name = "notes 2"
when = "a note, an idea — «keep this»"
files = ["notion.md"]

[cites]
'''.encode()
    # The sha256 of each text of stable.INDEX_TEXTS, in their order: of what it builds from SOURCE
    # for FORMERLY — the name the repository of releases had when the lines below were written,
    # kept so that they stay what they were — and v0.1.3, then from SOURCE_2 for another repository and v10.20.30, one
    # after the other. From the first release cut with an index on, a line here is never changed
    # and never taken out: the text it holds is the index of releases that are out. New words — a
    # new function at the end of INDEX_TEXTS and a new line here. No check refuses a change of a
    # text together with its line: that is for the reader of the pull request to see.
    FINGERPRINTS = (
        "f6d2f807250423b4a6ee66c7c643115f433c34a366ba203f7dd5334b67adf659",
        "bfe8428990328b31551863bb69618a1fd6d0e3a2595fcf3e3136cbe7a12bced7",
    )

    # The texts as this code holds them, before any test puts others in their place: a release
    # published outside a mock is in the last of them, the words of today.
    TODAY = stable.INDEX_TEXTS

    @classmethod
    def fingerprint(cls, build) -> str:
        built = (build(stable.tomllib.loads(cls.SOURCE.decode()), FORMERLY, "v0.1.3")
                 + build(stable.tomllib.loads(cls.SOURCE_2.decode()), "some-one/rules.of_hers", "v10.20.30"))
        return release.hashlib.sha256(built.encode()).hexdigest()

    @staticmethod
    def reworded(data, repository, tag):
        """The words of a later day: one word of the text another."""
        text = stable._index_1(data, repository, tag)
        assert "Not at the branch this index came" in text
        return text.replace("Not at the branch this index came", "Never at the branch this index came")

    def test_every_text_an_index_was_published_in_is_still_what_it_was(self):
        self.assertEqual(len(stable.INDEX_TEXTS), len(self.FINGERPRINTS),
                         "a text of INDEX_TEXTS added — add its fingerprint — or taken out: a text taken out refuses "
                         "every release cut with it, for good")
        data = stable.tomllib.loads(self.SOURCE.decode())
        for n, (build, fingerprint) in enumerate(zip(stable.INDEX_TEXTS, self.FINGERPRINTS), start=1):
            with self.subTest(text=n):
                self.assertEqual(self.fingerprint(build), fingerprint,
                                 f"the index text {n} is not what it was published as: every release cut with it would "
                                 f"be refused, the branch `stable` red with no way back. Leave it as it was and add "
                                 f"the new words as a new text at the end of stable.INDEX_TEXTS")
        self.assertEqual(stable.index_text(self.SOURCE, REPOSITORY, "v0.1.3"),
                         stable.INDEX_TEXTS[-1](data, REPOSITORY, "v0.1.3"), "the cut publishes the last text")

    def test_a_release_keeps_the_words_it_was_cut_with_when_the_words_change(self):
        self.w.publish("v0.1.0", DAY0)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        with mock.patch.object(stable, "INDEX_TEXTS", (*self.TODAY, self.reworded)):
            self.w.publish("v0.1.1", DAY0 + 8 * DAY)
            self.assertIn("Never at the branch", git(self.w.origin, "show", "refs/tags/v0.1.1:rules-index.md"))
            self.assertIn("Not at the branch", git(self.w.origin, "show", "refs/tags/v0.1.0:rules-index.md"))
            out = self.stays(DAY0 + 9 * DAY, "v0.1.0")          # the branch as it stands is still this code's
            self.assertNotIn("finding", out)
            self.moved(DAY0 + 15 * DAY, to="v0.1.1")
            self.assertIn("Never at the branch", self.w.at_branch("rules-index.md"))
            code, out = self.w.hand(DAY0 + 16 * DAY, "v0.1.0", "back, to a release in the words before")
            self.assertEqual((code, self.w.on(), self.w.record()["withdrawn"]), (0, "v0.1.0", ["v0.1.1"]), out)
            self.assertIn("Not at the branch", self.w.at_branch("rules-index.md"))
            self.stays(DAY0 + 17 * DAY, "v0.1.0")

    def test_words_changed_in_place_strand_the_branch_which_is_why_a_text_is_never_edited(self):
        """What this change is against, on record: with the one text edited — not a second one
        added — the release the branch is on is no longer whole, the night is red, and a hand
        cannot move to it or back to it either."""
        self.w.publish("v0.1.0", DAY0)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        tip = self.w.tip()
        with mock.patch.object(stable, "INDEX_TEXTS", (self.reworded,)):
            code, out = self.w.run(DAY0 + 8 * DAY)
            self.assertEqual((code, self.w.tip()), (1, tip), out)
            self.assertIn("STABLE finding: `stable` is on v0.1.0, which is not a whole release: rules-index.md of v0.1.0 "
                          "is not the index of its own topics", out)
            code, out = self.w.hand(DAY0 + 8 * DAY, "v0.1.0", "stay")
            self.assertEqual((code, self.w.tip()), (1, tip), out)
            self.assertIn("STABLE finding: not moved to v0.1.0", out)
        self.stays(DAY0 + 8 * DAY, "v0.1.0")

    def test_an_index_in_words_this_code_never_published_is_refused_whatever_texts_it_holds(self):
        with mock.patch.object(stable, "INDEX_TEXTS", (stable._index_1, self.reworded)):
            self.w.publish("v0.1.0", DAY0)
            self.w.cut("v0.1.1", DAY0 + DAY, {"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
            text = (self.w.work / "rules-index.md").read_text(encoding="utf-8").replace("Never at the branch", "Always at the branch")
            (self.w.work / "rules-index.md").write_text(text, encoding="utf-8")
            path = self.w.work / "release-manifest.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["index"]["sha256"] = release.hashlib.sha256(text.encode()).hexdigest()
            path.write_text(release.dumps(data), encoding="utf-8")
            self.w.land("v0.1.1", DAY0 + DAY)
            code, out = self.w.run(DAY0 + 30 * DAY)
            self.assertEqual((code, self.w.on()), (1, "v0.1.0"), out)
            self.assertIn("STABLE finding: rules-index.md of v0.1.1 is not the index of its own topics", out)

    def test_a_text_that_does_not_take_the_topics_is_passed_over_and_when_none_does_the_newest_says_why(self):
        def wants_more(data, repository, tag):
            raise stable.Refused("the words of today want a key these topics lack")

        def wants_less(data, repository, tag):
            raise stable.Refused("the words before knew no such key")

        first = stable._index_1(stable.tomllib.loads(self.SOURCE.decode()), REPOSITORY, "v0.1.3")
        with mock.patch.object(stable, "INDEX_TEXTS", (stable._index_1, wants_more)):
            self.assertEqual(stable.index_texts(self.SOURCE, REPOSITORY, "v0.1.3"), [first])
            with self.assertRaisesRegex(stable.Refused, "the words of today want a key"):
                stable.index_text(self.SOURCE, REPOSITORY, "v0.1.3")            # the cut writes today's words or none
        with mock.patch.object(stable, "INDEX_TEXTS", (stable._index_1, self.reworded)):
            self.assertEqual(stable.index_texts(self.SOURCE, REPOSITORY, "v0.1.3"),
                             [first.replace("Not at the branch", "Never at the branch"), first], "the newest words first")
        with mock.patch.object(stable, "INDEX_TEXTS", (wants_less, wants_more)):
            with self.assertRaisesRegex(stable.Refused, "the words of today want a key"):
                stable.index_texts(self.SOURCE, REPOSITORY, "v0.1.3")
        def dies(data, repository, tag):
            return data["a key older topics lack"]["x"]

        with mock.patch.object(stable, "INDEX_TEXTS", (stable._index_1, dies)):
            self.assertEqual(stable.index_texts(self.SOURCE, REPOSITORY, "v0.1.3"), [first],
                             "a newer text that dies on older topics does not keep the older words from being tried")
            with self.assertRaisesRegex(stable.Refused, r"is not what an index is written from \(KeyError\)"):
                stable.index_text(self.SOURCE, REPOSITORY, "v0.1.3")
        with mock.patch.object(stable, "INDEX_TEXTS", (dies, stable._index_1)):
            self.assertEqual(stable.index_texts(self.SOURCE, REPOSITORY, "v0.1.3"), [first],
                             "nor does an older text that dies on newer topics refuse the release in today's words")
        with mock.patch.object(stable, "INDEX_TEXTS", (dies, dies)):
            with self.assertRaisesRegex(stable.Refused, r"is not what an index is written from \(KeyError\)"):
                stable.index_texts(self.SOURCE, REPOSITORY, "v0.1.3")
        huge = b"0x" + b"f" * 5000              # a number Python refuses to print: the text's own check dies on it
        for hostile in (self.SOURCE.replace(b'files = ["core.md", "shopping.md"]', b"files = [" + huge + b"]"),
                        self.SOURCE.replace(b'name = "a purchase"', b"name = " + huge)):
            with self.subTest(hostile=hostile[-60:]):
                with self.assertRaises(stable.Refused):
                    stable.index_texts(hostile, REPOSITORY, "v0.1.3")
                with self.assertRaises(stable.Refused):
                    stable.index_text(hostile, REPOSITORY, "v0.1.3")
        for bad in (b"\xff", b"about = [", b"x = " + b"[" * 3000 + b"]" * 3000):
            with self.assertRaisesRegex(stable.Refused, "engine/rules/topics.toml cannot be read"):
                stable.index_texts(bad, REPOSITORY, "v0.1.3")
        with self.assertRaisesRegex(stable.Refused, "is not a repository's owner/name"):
            stable.index_texts(self.SOURCE, "not a repository", "v0.1.3")

    def test_a_newer_text_that_dies_on_the_topics_of_a_release_cut_before_it_does_not_strand_that_release(self):
        """Found by the adversarial reading of this change: a text that died — not refused — on
        older topics ended the search, and the release in the words before was «not whole»."""
        def newer(data, repository, tag):
            return stable._index_1(data, repository, tag) + f"\nSince {data['since']['release']}.\n"

        self.w.publish("v0.1.0", DAY0)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        with mock.patch.object(stable, "INDEX_TEXTS", (*self.TODAY, newer)):
            out = self.stays(DAY0 + 8 * DAY, "v0.1.0")
            self.assertNotIn("finding", out)
            code, out = self.w.hand(DAY0 + 9 * DAY, "v0.1.0", "stay")
            self.assertEqual(code, 0, out)

    def test_topics_a_text_dies_on_are_a_finding_about_the_release_not_a_run_that_died(self):
        self.w.publish("v0.1.0", DAY0)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        self.w.cut("v0.1.1", DAY0 + 8 * DAY, {"engine/rules/core.md": b"Core rules, v0.1.1.\n"})
        path = self.w.work / "files" / topics.TOPICS
        source = path.read_bytes()
        self.assertEqual(source.count(b'files = ["core.md"]'), 1)
        source = source.replace(b'files = ["core.md"]', b"files = [0x" + b"f" * 5000 + b"]")
        path.write_bytes(source)
        manifest = self.w.work / "release-manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["files"][topics.TOPICS] = release.hashlib.sha256(source).hexdigest()
        manifest.write_text(release.dumps(data), encoding="utf-8")
        self.w.land("v0.1.1", DAY0 + 8 * DAY)
        code, out = self.w.run(DAY0 + 30 * DAY)
        self.assertEqual((code, self.w.on()), (1, "v0.1.0"), out)
        self.assertIn("STABLE finding: the topics of v0.1.1 give no index: engine/rules/topics.toml is not what an index "
                      "is written from (ValueError)", out)
        code, out = self.w.hand(DAY0 + 30 * DAY, "v0.1.0", "v0.1.1 is broken for good")
        self.assertEqual((code, self.w.record()["withdrawn"]), (0, ["v0.1.1"]), out)
        self.stays(DAY0 + 31 * DAY, "v0.1.0")

    def test_a_release_cut_in_todays_words_while_the_mover_still_holds_only_the_words_before_is_refused(self):
        """The order the two repositories keep: the copy with the new words comes to the repository
        of releases first (`release cut` refuses while the copy differs). Were a release cut in
        words the copy there does not hold, it would not be moved to."""
        self.w.publish("v0.1.0", DAY0)
        with mock.patch.object(stable, "INDEX_TEXTS", (stable._index_1, self.reworded)):
            self.w.publish("v0.1.1", DAY0 + DAY)
        code, out = self.w.run(DAY0 + 30 * DAY)
        self.assertEqual((code, self.w.on()), (1, "v0.1.0"), out)
        self.assertIn("STABLE finding: rules-index.md of v0.1.1 is not the index of its own topics", out)


class Renamed(Story):
    """The repository of releases takes another name — renamed, or handed to another owner. An
    index names the repository it was cut for and a release keeps its tag, so the releases that are out
    go on naming the old one: the code keeps that name (`stable.FORMER`), or every one of them
    would be «not whole» from that day.

    What these fixtures cannot see: that GitHub answers the old name with the repository's new
    place — for a chat's connector, a raw link, a clone — and for how long."""

    def setUp(self):
        self.w = World(FORMERLY)
        self.addCleanup(self.w.close)
        self.w.publish("v0.1.0", DAY0)
        self.moved(DAY0 + 7 * DAY, to="v0.1.0")
        self.w.publish("v0.1.1", DAY0 + 8 * DAY)
        self.now = DAY0 + 9 * DAY
        # in these stories v0.1.0 and v0.1.1 are the releases cut under the old name
        patch = mock.patch.dict(stable.FORMER, {RENAMED: ((FORMERLY, ("v0.1.0", "v0.1.1")),)})
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_releases_that_are_out_stay_whole_and_the_branch_goes_on_as_before(self):
        self.w.rename(RENAMED)
        out = self.stays(self.now, "v0.1.0")
        self.assertIn("STABLE note: v0.1.1 is due at 2026-11-17T09:00:00Z, not yet", out)
        self.assertNotIn("STABLE finding", out)
        out = self.moved(DAY0 + 15 * DAY, to="v0.1.1")
        self.assertIn("STABLE moved: v0.1.0 -> v0.1.1 (the schedule)", out)
        self.assertIn(f"Read them in the repository `{FORMERLY}` at the tag `v0.1.1`", self.w.at_branch("rules-index.md"),
                      "the release as its tag has it: its index names the repository it was cut for")
        code, out = self.w.hand(DAY0 + 16 * DAY, "v0.1.0", "back, under the new name")
        self.assertEqual((code, self.w.on(), self.w.record()["withdrawn"]), (0, "v0.1.0", ["v0.1.1"]), out)

    def test_the_first_move_of_all_can_come_after_the_rename(self):
        """The repository changed its name before its branch `stable` existed."""
        w = World(FORMERLY)
        self.addCleanup(w.close)
        w.publish("v0.1.0", DAY0)
        w.rename(RENAMED)
        code, out = w.hand(DAY0 + DAY, "v0.1.0", "a test installation cannot wait a week")
        self.assertEqual((code, w.on()), (0, "v0.1.0"), out)
        self.assertIn("STABLE moved: none -> v0.1.0 (by hand)", out)

    def test_a_release_cut_under_the_new_name_follows_those_cut_under_the_old(self):
        self.w.rename(RENAMED, factory=True)
        self.w.publish("v0.1.2", self.now)
        self.assertIn(f"Read them in the repository `{RENAMED}` at the tag `v0.1.2`",
                      git(self.w.origin, "show", "refs/tags/v0.1.2:rules-index.md"))
        self.moved(DAY0 + 15 * DAY, to="v0.1.1")
        self.moved(DAY0 + 16 * DAY, to="v0.1.2")
        self.assertEqual(self.w.moves(), ["stable: none -> v0.1.0 (the schedule)", "stable: v0.1.0 -> v0.1.1 (the schedule)",
                                          "stable: v0.1.1 -> v0.1.2 (the schedule)"])

    def test_without_the_old_name_the_branch_is_on_a_release_it_cannot_stand_on(self):
        """What the name is kept for: taken out, the release the branch is on is refused, the
        schedule moves nothing, and no hand moves to a release cut under the old name — forward
        or back."""
        self.w.publish("v0.1.2", self.now)
        self.w.rename(RENAMED)
        code, out = self.w.hand(self.now, "v0.1.1", "onto the second of three")
        self.assertEqual((code, self.w.on()), (0, "v0.1.1"), out)
        tip = self.w.tip()
        with mock.patch.dict(stable.FORMER, clear=True):
            code, out = self.w.run(DAY0 + 15 * DAY)
            self.assertEqual((code, self.w.tip()), (1, tip), out)
            self.assertIn(f"STABLE finding: `stable` is on v0.1.1, which is not a whole release: rules-index.md of v0.1.1 "
                          f"is not the index of its own topics for {RENAMED}", out)
            for to in ("v0.1.0", "v0.1.2"):
                code, out = self.w.hand(DAY0 + 30 * DAY, to, "back, or forward, then")
                self.assertEqual((code, self.w.tip()), (1, tip), out)
                self.assertIn(f"STABLE finding: not moved to {to}: rules-index.md of {to} is not the index of its own topics", out)

    def test_the_old_name_is_taken_for_the_repository_it_is_listed_under_and_no_other(self):
        """A fork or a copy under a third name holds an index to its own name alone, as before; and
        the old name does not take the new one's releases: the list runs one way."""
        self.w.rename("someone/rules")
        out = self.stays(self.now, "v0.1.0", code=1)
        self.assertIn("is not the index of its own topics for someone/rules", out)
        self.w.rename(RENAMED, factory=True)
        self.w.publish("v0.1.2", self.now)
        self.w.rename(FORMERLY)
        code, out = self.w.run(DAY0 + 30 * DAY)
        self.assertEqual((code, self.w.on()), (1, "v0.1.1"), out)
        self.assertIn(f"STABLE finding: rules-index.md of v0.1.2 is not the index of its own topics for {FORMERLY}", out)

    def test_an_index_that_names_a_third_repository_is_refused_under_the_new_name_too(self):
        self.w.rename(RENAMED, factory=True)
        self.w.cut("v0.1.2", self.now, {"engine/rules/core.md": b"Core rules, v0.1.2.\n"})
        path = self.w.work / "rules-index.md"
        text = path.read_text(encoding="utf-8").replace(RENAMED, "someone/rules")
        path.write_text(text, encoding="utf-8")
        manifest = self.w.work / "release-manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["index"]["sha256"] = release.hashlib.sha256(text.encode()).hexdigest()
        manifest.write_text(release.dumps(data), encoding="utf-8")
        self.w.land("v0.1.2", self.now)
        code, out = self.w.run(DAY0 + 30 * DAY)
        self.assertEqual((code, self.w.on()), (1, "v0.1.1"), out)
        self.assertIn(f"STABLE finding: rules-index.md of v0.1.2 is not the index of its own topics for {RENAMED}: it was "
                      f"edited, or built for another repository or release", out)

    def test_a_name_typed_in_another_case_at_the_rename_is_the_same_repository(self):
        """GitHub hands the workflow the name as it was typed at the rename; the releases cut under
        the old name are whole all the same, and so is the next one, cut for the name as the
        template's defaults and the list spell it."""
        self.w.rename(RENAMED, factory=True)
        self.w.publish("v0.1.2", self.now)
        self.w.name = "Grafyla/Releases"
        self.stays(self.now, "v0.1.0")
        self.moved(DAY0 + 15 * DAY, to="v0.1.1")
        self.moved(DAY0 + 16 * DAY, to="v0.1.2")
        self.assertIn(f"`{RENAMED}`", self.w.at_branch("rules-index.md"))

    def test_a_name_in_between_is_red_until_the_repository_takes_a_listed_one(self):
        """Said, not prevented: the entry comes before the rename, and for the name the repository
        ends on. Renamed in two steps, it is red at the name in between and goes on at the last.
        (A rename back is another story: the old releases are whole under their own name again,
        and only a release cut under the name left is refused — the story of the list running one
        way, above.)"""
        self.w.rename("grafyla/memory-engine-releases")
        out = self.stays(self.now, "v0.1.0", code=1)
        self.assertIn("STABLE finding: `stable` is on v0.1.0, which is not a whole release", out)
        self.w.rename(RENAMED)
        self.moved(DAY0 + 15 * DAY, to="v0.1.1")

    def test_an_old_name_is_taken_for_the_releases_listed_with_it_and_no_other(self):
        """An old name is another address, and one that may lead elsewhere some day. A release
        not listed with it that names it — its index edited after the cut, the manifest made to
        agree, or the template's defaults not changed yet — is refused, as an index that names
        any other repository is; and it is whole once the list names it: the price of the list."""
        self.w.publish("v0.1.2", self.now)                # the factory still cuts for the old name…
        self.w.rename(RENAMED)                          # …and GitHub runs the workflow under the new one
        code, out = self.w.run(DAY0 + 30 * DAY)
        self.assertEqual((code, self.w.on()), (1, "v0.1.1"), out)
        self.assertIn(f"STABLE finding: rules-index.md of v0.1.2 is not the index of its own topics for {RENAMED}: it was "
                      f"edited, or built for another repository or release", out)
        with mock.patch.dict(stable.FORMER, {RENAMED: ((FORMERLY, ("v0.1.0", "v0.1.1", "v0.1.2")),)}):
            self.moved(DAY0 + 31 * DAY, to="v0.1.2")
        self.assertIn(f"`{FORMERLY}`", self.w.at_branch("rules-index.md"))

    def test_a_release_cut_before_the_listed_ones_under_a_lower_number_is_not_taken(self):
        """The list is of releases, not a bound: a release under a lower number than the listed
        ones is not taken for one cut under the old name, though it names it. Here it was cut
        first. A number left free and tagged later by hand is not tried here: `names` reads the
        tag's name alone, and `release cut` refuses to cut below the newest release."""
        w = World(FORMERLY)
        self.addCleanup(w.close)
        w.publish("v0.0.9", DAY0)
        w.publish("v0.1.1", DAY0 + DAY)
        w.rename(RENAMED)
        with mock.patch.dict(stable.FORMER, {RENAMED: ((FORMERLY, ("v0.1.1",)),)}):
            code, out = w.hand(DAY0 + 2 * DAY, "v0.0.9", "by hand, onto the lower number")
            self.assertEqual((code, w.tip()), (1, None), out)
            self.assertIn(f"STABLE finding: not moved to v0.0.9: rules-index.md of v0.0.9 is not the index of its own "
                          f"topics for {RENAMED}", out)
            code, out = w.hand(DAY0 + 2 * DAY, "v0.1.1", "the listed one")
            self.assertEqual((code, w.on()), (0, "v0.1.1"), out)

    def test_the_names_this_code_holds(self):
        self.assertEqual(stable.names(RENAMED, "v0.1.1"), (RENAMED, FORMERLY), "its own name first, then the ones it had")
        self.assertEqual(stable.names(RENAMED, "v0.1.2"), (RENAMED,), "not listed as cut under the old name")
        self.assertEqual(stable.names(RENAMED, "v0.0.9"), (RENAMED,), "nor is a lower number")
        self.assertEqual(stable.names(FORMERLY, "v0.1.0"), (FORMERLY,))
        self.assertEqual(stable.names("someone/rules", "v0.1.0"), ("someone/rules",))
        self.assertEqual(stable.names("Grafyla/Releases", "v0.1.0"), ("Grafyla/Releases", RENAMED, FORMERLY),
                         "GitHub does not tell names apart by case and hands the workflow the name as it was typed at "
                         "the rename: the name as the list spells it is taken too — a release is cut for that spelling")
        self.assertEqual(stable.names("Grafyla/Releases", "v0.1.2"), ("Grafyla/Releases", RENAMED))


class FormerNames(unittest.TestCase):
    """`stable.FORMER` as the code holds it, outside the stories above, which put their own in its place."""

    def test_the_list_is_what_it_was(self):
        self.assertEqual(stable.FORMER, FORMER,
                         "an old name taken out, or a release taken off its list, refuses that release for good: the "
                         "branch `stable` red on it, and no move back to it. A name or a release is added, or the list "
                         "moved under a new name, in the pull request that needs it — with this line")

    def test_its_form(self):
        self.assertEqual(len({name.lower() for name in stable.FORMER}), len(stable.FORMER),
                         "two entries for one repository, apart only by the case of their letters: the first alone is read")
        for name, had in stable.FORMER.items():
            self.assertTrue(stable._REPOSITORY.fullmatch(name), name)
            for old, cut in had:
                self.assertTrue(stable._REPOSITORY.fullmatch(old), old)
                self.assertNotIn(old.lower(), {key.lower() for key in stable.FORMER},
                                 "an old name that is an entry of its own: a list is not followed from name to name")
                self.assertIsInstance(cut, tuple)
                self.assertTrue(cut and all(stable.TAG_RE.fullmatch(tag) for tag in cut), cut)

    def test_the_releases_out_under_the_old_name_are_on_its_list(self):
        """v0.1.13 and v0.1.14 are the releases cut with an index under the old name (the repository
        of releases, 23.11.2026); the next is cut under the new one."""
        for tag in ("v0.1.13", "v0.1.14"):
            self.assertEqual(stable.names(RENAMED, tag), (RENAMED, FORMERLY))
        for tag in ("v0.1.15", "v0.1.12", "v0.0.1"):
            self.assertEqual(stable.names(RENAMED, tag), (RENAMED,), tag)


class OnItsOwn(unittest.TestCase):
    """The repository of releases runs its own copy of this file, so it must run alone, and say
    the same things as the engine it does not import."""

    SOURCE = Path(stable.__file__)

    def test_it_imports_nothing_of_the_engine(self):
        tree = ast.parse(self.SOURCE.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "a relative import")
                names.add((node.module or "").split(".")[0])
        self.assertEqual(names - {"__future__"}, {"argparse", "hashlib", "json", "os", "re", "subprocess", "sys",
                                                  "tomllib", "unicodedata", "dataclasses", "datetime", "pathlib"})

    def test_it_runs_as_a_file_outside_the_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "stable.py"
            copy.write_bytes(self.SOURCE.read_bytes())
            proc = subprocess.run(["python3", "-I", "-B", str(copy), "--repo", tmp, "--repository", "o/n", "--to", "v0.1.0"],
                                  cwd=tmp, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("ERROR the fields «to» and «reason» (--to, --reason) go together", proc.stderr)
            proc = subprocess.run(["python3", "-I", "-B", str(copy), "--repo", tmp, "--repository", "o/n"], cwd=tmp,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("ERROR git ls-remote", proc.stderr)

    def test_its_week_is_the_channels_week_and_its_names_are_the_engines(self):
        self.assertEqual(stable.DELAY, lock_check.CHANNEL_DELAY["stable"])
        self.assertEqual(stable.PUBLISHED_FORMAT, lock_check.PUBLISHED_FORMAT)
        self.assertEqual(stable.TAG_RE.pattern, release.PUBLISHED_TAG_RE.pattern)
        self.assertEqual(stable.MANIFEST_FILE, release.MANIFEST_FILE)
        self.assertEqual(stable.FILES, release.PUBLISHED_FILES)
        self.assertEqual(stable.INDEX_FILE, release.INDEX_FILE)
        self.assertEqual((stable.TOPICS, stable.RULES), (topics.TOPICS, topics.RULES))
        self.assertEqual(release.STABLE, "engine/setup/stable.py")
        self.assertTrue((Path(__file__).resolve().parents[3] / release.STABLE).is_file())


if __name__ == "__main__":
    unittest.main()

"""The installation evening as a scenario (Issue #18, item 6).

The first live installation (evening B, 25.10.2026, release v0.1.2), as Issue #18 records it:
N1 reported the push «not through the gate», the creation of main among its lines (item 2),
and the trusted contact closed that Issue as known; the bot opened its record PR with its own
token, the gate let it in and it merged; the record matched all five fingerprints; the watch
ended with no open alarm. Here the same evening runs through the real entry points: the
factory cuts the release with `engine.setup.release manifest`; main is created with only the
person's part; the trusted contact installs the release with `engine.setup.release install`
and pushes it; every run after that is `engine.checks.watch` and `engine.checks.alarm`
against the published manifest; the record PR goes through `engine.checks.run` (C1–C7) and
the watch inside the gate, then job `merge`.

The person's part is a fixture: two fields and three routine masters — mail, mark and morning,
the routines of 25.10 (invitations came on 28.10, #18 comment 6477549902); five fingerprints.
The texts are not the pilot's. #18 counts three N1 lines on the evening and names one of
them, the creation of main; here there are two: the creation of main and the installation
push.

What the fixtures cannot see: GitHub itself (branch protection, the bot's token, job
`merge`'s timing, the gate's `--manifest-url` step and its record-PR test on the merge
result — both held in engine/setup/tests/test_release_scenarios.py and check.yml), the
platform where the fields and routines are installed, the Notion copy and the daily mark
(N2–N4) — #18 does not describe them for the evening, and this run notes N4 as «before
installation» — and the nightly job's colour, which is the alarm step's after item 1 (the
watch itself still exits 1 on a known line).
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine.checks import gitio, run
from engine.checks.tests.fakeapi import REPO
from engine.checks.tests.helpers import VALID_CONFIG, Repo
from engine.checks.tests.test_scenarios import ISSUES, World
from engine.setup import release
from engine.setup.tests.test_release import TEMPLATE

TAG = "v0.1.2"
CUT = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
CONTACT = "helper"
ROUTINES = {
    "user/routines/mail.md": b"Twice a day: new letters in a few plain words.\n",
    "user/routines/mark.md": b"Once a day: the quiet check-in for the trusted contact.\n",
    "user/routines/morning.md": b"\nEvery morning at eight: the day's calendar and one line of weather.\n\n",
}
PERSON_PART = {
    "README.md": b"A person's memory.\n",
    "user/config.toml": VALID_CONFIG + f'\n[alarm]\ntrusted_contact = "{CONTACT}"\n'.encode(),
    "user/fields/chat.md": b"Who: a test person.\n",
    "user/fields/bot.md": b"Who: a test person, for the bot.\n",
    **ROUTINES,
}


def quiet(fn, *args, **kw) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = fn(*args, **kw)
    return code, out.getvalue()


class Evening:
    """The factory, its published manifest, and the person's repository with its GitHub."""

    def __init__(self):
        self.factory = Repo(TEMPLATE)
        self.w = World(files=PERSON_PART)
        w = self.w
        w.api.statuses[f"repos/{REPO}/collaborators/{CONTACT}"] = 204
        # main is created that evening, with the person's part — not the old push World records.
        w.api.lists[f"repos/{REPO}/activity"].clear()
        w.record("branch_creation", "0" * 40, w.main, CONTACT, w.now - timedelta(hours=1))
        code, text = quiet(release.main, ["manifest", "--repo", str(self.factory.path), "--rev", "main",
                                          "--tag", TAG], now=CUT)
        assert code == 0, text
        self.manifest = w.repo.path.parent / f"{w.repo.path.name}-{TAG}.json"
        self.manifest.write_text(text, encoding="utf-8")

    def close(self) -> None:
        self.factory.close()
        self.w.close()

    def install(self) -> list[str]:
        """The trusted contact installs the release by hand and pushes it to main."""
        code, out = quiet(release.main, ["install", "--repo", str(self.factory.path), "--rev", "main",
                                         "--tag", TAG, "--into", str(self.w.repo.path)])
        assert code == 0, out
        self.w.push({}, CONTACT)                 # everything `install` wrote, in one commit
        self.w.release, self.w.published = TAG, self.manifest
        return out.split()

    def fingerprints(self) -> bytes:
        """The record of the installation, computed here from main as REPO-3 and DESIGN say,
        not with the engine's own helpers: a field is «engine part, one empty line, person's
        part» (C6), the chat field's sha256 as the trusted contact gives it; a routine is its
        master «without leading and trailing newlines» (REPO-3)."""
        def on_main(path: str) -> bytes:
            return gitio.file_at(self.w.repo.path, self.w.main, path)

        lines = []
        for name in ("chat", "bot"):
            text = (on_main(f"engine/rules/fields/{name}.md").rstrip(b"\n") + b"\n\n"
                    + on_main(f"user/fields/{name}.md").rstrip(b"\n"))
            lines.append(f'[fields.{name}]\nsha256 = "{hashlib.sha256(text).hexdigest()}"\n')
        for path in sorted(ROUTINES):
            sha = hashlib.sha256(on_main(path).strip(b"\n")).hexdigest()
            lines.append(f'[routines.{Path(path).stem}]\nsha256 = "{sha}"\n')
        return "\n".join(lines).encode()

    def record_pr(self, record: bytes) -> tuple[int, str]:
        """The bot's PR with the record, as the gate judges it: C1–C7 against the published
        manifest, then the watch inside the gate, then the alarms; job `merge` lands it only
        when all are green. The masters are inside N5's 36 h here, as on the evening."""
        w = self.w
        base = w.main
        w.repo.checkout(base)
        head = w.repo.commit({"user/fields/installed.toml": record})
        w.repo.checkout(base)
        code, out = quiet(run.main, ["--repo", str(w.repo.path), "--base", base, "--head", head, "--kind", "person",
                                     "--body", str(w.repo.body_file("Record of the installation\n")),
                                     "--manifest", str(self.manifest)],
                          now=w.now)
        if code != 0:
            return code, out
        _, red, watched = w.run(record_pr=True)
        if red:
            return red, out + watched
        w.merge({"user/fields/installed.toml": record})
        return 0, out + watched


class InstallationEvening(unittest.TestCase):
    def setUp(self):
        self.e = Evening()
        self.addCleanup(self.e.close)

    def test_the_evening_ends_with_no_open_alarm(self):
        e, w = self.e, self.e.w
        written = e.install()
        self.assertIn(".github/workflows/check.yml", written)
        self.assertIn("user/engine.lock", written)

        # The push starts the watch: the creation of main and the push are N1's alarm — an
        # expected one, which the trusted contact closes as known.
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        [issue] = w.issues("open")
        self.assertIn(f"main: branch_creation by {CONTACT}", issue["body"])
        self.assertIn(f"main: push by {CONTACT}", issue["body"])
        self.assertTrue(issue["body"].startswith(f"@{CONTACT} @owner —"))
        self.assertNotIn("N5", issue["title"])                             # the masters are inside their 36 h
        w.api.close(ISSUES, issue["number"], by=CONTACT)

        # The bot installs its field and routines, the contact the chat field; the record PR.
        w.advance(1)
        record = e.fingerprints()
        self.assertEqual(record.count(b"sha256 = "), 5)
        code, out = e.record_pr(record)
        self.assertEqual(code, 0, out)
        self.assertIn("C7 N/A machine record only", out)
        self.assertIn("SUMMARY block=0", out)

        # The watch after the merge and the next two nights: no open alarm. The watch itself
        # still finds the two known lines until they leave N1's 36 h (Issue #18, item 1).
        found = []
        for hours in (0, 24, 24):
            w.advance(hours)
            watch_exit, red, out = w.run()
            found.append(watch_exit)
            self.assertEqual((red, w.issues("open")), (0, []), out)
        self.assertEqual(found, [1, 1, 0])
        self.assertNotIn("N5 ALARM", out)                                  # the masters are past N5's 36 h: all five match
        self.assertEqual([i["title"] for i in w.issues("closed")], [issue["title"]])

    def test_a_record_that_misses_one_routine_is_n5s_alarm_after_the_grace(self):
        # The record PR lands: the gate holds the routine entries a record has to their
        # masters (C2, item 33), not that every routine has one. N5 finds the routine with no
        # record once the masters are 36 h old.
        e, w = self.e, self.e.w
        e.install()
        w.run()
        [issue] = w.issues("open")
        w.api.close(ISSUES, issue["number"], by=CONTACT)
        record = e.fingerprints()
        cut = record[:record.index(b"[routines.mark]")] + record[record.index(b"[routines.morning]"):]
        self.assertEqual(cut.count(b"sha256 = "), 4)
        code, out = e.record_pr(cut)
        self.assertEqual(code, 0, out)
        w.advance(48)
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        self.assertIn("routine mark changed more than 36 h ago and is not reinstalled", out)

"""Scenarios: the watch and the alarms over days, on fixtures (F1).

Each test is a story docs/DESIGN.md §5–§6 tells; the assertions are what the owner and the
trusted contact would see on GitHub: which Issues exist, what they say, and whether the
gate would be red (the alarm step's exit code). The same two entry points the workflows
call — `engine.checks.watch` and `engine.checks.alarm` — run here against a throwaway
repository and a stand-in GitHub that serves back what the previous runs wrote.
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from engine.backup import run as backup_run
from engine.backup.tests.fakenotion import FakeNotion, page, paragraph, rich, source
from engine.backup.tests.test_workflow import copy_step
from engine.checks.tests.test_watch_workflow import watch_step
from engine.checks import alarm, drift, watch
from engine.checks import run as gate_checks
from engine.checks.limits_check import assemble
from engine.checks.tests.fakeapi import BOT, REPO, FakeApi
from engine.checks.tests.helpers import BASE_FILES, VALID_CONFIG, Repo, _git, body

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
ISSUES = f"repos/{REPO}/issues"
ACTIONS_ID = 15368
RUN_URL = "https://github.com/owner/person-memory/actions/runs/1"


def installed(files: dict[str, bytes]) -> bytes:
    text = ""
    for name in ("chat", "bot"):
        engine = files[f"engine/rules/fields/{name}.md"].decode()
        person = files[f"user/fields/{name}.md"].decode()
        text += f'[fields.{name}]\nsha256 = "{drift.digest(assemble(engine, person))}"\n\n'
    return text.encode()


class World:
    """A person's repository and its GitHub, as the checks see them."""

    def __init__(self, config: bytes = VALID_CONFIG, files: dict[str, bytes] | None = None):
        """`files` — the first tree, as it is (its own user/config.toml; `config` is then not
        used); by default an engine installed long ago with its fields recorded."""
        if files is None:
            files = {**BASE_FILES, "user/config.toml": config}
            files["user/fields/installed.toml"] = installed(files)
        self.repo = Repo(files)
        self.main = self.repo.base
        self.release = "v0.1.0"
        self.published: Path | None = None      # a manifest the factory published; else one read off main
        self.now = T0
        self.api = FakeApi()
        a = self.api
        a.lists[ISSUES] = []
        a.lists[f"repos/{REPO}/activity"] = []
        a.objects["apps/github-actions"] = {"id": ACTIONS_ID}
        a.objects[f"repos/{REPO}/branches/main"] = {"protection": {
            "enabled": True, "required_status_checks": {"checks": [{"context": "gate", "app_id": ACTIONS_ID}]}}}
        a.objects[f"repos/{REPO}/rules/branches/main"] = []
        self._numbers = 0
        self.record("push", "0" * 40, self.main, "owner", self.now - timedelta(days=10))   # the first commit, long ago
        self._live()
        # The person's Notion, as the integration sees it, and the copy's branch.
        self.notion = FakeNotion(
            pages=[page("home", "Home", {"type": "workspace", "workspace": True}),
                   page("notes", "Notes", {"type": "page_id", "page_id": "home"})],
            sources=[source("docs", "Documents", "docsdb", "home")],
            rows={"docs": [page(f"row{i}", f"Document {i}", {"type": "data_source_id", "data_source_id": "docs"}) for i in range(3)]},
            blocks={"notes": [paragraph("n1", "shopping list")]})
        self._copy_parent = Path(tempfile.mkdtemp())
        self.copy_dir = self._copy_parent / "copy"
        self.backup_head: str | None = None

    def close(self) -> None:
        self.repo.close()
        shutil.rmtree(self._copy_parent, ignore_errors=True)

    # -- time and GitHub's records ---------------------------------------------------

    def advance(self, hours: float) -> None:
        self.now += timedelta(hours=hours)

    def _live(self) -> None:
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": self.main}}

    def record(self, kind, before, after, who, when=None, ref="refs/heads/main") -> None:
        log = self.api.lists[f"repos/{REPO}/activity"]
        log.append({"id": len(log) + 1, "activity_type": kind, "before": before, "after": after,
                    "timestamp": (when or self.now).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "actor": {"login": who}, "ref": ref})
        log.sort(key=lambda r: (r["timestamp"], r["id"]), reverse=True)

    def merge(self, files: dict[str, bytes]) -> None:
        """A PR with these files, squash-merged by job `merge` after a verified gate run."""
        self._numbers += 1
        number, run = self._numbers, 500 + self._numbers
        head = hashlib.sha1(f"head{number}".encode()).hexdigest()
        before = self.main
        self.repo.checkout(before)
        self.main = self.repo.commit(files)
        self.record("pr_merge", before, self.main, BOT)
        a = self.api
        a.objects[f"repos/{REPO}/commits/{self.main}/pulls"] = [{"number": number, "merge_commit_sha": self.main, "base": {"ref": "main"}}]
        a.objects[f"repos/{REPO}/pulls/{number}"] = {"merged_by": {"login": BOT}, "head": {"sha": head}}
        a.lists[f"repos/{REPO}/commits/{head}/statuses"] = [{"context": "gate", "state": "success", "creator": {"login": BOT},
                                                             "target_url": f"https://github.com/{REPO}/actions/runs/{run}"}]
        a.objects[f"repos/{REPO}/actions/runs/{run}"] = {"path": ".github/workflows/check.yml", "repository": {"full_name": REPO},
                                                         "event": "pull_request_target", "head_sha": head, "head_branch": "bot/x"}
        a.lists[f"repos/{REPO}/actions/runs/{run}/jobs"] = [{"id": run * 10, "name": "check", "conclusion": "success"}]
        a.texts[f"repos/{REPO}/actions/jobs/{run * 10}/logs"] = f"2026-10-23T08:00:01Z GATE pr={number} head={head} base={before} run={run}\n"
        self._live()

    def gate(self, files: dict[str, bytes], record: bool = False) -> tuple[int, str]:
        """A PR with these files, as job `check` judges it: C1–C7 (engine.checks.run), then the
        watch on the main it checked out (N5 skipped for a record PR) and its alarms — (exit,
        output). Green — job `merge` lands it; red — main stays where it was. A record PR's
        body is REPO-3's one line, and C7 does not apply to it (§5)."""
        base = self.main
        self.repo.checkout(base)
        head = self.repo.commit(files)
        self.repo.checkout(base)
        text = "Record of the installation\n" if record else body(*sorted(files))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate_checks.main(["--repo", str(self.repo.path), "--base", base, "--head", head, "--kind", "person",
                                     "--body", str(self.repo.body_file(text)),
                                     "--manifest", str(self.repo.manifest_file(base, self.release))], now=self.now)
        if code != 0:
            return code, out.getvalue()
        _, red, watched = self.run(record_pr=record)
        if red:
            return red, out.getvalue() + watched
        self.merge(files)
        return 0, out.getvalue() + watched

    def push(self, files: dict[str, bytes], who: str) -> None:
        """A direct push to main — not through the gate."""
        before = self.main
        self.repo.checkout(before)
        self.main = self.repo.commit(files)
        self.record("push", before, self.main, who)
        self._live()

    def bypass(self, who: str, text: bytes) -> None:
        """A direct push that changes the bot field and records its installation too, so
        that only N1 has something to say (N5 would otherwise be right to speak up at 36 h)."""
        self.push({"user/fields/bot.md": text,
                   "user/fields/installed.toml": installed({**BASE_FILES, "user/fields/bot.md": text})}, who)

    # -- what the workflows run ---------------------------------------------------------

    def run(self, record_pr: bool = False, main: str | None = None) -> tuple[int, int, str]:
        """One run of the checks and the alarms: (watch exit, alarm exit, output).
        The alarm exit is what the gate turns red on."""
        main = main or self.main
        manifest = self.published or self.repo.manifest_file(self.main, self.release)
        report = self.repo.path.parent / f"{self.repo.path.name}-report.json"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            argv = ["--repo", str(self.repo.path), "--main", main, "--manifest", str(manifest),
                    "--report", str(report), "--branches", "refs/heads/"]
            w = watch.main(argv + (["--record-pr"] if record_pr else []), api=self.api, now=self.now)
            with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner"}):
                a = alarm.main(["--repo", str(self.repo.path), "--main", main, "--report", str(report),
                                "--run-url", RUN_URL], api=self.api)
        return w, a, out.getvalue()

    def nightly(self) -> tuple[bool, str]:
        """One run of watch.yml: (the job is red, output). The step «N1, N5» is decided by its
        own line from the workflow (`watch_step`), the alarm step by its exit code, and the
        job is red when either step fails — as on GitHub (Issue #18, item 1). A crash of the
        watch ends `python3 -m` with exit 1, as here."""
        runner_temp = Path(tempfile.mkdtemp(dir=self._copy_parent))   # new every job
        report = runner_temp / "report.json"
        manifest = self.repo.manifest_file(self.main, self.release)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                code = watch.main(["--repo", str(self.repo.path), "--main", self.main, "--manifest", str(manifest),
                                   "--report", str(report), "--branches", "refs/heads/"], api=self.api, now=self.now)
            except Exception as exc:                   # noqa: BLE001 — `python3 -m` ends such a run with exit 1
                print(f"CRASH {type(exc).__name__}: {exc}")
                code = 1
            step_red = watch_step(runner_temp, code) != 0
            with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner"}):
                a = alarm.main(["--repo", str(self.repo.path), "--main", self.main, "--report", str(report),
                                "--run-url", RUN_URL], api=self.api)
        print(f"STEP {'red' if step_red else 'green'} ALARM-EXIT {a}", file=out)
        return step_red or a != 0, out.getvalue()

    def night(self, push_fails: bool = False) -> tuple[int, int, str]:
        """One run of the backup workflow: the copy, its commit and push, its alarms —
        (backup exit, alarm exit, output). Whether the commit runs is decided by the step's own
        line from backup.yml, run in bash (`copy_step`): after exit 0, or after exit 1 with a
        non-empty report — a crash exits 1 too (Issue #18, item 42). A commit or push that
        fails is handed to the alarms."""
        if self.backup_head is None and not self.copy_dir.exists():
            # The first night, as backup.yml does it: a worktree, then an orphan branch.
            _git(self.repo.path, "worktree", "add", "--detach", str(self.copy_dir), self.main)
            _git(self.copy_dir, "checkout", "-q", "--orphan", "notion-backup")
            _git(self.copy_dir, "rm", "-r", "-q", "-f", ".")
        runner_temp = Path(tempfile.mkdtemp(dir=self._copy_parent))   # new every job: no report from last night
        report = runner_temp / "report.json"
        out = io.StringIO()
        extra: list[str] = []
        with contextlib.redirect_stdout(out):
            try:
                code = backup_run.main(["--repo", str(self.repo.path), "--main", self.main, "--worktree",
                                        str(self.copy_dir), "--report", str(report)], api=self.notion, now=self.now)
            except Exception as exc:                   # noqa: BLE001 — `python3 -m` ends such a run with exit 1
                print(f"CRASH {type(exc).__name__}: {exc}")
                code = 1
            committed = copy_step(runner_temp, code) == 0
            if committed and push_fails:
                extra = ["--error", "the copy was made but could not be committed or pushed to notion-backup"]
            elif committed:
                _git(self.copy_dir, "add", "-A")
                _git(self.copy_dir, "commit", "-q", "--allow-empty", "-m", "Notion copy")
                head = _git(self.copy_dir, "rev-parse", "HEAD")
                self.record("branch_creation" if self.backup_head is None else "push",
                            self.backup_head or "0" * 40, head, BOT, ref="refs/heads/notion-backup")
                self.backup_head = head
            with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "owner"}):
                a = alarm.main(["--repo", str(self.repo.path), "--main", self.main, "--report", str(report),
                                "--run-url", RUN_URL, "--what", "backup", *extra], api=self.api)
        return code, a, out.getvalue()

    # -- the bot's daily mark (N4) ---------------------------------------------------

    MARKS = 7

    def install_marks(self) -> None:
        """Installation: the Issue «routine marks», and the config that names it and the
        person's login — a merge of the trusted contact's (C3 lets only that push in)."""
        a = self.api
        issue = {"number": self.MARKS, "title": "routine marks", "state": "open", "user": {"login": "owner"}}
        a.lists[ISSUES].append(issue)
        a.objects[f"{ISSUES}/{self.MARKS}"] = issue
        a.lists[f"{ISSUES}/{self.MARKS}/comments"] = []
        config = VALID_CONFIG.replace(b'timezone = "Asia/Jerusalem"\n', b'timezone = "Asia/Jerusalem"\ngithub_login = "alex"\n')
        self.merge({"user/config.toml": config + f"\n[alarm]\nmarks_issue = {self.MARKS}\n".encode()})

    def mark(self, scopes: str = "repo", who: str = "alex") -> None:
        """The bot's routine posts its daily mark, now."""
        self.api.lists[f"{ISSUES}/{self.MARKS}/comments"].append({
            "user": {"login": who}, "created_at": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": f"ROUTINE-MARK {self.now.strftime('%Y-%m-%dT%H:%M:%S+00:00')} scopes={scopes}"})

    def delete_copy_branch(self, who: str) -> None:
        """A collaborator deletes notion-backup: the branch is gone, GitHub records it."""
        _git(self.repo.path, "worktree", "remove", "--force", str(self.copy_dir))
        _git(self.repo.path, "branch", "-D", "notion-backup")
        self.record("branch_deletion", self.backup_head, "0" * 40, who, ref="refs/heads/notion-backup")
        self.backup_head = None

    def copied(self) -> dict[str, str]:
        """The files of the committed copy, as git holds them."""
        out = _git(self.copy_dir, "ls-tree", "-r", "--name-only", "HEAD")
        return {p: _git(self.copy_dir, "show", f"HEAD:{p}") for p in out.splitlines()}

    def issues(self, state: str) -> list[dict]:
        return [i for i in self.api.lists[ISSUES] if i["state"] == state]

    def alarms(self, state: str) -> list[dict]:
        """The alarm Issues only — not the «routine marks» Issue installation opened."""
        return [i for i in self.issues(state) if i.get("user", {}).get("login") == BOT]

    def comments(self, number: int) -> list[str]:
        return [c["body"] for c in self.api.lists[f"{ISSUES}/{number}/comments"]]


class Scenarios(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def tearDown(self):
        self.w.close()

    def test_quiet_days_make_no_noise(self):
        w = self.w
        for _ in range(3):
            _, red, out = w.run()
            self.assertEqual(red, 0, out)
            w.advance(24)
        w.merge({"user/fields/chat.md": b"Who: changed.\n"})          # a save through the gate
        w.merge({"user/fields/installed.toml": installed({**BASE_FILES, "user/fields/chat.md": b"Who: changed.\n"})})
        w.advance(48)
        _, red, out = w.run()
        self.assertEqual(red, 0, out)
        self.assertEqual(w.api.writes, [])                                 # not one Issue, not one comment

    def test_a_bypass_alarms_once_then_a_close_is_known_and_a_new_bypass_alarms_again(self):
        w = self.w
        w.bypass("alex", b"Who: pushed past the gate.\n")
        _, red, _ = w.run()
        self.assertEqual(red, 1)                                           # the save is red
        [issue] = w.issues("open")
        self.assertEqual(issue["title"], alarm.TITLES["N1"])
        self.assertIn("push by alex", issue["body"])
        self.assertTrue(issue["body"].startswith("@owner —"))
        writes = len(w.api.writes)
        w.advance(12)
        _, red, _ = w.run()
        self.assertEqual((red, len(w.api.writes)), (1, writes))           # the same finding: no second alarm, still red
        w.api.close(ISSUES, issue["number"], by="owner")                   # «known, not fixing»
        _, red, out = w.run()
        self.assertEqual(red, 0, out)                                      # green again, no new Issue
        self.assertEqual(w.issues("open"), [])
        w.advance(40)                                                      # the push leaves the window
        _, red, _ = w.run()
        self.assertEqual((red, w.issues("open")), (0, []))
        w.bypass("alex", b"Who: pushed again.\n")                        # a new bypass is a new alarm
        _, red, _ = w.run()
        self.assertEqual(red, 1)
        self.assertEqual(len(w.issues("open")), 1)

    # -- Issue #18, item 1: the nightly job's colour follows the alarm step --------------

    def test_the_nightly_job_is_green_once_every_finding_is_known_and_red_again_on_a_new_one(self):
        w = self.w
        w.bypass("alex", b"Who: pushed past the gate.\n")
        red, out = w.nightly()
        self.assertTrue(red, out)                                          # a new finding: red, and an Issue
        [issue] = w.issues("open")
        w.api.close(ISSUES, issue["number"], by="owner")                   # «known, not fixing»
        for _ in range(2):                                                 # the line stays in N1's 36 h window
            w.advance(12)
            red, out = w.nightly()
            self.assertFalse(red, out)                                     # was red here: «Run failed: watch» every night
            self.assertIn("acknowledged N1", out)
        self.assertEqual(w.issues("open"), [])
        w.bypass("alex", b"Who: pushed again.\n")
        red, out = w.nightly()
        self.assertTrue(red, out)
        self.assertEqual(len(w.issues("open")), 1)

    def test_a_watch_that_cannot_start_keeps_the_nightly_job_red_in_its_own_step(self):
        w = self.w
        with mock.patch.object(watch.lock_check, "load_manifest", side_effect=ValueError("manifest gone")):
            red, out = w.nightly()
        self.assertTrue(red, out)
        self.assertIn("STEP red", out)                                     # exit 2: red whatever the alarms say
        self.assertEqual([i["title"] for i in w.alarms("open")], [alarm.ERROR_TITLES["checks"]])

    def test_a_watch_that_crashes_before_its_report_keeps_the_nightly_job_red(self):
        w = self.w
        with mock.patch.object(watch, "render", side_effect=KeyError("a bug")):
            red, out = w.nightly()
        self.assertTrue(red, out)
        self.assertIn("CRASH KeyError", out)
        self.assertIn("STEP red", out)                                     # exit 1, but no report: a crash, not findings
        self.assertIn("no readable report", w.alarms("open")[0]["body"])

    def test_a_full_disk_while_the_watch_writes_its_report_keeps_the_nightly_job_red(self):
        # The report is written in one step: a write that fails half-way leaves no file, not a
        # cut one that `-s` would take for a finished night.
        w, real = self.w, Path.write_text

        def write_text(path, *args, **kwargs):
            if path.name == "report.json.part":
                real(path, '{"N1": [')                                     # half written, then the disk is full
                raise OSError(28, "No space left on device", str(path))
            return real(path, *args, **kwargs)
        with mock.patch.object(Path, "write_text", write_text):
            red, out = w.nightly()
        self.assertTrue(red, out)
        self.assertIn("STEP red", out)
        self.assertIn("CRASH OSError", out)

    def test_the_bots_account_closing_counts_the_same_as_anyone(self):
        # Owner, 23.10.2026 (DESIGN §7): whoever closed it, the bot or the person, judged it known.
        w = self.w
        w.bypass("alex", b"x\n")
        w.run()
        [issue] = w.issues("open")
        w.api.close(ISSUES, issue["number"], by="alex")
        _, red, _ = w.run()
        self.assertEqual((red, w.issues("open")), (0, []))

    def test_n1_is_never_closed_by_the_machine_and_says_clean_once(self):
        w = self.w
        w.bypass("alex", b"x\n")
        w.run()
        [issue] = w.issues("open")
        w.advance(40)                                                      # the push leaves the window
        _, red, _ = w.run()
        self.assertEqual(red, 0)
        self.assertEqual(len(w.issues("open")), 1)                         # still open: the machine never closes N1
        self.assertEqual([c for c in w.comments(issue["number"]) if "Clean in" in c].__len__(), 1)
        w.advance(24)
        w.run()
        self.assertEqual([c for c in w.comments(issue["number"]) if "Clean in" in c].__len__(), 1)   # said once

    def test_a_forgotten_reinstall_alarms_the_record_lands_through_the_gate_and_a_machine_close_is_not_known(self):
        w = self.w
        w.merge({"user/fields/chat.md": b"Who: v2.\n"})                    # the master changed through the gate
        _, red, _ = w.run()
        self.assertEqual((red, w.issues("open")), (0, []))                 # within the grace: nothing yet
        w.advance(37)
        _, red, _ = w.run()
        self.assertEqual(red, 1)
        [issue] = w.issues("open")
        self.assertEqual(issue["title"], alarm.TITLES["N5"])
        # The bot records the installation: in the gate, that PR is not judged by N5, so it can land.
        _, red, out = w.run(record_pr=True)
        self.assertEqual(red, 0, out)
        self.assertEqual(len(w.issues("open")), 1)                         # and the Issue was not touched
        w.merge({"user/fields/installed.toml": installed({**BASE_FILES, "user/fields/chat.md": b"Who: v2.\n"})})
        _, red, _ = w.run()
        self.assertEqual((red, w.issues("open")), (0, []))                 # clean: the machine closed N5
        self.assertEqual(w.issues("closed")[0]["closed_by"], {"login": BOT})
        # The same drift again: a machine close was «clean», not «known» — a new alarm.
        w.merge({"user/fields/chat.md": b"Who: v3.\n"})
        w.advance(37)
        _, red, _ = w.run()
        self.assertEqual(red, 1)
        self.assertEqual([i["title"] for i in w.issues("open")], [alarm.TITLES["N5"]])

    def test_a_routine_hashed_wrong_or_left_stale_is_red_in_the_record_pr(self):
        # Issue #18, item 33: the first installation's record hashed a routine with its last
        # newline, and only N5 would have said so, 36 h later. Every routine entry of a record
        # PR is compared with its master at once, also one the PR leaves as it was (Kimi,
        # review of PR #25): N5 is skipped on that PR.
        w = self.w
        morning = b"Every morning: the calendar.\n"
        code, out = w.gate({"user/routines/morning.md": morning})
        self.assertEqual(code, 0, out)                                     # the master lands
        fields = installed(BASE_FILES)

        def entry(name: str, master: bytes) -> bytes:
            sha = hashlib.sha256(master.strip(b"\n")).hexdigest()       # REPO-3: without the outer newlines
            return f'\n[routines.{name}]\nsha256 = "{sha}"\n'.encode()

        wrong = fields + f'\n[routines.morning]\nsha256 = "{hashlib.sha256(morning).hexdigest()}"\n'.encode()
        landed = w.main
        code, out = w.gate({"user/fields/installed.toml": wrong}, record=True)
        self.assertEqual(code, 1, out)                                     # red in its own PR
        self.assertIn("C2 BLOCK user/fields/installed.toml records routine morning with a sha256 that is not of its master", out)
        self.assertEqual(w.main, landed)                                   # and it did not land
        right = fields + entry("morning", morning)
        code, out = w.gate({"user/fields/installed.toml": right}, record=True)
        self.assertEqual(code, 0, out)
        w.advance(37)
        _, red, out = w.run()
        self.assertEqual((red, w.issues("open")), (0, []), out)          # N5 has nothing to say
        # The morning master changes and an evening routine is added. A record PR that writes
        # only the evening's entry and leaves the morning's as it was is red: reinstall both.
        new_morning = b"Every morning: the calendar and the weather.\n"
        evening = b"\nEvery evening: what is open for tomorrow.\n"
        code, out = w.gate({"user/routines/morning.md": new_morning, "user/routines/evening.md": evening})
        self.assertEqual(code, 0, out)
        landed = w.main
        code, out = w.gate({"user/fields/installed.toml": right + entry("evening", evening)}, record=True)
        self.assertEqual(code, 1, out)
        self.assertIn("records routine morning with a sha256 that is not of its master", out)
        self.assertNotIn("routine evening", out)
        self.assertEqual(w.main, landed)
        code, out = w.gate({"user/fields/installed.toml": fields + entry("morning", new_morning) + entry("evening", evening)},
                           record=True)
        self.assertEqual(code, 0, out)
        w.advance(37)
        _, red, out = w.run()
        self.assertEqual((red, w.issues("open")), (0, []), out)

    def test_an_address_in_a_routine_is_blocked_at_the_gate_and_one_pushed_past_it_is_an_alarm(self):
        # Issue #18, item 35: a routine master once held the trusted contact's address; the
        # routines hold none — his two live in the bot field's part (DOC-1).
        w = self.w
        routine = {"user/routines/invitations.md": b"Accept the invitations from alex.trusted@example.com.\n"}
        before = w.main
        code, out = w.gate(routine)
        self.assertEqual(code, 1, out)
        self.assertIn("C4 BLOCK user/routines/invitations.md line 1: an e-mail address", out)
        self.assertNotIn("example.com", out)                               # a finding names the place, never the address
        self.assertEqual(w.main, before)
        w.push(routine, "alex")                                            # past the gate
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        [issue] = w.issues("open")
        self.assertIn("user/routines/invitations.md line 1: an e-mail address", issue["body"])
        self.assertNotIn("example.com", issue["body"] + out)

    def test_a_run_whose_main_moved_on_touches_nothing(self):
        w = self.w
        w.bypass("alex", b"x\n")
        w.run()
        [issue] = w.issues("open")
        old = w.main
        w.merge({"user/fields/chat.md": b"Who: later.\n"})                 # main moves on
        writes = len(w.api.writes)
        _, red, out = w.run(main=old)                                      # a re-run of the old run
        self.assertEqual((red, len(w.api.writes)), (0, writes), out)       # judged nothing, wrote nothing
        self.assertIn("its run judges it", out)
        self.assertEqual(w.issues("open"), [issue])

    def test_an_error_is_its_own_alarm_judges_only_its_check_and_is_not_known_for_long(self):
        w = self.w
        w.merge({"user/fields/chat.md": b"Who: v2.\n"})
        w.advance(37)
        w.run()                                                            # N5 is open
        del w.api.objects["apps/github-actions"]                           # the settings part cannot run
        w.merge({"user/fields/installed.toml": installed({**BASE_FILES, "user/fields/chat.md": b"Who: v2.\n"})})
        _, red, _ = w.run()
        self.assertEqual(red, 1)
        titles = sorted(i["title"] for i in w.issues("open"))
        self.assertEqual(titles, [alarm.TITLES["ERROR"]])                  # N5 ran clean and closed; the error is open
        w.api.objects["apps/github-actions"] = {"id": ACTIONS_ID}
        _, red, _ = w.run()
        self.assertEqual((red, w.issues("open")), (0, []))                 # the error is gone: closed by the machine
        del w.api.objects["apps/github-actions"]
        w.run()
        [error] = w.issues("open")
        w.api.close(ISSUES, error["number"], by="owner")                   # «known» — but an error is known only until the next run
        _, red, _ = w.run()
        self.assertEqual(red, 1)
        self.assertEqual(len(w.issues("open")), 1)

    def test_a_release_by_hand_is_an_expected_alarm_the_trusted_contact_closes(self):
        w = World(VALID_CONFIG + b'\n[alarm]\ntrusted_contact = "helper"\n')
        self.addCleanup(w.close)
        w.api.statuses[f"repos/{REPO}/collaborators/helper"] = 204
        w.push({"engine/rules/core.md": b"Core rules, v2.\n", ".github/workflows/check.yml": b"name: check v2\n",
                "user/engine.lock": b'release = "v0.2.0"\n'}, "Helper")
        w.release = "v0.2.0"
        _, red, _ = w.run()
        self.assertEqual(red, 1)
        [issue] = w.issues("open")
        self.assertIn("push by Helper", issue["body"])
        self.assertTrue(issue["body"].startswith("@helper @owner —"))      # both are told
        w.api.close(ISSUES, issue["number"], by="Helper")
        _, red, _ = w.run()
        self.assertEqual((red, w.issues("open")), (0, []))
        w.advance(20)
        w.merge({"user/fields/chat.md": b"Who: after the release.\n"})    # the next save is green
        _, red, _ = w.run()
        self.assertEqual(red, 0)

    def test_a_forged_marker_in_a_closed_issue_is_the_price_the_owner_took(self):
        # Grok Bot found it; the owner (23.10.2026; DESIGN §7) took it as a price, not a rework.
        w = self.w
        w.bypass("alex", b"x\n")
        w.run()
        [issue] = w.issues("open")
        w.api.close(ISSUES, issue["number"], by="alex")
        future = "main: force_push by alex at 2026-10-24T12:00:00Z — not through the gate"
        issue["body"] += f"\n<!-- alarm-line: {alarm.line_hash(future)} -->"   # the bot edits the closed Issue
        w.record("force_push", w.main, w.repo.base, "alex", T0 + timedelta(days=1))
        w.main = w.repo.base
        w._live()
        w.now = T0 + timedelta(days=1, hours=1)
        _, red, out = w.run()
        self.assertEqual((red, w.issues("open")), (0, []), out)            # silence, as accepted


if __name__ == "__main__":
    unittest.main()


CARD = "4111 1111 1111 1111"


class CopyScenarios(unittest.TestCase):
    """The nightly copy and its watch over days (docs/DESIGN.md §6, §9): what the owner and
    the trusted contact see on GitHub — the branch, the Issues, the gate."""

    def setUp(self):
        self.w = World()

    def tearDown(self):
        self.w.close()

    def test_the_copy_runs_every_night_and_the_watch_sees_it_alive(self):
        w = self.w
        for _ in range(3):
            code, red, out = w.night()
            self.assertEqual((code, red), (0, 0), out)
            _, gate_red, out = w.run()
            self.assertEqual(gate_red, 0, out)
            self.assertIn("N2 OK", out)
            w.advance(24)
        files = w.copied()
        self.assertEqual(sorted(files), ["README.md", "copy.json", "data/docs/_source.md", "data/docs/row0.md",
                                         "data/docs/row1.md", "data/docs/row2.md", "pages/home.md", "pages/notes.md"])
        self.assertIn("shopping list", files["pages/notes.md"])
        self.assertEqual(w.issues("open"), [])
        self.assertEqual(len(_git(w.copy_dir, "rev-list", "HEAD").splitlines()), 3)   # a stamp commit a night

    def test_a_small_base_that_vanishes_is_an_alarm_while_the_total_barely_moves(self):
        w = self.w                                      # Kimi's example (23.10.2026): the Documents base is a few rows among many
        w.notion.sources.append(source("ids", "Documents", "idsdb", "home"))
        w.notion.row_lists["ids"] = [page("id1", "Passport", {"type": "data_source_id", "data_source_id": "ids"})]
        w.notion.row_lists["docs"] = [page(f"row{i}", f"Document {i}", {"type": "data_source_id", "data_source_id": "docs"}) for i in range(12)]
        code, red, out = w.night()
        self.assertEqual((code, red), (0, 0), out)
        w.advance(24)
        w.notion.sources.pop()                          # the base is gone: 15 records → 14, a drop of 7 % — under the 20 % floor
        del w.notion.row_lists["ids"]
        code, red, out = w.night()
        self.assertEqual((code, red), (1, 1), out)
        issue = w.issues("open")[0]
        self.assertEqual(issue["title"], alarm.CHECK_TITLES["N3"])
        self.assertIn("the base data/ids (1 row) is gone from the copy since", issue["body"])
        self.assertNotIn("fell from", issue["body"])    # the total said nothing
        for _ in range(3):                              # held night after night (Issue #18, item 43): no longer closed the next night
            w.advance(24)
            code, red, out = w.night()
            self.assertEqual((code, red), (1, 1), out)
            self.assertEqual([i["number"] for i in w.issues("open")], [issue["number"]])
        self.assertEqual(w.comments(issue["number"]), [])                      # the same line every night: no new comment

    def test_a_base_she_removed_closes_once_her_decision_is_recorded(self):
        w = self.w
        w.night()
        w.advance(24)
        w.notion.sources.clear()                        # she deleted the Documents base
        w.notion.row_lists.pop("docs")
        code, red, out = w.night()
        self.assertEqual((code, red), (1, 1), out)
        [issue] = w.issues("open")
        self.assertIn("the base data/docs (3 rows) is gone from the copy", issue["body"])
        since = json.loads(w.copied()["copy.json"])["held"]["docs"]["since"]
        self.assertIn(f"since {since}", issue["body"])
        # The bot asks her in plain words; she says she deleted it; its PR with her yes records
        # the base and the day as the line names them.
        w.merge({"user/config.toml": VALID_CONFIG + f'\n[notion]\nremoved_bases = ["docs@{since}"]\n'.encode()})
        w.advance(24)
        code, red, out = w.night()
        self.assertEqual((code, red, w.issues("open")), (0, 0, []), out)
        self.assertIn("Clean", w.comments(issue["number"])[-1])
        self.assertEqual(json.loads(w.copied()["copy.json"])["held"], {})

    def test_a_base_unshared_by_mistake_closes_once_it_is_shared_again(self):
        w = self.w
        w.night()
        w.advance(24)
        gone = w.notion.sources.pop()                   # unshared from the integration: the copy cannot see it
        rows = w.notion.row_lists.pop("docs")
        w.night()
        w.advance(24)
        self.assertEqual(w.night()[:2], (1, 1))         # still held the next night
        w.notion.sources.append(gone)                   # she shares it again (the bot helped her)
        w.notion.row_lists["docs"] = rows
        w.advance(24)
        code, red, out = w.night()
        self.assertEqual((code, red, w.issues("open")), (0, 0, []), out)

    def test_a_whole_copy_emptied_is_held_too(self):
        w = self.w
        w.night()
        w.advance(24)
        w.notion.sources.clear()                        # everything unshared: only the copy's own files stay
        w.notion.pages.clear()
        w.notion.row_lists.clear()
        code, red, out = w.night()
        self.assertEqual((code, red), (1, 1), out)
        w.advance(24)
        code, red, out = w.night()                      # before item 43 this night was «Clean … Closing»
        self.assertEqual((code, red), (1, 1), out)
        self.assertIn("data/docs (3 rows) is gone from the copy", out)               # tonight's findings, not the body's
        self.assertNotIn("fell from", out)              # the total's share is that one night's
        w.advance(24)
        _, _, out = w.night()
        self.assertIn("data/docs (3 rows) is gone from the copy", out)
        number = w.issues("open")[0]["number"]
        self.assertEqual(len(w.comments(number)), 1)    # night 2 dropped the total's line; night 3 the same as night 2

    def test_a_clean_up_of_one_row_in_a_small_base_is_not_an_alarm(self):
        w = self.w
        w.night()
        w.advance(24)
        w.notion.row_lists["docs"].pop()               # 3 rows → 2: a third of a small base, 5 records → 4 (20 %, not over the floor)
        code, red, out = w.night()
        self.assertEqual((code, red, w.issues("open")), (0, 0, []), out)

    def test_a_copy_that_stops_is_an_alarm_until_it_runs_again(self):
        w = self.w
        w.night()
        w.advance(48)                                   # two nights without a copy
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        self.assertIn("the Notion copy stopped", w.issues("open")[0]["body"])
        self.assertEqual(w.issues("open")[0]["title"], alarm.CHECK_TITLES["N2"])
        w.night()                                       # it runs again
        _, red, out = w.run()
        self.assertEqual(red, 0, out)
        self.assertEqual(w.issues("open"), [])
        self.assertIn("Clean", w.comments(w.issues("closed")[0]["number"])[-1])

    def test_a_collaborator_rewriting_the_copy_is_an_alarm(self):
        w = self.w
        w.night()
        w.advance(1)
        w.record("force_push", w.backup_head, w.backup_head, "alex", ref="refs/heads/notion-backup")
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        self.assertIn("force_push by alex", w.issues("open")[0]["body"])
        self.assertIn("the copy's history was rewritten", out)

    def test_a_mass_deletion_in_notion_is_an_alarm_that_the_gate_leaves_alone_until_the_copy_clears_it(self):
        w = self.w
        w.night()
        w.advance(24)
        w.notion.row_lists["docs"] = w.notion.row_lists["docs"][:1]      # two of five records gone
        code, red, out = w.night()
        self.assertEqual((code, red), (1, 1), out)
        n3 = w.issues("open")[0]
        self.assertEqual(n3["title"], alarm.CHECK_TITLES["N3"])
        self.assertIn("fell from 5 to 3 records (40 %)", n3["body"])
        _, gate_red, out = w.run()                       # a save the next day: N3 is not the gate's to judge
        self.assertEqual(gate_red, 0, out)
        self.assertEqual(w.issues("open")[0]["number"], n3["number"])
        self.assertNotIn("N3", out)                      # the watch says nothing about the copy's checks
        w.advance(24)
        code, red, out = w.night()                       # the count holds: the copy itself clears it
        self.assertEqual((code, red), (0, 0), out)
        self.assertEqual(w.issues("open"), [])

    def test_a_card_number_typed_into_notion_never_reaches_git(self):
        w = self.w
        w.night()
        w.advance(24)
        w.notion.blocks["notes"] = [paragraph("n1", f"shopping list; card {CARD} pin 1234")]
        code, red, out = w.night()
        self.assertEqual((code, red), (1, 1), out)
        files = w.copied()
        self.assertFalse(any("4111" in text for text in files.values()))
        self.assertIn("[card number removed]", files["pages/notes.md"])
        self.assertIn("pin 1234", files["pages/notes.md"])          # a PIN is the bot's rule, not a machine's (§9)
        issue = w.issues("open")[0]
        self.assertEqual(issue["title"], alarm.CHECK_TITLES["N6"])
        self.assertIn("the Notion copy found this", issue["body"])      # not «the checks of main»
        self.assertNotIn("4111", issue["body"])
        self.assertIn("remove it from Notion", issue["body"])
        self.assertNotIn("4111", out)
        w.advance(24)
        w.notion.blocks["notes"] = [paragraph("n1", "shopping list; card removed")]
        code, red, out = w.night()
        self.assertEqual((code, red), (0, 0), out)
        self.assertEqual(w.issues("open"), [])

    def test_a_card_pasted_from_a_bank_is_cut_and_a_date_range_is_not(self):
        # Issue #18, item 44: bank text splits the groups with no-break spaces; the pattern of
        # the owner's copy also read some date ranges as cards.
        w = self.w
        w.night()
        w.advance(24)
        w.notion.blocks["notes"] = [paragraph("n1", "card 4111\u00a01111\u00a01111\u00a01111 12/30"),
                                    paragraph("n2", "trip 2026-02-22 - 2026-03-01")]
        code, red, out = w.night()
        self.assertEqual((code, red), (1, 1), out)
        notes = w.copied()["pages/notes.md"]
        self.assertNotIn("4111", notes)
        self.assertIn("card [card number removed] 12/30", notes)
        self.assertIn("trip 2026-02-22 - 2026-03-01", notes)
        self.assertIn("pages/notes.md: 1 card number(s) removed", w.issues("open")[0]["body"])
        w.advance(24)
        w.notion.blocks["notes"] = [paragraph("n2", "trip 2026-02-22 - 2026-03-01")]
        code, red, out = w.night()
        self.assertEqual((code, red, w.issues("open")), (0, 0, []), out)    # the date range alone is clean

    def test_notion_down_commits_nothing_and_raises_the_copys_own_error(self):
        w = self.w
        w.night()
        head = w.backup_head
        w.advance(24)
        w.notion.down = True
        code, red, out = w.night()
        self.assertEqual((code, red), (2, 1), out)
        self.assertEqual(w.backup_head, head)                          # no commit for that night
        self.assertEqual(w.issues("open")[0]["title"], alarm.ERROR_TITLES["backup"])
        w.advance(13)
        _, gate_red, out = w.run()                                     # 37 h since the copy: N2 says stopped
        self.assertEqual(gate_red, 1, out)
        self.assertEqual({i["title"] for i in w.issues("open")}, {alarm.ERROR_TITLES["backup"], alarm.CHECK_TITLES["N2"]})
        w.notion.down = False
        code, red, out = w.night()                                     # back: the same five records, no N3
        self.assertEqual((code, red), (0, 0), out)
        self.assertEqual([i["title"] for i in w.issues("open")], [alarm.CHECK_TITLES["N2"]])
        _, gate_red, out = w.run()
        self.assertEqual((gate_red, w.issues("open")), (0, []))

    # -- Issue #18, item 42: a copy that fails after Notion was read --------------------
    # Before: the write and the lock read stood outside the copy's `try`, a crash exited 1
    # («N3 or N6 found something»), backup.yml committed the half tree, and the only alarm
    # was «no readable report».

    def disk_full(self, rel: str):
        """From the write of this file of the copy on, the disk has no room: every write the
        night makes after it — the copy's and the report's — empties its file, then fails."""
        w, real, full = self.w, Path.write_text, []

        def write_text(path, *args, **kwargs):
            ours = w.copy_dir in path.parents or path.name.startswith("report.json")
            if ours and (full or path == w.copy_dir / rel):
                full.append(path)
                real(path, "")
                raise OSError(errno.ENOSPC, "No space left on device", str(path))
            return real(path, *args, **kwargs)
        return mock.patch.object(Path, "write_text", write_text)

    def test_a_full_disk_while_writing_commits_nothing_and_the_next_night_heals(self):
        w = self.w
        w.night()
        head, before = w.backup_head, w.copied()
        w.advance(24)
        with self.disk_full("pages/notes.md"):                        # `write` has already deleted the old tree
            code, red, out = w.night()
        self.assertEqual((code, red), (2, 1), out)
        self.assertIn("ERROR the copy could not be made: OSError", out)
        self.assertIn("ERROR the report could not be written: OSError", out)   # the same disk: no report at all
        self.assertEqual(w.backup_head, head)                          # no commit: the branch keeps last night's copy
        self.assertEqual(w.copied(), before)
        opened = w.issues("open")
        self.assertEqual([i["title"] for i in opened], [alarm.ERROR_TITLES["backup"]])
        self.assertIn("no readable report", opened[0]["body"])
        w.advance(24)
        code, red, out = w.night()                                     # room again: the copy runs, the alarm closes
        self.assertEqual((code, red), (0, 0), out)
        self.assertNotEqual(w.backup_head, head)
        self.assertEqual(w.issues("open"), [])

    def test_a_full_disk_on_the_stamp_keeps_an_open_shrink_alarm_open(self):
        # The night judged neither N3 nor N6 (alarm._concerns): an open N3 is not closed by it.
        w = self.w
        w.night()
        w.advance(24)
        w.notion.row_lists["docs"] = []                                # the Documents base emptied in Notion
        code, red, out = w.night()
        self.assertEqual((code, red), (1, 1), out)
        n3 = alarm.CHECK_TITLES["N3"]
        self.assertIn(n3, [i["title"] for i in w.issues("open")])
        w.advance(24)
        with self.disk_full("copy.json"):                              # every page written, the stamp not
            code, red, out = w.night()
        self.assertEqual(code, 2, out)
        self.assertEqual({i["title"] for i in w.issues("open")}, {n3, alarm.ERROR_TITLES["backup"]})

    def test_a_text_the_copy_cannot_write_is_an_error_every_night_until_it_is_fixed(self):
        w = self.w
        w.night()
        head = w.backup_head
        w.notion.blocks["notes"] = [paragraph("n1", "half an emoji \ud83d")]   # a lone surrogate: UTF-8 cannot hold it
        for _ in range(2):
            w.advance(24)
            code, red, out = w.night()
            self.assertEqual(code, 2, out)
            self.assertEqual(w.backup_head, head)
        self.assertIn("the copy could not be made: UnicodeEncodeError", w.issues("open")[0]["body"])
        _, gate_red, out = w.run()                                     # 48 h with no commit: N2 says the copy stopped
        self.assertEqual(gate_red, 1, out)
        self.assertEqual({i["title"] for i in w.issues("open")}, {alarm.ERROR_TITLES["backup"], alarm.CHECK_TITLES["N2"]})
        w.notion.blocks["notes"] = [paragraph("n1", "shopping list")]
        w.advance(24)
        code, red, out = w.night()
        self.assertEqual((code, red), (0, 0), out)
        _, gate_red, out = w.run()
        self.assertEqual((gate_red, w.issues("open")), (0, []))

    def test_a_broken_lock_on_main_commits_nothing(self):
        w = self.w
        w.night()
        head = w.backup_head
        w.advance(24)
        w.push({"user/engine.lock": b"release = \n"}, "alex")          # around the gate; N1 has its own say
        code, red, out = w.night()
        self.assertEqual((code, red), (2, 1), out)
        self.assertEqual(w.backup_head, head)
        self.assertIn("the copy could not be made: TOMLDecodeError", w.issues("open")[0]["body"])
        w.push({"user/engine.lock": BASE_FILES["user/engine.lock"]}, "alex")
        w.advance(24)
        code, red, out = w.night()
        self.assertEqual((code, red), (0, 0), out)
        self.assertEqual(w.issues("open"), [])

    def test_a_crash_the_copy_does_not_foresee_commits_nothing(self):
        # An exception outside run.FAILS: `python3 -m` exits 1 with no report, and backup.yml
        # takes that for a crash, not for findings. Last night's report is not tonight's.
        w = self.w
        w.night()
        head = w.backup_head
        w.advance(24)
        with mock.patch.object(backup_run.copying, "write", side_effect=KeyError("a bug")):
            code, red, out = w.night()
        self.assertEqual((code, red), (1, 1), out)
        self.assertIn("CRASH KeyError", out)
        self.assertEqual(w.backup_head, head)
        opened = w.issues("open")
        self.assertEqual([i["title"] for i in opened], [alarm.ERROR_TITLES["backup"]])
        self.assertIn("no readable report", opened[0]["body"])
        w.advance(24)
        code, red, out = w.night()
        self.assertEqual((code, red), (0, 0), out)
        self.assertEqual(w.issues("open"), [])

    def test_an_excluded_page_stays_out_and_the_first_night_has_no_shrink(self):
        w = World(VALID_CONFIG + b'\n[notion]\nbackup_exclude = ["notes"]\n')
        try:
            code, red, out = w.night()
            self.assertEqual((code, red), (0, 0), out)
            self.assertNotIn("pages/notes.md", w.copied())
            self.assertIn("pages/home.md", w.copied())
            self.assertIn("COPY records=4", out)
        finally:
            w.close()


    # -- Issue #18, items 45–46: found on the owner's own copy (notion-backup PR #1) ----------

    def test_with_exclusions_a_page_shared_without_its_parent_stops_the_night(self):
        w = World(VALID_CONFIG + b'\n[notion]\nbackup_exclude = ["notes"]\n')
        try:
            code, red, out = w.night()
            self.assertEqual((code, red), (0, 0), out)
            head = w.backup_head
            w.advance(24)
            # a sub-page of a page the integration cannot see — it may hang under the excluded one
            w.notion.pages.append(page("diary", "Shared alone", {"type": "page_id", "page_id": "private"}))
            code, red, out = w.night()
            self.assertEqual((code, red), (2, 1), out)
            self.assertEqual(w.backup_head, head)                          # nothing committed, nothing «just in case»
            self.assertNotIn("pages/diary.md", w.copied())
            self.assertIn("diary: the page private above it is not shared with the integration",
                          w.issues("open")[0]["body"])
            w.advance(24)
            w.notion.pages = [p for p in w.notion.pages if p["id"] != "diary"]   # unshared again
            code, red, out = w.night()
            self.assertEqual((code, red), (0, 0), out)
            self.assertEqual(w.issues("open"), [])
        finally:
            w.close()

    def test_a_typo_in_the_exclusions_on_main_never_copies_the_excluded_page(self):
        w = World(VALID_CONFIG + b'\n[notion]\nbackup_exclude = ["notes"]\n')
        try:
            w.night()
            w.advance(24)
            w.push({"user/config.toml": VALID_CONFIG + b'\n[notion]\nbackup_exlude = ["notes"]\n'}, "alex")   # around the gate
            code, red, out = w.night()
            self.assertEqual((code, red), (2, 1), out)
            self.assertIn("the settings are not usable: unknown key notion.backup_exlude", w.issues("open")[0]["body"])
            self.assertNotIn("pages/notes.md", w.copied())
            w.push({"user/config.toml": VALID_CONFIG + b'\n[notion]\nbackup_exclude = ["notes"]\n'}, "alex")
            w.advance(24)
            code, red, out = w.night()
            self.assertEqual((code, red), (0, 0), out)
            self.assertNotIn("notes", _git(w.copy_dir, "log", "--all", "--name-only", "--format="))   # no night ever had it
            # A number instead of an id (Kimi, review of PR #23): the same stop, not «exclude nothing».
            w.push({"user/config.toml": VALID_CONFIG + b'\n[notion]\nbackup_exclude = [123]\n'}, "alex")
            w.advance(24)
            code, red, out = w.night()
            self.assertEqual((code, red), (2, 1), out)
            self.assertIn("notion.backup_exclude must be a list of strings", w.issues("open")[0]["body"])
            self.assertNotIn("notes", _git(w.copy_dir, "log", "--all", "--name-only", "--format="))
        finally:
            w.close()


class AdversarialCopyScenarios(unittest.TestCase):
    """What a collaborator's token, a flaky Notion or a hostile page can do to the copy and
    its watch — the owner's safety check before the second reader (ADR-0033)."""

    def setUp(self):
        self.w = World()

    def tearDown(self):
        self.w.close()

    def test_the_bots_account_cannot_replace_or_rewrite_the_copy_unseen(self):
        w = self.w
        w.night()
        w.advance(1)
        w.record("push", w.backup_head, "f" * 40, "alex", ref="refs/heads/notion-backup")   # a «clean» copy pushed by hand
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        body = w.issues("open")[0]["body"]
        self.assertIn("push by alex", body)
        self.assertIn("not the backup workflow", body)
        self.assertIn("a change GitHub did not record", body)      # the branch head is not what the log ends at
        w.record("force_push", "f" * 40, w.backup_head, "alex", ref="refs/heads/notion-backup")   # history rewritten back
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        self.assertIn("force_push by alex", w.comments(w.issues("open")[0]["number"])[-1])

    def test_a_deleted_copy_branch_is_an_alarm_and_the_next_night_starts_afresh(self):
        w = self.w
        w.night()
        w.advance(24)
        w.night()
        w.advance(1)
        w.delete_copy_branch("alex")
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        self.assertIn("branch_deletion by alex", w.issues("open")[0]["body"])
        w.advance(23)
        code, red, out = w.night()                       # the workflow makes a new branch; nothing to compare with
        self.assertEqual((code, red), (0, 0), out)
        self.assertIn("COPY records=5", out)
        _, red, out = w.run()                            # the deletion is still inside the window
        self.assertEqual(red, 1, out)
        self.assertEqual(len(w.issues("open")), 1)

    def test_a_closed_stopped_copy_alarm_stays_closed_for_the_same_stop(self):
        w = self.w
        w.night()
        w.advance(48)
        _, red, _ = w.run()
        self.assertEqual(red, 1)
        n2 = w.issues("open")[0]
        w.api.close(ISSUES, n2["number"], by="owner")    # «known, not fixing»
        w.advance(24)
        _, red, out = w.run()
        self.assertEqual((red, w.issues("open")), (0, []), out)
        w.night()                                        # it runs again: nothing to say
        _, red, out = w.run()
        self.assertEqual((red, w.issues("open")), (0, []), out)

    def test_notion_dying_half_way_commits_nothing(self):
        w = self.w
        w.night()
        head = w.backup_head
        w.advance(24)
        w.notion.fail_at = 4                             # after the searches, inside the rows
        code, red, out = w.night()
        self.assertEqual((code, red), (2, 1), out)
        self.assertEqual(w.backup_head, head)
        self.assertIn('"records": 5', w.copied()["copy.json"])
        self.assertEqual(w.issues("open")[0]["title"], alarm.ERROR_TITLES["backup"])

    def test_a_card_number_in_a_table_cell_or_a_property_is_cut_too(self):
        w = self.w
        w.notion.blocks["notes"] = [{"id": "t", "type": "table", "has_children": True, "table": {"table_width": 2},
                                     "_children": [{"id": "r", "type": "table_row", "table_row": {"cells": [rich("card"), rich(CARD)]}}]}]
        w.notion.row_lists["docs"][0]["properties"]["Card"] = {"id": "c", "type": "rich_text", "rich_text": rich(CARD)}
        code, red, out = w.night()
        self.assertEqual((code, red), (1, 1), out)
        files = w.copied()
        self.assertFalse(any("4111" in text for text in files.values()))
        self.assertIn("| card | [card number removed] |", files["pages/notes.md"])
        self.assertIn("- **Card:** [card number removed]", files["data/docs/row0.md"])
        body = w.issues("open")[0]["body"]
        self.assertEqual(len(alarm.LINE_RE.findall(body)), 2)
        self.assertNotIn("4111", body + out)

    def test_a_config_that_excludes_everything_is_a_shrink_alarm(self):
        w = self.w
        w.night()
        w.advance(24)
        w.merge({"user/config.toml": VALID_CONFIG + b'\n[notion]\nbackup_exclude = ["home"]\n'})
        code, red, out = w.night()
        self.assertEqual((code, red), (1, 1), out)
        self.assertIn("fell from 5 to 0 records (100 %)", w.issues("open")[0]["body"])
        self.assertEqual(sorted(w.copied()), ["README.md", "copy.json"])

    def test_a_hostile_page_title_never_reaches_the_alarm_or_the_run_log(self):
        w = self.w                                           # machine output carries no titles (DESIGN §11, plan decision 58)
        title = "@stranger <!-- alarm-line: " + "0" * 64 + " --> ``` end"
        w.notion.pages[1]["properties"]["Name"]["title"] = rich(title)
        w.notion.blocks["notes"] = [paragraph("n1", f"card {CARD}")]
        code, red, out = w.night()
        self.assertEqual((code, red), (1, 1), out)
        body = w.issues("open")[0]["body"]
        for text in (body, out):
            self.assertNotIn("@stranger", text)              # no ping, no forged marker, no fence: the title is not there at all
            self.assertNotIn("0" * 64, text)
        self.assertIn("pages/notes.md: 1 card number(s) removed", body)
        hashes = alarm.LINE_RE.findall(body)
        self.assertEqual(len(hashes), 1)
        self.assertEqual(body.count("```"), 2)

    def test_a_push_that_fails_after_a_clean_copy_is_an_alarm_the_same_night(self):
        w = self.w
        w.night()
        head = w.backup_head
        w.advance(24)
        code, red, out = w.night(push_fails=True)
        self.assertEqual((code, red), (0, 1), out)                  # the copy was fine; the night was not
        self.assertEqual(w.backup_head, head)
        issue = w.issues("open")[0]
        self.assertEqual(issue["title"], alarm.ERROR_TITLES["backup"])
        self.assertIn("could not be committed or pushed", issue["body"])
        w.advance(24)
        code, red, out = w.night()                                   # the next night pushes: the error closes
        self.assertEqual((code, red), (0, 0), out)
        self.assertEqual(w.issues("open"), [])

    def test_a_deletion_after_a_failed_night_is_measured_against_the_last_good_copy(self):
        w = self.w
        w.night()
        w.advance(24)
        w.notion.down = True
        w.night()
        w.advance(24)
        w.notion.down = False
        w.notion.row_lists["docs"] = []
        code, red, out = w.night()
        self.assertEqual(code, 1, out)
        self.assertTrue(any("fell from 5 to 2 records (60 %)" in i["body"] for i in w.issues("open")), out)


class RoutineScenarios(unittest.TestCase):
    """The bot's daily mark and N4 over days (docs/DESIGN.md §6): a routine that dies, a
    token that grows, a collaborator who closes or renames the Issue."""

    def setUp(self):
        self.w = World()

    def tearDown(self):
        self.w.close()

    def test_before_installation_n4_is_a_note_and_the_gate_is_green(self):
        _, red, out = self.w.run()
        self.assertEqual(red, 0, out)
        self.assertIn("N4 NOTE no open Issue «routine marks» and no alarm.marks_issue", out)

    def test_daily_marks_keep_n4_clean_and_a_dead_routine_is_an_alarm_until_it_returns(self):
        w = self.w
        w.install_marks()
        for _ in range(3):
            w.mark()
            _, red, out = w.run()
            self.assertEqual(red, 0, out)
            self.assertIn("N4 OK", out)
            w.advance(24)
        w.advance(24)                                       # a second day without a mark
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        issue = w.alarms("open")[0]
        self.assertEqual(issue["title"], alarm.CHECK_TITLES["N4"])
        self.assertIn("the bot's daily routine stopped", issue["body"])
        w.mark()
        _, red, out = w.run()
        self.assertEqual((red, w.alarms("open")), (0, []), out)

    def test_a_token_that_grew_is_an_alarm_the_same_day(self):
        w = self.w
        w.install_marks()
        w.mark()
        w.advance(24)
        w.mark(scopes="repo,workflow")
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        self.assertIn("has the scope(s) workflow — more than repo", w.alarms("open")[0]["body"])
        w.advance(24)
        w.mark(scopes="repo")                               # the right was taken away again
        _, red, out = w.run()
        self.assertEqual((red, w.alarms("open")), (0, []), out)

    def test_closing_the_marks_issue_does_not_silence_n4(self):
        w = self.w
        w.install_marks()
        w.mark()
        w.api.close(ISSUES, World.MARKS, by="alex")
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        self.assertIn("where the bot's marks go, is closed", w.alarms("open")[0]["body"])

    def test_renaming_the_marks_issue_is_an_alarm_until_the_title_returns(self):
        w = self.w
        w.install_marks()
        w.mark()
        issue = w.api.objects[f"{ISSUES}/{World.MARKS}"]
        issue["title"] = "routine marks (old)"              # a collaborator renamed it; the routine looks for the exact title
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        self.assertIn("is titled «routine marks (old)», not «routine marks»", w.alarms("open")[0]["body"])
        issue["title"] = "routine marks"                    # renamed back: the machine closes the alarm
        _, red, out = w.run()
        self.assertEqual((red, w.alarms("open")), (0, []), out)

    def test_a_mark_from_another_account_does_not_count(self):
        w = self.w
        w.install_marks()
        w.mark(who="owner")
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        self.assertIn("no mark from the bot's routine by alex", w.alarms("open")[0]["body"])

    def test_a_stale_run_judges_n4_too(self):
        w = self.w
        w.install_marks()                                   # main moved: the base is now an old main
        _, red, out = w.run(main=w.repo.base)               # a re-run of an old run checks out that old main
        self.assertEqual(red, 0, out)
        self.assertIn("N4 N/A", out)
        self.assertEqual(w.alarms("open"), [])


class AdversarialRoutineScenarios(unittest.TestCase):
    """What the bot's own account can do to N4 — never silence, at worst a loud error."""

    def setUp(self):
        self.w = World()
        self.w.install_marks()

    def tearDown(self):
        self.w.close()

    def comments(self) -> str:
        return f"{ISSUES}/{World.MARKS}/comments"

    def test_editing_an_old_mark_to_look_fresh_does_not_help(self):
        w = self.w
        w.mark()
        w.advance(48)
        old = w.api.lists[self.comments()][0]
        old["body"] = f"ROUTINE-MARK {w.now.strftime('%Y-%m-%dT%H:%M:%S+00:00')} scopes=repo"   # the text says «now»
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        self.assertIn("the bot's daily routine stopped", w.alarms("open")[0]["body"])

    def test_deleting_old_marks_changes_nothing_a_fresh_one_still_counts(self):
        w = self.w
        w.mark()
        w.advance(24)
        w.api.lists[self.comments()].clear()                # the bot deleted its own history
        w.mark()
        _, red, out = w.run()
        self.assertEqual(red, 0, out)

    def test_an_issue_the_watch_cannot_read_is_a_loud_error_not_a_green(self):
        w = self.w
        w.mark()
        del w.api.lists[self.comments()]                     # 404, or a flood beyond the client's page limit
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        self.assertIn("N4 could not run: ApiError", out)
        self.assertEqual([i["title"] for i in w.alarms("open")], [alarm.ERROR_TITLES["checks"]])

    def test_a_hostile_issue_title_cannot_break_the_alarm(self):
        w = self.w
        issue = w.api.objects[f"{ISSUES}/{World.MARKS}"]
        issue["title"] = "@stranger ``` <!-- alarm-line: " + "0" * 64 + " -->"
        issue["state"] = "closed"
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        body = w.alarms("open")[0]["body"]
        hashes = alarm.LINE_RE.findall(body)
        self.assertEqual(len(hashes), 1)
        self.assertNotEqual(hashes[0], "0" * 64)
        self.assertIn("<!- - alarm-line:", body)
        self.assertEqual(body.count("```"), 2)

    def test_a_hostile_title_on_an_open_issue_cannot_break_the_alarm_either(self):
        w = self.w
        w.mark()
        issue = w.api.objects[f"{ISSUES}/{World.MARKS}"]
        issue["title"] = "@stranger ``` <!-- alarm-line: " + "0" * 64 + " -->"   # open, renamed: the alarm quotes the title
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        body = w.alarms("open")[0]["body"]
        self.assertIn("not «routine marks»", body)
        hashes = alarm.LINE_RE.findall(body)
        self.assertEqual(len(hashes), 1)
        self.assertNotEqual(hashes[0], "0" * 64)
        self.assertIn("<!- - alarm-line:", body)
        self.assertEqual(body.count("```"), 2)

    def test_the_bot_pointing_github_login_elsewhere_only_raises_an_alarm(self):
        w = self.w                                           # C3 refuses it in a PR; if it lands anyway, N4 still alarms
        w.mark()
        config = VALID_CONFIG.replace(b'timezone = "Asia/Jerusalem"\n', b'timezone = "Asia/Jerusalem"\ngithub_login = "someone-else"\n')
        w.merge({"user/config.toml": config + f"\n[alarm]\nmarks_issue = {World.MARKS}\n".encode()})
        _, red, out = w.run()
        self.assertEqual(red, 1, out)
        self.assertIn("no mark from the bot's routine by someone-else", w.alarms("open")[0]["body"])

    def test_junk_around_the_mark_line_is_tolerated_but_a_wrong_line_is_not(self):
        w = self.w
        w.api.lists[self.comments()].append({"user": {"login": "alex"}, "created_at": w.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                             "body": "Daily mark:\nROUTINE-MARK 2026-10-24T07:35:00+03:00 scopes=repo\nhave a nice day"})
        _, red, out = w.run()
        self.assertEqual(red, 0, out)
        w.advance(24)
        w.api.lists[self.comments()].append({"user": {"login": "alex"}, "created_at": w.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                             "body": "ROUTINE MARK ok scopes repo"})
        w.advance(24)
        _, red, out = w.run()                                # the last well-formed mark is 48 h old
        self.assertEqual(red, 1, out)
        self.assertIn("stopped", w.alarms("open")[0]["body"])

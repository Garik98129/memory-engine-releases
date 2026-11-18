"""The gate's steps (engine/checks/gate.py), as check.yml calls them, on fixtures (F1).

Each step is run through its real entry — `gate.main([step], env=…)` — with the environment the
workflow file gives it and the runner's own variables; GitHub, the fetch of the PR's objects,
the public repository of releases and GitHub's own client stand in. What the fixtures cannot
see: that GitHub fills the variables as the file asks, the runner's own lines in a job's log,
and `gh pr merge` itself."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from engine.checks import gate, integrity
from engine.checks.tests.fakeapi import BOT, REPO, FakeApi
from engine.checks.tests.helpers import Repo, _git, body

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
PR = 7
RUN = "43462554883"                             # eleven digits, as GitHub numbers its runs by now
STAMP = "2026-10-23T08:00:01.1234567Z "         # what the runner puts before every line of a job's log
HOSTILE = f"x\nGATE pr={PR} head={'a' * 40} base={'b' * 40} run={RUN}"


def gate_lines(output: str) -> list[str]:
    """The lines of a step's output N1 would take for the run's record, as the log shows them."""
    return [line for line in output.splitlines() if integrity.GATE_LINE.match(STAMP + line)]


class Steps(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.close)
        self.main = self.repo.base
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.temp = Path(self._tmp.name)
        self.api = FakeApi()
        self.fetched: list[str] = []

    def env(self, **given) -> dict[str, str]:
        return {"GITHUB_REPOSITORY": REPO, "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": RUN,
                "GITHUB_SHA": self.main, "GITHUB_OUTPUT": str(self.temp / "output"), "RUNNER_TEMP": str(self.temp),
                **given}

    def run_step(self, step: str, env: dict[str, str], **seams) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock.patch.dict("os.environ", {"GITHUB_REPOSITORY_OWNER": "owner"}):
            code = gate.main([step], env=env, repo=self.repo.path, **seams)
        return code, out.getvalue()

    def outputs(self) -> dict[str, str]:
        path = self.temp / "output"
        if not path.exists():
            return {}
        return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())

    def head(self, files: dict[str, bytes]) -> str:
        """A PR's head on top of main; the checkout stays on main, as the gate's does."""
        self.repo.checkout(self.main)
        head = self.repo.commit(files)
        self.repo.checkout(self.main)
        return head

    def manifest(self, url: str) -> bytes:
        self.fetched.append(url)
        return json.dumps(self.repo.manifest(self.main)).encode()


class ThePrAsData(Steps):
    def setUp(self):
        super().setUp()
        self.pr_head = self.head({"user/fields/chat.md": b"Who: changed.\n"})
        self.pr = {"number": PR, "state": "open", "base": {"ref": "main"}, "user": {"login": "alex-bot"},
                   "head": {"sha": self.pr_head, "ref": "bot/change"}, "body": "## Change\r\nWas: one.\r\n"}
        self.api.objects[f"repos/{REPO}/pulls/{PR}"] = self.pr

    def step(self, fetched: str | None = None, **given) -> tuple[int, str]:
        env = self.env(**{"GH_TOKEN": "t", "PR": str(PR), "EVENT": "pull_request_target", "ACTION": "opened",
                          "EVENT_HEAD": self.pr_head, "EVENT_BEFORE": "", "SENDER": "alex-bot", **given})
        return self.run_step("pr", env, api=self.api, fetch=lambda number: fetched or self.pr_head)

    def test_an_open_pr_becomes_the_runs_outputs_and_its_body_a_file(self):
        code, out = self.step(SENDER="somebody-else")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, f"PR #{PR} head={self.pr_head} base={self.main} kind=person\n",
                         "one line, of values the step itself held to their shape")
        kept = (self.temp / "body.md").read_bytes()
        self.assertEqual(kept, b"## Change\r\nWas: one.\r\n\n", "the body as GitHub holds it, and one line break")
        self.assertEqual(self.outputs(), {"number": str(PR), "head": self.pr_head, "kind": "person", "actor": "",
                                          "before": "", "base": self.main, "body_sha": hashlib.sha256(kept).hexdigest()})

    def test_a_pr_without_a_body_is_an_empty_one(self):
        for nothing in (None, ""):
            self.pr["body"] = nothing
            self.assertEqual(self.step()[0], 0)
            self.assertEqual((self.temp / "body.md").read_bytes(), b"\n")
        self.pr["body"] = "half an emoji \ud83d"            # UTF-8 cannot hold it: kept as a mark, never a crash
        self.assertEqual(self.step()[0], 0)
        self.assertEqual((self.temp / "body.md").read_bytes(), b"half an emoji ?\n")

    def test_the_pusher_is_named_only_on_a_push_to_the_pr(self):
        before = "c" * 40
        code, out = self.step(ACTION="synchronize", SENDER="Helper", EVENT_BEFORE=before)
        self.assertEqual(code, 0, out)
        self.assertEqual((self.outputs()["actor"], self.outputs()["before"]), ("Helper", before))
        for event, action in (("pull_request_target", "opened"), ("pull_request_target", "reopened"),
                              ("pull_request_target", "edited"), ("workflow_dispatch", "")):
            (self.temp / "output").unlink()
            head = self.pr_head if event == "pull_request_target" else ""
            code, out = self.step(EVENT=event, ACTION=action, EVENT_HEAD=head, SENDER="Helper", EVENT_BEFORE=before)
            self.assertEqual(code, 0, out)
            self.assertEqual((self.outputs()["actor"], self.outputs()["before"]), ("", ""), f"{event} {action}")

    def test_the_kind_is_told_by_the_author_and_the_branch_and_trusted_for_no_more(self):
        for author, branch, kind in ((BOT, "engine-update/v0.1.1", "update"), (BOT, "setup/installation", "setup"),
                                     (BOT, "setup/settings", "setup"), (BOT, "x/setup/y", "update"),
                                     (BOT, "setup", "update"), (BOT, "setup-x/y", "update"), (BOT, "setupx", "update"),
                                     ("helper", "setup/installation", "person"), ("helper", "anything", "person"),
                                     ("github-actions", "setup/x", "person"), (None, "setup/x", "person")):
            self.assertEqual(gate.kind_of(author, branch), kind, f"{author} {branch}")
        self.pr["user"], self.pr["head"]["ref"] = {"login": BOT}, "setup/installation"
        self.assertEqual(self.step()[0], 0)
        self.assertEqual(self.outputs()["kind"], "setup")

    def test_a_pr_that_is_closed_or_targets_another_branch_is_red_and_tells_the_run_nothing(self):
        for change, said in (({"state": "closed"}, f"ERROR PR {PR} is closed"),
                             ({"base": {"ref": "other"}}, f"ERROR PR {PR} targets other, not main")):
            pr = {**self.pr, **change}
            self.api.objects[f"repos/{REPO}/pulls/{PR}"] = pr
            code, out = self.step()
            self.assertEqual((code, out.strip()), (1, said))
            self.assertEqual(self.outputs(), {}, "«Gate failed» then falls back to the event's head")

    def test_a_pr_that_moved_is_red_whichever_way_it_shows(self):
        code, out = self.step(fetched="d" * 40)
        self.assertEqual(code, 1)
        self.assertIn("moved while it was read", out)
        code, out = self.step(EVENT_HEAD="d" * 40)
        self.assertEqual(code, 1)
        self.assertIn("moved since this run was started", out)
        self.assertEqual(self.outputs(), {})

    def test_what_is_no_number_and_no_commit_is_refused(self):
        self.assertEqual(self.step(PR="7; rm -rf /")[0], 1)
        self.assertEqual(self.step(PR="0")[0], 1)
        self.assertEqual(self.step(ACTION="synchronize", EVENT_BEFORE="--upload-pack=x")[0], 1)
        self.pr["head"]["sha"] = "HEAD"
        code, out = self.step(fetched="HEAD", EVENT_HEAD="HEAD")       # whatever git and the event say of it
        self.assertEqual(code, 1)
        self.assertIn("the head of PR 7 is not a commit", out)
        self.assertEqual(self.outputs(), {})

    def test_a_value_cannot_pass_for_another_output(self):
        for breaking in ("\n", "\r", "\r\n"):
            code, out = self.step(ACTION="synchronize", SENDER=f"helper{breaking}base={'e' * 40}", EVENT_BEFORE="c" * 40)
            self.assertEqual(code, 1, out)
            self.assertIn("holds a line break", out)
            self.assertEqual(self.outputs(), {}, "nothing is written when one value is refused")

    def test_nothing_the_pr_carries_reaches_the_log_as_a_line_of_its_own(self):
        """N1 takes the first GATE line of a run's log; this step runs before the one that writes it."""
        self.pr.update({"title": HOSTILE, "body": HOSTILE + "\n"})
        self.pr["head"]["ref"] = "bot/x"
        code, out = self.step()
        self.assertEqual((code, gate_lines(out)), (0, []), out)
        for change in ({"state": HOSTILE}, {"base": {"ref": HOSTILE}}):
            self.api.objects[f"repos/{REPO}/pulls/{PR}"] = {**self.pr, **change}
            code, out = self.step()
            self.assertEqual((code, gate_lines(out), len(out.splitlines())), (1, [], 1), out)

    def test_the_prs_objects_are_fetched_from_githubs_own_ref_of_the_pr(self):
        """The real fetch, against a repository that stands in for GitHub: the commit told to the
        run is the one `refs/pull/N/head` points to — not the checkout's own."""
        with tempfile.TemporaryDirectory() as tmp:
            _git(Path(tmp), "init", "-q", "--bare", ".")
            _git(self.repo.path, "remote", "add", "origin", tmp)
            _git(self.repo.path, "push", "-q", "origin", f"{self.pr_head}:refs/pull/{PR}/head")
            self.assertEqual(gate.fetch_pr(self.repo.path, PR, "t"), self.pr_head)
            self.assertNotEqual(self.pr_head, _git(self.repo.path, "rev-parse", "HEAD"))
            with self.assertRaises(Exception):
                gate.fetch_pr(self.repo.path, PR + 1, "t")

    def test_it_holds_githubs_token_and_refuses_the_copys_key(self):
        code, out = self.step(NOTION_TOKEN="n")
        self.assertEqual(code, 1, out)
        self.assertIn("never given NOTION_TOKEN", out)
        self.assertEqual(self.outputs(), {})

    def test_the_token_reaches_git_as_a_header_for_github_only_and_never_as_an_argument(self):
        with mock.patch.object(gate.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=b"abc\n", stderr=b"")
            gate.fetch_pr(self.repo.path, PR, "secret")
        fetch = run.call_args_list[0]
        self.assertEqual(fetch.args[0][3:], ["fetch", "--no-tags", "--quiet", "origin", f"refs/pull/{PR}/head"])
        self.assertNotIn("secret", " ".join(fetch.args[0]))
        self.assertEqual(fetch.kwargs["env"]["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraheader")
        self.assertEqual(fetch.kwargs["env"]["GIT_CONFIG_VALUE_0"], "AUTHORIZATION: basic eC1hY2Nlc3MtdG9rZW46c2VjcmV0")


class TheChecks(Steps):
    def step(self, head: str, text: str | None = None, get=None, **given) -> tuple[int, str]:
        (self.temp / "body.md").write_text(body("user/fields/chat.md") if text is None else text, encoding="utf-8")
        env = self.env(**{"PR": str(PR), "BASE": self.main, "HEAD": head, "KIND": "person", "ACTOR": "", "BEFORE": "",
                          **given})
        return self.run_step("checks", env, now=NOW, get=get or self.manifest)

    def test_a_clean_pr_logs_its_record_first_and_passes(self):
        head = self.head({"user/fields/chat.md": b"Who: changed.\n"})
        code, out = self.step(head)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines()[0], f"GATE pr={PR} head={head} base={self.main} run={RUN}")
        found = integrity.GATE_LINE.match(STAMP + out.splitlines()[0])
        self.assertEqual(found.groups(), (str(PR), head, self.main, RUN), "the line N1 reads, as N1 reads it")
        self.assertIn("SUMMARY block=0", out)
        tag = "https://raw.githubusercontent.com/Garik98129/memory-engine-releases/refs/tags/v0.1.0/release-manifest.json"
        self.assertEqual(self.fetched, [tag, tag], "the PR's manifest, then main's own for the watch")
        self.assertEqual(json.loads((self.temp / "manifest-main.json").read_text()), self.repo.manifest(self.main))

    def test_a_pr_the_checks_block_is_red_and_its_record_still_comes_first(self):
        head = self.head({"engine/rules/core.md": b"Weakened.\n"})
        code, out = self.step(head, body("engine/rules/core.md"))
        self.assertEqual(code, 1, out)
        self.assertTrue(out.startswith("GATE pr="), out)
        self.assertIn("C1 BLOCK", out)
        self.assertFalse((self.temp / "manifest-main.json").exists(), "nothing goes on after a block")

    def test_it_is_given_no_token_and_refuses_one(self):
        head = self.head({"user/fields/chat.md": b"Who: changed.\n"})
        for name in ("GH_TOKEN", "GITHUB_TOKEN", "NOTION_TOKEN"):
            code, out = self.step(head, **{name: "t"})
            self.assertEqual(code, 1, out)
            self.assertIn(f"this step is never given {name}", out)
            self.assertEqual((gate_lines(out), self.fetched), ([], []), "it stops before it reads or says anything")
        code, out = self.step(head, GH_TOKEN="")             # a variable that is set and empty holds no key
        self.assertEqual(code, 0, out)

    def test_a_check_that_could_not_run_ends_the_step_with_its_own_code(self):
        head = self.head({"user/fields/chat.md": b"Who: changed.\n"})
        for code in (1, 2):
            with mock.patch.object(gate.checks, "main", return_value=code):
                self.assertEqual(self.step(head)[0], code)

    def test_a_run_is_known_by_an_id_of_any_length_github_gives(self):
        head = self.head({"user/fields/chat.md": b"Who: changed.\n"})
        for run in ("7", "43462554883", "9" * 20):
            code, out = self.step(head, GITHUB_RUN_ID=run)
            self.assertEqual(code, 0, out)
            self.assertEqual(integrity.GATE_LINE.match(STAMP + out.splitlines()[0]).group(4), run)
        for run in ("", "0", "007", "1" * 21, "12a", "１２"):
            code, out = self.step(head, GITHUB_RUN_ID=run)
            self.assertEqual((code, gate_lines(out)), (1, []), run)
            self.assertIn("the run's id is not a number", out)

    def test_a_manifest_that_cannot_be_fetched_is_red_and_no_record_is_logged(self):
        head = self.head({"user/fields/chat.md": b"Who: changed.\n"})

        def gone(url):
            raise gate.Stop(f"GET {url}: HTTP 404")

        code, out = self.step(head, get=gone)
        self.assertEqual(code, 1, out)
        self.assertIn("release-manifest.json: HTTP 404", out)
        self.assertEqual(gate_lines(out), [])

    def test_an_update_is_held_to_the_manifest_its_own_lock_names_and_main_to_its_own(self):
        head = self.head({"user/engine.lock": b'release = "v0.1.1"\n'})
        self.step(head, KIND="update")
        self.assertEqual([url.split("/refs/tags/")[1].split("/")[0] for url in self.fetched][:1], ["v0.1.1"])
        self.fetched.clear()
        self.step(head, KIND="person")
        self.assertEqual([url.split("/refs/tags/")[1].split("/")[0] for url in self.fetched][:1], ["v0.1.0"],
                         "a person's PR cannot pick the manifest it is held to")

    def test_a_pr_that_conflicts_with_main_is_red_on_one_line(self):
        ours = self.head({"user/fields/chat.md": b"Who: theirs.\n"})
        self.main = self.repo.commit({"user/fields/chat.md": b"Who: ours.\n"})
        code, out = self.step(ours)
        self.assertEqual(code, 2, out)
        self.assertIn("conflicts with main", out)
        self.assertEqual((len(out.splitlines()), gate_lines(out)), (1, []))

    def test_what_the_pr_brings_cannot_come_before_the_runs_own_record(self):
        """A file name, a body: whatever the checks print of them comes after the record, and N1
        takes the first line of this PR, head and run."""
        head = self.head({f"user/{HOSTILE}\n/y.md": b"x\n", "user/fields/chat.md": b"Who: changed.\n"})
        code, out = self.step(head, HOSTILE + "\n")
        self.assertNotEqual(code, 0, out)
        self.assertGreater(len(gate_lines(out)), 1, "the checks print a path as it is: the forged line is in the log")
        self.assertEqual(gate_lines(out)[0], f"GATE pr={PR} head={head} base={self.main} run={RUN}")
        self.assertEqual(out.splitlines()[0], gate_lines(out)[0])

    def test_what_is_no_commit_no_number_and_no_kind_is_refused_before_anything_is_read(self):
        head = self.head({"user/fields/chat.md": b"Who: changed.\n"})
        for change in ({"HEAD": "--output=/tmp/x"}, {"BASE": "main"}, {"PR": "x"}, {"KIND": "admin"},
                       {"GITHUB_RUN_ID": f"1\nGATE pr={PR}"}):
            code, out = self.step(head, **change)
            self.assertEqual((code, gate_lines(out)), (1, []), f"{change}: {out}")
        self.assertEqual(self.fetched[2:], [], "only the run id is read after the first manifest")

    def test_the_pushers_name_and_the_head_before_reach_c3_as_values_never_as_options(self):
        head = self.head({"user/fields/chat.md": b"Who: changed.\n"})
        with mock.patch.object(gate.checks, "main", return_value=0) as checks:
            self.step(head, ACTOR="-x", BEFORE="c" * 40)
        argv = checks.call_args.args[0]
        self.assertIn("--actor=-x", argv)
        self.assertIn(f"--before={'c' * 40}", argv)


class ThePublicFile(unittest.TestCase):
    URL = "https://raw.githubusercontent.com/owner/releases/refs/tags/v0.1.0/release-manifest.json"

    def fetch(self, answers: list):
        calls, sleeps = [], []

        @contextlib.contextmanager
        def opener(request, timeout):
            calls.append(request)
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            yield mock.Mock(status=answer[0], read=lambda: answer[1])

        try:
            return gate.fetch_public(self.URL, sleeps.append, opener), calls, sleeps
        except gate.Stop as exc:
            return exc, calls, sleeps

    def error(self, code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(self.URL, code, "x", None, None)

    def test_it_is_read_without_a_token(self):
        got, calls, sleeps = self.fetch([(200, b"{}")])
        self.assertEqual((got, sleeps), (b"{}", []))
        self.assertEqual({k.lower() for k in calls[0].headers}, {"user-agent"})

    def test_a_hiccup_is_tried_again_three_times_and_no_more(self):
        got, calls, sleeps = self.fetch([self.error(503), urllib.error.URLError("reset"), (200, b"{}")])
        self.assertEqual((got, sleeps), (b"{}", [1.0, 2.0]))
        got, calls, sleeps = self.fetch([self.error(429), self.error(408), (200, b"{}")])
        self.assertEqual((got, sleeps), (b"{}", [1.0, 2.0]))
        got, calls, sleeps = self.fetch([self.error(502)] * 4)
        self.assertIsInstance(got, gate.Stop)
        self.assertEqual((len(calls), sleeps), (4, [1.0, 2.0, 4.0]))
        got, calls, sleeps = self.fetch([TimeoutError("timed out")] * 4)
        self.assertIsInstance(got, gate.Stop)

    def test_not_published_is_a_failure_at_once(self):
        for answer in (self.error(404), self.error(403), (204, b"")):
            got, calls, sleeps = self.fetch([answer])
            self.assertIsInstance(got, gate.Stop, answer)
            self.assertEqual((len(calls), sleeps), (1, []))

    def test_only_the_public_host_is_asked(self):
        for url in ("https://example.org/release-manifest.json", "http://raw.githubusercontent.com/x",
                    "https://raw.githubusercontent.com.evil/x", "file:///etc/passwd"):
            with self.assertRaises(gate.Stop):
                gate.fetch_public(url, opener=lambda *a, **k: self.fail("asked"))


class TheWatchInTheGate(Steps):
    def step(self, head: str, watched=None, alarmed: int = 0) -> tuple[int, str, list, list]:
        (self.temp / "manifest-main.json").write_text(json.dumps(self.repo.manifest(self.main)), encoding="utf-8")
        watch_args, alarm_args = [], []

        def watch(argv, api=None, now=None):
            watch_args.extend(argv)
            if isinstance(watched, BaseException):
                raise watched
            return watched or 0

        def alarm(argv, api=None):
            alarm_args.extend(argv)
            return alarmed

        env = self.env(GH_TOKEN="t", BASE=self.main, HEAD=head)
        with mock.patch.object(gate.watch, "main", watch), mock.patch.object(gate.alarm, "main", alarm):
            code, out = self.run_step("watch", env, api=self.api, now=NOW)
        return code, out, watch_args, alarm_args

    def test_the_colour_is_the_alarms_whatever_the_watch_found(self):
        head = self.head({"user/fields/chat.md": b"Who: changed.\n"})
        for watched, alarmed in ((0, 0), (1, 0), (1, 1), (2, 1), (0, 2)):
            code, _out, watch_args, alarm_args = self.step(head, watched, alarmed)
            self.assertEqual(code, alarmed, f"watch {watched}, alarm {alarmed}")
        self.assertEqual(watch_args[2:], ["--main", self.main, "--manifest", str(self.temp / "manifest-main.json"),
                                          "--report", str(self.temp / "watch.json")])
        self.assertEqual(alarm_args[2:], ["--main", self.main, "--report", str(self.temp / "watch.json"), "--run-url",
                                          f"https://github.com/{REPO}/actions/runs/{RUN}"])

    def test_a_watch_that_crashes_still_reaches_the_alarms(self):
        head = self.head({"user/fields/chat.md": b"Who: changed.\n"})
        for crash in (RuntimeError("boom"), SystemExit(2)):
            code, out, _watch_args, alarm_args = self.step(head, crash, alarmed=1)
            self.assertEqual(code, 1, out)
            self.assertTrue(alarm_args, "no report then, and the alarms say so")
            self.assertIn(f"CRASH {type(crash).__name__}", out)

    def test_only_a_pr_that_records_an_installation_and_nothing_else_skips_n5(self):
        record = {"user/fields/installed.toml": b"[fields.chat]\nsha256 = \"0\"\n"}
        cases = ((record, True), ({**record, "user/fields/chat.md": b"Who: changed.\n"}, False),
                 ({"user/fields/chat.md": b"Who: changed.\n"}, False), ({}, False))
        for files, skips in cases:
            head = self.head(files)
            self.assertEqual(gate.records_installation(self.repo.path, self.main, head), skips, sorted(files))
            _code, _out, watch_args, _alarm = self.step(head)
            self.assertEqual("--record-pr" in watch_args, skips, sorted(files))

    def test_the_real_watch_and_alarms_run_behind_it(self):
        self.api.lists[f"repos/{REPO}/issues"] = []
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": "f" * 40}}     # main moved on
        (self.temp / "manifest-main.json").write_text(json.dumps(self.repo.manifest(self.main)), encoding="utf-8")
        head = self.head({"user/fields/chat.md": b"Who: changed.\n"})
        code, out = self.run_step("watch", self.env(GH_TOKEN="t", BASE=self.main, HEAD=head), api=self.api, now=NOW)
        self.assertEqual(code, 0, out)
        self.assertIn("its run judges it", out)
        self.assertIn("ALARM none", out)


class TheMerge(Steps):
    def setUp(self):
        super().setUp()
        self.pr_head = "a" * 40
        self.text = "## Change\nWas: one. Is: another.\n"
        self.api.objects[f"repos/{REPO}/pulls/{PR}"] = {"number": PR, "state": "open", "base": {"ref": "main"},
                                                        "body": self.text}
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": self.main}}
        self.calls: list[list[str]] = []

    def step(self, client_code: int = 0, **given) -> tuple[int, str]:
        checked = hashlib.sha256(gate.body_bytes({"body": self.text})).hexdigest()
        env = self.env(**{"GH_TOKEN": "t", "PR": str(PR), "HEAD": self.pr_head, "BASE": self.main, "BODY_SHA": checked,
                          **given})

        def client(args):
            self.calls.append(args)
            return client_code

        return self.run_step("merge", env, api=self.api, client=client)

    def test_it_merges_exactly_the_head_and_the_body_that_were_checked(self):
        code, out = self.step()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls, [["pr", "merge", str(PR), "--repo", REPO, "--squash", "--delete-branch",
                                       "--match-head-commit", self.pr_head, "--body-file", str(self.temp / "body.md")]])
        self.assertEqual((self.temp / "body.md").read_bytes(), self.text.encode() + b"\n")
        self.assertEqual(self.api.writes, [], "nothing else is written")

    def test_a_merge_github_refuses_is_red(self):
        self.assertEqual(self.step(client_code=1)[0], 1)

    def test_githubs_own_client_is_really_called_and_its_exit_is_the_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Path(tmp) / "gh"
            client.write_text('#!/bin/sh\necho "$*" > "$0.said"\nexit 3\n', encoding="utf-8")
            client.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}"}):
                self.assertEqual(gate.gh(["pr", "merge", "7"]), 3)
            self.assertEqual((Path(tmp) / "gh.said").read_text(), "pr merge 7\n")

    def test_it_refuses_the_copys_key(self):
        code, out = self.step(NOTION_TOKEN="n")
        self.assertEqual((code, self.calls, self.api.writes), (1, [], []), out)

    def test_when_main_moved_nothing_merges_and_the_check_starts_again(self):
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": "f" * 40}}
        code, out = self.step()
        self.assertEqual((code, self.calls), (0, []), out)
        self.assertIn("main moved since the check", out)
        self.assertEqual(self.api.writes, [
            ("POST", f"repos/{REPO}/statuses/{self.pr_head}",
             {"state": "pending", "context": "gate", "description": "main moved; checking again"}),
            ("POST", f"repos/{REPO}/actions/workflows/check.yml/dispatches", {"ref": "main", "inputs": {"pr": str(PR)}})])

    def test_a_body_edited_after_the_check_is_not_merged(self):
        self.api.objects[f"repos/{REPO}/pulls/{PR}"]["body"] = self.text + "One more line.\n"
        code, out = self.step()
        self.assertEqual((code, self.calls), (1, []), out)
        self.assertIn("The PR body changed after the check", out)

    def test_a_pr_pointed_at_another_branch_or_closed_after_the_check_is_not_merged(self):
        """An edit of the PR's target starts no run for main and cancels none; GitHub's client
        merges where the PR points. The check was of a merge into main."""
        pr = self.api.objects[f"repos/{REPO}/pulls/{PR}"]
        for change, said in (({"base": {"ref": "other"}}, f"ERROR PR {PR} targets other now, not main"),
                             ({"base": {}}, f"ERROR PR {PR} targets None now, not main"),
                             ({"state": "closed"}, f"ERROR PR {PR} is closed now; nothing is merged")):
            self.api.objects[f"repos/{REPO}/pulls/{PR}"] = {**pr, **change}
            code, out = self.step()
            self.assertEqual((code, self.calls, self.api.writes), (1, [], []), out)
            self.assertTrue(out.startswith(said), out)

    def test_a_pr_merged_already_is_left_to_githubs_client_as_before(self):
        self.api.objects[f"repos/{REPO}/pulls/{PR}"].update({"state": "closed", "merged": True, "base": {"ref": "other"}})
        code, out = self.step()
        self.assertEqual((code, len(self.calls)), (0, 1), out)

    def test_what_is_no_commit_is_refused_and_nothing_merges(self):
        for change in ({"HEAD": "main"}, {"BASE": ""}, {"PR": "7 --admin"}):
            code, _out = self.step(**change)
            self.assertEqual((code, self.calls, self.api.writes), (1, [], []), change)


class TheEntry(Steps):
    def test_a_step_that_is_not_one_is_refused(self):
        for argv in ([], ["pr", "checks"], ["everything"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(gate.main(argv, env=self.env()), 2)
            self.assertIn("ERROR usage: one step of pr, checks, watch, merge", out.getvalue())

    def test_a_variable_the_workflow_did_not_give_is_named(self):
        code, out = self.run_step("merge", self.env(GH_TOKEN="t", PR="7"))
        self.assertEqual((code, out.strip()), (2, "ERROR the step «merge» was not given HEAD, BASE, BODY_SHA"))

    def test_only_a_steps_own_zero_ends_it_green(self):
        """A parser's exit inside a step, or a step that returns something that is no exit code:
        red, whatever it is — the shell before it failed on anything but success."""
        def exits(code):
            def step(repo, env):
                raise SystemExit(code)
            return step

        cases = [(exits(0), 1), (exits(None), 1), (exits("usage"), 1), (exits(2), 2), (lambda repo, env: None, 1),
                 (lambda repo, env: False, 1), (lambda repo, env: True, 1), (lambda repo, env: 256, 1),
                 (lambda repo, env: -1, 1), (lambda repo, env: 3, 3), (lambda repo, env: 0, 0)]
        for run, wanted in cases:
            self.assertEqual(gate.main_of({"x": gate.Step(run, ())}, "doc", ["x"], self.env()), wanted)

    def test_an_error_that_carries_lines_of_its_own_is_said_on_one(self):
        def step(repo, env):
            raise RuntimeError(HOSTILE + "\nmore")

        def stopped(repo, env):
            raise gate.Stop(HOSTILE + "\nmore")

        for run, code in ((step, 2), (stopped, 1)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(gate.main_of({"x": gate.Step(run, ())}, "doc", ["x"], self.env()), code)
            self.assertEqual((len(out.getvalue().splitlines()), gate_lines(out.getvalue())), (1, []), out.getvalue())
        self.assertEqual(gate.plain("\u0432\u0435\u0442\u043a\u0430\nmain x"), "??????main?x")   # a branch named in Cyrillic
        self.assertEqual(gate.one_line("a\n b\r\nc"), "a b c")

    def test_a_step_run_as_the_workflow_runs_it_ends_the_process_with_its_code(self):
        """`python3 -m engine.checks.gate <step>`, as a process: the code GitHub's shell sees."""
        clean = {"PATH": os.environ["PATH"], **self.env(PR=str(PR), BASE=self.main, HEAD=self.main, KIND="person",
                                                        ACTOR="", BEFORE="")}
        root = Path(gate.__file__).resolve().parents[2]

        def run(*argv, **more):
            done = subprocess.run([sys.executable, "-B", "-m", "engine.checks.gate", *argv], cwd=root,
                                  env={**clean, **more}, capture_output=True, text=True)
            return done.returncode, done.stdout

        self.assertEqual(run()[0], 2)
        self.assertEqual(run("merge"), (2, "ERROR the step «merge» was not given GH_TOKEN, BODY_SHA\n"))
        code, out = run("checks", GH_TOKEN="t")
        self.assertEqual((code, out.startswith("ERROR this step is never given GH_TOKEN")), (1, True), out)

    def test_anything_else_is_one_loud_line_never_a_traceback(self):
        env = self.env(GH_TOKEN="t", PR=str(PR), HEAD="a" * 40, BASE=self.main, BODY_SHA="0")
        code, out = self.run_step("merge", env, api=self.api)            # the API has no main to show
        self.assertEqual((code, len(out.splitlines())), (2, 1), out)
        self.assertTrue(out.startswith("ERROR the step «merge» could not run: ApiError:"), out)


if __name__ == "__main__":
    unittest.main()

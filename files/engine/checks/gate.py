"""The gate's jobs, as `.github/workflows/check.yml` runs them: one step, one call (docs/DESIGN.md §5).

    python -m engine.checks.gate pr        # the PR as data
    python -m engine.checks.gate checks    # the checks of the PR: C1–C7
    python -m engine.checks.gate watch     # the watch of the main the run checked out, and its alarms
    python -m engine.checks.gate merge     # job `merge`

The workflow file holds only what GitHub reads from a workflow file — the triggers, the rights,
the jobs, the order of the steps and what each step is given. What a step does is here, in main's
code, so a release that changes it changes no workflow file and reaches a person's repository by
the weekly update: GITHUB_TOKEN cannot write workflow files, and a release that changes one waits
for hands.

A step is given its part of the run in environment variables — `STEPS` names them for each step,
and a test holds the workflow file to that list — and reads the runner's own (GITHUB_REPOSITORY,
GITHUB_OUTPUT, RUNNER_TEMP, …). The steps stay apart where the keys differ: `checks` reads what a
PR brings — its tree, its body — and is given no token; it refuses to run with one.

- `pr` reads the PR through the API and fetches its objects; nothing of the PR is checked out or
  executed. It tells the run the PR's number, head, kind, the pusher where the event names one,
  the main it will be checked against, and the sha256 of the body it read.
- `checks` fetches the release manifest from the public repository of releases, without a token,
  logs the line N1 knows a lawful check by — `GATE pr=N head=… base=… run=…` — before anything
  the PR brings can be printed, and runs C1–C7 (`engine.checks.run`).
- `watch` runs the checks of main and their alarms inside the gate: the nightly watch can be
  switched off by a collaborator, a save cannot skip this. Its exit code is the alarms'.
- `merge` merges exactly the head and the body `check` verified, and only onto the main it read;
  when main moved meanwhile it starts the check again instead. A PR that was closed, or pointed
  at another branch, after the check is not merged: the check was of a merge into main.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from . import alarm, gitio, paths_check, watch
from . import run as checks
from .github import GitHub
from .integrity import BOT

SHA_RE = re.compile(r"[0-9a-f]{40}")
NUMBER_RE = re.compile(r"[1-9][0-9]{0,9}")
RUN_RE = re.compile(r"[1-9][0-9]{0,19}")        # a run's id: eleven digits by 2026, and growing
SETUP_BRANCHES = "setup/"                       # the workflows `setup` and `settings` open their PRs from here
RECORD = b"user/fields/installed.toml"          # a PR that changes only this records an installation
PUBLIC_HOST = "https://raw.githubusercontent.com/"
TOKENS = ("GH_TOKEN", "GITHUB_TOKEN")           # what the step that reads the PR's content is never given
NOTION = ("NOTION_TOKEN",)                      # the copy's key: one step of the copy holds it, and no other step
BODY, MANIFEST, MAIN_MANIFEST, REPORT = "body.md", "manifest.json", "manifest-main.json", "watch.json"
RETRIED = frozenset({408, 429, 500, 502, 503, 504})
# The runner's own variables a step reads — GitHub sets them for every step of every job.
RUNNER = ("GITHUB_REPOSITORY", "GITHUB_SERVER_URL", "GITHUB_RUN_ID", "GITHUB_SHA", "GITHUB_OUTPUT", "RUNNER_TEMP")


class Stop(Exception):
    """The step ends red with this line."""


def one_line(text: object, limit: int = 300) -> str:
    """A text for the run's log on one line: whatever a PR, a branch name or an error carries
    cannot start a line of its own there — N1 reads the log by its lines."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def plain(text: object, limit: int = 100) -> str:
    """A name GitHub gave back — a branch, a state — as printable ASCII, for a log line."""
    return "".join(c if "!" <= c <= "~" else "?" for c in str(text))[:limit] or "?"


def number_of(value: str) -> int:
    if not NUMBER_RE.fullmatch(value or ""):
        raise Stop(f"the PR's number is not a number: {plain(value)}")
    return int(value)


def run_of(value: str) -> str:
    """The run's own id, as GitHub gives it and as N1 reads it back from the log: digits."""
    if not RUN_RE.fullmatch(value or ""):
        raise Stop(f"the run's id is not a number: {plain(value)}")
    return value


def sha_of(value: str, what: str) -> str:
    if not SHA_RE.fullmatch(value or ""):
        raise Stop(f"{what} is not a commit: {plain(value)}")
    return value


def never_given(env: Mapping[str, str], names: tuple[str, ...], why: str) -> None:
    """The steps stay apart where the keys differ, and a step says so itself: a workflow file
    that hands it a key it must not hold is a red step, not a quiet widening."""
    given = [name for name in names if env.get(name)]
    if given:
        raise Stop(f"this step is never given {', '.join(given)}: {why}")


def run_url(env: Mapping[str, str]) -> str:
    return f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"


def write_outputs(path: str, values: dict[str, str]) -> None:
    """The step's outputs, as GitHub reads them: one `name=value` a line. A value that would
    start a line of its own is refused — it could pass for another output."""
    for name, value in values.items():
        if "\n" in value or "\r" in value:
            raise Stop(f"the output {name} holds a line break")
    with open(path, "a", encoding="utf-8") as out:
        out.write("".join(f"{name}={value}\n" for name, value in values.items()))


def auth(token: str) -> dict:
    """The token as a header for one git call, for github.com only: never in .git/config or argv."""
    from ..setup import update          # here, not at the top: update imports the checks
    return update._auth("https://github.com/", token)


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env={**os.environ, **(env or {})})
    if proc.returncode != 0:
        raise gitio.GitError(f"git {args[0]}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout.decode().strip()


def fetch_public(url: str, sleep: Callable[[float], None] = time.sleep, opener=urllib.request.urlopen,
                 tries: int = 4) -> bytes:
    """A file of the public repository of releases, read without a token. An answer that is not
    200 is a failure; a server's or the network's hiccup is tried again three times."""
    if not url.startswith(PUBLIC_HOST):
        raise Stop(f"not a file of the public repository of releases: {plain(url, 200)}")
    request = urllib.request.Request(url, headers={"User-Agent": "memory-engine-checks"})
    for attempt in range(tries):
        last = attempt + 1 == tries
        try:
            with opener(request, timeout=30) as response:
                if response.status != 200:
                    raise Stop(f"GET {url}: HTTP {response.status}")
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRIED or last:
                raise Stop(f"GET {url}: HTTP {exc.code}") from None
        except OSError as exc:              # URLError, a timeout, a reset: the network
            if last:
                raise Stop(f"GET {url}: {one_line(getattr(exc, 'reason', exc))}") from None
        sleep(2.0 ** attempt)
    raise Stop(f"GET {url}: no answer")     # not reached: the last try returns or raises


def manifest_at(repo: Path, base: str, head: str, kind: str, get: Callable[[str], bytes]) -> bytes:
    """The release manifest a PR is held to — main's own for a person's PR, the PR's for an
    update: the tag comes from the lock, validated, never from anything else the PR says."""
    return get(checks.manifest_url(repo, base, gitio.merge_result(repo, base, head), kind))


# ---- pr ------------------------------------------------------------------------------

def kind_of(author: object, branch: object) -> str:
    """Only automation opens PRs as github-actions[bot]; they may touch the engine, and C1 still
    holds it to the release manifest. The workflows `setup` and `settings` open theirs from
    setup/: C2 lets such a PR only add the user's part to a repository not installed yet, or change it
    where it stands on an installed one. Neither the author nor the branch is trusted for more."""
    if author != BOT:
        return paths_check.PERSON
    return paths_check.SETUP if str(branch).startswith(SETUP_BRANCHES) else paths_check.UPDATE


def body_bytes(pr: dict) -> bytes:
    """The PR body as the run keeps it: C4 and C7 read these bytes, their sha256 goes to job
    `merge`, and they become the merge commit's message. One line break ends them."""
    body = pr.get("body")
    return (body if isinstance(body, str) else "").encode("utf-8", "replace") + b"\n"


def fetch_pr(repo: Path, number: int, token: str) -> str:
    """Fetch the PR's objects; the commit GitHub's own ref of the PR points to."""
    _git(repo, "fetch", "--no-tags", "--quiet", "origin", f"refs/pull/{number}/head", env=auth(token))
    return _git(repo, "rev-parse", "FETCH_HEAD")


def pr_as_data(repo: Path, api, env: Mapping[str, str], temp: Path, fetch: Callable[[int], str]) -> dict[str, str]:
    number = number_of(env["PR"])
    pr = api.get(f"repos/{api.repository}/pulls/{number}")
    state, target = pr.get("state"), (pr.get("base") or {}).get("ref")
    if state != "open":
        raise Stop(f"PR {number} is {plain(state)}")
    if target != "main":
        raise Stop(f"PR {number} targets {plain(target)}, not main")
    head = sha_of(str((pr.get("head") or {}).get("sha") or ""), f"the head of PR {number}")
    kind = kind_of((pr.get("user") or {}).get("login"), (pr.get("head") or {}).get("ref"))
    if fetch(number) != head:
        raise Stop(f"PR {number} moved while it was read; the run for the new head checks it")
    event, action = env["EVENT"], env["ACTION"]
    if event == "pull_request_target" and env["EVENT_HEAD"] != head:
        raise Stop(f"PR {number} moved since this run was started; the run for the new head checks it")
    # Who pushed this head, and what the head was before. Trusted for the protected config keys
    # (C3) only on `synchronize`: the sender is the pusher, and C3 can see whether this very push
    # brought the change. An open, a reopen, an edit or a dispatch does not say who pushed what:
    # then nobody is trusted, a change of a protected key is red, and the trusted contact makes
    # it in a push of their own.
    actor = before = ""
    if (event, action) == ("pull_request_target", "synchronize"):
        actor, before = env["SENDER"], env["EVENT_BEFORE"]
        if before:
            sha_of(before, "the head before this push")
    body = body_bytes(pr)
    (temp / BODY).write_bytes(body)
    return {"number": str(number), "head": head, "kind": kind, "actor": actor, "before": before,
            "base": sha_of(_git(repo, "rev-parse", "HEAD"), "the checkout"),
            "body_sha": hashlib.sha256(body).hexdigest()}


def step_pr(repo: Path, env: Mapping[str, str], api=None, fetch: Callable[[int], str] | None = None) -> int:
    never_given(env, NOTION, "it holds GitHub's token")
    token = env["GH_TOKEN"]
    api = api or GitHub(env["GITHUB_REPOSITORY"], token)
    out = pr_as_data(repo, api, env, Path(env["RUNNER_TEMP"]), fetch or (lambda number: fetch_pr(repo, number, token)))
    write_outputs(env["GITHUB_OUTPUT"], out)
    print(f"PR #{out['number']} head={out['head']} base={out['base']} kind={out['kind']}")
    return 0


# ---- checks --------------------------------------------------------------------------

def step_checks(repo: Path, env: Mapping[str, str], now: datetime | None = None,
                get: Callable[[str], bytes] = fetch_public) -> int:
    never_given(env, TOKENS + NOTION, "it reads what the PR brings")
    temp = Path(env["RUNNER_TEMP"])
    number = number_of(env["PR"])
    base, head = sha_of(env["BASE"], "the main to check against"), sha_of(env["HEAD"], "the PR's head")
    kind = env["KIND"]
    if kind not in paths_check.KINDS:
        raise Stop(f"not a kind of PR: {plain(kind)}")
    (temp / MANIFEST).write_bytes(manifest_at(repo, base, head, kind, get))
    # The run's own record of what it checked; N1 reads it by run id. Before the checks print
    # anything: N1 takes the first such line of this PR, head and run, and nothing above it
    # in this job's log comes from the PR.
    print(f"GATE pr={number} head={head} base={base} run={run_of(env['GITHUB_RUN_ID'])}", flush=True)
    code = checks.main(["--repo", str(repo), "--base", base, "--head", head, "--kind", kind,
                        f"--actor={env['ACTOR']}", f"--before={env['BEFORE']}",
                        "--manifest", str(temp / MANIFEST), "--body", str(temp / BODY)], now=now)
    if code != 0:
        return code
    # Main's own manifest, for the watch inside the gate: fetched here, where no token is.
    (temp / MAIN_MANIFEST).write_bytes(manifest_at(repo, base, base, paths_check.PERSON, get))
    return 0


# ---- watch ---------------------------------------------------------------------------

def records_installation(repo: Path, base: str, head: str) -> bool:
    """Whether the PR changes `user/fields/installed.toml` and nothing else — the same view of
    the change as C2: main against the merge result, no renames."""
    result = gitio.merge_result(repo, base, head)
    return [change.path for change in gitio.changes(repo, base, result)] == [RECORD]


def step_watch(repo: Path, env: Mapping[str, str], api=None, now: datetime | None = None,
               branches: str | None = None) -> int:
    never_given(env, NOTION, "it holds GitHub's token")
    temp = Path(env["RUNNER_TEMP"])
    base, head = sha_of(env["BASE"], "the main to check"), sha_of(env["HEAD"], "the PR's head")
    args = ["--repo", str(repo), "--main", base, "--manifest", str(temp / MAIN_MANIFEST),
            "--report", str(temp / REPORT)]
    if branches is not None:                     # the tests' fixtures keep their branches elsewhere
        args += ["--branches", branches]
    if records_installation(repo, base, head):
        args.append("--record-pr")
    try:
        watch.main(args, api=api, now=now)       # whether what it found is red is the alarms' call
    except (Exception, SystemExit) as exc:       # noqa: BLE001 — no report then, and the alarms say so (P5)
        print(f"CRASH {type(exc).__name__}: {one_line(exc)}")
        traceback.print_exc()
    return alarm.main(["--repo", str(repo), "--main", base, "--report", str(temp / REPORT),
                       "--run-url", run_url(env)], api=api)


# ---- merge ---------------------------------------------------------------------------

def gh(args: list[str]) -> int:
    """GitHub's own client, as the workflow called it; the token is in its environment."""
    return subprocess.run(["gh", *args]).returncode


def start_check(api, number: int) -> None:
    api.post_no_content(f"repos/{api.repository}/actions/workflows/check.yml/dispatches",
                        {"ref": "main", "inputs": {"pr": str(number)}})


def step_merge(repo: Path, env: Mapping[str, str], api=None, client: Callable[[list[str]], int] = gh) -> int:
    never_given(env, NOTION, "it holds GitHub's token")
    temp = Path(env["RUNNER_TEMP"])
    api = api or GitHub(env["GITHUB_REPOSITORY"], env["GH_TOKEN"])
    number = number_of(env["PR"])
    head, base = sha_of(env["HEAD"], "the head the check verified"), sha_of(env["BASE"], "the main the check read")
    main = api.get(f"repos/{api.repository}/git/ref/heads/main")["object"]["sha"]
    if main != base:
        # Another PR landed meanwhile: what this PR would merge into was never checked.
        print("main moved since the check; checking again on the new main")
        api.post(f"repos/{api.repository}/statuses/{head}",
                 {"state": "pending", "context": "gate", "description": "main moved; checking again"})
        start_check(api, number)
        return 0
    pr = api.get(f"repos/{api.repository}/pulls/{number}")
    # What job `check` held when it read the PR, held again now: an edit that points the PR at
    # another branch starts no run for main and cancels none, and GitHub's client would merge
    # where the PR points. A PR merged already is the client's to say, as before.
    if pr.get("merged") is not True:
        if pr.get("state") != "open":
            raise Stop(f"PR {number} is {plain(pr.get('state'))} now; nothing is merged")
        target = (pr.get("base") or {}).get("ref")
        if target != "main":
            raise Stop(f"PR {number} targets {plain(target)} now, not main, where it was checked; nothing is merged")
    body = body_bytes(pr)
    if hashlib.sha256(body).hexdigest() != env["BODY_SHA"]:
        raise Stop("The PR body changed after the check; the run for the edit checks it")
    # The checked record becomes the merge commit's message: a PR body can be edited later,
    # the history of main cannot.
    (temp / BODY).write_bytes(body)
    return client(["pr", "merge", str(number), "--repo", api.repository, "--squash", "--delete-branch",
                   "--match-head-commit", head, "--body-file", str(temp / BODY)])


# ---- the steps, and what the workflow gives each -------------------------------------

@dataclass(frozen=True)
class Step:
    run: Callable[..., int]         # (the checkout, the environment) → the step's exit code
    env: tuple[str, ...]            # the variables the workflow's step sets: exactly these


STEPS: dict[str, Step] = {
    "pr": Step(step_pr, ("GH_TOKEN", "PR", "EVENT", "ACTION", "EVENT_HEAD", "EVENT_BEFORE", "SENDER")),
    "checks": Step(step_checks, ("PR", "BASE", "HEAD", "KIND", "ACTOR", "BEFORE")),
    "watch": Step(step_watch, ("GH_TOKEN", "BASE", "HEAD")),
    "merge": Step(step_merge, ("GH_TOKEN", "PR", "HEAD", "BASE", "BODY_SHA")),
}


def main_of(steps: dict[str, Step], doc: str, argv: list[str] | None = None,
            env: Mapping[str, str] | None = None, repo: Path = Path("."), **seams) -> int:
    """One step by its name. Anything it could not do is one loud line and a red step (P5),
    never a traceback read as a verdict. `repo` and `seams` are the tests': the checkout, and
    what stands in for GitHub, the network and the clock."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in steps:
        print(f"ERROR usage: one step of {', '.join(steps)} — {one_line(doc.splitlines()[0])}")
        return 2
    env = os.environ if env is None else env
    step = steps[argv[0]]
    missing = [name for name in (*step.env, *RUNNER) if name not in env]
    if missing:
        print(f"ERROR the step «{argv[0]}» was not given {', '.join(missing)}")
        return 2
    try:
        code = step.run(repo, env, **seams)
    except Stop as exc:
        print(f"ERROR {one_line(exc)}")
        return 1
    except SystemExit as exc:   # a parser's exit inside a step: its code when it is a failure's, else a failure
        code = exc.code if isinstance(exc.code, int) and exc.code != 0 else 1
    except Exception as exc:    # noqa: BLE001
        reason = getattr(exc, "reason", "")
        print(f"ERROR the step «{argv[0]}» could not run: {type(exc).__name__}: {one_line(exc)}"
              + (f" — {one_line(reason)}" if reason else ""))
        return 2
    # Green only on a step's own 0: anything that is not an exit code a shell can carry is red.
    return code if type(code) is int and 0 <= code <= 255 else 1


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None, **seams) -> int:
    return main_of(STEPS, __doc__, argv, env, **seams)


if __name__ == "__main__":
    sys.exit(main())

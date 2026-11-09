"""The engine update of a person's repository (docs/DESIGN.md §5; step 3 of the update plan).

    python -m engine.setup.update --repo . --main MAIN_SHA --run-url URL

Run by `.github/workflows/update.yml` every week and by hand, on main's code. It reads the
tags of the public repository of releases (`release.manifests` in main's
`engine/defaults.toml`) and takes the newest release main's channel allows — C1's own rule
(`lock_check.not_due`), so it never opens a PR the gate would hold back. It fetches that
release by git, without a token — the commits of the tags newer than the installed one,
without their history: the manifests, and the files `release cut` published under
`files/` — and holds every file of the chosen release to its manifest, the same sha256 C1
will hold the PR to. It builds the update commit on main: `engine/` and `.github/`
replaced by the release, `user/engine.lock` moved, nothing else touched; its dates are
main's, so the same main and the same release give the same commit. Then:

- the release changes nothing under `.github/` → the commit goes to the branch
  `engine-update/<tag>` (unless the open update PR already has it), the update PR is
  opened (or the open one kept), older update PRs it opened are closed, and once GitHub
  shows the PR at that commit the gate is started through workflow_dispatch: a PR opened
  or pushed with GITHUB_TOKEN starts no `pull_request_target` run. The gate checks it like
  any PR (`kind=update`) and job `merge` lands it. A PR somebody else opened from that
  branch stops the run: the update does not push into it;
- it changes `.github/` → no branch: GITHUB_TOKEN cannot write workflow files, and N1
  holds any branch that changes `.github/`. An Issue «has to be applied by hand» names the
  trusted contact and the owner, once per release — closed by hand, it is not opened
  again («known», as with the alarms); this run closes it once main holds that release
  or a newer one.

Nothing newer is due → «NONE», exit 0. Anything that could not be done → «ERROR», exit 1:
the run is red, and GitHub mails whoever started it or, for a scheduled run, whoever last
changed the workflow's schedule.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from engine.checks import alarm, config_check, gitio, lock_check, run
from engine.checks.github import GitHub
from engine.checks.integrity import BOT
from engine.setup.release import MANIFEST_FILE, PUBLISHED_FILES, PUBLISHED_TAG_RE

BRANCH_PREFIX = "engine-update/"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
HANDS_TITLE = "Engine update {tag} has to be applied by hand"
HANDS_RE = re.compile(r"Engine update (v\d+\.\d+\.\d+) has to be applied by hand")
ZERO = "0" * 40


def github_url(repository: str) -> str:
    return f"https://github.com/{repository}.git"


def _git(repo: Path, *args: str, stdin: bytes | None = None, env: dict | None = None) -> bytes:
    proc = subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True,
                          env={**os.environ, **(env or {})})
    if proc.returncode != 0:
        raise gitio.GitError(f"git {args[0]}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def _auth(url: str, token: str) -> dict:
    """The token as a header for this git call only: never in .git/config or argv, and only
    for github.com (as the gate fetches a PR)."""
    if not token or not url.startswith("https://github.com/"):
        return {}
    header = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": header}


def published_tags(store: Path, url: str) -> list[str]:
    """The release tags of the public repository, oldest first; anything not
    vMAJOR.MINOR.PATCH as the workflow `tag` writes it is not a release."""
    out = _git(store, "ls-remote", "--tags", "--refs", url)
    tags = set()
    for line in out.decode("utf-8", "replace").splitlines():
        _sha, _, ref = line.partition("\t")
        name = ref.removeprefix("refs/tags/")
        if ref.startswith("refs/tags/") and PUBLISHED_TAG_RE.fullmatch(name):
            tags.add(name)
    return sorted(tags, key=lock_check.release_key)


def fetch(store: Path, url: str, tags: list[str]) -> None:
    """The commits of these tags, and nothing of their history, into a scratch repository."""
    if tags:
        _git(store, "fetch", "--quiet", "--no-tags", "--depth", "1", url,
             *(f"+refs/tags/{t}:refs/tags/{t}" for t in tags))


def read_manifest(store: Path, tag: str) -> dict:
    raw = gitio.file_at(store, tag, MANIFEST_FILE)
    if raw is None:
        raise ValueError(f"{tag} has no {MANIFEST_FILE}")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise ValueError(f"the manifest of {tag} has no «files» table")
    if data.get("release") != tag:
        raise ValueError(f"the manifest at {tag} names {data.get('release')!r}")
    return data


def choose(store: Path, tags: list[str], installed: str, repo: Path, main: str,
           now: datetime) -> tuple[str, dict] | None:
    """The newest release newer than the installed one that main's channel allows by now."""
    for tag in sorted((t for t in tags if lock_check.release_key(t) > lock_check.release_key(installed)),
                      key=lock_check.release_key, reverse=True):
        manifest = read_manifest(store, tag)
        if lock_check.not_due(repo, main, manifest, now) is None:
            return tag, manifest
    return None


def release_files(store: Path, tag: str, manifest: dict) -> dict[str, str]:
    """Path in the person's repository → blob sha in the scratch repository, for every file
    of the release, each held to the manifest: the same set, plain files, the same sha256.
    Every path is under the two areas C1 locks, so an update writes nowhere else."""
    expected: dict[str, str] = manifest["files"]
    problems = [f"{p} is outside engine/ and .github/" for p in sorted(expected)
                if not p.startswith(tuple(f"{prefix}/" for prefix in lock_check.LOCKED_PREFIXES))]
    found: dict[str, str] = {}
    for path_b, (mode, sha) in sorted(gitio.tree(store, tag, PUBLISHED_FILES).items()):
        path = path_b.decode("utf-8").removeprefix(f"{PUBLISHED_FILES}/")
        if mode != gitio.REGULAR:
            problems.append(f"{path} has mode {mode}")
        found[path] = sha
    problems += [f"{p} is not published" for p in sorted(expected.keys() - found.keys())]
    problems += [f"{p} is published but not in the manifest" for p in sorted(found.keys() - expected.keys())]
    problems += [f"{p} differs from the manifest" for p in sorted(expected.keys() & found.keys())
                 if hashlib.sha256(gitio.blob(store, found[p])).hexdigest() != expected[p]]
    if problems:
        raise ValueError(f"the published files of {tag} do not match its manifest: " + "; ".join(problems[:10]))
    return found


def changes_github(repo: Path, main: str, store: Path, files: dict[str, str]) -> bool:
    """Whether the release changes anything under .github/ against main: a git object name
    is its content's hash, so equal names in the two repositories are equal files."""
    on_main = {p.decode("utf-8", "replace"): entry for p, entry in gitio.tree(repo, main, ".github").items()}
    released = {p: (gitio.REGULAR, sha) for p, sha in files.items() if p.startswith(".github/")}
    return on_main != released


def update_commit(repo: Path, main: str, store: Path, tag: str, files: dict[str, str]) -> str:
    """main with engine/ and .github/ replaced by the release and the lock moved — a commit
    of its own, built in a scratch index; the checkout is not touched. Dated with main's
    commit time: the same main and release give the same commit, so a second run pushes
    nothing new."""
    entries = [f"0 {ZERO}\t".encode() + p + b"\0" for p in gitio.tree(repo, main, *lock_check.LOCKED_PREFIXES)]
    for path, sha in sorted(files.items()):
        new = _git(repo, "hash-object", "-w", "--stdin", stdin=gitio.blob(store, sha)).decode().strip()
        entries.append(f"100644 {new}\t{path}\0".encode())
    lock = _git(repo, "hash-object", "-w", "--stdin", stdin=f'release = "{tag}"\n'.encode()).decode().strip()
    entries.append(f"100644 {lock}\t{lock_check.LOCK_FILE}\0".encode())
    stamp = f"{_git(repo, 'log', '-1', '--format=%ct', main).decode().strip()} +0000"
    with tempfile.TemporaryDirectory() as tmp:
        index = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(repo, "read-tree", main, env=index)
        _git(repo, "update-index", "-z", "--index-info", stdin=b"".join(entries), env=index)
        tree = _git(repo, "write-tree", env=index).decode().strip()
    who = {"GIT_AUTHOR_NAME": BOT, "GIT_AUTHOR_EMAIL": BOT_EMAIL, "GIT_AUTHOR_DATE": stamp,
           "GIT_COMMITTER_NAME": BOT, "GIT_COMMITTER_EMAIL": BOT_EMAIL, "GIT_COMMITTER_DATE": stamp}
    return _git(repo, "commit-tree", tree, "-p", main, "-m", f"Engine update to {tag}", env=who).decode().strip()


def _head(pr: dict) -> dict:
    return pr.get("head") or {}


def _ours(api, pr: dict) -> bool:
    """A PR this workflow opened: by github-actions[bot], from a branch of this repository."""
    return ((pr.get("user") or {}).get("login") == BOT
            and (_head(pr).get("repo") or {}).get("full_name") == api.repository)


def open_prs(api) -> list[dict]:
    return list(api.pages(f"repos/{api.repository}/pulls", {"state": "open"}))


def _tag_of(branch: str) -> tuple[int, int, int] | None:
    try:
        return lock_check.release_key(branch.removeprefix(BRANCH_PREFIX))
    except ValueError:
        return None


def taken_by_others(api, prs: list[dict], branch: str) -> list[dict]:
    """Open PRs from this branch that this workflow did not open: pushing would change them."""
    return [pr for pr in prs if _head(pr).get("ref") == branch and not _ours(api, pr)]


def existing_pr(api, prs: list[dict], branch: str) -> dict | None:
    return next((pr for pr in prs if _head(pr).get("ref") == branch and _ours(api, pr)), None)


def open_pr(api, prs: list[dict], tag: str, channel: str) -> tuple[int, list[int]]:
    """The update PR for tag — the open one, or a new one — and the older update PRs this
    workflow opened, which it closes. A PR for a newer release is left alone."""
    branch = BRANCH_PREFIX + tag
    number, closed = None, []
    for pr in prs:
        ref = str(_head(pr).get("ref", ""))
        if not _ours(api, pr) or not ref.startswith(BRANCH_PREFIX):
            continue
        if ref == branch:
            number = pr["number"]
        elif (_tag_of(ref) or (0, 0, 0)) < lock_check.release_key(tag):
            api.patch(f"repos/{api.repository}/pulls/{pr['number']}", {"state": "closed"})
            closed.append(pr["number"])
    if number is None:
        # No dates or other digits in the body: it becomes the merge commit's message, and C4
        # reads it like any PR body.
        body = (f"Engine update to {tag}, due on the {channel} channel.\n\n"
                f"Opened by the update workflow. The gate checks it like any PR: C1 holds every file "
                f"under engine/ and .github/ to the manifest of {tag} in the public repository of releases.\n")
        number = api.post(f"repos/{api.repository}/pulls",
                          {"title": f"Engine update to {tag}", "head": branch, "base": "main", "body": body})["number"]
    return number, closed


def wait_for_head(api, number: int, commit: str, sleep: Callable[[float], None] = time.sleep,
                  tries: int = 20, pause: float = 3.0) -> None:
    """GitHub moves a PR's head after a push in its own time; the gate reads the head the API
    shows and goes red on one that moved while it read. So the gate starts only once the PR
    shows this commit."""
    for attempt in range(tries):
        if _head(api.get(f"repos/{api.repository}/pulls/{number}")).get("sha") == commit:
            return
        if attempt + 1 < tries:
            sleep(pause)
    raise ValueError(f"PR #{number} does not show {commit} after {tries} reads; the gate is not started")


def start_gate(api, number: int) -> None:
    api.post_no_content(f"repos/{api.repository}/actions/workflows/check.yml/dispatches",
                        {"ref": "main", "inputs": {"pr": str(number)}})


def _hands_issues(api, state: str = "open") -> list[dict]:
    return [i for i in api.pages(f"repos/{api.repository}/issues", {"state": state})
            if (i.get("user") or {}).get("login") == BOT and "pull_request" not in i
            and HANDS_RE.fullmatch(str(i.get("title", "")))]


def close_applied(api, installed: str) -> list[int]:
    """Issues «has to be applied by hand» for a release main now holds, or an older one."""
    closed = []
    for issue in _hands_issues(api):
        if lock_check.release_key(HANDS_RE.fullmatch(issue["title"]).group(1)) <= lock_check.release_key(installed):
            api.post(f"repos/{api.repository}/issues/{issue['number']}/comments",
                     {"body": f"main holds {installed}: applied. Closing."})
            api.patch(f"repos/{api.repository}/issues/{issue['number']}", {"state": "closed"})
            closed.append(issue["number"])
    return closed


def ask_for_hands(api, tag: str, channel: str, owner: str, contact: str, run_url: str) -> tuple[int, str]:
    """One Issue per release that has to be applied by hand: (number, what happened). One
    somebody closed is «known» and not opened again."""
    title = HANDS_TITLE.format(tag=tag)
    for issue in _hands_issues(api, "all"):
        if issue["title"] == title:
            return issue["number"], "already open" if issue.get("state") == "open" else "closed by hand: not asked again"
    body = (f"{alarm._names(owner, contact)} — release {tag} is due on the {channel} channel and changes "
            f".github/. GITHUB_TOKEN cannot write workflow files, so the update workflow opened no PR. "
            f"The trusted contact applies it by hand, as docs/DESIGN.md §5 says; N1 then raises its expected "
            f"alarm. This Issue closes itself once main holds {tag}.\n\nRun: {run_url}\n")
    return api.post(f"repos/{api.repository}/issues", {"title": title, "body": body})["number"], "opened"


def push(repo: Path, url: str, commit: str, branch: str, token: str) -> None:
    """The workflow's own branch: whatever is there is replaced."""
    _git(repo, "push", "--quiet", "--force", url, f"{commit}:refs/heads/{branch}", env=_auth(url, token))


def update(repo: Path, main: str, api, origin: Callable[[str], str], token: str, now: datetime,
           owner: str, run_url: str, sleep: Callable[[float], None] = time.sleep) -> list[str]:
    """One run; the lines it prints. Raises on anything it could not do."""
    installed = lock_check.lock_release(repo, main)
    if installed is None:
        raise ValueError(f"{lock_check.LOCK_FILE} is missing on main")
    lock_check.release_key(installed)
    lines = [f"closed #{n}: {installed} is installed" for n in close_applied(api, installed)]
    url = origin(run.releases_repository(repo, main))
    channel = lock_check.channel(repo, main)
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp)
        _git(store, "init", "--quiet", "--bare")
        tags = [t for t in published_tags(store, url) if lock_check.release_key(t) > lock_check.release_key(installed)]
        fetch(store, url, tags)
        chosen = choose(store, tags, installed, repo, main, now)
        if chosen is None:
            return lines + [f"NONE {installed} is the newest release due on the {channel} channel"]
        tag, manifest = chosen
        files = release_files(store, tag, manifest)
        if changes_github(repo, main, store, files):
            config = config_check.read(repo, main)
            contact = str(config_check.protected_value(config, "alarm", "trusted_contact") or "")
            if not config_check.LOGIN_RE.fullmatch(contact):
                contact = ""                # never put an unchecked string into a mention
            number, what = ask_for_hands(api, tag, channel, owner, contact, run_url)
            return lines + [f"HANDS {tag} changes .github/: issue #{number} {what}"]
        commit = update_commit(repo, main, store, tag, files)
    branch = BRANCH_PREFIX + tag
    prs = open_prs(api)
    others = taken_by_others(api, prs, branch)
    if others:
        pr = others[0]
        raise ValueError(f"PR #{pr.get('number')} by {(pr.get('user') or {}).get('login')} is open from {branch}; "
                         f"the update does not push into a PR it did not open")
    mine = existing_pr(api, prs, branch)
    if mine is None or _head(mine).get("sha") != commit:
        push(repo, origin(api.repository), commit, branch, token)
    number, closed = open_pr(api, prs, tag, channel)
    wait_for_head(api, number, commit, sleep)
    start_gate(api, number)
    lines += [f"closed #{n}: superseded by {tag}" for n in closed]
    return lines + [f"UPDATE {tag} branch={branch} commit={commit} pr=#{number}; the gate is started"]


def main(argv: list[str] | None = None, now: datetime | None = None, api=None,
         origin: Callable[[str], str] = github_url, sleep: Callable[[float], None] = time.sleep) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--main", required=True, help="main, as the workflow checked it out")
    ap.add_argument("--run-url", required=True)
    args = ap.parse_args(argv)
    token = os.environ.get("GH_TOKEN", "")
    try:
        if api is None:
            api = GitHub(os.environ.get("GITHUB_REPOSITORY", ""), token)
        lines = update(args.repo, args.main, api, origin, token, now or datetime.now(timezone.utc),
                       os.environ.get("GITHUB_REPOSITORY_OWNER", ""), args.run_url, sleep)
    except Exception as exc:    # noqa: BLE001 — anything is a loud ERROR line, never a traceback (P5)
        reason = getattr(exc, "reason", "")
        print(f"ERROR the update could not run: {type(exc).__name__}: {exc}" + (f" — {reason}" if reason else ""))
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

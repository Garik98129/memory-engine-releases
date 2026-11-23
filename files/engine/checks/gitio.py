"""Read git objects without checking anything out.

The checks never execute or check out a PR's files: they read blobs and trees by
revision. Paths stay bytes until a check decides they are valid UTF-8.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SYMLINK = "120000"
SUBMODULE = "160000"
EXECUTABLE = "100755"
REGULAR = "100644"


class GitError(RuntimeError):
    """A git command failed; the check that needed it cannot run (P5: loud)."""


class Conflict(GitError):
    """The PR does not merge cleanly into main: there is no result to check."""


def git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


@dataclass(frozen=True)
class Change:
    status: str        # A, M, D, T (renames are split into D + A)
    old_mode: str
    new_mode: str
    path: bytes


def merge_result(repo: Path, base: str, head: str) -> str:
    """Tree that main would hold after the PR merges — the object the checks read.

    Checking the merge result instead of the PR head means a PR made from an older
    main is judged by what actually lands, not by files main has since changed.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), "merge-tree", "--write-tree", "--name-only",
         "--no-messages", base, head],
        capture_output=True,
    )
    lines = proc.stdout.decode(errors="replace").splitlines()
    if proc.returncode == 1:
        raise Conflict(f"the PR conflicts with main in {', '.join(lines[1:]) or '?'}")
    if proc.returncode != 0 or not lines:
        raise GitError(f"git merge-tree: {proc.stderr.decode(errors='replace').strip()}")
    return lines[0]


def changes(repo: Path, base: str, head: str) -> list[Change]:
    """Every path changed between base and head, renames split into delete + add."""
    out = git(repo, "diff", "--raw", "-z", "--no-renames", "--no-abbrev", base, head)
    items = out.split(b"\0")
    result: list[Change] = []
    i = 0
    while i < len(items) - 1:
        meta = items[i].decode()
        path = items[i + 1]
        old_mode, new_mode, _old, _new, status = meta.lstrip(":").split(" ")
        result.append(Change(status[0], old_mode, new_mode, path))
        i += 2
    return result


def tree(repo: Path, rev: str, *prefixes: str) -> dict[bytes, tuple[str, str]]:
    """Map path → (mode, blob sha) for every file under the prefixes at rev."""
    out = git(repo, "ls-tree", "-r", "-z", "--full-tree", rev, "--", *prefixes)
    result: dict[bytes, tuple[str, str]] = {}
    for entry in out.split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode, _kind, sha = meta.decode().split(" ")
        result[path] = (mode, sha)
    return result


def blob(repo: Path, sha: str) -> bytes:
    return git(repo, "cat-file", "blob", sha)


def file_at(repo: Path, rev: str, path: str) -> bytes | None:
    """Content of a regular file at rev, or None if it does not exist there."""
    entries = tree(repo, rev, path)
    entry = entries.get(path.encode())
    if entry is None:
        return None
    return blob(repo, entry[1])


def changed_files(repo: Path, base: str, head: str, prefix: str):
    """(path, old bytes or None, new bytes) for each regular file under prefix that
    head adds or changes. Paths that are not UTF-8 or not regular files are left to
    C2."""
    for ch in changes(repo, base, head):
        try:
            path = ch.path.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if ch.status == "D" or not path.startswith(prefix):
            continue
        new = file_at(repo, head, path)
        if new is not None:
            yield path, file_at(repo, base, path), new


def added_lines(old: str | None, new: str) -> set[int]:
    """Line numbers in new whose text is not a line of old."""
    seen = set(old.splitlines()) if old is not None else set()
    return {no for no, line in enumerate(new.splitlines(), start=1) if line not in seen}


def commit_time(repo: Path, rev: str) -> datetime:
    """Committer time of a commit, with its UTC offset."""
    return datetime.fromisoformat(git(repo, "show", "-s", "--format=%cI", rev).decode().strip())


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"   # git's empty tree: the base of a branch with no common ancestor


def files_under(repo: Path, rev: str, prefix: str):
    """(path, bytes) for every regular file under prefix at rev — the whole current
    state, for the nightly checks. Paths that are not UTF-8 or not regular files are
    left to the tree rules of C2."""
    for path_b, (mode, sha) in sorted(tree(repo, rev, prefix).items()):
        try:
            path = path_b.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if mode in (REGULAR, EXECUTABLE):
            yield path, blob(repo, sha)


def is_ancestor(repo: Path, ancestor: str, rev: str) -> bool:
    """True if ancestor is rev or reachable from it; False for an unknown object."""
    proc = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, rev],
                          capture_output=True)
    return proc.returncode == 0


def merge_base(repo: Path, a: str, b: str) -> str:
    """The best common ancestor, or the empty tree if the two share no history."""
    proc = subprocess.run(["git", "-C", str(repo), "merge-base", a, b], capture_output=True)
    if proc.returncode == 1:
        return EMPTY_TREE
    if proc.returncode != 0:
        raise GitError(f"git merge-base: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout.decode().split()[0]


def last_change(repo: Path, rev: str, *paths: str) -> datetime | None:
    """When main last changed any of paths: the committer time of the newest
    first-parent commit that touched them — on main that is the merge, by job
    `merge`. None if no commit touched them."""
    out = git(repo, "log", "-1", "--first-parent", "--format=%cI", rev, "--", *paths).decode().strip()
    return datetime.fromisoformat(out) if out else None

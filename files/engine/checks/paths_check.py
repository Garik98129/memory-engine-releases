"""C2 allowed-paths: a PR reaches only its own area (docs/DESIGN.md §6).

A person's PR (opened by the bot) may change only an exact list of files under
user/. An update PR may change only the engine, the workflows and the lock. Path
tricks — symlinks, submodules, executable bits, non-UTF-8 names, «..», case
collisions — are red whatever the area. Renames are split into delete + add, so a
rename across areas fails on one of its halves.

Which kind a PR is does not open the engine to arbitrary content: an update PR
still has to match the release manifest of its release (C1).

The other half of C2 — the routine entries of a read-back record against their masters —
is `drift.record_check`.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import gitio
from .report import Finding

PERSON = "person"
UPDATE = "update"
KINDS = (PERSON, UPDATE)

INSTALLED = "user/fields/installed.toml"
PERSON_FILES = frozenset({
    "user/config.toml",
    "user/fields/chat.md",
    "user/fields/bot.md",
    INSTALLED,
})
ROUTINE_RE = re.compile(r"user/routines/[a-z0-9][a-z0-9-]{0,63}\.md")

UPDATE_PREFIXES = ("engine/", ".github/workflows/")
UPDATE_FILES = frozenset({"user/engine.lock"})

BAD_MODES = {
    gitio.SYMLINK: "a symlink",
    gitio.SUBMODULE: "a submodule",
    gitio.EXECUTABLE: "an executable bit",
}


def allowed(kind: str, path: str) -> bool:
    if kind == PERSON:
        return path in PERSON_FILES or ROUTINE_RE.fullmatch(path) is not None
    if kind == UPDATE:
        return path in UPDATE_FILES or path.startswith(UPDATE_PREFIXES)
    raise ValueError(f"unknown PR kind {kind!r}")


def _shape_problem(path: str) -> str | None:
    if path.startswith("/") or "\\" in path:
        return "is not a plain relative path"
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "has an empty, «.» or «..» component"
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return "has a control character"
    return None


ROOT_FILES = frozenset({"README.md", "LICENSE", ".gitignore"})
LOCKED_AREAS = ("engine/", ".github/")   # held byte for byte to the release by C1


def tree_findings(repo: Path, rev: str) -> list[Finding]:
    """C2's rules over a whole tree — for N1 on main, which a direct push may have
    reached without a PR: every path in a known area, no path tricks anywhere."""
    findings: list[Finding] = []
    entries = gitio.tree(repo, rev)
    by_fold: dict[str, set[str]] = {}
    for p in entries:
        decoded = p.decode("utf-8", "replace")
        by_fold.setdefault(decoded.casefold(), set()).add(decoded)
    for path_b, (mode, _sha) in sorted(entries.items()):
        try:
            path = path_b.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Finding("C2", f"path {path_b!r} is not valid UTF-8"))
            continue
        if path.startswith(LOCKED_AREAS):
            continue            # C1 holds every file there to the release: content, mode, extras
        problem = _shape_problem(path)
        if problem:
            findings.append(Finding("C2", f"{path} {problem}"))
            continue
        if mode in BAD_MODES:
            findings.append(Finding("C2", f"{path} is {BAD_MODES[mode]}"))
            continue
        if not (path in ROOT_FILES or allowed(PERSON, path) or path in UPDATE_FILES):
            findings.append(Finding("C2", f"{path} is outside every area"))
            continue
        if len(by_fold.get(path.casefold(), ())) > 1:
            others = sorted(by_fold[path.casefold()] - {path})
            findings.append(Finding("C2", f"{path} collides by letter case with {', '.join(others)}"))
    return findings


def check(repo: Path, base: str, head: str, kind: str) -> list[Finding]:
    if kind not in KINDS:
        raise ValueError(f"unknown PR kind {kind!r}")
    findings: list[Finding] = []
    changes = gitio.changes(repo, base, head)
    if not changes:
        return [Finding("C2", "the PR changes nothing")]

    head_paths = [p.decode("utf-8", "replace") for p in gitio.tree(repo, head)]
    by_fold: dict[str, set[str]] = {}
    for p in head_paths:
        by_fold.setdefault(p.casefold(), set()).add(p)

    for ch in changes:
        try:
            path = ch.path.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Finding("C2", f"path {ch.path!r} is not valid UTF-8"))
            continue
        problem = _shape_problem(path)
        if problem:
            findings.append(Finding("C2", f"{path} {problem}"))
            continue
        if ch.status != "D" and ch.new_mode in BAD_MODES:
            findings.append(Finding("C2", f"{path} is {BAD_MODES[ch.new_mode]}"))
            continue
        if not allowed(kind, path):
            findings.append(Finding("C2", f"{path} is outside the {kind} area"))
            continue
        if ch.status != "D" and len(by_fold.get(path.casefold(), ())) > 1:
            others = sorted(by_fold[path.casefold()] - {path})
            findings.append(Finding("C2", f"{path} collides by letter case with {', '.join(others)}"))
    # The read-back record is written after the master landed and the text was installed:
    # in a PR of its own. In the same PR as a master it would match it by construction
    # and N5 could never see a forgotten reinstall.
    paths = {ch.path.decode("utf-8", "replace") for ch in changes}
    if INSTALLED in paths and len(paths) > 1:
        findings.append(Finding("C2", f"{INSTALLED} is the record of an installation: a PR of its own, "
                                      "after the master it records has landed"))
    return findings

"""C5 text-integrity: no invisible corruption of the person's texts (docs/DESIGN.md §6).

Over the files a PR changes under user/: a NUL byte or bytes that are not UTF-8
block the PR; in the lines the PR adds, a word that mixes Latin and Cyrillic
letters blocks it — a look-alike swap such as Latin «a» inside a Russian word
(flagship B1, B13). A Latin name with a Russian ending is not a swap.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from . import gitio
from .numbers_check import USER_PREFIX
from .report import Finding

WORD_RE = re.compile(r"[^\W\d_]+")


def _script(ch: str) -> str | None:
    name = unicodedata.name(ch, "")
    if name.startswith("LATIN"):
        return "latin"
    if name.startswith("CYRILLIC"):
        return "cyrillic"
    return None


def _runs(word: str) -> list[str]:
    runs: list[str] = []
    for ch in word:
        script = _script(ch)
        if script and (not runs or runs[-1] != script):
            runs.append(script)
    return runs


def _is_swap(word: str) -> bool:
    runs = _runs(word)
    if len(runs) < 2:
        return False
    if runs == ["latin", "cyrillic"]:
        # A Latin name with a Russian ending («Notionе», «GitHubом», «iPhoneом») is how
        # Russian is written: a name of 2+ letters, an ending of up to 3. One Latin capital
        # before a Russian word («Cегодня», «Mама») is the usual look-alike swap.
        latin = sum(1 for c in word if _script(c) == "latin")
        cyrillic = sum(1 for c in word if _script(c) == "cyrillic")
        return latin < 2 or cyrillic > 3
    return True


def mixed_words(line: str) -> list[str]:
    return [w for w in WORD_RE.findall(line) if _is_swap(w)]


def _file_findings(path: str, raw: bytes, old_raw: bytes | None, every_line: bool) -> list[Finding]:
    if b"\0" in raw:
        return [Finding("C5", f"{path} contains a NUL byte")]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [Finding("C5", f"{path} is not valid UTF-8")]
    old = old_raw.decode("utf-8", "replace") if old_raw is not None else None
    fresh = None if every_line else gitio.added_lines(old, text)
    return [Finding("C5", f"{path} line {no}: a word mixes Latin and Cyrillic letters")
            for no, line in enumerate(text.splitlines(), start=1)
            if (fresh is None or no in fresh) and mixed_words(line)]


def check(repo: Path, base: str, head: str) -> list[Finding]:
    findings: list[Finding] = []
    for path, old_raw, raw in gitio.changed_files(repo, base, head, USER_PREFIX):
        findings += _file_findings(path, raw, old_raw, every_line=False)
    return findings


def check_tree(repo: Path, rev: str) -> list[Finding]:
    """C5 over every line of every file under user/ at rev — for N1 on main."""
    findings: list[Finding] = []
    for path, raw in gitio.files_under(repo, rev, USER_PREFIX):
        findings += _file_findings(path, raw, None, every_line=True)
    return findings

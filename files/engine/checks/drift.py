"""N5 field-drift: what the AI platform runs is what the repository says (docs/DESIGN.md §6, §10).

CI cannot read an instruction field or a routine on the platform. It can compare two
files in the repository: the master — for a field, the assembled text (C6's
`assemble`); for a routine, its master file — and the read-back record the bot keeps in
`user/fields/installed.toml` after it installs that text:

    [fields.chat]
    sha256 = "…"          # of the assembled text, UTF-8
    [fields.bot]
    sha256 = "…"
    [routines.morning]    # user/routines/morning.md
    sha256 = "…"          # of the master without its leading and trailing newlines

A master that changed more than GRACE ago and still differs from its record was not
reinstalled — an alarm to the trusted contact, who reinstalls the chat field by hand
(§10). A record for a routine whose master is gone means the platform may still run it.
N5 catches a forgotten reinstall, not a false record: the bot writes the record itself.
C2 (`record_check`) catches a record PR whose routine entry is not of its master; a record
equal to its master still does not prove what the platform runs.
"""
from __future__ import annotations

import hashlib
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

from . import gitio
from .limits_check import FIELDS, ROUTINES, assemble
from .paths_check import ROUTINE_RE
from .report import ALARM, Finding

INSTALLED = "user/fields/installed.toml"
GRACE = timedelta(hours=36)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def routine_text(raw: bytes) -> str:
    """The installed text of a routine: its master without leading and trailing newlines."""
    return raw.decode("utf-8").strip("\n")


def _alarm(message: str) -> Finding:
    return Finding("N5", message, ALARM)


def _record(repo: Path, main: str) -> tuple[dict, list[Finding]]:
    raw = gitio.file_at(repo, main, INSTALLED)
    if raw is None:
        return {}, []
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return {}, [_alarm(f"{INSTALLED} does not parse: {exc}")]
    return data, []


def _recorded(record: dict, table: str, name: str) -> object:
    section = record.get(table)
    entry = section.get(name) if isinstance(section, dict) else None
    return entry.get("sha256") if isinstance(entry, dict) else None


def _late(repo: Path, main: str, now: datetime, *paths: str) -> bool:
    """True if main last changed paths more than GRACE ago. Counted from the master's
    change, not the record's: a record rewritten later but still wrong does not restart
    the clock. The finding names no hour count: the same drift must read the same on
    every run, so that one acknowledgement covers it (§6)."""
    changed = gitio.last_change(repo, main, *paths)
    return changed is not None and now - changed > GRACE


HOURS = int(GRACE.total_seconds() // 3600)


def _shown(name: str) -> str:
    """A record's key as a finding prints it: the name of a routine master C2 allows as it is,
    anything else quoted — a key with a line break in it could forge a line of the run's log."""
    return name if ROUTINE_RE.fullmatch(f"{ROUTINES}{name}.md") else repr(name)


def record_check(repo: Path, base: str, head: str) -> list[Finding]:
    """C2's part for a PR that writes the read-back record (Issue #18, item 33): every routine
    entry of the record it lands — added, changed or left as it was — must be the sha256 of
    its master as the PR would land it (`head` is the merge of the PR with main the gate
    checks), without the master's leading and trailing newlines, and a routine it records must
    have a master. N5 is skipped on such a PR, so a wrong or stale entry (the first
    installation hashed a routine with its last newline; a master changed and not
    reinstalled) would otherwise land green and surface only as N5's alarm 36 h later. The
    fields stay N5's: the chat field's sha256 is given by the trusted contact by hand (REPO-3),
    and one slip in its form would block the whole record. A routine with no entry is N5's
    too: the record need not list every routine to land."""
    raw = gitio.file_at(repo, head, INSTALLED)
    if raw is None or raw == gitio.file_at(repo, base, INSTALLED):
        return []
    try:
        new = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [Finding("C2", f"{INSTALLED} does not parse: {exc}")]
    routines = new.get("routines", {})
    if not isinstance(routines, dict):
        return [Finding("C2", f"{INSTALLED}: «routines» is not a table with one table per routine (REPO-3)")]
    found: list[Finding] = []
    for name in sorted(routines):
        entry = routines[name]
        shown = _shown(name)
        if shown != name:
            found.append(Finding("C2", f"{INSTALLED} records routine {shown}: not the name of a file in {ROUTINES}"))
            continue
        sha = entry.get("sha256") if isinstance(entry, dict) else None
        if not isinstance(sha, str):
            found.append(Finding("C2", f"{INSTALLED} records routine {name} without a sha256 (REPO-3)"))
            continue
        master = gitio.file_at(repo, head, f"{ROUTINES}{name}.md")
        if master is None:
            found.append(Finding("C2", f"{INSTALLED} records routine {name}, but {ROUTINES}{name}.md is not on main"))
            continue
        try:
            text = routine_text(master)
        except UnicodeDecodeError:
            continue                    # C5 reports it
        if sha != digest(text):
            found.append(Finding("C2", f"{INSTALLED} records routine {name} with a sha256 that is not of its master on "
                                       f"main without its leading and trailing newlines: install that master and record "
                                       f"it again"))
    return found


def check(repo: Path, main: str, now: datetime) -> list[Finding]:
    record, found = _record(repo, main)
    if found:
        return found

    for name, (engine_path, person_path, _key, _floor) in FIELDS.items():
        engine_part = gitio.file_at(repo, main, engine_path)
        person_part = gitio.file_at(repo, main, person_path)
        if engine_part is None or person_part is None:
            continue                    # N1 reports a missing file (C1, C6)
        try:
            text = assemble(engine_part.decode("utf-8"), person_part.decode("utf-8"))
        except UnicodeDecodeError:
            continue                    # N1 reports it (C5)
        if _recorded(record, "fields", name) == digest(text):
            continue
        if _late(repo, main, now, engine_path, person_path):
            found.append(_alarm(f"the {name} field changed more than {HOURS} h ago and is not reinstalled: "
                                f"{INSTALLED} does not match it"))

    masters = {}
    for path, raw in gitio.files_under(repo, main, ROUTINES):
        if path.endswith(".md"):
            masters[path[len(ROUTINES):-len(".md")]] = (path, raw)
    for name, (path, raw) in sorted(masters.items()):
        try:
            text = routine_text(raw)
        except UnicodeDecodeError:
            continue                    # N1 reports it (C5)
        if _recorded(record, "routines", name) == digest(text):
            continue
        if _late(repo, main, now, path):
            found.append(_alarm(f"routine {name} changed more than {HOURS} h ago and is not reinstalled: "
                                f"{INSTALLED} does not match it"))

    routines = record.get("routines")
    for name in sorted(routines if isinstance(routines, dict) else {}):
        if name in masters:
            continue
        path = f"{ROUTINES}{name}.md"
        # From the master's deletion; a master that never existed — from the record.
        ever = gitio.last_change(repo, main, path) is not None
        if _late(repo, main, now, path if ever else INSTALLED):
            found.append(_alarm(f"routine {_shown(name)} is recorded as installed, but its master has been gone "
                                f"for more than {HOURS} h: the platform may still run it"))
    return found

"""The nightly copy, as the workflow runs it (docs/DESIGN.md §9):

    python -m engine.backup.run --repo . --main MAIN_SHA --worktree /path/to/notion-backup \\
        --report report.json

Reads the person's config at MAIN (`notion.backup_exclude`, `notion.shrink_alarm`,
`notion.removed_bases`) and the floors; reads Notion with the token in NOTION_TOKEN; writes the copy into the worktree of
`notion-backup` — the workflow commits and pushes it — and the report `engine.checks.alarm`
reads: {"N3": [...], "N6": [...], "error": [...], "skipped": []}. Nothing is written when
Notion could not be read in full. Exit code 1 if N3 or N6 found something, 2 if the copy
could not be made — the config or the floors, Notion or `user/engine.lock` not read, the
copy, its stamp or the report not written (an alarm too) — 0 otherwise. The report is the
night's last write, made in one step (a file beside it, renamed): a night that crashes
before it leaves none, and the workflow takes exit 1 without a non-empty report for a
crash — nothing is committed (Issue #18, item 42).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from ..checks import config_check, gitio, lock_check
from ..checks.report import ALARM, Finding, render
from . import copy as copying
from .notion import Notion, NotionError


def _floors(repo: Path, main: str) -> dict:
    raw = gitio.file_at(repo, main, config_check.DEFAULTS)
    if raw is None:
        raise ValueError(f"{config_check.DEFAULTS} is missing on main")
    return tomllib.loads(raw.decode("utf-8")).get("floors", {})


def settings(repo: Path, main: str) -> tuple[set[str], float, list[str], set[tuple[str, str]]]:
    """(excluded ids, shrink threshold, payment patterns, her decisions on bases she removed
    as (id, day)) from the config and the floors.
    The threshold never exceeds the floor: C3 keeps the config under it, and this run
    does not trust a config C3 has not seen. A config the copy cannot read whole stops the
    night (`config_check.read_for_copy`, Issue #18, item 46)."""
    floors = _floors(repo, main)
    ceiling = floors.get("backup_shrink_alarm_max")
    if isinstance(ceiling, bool) or not isinstance(ceiling, (int, float)):
        raise ValueError("floor backup_shrink_alarm_max is missing")
    payment = floors.get("payment_patterns_min")
    if not isinstance(payment, list) or not payment:
        raise ValueError("floor payment_patterns_min is missing")
    config = config_check.read_for_copy(repo, main)    # raises: never «exclude nothing» on a broken config
    notion = config.get("notion", {}) if isinstance(config.get("notion"), dict) else {}
    exclude = {copying.norm(x) for x in notion.get("backup_exclude", []) if isinstance(x, str)} - {""}
    wanted = notion.get("shrink_alarm")
    threshold = float(ceiling)
    if isinstance(wanted, (int, float)) and not isinstance(wanted, bool) and 0 < wanted < threshold:
        threshold = float(wanted)
    return exclude, threshold, [str(p) for p in payment], copying.removed_decisions(notion.get("removed_bases", []))


# The failures that end a night as «the copy could not be made» (exit 2): caught while
# Notion is read, and while the lock is read and the copy written.
FAILS = (NotionError, ValueError, RuntimeError, gitio.GitError, OSError)


def _write_report(path: Path, report: dict) -> None:
    """In one step — a file beside it, then a rename — so the workflow never finds a half
    report: on a full disk the file being written is emptied first, then the write fails."""
    part = path.with_name(path.name + ".part")
    try:
        part.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def _not_made(path: Path, report: dict, exc: Exception) -> int:
    """The night's own error, exit 2: the workflow stops before its commit whether or not the
    report gets written. With it the alarms raise «the copy could not run» and judge neither
    N3 nor N6 (`alarm._concerns`); without it, «no readable report». The report goes last."""
    report["error"].append(f"the copy could not be made: {type(exc).__name__}: {exc}")
    print(render([], [], notes=[f"ERROR {e}" for e in report["error"]], word="alarm"))
    try:
        _write_report(path, report)
    except OSError as again:                    # the same full disk: exit 2 still stops the commit
        print(f"ERROR the report could not be written: {type(again).__name__}")
    return 2


def main(argv: list[str] | None = None, api=None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--main", required=True, help="main, as the workflow checked it out")
    ap.add_argument("--worktree", type=Path, required=True, help="a checkout of notion-backup")
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--token-env", default="NOTION_TOKEN")
    args = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)

    report: dict = {"N3": [], "N6": [], "error": [], "skipped": []}
    findings: list[Finding] = []
    try:
        exclude, threshold, payment, removed = settings(args.repo, args.main)
        previous = copying.read_stamp(args.worktree)
        if api is None:
            api = Notion(os.environ.get(args.token_env, ""))
        result = copying.collect(api, exclude, payment)
    except FAILS as exc:
        # Nothing written: a half copy would read as a deletion, or hide one (P5).
        return _not_made(args.report, report, exc)

    message = copying.shrink(previous, result.records, threshold)
    if message:
        findings.append(Finding("N3", message, ALARM))
    lines, held = copying.held_bases(previous, result.bases, result.excluded_bases, removed, now.strftime("%Y-%m-%d"))
    findings += [Finding("N3", line, ALARM) for line in lines]
    findings += [Finding("N6", line, ALARM) for line in result.redacted]
    try:
        stamp = {"run": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "main": args.main,
                 "release": lock_check.lock_release(args.repo, args.main),
                 "records": result.records, "pages": result.pages, "rows": result.rows,
                 "sources": result.sources, "excluded": result.excluded, "bases": result.bases, "held": held,
                 "card_numbers_removed": len(result.redacted), "requests": getattr(api, "requests", None)}
        copying.write(result, args.worktree, stamp)
    except FAILS as exc:
        # `write` deletes the old tree first, so a failure here leaves a half tree (a full
        # disk, a text UTF-8 cannot hold): exit 2 keeps the workflow from committing it.
        return _not_made(args.report, report, exc)

    report["N3"] = [f.message for f in findings if f.check == "N3"]
    report["N6"] = [f.message for f in findings if f.check == "N6"]
    notes = result.notes + [f"COPY records={result.records} pages={result.pages} rows={result.rows} "
                            f"sources={result.sources} excluded={result.excluded} requests={stamp['requests']}"]
    print(render(findings, ["N3", "N6"], notes=notes, word="alarm"))
    try:
        _write_report(args.report, report)      # last: only a night that got this far has a report
    except OSError as exc:
        return _not_made(args.report, report, exc)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

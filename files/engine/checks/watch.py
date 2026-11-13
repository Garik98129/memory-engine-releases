"""Run the checks of main — N1, N2, N4, N5 and the bot's to-do N7 — and write the report the alarms read (docs/DESIGN.md §6).

    python -m engine.checks.watch --repo . --main MAIN_SHA --manifest release-manifest.json \\
        --report report.json

Reads GitHub through GITHUB_REPOSITORY, GITHUB_REPOSITORY_OWNER and GH_TOKEN. Prints
one line per finding; writes {"N1": [...], "N2": [...], "N4": [...], "N5": [...], "todo": [...], "field": {...}, "error": [...],
"skipped": [...]} for `engine.checks.alarm`. «field» is the instruction field of an installation
without a bot, for the Issue that hands a changed field to her (`field_issue.py`): not an alarm
either, and left out by a run that does not judge it. «todo» is N7's work list for the bot, not an alarm: it
never turns the run red, and a run that does not judge N5, or whose record does not parse,
leaves it out, so its Issue is left alone. A part that could not run is an error line, and
the other parts still report; a skipped check is one this run did not judge, and the alarms leave
its Issue alone. A run whose main is no longer main — a re-run of an old run, or main
moved since the checkout — judges nothing: the run for the new main does. Exit code 1 if
anything raises an alarm, 2 if a part could not run — an error is an alarm too, and a run
with an error closes nothing (P5).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import (backup_check, behind, config_check, drift, field_issue, integrity, limits_check, lock_check,
               paths_check, routine_check)
from .github import GitHub
from .report import render


def main(argv: list[str] | None = None, api=None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--main", required=True, help="main, as the workflow checked it out")
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--branches", default="refs/remotes/origin/",
                    help="where the fetched branches are, as a ref prefix")
    ap.add_argument("--record-pr", action="store_true",
                    help="the gate checks a PR that records an installation: N5 does not apply")
    args = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)

    report: dict = {"N1": [], "N2": [], "N4": [], "N5": [], "error": [], "skipped": []}
    n1, n2, n4, n5, notes = [], [], [], [], []
    ran = ["N1", "N2", "N4", "N5"]
    skipped: dict[str, str] = {}
    try:
        if api is None:
            api = GitHub(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", ""))
        manifest = lock_check.load_manifest(args.manifest)
        live = api.get(f"repos/{api.repository}/git/ref/heads/main")["object"]["sha"]
    except Exception as exc:    # noqa: BLE001 — nothing can run without these; loud (P5)
        report["error"].append(f"the checks could not start: {type(exc).__name__}: {exc}")
        live = None
    if live is not None and live != args.main:
        # A re-run of an old run checks out the old main with the old workflow; a run that
        # judged it could close alarms the current main deserves. Nothing is judged here:
        # the run for the current main does (every push to main starts one, and job `merge`
        # starts the check again when main moved).
        reason = f"main is at {live[:12]} now, this run checked out {args.main[:12]}: its run judges it"
        ran, skipped = [], {"N1": reason, "N2": reason, "N4": reason, "N5": reason}
    elif live is not None:
        n1, notes, errors = integrity.check(api, args.repo, args.main, manifest, now,
                                            os.environ.get("GITHUB_REPOSITORY_OWNER", ""), args.branches)
        report["error"] += errors
        try:
            config = config_check.read(args.repo, args.main)
        except Exception as exc:    # noqa: BLE001 — loud, and N2 and N4 run on {} as they did before (P5)
            report["error"].append(f"the config could not be read: {type(exc).__name__}: {exc}")
            config = {}
        try:
            installed = paths_check.installed(args.repo, args.main)
        except Exception as exc:    # noqa: BLE001 — loud; read as installed, so every check still runs (P5)
            report["error"].append(f"whether main is installed could not be read: {type(exc).__name__}: {exc}")
            installed = True
        if not installed:
            # A repository made from the template, before `setup` (DESIGN_PHASE3 §5): no copy,
            # no bot, no field to watch yet. Said, never a silent green; skipped, so the alarms
            # leave those Issues alone. N1 still holds the engine and main's record.
            reason = "not installed yet: the workflow «setup» writes her part"
            ran = [c for c in ran if c not in ("N2", "N4", "N5")]
            skipped.update({"N2": reason, "N4": reason, "N5": reason})
            notes.append("N2 NOTE N2, N4, N5 and N7 do not run: not installed yet — the workflow «setup» "
                          "writes her part")
        elif not config_check.copy_on(config):
            # The copy is off (DESIGN_PHASE3 §7): no branch to watch. Said, never a silent green;
            # skipped, so the alarms leave N2's Issue alone.
            ran = [c for c in ran if c != "N2"]
            skipped["N2"] = "the nightly copy is off (notion.backup = false)"
            notes.append("N2 NOTE the nightly copy is off (notion.backup = false): nothing to watch")
        else:
            try:
                n2, notes2 = backup_check.check(api, args.repo, args.branches, now)
                notes += notes2
            except Exception as exc:    # noqa: BLE001
                report["error"].append(f"N2 could not run: {type(exc).__name__}: {exc}")
        bot = installed and limits_check.has_bot(config)     # before installation: skipped above
        if installed and not bot:
            # No bot (docs/DESIGN_PHASE3.md §2): no daily mark to watch, no bot's field or
            # routine record to compare, no to-do for a bot. Said in a line, never a silent
            # green (P5); skipped, so the alarms leave those Issues alone.
            reason = "this installation has no bot (platform.bot = false)"
            ran = [c for c in ran if c not in ("N4", "N5")]
            skipped.update({"N4": reason, "N5": reason})
            notes.append("N4 NOTE N4, N5 and N7 do not run: this installation has no bot (platform.bot = false)")
            # Nobody installs her field for her: a field that is not the one she was given goes
            # to her in an Issue (DESIGN_PHASE3 §6). Not an alarm; the alarms step writes it.
            try:
                handed = field_issue.check(args.repo, args.main, config)
                if handed is not None:      # None: a part of the field cannot be read — N1 says so
                    report["field"] = handed
            except Exception as exc:    # noqa: BLE001
                report["error"].append(f"the field's Issue could not be judged: {type(exc).__name__}: {exc}")
        elif bot:
            try:
                n4, notes4 = routine_check.check(api, config, now)
                notes += notes4
            except Exception as exc:    # noqa: BLE001
                report["error"].append(f"N4 could not run: {type(exc).__name__}: {exc}")
        if bot and args.record_pr:
            # The record this PR brings is what N5 would ask for: judging main without it
            # would hold the record out forever. Skipped, not «clean»: the alarms leave N5 alone.
            ran.remove("N5")
            skipped["N5"] = "this PR records an installation"
        elif bot:
            try:
                n5 = drift.check(args.repo, args.main, now)
            except Exception as exc:    # noqa: BLE001
                report["error"].append(f"N5 could not run: {type(exc).__name__}: {exc}")
            # The bot's to-do after a release (N7): judged where N5 is, on the same main.
            try:
                todo = behind.check(args.repo, args.main, now)
                if todo is not None:        # None: the record does not parse (N5) — the list stays as it is
                    report["todo"] = todo
            except Exception as exc:    # noqa: BLE001
                report["error"].append(f"N7 could not run: {type(exc).__name__}: {exc}")

    report["N1"] = [f.message for f in n1]
    report["N2"] = [f.message for f in n2]
    report["N4"] = [f.message for f in n4]
    report["N5"] = [f.message for f in n5]
    report["skipped"] = sorted(skipped)
    print(render(n1 + n2 + n4 + n5, ran, skipped, notes=notes + [f"ERROR {e}" for e in report["error"]], word="alarm"))
    for line in report.get("todo", []):
        print(f"TODO {line}")
    if "field" in report:
        print(f"FIELD {drift.digest(report['field']['text'])[:12]}: handed to her in the Issue "
              f"«{field_issue.TITLE}» when it is not the one she was given")
    # The report goes last, in one step: watch.yml takes exit 1 as «found something» only with
    # a non-empty report, and a crash exits 1 too — as engine/backup/run.py.
    part = args.report.with_name(args.report.name + ".part")
    try:
        part.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        os.replace(part, args.report)
    finally:
        part.unlink(missing_ok=True)
    if report["error"]:
        return 2
    return 1 if n1 or n2 or n4 or n5 else 0


if __name__ == "__main__":
    sys.exit(main())

"""Run the checks of main — N1, N2, N4 and N5 — and write the report the alarms read (docs/DESIGN.md §6).

    python -m engine.checks.watch --repo . --main MAIN_SHA --manifest release-manifest.json \\
        --report report.json

Reads GitHub through GITHUB_REPOSITORY, GITHUB_REPOSITORY_OWNER and GH_TOKEN. Prints
one line per finding; writes {"N1": [...], "N2": [...], "N4": [...], "N5": [...], "error": [...], "skipped": [...]}
for `engine.checks.alarm`. A part that could not run is an error line, and the other
parts still report; a skipped check is one this run did not judge, and the alarms leave
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

from . import backup_check, config_check, drift, integrity, lock_check, routine_check
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
            n2, notes2 = backup_check.check(api, args.repo, args.branches, now)
            notes += notes2
        except Exception as exc:    # noqa: BLE001
            report["error"].append(f"N2 could not run: {type(exc).__name__}: {exc}")
        try:
            n4, notes4 = routine_check.check(api, config_check.read(args.repo, args.main), now)
            notes += notes4
        except Exception as exc:    # noqa: BLE001
            report["error"].append(f"N4 could not run: {type(exc).__name__}: {exc}")
        if args.record_pr:
            # The record this PR brings is what N5 would ask for: judging main without it
            # would hold the record out forever. Skipped, not «clean»: the alarms leave N5 alone.
            ran.remove("N5")
            skipped["N5"] = "this PR records an installation"
        else:
            try:
                n5 = drift.check(args.repo, args.main, now)
            except Exception as exc:    # noqa: BLE001
                report["error"].append(f"N5 could not run: {type(exc).__name__}: {exc}")

    report["N1"] = [f.message for f in n1]
    report["N2"] = [f.message for f in n2]
    report["N4"] = [f.message for f in n4]
    report["N5"] = [f.message for f in n5]
    report["skipped"] = sorted(skipped)
    print(render(n1 + n2 + n4 + n5, ran, skipped, notes=notes + [f"ERROR {e}" for e in report["error"]], word="alarm"))
    # The report goes last, in one step: watch.yml takes exit 1 as «found something» only with
    # a non-empty report, and a crash exits 1 too (Issue #18, item 1) — as engine/backup/run.py.
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

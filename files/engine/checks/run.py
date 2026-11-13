"""Run the PR checks C1–C7 and print one line per finding (docs/DESIGN.md §6, §11).

    python -m engine.checks.run --repo . --base MAIN_SHA --head PR_SHA --kind person \\
        --manifest release-manifest.json --body pr-body.md

The checks read the merge result — the tree main would hold after the PR merges —
and take thresholds and floors from main. `--manifest-url` prints where the gate
fetches the release manifest from, and exits.

Exit code 1 if anything blocks, 2 if a check could not run (P5: loud, never «skipped»).
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from . import (approval_check, config_check, drift, gitio, limits_check, lock_check,
               numbers_check, paths_check, text_check)
from .report import render
from ..setup import setup as installer

REPO_RE = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
MANIFEST_FILE = "release-manifest.json"


def releases_repository(repo: Path, base: str) -> str:
    """The public repository of releases, owner/name, from main's defaults — validated, so
    it cannot steer a URL elsewhere."""
    raw = gitio.file_at(repo, base, config_check.DEFAULTS)
    if raw is None:
        raise ValueError(f"{config_check.DEFAULTS} is missing on main")
    table = tomllib.loads(raw.decode("utf-8")).get("release", {})
    releases = table.get("manifests", "") if isinstance(table, dict) else ""
    if not isinstance(releases, str) or not REPO_RE.fullmatch(releases) or releases.partition("/")[2].strip(".") == "":
        raise ValueError(f"release.manifests {releases!r} is not owner/name")
    return releases


def manifest_url(repo: Path, base: str, result: str, kind: str) -> str:
    """The manifest file at the release's tag in the public repository of releases:
    read without a token (§5). The repository comes from main's defaults, the tag
    from the lock — both validated, so neither can steer the URL elsewhere."""
    releases = releases_repository(repo, base)
    tag = lock_check.wanted_release(repo, base, result, kind)
    return f"https://raw.githubusercontent.com/{releases}/refs/tags/{tag}/{MANIFEST_FILE}"


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--base", required=True, help="main, as the gate checked it out")
    ap.add_argument("--head", required=True, help="the PR head commit")
    ap.add_argument("--kind", required=True, choices=paths_check.KINDS)
    ap.add_argument("--actor", default="",
                    help="login of who pushed the checked head, when the run knows it; else empty")
    ap.add_argument("--before", default="",
                    help="the PR head before that push (event `before` on synchronize); else empty")
    ap.add_argument("--body", type=Path, help="the PR body, as fetched by the gate")
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--manifest", type=Path)
    what.add_argument("--manifest-url", action="store_true")
    args = ap.parse_args(argv)
    if args.manifest is not None and args.body is None:
        ap.error("--manifest needs --body")

    repo, base, kind = args.repo, args.base, args.kind
    now = now or datetime.now(timezone.utc)                 # the gate's clock: C1's channel, C7's approval
    try:
        result = gitio.merge_result(repo, base, args.head)
        if args.manifest_url:
            print(manifest_url(repo, base, result, kind))
            return 0
        manifest = lock_check.load_manifest(args.manifest)
        body = args.body.read_text(encoding="utf-8")
        findings, notes = [], []
        findings += lock_check.check(repo, base, result, kind, manifest, now)
        findings += paths_check.check(repo, base, result, kind)
        if kind == paths_check.SETUP:                             # C2: her part, as setup builds it and nothing else
            findings += installer.held(repo, result)
        findings += drift.record_check(repo, base, result)        # C2: a record's routine entries, against their masters
        findings += config_check.check(repo, base, result, kind, args.actor, args.before)
        findings += numbers_check.check(repo, base, result, body, notes)
        findings += text_check.check(repo, base, result)
        findings += limits_check.check(repo, base, result)
        ran = ["C1", "C2", "C3", "C4", "C5", "C6"]
        skipped = {}
        reason = approval_check.not_applicable(repo, base, result, kind)
        if reason is None:
            findings += approval_check.check(repo, base, result, body, now, gitio.commit_time(repo, args.head))
            ran.append("C7")
        else:
            skipped["C7"] = reason
    except Exception as exc:    # a bug in a check is loud too (P5), never a traceback read as a block
        print(f"ERROR a check could not run: {type(exc).__name__}: {exc}")
        print("SUMMARY block=? ran=none")
        return 2

    print(render(findings, ran, skipped, notes))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

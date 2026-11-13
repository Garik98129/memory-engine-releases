"""C1 engine-lock: the engine and the workflows equal the release (docs/DESIGN.md §6).

The reference is the release manifest in the public repository of releases — never a file
the PR can edit. `user/engine.lock` only records which release is installed. An
update PR may not move to an older release: no rollback through the update path.
The same release is a repair (N1 restores the engine from it). A newer release is due
only when the person's channel allows it: `canary` at once, `stable` 7 days after the
release was published (§5) — so neither the update workflow nor a bot pushing to an
update PR brings it in earlier.

Manifest (JSON): {"release": "v1.2.3", "published": "2026-11-09T03:30:49Z",
"files": {"engine/...": "<sha256>", ...}}. The paths are the paths in a person's repository.
"""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config_check, gitio
from .paths_check import PERSON, SETUP, UPDATE
from .report import Finding

LOCKED_PREFIXES = ("engine", ".github")
LOCK_FILE = "user/engine.lock"
RELEASE_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
PUBLISHED_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# How long after it was published a release reaches each channel (§5). A channel main's
# config does not name validly — missing, not a table, not a string, unknown — waits the
# longest; C3 reports the config itself.
CHANNEL_DELAY = {"canary": timedelta(0), "stable": timedelta(days=7)}
SLOWEST = "stable"


def release_key(tag: object) -> tuple[int, int, int]:
    m = RELEASE_RE.fullmatch(tag) if isinstance(tag, str) else None
    if m is None:
        raise ValueError(f"release tag {tag!r} is not vMAJOR.MINOR.PATCH")
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise ValueError("manifest has no «files» table")
    release_key(data.get("release", ""))
    return data


def lock_release(repo: Path, rev: str) -> str | None:
    raw = gitio.file_at(repo, rev, LOCK_FILE)
    if raw is None:
        return None
    return tomllib.loads(raw.decode("utf-8")).get("release")


def wanted_release(repo: Path, base: str, head: str, kind: str) -> str:
    """Which release's manifest the gate must fetch: the installed one for a
    person's PR and a setup PR, the one the update names for an update. Validated, so
    the tag is safe to put into a URL."""
    tag = lock_release(repo, base if kind in (PERSON, SETUP) else head)
    if tag is None:
        raise ValueError(f"{LOCK_FILE} is missing")
    release_key(tag)
    return tag


def published_at(manifest: dict) -> datetime | None:
    """When the factory cut the release, from its manifest; None if the manifest carries
    no valid time. The workflow `tag` tags it on the push that brings the manifest."""
    value = manifest.get("published")
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, PUBLISHED_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def channel(repo: Path, base: str) -> str:
    """The channel main's config names — never the PR's, so an update PR cannot pick a
    faster channel for itself (C3 guards the key). Anything else reads as the slowest."""
    engine = config_check.read(repo, base).get("engine")
    name = engine.get("channel") if isinstance(engine, dict) else None
    return name if isinstance(name, str) and name in CHANNEL_DELAY else SLOWEST


def not_due(repo: Path, base: str, manifest: dict, now: datetime) -> Finding | None:
    """A newer release before the person's channel allows it."""
    release = manifest["release"]
    published = published_at(manifest)
    if published is None:
        return Finding("C1", f"the manifest of {release} has no valid «published» time")
    name = channel(repo, base)
    due = published + CHANNEL_DELAY[name]
    if now < due:
        return Finding("C1", f"{release} is due on the {name} channel at {due.strftime(PUBLISHED_FORMAT)}, not yet")
    return None


def check(repo: Path, base: str, head: str, kind: str, manifest: dict,
          now: datetime | None = None) -> list[Finding]:
    """`now` is the gate's clock; the channel's due time is held against it."""
    findings: list[Finding] = []
    release = manifest["release"]

    base_release = lock_release(repo, base)
    if base_release is None:
        findings.append(Finding("C1", f"{LOCK_FILE} is missing on the base branch"))
    elif kind in (PERSON, SETUP) and base_release != release:
        findings.append(Finding("C1", f"manifest is {release}, but the installed release is {base_release}"))
    elif kind == UPDATE:
        if release_key(release) < release_key(base_release):
            findings.append(Finding("C1", f"update to {release} is older than the installed {base_release}"))
        elif release_key(release) > release_key(base_release):    # the same release is a repair: due already
            late = not_due(repo, base, manifest, now or datetime.now(timezone.utc))
            if late is not None:
                findings.append(late)
        head_release = lock_release(repo, head)
        if head_release != release:
            findings.append(Finding("C1", f"{LOCK_FILE} names {head_release}, the manifest is {release}"))

    expected: dict[str, str] = manifest["files"]
    present = gitio.tree(repo, head, *LOCKED_PREFIXES)
    present_paths = {p.decode("utf-8", "replace"): v for p, v in present.items()}

    for path in sorted(expected.keys() - present_paths.keys()):
        findings.append(Finding("C1", f"{path} is missing"))
    for path in sorted(present_paths.keys() - expected.keys()):
        findings.append(Finding("C1", f"{path} is not part of release {release}"))
    for path in sorted(expected.keys() & present_paths.keys()):
        mode, sha = present_paths[path]
        if mode != gitio.REGULAR:
            findings.append(Finding("C1", f"{path} has mode {mode}, expected {gitio.REGULAR}"))
            continue
        digest = hashlib.sha256(gitio.blob(repo, sha)).hexdigest()
        if digest != expected[path]:
            findings.append(Finding("C1", f"{path} differs from release {release}"))
    return findings

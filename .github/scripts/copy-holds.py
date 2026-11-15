"""Says whether a change of .github/scripts/stable.py still holds the releases the copy before
it held (workflow `tests`).

    python3 -I copy-holds.py <base> --repository OWNER/NAME [--copy PATH]

The code that moves the branch `stable` builds a release's index again from the release's own
topics, and moves only to a release whose published index is such a text, byte for byte. The
template keeps every text an index was published in (`INDEX_TEXTS` in the copy), and new words
come as a new text beside the old. So a copy in which a kept text was changed by one word, or
taken out, refuses every release published in it: the branch is then on a release that «is not whole», the
night's run is red, no move back is possible, and only a new release moved to by hand, past
its week, ends it (found by the reading of this change against its documents, on real
repositories). The template's `release cut` wants the copy here to be the release's own file,
so such a change does arrive as a pull request to this repository — and this is where it is
stopped: with the copy at <base> and with the one the pull request brings, every release tag is
read, and the branch `stable` as it stands; a release whole by the first and refused by the
second is a finding, and so is a branch the first reads and the second calls not its own — a
record of another form is enough — which only a move by hand would end, withdrawing every
newer release.

What this does not judge: a copy that holds more than the one before — one that takes an
edited index for whole. That is read in the pull request, as code, by its reader; this check
is for what a careful change breaks without meaning to.

A finding is not answered here. It says the template changed what a whole release is, and that
is the template's to solve — an index text that stays what it was for the releases cut with
it — before the copy comes.

Exit 0 — the new copy holds every release the old one held, or there is nothing to compare;
1 — a finding; 2 — the comparison could not be made.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

COPY = ".github/scripts/stable.py"


def load(name: str, source: bytes, path: str) -> types.ModuleType:
    """The copy's code from its source as it stands: no compiled file is read or left."""
    module = types.ModuleType(name)
    module.__file__ = path
    sys.modules[name] = module
    exec(compile(source, path, "exec", dont_inherit=True), module.__dict__)      # the copy's own future imports, not this file's
    return module


def plain(text: object) -> str:
    """On one line, printable ASCII, no «#»: a release's own words are a stranger's text."""
    line = " ".join(str(text).split()).encode("ascii", "backslashreplace").decode()
    return "".join(c if " " <= c <= "~" and c != "#" else f"\\x{ord(c):02x}" for c in line)


BRANCH = "the branch stable"


def verdicts(module: types.ModuleType, repo: Path, repository: str, now: datetime) -> dict[str, str | None]:
    """Release tag → None when the copy holds it whole, else its reason; and, under BRANCH, None
    when the copy reads the branch `stable` as one it left (or there is none), else its reason."""
    at = module.origin(repo, repository)
    found: dict[str, str | None] = {}
    for tag in sorted(at.tags, key=module.key):
        try:
            module.inspect(repo, at, tag)
            found[tag] = None
        except module.Refused as exc:
            found[tag] = str(exc)
    try:
        module.state(repo, at, at.tip, now)
        found[BRANCH] = None
    except module.Broken as exc:
        found[BRANCH] = str(exc)
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", help="the commit or ref the pull request is for: its copy is the one before")
    ap.add_argument("--repository", required=True, help="this repository's owner/name")
    ap.add_argument("--copy", type=Path, default=Path(__file__).resolve().parent / "stable.py", help="the copy the pull request brings")
    ap.add_argument("--repo", type=Path, default=Path("."), help="a checkout of this repository")
    args = ap.parse_args(argv)
    before = subprocess.run(["git", "-C", str(args.repo), "show", f"{args.base}:{COPY}"], capture_output=True)
    if before.returncode != 0:
        print(f"COPY nothing to compare: {plain(args.base)} has no {COPY} yet")
        return 0
    try:
        now = args.copy.read_bytes()
    except OSError:
        print(f"COPY ERROR {plain(args.copy)} cannot be read")
        return 2
    if now == before.stdout:
        print(f"COPY is the one {plain(args.base)} has: nothing changed in it")
        return 0
    try:
        moment = datetime.now(timezone.utc)
        was = verdicts(load("stable_before", before.stdout, f"{args.base}:{COPY}"), args.repo, args.repository, moment)
        new = verdicts(load("stable_now", now, str(args.copy)), args.repo, args.repository, moment)
    except Exception as exc:        # a copy that does not load, or no longer has what this reads: said, not guessed
        print(f"COPY ERROR the two copies could not be compared ({type(exc).__name__}: {plain(exc)[:200]}): if the "
              f"copy no longer has origin, key, inspect, state, Refused and Broken, this check changes with it, in the "
              f"same pull request")
        return 2
    unseen = "it does not see its tag"
    lost = [tag for tag, reason in was.items() if tag != BRANCH and reason is None and new.get(tag, unseen) is not None]
    for tag in lost:
        print(f"COPY finding: {tag} is whole by the copy before and refused by this one: {plain(new.get(tag, unseen))}")
    if lost:
        print(f"COPY finding: with this copy the branch stable could not stay on or move back to {len(lost)} of the "
              f"releases it holds today. The template changed what a whole release is; that is solved there — "
              f"the index text stays what it was for the releases cut with it — before the copy comes here.")
    stranded = was[BRANCH] is None and new[BRANCH] is not None
    if stranded:
        print(f"COPY finding: the branch stable as it stands is read by the copy before and is not this one's: "
              f"{plain(new[BRANCH])}. Every night would be red until a move by hand, which withdraws every newer "
              f"release. The template changed what it leaves on the branch; that is solved there first.")
    if lost or stranded:
        return 1
    whole = sum(1 for tag, reason in was.items() if tag != BRANCH and reason is None)
    branch = ("the branch stable is read as before" if was[BRANCH] is None else
              f"the branch stable is read as its own by neither copy — by the one before: {plain(was[BRANCH])} — "
              f"which is not this pull request's doing")
    print(f"COPY holds: every one of the {whole} releases whole by the copy before is whole by this one, and {branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

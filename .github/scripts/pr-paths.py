"""Says whether a pull request is a release and nothing else (workflow `pr`).

    python3 -I pr-paths.py <base> <head>

A release is three things at the top of this repository: `files/`, `release-manifest.json` and
`rules-index.md`. A pull request that changes any of them changes nothing else, for two reasons.

A tag is never taken back. The code that moves the branch `stable` (.github/scripts/stable.py)
refuses a release whose commit on `main` changed more than those three, and stays red on it
until a hand withdraws the release — so it is said here, before the merge, while the pull
request can still be split.

And what runs here must not come with what it judges. `.github/` holds the code that tags a
release and decides when one is trusted; a change to it comes by a pull request of its own,
which its own reader reads as code. The template's `release cut` never writes there; this
holds a pull request that was made to do it all the same.

What is compared is what the pull request brings: the paths that differ between <head> and the
commit where it left <base>. Nothing of the pull request is checked out or run — its paths are
read, as names. A path is printed escaped: it is a stranger's text.

Exit 0 — a release and nothing else, or no release at all; 1 — a release mixed with something
else; 2 — it could not be told.
"""
from __future__ import annotations

import re
import subprocess
import sys

KEPT = (b"release-manifest.json", b"rules-index.md")    # what a release is, beside files/ (stable.py: KEPT)
FOLDER = b"files/"
SHOWN = 10
_REV = re.compile(r"[0-9a-f]{40}|refs/[A-Za-z0-9._/-]+")


class Failed(RuntimeError):
    """What the pull request changes could not be read."""


def git(*args: str) -> bytes:
    proc = subprocess.run(["git", *args], capture_output=True)
    if proc.returncode != 0:
        raise Failed(f"git {' '.join(args[:2])}: {proc.stderr.decode(errors='replace').strip()[:200]!a}")
    return proc.stdout


def changed(base: str, head: str) -> list[bytes]:
    """The paths the pull request brings: what differs between `head` and the commit where it
    left `base`. A rename is its two paths, the old and the new."""
    for rev in (base, head):
        if not _REV.fullmatch(rev):
            raise Failed(f"{rev!a} is not a commit's sha or a ref")
        git("cat-file", "-e", f"{rev}^{{commit}}")
    forks = git("merge-base", base, head).decode().split()
    if len(forks) != 1:
        raise Failed("the pull request has no single commit where it left its base")
    return [p for p in git("diff", "--name-only", "-z", "--no-renames", forks[0], head).split(b"\0") if p]


def of_release(path: bytes) -> bool:
    return path in KEPT or path.startswith(FOLDER)


def shown(paths: list[bytes]) -> str:
    more = f" and {len(paths) - SHOWN} more" if len(paths) > SHOWN else ""
    return ", ".join(ascii(p.decode("utf-8", "backslashreplace")) for p in sorted(paths)[:SHOWN]) + more


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("PR ERROR usage: pr-paths.py <base> <head>")
        return 2
    try:
        paths = changed(*argv)
    except Failed as exc:
        print(f"PR ERROR {exc}")
        return 2
    release = [p for p in paths if of_release(p)]
    other = [p for p in paths if not of_release(p)]
    if release and other:
        print(f"PR finding: a release's pull request changes files/, release-manifest.json and rules-index.md, "
              f"and nothing else. This one changes {len(release)} of a release's paths and also: {shown(other)}. "
              f"Split it — the release in one pull request, the rest in another: a release whose commit "
              f"changed more is never moved to by the branch stable, and its tag cannot be taken back.")
        return 1
    if release:
        print(f"PR is a release and nothing else: {len(release)} paths, all of them files/, the manifest or the index")
    elif other:
        print(f"PR is not a release: it changes none of a release's paths ({len(other)} others)")
    else:
        print("PR changes nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

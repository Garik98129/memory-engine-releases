"""Says whether a pull request is a release and nothing else (workflow `pr`).

    python3 -I pr-paths.py <base> <head>

A release is three things at the top of this repository: `files/`, `release-manifest.json` and
`rules-index.md`. A pull request that changes any of them changes nothing else, for two reasons.

A tag is never taken back. The code that moves the branch `stable` (.github/scripts/stable.py)
refuses a release whose commit on `main` changed more than those three, and the night is red
for it until a hand withdraws the release or the branch moves on to a newer whole one — so it
is said here, before the merge, while the pull request can still be split.

And what runs here must not come with what it judges. `.github/` holds the code that tags a
release and decides when one is trusted; a change to it comes by a pull request of its own,
which its own reader reads as code. The template's `release cut` never writes there; this
holds a pull request that was made to do it all the same.

What is compared is what the pull request brings: the paths that differ between <head> and
the one commit where it left <base>. One commit: a branch that crossed `main` more than once
has several, and a diff from the one git happens to name hid a change to `.github/` that the
merge then brought (the adversarial reading of this change) — such a pull request is not
judged at all: it is told to take `main` in again («Update branch»), after which it left
`main` at one commit, `main`'s own. With one such commit, a merge changes on `main` no path
the branch did not change since it, so the branch's paths are all a merge can bring; a path
the branch changed just as `main` did meanwhile is counted too, which only asks for a rebase.
Nothing of the pull request is checked out or run — its paths are read, as names. A path is
printed escaped: it is a stranger's text.

Which commit of a merged branch gets the tag is not this check's but the script's that tags
(.github/scripts/tag-release.sh): it looks at the commits of `main`'s own line only, so of a
pull request merged by squash or by a merge commit only what `main` took is tagged. Merged by
rebase, its commits would each be looked at — which is why that way of merging is switched off
in this repository's settings.

Exit 0 — a release and nothing else, or no release at all; 1 — a release mixed with something
else; 2 — it could not be told: no single commit where it left its base, or git failed.
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
    """The paths the pull request brings: what differs between `head` and the one commit where
    it left `base`. A rename is its two paths, the old and the new. Failed when there is no
    such single commit."""
    for rev in (base, head):
        if not _REV.fullmatch(rev):
            raise Failed(f"{rev!a} is not a commit's sha or a ref")
        git("cat-file", "-e", f"{rev}^{{commit}}")
    forks = subprocess.run(["git", "merge-base", "--all", base, head], capture_output=True).stdout.decode().split()
    if len(forks) != 1:
        raise Failed(f"the pull request left main at {len(forks)} commits, not at one: what its merge would bring "
                     f"cannot be told from its paths. Press «Update branch» on the pull request — or rebase it "
                     f"onto main as it is now — and this check reads it again")
    return [p for p in git("diff", "--name-only", "-z", "--no-renames", forks[0], head).split(b"\0") if p]


def of_release(path: bytes) -> bool:
    return path in KEPT or path.startswith(FOLDER)


def shown(paths: list[bytes]) -> str:
    """The first paths, each on the line and in plain ASCII: no line break, and no «#» to open the
    runner's older form of a command, which it reads anywhere in a line."""
    more = f" and {len(paths) - SHOWN} more" if len(paths) > SHOWN else ""
    return ", ".join(ascii(p.decode("utf-8", "backslashreplace")).replace("#", "\\x23")
                     for p in sorted(paths)[:SHOWN]) + more


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

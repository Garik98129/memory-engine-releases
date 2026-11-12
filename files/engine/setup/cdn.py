"""The chat field for a platform whose chat did not open the rules' raw GitHub link (ChatGPT,
12.11.2026, on the owner's tests: the raw link, with its GitHub app on or off) but opened the
same file of the same release through jsDelivr, which serves a public repository's files at a
tag. `fields/solo-cdn.md` is `solo.md` with its one link moved to that address: the same
repository, the same release, the same files — only the host differs.

    python -m engine.setup.cdn            # say whether fields/solo-cdn.md is current
    python -m engine.setup.cdn --write    # rebuild it

The file is built, never edited by hand: a test holds it to `solo.md`, and the release's cut
holds `solo.md`'s link (`release.chat_rules_findings`), so both fields name one release.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RULES = Path(__file__).resolve().parents[1] / "rules"
SOLO = RULES / "fields" / "solo.md"
SOLO_CDN = RULES / "fields" / "solo-cdn.md"
# the link on the field's «Topic rules» line — its repository and release
LINK_RE = re.compile(r"https://raw\.githubusercontent\.com/([^/\s]+/[^/\s]+)/([^/\s]+)/files/engine/rules/")


def link(repo: str, tag: str, name: str = "") -> str:
    """A rules file of a release on jsDelivr; without a name, the folder the field adds names to."""
    return f"https://cdn.jsdelivr.net/gh/{repo}@{tag}/files/engine/rules/{name}"


def solo_cdn(solo: str) -> str:
    """solo.md with its one rules link moved to jsDelivr."""
    found = LINK_RE.findall(solo)
    if len(found) != 1:
        raise ValueError(f"fields/solo.md holds {len(found)} rules links, not one")
    return LINK_RE.sub(lambda m: link(m.group(1), m.group(2)), solo)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="rebuild fields/solo-cdn.md")
    args = ap.parse_args(argv)
    want = solo_cdn(SOLO.read_text(encoding="utf-8"))
    if SOLO_CDN.is_file() and SOLO_CDN.read_text(encoding="utf-8") == want:
        print("CDN current")
        return 0
    if args.write:
        SOLO_CDN.write_text(want, encoding="utf-8")
        print(f"CDN wrote {SOLO_CDN.name}")
        return 0
    print(f"CDN stale: {SOLO_CDN.name} — run python -m engine.setup.cdn --write")
    return 1


if __name__ == "__main__":
    sys.exit(main())

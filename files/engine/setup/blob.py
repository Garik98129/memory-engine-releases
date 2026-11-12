"""The chat field for a platform whose chat did not open the rules' raw link but opens GitHub's own
page of the same file (ChatGPT, 12.11.2026, on the owner's tests: the raw link failed with its
GitHub app on or off; with the app off it opened the github.com page of a rules file and retold
it). `fields/solo-blob.md` is `solo.md` with its one link moved to those pages: the same
repository, the same release, the same files — only the address form differs.

    python -m engine.setup.blob            # say whether fields/solo-blob.md is current
    python -m engine.setup.blob --write    # rebuild it

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
SOLO_BLOB = RULES / "fields" / "solo-blob.md"
# the link on the field's «Topic rules» line — its repository and release
LINK_RE = re.compile(r"https://raw\.githubusercontent\.com/([^/\s]+/[^/\s]+)/([^/\s]+)/files/engine/rules/")


def link(repo: str, tag: str, name: str = "") -> str:
    """GitHub's page of a rules file of a release; without a name, the folder the field adds names to."""
    return f"https://github.com/{repo}/blob/{tag}/files/engine/rules/{name}"


def solo_blob(solo: str) -> str:
    """solo.md with its one rules link moved to GitHub's pages."""
    found = LINK_RE.findall(solo)
    if len(found) != 1:
        raise ValueError(f"fields/solo.md holds {len(found)} rules links, not one")
    return LINK_RE.sub(lambda m: link(m.group(1), m.group(2)), solo)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="rebuild fields/solo-blob.md")
    args = ap.parse_args(argv)
    want = solo_blob(SOLO.read_text(encoding="utf-8"))
    if SOLO_BLOB.is_file() and SOLO_BLOB.read_text(encoding="utf-8") == want:
        print("BLOB current")
        return 0
    if args.write:
        SOLO_BLOB.write_text(want, encoding="utf-8")
        print(f"BLOB wrote {SOLO_BLOB.name}")
        return 0
    print(f"BLOB stale: {SOLO_BLOB.name} — run python -m engine.setup.blob --write")
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""The topic rules as one file, for a platform whose chat cannot open the rules' link (ChatGPT,
12.11.2026: it did not open the raw link, with its GitHub app on or off). The person adds the file to her
project's files once; `fields/solo-file.md` points the chat to it instead of the link.

    python -m engine.setup.bundle            # say whether the file is current
    python -m engine.setup.bundle --write    # rebuild it

The file is built, never edited by hand: a test holds it to the topic files, so a rule changed
in its own file and not rebuilt here fails the tests. One section per file, each opened by a
line «=== FILE: <name> ===», in the order of `ORDER`.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RULES = Path(__file__).resolve().parents[1] / "rules"
BUNDLE = RULES / "memory-engine-rules.md"
# install.md first: the first message sends the chat there; then the files the field lists
ORDER = ("install.md", "core.md", "health.md", "notion.md", "mail.md", "shopping.md", "calendar.md",
         "documents.md")
HEADER = ("# memory-engine-rules.md\n\n"
          "The topic rules of the memory engine, one section per file, each opened by a line "
          "«=== FILE: <name> ===». Where your instructions say to open a file, read its section here. "
          "Built from the engine's rule files; a new release brings a new copy of this file.\n")
SOLO = RULES / "fields" / "solo.md"
SOLO_FILE = RULES / "fields" / "solo-file.md"
# the link on the field's «Topic rules» line, and what stands in its place in solo-file.md
LINK_RE = re.compile(r"https://raw\.githubusercontent\.com/[^/\s]+/[^/\s]+/[^/\s]+/files/engine/rules/ \+ name")
IN_FILE = f"the project file {BUNDLE.name}, its section «=== FILE: name ===»"


def build(rules: Path = RULES) -> str:
    parts = [HEADER]
    for name in ORDER:
        parts.append(f"\n=== FILE: {name} ===\n\n" + (rules / name).read_text(encoding="utf-8").strip("\n") + "\n")
    return "".join(parts)


def solo_file(solo: str) -> str:
    """solo.md with its one link replaced by the project file — the hard part the chat gets on a
    platform that reads the rules from the file."""
    found = LINK_RE.findall(solo)
    if len(found) != 1:
        raise ValueError(f"fields/solo.md holds {len(found)} rules links, not one")
    return LINK_RE.sub(IN_FILE, solo)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="rebuild the bundle and fields/solo-file.md")
    args = ap.parse_args(argv)
    want = {BUNDLE: build(), SOLO_FILE: solo_file(SOLO.read_text(encoding="utf-8"))}
    stale = [p for p, text in want.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]
    if args.write:
        for p in stale:
            p.write_text(want[p], encoding="utf-8")
        print(f"BUNDLE wrote {', '.join(p.name for p in stale) or 'nothing: both current'}")
        return 0
    if stale:
        print(f"BUNDLE stale: {', '.join(p.name for p in stale)} — run python -m engine.setup.bundle --write")
        return 1
    print("BUNDLE current")
    return 0


if __name__ == "__main__":
    sys.exit(main())

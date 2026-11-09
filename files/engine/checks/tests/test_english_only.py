"""The template is English for everyone (docs/DESIGN.md §11; decision 44 of the plan): no
Cyrillic or Hebrew anywhere in the tracked files but docs/, with three exceptions, each
for a reason — C5 catches Latin/Cyrillic look-alike swaps, so its comments and its tests
show Cyrillic words; C4's tests hold an Israeli ID written the Israeli way and Russian
lines that must not trip the number patterns. docs/ is the design record: it quotes the
owner's decisions in his own words and is not checked here. The person's language lives
in her part, under user/, never in the engine."""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FOREIGN = re.compile(r"[\u0400-\u052F\u0590-\u05FF]")
ALLOWED = frozenset({
    "engine/checks/text_check.py",            # C5: the look-alike examples are Cyrillic by nature
    "engine/checks/tests/test_text.py",       # C5's fixtures
    "engine/checks/tests/test_numbers.py",    # C4: an Israeli ID as Israelis write it; Russian lines that must pass
})
SKIP_PREFIX = "docs/"


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\n") if p and not p.startswith(SKIP_PREFIX)]


class EnglishOnly(unittest.TestCase):
    def test_no_cyrillic_or_hebrew_outside_the_allowed_files(self):
        hits = []
        for path in tracked():
            if path in ALLOWED:
                continue
            try:
                text = (ROOT / path).read_text(encoding="utf-8")
            except (UnicodeDecodeError, FileNotFoundError):
                continue
            for n, line in enumerate(text.split("\n"), 1):
                if FOREIGN.search(line):
                    hits.append(f"{path}:{n}: {line.strip()[:80]}")
        self.assertEqual(hits, [], "\n" + "\n".join(hits))

    def test_every_allowed_file_still_exists(self):
        for path in sorted(ALLOWED):
            self.assertTrue((ROOT / path).is_file(), f"{path} is in the exception list but gone — drop it from the list")


if __name__ == "__main__":
    unittest.main()

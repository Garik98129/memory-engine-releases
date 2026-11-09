"""The hard parts of the fields as released leave the person's part the room that
engine/rules/README.md promises — counted as C6 counts (limits_check.length), with the two
newlines its assembly puts between the parts."""
import unittest
from pathlib import Path

from engine.checks import limits_check

FIELDS = Path(__file__).resolve().parents[2] / "rules" / "fields"
JOIN = 2
PROMISED = {"chat.md": (4000, 990), "bot.md": (10000, 2900)}      # the pilot's platform limit, the room promised


class ReleasedHardParts(unittest.TestCase):
    def test_the_persons_part_keeps_the_room_the_readme_promises(self):
        for name, (limit, room) in PROMISED.items():
            used = limits_check.length((FIELDS / name).read_text(encoding="utf-8").strip("\n"))
            self.assertLessEqual(used + JOIN + room, limit,
                                 f"{name}: {used} characters leave {limit - used - JOIN}, less than the {room} promised")


if __name__ == "__main__":
    unittest.main()

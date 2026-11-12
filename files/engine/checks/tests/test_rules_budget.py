"""The hard parts of the fields as released leave the person's part the room that
engine/rules/README.md promises — counted as C6 counts (limits_check.length), with the two
newlines its assembly puts between the parts."""
import unittest
from pathlib import Path

from engine.checks import limits_check

FIELDS = Path(__file__).resolve().parents[2] / "rules" / "fields"
JOIN = 2
PROMISED = {"chat.md": (4000, 1030), "bot.md": (10000, 2900),     # the platform's limit, the room promised
            "solo.md": (4000, 1030),      # the chat that writes: the same field, the same room (DESIGN_PHASE3 §6a)
            "solo-blob.md": (4000, 1030)}  # the same, the rules on GitHub's pages (ChatGPT)
# chat 1 030: the chat's own part needs it with its line 1 «(v5)» and the chat's gender line


class ReleasedHardParts(unittest.TestCase):
    def test_the_persons_part_keeps_the_room_the_readme_promises(self):
        for name, (limit, room) in PROMISED.items():
            used = limits_check.length((FIELDS / name).read_text(encoding="utf-8").strip("\n"))
            self.assertLessEqual(used + JOIN + room, limit,
                                 f"{name}: {used} characters leave {limit - used - JOIN}, less than the {room} promised")


    def test_the_chat_that_writes_leaves_her_at_least_the_room_she_has(self):
        """The pilot's way out (DESIGN_PHASE3 §6a): her part, as it fits beside chat.md today, fits
        beside solo.md too — the switch never asks her to shorten it."""
        room = lambda name: 4000 - limits_check.length((FIELDS / name).read_text(encoding="utf-8").strip("\n")) - JOIN
        self.assertGreaterEqual(room("solo.md"), room("chat.md"))
        self.assertGreaterEqual(room("solo-blob.md"), room("chat.md"))


if __name__ == "__main__":
    unittest.main()

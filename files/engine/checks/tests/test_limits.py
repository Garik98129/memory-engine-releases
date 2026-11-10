import unittest

from engine.checks import limits_check
from engine.checks.tests.helpers import VALID_CONFIG, Repo


def messages(findings):
    return [f.message for f in findings]


class FieldLimits(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def fill(self, engine_part: str, limit: int) -> str:
        """A person's part that makes the assembled field exactly limit long."""
        return "x" * (limit - limits_check.length(engine_part.strip("\n")) - 2)

    def test_field_at_the_limit_passes_one_more_blocks(self):
        engine_part = "Chat safety rules.\n"
        at = self.fill(engine_part, 4000)
        head = self.repo.commit({"user/fields/chat.md": at.encode()})
        self.assertEqual(limits_check.check(self.repo.path, self.repo.base, head), [])
        head = self.repo.commit({"user/fields/chat.md": (at + "y").encode()})
        self.assertEqual(messages(limits_check.check(self.repo.path, self.repo.base, head)),
                         ["the chat field would be 4001 characters, the limit is 4000"])

    def test_an_update_that_lengthens_the_engine_part_blocks(self):
        main = self.repo.commit({"user/fields/chat.md": self.fill("Chat safety rules.\n", 4000).encode()})
        head = self.repo.commit({"engine/rules/fields/chat.md": b"Chat safety rules, longer.\n"})
        self.assertIn("the chat field would be 4008 characters, the limit is 4000",
                      messages(limits_check.check(self.repo.path, main, head)))

    def test_emoji_counts_as_two(self):
        self.assertEqual(limits_check.length("a😀"), 3)

    def test_a_tighter_config_limit_applies(self):
        cfg = VALID_CONFIG.replace(b"chat_field_limit = 4000", b"chat_field_limit = 100")
        head = self.repo.commit({"user/config.toml": cfg, "user/fields/chat.md": b"x" * 200})
        self.assertIn("the chat field would be 220 characters, the limit is 100",
                      messages(limits_check.check(self.repo.path, self.repo.base, head)))

    def test_a_looser_config_never_lifts_the_floor(self):
        cfg = VALID_CONFIG.replace(b"chat_field_limit = 4000", b"chat_field_limit = 9000")
        head = self.repo.commit({"user/config.toml": cfg, "user/fields/chat.md": b"x" * 5000})
        self.assertIn("the chat field would be 5020 characters, the limit is 4000",
                      messages(limits_check.check(self.repo.path, self.repo.base, head)))

    def test_a_routine_at_the_measured_limit_passes_one_more_blocks(self):
        """10 000: a routine of that length was saved whole on the owner's bot (09.11.2026)."""
        head = self.repo.commit({"user/routines/morning.md": b"x" * 10000})
        self.assertEqual(limits_check.check(self.repo.path, self.repo.base, head), [])
        head = self.repo.commit({"user/routines/morning.md": b"x" * 10001})
        self.assertEqual(messages(limits_check.check(self.repo.path, self.repo.base, head)),
                         ["user/routines/morning.md is 10001 characters, the limit is 10000"])

    def test_a_tighter_routine_limit_in_the_config_applies(self):
        cfg = VALID_CONFIG.replace(b"[platform]\n", b"[platform]\nroutine_limit = 4000\n")
        self.assertIn(b"routine_limit = 4000", cfg)
        head = self.repo.commit({"user/config.toml": cfg, "user/routines/morning.md": b"x" * 4001})
        self.assertEqual(messages(limits_check.check(self.repo.path, self.repo.base, head)),
                         ["user/routines/morning.md is 4001 characters, the limit is 4000"])

    def test_missing_person_part_is_loud(self):
        head = self.repo.commit({"user/fields/bot.md": None})
        self.assertIn("user/fields/bot.md is missing",
                      messages(limits_check.check(self.repo.path, self.repo.base, head)))


if __name__ == "__main__":
    unittest.main()

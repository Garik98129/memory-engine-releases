import unittest

from engine.checks import text_check
from engine.checks.tests.helpers import Repo


def messages(findings):
    return [f.message for f in findings]


class TextIntegrity(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def test_latin_letter_inside_a_russian_word_blocks(self):
        head = self.repo.commit({"user/fields/chat.md": "Who: Мaма.\n".encode()})
        self.assertEqual(messages(text_check.check(self.repo.path, self.repo.base, head)),
                         ["user/fields/chat.md line 1: a word mixes Latin and Cyrillic letters"])

    def test_separate_words_in_two_scripts_are_fine(self):
        head = self.repo.commit({"user/fields/chat.md": "Она пишет Hello, Wi-Fi и שלום.\n".encode()})
        self.assertEqual(text_check.check(self.repo.path, self.repo.base, head), [])

    def test_a_latin_name_with_a_russian_ending_is_not_a_swap(self):
        head = self.repo.commit({"user/fields/chat.md": "Запиши в Notionе, спроси у GitHubа.\n".encode()})
        self.assertEqual(text_check.check(self.repo.path, self.repo.base, head), [])

    def test_swaps_at_the_end_or_inside_a_latin_word_block(self):
        self.assertEqual(text_check.mixed_words("Мамa и Nоtion"), ["Мамa", "Nоtion"])

    def test_a_latin_capital_before_a_russian_word_is_a_swap(self):
        self.assertEqual(text_check.mixed_words("Cегодня Mама и Aнна пришли"), ["Cегодня", "Mама", "Aнна"])
        self.assertEqual(text_check.mixed_words("в Notionе, iPhoneом, GitHubа, PRы"), [])
        self.assertEqual(text_check.mixed_words("Notionнейшего"), ["Notionнейшего"])

    def test_nul_byte_blocks(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who:\0 x\n"})
        self.assertEqual(messages(text_check.check(self.repo.path, self.repo.base, head)),
                         ["user/fields/chat.md contains a NUL byte"])

    def test_bytes_that_are_not_utf8_block(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who: \xff\n"})
        self.assertEqual(messages(text_check.check(self.repo.path, self.repo.base, head)),
                         ["user/fields/chat.md is not valid UTF-8"])

    def test_only_added_lines_are_read_for_look_alikes(self):
        main = self.repo.commit({"user/fields/chat.md": "Мaма.\nOne.\n".encode()})
        head = self.repo.commit({"user/fields/chat.md": "Мaма.\nTwo.\n".encode()})
        self.assertEqual(text_check.check(self.repo.path, main, head), [])


if __name__ == "__main__":
    unittest.main()

import hashlib
import unittest

from engine.checks import numbers_check
from engine.checks.tests.helpers import VALID_CONFIG, Repo

# Synthetic numbers only: a valid-check-digit ID, the public Visa test card,
# the textbook example IBANs, made-up phones.
ALL = list(numbers_check.PATTERNS)


def kinds(text):
    return sorted({k for _, k in numbers_check.scan(text, ALL)})


class Patterns(unittest.TestCase):
    def test_israeli_id_with_its_check_digit(self):
        self.assertEqual(kinds("ת.ז. 123456782"), ["an Israeli ID number"])
        self.assertEqual(kinds("ID 12345678-2"), ["an Israeli ID number"])
        self.assertEqual(kinds("order 123456789"), [])

    def test_card_with_luhn(self):
        self.assertEqual(kinds("card 4111 1111 1111 1111"), ["a card number"])
        self.assertEqual(kinds("card 4111-1111-1111-1112"), [])

    def test_card_shapes_as_banks_print_them(self):
        # No-break and narrow no-break spaces, two spaces, a dash with
        # spaces; Amex and Diners groups; a card followed by its expiry or CVV.
        for text in ("4111\u00a01111\u00a01111\u00a01111", "4111\u202f1111\u202f1111\u202f1111",
                     "4111  1111  1111  1111", "4111 - 1111 - 1111 - 1111", "5555\u00a0 5555\u00a0 5555\u00a0 4444",
                     "3782  822463  10005", "3056\u00a0930902\u00a05904", "4111  1111  1111  1111 12/30",
                     "4111 1111 1111 1111 123", "Card No.4111 1111 1111 1111",
                     "8171\u00a01234\u00a05678\u00a09011"):                    # UnionPay's 81 range (since 2019)
            self.assertEqual(kinds(f"card {text}"), ["a card number"], repr(text))

    def test_every_length_with_every_separator_is_cut_whole(self):
        # 13, 17 and 18 digits were caught only with single spaces, and
        # a 17-digit card whose first 16 digits pass Luhn too lost only those 16.
        def luhn_valid(prefix: str, length: int) -> str:
            body = prefix + "1" * (length - len(prefix) - 1)
            return next(body + d for d in "0123456789" if numbers_check._luhn(body + d))

        layouts = {13: (4, 4, 4, 1), 14: (4, 6, 4), 15: (4, 6, 5), 16: (4, 4, 4, 4),
                   17: (4, 4, 4, 4, 1), 18: (4, 4, 4, 4, 2), 19: (4, 4, 4, 4, 3)}
        for length, sizes in layouts.items():
            number = luhn_valid("3782" if length in (14, 15) else "4111", length)
            groups, at = [], 0
            for size in sizes:
                groups.append(number[at:at + size])
                at += size
            for sep in (" ", "-", "\u00a0", "\u202f", "  ", " - "):
                text = sep.join(groups)
                with self.subTest(length=length, sep=repr(sep)):
                    self.assertEqual(numbers_check.redact(f"card {text} end", ["card"]),
                                     ("card [card number removed] end", 1))
        self.assertTrue(numbers_check._luhn("4111111111111111") and numbers_check._luhn("41111111111111113"))
        self.assertEqual(numbers_check.redact("4111\u00a01111\u00a01111\u00a01111\u00a03", ["card"]),
                         ("[card number removed]", 1))

    def test_what_is_not_a_card(self):
        fraction = "1000000000000008"                     # Luhn-valid: as a card it would count
        self.assertEqual(kinds(fraction), ["a card number"])
        for text in (f"0.{fraction}", f"price 12.{fraction} EUR", f"цена 0,{fraction} ₪",
                     "4111.1111.1111.1111", "4111_1111_1111_1111",     # dots and underscores are not separators
                     "2023  2024  2025  2026", "2026-02-22 - 2026-03-01", "22.02.2026 – 01.03.2026",
                     "4111  1111  1111  1111  1111",            # a fifth group of four: a table row
                     "4111  4111  1111  1111  1111",            # nor from its second group on
                     "1500  4111  1111  1111  1111"):           # even when the first group is no card's
            self.assertEqual(kinds(text), [], repr(text))

    def test_no_date_range_reads_as_a_card(self):
        # An earlier pattern read 8–10 % of «2026-02-22 - 2026-03-01» as cards. Every
        # start day of 2026, four lengths, six spellings. («2026-02-22-2026-03-01», with no
        # spaces, is the free form's, as in v0.1.4, and not changed here.)
        from datetime import date, timedelta
        start, hits = date(2026, 1, 1), []
        for i in range(366):
            a = start + timedelta(days=i)
            for j in (1, 6, 13, 29):
                b = a + timedelta(days=j)
                for f, between in (("%Y-%m-%d", " - "), ("%Y-%m-%d", "  "), ("%d.%m.%Y", " – "), ("%d.%m.%Y", " - "),
                                   ("%d/%m/%Y", " - "), ("%Y-%m-%d", "\u00a0-\u00a0")):
                    line = a.strftime(f) + between + b.strftime(f)
                    if kinds(line):
                        hits.append(line)
        self.assertEqual(hits, [])

    def test_four_groups_of_four_are_a_card_with_any_of_its_separators(self):
        # The price of the wider separators, the same as of the single space before them: a
        # row of exactly four four-digit numbers that passes Luhn and starts like a card.
        self.assertEqual(kinds("4012 8888 8888 1881"), ["a card number"])
        self.assertEqual(kinds("4012  8888  8888  1881"), ["a card number"])
        self.assertEqual(kinds("1500  5200  6100  3802"), [])            # wider separators: only a card's first group
        # And with a single space a short number after the four groups no longer hides them:
        # a card followed by its expiry or CVV is caught, and so is such a row.
        self.assertEqual(kinds("4012 8888 8888 1881 15 pcs"), ["a card number"])
        # Three groups of four and one digit are a 13-digit card's layout: such a row passing
        # Luhn is a card too.
        self.assertEqual(kinds("4222\u00a02222\u00a02222\u00a02"), ["a card number"])

    def test_iban_with_its_checksum(self):
        self.assertEqual(kinds("GB82 WEST 1234 5698 7654 32"), ["an IBAN"])
        self.assertEqual(kinds("IL62 0108 0000 0009 9999 999"), ["an IBAN"])
        self.assertEqual(kinds("GB83 WEST 1234 5698 7654 32"), [])

    def test_phones(self):
        for text in ("050-123-4567", "03-1234567", "+972 50 123 4567", "00972-50-1234567",
                     "+1 (415) 555-2671"):
            self.assertEqual(kinds(f"call {text}"), ["a phone number"], text)

    def test_ordinary_text_is_clean(self):
        self.assertEqual(kinds("Звони 101. 2026-10-23 08:16, рост 1.75, 3787 знаков, v0.1.0"), [])

    def test_digits_inside_a_hash_do_not_count(self):
        self.assertEqual(kinds("a123456782b f0501234567a x4111111111111111y"), [])
        hits = [i for i in range(5000) if numbers_check.scan(hashlib.sha256(str(i).encode()).hexdigest(), ALL)]
        self.assertEqual(hits, [])


class Redact(unittest.TestCase):
    def test_a_card_printed_by_a_bank_is_cut_its_expiry_and_a_date_range_stay(self):
        text, count = numbers_check.redact("card 4111\u00a01111\u00a01111\u00a01111 12/30\n"
                                           "card 4111 1111 1111 1111 12/30\n"
                                           "trip 2026-02-22 - 2026-03-01\n", ["card"])
        self.assertEqual(text, "card [card number removed] 12/30\ncard [card number removed] 12/30\n"
                               "trip 2026-02-22 - 2026-03-01\n")
        self.assertEqual(count, 2)

    def test_a_19_digit_card_is_cut_whole_even_when_its_first_16_digits_pass(self):
        text, count = numbers_check.redact("card 4111  1111  1111  1111  003\n", ["card"])
        self.assertEqual((text, count), ("card [card number removed]\n", 1))


PAY = " — payment data never goes into git"
HOME = " — its home is the Documents base in Notion"


class Check(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def run_check(self, head, body="", base=None):
        return [f.message for f in numbers_check.check(self.repo.path, base or self.repo.base, head, body)]

    def test_a_document_number_in_a_master_points_to_its_home(self):
        head = self.repo.commit({"user/fields/bot.md": b"Who: a test person.\nID 123456782\n"})
        findings = numbers_check.check(self.repo.path, self.repo.base, head, "")
        self.assertEqual([f.message for f in findings], ["user/fields/bot.md line 2: an Israeli ID number" + HOME])
        self.assertNotIn("123456782", findings[0].line())

    def test_a_card_in_a_master_is_payment_data(self):
        head = self.repo.commit({"user/routines/r.md": b"Pay with 4111 1111 1111 1111\n"})
        self.assertEqual(self.run_check(head), ["user/routines/r.md line 1: a card number" + PAY])

    def test_bank_account_and_family_phone_in_a_master_point_to_their_home(self):
        head = self.repo.commit({"user/fields/chat.md": b"IL62 0108 0000 0009 9999 999\nPhone 050-123-4567\n"})
        self.assertEqual(self.run_check(head), ["user/fields/chat.md line 1: an IBAN" + HOME,
                                                "user/fields/chat.md line 2: a phone number" + HOME])

    def test_the_pr_body_names_no_number_at_all(self):
        head = self.repo.commit({"user/fields/chat.md": b"x\n"})
        self.assertEqual(self.run_check(head, "## Change\nпаспорт: обновлён\n"), [])
        self.assertEqual(self.run_check(head, "## Change\nphone 050-123-4567\ncard 4111 1111 1111 1111\n"),
                         ["PR body line 3: a card number" + PAY, "PR body line 2: a phone number" + HOME])

    def test_a_number_already_on_main_does_not_block_an_unrelated_edit(self):
        main = self.repo.commit({"user/routines/r.md": b"Card 4111 1111 1111 1111\nMorning.\n"})
        head = self.repo.commit({"user/routines/r.md": b"Card 4111 1111 1111 1111\nEvening.\n"})
        self.assertEqual(self.run_check(head, base=main), [])

    def test_the_engine_is_not_scanned_here(self):
        head = self.repo.commit({"engine/checks/tests/fixture.md": b"4111 1111 1111 1111\n"})
        self.assertEqual(self.run_check(head), [])

    def test_the_country_decides_which_document_numbers_count(self):
        us = VALID_CONFIG.replace(b'country = "IL"', b'country = "US"')
        main = self.repo.commit({"user/config.toml": us})
        head = self.repo.commit({"user/fields/chat.md": b"ref 123456782\nGB82 WEST 1234 5698 7654 32\n"})
        self.assertEqual(self.run_check(head, base=main), ["user/fields/chat.md line 2: an IBAN" + HOME])

    def test_a_pr_cannot_switch_the_country_to_pass_itself(self):
        us = VALID_CONFIG.replace(b'country = "IL"', b'country = "US"')
        head = self.repo.commit({"user/config.toml": us, "user/fields/chat.md": b"ID 123456782\n"})
        self.assertEqual(self.run_check(head), ["user/fields/chat.md line 1: an Israeli ID number" + HOME])

    def test_the_config_cannot_remove_the_payment_floor(self):
        cfg = VALID_CONFIG + b'\n[personal]\npatterns = []\n'
        head = self.repo.commit({"user/config.toml": cfg, "user/fields/chat.md": b"4111 1111 1111 1111\n"})
        self.assertEqual(self.run_check(head), ["user/fields/chat.md line 1: a card number" + PAY])

    def test_a_broken_config_still_scans_with_the_floor_and_the_common_patterns(self):
        for cfg in (VALID_CONFIG + b'\n[personal]\npatterns = 5\n', b'personal = 5\n' + VALID_CONFIG,
                    b'not toml [\n'):
            head = self.repo.commit({"user/config.toml": cfg,
                                     "user/fields/chat.md": b"4111 1111 1111 1111\n" + cfg[:3]})
            self.assertIn("user/fields/chat.md line 1: a card number" + PAY, self.run_check(head))


class EmailsInRoutines(unittest.TestCase):
    """A routine master held the trusted contact's e-mail and the gate did
    not see it. DOC-1 keeps addresses out of the routines; the bot field's part may hold his
    two. A machine cannot tell whose an address is, so any address under user/routines/ counts."""

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def lines(self, files, body=""):
        head = self.repo.commit(files)
        return [f.line() for f in numbers_check.check(self.repo.path, self.repo.base, head, body)]

    def test_an_address_in_a_routine_is_blocked_and_never_printed(self):
        lines = self.lines({"user/routines/invitations.md": b"Every hour: accept.\nOrganizer alex.trusted@example.com is known.\n"})
        self.assertEqual(len(lines), 1)
        self.assertIn("user/routines/invitations.md line 2: an e-mail address", lines[0])
        self.assertNotIn("example.com", lines[0])

    def test_an_address_in_the_bot_fields_part_and_a_mention_in_a_routine_stay(self):
        self.assertEqual(self.lines({"user/fields/bot.md": b"TRUSTED CONTACT: alex.trusted@example.com\n",
                                     "user/routines/mark.md": b"Post the mark; @owner reads it.\n"}), [])

    def test_what_is_and_is_not_an_address(self):
        for text in ("alex@mail.example.org.", "alex@пример.рф", "alex@example.comбыл", "noreply@calendar.google.com"):
            self.assertTrue(numbers_check.email_lines(text), text)
        for text in ("@owner", "me@home", "react@18.3.1", "9@10.30", "2@3.5", "a@b.c"):
            self.assertEqual(numbers_check.email_lines(text), [], text)

    def test_an_old_line_is_not_blamed_on_an_unrelated_edit_but_the_night_sees_it(self):
        main = self.repo.commit({"user/routines/mail.md": b"Ask alex.trusted@example.com\n"})
        head = self.repo.commit({"user/routines/mail.md": b"Ask alex.trusted@example.com\nThen stop.\n"})
        self.assertEqual(numbers_check.check(self.repo.path, main, head, ""), [])
        tree = [f.line() for f in numbers_check.check_tree(self.repo.path, head)]
        self.assertTrue(any("user/routines/mail.md line 1: an e-mail address" in l for l in tree), tree)


if __name__ == "__main__":
    unittest.main()

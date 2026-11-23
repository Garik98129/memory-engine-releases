"""C4's country table: each pattern finds its scheme's published valid examples and misses
the near misses; dates, times, prices, versions, order numbers, ISO weeks and year ranges trip
no country's patterns; BY_COUNTRY, PATTERNS and C3's KNOWN_PATTERNS name the same patterns;
a country with no row gets a note, not silence; a PR that adds a country's number is blocked,
naming the kind and the place, never the digits. Synthetic numbers only: the schemes' published
examples and made-up phones."""
import contextlib
import io
import random
import re
import unittest
from datetime import date, datetime, timedelta, timezone

from engine.checks import config_check, integrity, numbers_check, run
from engine.checks.tests.helpers import VALID_CONFIG, Repo, body

ALL = list(numbers_check.PATTERNS)
# The table's patterns but Israel's, which predate it and are kept as they are.
TABLE = [n for n in dict.fromkeys(n for names in numbers_check.BY_COUNTRY.values() for n in names)
         if n not in numbers_check.BY_COUNTRY["IL"]]
HOME = " — its home is the Documents base in Notion"

FOUND = {   # pattern → lines it finds: SPEC's valid examples in every written form SPEC allows
    "ru_inn": ("123456789047",),
    "ru_snils": ("112-233-445 95", "112-233-445-95"),
    "ru_passport": ("45 06 123456",),
    "ru_phone": ("8 912 345-67-89", "8 (912) 345-67-89", "8-912-345-67-89", "8 495 123-45-67", "8 812 123 45 67"),
    "ua_unzr": ("19900101-00015", "20000229-01234"),
    "ua_phone": ("050 123 45 67", "050-123-45-67", "(050) 123-45-67", "044 123 45 67", "(099) 123-45-67"),
    "us_ssn": ("536-90-4399",),
    "nanp_phone": ("(212) 555-1234", "212-555-1234", "212.555.1234", "1 212-555-1234", "1-212-555-1234"),
    "ca_sin": ("123-456-782", "123 456 782"),
    "de_idnr": ("36574261809", "11234567890", "36 574 261 809", "11123456786"),
    "de_phone": ("0151 12345678", "0171/1234567", "0160-1234567"),
    "gb_nino": ("AB 12 34 56 C", "AB123456C", "ab123456c"),
    "gb_nhs": ("943 476 5919", "943 476 5870", "943-476-5919"),
    "gb_phone": ("07700 900123", "07700 900 123"),
    "kr_rrn": ("900101-1234567", "000229-3234567", "991231-2234567"),
    "kr_phone": ("010-1234-5678", "010 1234 5678", "010-123-4567", "02-123-4567", "031-1234-5678"),
    "jp_mynumber": ("621498320257", "6214 9832 0257", "6214-9832-0257"),
    "jp_phone": ("090-1234-5678", "03-1234-5678", "045-123-4567"),
    "fr_nir": ("2 95 10 99 126 111 93", "2 53 07 2A 073 004 43", "2 53 07 2B 073 004 70",
               "295109912611193", "253072B07300470"),
    "fr_phone": ("06 12 34 56 78", "01.23.45.67.89", "01-23-45-67-89"),
    "br_cpf": ("390.533.447-05", "123.456.789-09", "390533447-05"),
    "br_phone": ("(11) 98765-4321", "(21) 2345-6789"),
}

_NINO_BAD = tuple(f"{a}{b}123456C" for a, b in [(x, "A") for x in "DFIQUV"] + [("A", x) for x in "DFIQUVO"]) + tuple(
    f"{p}123456C" for p in ("BG", "GB", "KN", "NK", "NT", "TN", "ZZ"))
MISSED = {  # pattern → near misses it must not find
    "ru_inn": ("123456789048", "123456789057",          # each check digit off by one
               "12345678904", "1234 5678 9047",          # eleven digits; grouped is not its form
               "0.123456789047", "123456789047,5"),      # a decimal
    "ru_snils": ("112-233-445 96", "11223344595", "112 233 445 95"),
    "ru_passport": ("4506 123456", "4506123456", "45-06-123456", "45 06 12345"),
    "ru_phone": ("89123456789", "8 712 345-67-89", "8 912 3456789", "8 912 345 6789", "8 012 345-67-89"),
    "ua_unzr": ("20010229-01234", "19901301-00015", "19900132-00015", "19900431-00015",   # no such day
                "1990010100015", "18990101-00015", "19900101-0001"),
    "ua_phone": ("0501234567", "020 123 45 67", "040 123 45 67", "042 123 45 67", "050 123-45-67",
                 "(050) 123 45 67"),
    "us_ssn": ("666-90-4399", "000-90-4399", "900-90-4399", "999-90-4399", "536-00-4399", "536-90-0000",
               "078-05-1120", "457-55-5462", "219-09-9999", "536904399", "536 90 4399"),
    "nanp_phone": ("2125551234", "(112) 555-1234", "(012) 555-1234", "212-155-1234", "212-055-1234",
                   "212-911-1234", "212-311-1234", "292-555-1234", "212 555 1234"),
    "ca_sin": ("123-456-783", "123456782", "123-456 782",
               "046 454 286", "823 456 785"),             # Luhn-valid, but a SIN never starts with 0 or 8
    "de_idnr": ("36574261800", "65929970480",
                "06574261895",                             # check digit right, a leading 0
                "12345678903",                             # check digit right, no digit repeated
                "11234567881",                             # check digit right, two digits repeated
                "11112345678",                             # check digit right, one digit four times
                "36 5742 61809", "3657 4261 809"),
    "de_phone": ("015112345678", "0150 12345678", "0151 123456", "0151 123456789", "0161 1234567"),
    "gb_nino": _NINO_BAD + ("AB123456E", "AB 123456 C", "AB12 34 56C", "A1234567C"),
    "gb_nhs": ("943 476 5910", "9434765919", "943 476-5919") + tuple(f"123 456 789{d}" for d in range(10)),
    "gb_phone": ("07700900123", "07600 900123", "0770 0900123", "07700 9001234"),
    "kr_rrn": ("901301-1234567", "900230-1234567", "900431-1234567", "900100-1234567", "900001-1234567",
               "9001011234567", "900101 1234567"),
    "kr_phone": ("01012345678", "012-1234-5678", "036-123-4567", "010-1234 5678", "02 123 4567", "070-1234-5678"),
    "jp_mynumber": ("621498320258", "6214 9832-0257", "62149832025", "6214 98320257"),
    "jp_phone": ("09012345678", "090 1234 5678", "03 1234 5678", "060-1234-5678"),
    "fr_nir": ("2 95 10 99 126 111 94", "295109912611194",
               "2 95 13 99 126 111 44", "2 95 19 99 126 111 43", "2 95 00 99 126 111 30",   # month 13, 19, 00
               "5 95 10 99 126 111 40",                   # sex digit 5
               "2 53 07 2C 073 004 43", "2 95 10 99 126 11193"),
    "fr_phone": ("0612345678", "06 12.34 56 78", "00 12 34 56 78", "06 12 34 56"),
    "br_cpf": ("390.533.447-06", "390.533.447-15", "111.111.111-11", "000.000.000-00", "999999999-99",
               "39053344705", "390.533.44705"),
    "br_phone": ("11987654321", "(11) 98765 4321", "11 98765-4321", "(11) 6123-4567", "(11) 1234-5678"),
}

# Lines that hold no personal number: no country's pattern may find one in them.
CLEAN = (
    "2026-11-11", "11.11.2026", "11/11/2026", "on 2026-11-11 at 12:30", "12:30", "12:30:45", "08:15-17:45",
    "1 299.00", "12,345.67", "1 234 567.89", "1,234,567.89", "v1.2.3", "2.10.15", "10.0.19045.3693",
    "#123456789", "№ 1234567890", "order 1234567890", "2026-W46", "2026-W46-3", "2026W46",
    "2025-2026", "2020–2026", "1990 - 2026", "2025/2026",
    "Meeting 11.11.2026 12:30-13:45, price 1 299.00 EUR, order #123456789, v1.2.3, week 2026-W46, 2025-2026",
)


def hits(line, names):
    return numbers_check.scan(line, list(names))


class EachPattern(unittest.TestCase):
    def test_every_valid_example_is_found_under_its_kind(self):
        for name, lines in FOUND.items():
            kind = numbers_check.PATTERNS[name][1]
            for line in lines:
                with self.subTest(name=name, line=line):
                    self.assertEqual(hits(line, [name]), [(1, kind)])
                    self.assertEqual(hits(f"see: {line}.", [name]), [(1, kind)])    # in a sentence

    def test_no_near_miss_is_found(self):
        for name, lines in MISSED.items():
            for line in lines:
                with self.subTest(name=name, line=line):
                    self.assertEqual(hits(line, [name]), [])

    def test_a_number_glued_to_a_word_or_inside_a_longer_row_or_a_decimal_is_not_one(self):
        for name, lines in FOUND.items():
            for line in lines:
                texts = [f"{line}x", f"-{line}", f"{line}-", f"+{line}", f"7 {line}", f"{line} 7",
                         f"7.{line}", f"{line}.7", f"7,{line}", f"{line},7"]
                if not line.startswith("("):            # a letter before a parenthesis does not glue
                    texts.append(f"x{line}")
                for text in texts:
                    with self.subTest(name=name, text=text):
                        self.assertEqual(hits(text, [name]), [])

    def test_every_check_digit_off_by_one_is_missed(self):
        # The last digit of each example with a check digit, plus one and minus one.
        for name in ("ru_inn", "ru_snils", "ca_sin", "de_idnr", "gb_nhs", "jp_mynumber", "fr_nir", "br_cpf"):
            for line in FOUND[name]:
                m = re.search(r"\d(?=\D*$)", line)
                for step in (1, 9):
                    bumped = line[:m.start()] + str((int(m.group()) + step) % 10) + line[m.end():]
                    with self.subTest(name=name, bumped=bumped):
                        self.assertEqual(hits(bumped, [name]), [])

    def test_every_table_pattern_has_examples_both_ways(self):
        self.assertEqual(set(FOUND), set(TABLE))
        self.assertEqual(set(MISSED), set(TABLE))


class NoFalsePositives(unittest.TestCase):
    def assertClean(self, line):
        for country, names in numbers_check.BY_COUNTRY.items():
            self.assertEqual(hits(line, names), [], (country, line))

    def test_dates_times_prices_versions_orders_weeks_and_year_ranges(self):
        for line in CLEAN:
            with self.subTest(line=line):
                self.assertClean(line)
                self.assertEqual(hits(line, ALL), [])

    def test_every_day_of_the_year_in_every_common_spelling(self):
        found, day = [], date(2026, 1, 1)
        while day.year == 2026:
            later = day + timedelta(days=13)
            for f in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%y"):
                for line in (day.strftime(f), f"{day:{f}} 12:30", f"{day:{f}} 23:59:59", f"{day:{f}} - {later:{f}}",
                             f"{day:{f}}-{later:{f}}", f"{day:{f}} {later:{f}}", f"{day:{f}}\u2013{later:{f}}"):
                    found += [line] if numbers_check.scan(line, TABLE) else []
            for line in (f"{day:%G-W%V}", f"{day:%G-W%V-%u}", f"{day:%GW%V}"):
                found += [line] if numbers_check.scan(line, TABLE) else []
            day += timedelta(days=1)
        self.assertEqual(found, [])

    def test_year_ranges_and_prices(self):
        found = []
        for year in range(1900, 2100):
            for later in (year + 1, year + 26):
                for sep in ("-", "\u2013", " - ", "/", " "):
                    found += [f"{year}{sep}{later}"] if numbers_check.scan(f"{year}{sep}{later}", TABLE) else []
        rnd = random.Random(2026)
        for digits in range(1, 13):                           # amounts from 0.01 to ten billion
            for _ in range(300):
                amount = f"{rnd.randrange(10 ** digits) / 100:,.2f}"
                for line in (amount, amount.replace(",", " "), amount.replace(",", "\u00a0"),
                             amount.replace(",", "_").replace(".", ",").replace("_", "."),
                             amount.replace(",", "_").replace(".", ",").replace("_", " ")):
                    found += [line] if numbers_check.scan(line, TABLE) else []
        self.assertEqual(found, [])


class Table(unittest.TestCase):
    def test_the_table_is_specs_eleven_countries(self):
        self.assertEqual(numbers_check.BY_COUNTRY, {
            "IL": ("il_id", "il_phone"),
            "RU": ("ru_inn", "ru_snils", "ru_passport", "ru_phone"),
            "UA": ("ua_unzr", "ua_phone"),
            "US": ("us_ssn", "nanp_phone"),
            "CA": ("ca_sin", "nanp_phone"),
            "DE": ("de_idnr", "de_phone"),
            "GB": ("gb_nino", "gb_nhs", "gb_phone"),
            "KR": ("kr_rrn", "kr_phone"),
            "JP": ("jp_mynumber", "jp_phone"),
            "FR": ("fr_nir", "fr_phone"),
            "BR": ("br_cpf", "br_phone"),
        })

    def test_c3_knows_exactly_the_patterns_c4_has(self):
        self.assertEqual(config_check.KNOWN_PATTERNS, frozenset(numbers_check.PATTERNS))
        for country, names in numbers_check.BY_COUNTRY.items():
            self.assertLessEqual(set(names), config_check.KNOWN_PATTERNS, country)
            self.assertLessEqual(set(names), set(numbers_check.PATTERNS), country)

    def test_each_countrys_patterns_are_on_for_it_and_find_its_numbers(self):
        repo = Repo()
        self.addCleanup(repo.close)
        for country, names in numbers_check.BY_COUNTRY.items():
            if country == "IL":
                continue
            head = repo.commit({"user/config.toml": VALID_CONFIG.replace(b'country = "IL"',
                                                                           f'country = "{country}"'.encode())})
            payment, document = numbers_check.active_patterns(repo.path, head, head)
            self.assertEqual(payment, ["card"])
            self.assertEqual(document, ["iban", "phone_intl", *names], country)
            for name in names:
                for line in FOUND[name]:
                    with self.subTest(country=country, line=line):
                        self.assertIn((1, numbers_check.PATTERNS[name][1]), numbers_check.scan(line, document))
            self.assertEqual(numbers_check.scan("ID 123456782", document), [], country)   # Israel's stays off


class Notes(unittest.TestCase):
    """A country with no row in the table is not a silent green: C4 notes that only the
    universal layer applies, and the note does not block."""

    NOTE = ("C4 NOTE no national document patterns for person.country {}: cards, IBAN and "
            "international phone numbers are still checked")

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def config(self, country: bytes) -> bytes:
        return VALID_CONFIG.replace(b'country = "IL"', b'country = "' + country + b'"')

    def run_check(self, base, head, text=""):
        notes = []
        findings = numbers_check.check(self.repo.path, base, head, text, notes)
        return [f.message for f in findings], notes

    def test_a_country_with_no_row_is_noted_and_the_universal_layer_still_blocks(self):
        main = self.repo.commit({"user/config.toml": self.config(b"NL")})
        head = self.repo.commit({"user/fields/chat.md": b"Who: a test person.\nNL91 ABNA 0417 1643 00\n"
                                                        b"card 4111 1111 1111 1111\n+31 20 123 4567\nID 123456782\n"})
        messages, notes = self.run_check(main, head)
        self.assertEqual(notes, [self.NOTE.format("NL")])
        self.assertEqual(messages, ["user/fields/chat.md line 3: a card number — payment data never goes into git",
                                    "user/fields/chat.md line 2: an IBAN" + HOME,
                                    "user/fields/chat.md line 4: a phone number" + HOME])
        self.assertEqual(self.run_check(main, main)[1], [self.NOTE.format("NL")])   # a PR with nothing in user/

    def test_main_and_the_pr_are_each_noted_once(self):
        main = self.repo.commit({"user/config.toml": self.config(b"NL")})
        self.assertEqual(self.run_check(main, self.repo.commit({"user/config.toml": self.config(b"SE")}))[1],
                         [self.NOTE.format("NL, SE")])
        self.repo.checkout(main)
        head = self.repo.commit({"user/config.toml": VALID_CONFIG, "user/fields/chat.md": b"ID 123456782\n"})
        messages, notes = self.run_check(main, head)
        self.assertEqual(notes, [self.NOTE.format("NL")])          # main's country has no row; the PR's has
        self.assertEqual(messages, ["user/fields/chat.md line 1: an Israeli ID number" + HOME])

    def test_a_country_with_a_row_or_no_valid_code_gets_no_note(self):
        self.assertEqual(self.run_check(self.repo.base, self.repo.base), ([], []))
        for country in (b"RU", b"BR"):
            head = self.repo.commit({"user/config.toml": self.config(country)})
            self.assertEqual(self.run_check(head, head)[1], [], country)
        for bad in (b"Israel", b"nl", b""):                         # C3's finding, not C4's note
            head = self.repo.commit({"user/config.toml": self.config(bad)})
            self.assertEqual(self.run_check(head, head)[1], [], bad)

    def test_the_old_call_without_notes_is_unchanged(self):
        main = self.repo.commit({"user/config.toml": self.config(b"NL")})
        head = self.repo.commit({"user/fields/chat.md": b"card 4111 1111 1111 1111\n"})
        self.assertEqual([f.message for f in numbers_check.check(self.repo.path, main, head, "")],
                         ["user/fields/chat.md line 1: a card number — payment data never goes into git"])

    def test_the_note_reaches_the_gates_output_and_does_not_block(self):
        main = self.repo.commit({"user/config.toml": self.config(b"NL")})
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run.main(["--repo", str(self.repo.path), "--base", main, "--head", head, "--kind", "person",
                             "--body", str(self.repo.body_file(body("user/fields/chat.md"))),
                             "--manifest", str(self.repo.manifest_file(main))],
                            now=datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(code, 0, out.getvalue())
        lines = out.getvalue().splitlines()
        self.assertIn("C4 OK", lines)
        self.assertIn(self.NOTE.format("NL"), lines)
        self.assertIn("SUMMARY block=0 ran=C1,C2,C3,C4,C5,C6,C7", lines)


class Nightly(unittest.TestCase):
    """N1 reads C4 over the whole tree of main every night: a country with no row is said
    there too, not only in a PR."""

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def tree(self, country: str) -> tuple[list[str], list[str]]:
        main = self.repo.commit({"user/config.toml": VALID_CONFIG.replace(
            b'country = "IL"', f'country = "{country}"'.encode())})
        alarms, notes = integrity.tree(self.repo.path, main, self.repo.manifest(main))
        return [a.line() for a in alarms], notes

    def test_a_country_with_no_row_is_a_note_of_the_night_not_a_silent_ok(self):
        self.assertEqual(self.tree("NL"), ([], ["N1 NOTE C4 " + numbers_check.NO_TABLE.format("NL")]))

    def test_a_country_with_a_row_adds_no_note(self):
        self.assertEqual(self.tree("DE"), ([], []))


class Scenario(unittest.TestCase):
    """Through C4's entry point, as the gate calls it: a PR that adds a country's number
    under user/ or in its body is blocked; the finding names the kind and the place."""

    NUMBERS = {   # country → (a document number, a local phone)
        "RU": ("123456789047", "8 (912) 345-67-89"),
        "UA": ("20000229-01234", "(050) 123-45-67"),
        "US": ("536-90-4399", "(212) 555-1234"),
        "CA": ("123 456 782", "1-212-555-1234"),
        "DE": ("36 574 261 809", "0171/1234567"),
        "GB": ("AB 12 34 56 C", "07700 900 123"),
        "KR": ("900101-1234567", "010-1234-5678"),
        "JP": ("6214 9832 0257", "090-1234-5678"),
        "FR": ("2 53 07 2A 073 004 43", "06 12 34 56 78"),
        "BR": ("390.533.447-05", "(11) 98765-4321"),
    }

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def assertNoDigits(self, number, line):
        for part in re.findall(r"\d{2,}", number):
            self.assertNotIn(part, line)

    def test_a_countrys_number_in_a_master_or_the_body_is_blocked_by_kind_and_place(self):
        self.assertEqual(set(self.NUMBERS), set(numbers_check.BY_COUNTRY) - {"IL"})
        for country, (document, phone) in self.NUMBERS.items():
            with self.subTest(country=country):
                self.repo.checkout(self.repo.base)
                main = self.repo.commit({"user/config.toml": VALID_CONFIG.replace(
                    b'country = "IL"', f'country = "{country}"'.encode())})
                head = self.repo.commit({"user/fields/chat.md":
                                         f"Who: a test person.\nnumber {document}\nphone {phone}\n".encode()})
                kind = numbers_check.PATTERNS[numbers_check.BY_COUNTRY[country][0]][1]
                findings = numbers_check.check(self.repo.path, main, head, f"## Change\nnumber {document}\n")
                self.assertEqual([f.message for f in findings], [
                    f"user/fields/chat.md line 2: {kind}" + HOME,
                    "user/fields/chat.md line 3: a phone number" + HOME,
                    f"PR body line 2: {kind}" + HOME])
                for f in findings:
                    self.assertTrue(f.line().startswith("C4 BLOCK "), f.line())
                    self.assertNoDigits(document, f.line())
                    self.assertNoDigits(phone, f.line())

    def test_a_pr_that_moves_the_country_is_read_with_the_new_countrys_patterns_at_once(self):
        main = self.repo.commit({"user/config.toml": VALID_CONFIG})                     # IL on main
        head = self.repo.commit({"user/config.toml": VALID_CONFIG.replace(b'country = "IL"', b'country = "DE"'),
                                 "user/fields/chat.md": b"Who: a test person.\nnumber 36 574 261 809\n"})
        kind = numbers_check.PATTERNS["de_idnr"][1]
        self.assertEqual([f.message for f in numbers_check.check(self.repo.path, main, head, "")],
                         [f"user/fields/chat.md line 2: {kind}" + HOME])

    def test_another_countrys_number_is_not_this_ones(self):
        main = self.repo.commit({"user/config.toml": VALID_CONFIG.replace(b'country = "IL"', b'country = "DE"')})
        head = self.repo.commit({"user/fields/chat.md": b"SSN 536-90-4399\nCPF 390.533.447-05\nNINO AB123456C\n"})
        self.assertEqual(numbers_check.check(self.repo.path, main, head, ""), [])


if __name__ == "__main__":
    unittest.main()

"""C4 personal-numbers: two kinds of numbers, two rules (docs/DESIGN.md §6, §9).

- Payment data — card numbers — never goes into git: blocked in the lines a PR
  adds under user/ and in the PR body. The nightly copy cuts it out of Notion (N6).
- Document numbers, bank accounts and phones — the person's and her family's —
  live in one home, the Documents base in Notion, and reach git through the
  nightly copy. In the lines a PR adds under user/ and in the
  PR body — which becomes the merge commit's message — they are blocked with a
  pointer to that home: a renewed passport is changed once, no field needs
  reinstalling, and the bot writes «updated», not the number.

Which document numbers and phone formats are recognised follows person.country —
main's and the PR's together, so a PR cannot loosen C4 for itself; cards, IBAN
and international phone numbers are recognised everywhere. BY_COUNTRY has IL, RU,
UA, US, CA, DE, GB, KR, JP, FR and BR; any other country gets that universal layer
only, and C4's output says so in a NOTE. A number with a check digit counts only when
it passes; one without counts only in its written form, separators included. The
engine is not scanned here: C1 holds it to the release, the factory checks it (F7).

Under user/routines/ an e-mail address is a finding too (a routine
master once held the trusted contact's, and the gate did not see it). DOC-1 keeps hers, her
family's and the trusted contact's out of the routines — his two live in the bot field's
part; a machine cannot tell whose an address is, so any address under user/routines/ is
held, and a routine names a sender by its name.

A finding names the place and the kind of number, never the digits: CI logs are
read by more people than the person.
"""
from __future__ import annotations

import re
import tomllib
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Callable

from . import gitio
from .report import Finding

USER_PREFIX = "user/"
ROUTINES_PREFIX = "user/routines/"
# An address: a domain with a dot and a last part of letters, any script; a GitHub @mention
# («@owner»), «me@home» and version or size tags («react@18.3.1», «9@10.30») are not one.
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@(?:[^\W_]+(?:-[^\W_]+)*\.)+[^\W\d_]{2,}(?![\w-])")
EMAIL_WHY = ("an e-mail address — the routines hold none (DOC-1): the trusted contact's two are in the "
             "bot field's part; name a sender by its name")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def _il_id_ok(digits: str) -> bool:
    # Israeli ID: weights 1,2,1,2,… from the left; a product above 9 adds its digits.
    total = 0
    for i, ch in enumerate(digits):
        d = int(ch) * (1 if i % 2 == 0 else 2)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def _iban_ok(text: str) -> bool:
    s = text[4:] + text[:4]
    return int("".join(str(int(c, 36)) for c in s)) % 97 == 1


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


# A number counts only when it stands alone: digits glued to letters are part of a
# hash or a code (user/fields/installed.toml holds sha256 values).
IL_ID_RE = re.compile(r"(?<![\w-])(?:\d{9}|\d{8}-\d)(?![\w-])")
# A card number: 13–19 digits passing Luhn. Never right after a digit and a decimal point
# or comma, or the fraction of a long price would be one. Two forms:
# - free: any grouping, a single plain space or a single dash between digits — the whole
#   run is the number;
# - as banks print it: a first group of four most payment cards start with (2200–2799,
#   3000–6999, and UnionPay's 8100–8171 since 2019), then groups of one to six digits, split
#   by one or two spaces — a plain, a no-break (U+00A0) or a narrow no-break one (U+202F) —
#   or by a dash with at most one such space on each side, the same separator throughout;
#   not after a group with that separator, and a run of more than 19 digits is a row of
#   numbers, not a card. The number is the longest run of whole groups, 13–19 digits, that
#   passes Luhn: so a 17- or 18-digit card is cut whole even when its first 16 digits pass
#   too, and a card followed by its expiry or CVV is still found. A dot or an underscore
#   between groups is not a separator.
_SP = "[ \u00a0\u202f]"
_SEP = rf"({_SP}{{1,2}}|{_SP}?-{_SP}?)"
_START = r"(?<![\w-])(?<!\d\.)(?<!\d,)"
_NOT_AFTER_GROUP = "".join(rf"(?<!\d{s})" for s in (_SP, _SP * 2, "-", _SP + "-", "-" + _SP, _SP + "-" + _SP))
_FIRST = r"(?:2[2-7]\d\d|[3-6]\d{3}|81(?:[0-6]\d|7[01]))"
CARD_FREE = re.compile(_START + r"(?:\d[ -]?){12,18}\d(?![\w-])")
CARD_GROUPED = re.compile(_START + _NOT_AFTER_GROUP + rf"{_FIRST}{_SEP}\d{{1,6}}(?:\1\d{{1,6}})*(?![\w-])(?!\1\d)")


def _card_free(m: re.Match) -> int:
    """How much of a free-form match is a card: all of it or nothing."""
    return len(m.group()) if 13 <= len(d := _digits(m.group())) <= 19 and _luhn(d) else 0


def _card_grouped(m: re.Match) -> int:
    """How much of a grouped match is a card: up to the end of the longest run of whole groups,
    13–19 digits, that passes Luhn; nothing for a run of more than 19 digits."""
    text = m.group()
    ends = [g.end() for g in re.finditer(r"\d+", text)]
    if len(_digits(text)) > 19:
        return 0
    best = 0
    for end in ends:
        d = _digits(text[:end])
        if 13 <= len(d) <= 19 and _luhn(d):
            best = end
    return best


# The free form first, then the grouped one; each with how much of its match is a card.
CARD_RES: tuple[tuple[re.Pattern, Callable[[re.Match], int]], ...] = (
    (CARD_FREE, _card_free),
    (CARD_GROUPED, _card_grouped),
)
IBAN_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}(?![A-Z0-9])")
PHONE_RES = (
    re.compile(r"(?<![\w+])(?:\+|00)[1-9]\d{0,2}(?:[ -]?\(?\d\)?){7,13}(?!\w)"),  # international
    re.compile(r"(?<![\w+])0(?:5\d|7\d|[2-489])(?:[ -]?\d){7}(?!\w)"),            # Israel, local
)


def _has_il_id(line: str) -> bool:
    return any(_il_id_ok(_digits(m.group())) for m in IL_ID_RE.finditer(line))


def _has_card(line: str) -> bool:
    return any(card(m) for regex, card in CARD_RES for m in regex.finditer(line))


def _has_iban(line: str) -> bool:
    return any(15 <= len(s := m.group().replace(" ", "")) <= 34 and _iban_ok(s)
               for m in IBAN_RE.finditer(line))


def _has_intl_phone(line: str) -> bool:
    return PHONE_RES[0].search(line) is not None


def _has_il_phone(line: str) -> bool:
    return PHONE_RES[1].search(line) is not None


# The rest of the country table (BY_COUNTRY). A form with no check digit counts only as it is
# written, with its separators; a bare run of digits counts only with its check digit. A number
# stands alone: not glued to a word, a plus or a dash, and not part of a longer row — not right
# after or before a digit with a space, a dot or a comma between (a row of amounts, a decimal).
_LONE = r"(?<![\w+-])(?<!\d[ .,])"
_DONE = r"(?![\w-])(?![ .,]\d)"
_MONTH_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)    # February: up to the 29th


def _weighted(digits: str, weights) -> int:
    return sum(w * int(d) for w, d in zip(weights, digits))


def _any(regex: re.Pattern, line: str, ok: Callable[[str], bool]) -> bool:
    return any(ok(m.group()) for m in regex.finditer(line))


RU_INN_RE = re.compile(_LONE + r"\d{12}" + _DONE)
RU_SNILS_RE = re.compile(_LONE + r"\d{3}-\d{3}-\d{3}[ -]\d\d" + _DONE)
RU_PASSPORT_RE = re.compile(_LONE + r"\d\d \d\d \d{6}" + _DONE)
UA_UNZR_RE = re.compile(_LONE + r"(?:19|20)\d{6}-\d{5}" + _DONE)
US_SSN_RE = re.compile(_LONE + r"\d{3}-\d\d-\d{4}" + _DONE)
CA_SIN_RE = re.compile(_LONE + r"\d{3}([ -])\d{3}\1\d{3}" + _DONE)
DE_IDNR_RE = re.compile(_LONE + r"(?:\d{11}|\d\d \d{3} \d{3} \d{3})" + _DONE)
GB_NINO_RE = re.compile(_LONE + r"(?:[A-Za-z]{2}\d{6}[A-Da-d]|[A-Za-z]{2} \d\d \d\d \d\d [A-Da-d])" + _DONE)
GB_NHS_RE = re.compile(_LONE + r"\d{3}([ -])\d{3}\1\d{4}" + _DONE)
KR_RRN_RE = re.compile(_LONE + r"\d{6}-\d{7}" + _DONE)
JP_MYNUMBER_RE = re.compile(_LONE + r"(?:\d{12}|\d{4}([ -])\d{4}\1\d{4})" + _DONE)
FR_NIR_RE = re.compile(_LONE + r"(?:[1-478]\d{4}(?:\d\d|2[AB])\d{8}"
                               r"|[1-478] \d\d \d\d (?:\d\d|2[AB]) \d{3} \d{3} \d\d)" + _DONE)
BR_CPF_RE = re.compile(_LONE + r"(?:\d{3}\.\d{3}\.\d{3}|\d{9})-\d\d" + _DONE)

_UA = r"(?:39|50|63|66|67|68|73|9[1-9]|3[1-8]|4[13-8]|5[1-7]|6[12459])"     # the code after the 0
_NANP = r"[2-9][0-8]\d", r"[2-9](?!11)\d\d"                                  # area code, exchange
RU_PHONE_RE = re.compile(_LONE + r"8(?:[ -][3489]\d\d|[ -]\([3489]\d\d\))[ -]\d{3}[ -]\d\d[ -]\d\d" + _DONE)
UA_PHONE_RE = re.compile(_LONE + rf"(?:0{_UA} \d{{3}} \d\d \d\d|0{_UA}-\d{{3}}-\d\d-\d\d"
                                 rf"|\(0{_UA}\) \d{{3}}-\d\d-\d\d)" + _DONE)
NANP_PHONE_RE = re.compile(_LONE + rf"(?:1[ -])?(?:\({_NANP[0]}\) {_NANP[1]}-\d{{4}}"
                                   rf"|{_NANP[0]}-{_NANP[1]}-\d{{4}}|{_NANP[0]}\.{_NANP[1]}\.\d{{4}})" + _DONE)
DE_PHONE_RE = re.compile(_LONE + r"01(?:5[1-9]|6[023]|7\d)[ /-]\d{7,8}" + _DONE)
GB_PHONE_RE = re.compile(_LONE + r"07[1-57-9]\d\d (?:\d{6}|\d{3} \d{3})" + _DONE)
KR_PHONE_RE = re.compile(_LONE + r"(?:01[016789]([ -])\d{3,4}\1\d{4}|(?:02|0[3-6][1-5])-\d{3,4}-\d{4})" + _DONE)
JP_PHONE_RE = re.compile(_LONE + r"(?:0[5789]0-\d{4}-\d{4}|0\d-\d{4}-\d{4}|0\d\d-\d{3}-\d{4})" + _DONE)
FR_PHONE_RE = re.compile(_LONE + r"0[1-9]([ .-])\d\d(?:\1\d\d){3}" + _DONE)
BR_PHONE_RE = re.compile(_LONE + r"\(\d\d\) (?:9\d{4}|[2-5]\d{3})-\d{4}" + _DONE)


def _ru_inn_ok(text: str) -> bool:
    d = _digits(text)
    n11 = _weighted(d, (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) % 11 % 10
    n12 = _weighted(d, (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) % 11 % 10
    return d[10:] == f"{n11}{n12}"


def _ru_snils_ok(text: str) -> bool:
    d = _digits(text)
    s = _weighted(d, range(9, 0, -1))
    control = s if s < 100 else 0 if s in (100, 101) else s % 101
    return int(d[9:]) == (0 if control in (100, 101) else control)


def _ua_unzr_ok(text: str) -> bool:
    try:
        date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return False
    return True


def _us_ssn_ok(text: str) -> bool:
    d = _digits(text)
    area = int(d[:3])
    return (area not in (0, 666) and area < 900 and d[3:5] != "00" and d[5:] != "0000"
            and d not in ("078051120", "457555462", "219099999"))


def _ca_sin_ok(text: str) -> bool:
    d = _digits(text)
    return d[0] not in "08" and _luhn(d)


def _de_idnr_ok(text: str) -> bool:
    d = _digits(text)
    repeated = [n for n in Counter(d[:10]).values() if n > 1]
    if d[0] == "0" or repeated not in ([2], [3]):     # one digit twice or three times, the rest once
        return False
    p = 10
    for ch in d[:10]:                       # ISO 7064 MOD 11,10
        s = (int(ch) + p) % 10 or 10
        p = 2 * s % 11
    return (11 - p) % 10 == int(d[10])


def _gb_nino_ok(text: str) -> bool:
    prefix = text[:2].upper()
    return (not set(prefix) & set("DFIQUV") and prefix[1] != "O"
            and prefix not in ("BG", "GB", "KN", "NK", "NT", "TN", "ZZ"))


def _gb_nhs_ok(text: str) -> bool:
    d = _digits(text)
    check = 11 - _weighted(d, range(10, 1, -1)) % 11
    return check != 10 and check % 11 == int(d[9])


def _kr_rrn_ok(text: str) -> bool:
    month, day = int(text[2:4]), int(text[4:6])
    return 1 <= month <= 12 and 1 <= day <= _MONTH_DAYS[month - 1]


def _jp_mynumber_ok(text: str) -> bool:
    d = _digits(text)
    r = _weighted(d, (6, 5, 4, 3, 2, 7, 6, 5, 4, 3, 2)) % 11
    return int(d[11]) == (0 if r <= 1 else 11 - r)


def _fr_nir_ok(text: str) -> bool:
    s = text.replace(" ", "")
    month = int(s[3:5])
    if not (1 <= month <= 12 or 20 <= month <= 99):
        return False
    number = int(s[:5] + {"2A": "19", "2B": "18"}.get(s[5:7], s[5:7]) + s[7:13])
    return int(s[13:]) == 97 - number % 97


def _br_cpf_ok(text: str) -> bool:
    d = _digits(text)
    if len(set(d)) == 1:                    # 111.111.111-11 passes the arithmetic
        return False
    for n in (9, 10):
        r = _weighted(d, range(n + 1, 1, -1)) % 11
        if int(d[n]) != (0 if r < 2 else 11 - r):
            return False
    return True


# The national patterns of BY_COUNTRY: the regex, the test of its check digit (None for a form
# with no check digit, which counts as written) and the kind a finding names.
NATIONAL: dict[str, tuple[re.Pattern, Callable[[str], bool] | None, str]] = {
    "ru_inn": (RU_INN_RE, _ru_inn_ok, "a Russian INN"),
    "ru_snils": (RU_SNILS_RE, _ru_snils_ok, "a Russian SNILS number"),
    "ru_passport": (RU_PASSPORT_RE, None, "a Russian passport number"),
    "ru_phone": (RU_PHONE_RE, None, "a phone number"),
    "ua_unzr": (UA_UNZR_RE, _ua_unzr_ok, "a Ukrainian UNZR number"),
    "ua_phone": (UA_PHONE_RE, None, "a phone number"),
    "us_ssn": (US_SSN_RE, _us_ssn_ok, "a US Social Security number"),
    "nanp_phone": (NANP_PHONE_RE, None, "a phone number"),
    "ca_sin": (CA_SIN_RE, _ca_sin_ok, "a Canadian Social Insurance Number"),
    "de_idnr": (DE_IDNR_RE, _de_idnr_ok, "a German tax ID"),
    "de_phone": (DE_PHONE_RE, None, "a phone number"),
    "gb_nino": (GB_NINO_RE, _gb_nino_ok, "a UK National Insurance number"),
    "gb_nhs": (GB_NHS_RE, _gb_nhs_ok, "an NHS number"),
    "gb_phone": (GB_PHONE_RE, None, "a phone number"),
    "kr_rrn": (KR_RRN_RE, _kr_rrn_ok, "a Korean resident registration number"),
    "kr_phone": (KR_PHONE_RE, None, "a phone number"),
    "jp_mynumber": (JP_MYNUMBER_RE, _jp_mynumber_ok, "a Japanese My Number"),
    "jp_phone": (JP_PHONE_RE, None, "a phone number"),
    "fr_nir": (FR_NIR_RE, _fr_nir_ok, "a French social security number"),
    "fr_phone": (FR_PHONE_RE, None, "a phone number"),
    "br_cpf": (BR_CPF_RE, _br_cpf_ok, "a Brazilian CPF"),
    "br_phone": (BR_PHONE_RE, None, "a phone number"),
}


def _compact(text: str) -> str:
    """A number without its separators, letters upper-cased: how a test value is listed."""
    return re.sub(r"[ ()./-]", "", text).upper()


def national_values(line: str) -> list[tuple[str, str]]:
    """(kind, compact value) of every national number in a line — for the release check."""
    return [(kind, _compact(m.group())) for regex, ok, kind in NATIONAL.values()
            for m in regex.finditer(line) if ok is None or ok(m.group())]


def _national(regex: re.Pattern, ok: Callable[[str], bool] | None) -> Callable[[str], bool]:
    return lambda line: any(ok is None or ok(m.group()) for m in regex.finditer(line))


# Payment kinds the copy can cut out of a text (N6): the regexes in order, each with how
# much of its match is the number, and the marker that replaces it.
REDACTORS: dict[str, tuple[tuple[tuple[re.Pattern, Callable[[re.Match], int]], ...], str]] = {
    "card": (CARD_RES, "[card number removed]"),
}


def redact(text: str, patterns: list[str]) -> tuple[str, int]:
    """The text with every number of these kinds replaced by its marker, and how many
    were replaced. A kind without a redactor is loud: the floor names it, so a silent
    pass would be a hole (P5)."""
    count = 0
    for name in patterns:
        if name not in REDACTORS:
            raise ValueError(f"no redactor for the payment pattern {name!r}")
        regexes, marker = REDACTORS[name]
        for regex, card in regexes:

            def swap(m: re.Match, card=card) -> str:
                nonlocal count
                cut = card(m)
                if not cut:
                    return m.group()
                count += 1
                return marker + m.group()[cut:]
            text = regex.sub(swap, text)
    return text, count


PATTERNS: dict[str, tuple[Callable[[str], bool], str]] = {
    "card": (_has_card, "a card number"),
    "iban": (_has_iban, "an IBAN"),
    "phone_intl": (_has_intl_phone, "a phone number"),
    "il_id": (_has_il_id, "an Israeli ID number"),
    "il_phone": (_has_il_phone, "a phone number"),
    **{name: (_national(regex, ok), kind) for name, (regex, ok, kind) in NATIONAL.items()},
}
EVERYWHERE = ("iban", "phone_intl")
BY_COUNTRY = {
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
}
# A country with no row: the universal layer only, and C4 says so (P5: never a silent green).
NO_TABLE = ("no national document patterns for person.country {}: cards, IBAN and "
            "international phone numbers are still checked")
NO_TABLE_NOTE = "C4 NOTE " + NO_TABLE


def scan(text: str, patterns: list[str]) -> list[tuple[int, str]]:
    """(line number, kind) for each line that holds a number of a kind in patterns."""
    hits: list[tuple[int, str]] = []
    for no, line in enumerate(text.splitlines(), start=1):
        for name in patterns:
            found, kind = PATTERNS[name]
            if found(line):
                hits.append((no, kind))
    return hits


def _toml(repo: Path, rev: str, path: str) -> dict:
    raw = gitio.file_at(repo, rev, path)
    if raw is None:
        return {}
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}               # C3 reports a broken config; the floor still applies


def _names(value: object) -> list[str]:
    return [n for n in value if isinstance(n, str)] if isinstance(value, list) else []


def _country(config: dict) -> object:
    person = config.get("person", {})
    return person.get("country") if isinstance(person, dict) else None


def untabled_countries(repo: Path, base: str, head: str) -> list[str]:
    """main's and the PR's person.country, each once, when it is a two-letter code with no row
    in BY_COUNTRY; anything that is no such code is C3's finding."""
    countries = [_country(_toml(repo, rev, "user/config.toml")) for rev in (base, head)]
    return [c for c in dict.fromkeys(countries)
            if isinstance(c, str) and re.fullmatch(r"[A-Z]{2}", c) and c not in BY_COUNTRY]


def active_patterns(repo: Path, base: str, head: str) -> tuple[list[str], list[str]]:
    """(payment patterns, document patterns) for this repository."""
    floors = _toml(repo, base, "engine/defaults.toml").get("floors", {})
    payment = _names(floors.get("payment_patterns_min"))
    if not payment or any(n not in PATTERNS for n in payment):
        raise ValueError("floor payment_patterns_min is missing or names unknown patterns")
    document = list(EVERYWHERE)
    for rev in (base, head):      # main's config and the PR's: a PR cannot loosen C4 for itself
        config = _toml(repo, rev, "user/config.toml")
        personal = config.get("personal", {})
        document += BY_COUNTRY.get(_country(config), ())
        document += _names(personal.get("patterns")) if isinstance(personal, dict) else []
    document = [n for n in dict.fromkeys(document) if n in PATTERNS and n not in payment]
    return payment, document      # C3 reports unknown names in the config


def email_lines(text: str) -> list[int]:
    """Line numbers that hold an e-mail address."""
    return [no for no, line in enumerate(text.splitlines(), start=1) if EMAIL_RE.search(line)]


def check(repo: Path, base: str, head: str, body: str, notes: list[str] | None = None) -> list[Finding]:
    """The findings; `notes`, when given, gets C4's NOTE lines, which do not block."""
    payment, document = active_patterns(repo, base, head)
    if notes is not None and (countries := untabled_countries(repo, base, head)):
        notes.append(NO_TABLE_NOTE.format(", ".join(countries)))
    findings: list[Finding] = []
    for path, old_raw, new_raw in gitio.changed_files(repo, base, head, USER_PREFIX):
        try:
            new = new_raw.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Finding("C4", f"{path} cannot be scanned: not valid UTF-8"))
            continue
        old = old_raw.decode("utf-8", "replace") if old_raw is not None else None
        fresh = gitio.added_lines(old, new)
        for no, kind in scan(new, payment):
            if no in fresh:
                findings.append(Finding("C4", f"{path} line {no}: {kind} — payment data never goes into git"))
        for no, kind in scan(new, document):
            if no in fresh:
                findings.append(Finding("C4", f"{path} line {no}: {kind} — its home is the Documents base in Notion"))
        if path.startswith(ROUTINES_PREFIX):
            findings += [Finding("C4", f"{path} line {no}: {EMAIL_WHY}") for no in email_lines(new) if no in fresh]
    for no, kind in scan(body, payment):
        findings.append(Finding("C4", f"PR body line {no}: {kind} — payment data never goes into git"))
    for no, kind in scan(body, document):
        findings.append(Finding("C4", f"PR body line {no}: {kind} — its home is the Documents base in Notion"))
    return findings


def check_tree(repo: Path, rev: str, notes: list[str] | None = None) -> list[Finding]:
    """C4 over every line of every file under user/ at rev — for N1 on main, which a
    direct push may have reached without a PR. `notes`, when given, gets what C4 cannot
    see — a country with no row — without a prefix: the caller names its check."""
    payment, document = active_patterns(repo, rev, rev)
    if notes is not None and (countries := untabled_countries(repo, rev, rev)):
        notes.append(NO_TABLE.format(", ".join(countries)))
    findings: list[Finding] = []
    for path, raw in gitio.files_under(repo, rev, USER_PREFIX):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Finding("C4", f"{path} cannot be scanned: not valid UTF-8"))
            continue
        for no, kind in scan(text, payment):
            findings.append(Finding("C4", f"{path} line {no}: {kind} — payment data never goes into git"))
        for no, kind in scan(text, document):
            findings.append(Finding("C4", f"{path} line {no}: {kind} — its home is the Documents base in Notion"))
        if path.startswith(ROUTINES_PREFIX):
            findings += [Finding("C4", f"{path} line {no}: {EMAIL_WHY}") for no in email_lines(text)]
    return findings

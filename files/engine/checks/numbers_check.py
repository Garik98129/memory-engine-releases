"""C4 personal-numbers: two kinds of numbers, two rules (docs/DESIGN.md §6, §9).

- Payment data — card numbers — never goes into git: blocked in the lines a PR
  adds under user/ and in the PR body. The nightly copy cuts it out of Notion (N6).
- Document numbers, bank accounts and phones — the person's and her family's —
  live in one home, the Documents base in Notion, and reach git through the
  nightly copy (owner, 23.10.2026). In the lines a PR adds under user/ and in the
  PR body — which becomes the merge commit's message — they are blocked with a
  pointer to that home: a renewed passport is changed once, no field needs
  reinstalling, and the bot writes «updated», not the number.

Which document numbers and phone formats are recognised follows person.country —
main's and the PR's together, so a PR cannot loosen C4 for itself; cards, IBAN
and international phone numbers are recognised everywhere. The
engine is not scanned here: C1 holds it to the release, the factory checks it (F7).

Under user/routines/ an e-mail address is a finding too (Issue #18, item 35: a routine
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
# or comma, or the fraction of a long price would be one. Two forms (Issue #18, item 44):
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
}
EVERYWHERE = ("iban", "phone_intl")
BY_COUNTRY = {"IL": ("il_id", "il_phone")}


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


def active_patterns(repo: Path, base: str, head: str) -> tuple[list[str], list[str]]:
    """(payment patterns, document patterns) for this repository."""
    floors = _toml(repo, base, "engine/defaults.toml").get("floors", {})
    payment = _names(floors.get("payment_patterns_min"))
    if not payment or any(n not in PATTERNS for n in payment):
        raise ValueError("floor payment_patterns_min is missing or names unknown patterns")
    document = list(EVERYWHERE)
    for rev in (base, head):      # main's config and the PR's: a PR cannot loosen C4 for itself
        config = _toml(repo, rev, "user/config.toml")
        person, personal = config.get("person", {}), config.get("personal", {})
        country = person.get("country") if isinstance(person, dict) else None
        document += BY_COUNTRY.get(country, ())
        document += _names(personal.get("patterns")) if isinstance(personal, dict) else []
    document = [n for n in dict.fromkeys(document) if n in PATTERNS and n not in payment]
    return payment, document      # C3 reports unknown names in the config


def email_lines(text: str) -> list[int]:
    """Line numbers that hold an e-mail address."""
    return [no for no, line in enumerate(text.splitlines(), start=1) if EMAIL_RE.search(line)]


def check(repo: Path, base: str, head: str, body: str) -> list[Finding]:
    payment, document = active_patterns(repo, base, head)
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


def check_tree(repo: Path, rev: str) -> list[Finding]:
    """C4 over every line of every file under user/ at rev — for N1 on main, which a
    direct push may have reached without a PR."""
    payment, document = active_patterns(repo, rev, rev)
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

"""C3 config: the person's config parses, has known keys, and loosens no floor.

The object checked is `user/config.toml` in the merge result (`head` here; see
run.py). The floors come from `engine/defaults.toml` on main for a person's PR,
and from the merge result for an update PR (that engine is the release, verified
by C1).
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..backup.copy import REMOVED_RE, norm
from . import gitio, numbers_check
from .paths_check import PERSON
from .report import Finding

CONFIG = "user/config.toml"
DEFAULTS = "engine/defaults.toml"

KNOWN_PATTERNS = frozenset({"card", "iban", "phone_intl", "il_id", "il_phone"})
CHANNELS = frozenset({"canary", "stable"})
PROTECTION_MODES = frozenset({"required", "none"})   # none: a plan without branch protection (Free)
# A GitHub login: letters, digits and single hyphens inside, at most 39 characters.
LOGIN_RE = re.compile(r"(?!-)(?!.*--)[A-Za-z0-9-]{1,39}(?<!-)")
LANGUAGE_RE = re.compile(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*")   # BCP 47, e.g. ru, he, pt-BR
COUNTRY_RE = re.compile(r"[A-Z]{2}")                         # ISO 3166-1 alpha-2

class Strings(list):
    """A list whose reader keeps only strings: anything else in it is a type error, or it
    would vanish unseen — a number in `notion.backup_exclude` excluded nothing (Kimi, review
    of PR #23). The other lists name each wrong item in their own checks below."""


# section → key → (type, required)
SCHEMA: dict[str, dict[str, tuple[type, bool]]] = {
    "person": {
        "language": (str, True),
        "work_languages": (list, False),
        "country": (str, True),
        "timezone": (str, True),
        "github_login": (str, False),    # the account the bot acts under — N4 reads its marks
    },
    "platform": {
        "name": (str, True),
        "chat_field_limit": (int, True),
        "bot_field_limit": (int, True),
        "routine_limit": (int, False),
        "read_budget_bytes": (int, True),
    },
    "engine": {"channel": (str, True)},
    "notion": {"backup_exclude": (Strings, False), "shrink_alarm": (float, False),
               "removed_bases": (Strings, False)},     # bases she removed: N3 stops holding them (her yes, C7)
    "alarm": {"trusted_contact": (str, False), "marks_issue": (int, False)},
    "github": {"protection": (str, False)},
    "review": {"second_reader": (str, False)},
    "personal": {"patterns": (list, False)},
}

# Keys whose change can silence the person's protection: alarms, where the bot's daily
# marks go and whose marks count (N4), the update channel, the optional second reader, and
# whether N1 expects branch protection. They change only in
# a push of the trusted contact's own that itself brings the change: the gate passes the
# push's sender as `actor` and the head before the push as `before` (both empty when the
# run cannot know them), and the keys at `before` must still equal main's — a change the
# bot slipped into the branch earlier stays red whatever the trusted contact pushes after
# it. So such a PR is one push and nothing more: a later push, or a check started again
# by job `merge` after main moved (a dispatch trusts nobody), turns it red for good, and
# the trusted contact closes it and opens a new one. With no trusted contact on main,
# only installation sets them.
PROTECTED = (("alarm", "trusted_contact"), ("alarm", "marks_issue"), ("person", "github_login"),
             ("engine", "channel"), ("review", "second_reader"), ("github", "protection"))
PROTECTED_DEFAULTS = {("github", "protection"): "required"}   # an absent key means this

# Two lists any change of which either tightens or loosens: C4's document patterns (the
# country's set and `personal.patterns` — the person may add, not remove) and the pages the
# Notion copy leaves out. Anyone may tighten them; a pattern that stops applying or a page
# newly left out passes only like a protected key — so the bot cannot loosen C4 in two PRs
# (a country first, then the number) or take a base out of the copy, whose alarm would
# fire once and fall silent the night after.

# config key → floor key: the value may not exceed the floor
MAXIMA = {
    ("platform", "chat_field_limit"): "chat_field_limit_max",
    ("platform", "bot_field_limit"): "bot_field_limit_max",
    ("platform", "routine_limit"): "routine_limit_max",
    ("notion", "shrink_alarm"): "backup_shrink_alarm_max",
}


def _type_ok(value: object, expected: type) -> bool:
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected is Strings:
        return isinstance(value, list) and all(isinstance(x, str) for x in value)
    return isinstance(value, expected)


def _type_name(expected: type) -> str:
    return "a list of strings" if expected is Strings else expected.__name__


def _parse(raw: bytes | None, name: str, check_id: str) -> tuple[dict | None, list[Finding]]:
    if raw is None:
        return None, [Finding(check_id, f"{name} is missing")]
    try:
        return tomllib.loads(raw.decode("utf-8")), []
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return None, [Finding(check_id, f"{name} does not parse: {exc}")]


def read(repo: Path, rev: str) -> dict:
    """The config at rev, or {} if it is missing or broken — for the nightly checks,
    whose tree part reports a broken config itself, and for the alarms, which must still
    reach someone when the config is broken."""
    parsed, _ = _parse(gitio.file_at(repo, rev, CONFIG), CONFIG, "C3")
    return parsed or {}


def read_for_copy(repo: Path, rev: str) -> dict:
    """The config at rev for the nightly copy, which must not run on a config it cannot read
    whole (Issue #18, item 46). `read` takes a missing or broken file for {} — for the copy
    that means «exclude nothing», and a mistyped key, a string instead of a list or a number in
    the list means the same — so it would copy what the person left out. Here they raise ValueError, «the
    settings are not usable: …»: a missing or broken file, an unknown section (a misspelled
    [notion] is one, so any is), an unknown key or a wrong type in [notion]. Other sections'
    keys and values are C3's on every PR and N1's every night, not the copy's. An exclusion
    spelled as a title, not an id, still excludes nothing: not checked here."""
    parsed, findings = _parse(gitio.file_at(repo, rev, CONFIG), CONFIG, "C3")
    if parsed is not None:
        findings += [Finding("C3", f"unknown section [{s}]") for s in parsed if s not in SCHEMA]
        notion = parsed.get("notion", {})
        if not isinstance(notion, dict):
            findings.append(Finding("C3", "[notion] is not a table"))
        else:
            findings += [Finding("C3", f"unknown key notion.{k}") for k in notion if k not in SCHEMA["notion"]]
            findings += [Finding("C3", f"notion.{k} must be {_type_name(expected)}")
                         for k, (expected, _) in SCHEMA["notion"].items() if k in notion and not _type_ok(notion[k], expected)]
    if findings:
        raise ValueError("the settings are not usable: " + "; ".join(f.message for f in findings))
    return parsed


def _value(config: dict | None, section: str, key: str) -> object:
    table = (config or {}).get(section, {})
    return table.get(key) if isinstance(table, dict) else None


def protected_value(config: dict | None, section: str, key: str) -> object:
    value = _value(config, section, key)
    return PROTECTED_DEFAULTS.get((section, key)) if value is None else value


def _protected_values(config: dict | None) -> list[object]:
    return [protected_value(config, s, k) for s, k in PROTECTED]


def document_patterns(config: dict | None, payment: frozenset[str] = frozenset()) -> set[str]:
    """C4's document patterns this config turns on: the ones everywhere, the country's, the
    person's own, less the payment floor's (`payment`), which C4 applies whatever the config
    says — as numbers_check.active_patterns reads them (Kimi, PR #16)."""
    person, personal = (config or {}).get("person", {}), (config or {}).get("personal", {})
    country = person.get("country") if isinstance(person, dict) else None
    own = personal.get("patterns", []) if isinstance(personal, dict) else []
    own = own if isinstance(own, list) else []
    return {*numbers_check.EVERYWHERE, *numbers_check.BY_COUNTRY.get(country, ()),
            *(p for p in own if isinstance(p, str) and p in numbers_check.PATTERNS)} - payment


def excluded_pages(config: dict | None) -> set[str]:
    """`notion.backup_exclude` as the copy reads it: ids in any spelling."""
    notion = (config or {}).get("notion", {})
    ids = notion.get("backup_exclude", []) if isinstance(notion, dict) else []
    return {norm(x) for x in ids if isinstance(x, str)} - {""} if isinstance(ids, list) else set()


def _guarded(config: dict | None, payment: frozenset[str]) -> tuple:
    return (_protected_values(config), sorted(document_patterns(config, payment)), sorted(excluded_pages(config)))


def _introduced_by_this_push(repo: Path, before: str, on_main: dict | None, payment: frozenset[str]) -> bool:
    if not before:
        return False
    try:
        raw = gitio.file_at(repo, before, CONFIG)
    except gitio.GitError:
        # After a force push `before` is the old head, which this run never fetched — it
        # fetched only the new head and main. Nothing shows what the keys were before the
        # push, so the change is not trusted.
        return False
    parsed, _ = _parse(raw, CONFIG, "C3")
    return _guarded(parsed, payment) == _guarded(on_main, payment)


def check(repo: Path, base: str, head: str, kind: str, actor: str = "", before: str = "") -> list[Finding]:
    floors_rev = base if kind == PERSON else head
    defaults, findings = _parse(gitio.file_at(repo, floors_rev, DEFAULTS), DEFAULTS, "C3")
    config, more = _parse(gitio.file_at(repo, head, CONFIG), CONFIG, "C3")
    findings += more
    if defaults is None or config is None:
        return findings
    floors = defaults.get("floors", {})
    floor_payment = floors.get("payment_patterns_min", [])
    payment = frozenset(n for n in floor_payment if isinstance(n, str)) if isinstance(floor_payment, list) else frozenset()

    for section, value in config.items():
        if section not in SCHEMA:
            findings.append(Finding("C3", f"unknown section [{section}]"))
            continue
        if not isinstance(value, dict):
            findings.append(Finding("C3", f"[{section}] is not a table"))
            continue
        for key in value:
            if key not in SCHEMA[section]:
                findings.append(Finding("C3", f"unknown key {section}.{key}"))
    for section, keys in SCHEMA.items():
        table = config.get(section, {})
        if not isinstance(table, dict):
            continue
        for key, (expected, required) in keys.items():
            if key not in table:
                if required:
                    findings.append(Finding("C3", f"required key {section}.{key} is missing"))
                continue
            if not _type_ok(table[key], expected):
                findings.append(Finding("C3", f"{section}.{key} must be {_type_name(expected)}"))
    if findings:
        return findings

    for (section, key), floor_key in MAXIMA.items():
        if key not in config.get(section, {}):
            continue
        value = config[section][key]
        floor = floors.get(floor_key)
        if floor is None:
            findings.append(Finding("C3", f"floor {floor_key} is missing in {DEFAULTS}"))
        elif value <= 0:
            findings.append(Finding("C3", f"{section}.{key} must be positive"))
        elif value > floor:
            findings.append(Finding("C3", f"{section}.{key} = {value} loosens the floor {floor}"))

    patterns = config.get("personal", {}).get("patterns", [])
    unknown = sorted(str(p) for p in patterns if not isinstance(p, str) or p not in KNOWN_PATTERNS)
    if unknown:
        findings.append(Finding("C3", f"unknown personal patterns: {', '.join(map(str, unknown))}"))

    person = config["person"]
    for lang in [person["language"], *person.get("work_languages", [])]:
        if not isinstance(lang, str) or not LANGUAGE_RE.fullmatch(lang):
            findings.append(Finding("C3", f"language {lang!r} is not a language code like ru or pt-BR"))
    if not COUNTRY_RE.fullmatch(person["country"]):
        findings.append(Finding("C3", f"person.country {person['country']!r} is not a two-letter code like IL"))

    if kind == PERSON:
        on_main, _ = _parse(gitio.file_at(repo, base, CONFIG), CONFIG, "C3")
        trusted = _value(on_main, "alarm", "trusted_contact")
        by_trusted = (isinstance(trusted, str) and trusted != "" and actor.lower() == trusted.lower()
                      and _introduced_by_this_push(repo, before, on_main, payment))
        for section, key in PROTECTED:
            if protected_value(on_main, section, key) != protected_value(config, section, key) and not by_trusted:
                findings.append(Finding("C3", f"{section}.{key} changes only in a push of the trusted contact's own"))
        dropped = sorted(document_patterns(on_main, payment) - document_patterns(config, payment))
        if dropped and not by_trusted:
            findings.append(Finding("C3", f"C4 would stop looking for {', '.join(dropped)} (person.country or "
                                          f"personal.patterns): only in a push of the trusted contact's own"))
        if excluded_pages(config) - excluded_pages(on_main) and not by_trusted:
            findings.append(Finding("C3", "notion.backup_exclude leaves a new page out of the copy: only in a "
                                          "push of the trusted contact's own"))

    if config["engine"]["channel"] not in CHANNELS:
        findings.append(Finding("C3", f"engine.channel must be one of {', '.join(sorted(CHANNELS))}"))
    if protected_value(config, "github", "protection") not in PROTECTION_MODES:
        findings.append(Finding("C3", f"github.protection must be one of {', '.join(sorted(PROTECTION_MODES))}"))
    contact = _value(config, "alarm", "trusted_contact")
    if contact and not LOGIN_RE.fullmatch(contact):
        findings.append(Finding("C3", f"alarm.trusted_contact {contact!r} is not a GitHub login"))
    login = _value(config, "person", "github_login")
    if login and not LOGIN_RE.fullmatch(login):
        findings.append(Finding("C3", f"person.github_login {login!r} is not a GitHub login"))
    removed = _value(config, "notion", "removed_bases")
    for entry in removed if isinstance(removed, list) else []:
        if isinstance(entry, str) and not REMOVED_RE.fullmatch(entry):     # a non-string is the type's finding
            findings.append(Finding("C3", f"notion.removed_bases entry {entry!r} is not <id>@<YYYY-MM-DD>, "
                                          f"the base and the day as N3's line names them"))
    number = _value(config, "alarm", "marks_issue")
    if number is not None and (isinstance(number, bool) or number <= 0):
        findings.append(Finding("C3", "alarm.marks_issue must be a positive Issue number"))
    try:
        ZoneInfo(config["person"]["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        findings.append(Finding("C3", f"unknown timezone {config['person']['timezone']!r}"))
    return findings

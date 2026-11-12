"""`setup`: what a new installation writes for the person, from her choices (docs/DESIGN_PHASE3.md
§5, step 2).

    python -m engine.setup.setup --repo . --platform grok --language ru --country IL \\
        --timezone Asia/Jerusalem --name Anna --gender female [--work-languages en,he] --issue issue.md

Writes the text of the Issue «Your assistant's instructions» into `--issue` first, then
`user/config.toml` and her part of the field, `user/fields/chat.md`, into the checkout. The
Issue holds the field to paste, where her platform keeps it, the plan it needs, the first
message to her AI — with the link to `install.md` in the release her repository holds
(`user/engine.lock`), or, where her chat reads the rules from a project file, that file's page
and a first message to it — and what works on her platform and what does not. Refuses — exit 2,
nothing written — a platform the table does not support, a choice C3 would refuse, a name C5 would
hold red every night (C4 scans her part too), a repository that already has her part, or a field
over the limit. No emergency number is asked: her part names her country, and the AI names its
number (owner, 12.11.2026). Her work languages — the other languages she works in, besides her
own; none when she works in her own — are chosen here too, not asked by her AI: without a bot
nothing writes her config after installation.

The Issue is English: the person who takes the engine from GitHub knows GitHub, and her AI
explains the rest in her language from the first message on — the owner's decision, 12.11.2026,
quoted in docs/DESIGN_PHASE3.md §5. Her language lives in her part only, never in the engine
(docs/DESIGN.md §11).
"""
from __future__ import annotations

import argparse
import sys
import tomllib
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..checks import limits_check, numbers_check, text_check
from ..checks.config_check import COUNTRY_RE, LANGUAGE_RE
from ..checks.lock_check import LOCK_FILE, release_key
from .release import PUBLISHED_FILES, PUBLISHED_TAG_RE, RULES

ENGINE = Path(__file__).resolve().parents[1]
PLATFORMS = ENGINE / "platforms.toml"
DEFAULTS = ENGINE / "defaults.toml"
SOLO = ENGINE / "rules" / "fields" / "solo.md"
SOLO_FILE = ENGINE / "rules" / "fields" / "solo-file.md"     # the chat reads the rules from the project file
BUNDLE = "memory-engine-rules.md"                             # engine/setup/bundle.py builds it
CONFIG = "user/config.toml"
PART = "user/fields/chat.md"
INSTALLABLE = ("supported", "beta")
KEYS = ("about-me", "health", "documents", "inbox")         # the anchors of engine/rules/install.md, INSTALL-4
MAP_KEY = "memory-map"                                     # the map page, INSTALL-7 (not «memory»: Notion's own Memory)
GENDERS = ("female", "male")
WORK_LANGUAGES_MAX = 3
LANGUAGE_MAX = 10                                            # zh-Hant-TW: language, script, region
NAME_MAX = 60                                                # UTF-16 units, as C6 counts the field
TIMEZONE_MAX = 32                                            # America/Argentina/ComodRivadavia
MARKS_MAX = 4                                                # accents or vowel signs on one letter
# Marks that show nothing — variation selectors, the grapheme joiner — carry hidden text, not names
INVISIBLE_MARKS = ((0x034F, 0x034F), (0x17B4, 0x17B5), (0x180B, 0x180F), (0xFE00, 0xFE0F), (0xE0100, 0xE01EF))


class SetupError(ValueError):
    """A choice setup refuses; the message says which and why, for the run log."""


def name_ok(name: str) -> bool:
    """Letters — with their accents and vowel signs (Unicode marks) — in words joined by one space,
    ', . or -; a full stop may be followed by a space, as in «J. R. Smith». No digit, no quote, no
    line break, no invisible mark, at most four marks on a letter, nothing at either end but
    a letter or a final full stop."""
    prev, marks = "", 0                                      # "" at the start, "L" after a letter
    for ch in name:
        category = unicodedata.category(ch)
        if category.startswith("L"):
            prev, marks = "L", 0
        elif category in ("Mn", "Mc"):
            marks += 1
            if prev != "L" or marks > MARKS_MAX or any(lo <= ord(ch) <= hi for lo, hi in INVISIBLE_MARKS):
                return False
        elif ch in " '.-" and (prev == "L" or (prev == "." and ch == " ")):
            prev = ch
        else:
            return False
    return prev in ("L", ".")


def platforms(path: Path = PLATFORMS) -> dict[str, dict]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _floor() -> int:
    return _toml(DEFAULTS)["floors"]["chat_field_limit_max"]


def limit_of(row: dict, floor: int) -> int:
    return min(row["field_limit"], floor) if row.get("field_limit") else floor


def _check(platform: str, language: str, country: str, timezone: str, name: str, gender: str,
           work_languages: tuple[str, ...], table: dict[str, dict]) -> dict:
    row = table.get(platform)
    if row is None:
        raise SetupError(f"platform {platform!r} is not one of {', '.join(sorted(table))}")
    if row.get("status") not in INSTALLABLE:
        raise SetupError(f"{row.get('title', platform)} is not supported yet: its AI cannot write her Notion "
                         f"(docs/DESIGN_PHASE3.md §3)")
    if len(language) > LANGUAGE_MAX or not LANGUAGE_RE.fullmatch(language):
        raise SetupError(f"language {language!r} is not a language code like ru or pt-BR")
    if not COUNTRY_RE.fullmatch(country):
        raise SetupError(f"country {country!r} is not a two-letter code like IL")
    if "/" not in timezone or len(timezone) > TIMEZONE_MAX:
        raise SetupError(f"time zone {timezone!r} is not one like Europe/Berlin")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise SetupError(f"time zone {timezone!r} is not one like Europe/Berlin") from None
    if limits_check.length(name) > NAME_MAX or not name_ok(name):
        raise SetupError(f"the name must be letters, with single spaces, ' . or -, at most {NAME_MAX} characters")
    if text_check.mixed_words(name):
        raise SetupError("the name mixes Latin and Cyrillic letters that look alike (C5)")
    if gender not in GENDERS:
        raise SetupError(f"gender must be one of {', '.join(GENDERS)}: the grammar the AI uses for her")
    if len(work_languages) > WORK_LANGUAGES_MAX:
        raise SetupError(f"at most {WORK_LANGUAGES_MAX} work languages")
    for lang in work_languages:
        if len(lang) > LANGUAGE_MAX or not LANGUAGE_RE.fullmatch(lang):
            raise SetupError(f"work language {lang!r} is not a language code like en or pt-BR")
    if len(set(work_languages)) < len(work_languages):
        raise SetupError("a work language is named twice")
    if language in work_languages:
        raise SetupError("her own language is hers already: name only the other languages she works in")
    return row


def work_languages_of(text: str) -> tuple[str, ...]:
    """«en,he» → ("en", "he"); an empty choice → none."""
    return tuple(part.strip() for part in text.split(",")) if text.strip() else ()


def _works(row: dict) -> str:
    """One line for her part: what her platform does, so the chat that writes knows it too
    (INSTALL-12 reads it there — the chat cannot read the Issue)."""
    try:
        notion = {"yes": "yes", "unknown": "not confirmed yet", "no": "no"}[row["notion"]]
        schedule = {"yes": "yes", "paid": f"{row['plan']} only", "unconfirmed": "not confirmed on her plan",
                    "no": "no"}[row["schedule"]]
    except KeyError as exc:
        raise SetupError(f"engine/platforms.toml: {row.get('title', '?')} has no known value for {exc}") from None
    return (f"Her platform: {row['title']}; plan: {row['plan']}; writes Notion itself: {notion}; "
            f"scheduled tasks: {schedule}; nightly copy: off.")


def build(platform: str, language: str, country: str, timezone: str, name: str, gender: str,
          work_languages: tuple[str, ...] = (), table: dict[str, dict] | None = None,
          floor: int | None = None) -> dict[str, str]:
    """{path: text} for her repository: the config and her part of the field."""
    table = platforms() if table is None else table
    floor = _floor() if floor is None else floor
    work_languages = tuple(work_languages)
    row = _check(platform, language, country, timezone, name, gender, work_languages, table)
    quoted = ", ".join(f'"{w}"' for w in work_languages)
    work = f"\nwork_languages = [{quoted}]" if work_languages else ""
    config = (
        "[person]\n"
        f'language = "{language}"{work}\n'
        f'country = "{country}"\n'
        f'timezone = "{timezone}"\n'
        "\n[platform]\n"
        f'name = "{platform}"\n'
        f"chat_field_limit = {limit_of(row, floor)}\n"
        "read_budget_bytes = 16384\n"
        'writer = "chat"                # one assistant, one field (DESIGN_PHASE3 §1)\n'
        "bot = false\n"
        "\n[engine]\n"
        'channel = "stable"\n'
        "\n[notion]\n"
        "backup = false                 # the nightly copy: offered later (DESIGN_PHASE3 §7)\n"
        "\n[github]\n"
        'protection = "none"            # a private repository on GitHub Free has no branch rules\n'
    )
    grammar = ("She is a woman: feminine forms for her." if gender == "female"
               else "He is a man: masculine forms; «she», «her» in the rules mean him.")
    part = (
        f"PERSON PART — {name} (v1).\n"
        f"Her name: {name}; address her as the map's «How we talk» says. {grammar}\n"
        f"Language codes — hers: {language}; work: {', '.join(work_languages) or 'none'}.\n"
        f"Her time zone: {timezone}.\n"
        f"Her emergency number: her country's ({country}) — name it from what you know; not sure → "
        "«call your local emergency number».\n"
        f"Her Notion: her map, the page titled «… · {MAP_KEY}»: read it before your first answer in each chat, "
        "danger signs still first (NOTION-10, 11). Notion connected, no map, not installing (install.md) → "
        "record nothing in Notion; ask her once to resend her installation's first message.\n"
        "«Continue setup» in any language, any day → the map's «Still to ask» (notion.md, NOTION-12).\n"
        f"{_works(row)}\n"
        "Trusted contact: none.\n"
    )
    patterns = ["card", *numbers_check.EVERYWHERE, *numbers_check.BY_COUNTRY.get(country, ())]
    if numbers_check.scan(part, patterns):
        raise SetupError("the name looks like a document number or a phone (C4)")
    return {CONFIG: config, PART: part}


def reads_file(platform: str) -> bool:
    """Whether her chat reads the rules from the project file, not by the link (on the owner's test,
    12.11.2026, ChatGPT did not open the raw link, with its GitHub app on or off)."""
    return platform in limits_check.FILE_RULES


def field_text(part: str, solo: str | None = None, platform: str = "") -> str:
    if solo is None:
        solo = (SOLO_FILE if reads_file(platform) else SOLO).read_text(encoding="utf-8")
    return limits_check.assemble(solo, part)


def install_link(repo: Path) -> str:
    """install.md in the public repository of releases, at the release her repository holds —
    a file of that very release, so it exists there; never the field's own older link."""
    manifests, tag = _release(repo)
    return f"https://raw.githubusercontent.com/{manifests}/{tag}/{PUBLISHED_FILES}/{RULES}install.md"


def bundle_link(repo: Path) -> str:
    """The rules file on GitHub's own page, at the release her repository holds: the Issue sends her
    there to download it and add it to her project's files (not yet tried from a phone)."""
    manifests, tag = _release(repo)
    return f"https://github.com/{manifests}/blob/{tag}/{PUBLISHED_FILES}/{RULES}{BUNDLE}"


def _release(repo: Path) -> tuple[str, str]:
    raw = (repo / LOCK_FILE).read_bytes() if (repo / LOCK_FILE).is_file() else None
    try:
        tag = tomllib.loads(raw.decode("utf-8")).get("release") if raw else None
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        tag = None
    if not isinstance(tag, str) or not PUBLISHED_TAG_RE.fullmatch(tag):
        raise SetupError(f"{LOCK_FILE} names no release: setup runs in a repository made from the template")
    release_key(tag)
    return _toml(DEFAULTS)["release"]["manifests"], tag


def issue(row: dict, field: str, link: str, rules_file: str = "") -> str:
    """The Issue she reads: steps 3 to 5 of DESIGN_PHASE3 §5, in plain English. With `rules_file`
    — the link to the rules file — the chat reads the rules from her project's files: one step
    more, and the first message points to the file instead of the link."""
    notion = {"yes": "Your AI writes your records into Notion itself.",
              "unknown": "Whether this AI can create pages in Notion is not known yet: if it cannot, "
                         "your records cannot be kept there — that is why it is in beta.",
              "no": "This AI cannot write to Notion."}[row["notion"]]
    schedule = {"yes": "It can do daily things on its own.",
                "paid": f"Daily things on its own need the {row['plan']} plan.",
                "unconfirmed": "Whether it does daily things on its own on your plan is not confirmed yet.",
                "no": "It cannot do anything on its own on a schedule."}[row["schedule"]]
    unknown = f" In beta — not known yet: {row['unknown']}." if row.get("unknown") else ""
    plan = "" if row["plan"] == "any" else (
        f"**You need the {row['plan']} plan.** On a smaller plan the instructions field is too short or the "
        "AI cannot do what this needs: the end of the text — your own part — would be cut off.\n\n")
    steps = ["**{n}. The instructions.** " + f"Open {row['field']}. Paste the whole text below, exactly as "
             "it is, and save. Check that its last line came in too: «Trusted contact: none.»\n\n"
             f"```\n{field}\n```\n\n"]
    if rules_file:
        steps.append("**{n}. The rules file.** " + f"Download {BUNDLE} from {rules_file} and add it to the "
                     "files of the same project. In our test this AI did not open the rules by a link, so it reads "
                     "them there.\n\n")
    steps.append("**{n}. Connections.** In the same app, connect Notion — and Google, if you want your mail "
                 "and calendar.\n\n")
    first = (f"Open the project file {BUNDLE}, its section «=== FILE: install.md ===», and follow it: "
             "start the installation." if rules_file else f"Open {link} and follow it: start the installation.")
    steps.append("**{n}. The first message.** Send your AI, in a new conversation" +
                 (" inside the project" if rules_file else "") + f":\n\n```\n{first}\n```\n\n")
    count = {3: "Three", 4: "Four"}[len(steps)]
    return (
        f"# Your assistant's instructions — {row['title']}\n\n"
        f"{plan}"
        f"{count} steps, all on your phone.\n\n"
        + "".join(s.replace("{n}", str(i), 1) for i, s in enumerate(steps, 1)) +
        "It answers in your language, first tells you what it can do — and that it shows you and asks "
        "before it sends, buys or deletes anything (short e-mails you dictate can go without the question, if "
        "you choose) — then looks at what you already have in Notion and asks "
        "«is it this one?» for what fits, asks — if your language is not English — in which language to name what it makes, "
        "and asks one «yes?» for the whole plan. It makes only what is missing; you make nothing there by "
        "hand. Not using Notion yet? Send the same message on the day you connect it. If it stops halfway, "
        "send it again.\n\n"
        "Then it asks a few questions — how to address you, what to help with first, how you like answers, "
        "what you must avoid; skip any. It can learn more about you later: on any day, say «continue setup» in your language.\n\n"
        f"**What works on {row['title']}.** {notion} {schedule}{unknown} No daily task is set up at installation "
        "yet: a later release brings them. The nightly copy of your Notion is off; it can be turned on "
        "later.\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path("."))
    for name in ("platform", "language", "country", "timezone", "name", "gender"):
        ap.add_argument(f"--{name}", required=True)
    ap.add_argument("--work-languages", default="",
                    help="the other languages she works in, besides her own — codes, comma-separated: en,he; "
                         "none: she works in her own")
    ap.add_argument("--issue", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        if any((args.repo / p).exists() for p in (CONFIG, PART)):
            raise SetupError("this repository already has her part: setup is the first installation only")
        table, floor = platforms(), _floor()
        files = build(args.platform, args.language, args.country, args.timezone, args.name, args.gender,
                      work_languages_of(args.work_languages), table, floor)
        field = field_text(files[PART], platform=args.platform)
        limit = limit_of(table[args.platform], floor)
        if (size := limits_check.length(field)) > limit:
            raise SetupError(f"the field would be {size} characters, the limit is {limit}")
        text = issue(table[args.platform], field, install_link(args.repo),
                     bundle_link(args.repo) if reads_file(args.platform) else "")
    except SetupError as exc:
        print(f"ERROR setup refused: {exc}")
        return 2
    try:
        args.issue.write_text(text, encoding="utf-8")          # first: a failure here leaves her part unwritten
        for path, content in files.items():
            (args.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (args.repo / path).write_text(content, encoding="utf-8")
    except OSError as exc:
        for path in files:
            (args.repo / path).unlink(missing_ok=True)          # all or nothing: a rerun is not refused
        print(f"ERROR setup could not write: {type(exc).__name__}: {exc}")
        return 2
    print(f"SETUP wrote {CONFIG}, {PART}; field {limits_check.length(field)} of {limit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

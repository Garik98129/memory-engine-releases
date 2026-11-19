"""`setup`: what a new installation writes for the person, from the user's choices (docs/DESIGN_PHASE3.md
§5, step 2).

    python -m engine.setup.setup --repo . --platform grok --language ru --country IL \\
        --timezone Asia/Jerusalem --name Anna --gender female [--work-languages en,he] --issue issue.md

Writes the text of the Issue «Your assistant's instructions» into `--issue` first, then
`user/config.toml` and the user's part, `user/fields/chat.md`, into the checkout. The Issue holds the
text to paste — one text for everybody, `engine/rules/fields/fixed.md`: it names no release and
nothing of the user's, so a release that changes the rules, or a change of settings, does not make
the user paste again; only a change of the text itself does (docs/DESIGN_PHASE3.md §4) —, where the platform keeps it, the plan it needs, the
first message to the user's AI, which carries the user's part — what that AI is to know of the user from the start,
and keeps on the map (INSTALL-7) —, and what works on the platform and what does not. Refuses — exit 2,
nothing written — a platform the table does not support, a choice C3 would refuse, a name C5 would
hold red every night (C4 scans the user's part too), a repository that already has the user's part, or a field
over the limit. No emergency number is asked: the user's part names the country, and the AI names its
number (owner, 12.11.2026). The user's work languages — the other languages used for work, besides the user's
own; none when there are no others — are chosen here too, not asked by the user's AI: without a bot
nothing writes the config after installation.

The Issue is English: the person who takes the engine from GitHub knows GitHub, and the user's AI
explains the rest in the user's language from the first message on — the owner's decision, 12.11.2026,
quoted in docs/DESIGN_PHASE3.md §5. The user's language lives in the user's part only, never in the engine
(docs/DESIGN.md §11).
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..checks import gitio, limits_check, numbers_check, text_check
from ..checks.config_check import COUNTRY_RE, LANGUAGE_RE
from ..checks.lock_check import LOCK_FILE, release_key
from ..checks.report import Finding
from .release import PUBLISHED_TAG_RE

ENGINE = Path(__file__).resolve().parents[1]
PLATFORMS = ENGINE / "platforms.toml"
DEFAULTS = ENGINE / "defaults.toml"
FIXED = ENGINE / "rules" / "fields" / "fixed.md"            # the one text for everybody (limits_check.FIXED)
CONFIG = "user/config.toml"
PART = "user/fields/chat.md"                                # the user's part: in the first message and on the map, not in the field
INSTALLABLE = ("supported", "beta")
KEYS = ("about-me", "health", "health-records", "documents", "inbox", "notes")  # the anchors of engine/rules/install.md, INSTALL-4
MAP_KEY = "memory-map"                                     # the map page, INSTALL-7 (not «memory»: Notion's own Memory)
GENDERS = ("female", "male")
GRAMMAR = {"female": "The user is a woman: feminine forms.",
           "male": "The user is a man: masculine forms."}
WORK_LANGUAGES_MAX = 3
LANGUAGE_MAX = 10                                            # zh-Hant-TW: language, script, region
NAME_MAX = 60                                                # UTF-16 units, as C6 counts the field
TIMEZONE_MAX = 32                                            # America/Argentina/ComodRivadavia
MARKS_MAX = 4                                                # accents or vowel signs on one letter
# Marks that show nothing — variation selectors, the grapheme joiner — carry hidden text, not names
INVISIBLE_MARKS = ((0x034F, 0x034F), (0x17B4, 0x17B5), (0x180B, 0x180F), (0xFE00, 0xFE0F), (0xE0100, 0xE01EF))
INVISIBLE_LETTERS = frozenset("\u115f\u1160\u3164\uffa0")     # Hangul fillers: letters by category, blank to the eye
WORDS_MAX = 5                                                # María de los Ángeles López
STOP_AFTER_MAX = 2                                           # «J.», «Jr.», «St.»: an initial or a title, not a sentence


FENCE_RE = re.compile(r"`{3,}")
# A city or «UTC+2» is what a person knows; the form needs the zone's own name
ZONE_HINT = ": it is written Region/City. Not sure which is yours? Ask your AI for your time zone in that form, and copy it"


def fenced(text: str) -> str:
    """A text in a code block the user copies from: a fence longer than any run of backticks in it, so
    no line of the text closes the block, pings anybody or passes for a marker."""
    fence = "`" * max([3, *(len(run) + 1 for run in FENCE_RE.findall(text))])
    return f"{fence}\n{text}\n{fence}"


class SetupError(ValueError):
    """A choice setup refuses; the message says which and why, for the run log."""


def name_ok(name: str) -> bool:
    """Letters — with their accents and vowel signs (Unicode marks) — in words joined by one space,
    ', . or -; a full stop may be followed by a space, as in «J. R. Smith». No digit, no quote, no
    line break, no invisible mark or letter, at most four marks on a letter, nothing at either end
    but a letter or a final full stop. A name, not a sentence — it goes into the user's field, which the
    AI reads as its instructions: at most five words, and a full stop only after one or two
    letters, an initial or a title."""
    prev, marks, run, words = "", 0, 0, 0                    # prev: "" at the start, "L" after a letter
    for ch in name:
        category = unicodedata.category(ch)
        if category.startswith("L"):
            if ch in INVISIBLE_LETTERS:
                return False
            if prev in ("", " ", "."):
                words += 1
            prev, marks, run = "L", 0, run + 1
        elif category in ("Mn", "Mc"):
            marks += 1
            if prev != "L" or marks > MARKS_MAX or any(lo <= ord(ch) <= hi for lo, hi in INVISIBLE_MARKS):
                return False
        elif ch in " '.-" and (prev == "L" or (prev == "." and ch == " ")):
            if ch == "." and run > STOP_AFTER_MAX:
                return False
            prev, run = ch, 0
        else:
            return False
    return prev in ("L", ".") and words <= WORDS_MAX


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
        raise SetupError(f"{row.get('title', platform)} is not supported yet: its AI cannot write the user's Notion "
                         f"(docs/DESIGN_PHASE3.md §3)")
    if len(language) > LANGUAGE_MAX or not LANGUAGE_RE.fullmatch(language):
        raise SetupError(f"language {language!r} is not a language code like ru or pt-BR")
    if not COUNTRY_RE.fullmatch(country):
        raise SetupError(f"country {country!r} is not a two-letter code like IL")
    if "/" not in timezone or len(timezone) > TIMEZONE_MAX:
        raise SetupError(f"time zone {timezone!r} is not one like Europe/Berlin{ZONE_HINT}")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise SetupError(f"time zone {timezone!r} is not one like Europe/Berlin{ZONE_HINT}") from None
    if limits_check.length(name) > NAME_MAX or not name_ok(name):
        raise SetupError(f"the name must be letters, with single spaces, ' . or -, at most {NAME_MAX} characters "
                         f"and {WORDS_MAX} words, a full stop only after an initial")
    if text_check.mixed_words(name):
        raise SetupError("the name mixes Latin and Cyrillic letters that look alike (C5)")
    if gender not in GENDERS:
        raise SetupError(f"gender must be one of {', '.join(GENDERS)}: the grammar the AI uses for the user")
    if len(work_languages) > WORK_LANGUAGES_MAX:
        raise SetupError(f"at most {WORK_LANGUAGES_MAX} work languages")
    for lang in work_languages:
        if len(lang) > LANGUAGE_MAX or not LANGUAGE_RE.fullmatch(lang):
            raise SetupError(f"work language {lang!r} is not a language code like en or pt-BR")
    if len(set(work_languages)) < len(work_languages):
        raise SetupError("a work language is named twice")
    if language in work_languages:
        raise SetupError("the user's own language is counted already: name only the other languages the user works in")
    return row


def work_languages_of(text: str) -> tuple[str, ...]:
    """«en,he» → ("en", "he"); an empty choice → none."""
    return tuple(part.strip() for part in text.split(",")) if text.strip() else ()


def _works(row: dict) -> str:
    """One line for the user's part: what the platform does, so the chat that writes knows it too
    (INSTALL-12 reads it there — the chat cannot read the Issue)."""
    try:
        notion = {"yes": "yes", "unknown": "not confirmed yet", "no": "no"}[row["notion"]]
        schedule = {"yes": "yes", "paid": f"{row['plan']} only", "unconfirmed": "not confirmed on the user's plan",
                    "no": "no"}[row["schedule"]]
    except KeyError as exc:
        raise SetupError(f"engine/platforms.toml: {row.get('title', '?')} has no known value for {exc}") from None
    return (f"The user's platform: {row['title']}; plan: {row['plan']}; writes Notion itself: {notion}; "
            f"scheduled tasks: {schedule}; nightly copy: off.")


def build(platform: str, language: str, country: str, timezone: str, name: str, gender: str,
          work_languages: tuple[str, ...] = (), table: dict[str, dict] | None = None,
          floor: int | None = None) -> dict[str, str]:
    """{path: text} for the user's repository: the config and the user's part — the lines the first message
    gives the user's AI and the map keeps (`first_message`; INSTALL-7). Nothing of it stands in the
    field: that is one text for everybody."""
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
        f'name = "{name}"                 # as the AI calls the user; the form «settings» keeps it (DESIGN_PHASE3 §6)\n'
        f'gender = "{gender}"\n'
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
    grammar = GRAMMAR[gender]
    part = (
        f"PERSON PART — {name} (v2).\n"
        f"The user's name: {name}; address the user as the map's «How we talk» says. {grammar}\n"
        f"Language codes — the user's: {language}; work: {', '.join(work_languages) or 'none'}.\n"
        f"The user's time zone: {timezone}.\n"
        f"The user's country: {country}. Its emergency number — name it from what you know; not sure → "
        "«call your local emergency number».\n"
        f"The user's Notion: the map, the page titled «… · {MAP_KEY}» (NOTION-10, 11).\n"
        f"{_works(row)}\n"
        "Trusted contact: none.\n"
    )
    patterns = ["card", *numbers_check.EVERYWHERE, *numbers_check.BY_COUNTRY.get(country, ())]
    if numbers_check.scan(part, patterns):
        raise SetupError("the name looks like a document number or a phone (C4)")
    return {CONFIG: config, PART: part}


def choices_of(config: str, part: str = "") -> dict:
    """The seven choices a config of `build`'s shape was built from. SetupError if it is not of
    that shape; whether the two files are `build`'s output is `held`'s question. All seven are
    read from the config — the user's name and the grammar too, which `build` writes there — never
    from the user's part's wording, which a later release may change."""
    try:
        table = tomllib.loads(config)
    except tomllib.TOMLDecodeError as exc:
        raise SetupError(f"{CONFIG} does not parse: {exc}") from None
    return choices_in(table)


def answers_in(table: dict) -> dict:
    """Those of the seven choices a parsed config holds in the shape `build` writes them — all
    seven in a config `build` wrote, fewer in one somebody wrote by hand."""
    person = table.get("person") if isinstance(table.get("person"), dict) else {}
    platform = table.get("platform") if isinstance(table.get("platform"), dict) else {}
    found = {"platform": platform.get("name"), "language": person.get("language"), "country": person.get("country"),
             "timezone": person.get("timezone"), "name": person.get("name"), "gender": person.get("gender")}
    found = {key: value for key, value in found.items() if isinstance(value, str)}
    work = person.get("work_languages", [])
    if isinstance(work, list) and all(isinstance(w, str) for w in work):
        found["work_languages"] = tuple(work)
    return found


def choices_in(table: dict) -> dict:
    """The seven choices in a parsed config, as `build` takes them."""
    if not isinstance(table.get("person"), dict) or not isinstance(table.get("platform"), dict):
        raise SetupError(f"{CONFIG} has no [person] or no [platform]")
    found = answers_in(table)
    if len(found) < 7:
        raise SetupError(f"{CONFIG} does not hold the user's choices as setup writes them")
    return found


def held(repo: Path, rev: str) -> list[Finding]:
    """The gate's hold on a setup PR (DESIGN_PHASE3 §5), run beside C2: the config and the user's
    part at rev are byte for byte what `build` writes for some seven choices, each a choice
    `build` accepts. Nothing else of a setup PR can be trusted — its author and its branch are
    names anybody who can push may borrow — so the content is: no key `build` does not write,
    no trusted contact, no other channel, no line in the user's field that `build` did not put there."""
    raw = {path: gitio.file_at(repo, rev, path) for path in (CONFIG, PART)}
    if None in raw.values():
        return []                   # C2 says which file a setup PR must bring
    try:
        texts = {path: data.decode("utf-8") for path, data in raw.items()}
        built = build(**choices_of(texts[CONFIG]))
    except UnicodeDecodeError:
        return [Finding("C2", "a setup PR's files are not valid UTF-8")]
    except SetupError as exc:
        return [Finding("C2", f"a setup PR carries what setup builds from the user's choices: {exc}")]
    return [Finding("C2", f"{path} is not what setup builds from these choices: a setup PR carries "
                          f"nothing else")
            for path in (CONFIG, PART) if built[path] != texts[path]]


def field_text(fixed: str | None = None) -> str:
    """The text the user pastes: the one text for everybody, with nothing of the user's joined to it
    (`limits_check.installed`)."""
    if fixed is None:
        # The file's bytes, as the checks read it from git: read_text would turn a CR LF into LF, and
        # the Issue's fingerprint would not be the one the watch computes (engine/checks/field_issue.py).
        fixed = FIXED.read_bytes().decode("utf-8")
    return fixed.strip("\n")


# The user's own word that starts the installation, and what the AI is to know of the user from the start: the
# rules of the topic «installation» are found as every topic's are, by the index the field names, and
# the user's part goes on the map (INSTALL-7) — the field holds nothing of the user's. NOTION-10 names this
# message by its first words, which stand.
FIRST_MESSAGE = ("Start the installation: read the rules of the topic «installation» and follow them. "
                 "My part — keep these lines on my map as they are:")
# The same lines again, after a change of the user's settings: one message to send, never a text to paste.
SETTINGS_MESSAGE = "My settings changed. My part is now this — put these lines on my map in place of the old ones:"
# …and on a page that gives the user another text: the settings may be what they were, and a map made
# before the one text holds no part of the user's yet.
PART_MESSAGE = "My part is this — keep these lines on my map, in place of older ones if it holds any:"
# What changes later, and how the user hears of it: said at installation, not asked in the form (the
# owner's decision, 13.11.2026, docs/DESIGN_PHASE3.md §5). The README says the same in each of its
# languages.
LATER = ("**Later.** The rules your AI reads are brought up to date by themselves, usually about a week after a "
         "release: you paste nothing for that. To change a setting — your language, your time zone, your name — "
         "run the workflow «settings» in this repository: it gives you one message to send to your AI; change "
         "your AI itself there, and it gives you the three steps for the new one. The text "
         "above changes only if its core ever does — a rare thing: your copy then opens a new page like this one, "
         "and GitHub tells you, by e-mail — in GitHub's notification settings — or on your phone — in the GitHub "
         "app: Profile, the gear, Notifications, «Direct mentions». With neither, the page still waits in your "
         "copy.\n")


def first_message(part: str, lead: str = FIRST_MESSAGE) -> str:
    """A message the user sends to the AI that carries the user's part: the first of the installation, or — with
    another lead — the one after a change of the settings."""
    return f"{lead}\n\n{part.strip(chr(10))}"


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


def _where(row: dict) -> str:
    """Step 1's first sentences: where the text goes. Where the platform has a place for every chat
    beside the one for a project (`field_all` in engine/platforms.toml), the user chooses between the
    two at once (the owner's decision, 16.11.2026, docs/DESIGN_PHASE3.md §4), with the price of
    «every chat» said."""
    if not row.get("field_all"):
        return f"Open {row['field']}."
    return (f"First choose where your assistant lives. **Only in a project** — {row['field']}: you talk to it "
            f"there, and your other chats stay as they are. Or **in all your chats** — {row['field_all']}: it "
            f"then looks at your Notion before its first answer in every chat, and may offer to record from "
            f"any of them. One place, not both.")


def issue(row: dict, field: str, part: str, manifests: str) -> str:
    """The Issue the user reads: steps 3 to 5 of DESIGN_PHASE3 §5, in plain English."""
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
        f"**You need the {row['plan']} plan.** On a smaller plan the instructions field is too short for the "
        "text below, or the AI cannot do what this needs.\n\n")
    return (
        f"# Your assistant's instructions — {row['title']}\n\n"
        f"{plan}"
        "Three steps, all on your phone.\n\n"
        f"**1. The instructions.** {_where(row)} The text below takes that place alone: put it in place of whatever "
        "stands there — exactly as it is — and save. If you wrote instructions of your own there, copy them out "
        "first: you give them to your AI after the first message. Check that its last line came "
        f"in too: it is «{field.rsplit(chr(10), 1)[-1]}» — keep it: your AI reads it to know its text is whole and "
        "current. The text is the same for everybody: new rules do not change it, and "
        f"neither does a change of your settings. It is {limits_check.length(field)} characters long: it fills "
        "most of the place, and nothing else goes beside it.\n\n"
        f"{fenced(field)}\n\n"
        "**2. Connections.** In the same app, connect Notion and GitHub — and Google, if you want your mail "
        f"and calendar. GitHub is how your AI reads its rules: they are public, in `{manifests}`. Do not give "
        "it this repository of yours.\n\n"
        "**3. The first message.** Send your AI, in a new conversation — inside the project, if the text went "
        "into one:\n\n"
        f"{fenced(first_message(part))}\n\n"
        "It answers in your language, first tells you what it can do — and that it shows you and asks "
        "before it sends, buys or deletes anything (short e-mails you dictate can go without the question, if "
        "you choose) — then looks at what you already have in Notion and asks "
        "«is it this one?» for what fits, asks — if your language is not English — in which language to name what it makes, "
        "and asks one «yes?» for the whole plan. It makes only what is missing; you make nothing there by "
        "hand. Not using Notion yet? Send the same message on the day you connect it. If it stops halfway, "
        "send it again.\n\n"
        "Then it asks a few questions — how to address you, what to help with first, how you like answers, "
        "what you must avoid; skip any. It can learn more about you later: on any day, say «continue setup» in your language.\n\n"
        "**Your old instructions**, if you copied any: once your AI has finished, send them to it with the words "
        "«My old instructions — keep in my Notion what is worth keeping», in your language. It tells you what it "
        "recorded, or that nothing needed keeping.\n\n"
        f"**What works on {row['title']}.** {notion} {schedule}{unknown} No daily task is set up at installation "
        "yet: a later release brings them. The nightly copy of your Notion is off; it can be turned on "
        "later.\n\n"
        f"{LATER}"
    )


def prepare(repo: Path, platform: str, language: str, country: str, timezone: str, name: str,
            gender: str, work_languages: str = "") -> tuple[dict[str, str], str, int, str]:
    """The user's files, field, its limit and the Issue's text — every refusal of `build` and the
    field's limit included. The caller says whether the repository may take them."""
    table, floor = platforms(), _floor()
    files = build(platform, language, country, timezone, name, gender,
                  work_languages_of(work_languages), table, floor)
    field = field_text()
    limit = limit_of(table[platform], floor)
    if (size := limits_check.length(field)) > limit:
        raise SetupError(f"the field would be {size} characters, the limit is {limit}")
    manifests, _tag = _release(repo)
    return files, field, limit, issue(table[platform], field, files[PART], manifests)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path("."))
    for name in ("platform", "language", "country", "timezone", "name", "gender"):
        ap.add_argument(f"--{name}", required=True)
    ap.add_argument("--work-languages", default="",
                    help="the other languages the user works in, besides the user's own — codes, comma-separated: en,he; "
                         "none: no others")
    ap.add_argument("--issue", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        if any((args.repo / p).exists() for p in (CONFIG, PART)):
            raise SetupError("this repository already has the user's part: setup is the first installation only")
        files, field, limit, text = prepare(args.repo, args.platform, args.language, args.country,
                                            args.timezone, args.name, args.gender, args.work_languages)
    except SetupError as exc:
        print(f"ERROR setup refused: {exc}")
        return 2
    try:
        args.issue.write_text(text, encoding="utf-8")          # first: a failure here leaves the user's part unwritten
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

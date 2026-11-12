"""`setup`: what a new installation writes for the person, from her choices (docs/DESIGN_PHASE3.md
§5, step 2).

    python -m engine.setup.setup --repo . --platform grok --language ru --country IL \\
        --timezone Asia/Jerusalem --name Anna --gender female --emergency 101 --issue issue.md

Writes the text of the Issue «Your assistant's instructions» into `--issue` first, then
`user/config.toml` and her part of the field, `user/fields/chat.md`, into the checkout. The
Issue holds the field to paste, where her platform keeps it, the plan it needs, the first
message to her AI — with the link to `install.md` in the release her repository holds
(`user/engine.lock`) — and what works on her platform and what does not. Refuses — exit 2,
nothing written — a platform the table does not support, a choice C3 would refuse, a name or a
number C4 or C5 would hold red every night, a repository that already has her part, or a field
over the limit.

The Issue is English for now: the person who takes the engine from GitHub knows GitHub, and her
AI explains the rest in her language from the first message on; the design (§5) asks for her
language, and the owner decides. Her language lives in her part only, never in the engine
(docs/DESIGN.md §11).
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
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
CONFIG = "user/config.toml"
PART = "user/fields/chat.md"
INSTALLABLE = ("supported", "beta")
KEYS = ("about-me", "health", "documents", "inbox")         # engine/rules/install.md, INSTALL-2
GENDERS = ("female", "male")
EMERGENCY_RE = re.compile(r"[1-9][0-9]{1,5}")                # 101, 112, 911, 999 — never 00 or 000000
NAME_RE = re.compile(r"[^\W\d_]+(?:[ '.-][^\W\d_]+)*")       # letters, single spaces, ' . -
NAME_MAX = 60


class SetupError(ValueError):
    """A choice setup refuses; the message says which and why, for the run log."""


def platforms(path: Path = PLATFORMS) -> dict[str, dict]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _floor() -> int:
    return _toml(DEFAULTS)["floors"]["chat_field_limit_max"]


def limit_of(row: dict, floor: int) -> int:
    return min(row["field_limit"], floor) if row.get("field_limit") else floor


def _check(platform: str, language: str, country: str, timezone: str, name: str, gender: str,
           emergency: str, table: dict[str, dict]) -> dict:
    row = table.get(platform)
    if row is None:
        raise SetupError(f"platform {platform!r} is not one of {', '.join(sorted(table))}")
    if row.get("status") not in INSTALLABLE:
        raise SetupError(f"{row.get('title', platform)} is not supported yet: its AI cannot write her Notion "
                         f"(docs/DESIGN_PHASE3.md §3)")
    if not LANGUAGE_RE.fullmatch(language):
        raise SetupError(f"language {language!r} is not a language code like ru or pt-BR")
    if not COUNTRY_RE.fullmatch(country):
        raise SetupError(f"country {country!r} is not a two-letter code like IL")
    if "/" not in timezone:
        raise SetupError(f"time zone {timezone!r} is not one like Europe/Berlin")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise SetupError(f"time zone {timezone!r} is not one like Europe/Berlin") from None
    if len(name) > NAME_MAX or not NAME_RE.fullmatch(name):
        raise SetupError(f"the name must be letters, with single spaces, ' . or -, at most {NAME_MAX}")
    if text_check.mixed_words(name):
        raise SetupError("the name mixes Latin and Cyrillic letters that look alike (C5)")
    if gender not in GENDERS:
        raise SetupError(f"gender must be one of {', '.join(GENDERS)}: the grammar the AI uses for her")
    if not EMERGENCY_RE.fullmatch(emergency):
        raise SetupError(f"emergency number {emergency!r} must be 2 to 6 digits, like 101 or 112")
    return row


def _works(row: dict) -> str:
    """One line for her part: what her platform does, so the chat that writes knows it too
    (INSTALL-5 reads it there — the chat cannot read the Issue)."""
    notion = {"yes": "creates her Notion items itself", "unknown": "writing to Notion not confirmed yet",
              "no": "cannot write to Notion"}[row["notion"]]
    schedule = {"yes": "runs tasks on a schedule", "paid": f"runs scheduled tasks on {row['plan']} only",
                "unconfirmed": "scheduled tasks not confirmed on her plan", "no": "no scheduled tasks"}[row["schedule"]]
    return f"Her platform: {row['title']}; needs plan: {row['plan']}; {notion}; {schedule}; the nightly copy is off."


def build(platform: str, language: str, country: str, timezone: str, name: str, gender: str, emergency: str,
          table: dict[str, dict] | None = None, floor: int | None = None) -> dict[str, str]:
    """{path: text} for her repository: the config and her part of the field."""
    table = platforms() if table is None else table
    floor = _floor() if floor is None else floor
    row = _check(platform, language, country, timezone, name, gender, emergency, table)
    config = (
        "[person]\n"
        f'language = "{language}"\n'
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
    keys = ", ".join(f"«· {k}»" for k in KEYS)
    grammar = ("She is a woman: feminine forms for her." if gender == "female"
               else "He is a man: masculine forms for him; «she» and «her» in these rules mean him.")
    part = (
        f"PERSON PART — {name} (v1).\n"
        f"Name, as you address her: {name}. {grammar}\n"
        f"Her language: {language}. Work language: none.\n"
        f"Her time zone: {timezone}.\n"
        f"Her emergency number: {emergency} — as she typed it at setup; confirm it with her once (INSTALL-1).\n"
        f"Her Notion: the items made at installation, found by the end of their titles: {keys}.\n"
        f"{_works(row)}\n"
        "Trusted contact: none.\n"
    )
    patterns = ["card", *numbers_check.EVERYWHERE, *numbers_check.BY_COUNTRY.get(country, ())]
    if numbers_check.scan(part, patterns):
        raise SetupError("the name or the number looks like a document number or a phone (C4)")
    return {CONFIG: config, PART: part}


def field_text(part: str, solo: str | None = None) -> str:
    solo = SOLO.read_text(encoding="utf-8") if solo is None else solo
    return limits_check.assemble(solo, part)


def install_link(repo: Path) -> str:
    """install.md in the public repository of releases, at the release her repository holds —
    a file of that very release, so it exists there; never the field's own older link."""
    raw = (repo / LOCK_FILE).read_bytes() if (repo / LOCK_FILE).is_file() else None
    try:
        tag = tomllib.loads(raw.decode("utf-8")).get("release") if raw else None
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        tag = None
    if not isinstance(tag, str) or not PUBLISHED_TAG_RE.fullmatch(tag):
        raise SetupError(f"{LOCK_FILE} names no release: setup runs in a repository made from the template")
    release_key(tag)
    manifests = _toml(DEFAULTS)["release"]["manifests"]
    return f"https://raw.githubusercontent.com/{manifests}/{tag}/{PUBLISHED_FILES}/{RULES}install.md"


def issue(row: dict, field: str, link: str) -> str:
    """The Issue she reads: steps 3 to 5 of DESIGN_PHASE3 §5, in plain English."""
    notion = {"yes": "Your AI writes your records into Notion itself.",
              "unknown": "Whether this AI can create pages in Notion is not known yet: if it cannot, "
                         "your records cannot be kept there — that is why it is in beta.",
              "no": "This AI cannot write to Notion."}[row["notion"]]
    schedule = {"yes": "It can do daily things on its own.",
                "paid": f"Daily things on its own need the {row['plan']} plan.",
                "unconfirmed": "Whether it does daily things on its own on your plan is not confirmed yet.",
                "no": "It cannot do anything on its own on a schedule."}[row["schedule"]]
    plan = "" if row["plan"] == "any" else (
        f"**You need the {row['plan']} plan.** On a smaller plan the instructions field is too short or the "
        "AI cannot do what this needs: the end of the text — your own part — would be cut off.\n\n")
    return (
        f"# Your assistant's instructions — {row['title']}\n\n"
        f"{plan}"
        "Three steps, all on your phone.\n\n"
        f"**1. The instructions.** Open {row['field']}. Paste the whole text below, exactly as it is, "
        "and save.\n\n"
        f"```\n{field}\n```\n\n"
        "**2. Connections.** In the same app, connect Notion — and Google, if you want your mail and "
        "calendar.\n\n"
        "**3. The first message.** Send your AI, in a new conversation:\n\n"
        f"```\nOpen {link} and follow it: start the installation.\n```\n\n"
        "It answers in your language, checks your emergency number with you, asks one «yes?», and makes "
        "your Notion ready. You make nothing there by hand. If it stops halfway, send the same message "
        "again: it makes only what is missing.\n\n"
        f"**What works on {row['title']}.** {notion} {schedule} No daily task is set up at installation "
        "yet: a later release brings them. The nightly copy of your Notion is off; it can be turned on "
        "later.\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path("."))
    for name in ("platform", "language", "country", "timezone", "name", "gender", "emergency"):
        ap.add_argument(f"--{name}", required=True)
    ap.add_argument("--issue", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        if any((args.repo / p).exists() for p in (CONFIG, PART)):
            raise SetupError("this repository already has her part: setup is the first installation only")
        table, floor = platforms(), _floor()
        files = build(args.platform, args.language, args.country, args.timezone, args.name, args.gender,
                      args.emergency, table, floor)
        field = field_text(files[PART])
        limit = limit_of(table[args.platform], floor)
        if (size := limits_check.length(field)) > limit:
            raise SetupError(f"the field would be {size} characters, the limit is {limit}")
        text = issue(table[args.platform], field, install_link(args.repo))
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

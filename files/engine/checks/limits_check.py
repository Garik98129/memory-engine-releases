"""C6 field-limits: every instruction field and routine fits its platform limit (docs/DESIGN.md §6).

An instruction field is assembled from the engine's hard part and the person's
part (`assemble`). The bot installs exactly that text, and N5 compares its sha256,
so this is the one definition of the assembly. Length is counted in UTF-16 code
units, as a browser text box counts it; for Cyrillic and Hebrew that equals the
number of characters, for an emoji it is 2. The limit is the person's config value,
never above the floor in engine/defaults.toml on main.

Runs over the merge result on every PR: an update that lengthens the engine part
is caught as well as a person's longer text.

Which fields exist follows `platform.writer` (docs/DESIGN_PHASE3.md §6a): while the bot
writes — phase 1, the pilot, and every config without the key — the chat's field and the
bot's; once the chat writes, the chat's field alone, with the hard part from `solo.md`.

Where the chat writes and no bot runs — every installation `setup` makes — the field is one
text for everybody, `fixed.md`, pasted once and never joined with anything of the user's
(docs/DESIGN_PHASE3.md §4): the user's part is not in the field, it reaches the user's AI in the first
message of the user's installation and lives on the user's map (`installed`). C6 then measures that text
alone, and the user's part by itself.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from . import gitio
from .report import Finding

# field → (engine part, person's part, config key, floor key)
FIELDS = {
    "chat": ("engine/rules/fields/chat.md", "user/fields/chat.md", "chat_field_limit", "chat_field_limit_max"),
    "bot": ("engine/rules/fields/bot.md", "user/fields/bot.md", "bot_field_limit", "bot_field_limit_max"),
}
# The chat's hard part when the chat writes itself: one field, no bot's field.
SOLO = "engine/rules/fields/solo.md"
# The same, for a platform whose chat did not open the rules' raw link but opens GitHub's own page
# of the file (built by engine/setup/blob.py; ChatGPT, the owner's tests of 12.11.2026).
SOLO_BLOB = "engine/rules/fields/solo-blob.md"
BLOB_RULES = ("chatgpt",)               # platform.name of those platforms
# The one text for everybody, where the chat writes and no bot runs: the rules by an address that
# never changes — the branch `stable` of the repository of releases — and nothing of the user's in it.
FIXED = "engine/rules/fields/fixed.md"
WRITERS = ("bot", "chat")               # platform.writer: who writes the user's Notion, mail and calendar
CONFIG = "user/config.toml"


def _platform(config: dict | None) -> dict:
    table = (config or {}).get("platform", {})
    return table if isinstance(table, dict) else {}


def writer(config: dict | None) -> str:
    """Who writes: «bot» — phase 1, and any config without the key — or «chat». A value C3
    refuses reads as «bot», the scheme every installation had before the key existed."""
    value = _platform(config).get("writer")
    return value if value in WRITERS else "bot"


def has_bot(config: dict | None) -> bool:
    """Whether a bot runs the user's routines and posts the daily mark: true unless the config says
    false — so N4, N5 and N7 stay on for every installation that had a bot."""
    value = _platform(config).get("bot")
    return value if isinstance(value, bool) else True


def fixed(config: dict | None) -> bool:
    """Whether the user's AI holds the one text for everybody: the chat writes and no bot runs. The user's part
    is then not a part of the field (`installed`)."""
    return writer(config) == "chat" and not has_bot(config)


def fields(config: dict | None) -> dict[str, tuple[str, str, str, str]]:
    """The instruction fields of this installation, as FIELDS reads them. With the one text for
    everybody the person's path is still the user's part — what the first message tells the user's AI of the user —
    but `installed` does not join it to the field."""
    if writer(config) == "chat":
        _engine, person, key, floor = FIELDS["chat"]
        if fixed(config):
            return {"chat": (FIXED, person, key, floor)}
        solo = SOLO_BLOB if _platform(config).get("name") in BLOB_RULES else SOLO
        return {"chat": (solo, person, key, floor)}
    return dict(FIELDS)


def config_at(repo: Path, rev: str) -> dict:
    """The person's config at rev, {} when missing or broken — C3 reports that."""
    return _toml(repo, rev, CONFIG)


ROUTINES = "user/routines/"
ROUTINE_KEYS = ("routine_limit", "routine_limit_max")


def assemble(engine_part: str, person_part: str) -> str:
    """The installed text of a field: the engine part, one empty line, the person's part."""
    return engine_part.strip("\n") + "\n\n" + person_part.strip("\n")


def installed(config: dict | None, engine_part: str, person_part: str) -> str:
    """The text the user pastes into the user's AI: the one text for everybody as it is — nothing of the user's is
    joined to it — or, in every other installation, the engine part and the user's part (`assemble`)."""
    return engine_part.strip("\n") if fixed(config) else assemble(engine_part, person_part)


def length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _toml(repo: Path, rev: str, path: str) -> dict:
    raw = gitio.file_at(repo, rev, path)
    if raw is None:
        return {}
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}               # C3 reports a broken config; the floors still apply


def _limit(platform: dict, floors: dict, key: str, floor_key: str) -> int:
    floor = floors.get(floor_key)
    if not isinstance(floor, int) or isinstance(floor, bool):
        raise ValueError(f"floor {floor_key} is missing in engine/defaults.toml")
    value = platform.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value < floor:
        return value
    return floor


def _text(repo: Path, rev: str, path: str, findings: list[Finding]) -> str | None:
    raw = gitio.file_at(repo, rev, path)
    if raw is None:
        findings.append(Finding("C6", f"{path} is missing"))
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        findings.append(Finding("C6", f"{path} cannot be measured: not valid UTF-8"))
        return None


def check(repo: Path, base: str, head: str) -> list[Finding]:
    floors = _toml(repo, base, "engine/defaults.toml").get("floors", {})
    config = config_at(repo, head)
    platform = _platform(config)
    findings: list[Finding] = []

    for name, (engine_path, person_path, key, floor_key) in fields(config).items():
        limit = _limit(platform, floors, key, floor_key)
        engine_part = _text(repo, head, engine_path, findings)
        person_part = _text(repo, head, person_path, findings)
        if engine_part is None or person_part is None:
            continue
        size = length(installed(config, engine_part, person_part))
        if size > limit:
            findings.append(Finding("C6", f"the {name} field would be {size} characters, the limit is {limit}"))
        if fixed(config) and (size := length(person_part.strip("\n"))) > limit:
            # The user's part is not in the field, but the user's AI still reads it — in the user's first message, then on
            # the user's map: it stays a few lines, held to the same limit.
            findings.append(Finding("C6", f"{person_path} is {size} characters, the limit is {limit}"))

    limit = _limit(platform, floors, *ROUTINE_KEYS)
    for path_b in sorted(gitio.tree(repo, head, ROUTINES)):
        path = path_b.decode("utf-8", "replace")
        text = _text(repo, head, path, findings)
        if text is not None and (size := length(text)) > limit:
            findings.append(Finding("C6", f"{path} is {size} characters, the limit is {limit}"))
    return findings

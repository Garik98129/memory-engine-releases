"""N7 behind: what the bot still has to bring up to the engine after a release (docs/DESIGN.md §5, §6).

Not an alarm. A release can change a routine's task, the hard part of a field, or add a
task. The bot keeps up by itself: the machine lists what is behind, the bot's daily routine
tells her there is an update to go through, and in a live chat, on her yes, the bot follows
«Updating after a release» in `engine/rules/routines.md`. The lines:

- a routine written from another version of its task, or a task with no routine yet:
  each task in `routines.md` names its routine and its version
  («Routine `morning`, task version 1.»); a master names in its first line that is not blank
  the task and the version it was written from («… (task 3, version 2)», these English
  words in any letter case); a master that names none counts as version 1, and its line
  then says what the first line must say — a rewrite whose first line the watch cannot
  read is told so, not sent round again;
- a version she said no to (`engine.declined` in her config, «walk@1», by a PR with her
  yes) is not listed again until the task's next version — unless its task says «never
  declined» (task 3: it is how she hears of every update); C3 lets a PR add only a task's
  current version;
- a field whose master — the assembled text, as C6 joins it — is not what the record says
  was installed (`user/fields/installed.toml`, REPO-3);
- a routine whose master is not what the record says was installed, once the master is
  GRACE old or a record was written after it: the bot installs a merged master in the same
  chat and then records, so a run in between does not open the list for her own small
  change, and a record that left it out is listed at once. A field has no grace: a release
  that changes one has N5's 36 h behind it, and the bot needs that time with her.

N5 raises an alarm on the last two when they stay so for 36 h; this list is the bot's work
list, not a warning. A record that does not parse is N5's alarm, and this run gives no list:
the Issue stays as it was until the record reads again. The lines are machine English; the bot tells her in plain words.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from typing import NamedTuple

from . import config_check, gitio
from .drift import INSTALLED, _record, _recorded, _shown, digest, routine_text
from .limits_check import ROUTINES, SOLO, SOLO_BLOB, assemble, fields

RULES = "engine/rules/routines.md"
GRACE = timedelta(hours=12)
TASK_HEAD_RE = re.compile(r"^## Task ([1-9][0-9]*) — ", re.M)
SECTION_RE = re.compile(r"^## ", re.M)
TASK_ROUTINE_RE = re.compile(r"^Routine `([a-z][a-z0-9-]*)`, task version ([1-9][0-9]*)(; never declined)?\.$", re.M)
MASTER_TASK_RE = re.compile(r"\(\s*task\s+([1-9][0-9]*)\s*,\s*(?:version\s*|v\.?\s*)([1-9][0-9]*)\s*\)", re.I)


class Task(NamedTuple):
    number: int
    version: int
    fixed: bool         # «never declined»


def task_list(text: str) -> dict[str, Task]:
    """{routine name: Task} from `routines.md`. A task's section ends at the next heading of
    its level. A task section with no «Routine …» line, or with two, is an error of the
    template: F-tests hold it, and here it raises, so the run reports it instead of listing
    nothing."""
    found: dict[str, Task] = {}
    for head in TASK_HEAD_RE.finditer(text):
        end = SECTION_RE.search(text, head.end())
        section = text[head.end():end.start() if end else len(text)]
        named = TASK_ROUTINE_RE.findall(section)
        if len(named) != 1:
            raise ValueError(f"{RULES}: task {head.group(1)} names {len(named)} routines, not one")
        name, version, fixed = named[0]
        if name in found:
            raise ValueError(f"{RULES}: routine {name} is named by two tasks")
        found[name] = Task(int(head.group(1)), int(version), bool(fixed))
    return found


def tasks(text: str) -> dict[str, tuple[int, int]]:
    """{routine name: (task number, task version)} from `routines.md`."""
    return {name: (t.number, t.version) for name, t in task_list(text).items()}


def master_version(raw: bytes) -> tuple[int | None, int]:
    """(task number or None, version) a master says, in its first line that is not blank, it
    was written from; a master that names none was written from version 1."""
    first = next((line for line in raw.decode("utf-8").split("\n") if line.strip()), "")
    m = MASTER_TASK_RE.search(first)
    return (int(m.group(1)), int(m.group(2))) if m else (None, 1)


def declined(config: dict) -> set[str]:
    """The «routine@version» entries she said no to (`engine.declined`); C3 checks their form."""
    section = config.get("engine")
    value = section.get("declined") if isinstance(section, dict) else None
    return {v for v in value if isinstance(v, str)} if isinstance(value, list) else set()


def check(repo: Path, main: str, now: datetime) -> list[str] | None:
    """The bot's work list on main, in a fixed order: fields, then routines by name — or None
    when the record does not parse: N5 raises that, and a list judged without the record
    would read «unreadable» as «nothing left» and close the Issue.
    None leaves the Issue as it is."""
    rules = gitio.file_at(repo, main, RULES)
    listed = task_list(rules.decode("utf-8")) if rules is not None else {}     # a missing file is N1's (C1)
    wanted = {name: (t.number, t.version) for name, t in listed.items()}
    config = config_check.read(repo, main)
    said_no = declined(config)
    record, broken = _record(repo, main)
    if broken:
        return None
    lines: list[str] = []

    for name, (engine_path, person_path, _key, _floor) in fields(config).items():
        engine_part = gitio.file_at(repo, main, engine_path)
        person_part = gitio.file_at(repo, main, person_path)
        if engine_part is None or person_part is None:
            continue        # N1 reports a missing file (C1, C6)
        try:
            text = assemble(engine_part.decode("utf-8"), person_part.decode("utf-8"))
        except UnicodeDecodeError:
            continue        # N1 reports it (C5)
        if _recorded(record, "fields", name) != digest(text):
            if engine_path in (SOLO, SOLO_BLOB):
                # The chat writes itself: its field is joined from solo.md (or solo-blob.md), and the trusted contact
                # gives it to her and records it — not the bot, whose ROUT-9 joins chat.md.
                lines.append(f"field {name}: the installed text is not its master ({engine_path} and her part) — "
                             f"the trusted contact installs it and records it")
            else:
                lines.append(f"field {name}: the installed text is not its master — install it and record it")

    masters = {path[len(ROUTINES):-len(".md")]: raw
               for path, raw in gitio.files_under(repo, main, ROUTINES) if path.endswith(".md")}
    for name in sorted(set(wanted) | set(masters)):
        shown = _shown(name)
        raw = masters.get(name)
        due = name in wanted and (listed[name].fixed or f"{name}@{wanted[name][1]}" not in said_no)
        if raw is None:
            if due:
                number, version = wanted[name]
                lines.append(f"routine {shown}: no master yet — write it from task {number}, version {version}")
            continue
        try:
            text = routine_text(raw)
            said = master_version(raw)
        except UnicodeDecodeError:
            continue            # N1 reports it (C5)
        if due:
            number, version = wanted[name]
            if said[0] is not None and said[0] != number:
                lines.append(f"routine {shown}: its first line names task {said[0]}, the engine's routine {shown} "
                             f"is task {number} — write it again from task {number}, version {version}")
            elif said[0] is None and version != 1:
                lines.append(f"routine {shown}: its first line has no \"(task {number}, version N)\", so it counts as "
                             f"version 1; the engine's task {number} is version {version} — write it again from the "
                             f"task, its first line ending \"(task {number}, version {version})\"")
            elif said[0] is not None and said[1] != version:
                lines.append(f"routine {shown}: written from task {number}, version {said[1]}; the engine's task "
                             f"{number} is version {version} — write it again from the task")
        if _recorded(record, "routines", name) != digest(text):
            changed = gitio.last_change(repo, main, f"{ROUTINES}{name}.md")
            recorded = gitio.last_change(repo, main, INSTALLED)
            if changed is None or now - changed > GRACE or (recorded is not None and recorded > changed):
                lines.append(f"routine {shown}: the installed text is not its master — install it and record it")
    return lines


TITLE = "To-do for the bot: the engine moved ahead"


def body(lines: list[str], run_url: str) -> str:
    """The Issue body: the whole current list. No mention — this is the bot's work, not an
    alarm to people; the bot reads the body as it stands."""
    shown = "\n".join(line.replace("`", "'").replace("<!--", "<!- -") for line in lines)
    return (f"What the bot still has to bring up to the engine after a release. The bot goes through it "
            f"with her in a live chat, on her yes («Updating after a release», {RULES}); the machine keeps this "
            f"list and closes the Issue when nothing is left. Nobody edits or closes it by hand.\n\n"
            f"```text\n{shown}\n```\n\nRun: {run_url}\n")

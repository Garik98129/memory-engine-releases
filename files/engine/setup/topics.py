"""The topic index of a release: which rules files a chat reads for which topic
(docs/DESIGN.md §5, «The topic index and the branch stable»).

    python -m engine.setup.topics --tag v0.1.13        # print the index of the committed rules

A chat without a repository of its own reads the rules in the public repository of releases.
It finds them by the index: one file, `rules-index.md` at the top of that repository, written
by `release cut` and never by hand. The index names its release, and every file of a topic by
its full path — a chat follows a link between two rules files unreliably, so a topic's line is
its whole set. The files are read at the release's tag, which never moves; the branch `stable`
of that repository (`engine/setup/stable.py`) only says which index is current, so a move of
the branch between two reads of one conversation mixes nothing.

The one source is `engine/rules/topics.toml`. `findings` is what `release cut` refuses a
release for: a topic that names a file the release lacks, a rules file no topic and no
`bot_only` line accounts for, a citation of another file's rule that the topic's set leaves
out with no decision on record (a citation of a `bot_only` file aside), a topic whose `when`
is not a part of the row of one of its files in the table people read
(`engine/rules/README.md`).

The index is not one of the release's files: it sits beside `files/`, outside the manifest's
`files` table, so a person's repository, C1 and the update never see it. The manifest holds its
sha256 under `index`, and the code that moves `stable` holds the published index to that.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from engine.setup import stable

RULES = "engine/rules/"
TOPICS = RULES + "topics.toml"
README = RULES + "README.md"
DEFAULTS = "engine/defaults.toml"
INDEX_FILE = stable.INDEX_FILE              # at the top of the repository of releases
_NAME_RE = re.compile(r"[a-z][a-z0-9-]*\.md")                    # a rules file, at the top of engine/rules/
_ID_RE = re.compile(r"(?<![A-Za-z0-9-])([A-Z]{2,})-[0-9]+(?![0-9A-Za-z])")
_DEFINED_RE = re.compile(r"^- \*\*([A-Z]{2,})-[0-9]+ ", re.MULTILINE)
_FILE_RE = re.compile(r"(?<![\w./-])(?:\./|engine/rules/)?([a-z][a-z0-9-]*\.md)(?![\w/-])")    # `mail.md`, engine/rules/mail.md, ./mail.md
_TOPIC_KEYS = frozenset({"name", "when", "files"})
_TOP_KEYS = frozenset({"about", "min_engine", "bot_only", "topic", "cites"})


def _text(files: dict[str, bytes], path: str) -> str:
    raw = files.get(path)
    if raw is None:
        raise ValueError(f"{path} is missing")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{path} is not valid UTF-8") from None


def rules_files(files: dict[str, bytes]) -> list[str]:
    """The rules files of a release: the .md files at the top of engine/rules/, the index for
    people (README.md) aside. `fields/` holds instruction fields, not rules."""
    names = [path[len(RULES):] for path in files if path.startswith(RULES)]
    return sorted(n for n in names if "/" not in n and n.endswith(".md") and n != "README.md")


def load(files: dict[str, bytes]) -> dict:
    """The topics as `engine/rules/topics.toml` gives them, their form checked: every key known,
    every value of its kind — a source that does not parse to exactly this is refused, never
    half read."""
    try:
        data = tomllib.loads(_text(files, TOPICS))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{TOPICS} cannot be read: {exc}") from None
    unknown = sorted(set(data) - _TOP_KEYS)
    if unknown:
        raise ValueError(f"{TOPICS} has keys this code does not know: {', '.join(unknown)}")
    about, bot_only, topics, cites = data.get("about"), data.get("bot_only"), data.get("topic"), data.get("cites")
    if not isinstance(about, str) or not stable.one_line(about.strip()):
        raise ValueError(f"{TOPICS} needs `about`: one plain paragraph — no line break, no address, no path, no release")
    least = data.get("min_engine")
    if not isinstance(least, str) or not stable.TAG_RE.fullmatch(least):
        raise ValueError(f"{TOPICS} needs `min_engine`: the oldest release of the engine these rules are written for")
    if not _names(bot_only):
        raise ValueError(f"{TOPICS} needs `bot_only`: a list of rules file names")
    if not isinstance(topics, list) or not topics:
        raise ValueError(f"{TOPICS} names no topic")
    seen: set[str] = set()
    for topic in topics:
        if not isinstance(topic, dict) or set(topic) != _TOPIC_KEYS:
            raise ValueError(f"a topic in {TOPICS} needs exactly `name`, `when` and `files`")
        name, when = topic["name"], topic["when"]
        if not isinstance(name, str) or not stable._TOPIC_NAME.fullmatch(name):
            raise ValueError(f"a topic's `name` in {TOPICS} is a few plain words: {name!r}")
        if not stable.one_line(when):
            raise ValueError(f"a topic's `when` in {TOPICS} is one plain line — no «|», no address, no path, no release: {when!r}")
        if name in seen:
            raise ValueError(f"{TOPICS} names the topic {name!r} twice")
        seen.add(name)
        if not _names(topic["files"]) or not topic["files"]:
            raise ValueError(f"the topic {name!r} in {TOPICS} needs `files`: rules file names, each once")
    if not isinstance(cites, dict) or not all(_NAME_RE.fullmatch(k) and _names(v) for k, v in cites.items()):
        raise ValueError(f"{TOPICS} needs [cites]: a rules file → the files it cites and is read without")
    return {"about": about.strip(), "min_engine": least, "bot_only": bot_only, "topics": topics, "cites": cites}


def _names(value: object) -> bool:
    return (isinstance(value, list) and all(isinstance(n, str) and _NAME_RE.fullmatch(n) for n in value)
            and len(set(value)) == len(value))


def cited(files: dict[str, bytes], name: str) -> set[str]:
    """The other rules files this one cites: by a rule's id — the id's letters are those of the
    file that defines such rules — or by the file's name."""
    owner: dict[str, str] = {}
    for other in rules_files(files):
        for prefix in set(_DEFINED_RE.findall(_text(files, RULES + other))):
            owner.setdefault(prefix, other)
    text = _text(files, RULES + name)
    found = {owner[p] for p in _ID_RE.findall(text) if p in owner}
    found |= {n for n in _FILE_RE.findall(text) if RULES + n in files and n != "README.md"}
    return found - {name}


def findings(files: dict[str, bytes]) -> list[str]:
    """Why the index of this release would send a chat wrong; empty when it would not."""
    try:
        data = load(files)
    except ValueError as exc:
        return [str(exc)]
    have = set(rules_files(files))
    found: list[str] = []
    in_topics: set[str] = set()
    for topic in data["topics"]:
        in_topics |= set(topic["files"])
        found += [f"the topic {topic['name']!r} names {n}, which is not in {RULES}"
                  for n in topic["files"] if n not in have]
    bot_only = set(data["bot_only"])
    found += [f"`bot_only` names {n}, which is not in {RULES}" for n in sorted(bot_only - have)]
    found += [f"{n} is in a topic and in `bot_only`" for n in sorted(in_topics & bot_only)]
    found += [f"{RULES}{n} is in no topic and not in `bot_only`: a chat would never be sent to it"
              for n in sorted(have - in_topics - bot_only)]
    if found:
        return found
    # Every citation that leaves a topic's set is a decision on record — and every record is needed.
    needed: set[tuple[str, str]] = set()
    for topic in data["topics"]:
        members = set(topic["files"])
        for name in topic["files"]:
            for other in sorted(cited(files, name) - members - bot_only):
                needed.add((name, other))
                if other not in data["cites"].get(name, []):
                    found.append(f"the topic {topic['name']!r} reads {name}, which cites {other}, and does not read "
                                 f"{other}: add it to the topic's files, or to [cites] of {name} if it is read without")
    for name, others in sorted(data["cites"].items()):
        found += [f"[cites] lets {name} go without {other}, and no topic needs that any more: drop it"
                  for other in others if (name, other) not in needed]
        if not others:
            found.append(f"[cites] has a line for {name} that lets it go without nothing: drop it")
    found += readme_findings(files, data)
    return list(dict.fromkeys(found))


def readme_rows(files: dict[str, bytes]) -> dict[str, str]:
    """File name → the «When» cell of its row in the table of engine/rules/README.md."""
    rows: dict[str, str] = {}
    for line in _text(files, README).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("| `") and len(cells) == 3:
            rows[cells[0].strip("`")] = cells[2]
    return rows


def says(cell: str, when: str) -> bool:
    """Whether a «When» cell of the table says `when`, word for word: as the whole cell, as one
    of its parts between «;» — the chat's own part without its «chat: » —, or as what stands
    before a «:» that goes on to say more."""
    parts = [cell, *(part.strip() for part in cell.split(";") if not part.strip().startswith("bot:"))]   # the bot's part is no chat's
    parts += [part[len("chat: "):] for part in parts if part.startswith("chat: ")]
    heads = {part.split(":", 1)[0] for part in parts if ":" in part} - {"chat", "bot"}      # who reads is no «when»
    return bool(when) and (when in parts or when in heads)


def readme_findings(files: dict[str, bytes], data: dict) -> list[str]:
    """Where the table people read and the topics a chat reads have parted: a rules file with no
    row, a row for a file that is gone, a topic whose `when` is not in the row of its own file."""
    rows = readme_rows(files)
    have = set(rules_files(files))
    listed = {name for name in rows if "/" not in name and name.endswith(".md")}
    found = [f"{README} has no row for {n}" for n in sorted(have - listed)]
    found += [f"{README} has a row for {n}, which is not in {RULES}" for n in sorted(listed - have)]
    for topic in data["topics"]:
        own = [n for n in topic["files"] if n in rows and says(rows[n], topic["when"])]
        if not own:
            found.append(f"the topic {topic['name']!r} is read «{topic['when']}», and no row of its files in "
                         f"{README} says so: the table and the topics have parted")
    return found


def repository(files: dict[str, bytes]) -> str:
    """The public repository of releases, as the release's own defaults name it."""
    try:
        name = tomllib.loads(_text(files, DEFAULTS))["release"]["manifests"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError):
        raise ValueError(f"{DEFAULTS} does not name the repository of releases (release.manifests)") from None
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+", name):
        raise ValueError(f"release.manifests in {DEFAULTS} is not an owner/name: {name!r}")
    return name


def index(files: dict[str, bytes], tag: str) -> str:
    """The index a chat reads, for the release `tag`: the topics as their source gives them.
    Whether they would send a chat wrong is `findings`, which `release cut` asks before it
    publishes; a source that does not parse is refused here too. The text itself is written
    out in one place, `stable.index_text` — the code that moves `stable` builds it again from
    the published topics and moves only to a release whose index is that text, or the text of
    the day the release was cut (`stable.INDEX_TEXTS`: words once published are kept)."""
    load(files)
    return stable.index_text(files[TOPICS], repository(files), tag)


def index_record(files: dict[str, bytes], tag: str) -> dict[str, str] | None:
    """What the manifest says of the index: its path beside `files/` and its sha256. None for a
    release without `engine/rules/topics.toml` — one cut before the index existed, whose manifest
    must come out again exactly as it was published."""
    if TOPICS not in files:
        return None
    return {"path": INDEX_FILE, "sha256": hashlib.sha256(index(files, tag).encode("utf-8")).hexdigest()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path("."), help="the template")
    ap.add_argument("--rev", default="HEAD", help="the template commit; an uncommitted file is not read")
    ap.add_argument("--tag", required=True, help="vMAJOR.MINOR.PATCH")
    args = ap.parse_args(argv)

    def git(*a: str) -> bytes:
        return subprocess.run(["git", "-C", str(args.repo), *a], check=True, capture_output=True).stdout

    try:
        commit = git("rev-parse", "--verify", "--quiet", f"{args.rev}^{{commit}}").decode().strip()
        files = {p: git("show", f"{commit}:{p}") for p in (DEFAULTS, *git(
            "ls-tree", "-r", "-z", "--name-only", commit, "--", RULES.rstrip("/")).decode("utf-8").split("\0")) if p}
        found = findings(files)
        if found:
            raise ValueError("; ".join(found[:10]))
        sys.stdout.write(index(files, args.tag))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

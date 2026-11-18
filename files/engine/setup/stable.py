"""Moves the branch `stable` of the public repository of releases (docs/DESIGN.md §5, «The
topic index and the branch stable»).

    python3 -I stable.py --repository OWNER/NAME                                  # the schedule
    python3 -I stable.py --repository OWNER/NAME --to v0.1.13 --reason "why"      # by hand
    python3 -I stable.py --repository OWNER/NAME --dry-run                        # say what it would do

A chat reads the rules of the engine in that repository. It is told one address that never
changes — the branch `stable`, the file `rules-index.md` — and the index it finds there names
its release and that release's files at its tag. This code decides which release that is.

**The schedule** moves `stable` to the newest release that is at least 7 days old — the delay
of the channel `stable` in a person's repository (`engine/checks/lock_check.py`), so the rules
a chat reads are never current before the engine may be: the user's repository takes the release at
its weekly update after the same 7 days, some days later. A release is
as old as the latest of three clocks: `published` in its manifest, which the factory wrote;
the commit its tag is on; and the commit of `main`'s own line that brought it in — the same
commit when the release was squashed, the merge when it came by a merge commit, whose date
GitHub writes, however the merged branch was built — so a manifest or a commit dated back
does not shorten the wait. The schedule
only moves forward.

**By hand** — `--to` with the reason, which is kept — for what cannot wait a week or must be
undone: a release moved to before it is 7 days old, or `stable` moved back to an older one.
Moving back, or `--to` the release `stable` is on already, withdraws every release newer than
it that exists at that moment: the schedule passes them over from then on, so it does not put
back the next day what a hand took away. A release cut later is not withdrawn; a withdrawn one
comes back only by `--to` it.

**The branch is a line of its own, never a tag's commit.** Every move is a new commit on top
of the last, so moving back is a commit too and the branch is never rewritten: it can be
protected against a forced push and against deletion without exception. Each commit holds the
release exactly as its tag has it — `rules-index.md`, `release-manifest.json`, `files/` — and
`stable.json`: the release and its commit, the withdrawn ones, when, how and why.

**A release is moved to only when it is whole**, read again here and not taken on the word of
the cut: its tag is on `main`; its manifest names it; `files/` is the manifest's files, byte
for byte, every path valid text; its index is the one the manifest holds the sha256 of, and is
the very text this code builds from the release's own topics (`engine/rules/topics.toml`
among its files) for this repository and this tag — in the words of today or in any words an
index was published in before (`INDEX_TEXTS`: a release keeps the words it was cut with) — so
the index says nothing its topics do not, and a line of the topics carries no slash, no «@» and
nothing of a tag's shape (`one_line` says what that keeps out and what it does not) — and names
only files the release has; what
`main` took is what the tag holds; and the commit that brought it to `main` changes nothing
but those three. A release cut
before the index existed is passed over — quietly while no release was ever moved to, as a
finding once one was. Any release newer than the one `stable` is on that is not whole makes
the run red at once, due or not: a tag is never taken back, so it stays red until a hand
withdraws that release.

**A branch that is not what this code would have left** is not built on by the schedule:
other entries than a release's and the record; a record that is not of the form, names a
release that has no tag or another commit than its tag's, or withdraws what does not exist; a
release that is not its tag's or not whole; a merge; a move the record puts on the schedule
that the schedule would not have made — before the release was due, at a time still to come,
backwards, or with another list of withdrawn releases than the commit before had. A move by
hand repairs it, on top of what was pushed, and withdraws every release newer than the one it
moves to — after a push to the branch nothing newer goes out until a hand moves to it. The limit: a whole release pushed there with a record that says
«by hand» is what a hand leaves — who may push to the branch is that repository's ruleset's
to hold, not this code's.

This file is run from the repository of releases' own copy, `.github/scripts/stable.py`, which
a pull request there brings and a release never does: the code that decides when a release is
trusted must not come with the release it judges. So it stands alone — the standard library
and git, nothing of the engine — `release cut` refuses a release while the copy and this file
differ, and it is run with `-I`, so no file beside the copy is imported in a module's place.

Exit 0 — moved, or nothing to move; 1 — a finding: a release or the branch is not as it must
be (a whole release older than a broken one is still moved to, and the run is red all the
same; `--dry-run` exits as the run would); 2 — the run itself failed, or this code died on
what it did not expect: a run that died judged nothing, so it is never a 1. Its last line says
so, after everything said before it — the workflow keeps both streams in one log. (A copy that
does not load at all never gets here: Python ends with 1 and no «STABLE finding» line, which
the workflow takes for a failed run as well.)

Every line it prints is one line (`shown`), what the command line is refused for among them:
what it says of a release carries the release's own words — a path, a name out of a manifest —
and a line break among them would start a line of its own, in this code's voice. What a person is told to do names the fields of the form the
workflow is started with, «to» and «reason», beside the flags they become.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DELAY = timedelta(days=7)                   # the channel `stable`: lock_check.CHANNEL_DELAY (a test holds them equal)
TAG_RE = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
PUBLISHED_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
BRANCH = "stable"
MAIN = "main"
MANIFEST_FILE = "release-manifest.json"
INDEX_FILE = "rules-index.md"
FILES = "files"
STATE_FILE = "stable.json"
KEPT = (FILES, MANIFEST_FILE, INDEX_FILE)   # what a commit of `stable` holds of its release, as the tag has it
RULES = "engine/rules/"
TOPICS = RULES + "topics.toml"              # the source of the index, a file of the release
REGULAR = "100644"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
SCHEDULE, HAND = "the schedule", "by hand"
NOT_LEFT = "a branch this code did not leave"   # what a repair says it came from: the branch was there, and on no release this code can name
RECORD = frozenset({"release", "commit", "moved", "how", "reason", "withdrawn"})
_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+")
_RULE_NAME = re.compile(r"[a-z][a-z0-9-]*\.md")
_TOPIC_NAME = re.compile(r"[a-z][a-z0-9]*( [a-z0-9]+)*")
_ANY_TAG = re.compile(r"v\s?\d+\.\d+\.\d+", re.IGNORECASE)        # a tag's shape, in any script's digits
NO_SIGNS = frozenset("|/\\@<>\u2215\u2044\u29f8")   # no table cell's bar, no slash or its look-alikes, no «@», no angle bracket
NO_KINDS = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")      # controls, format marks, separators of lines, unassigned
BLANKS = frozenset("\u3164\u115f\u1160\uffa0\u2800")      # letters and signs that print as nothing
SKEW = timedelta(minutes=5)                 # two runners' clocks may differ by this much
TOPIC_KEYS = frozenset({"name", "when", "files"})
TOP_KEYS = frozenset({"about", "min_engine", "bot_only", "topic", "cites"})
WHO = ("-c", "user.name=stable", "-c", "user.email=stable@memory-engine.invalid")     # nobody's address: a reserved domain


class Failed(RuntimeError):
    """The run cannot go on: git failed, or the checkout is not one this code can judge from."""


class Refused(ValueError):
    """A release that is not moved to. `before_index` — it was cut before the topic index
    existed: passed over, no finding."""

    def __init__(self, message: str, before_index: bool = False):
        super().__init__(message)
        self.before_index = before_index


class Broken(ValueError):
    """The branch `stable` is not what this code left."""


def git(repo: Path, *args: str, stdin: bytes | None = None, env: dict[str, str] | None = None) -> bytes:
    proc = subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, env=env)
    if proc.returncode != 0:
        raise Failed(f"git {' '.join(args[:3])}: {proc.stderr.decode(errors='replace').strip()[:300]}")
    return proc.stdout


def key(tag: str) -> tuple[int, int, int]:
    m = TAG_RE.fullmatch(tag) if isinstance(tag, str) else None
    if m is None:
        raise ValueError(f"{tag!r} is not a release tag vMAJOR.MINOR.PATCH")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime(PUBLISHED_FORMAT)


def shown(text: object) -> str:
    """The text on one line, as this code prints it: every character that does not print — a
    line break of any kind, a control, a format mark, half of a pair — is written out as its
    escape. A release's own words go into what is said of it, and the line that starts «STABLE
    moved» or «STABLE finding» must be this code's."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in str(text))


def one_line(text: object) -> bool:
    """One plain line an index may carry from the topics: text with no line break, no bar of a
    table cell, no slash, «@» or angle bracket — in any width or look-alike form —, nothing of
    the shape of a release's tag, and no character that is a control or a format mark. That
    keeps a link, a path, an `owner/name` and a tag out of a line. It does not keep out a bare
    domain name, a release written in words or without its «v», or a sentence that tells the
    reader to do something: what a line says is still read by whoever reads the topics' pull
    request."""
    if not isinstance(text, str) or not text or text != text.strip():
        return False
    wide = unicodedata.normalize("NFKC", text)
    return (not NO_SIGNS & (set(text) | set(wide)) and not _ANY_TAG.search(wide)
            and not any(unicodedata.category(c) in NO_KINDS for c in text))


def _index_1(data: dict, repository: str, tag: str) -> str:
    """The index in its first words: those the first release with an index is cut in. From that
    release on it is kept as it is (`INDEX_TEXTS`): every word it writes, and what it asks of
    the topics before it writes."""
    if set(data) - TOP_KEYS:
        raise Refused(f"{TOPICS} has keys this code does not know: {', '.join(sorted(set(data) - TOP_KEYS)[:5])}")
    about, topics, least = data.get("about"), data.get("topic"), data.get("min_engine")
    if not isinstance(about, str) or not one_line(about.strip()):
        raise Refused(f"{TOPICS} needs `about`: one plain paragraph — no line break, no address, no path, no release")
    if not isinstance(least, str) or not TAG_RE.fullmatch(least):
        raise Refused(f"{TOPICS} needs `min_engine`: the oldest release of the engine these rules are written for")
    if key(least) > key(tag):
        raise Refused(f"{TOPICS} names the minimal engine {least}, newer than {tag}: rules cannot need an engine "
                      f"that their own release does not bring")
    if not isinstance(topics, list) or not topics:
        raise Refused(f"{TOPICS} names no topic")
    rows, seen = [], set()
    for topic in topics:
        if not isinstance(topic, dict) or set(topic) != TOPIC_KEYS:
            raise Refused(f"a topic in {TOPICS} needs exactly `name`, `when` and `files`")
        name, when, names = topic["name"], topic["when"], topic["files"]
        if not isinstance(name, str) or not _TOPIC_NAME.fullmatch(name) or name in seen:
            raise Refused(f"a topic's `name` in {TOPICS} is a few plain words, each topic its own: {name!r}")
        seen.add(name)
        if not one_line(when):
            raise Refused(f"the topic {name!r} in {TOPICS} needs `when`: one plain line — no «|», no address, no path, no release")
        if (not isinstance(names, list) or not names or len(set(map(str, names))) != len(names)
                or not all(isinstance(n, str) and _RULE_NAME.fullmatch(n) for n in names)):
            raise Refused(f"the topic {name!r} in {TOPICS} needs `files`: rules file names, each once")
        rows.append(f"| {name} | {when} | " + " · ".join(f"`{FILES}/{RULES}{n}`" for n in names) + " |")
    return "\n".join([
        f"# Topic rules — release {tag}",
        "",
        f"The rules of release {tag}: which files to read, and when. Read them in the repository "
        f"`{repository}` at the tag `{tag}` — ref `{tag}`. Not at the branch this index came "
        f"from: the branch moves on to the next release, the tag never moves.",
        "",
        f"These rules are written for the engine of release `{least}` or newer in her repository. That line "
        f"is for her repository's checks: a chat cannot see her repository and does nothing about it.",
        "",
        "Before you answer or act on a topic, read every file of its line in full — all of them, "
        "once a conversation. The files name each other's rules; read the ones this index lists, "
        "not the ones a file mentions.",
        "",
        about.strip(),
        "",
        "| Topic | When | Files — all of them |",
        "|---|---|---|",
        *rows]) + "\n"


def _index_2(data: dict, repository: str, tag: str) -> str:
    """The index in its second words: the first text's, with the person who uses the engine called
    «the user» (the owner's decision, 18.11.2026). From the first release cut in them on, kept as
    it is (`INDEX_TEXTS`), like the first."""
    if set(data) - TOP_KEYS:
        raise Refused(f"{TOPICS} has keys this code does not know: {', '.join(sorted(set(data) - TOP_KEYS)[:5])}")
    about, topics, least = data.get("about"), data.get("topic"), data.get("min_engine")
    if not isinstance(about, str) or not one_line(about.strip()):
        raise Refused(f"{TOPICS} needs `about`: one plain paragraph — no line break, no address, no path, no release")
    if not isinstance(least, str) or not TAG_RE.fullmatch(least):
        raise Refused(f"{TOPICS} needs `min_engine`: the oldest release of the engine these rules are written for")
    if key(least) > key(tag):
        raise Refused(f"{TOPICS} names the minimal engine {least}, newer than {tag}: rules cannot need an engine "
                      f"that their own release does not bring")
    if not isinstance(topics, list) or not topics:
        raise Refused(f"{TOPICS} names no topic")
    rows, seen = [], set()
    for topic in topics:
        if not isinstance(topic, dict) or set(topic) != TOPIC_KEYS:
            raise Refused(f"a topic in {TOPICS} needs exactly `name`, `when` and `files`")
        name, when, names = topic["name"], topic["when"], topic["files"]
        if not isinstance(name, str) or not _TOPIC_NAME.fullmatch(name) or name in seen:
            raise Refused(f"a topic's `name` in {TOPICS} is a few plain words, each topic its own: {name!r}")
        seen.add(name)
        if not one_line(when):
            raise Refused(f"the topic {name!r} in {TOPICS} needs `when`: one plain line — no «|», no address, no path, no release")
        if (not isinstance(names, list) or not names or len(set(map(str, names))) != len(names)
                or not all(isinstance(n, str) and _RULE_NAME.fullmatch(n) for n in names)):
            raise Refused(f"the topic {name!r} in {TOPICS} needs `files`: rules file names, each once")
        rows.append(f"| {name} | {when} | " + " · ".join(f"`{FILES}/{RULES}{n}`" for n in names) + " |")
    return "\n".join([
        f"# Topic rules — release {tag}",
        "",
        f"The rules of release {tag}: which files to read, and when. Read them in the repository "
        f"`{repository}` at the tag `{tag}` — ref `{tag}`. Not at the branch this index came "
        f"from: the branch moves on to the next release, the tag never moves.",
        "",
        f"These rules are written for the engine of release `{least}` or newer in the user's repository. That "
        f"line is for that repository's checks: a chat cannot see that repository and does nothing about it.",
        "",
        "Before you answer or act on a topic, read every file of its line in full — all of them, "
        "once a conversation. The files name each other's rules; read the ones this index lists, "
        "not the ones a file mentions.",
        "",
        about.strip(),
        "",
        "| Topic | When | Files — all of them |",
        "|---|---|---|",
        *rows]) + "\n"


# Every text an index was ever published in, oldest first; the last is what `release cut`
# publishes now. A release is whole only while the index it published is the text this code
# builds from its topics, byte for byte — so a text that is edited here, by one word, refuses
# every release cut with it: the branch is then on a release that «is not whole», every night is
# red and no move back is possible (found on fixtures by the reading of the change that
# brought this code to the repository of releases). So, from the first release cut with an
# index on, a text is never edited and never taken out: new words are a new function, added at
# the end. A test holds each text to its fingerprint; the repository of releases reads a
# changed copy of this file against the releases that are out and is red when one would be
# refused (`copy-holds.py` there — a check of the pull request, which its reader reads). What
# the texts lean on together — `one_line`, the keys and the patterns of names, the paths,
# `index_names` — is not kept per text: narrowing it refuses the releases that no longer pass,
# and that same check there is what shows it before the copy is merged. Any kept text is taken
# for any release, whenever it was cut: a text is not bound to the releases of its day.
INDEX_TEXTS = (_index_1, _index_2)


def _source(source: bytes, repository: str, tag: str) -> dict:
    """The topics as their file gives them, read and no more: what form they must have is each
    text's own to ask. Refused for a repository or a file that cannot be read as one."""
    if not _REPOSITORY.fullmatch(repository):
        raise Refused(f"{repository!r} is not a repository's owner/name")
    key(tag)
    try:
        return tomllib.loads(source.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError):
        raise Refused(f"{TOPICS} cannot be read") from None


def index_texts(source: bytes, repository: str, tag: str) -> list[str]:
    """Every text the topics of the release `tag` give as its index, the newest words first: one
    for each of `INDEX_TEXTS` that takes these topics. Refused, with the reason of the newest,
    when none does. A text that does not take the topics is passed over, however it says so —
    one that dies on them too (a newer text reading a key older topics lack, a number no text
    can print): were its death the answer, the older words would never be tried, and the
    releases cut in them would be refused all the same."""
    data = _source(source, repository, tag)
    texts: list[str] = []
    why: Refused | None = None
    for build in reversed(INDEX_TEXTS):
        try:
            texts.append(_built(build, data, repository, tag))
        except Refused as exc:
            why = why or exc
    if not texts:
        raise why or Refused(f"{TOPICS} gives no index: this code holds no text to write one in")
    return texts


def _built(build, data: dict, repository: str, tag: str) -> str:
    """The text `build` writes from the topics — or Refused, whatever it died of on them."""
    try:
        return build(data, repository, tag)
    except Refused:
        raise
    except (ValueError, OverflowError, RecursionError, KeyError, TypeError, IndexError) as exc:       # as `inspect` has them
        raise Refused(f"{TOPICS} is not what an index is written from ({type(exc).__name__})") from None


def index_text(source: bytes, repository: str, tag: str) -> str:
    """The topic index of the release `tag`, from its topics (`engine/rules/topics.toml`), for a
    reader in `repository`, in the words of today — the last of `INDEX_TEXTS`. The one place the
    index is written out: `release cut` publishes exactly this text (`engine/setup/topics.py`),
    and a release is moved to only when the index it published is a text this code builds from
    its topics again — so an index says nothing its topics do not say, and names no repository
    but its own and no release but its own and the minimal engine its topics name, which is
    never newer than itself. Refused when the source is not of the form the words of today ask."""
    return _built(INDEX_TEXTS[-1], _source(source, repository, tag), repository, tag)


def index_names(text: str) -> list[str]:
    """The paths under `files/` an index names, as the lines of its table give them. One reading
    for every text of `INDEX_TEXTS`, because every text so far writes a path this way; a text
    that wrote it another way would need its own reading here — without one, the check that a
    release has every file its index names would pass that text by."""
    return sorted(set(re.findall(r"`" + FILES + r"/(" + re.escape(RULES) + r"[a-z][a-z0-9-]*\.md)`", text)))


@dataclass(frozen=True)
class Release:
    tag: str
    commit: str
    due: datetime                           # from when the schedule may move to it
    kept: dict[str, tuple[str, str, str]]   # name → (mode, type, sha) of what `stable` holds of it


@dataclass(frozen=True)
class State:
    tip: str                                # the commit `stable` is on
    release: str
    withdrawn: tuple[str, ...]


@dataclass(frozen=True)
class Origin:
    repository: str                         # this repository's owner/name: what an index must name
    tags: dict[str, str]                    # release tag → its commit
    tip: str | None                         # the commit of `stable`, or None
    line: tuple[str, ...]                   # the commits of main's own line (first parents), newest first


def origin(repo: Path, repository: str) -> Origin:
    """The tags, `stable` and `main` as origin has them now — read from origin, never from what
    the checkout happens to hold — with their objects fetched, whole: a shallow checkout cannot
    say whether a tag is on `main`. A tag of a release's name on what is no commit is no
    release's tag and is left out."""
    listing = git(repo, "ls-remote", "origin", "refs/tags/v*", f"refs/heads/{BRANCH}").decode("utf-8", "replace")
    tags: dict[str, str] = {}
    peeled: dict[str, str] = {}
    tip = None
    for line in listing.splitlines():
        sha, _, ref = line.partition("\t")
        if not _HEX40.fullmatch(sha):
            continue
        if ref == f"refs/heads/{BRANCH}":
            tip = sha
        elif ref.startswith("refs/tags/"):
            name = ref[len("refs/tags/"):]
            if name.endswith("^{}") and TAG_RE.fullmatch(name[:-3]):
                peeled[name[:-3]] = sha     # an annotated tag: the commit it names
            elif TAG_RE.fullmatch(name):
                tags[name] = sha
    tags.update(peeled)
    refs = [f"+refs/heads/{MAIN}:refs/remotes/origin/{MAIN}", "+refs/tags/v*:refs/stable-run/tags/v*"]
    if tip is not None:
        refs.append(f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")
    git(repo, "fetch", "--quiet", "--no-tags", "origin", *refs)
    if git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true":
        git(repo, "fetch", "--quiet", "--no-tags", "--unshallow", "origin", *refs)
    kinds = git(repo, "cat-file", "--batch-check=%(objecttype)", stdin="".join(f"{sha}\n" for sha in tags.values()).encode())
    tags = {tag: sha for (tag, sha), kind in zip(tags.items(), kinds.decode().split("\n")) if kind == "commit"}
    line = git(repo, "rev-list", "--first-parent", f"refs/remotes/origin/{MAIN}").decode().split()
    return Origin(repository, tags, tip, tuple(line))


def top(repo: Path, rev: str) -> dict[str, tuple[str, str, str]]:
    """Name → (mode, type, sha) of every entry at the top of rev's tree."""
    entries: dict[str, tuple[str, str, str]] = {}
    for entry in git(repo, "ls-tree", "-z", rev).split(b"\0"):
        if entry:
            meta, name = entry.split(b"\t", 1)
            mode, kind, sha = meta.decode().split(" ")
            entries[name.decode("utf-8", "backslashreplace")] = (mode, kind, sha)
    return entries


def blob(repo: Path, entries: dict[str, tuple[str, str, str]], name: str, of: str) -> bytes:
    entry = entries.get(name)
    if entry is None:
        raise Refused(f"{of} has no {name}")
    if entry[:2] != (REGULAR, "blob"):
        raise Refused(f"{name} of {of} is not a regular file")
    return git(repo, "cat-file", "blob", entry[2])


def committed(repo: Path, commit: str) -> datetime:
    """When the commit was made, by its committer's clock — the seconds, not the zone it wrote.
    Refused when the seconds are no date a calendar has."""
    try:
        return datetime.fromtimestamp(int(git(repo, "show", "-s", "--format=%ct", commit).decode().strip()), timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise Refused(f"the commit {commit[:12]} is dated outside the calendar") from None


def entered(repo: Path, at: Origin, tag: str, commit: str) -> str:
    """The commit of main's own line that brought the release in: the tagged commit itself when
    the release was squashed or pushed there, else the oldest commit of that line the tagged
    one is an ancestor of — the merge that brought its branch, however the branch was built (a
    tag set by hand on a commit of the branch, dated by its author; that repository's workflow
    `tag` tags the merge itself). Refused for a tag that is on no line of `main`."""
    if commit in at.line:
        return commit
    try:
        after = set(git(repo, "rev-list", "--ancestry-path", f"{commit}..refs/remotes/origin/{MAIN}").decode().split())
    except Failed:
        after = set()
    for candidate in reversed(at.line):                 # oldest first
        if candidate in after:
            return candidate
    raise Refused(f"the tag {tag} is not on {MAIN}: a release is one that was merged")


def inspect(repo: Path, at: Origin, tag: str) -> Release:
    """The release at `tag`, whole — or Refused with the reason. Whatever a release holds, the
    answer is one of the two: content this code cannot make sense of is a reason, never a
    crash — a run that died on a release would move nothing ever after."""
    try:
        return _inspect(repo, at, tag)
    except Refused:
        raise
    except (ValueError, OverflowError, RecursionError, KeyError, TypeError, IndexError) as exc:
        raise Refused(f"{tag} cannot be judged ({type(exc).__name__})") from None


def _inspect(repo: Path, at: Origin, tag: str) -> Release:
    commit = at.tags[tag]
    merge = entered(repo, at, tag, commit)
    entries = top(repo, commit)
    raw = blob(repo, entries, MANIFEST_FILE, tag)
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Refused(f"{MANIFEST_FILE} of {tag} cannot be read") from None
    if not isinstance(manifest, dict) or manifest.get("release") != tag:
        raise Refused(f"{MANIFEST_FILE} at the tag {tag} does not name {tag}")
    listed = manifest.get("files")
    if not isinstance(listed, dict) or not listed or not all(
            isinstance(p, str) and isinstance(h, str) and _HEX64.fullmatch(h) for p, h in listed.items()):
        raise Refused(f"{MANIFEST_FILE} of {tag} lists no files with their sha256")
    try:
        published = datetime.strptime(manifest.get("published"), PUBLISHED_FORMAT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise Refused(f"{MANIFEST_FILE} of {tag} has no valid «published» time") from None
    record = manifest.get("index")
    if record is None:
        raise Refused(f"{tag} was cut before the topic index existed", before_index=True)
    have: dict[str, str] = {}
    content: dict[str, bytes] = {}
    for entry in git(repo, "ls-tree", "-r", "-z", commit, "--", f"{FILES}/").split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode, _kind, sha = meta.decode().split(" ")
        try:
            name = path.decode("utf-8")[len(FILES) + 1:]
        except UnicodeDecodeError:
            raise Refused(f"a path under {FILES}/ of {tag} is not valid UTF-8") from None
        if mode != REGULAR:
            raise Refused(f"{FILES}/{name} of {tag} has mode {mode}; a release holds regular files only")
        data = git(repo, "cat-file", "blob", sha)
        have[name] = hashlib.sha256(data).hexdigest()
        if name == TOPICS:
            content[name] = data
    if have != listed:
        differ = sorted(set(have) ^ set(listed) | {p for p in set(have) & set(listed) if have[p] != listed[p]})
        raise Refused(f"{FILES}/ of {tag} is not its manifest's files: {', '.join(differ[:5])}")
    if not isinstance(record, dict) or set(record) != {"path", "sha256"} or record["path"] != INDEX_FILE:
        raise Refused(f"{MANIFEST_FILE} of {tag} does not name its index as {INDEX_FILE} with a sha256")
    raw = blob(repo, entries, INDEX_FILE, tag)
    if not isinstance(record["sha256"], str) or hashlib.sha256(raw).hexdigest() != record["sha256"]:
        raise Refused(f"{INDEX_FILE} of {tag} is not the one its manifest holds")
    if TOPICS not in content:
        raise Refused(f"{tag} has an index and no {TOPICS} to hold it to")
    try:
        texts = index_texts(content[TOPICS], at.repository, tag)
    except Refused as exc:
        raise Refused(f"the topics of {tag} give no index: {exc}") from None
    text = next((t for t in texts if raw == t.encode("utf-8")), None)
    if text is None:
        raise Refused(f"{INDEX_FILE} of {tag} is not the index of its own topics for {at.repository}: it was "
                      f"edited, or built for another repository or release")
    missing = [f"{FILES}/{name}" for name in index_names(text) if name not in listed]
    if missing:
        raise Refused(f"{INDEX_FILE} of {tag} names files the release does not have: {', '.join(missing[:5])}")
    parent = git(repo, "rev-list", "--parents", "-n", "1", merge).decode().split()[1:2]
    brought = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-z", parent[0] if parent else EMPTY_TREE,
                  merge).decode("utf-8", "backslashreplace").split("\0")
    more = sorted(n for n in brought if n and n not in KEPT)
    if more:
        raise Refused(f"the commit that brought {tag} to {MAIN} changes more than a release: {', '.join(more[:5])}")
    kept = {name: entries[name] for name in KEPT}
    if kept[FILES][:2] != ("040000", "tree"):
        raise Refused(f"{FILES} of {tag} is not a folder")
    landed = top(repo, merge)
    differ = [name for name in KEPT if landed.get(name) != kept[name]]
    if differ:
        raise Refused(f"{MAIN} took another {', '.join(differ)} than the tag {tag} holds: the tag is not on the "
                      f"release as it was merged")
    return Release(tag, commit, max(published, committed(repo, commit), committed(repo, merge)) + DELAY, kept)


def read_record(repo: Path, entries: dict[str, tuple[str, str, str]]) -> dict:
    """`stable.json` of a commit of the branch, its form checked — or Broken."""
    try:
        data = json.loads(blob(repo, entries, STATE_FILE, f"`{BRANCH}`").decode("utf-8"))
        if not isinstance(data, dict) or set(data) != RECORD or data["how"] not in (SCHEDULE, HAND):
            raise TypeError
        if not isinstance(data["withdrawn"], list) or not isinstance(data["reason"], str):
            raise TypeError
        key(data["release"]), [key(t) for t in data["withdrawn"]]
        data["moved"] = datetime.strptime(data["moved"], PUBLISHED_FORMAT).replace(tzinfo=timezone.utc)
    except (Refused, UnicodeDecodeError, ValueError, KeyError, TypeError, RecursionError):
        raise Broken(f"{STATE_FILE} of `{BRANCH}` cannot be read") from None
    return data


def state(repo: Path, at: Origin, tip: str | None, now: datetime) -> State | None:
    """What `stable` is on, from the commit `tip`; None when there is no such branch yet. Broken
    when the commit is not one this code would have left: other entries than a release's and
    the record; a record that cannot be read, names a release with no tag or another commit
    than its tag's, or withdraws what does not exist; a release that is not its tag's, or not
    whole; a move dated at a time still to come; and a move the record puts on the schedule
    that the schedule would not have made — before the release was due, backwards, on top of a
    commit that is no move, or with another list of withdrawn releases than the commit before
    it had. What a record puts on a hand this code cannot tell from a hand."""
    if tip is None:
        return None
    entries = top(repo, tip)
    if set(entries) != {*KEPT, STATE_FILE}:
        raise Broken(f"`{BRANCH}` holds {', '.join(sorted(entries)) or 'nothing'}, not {', '.join(sorted({*KEPT, STATE_FILE}))}")
    record = read_record(repo, entries)
    release, withdrawn = record["release"], record["withdrawn"]
    if release not in at.tags:
        raise Broken(f"`{BRANCH}` is on {release}, which has no tag")
    if record["commit"] != at.tags[release]:
        raise Broken(f"`{BRANCH}` says {release} is the commit {str(record['commit'])[:12]}, and its tag is on another")
    unknown = sorted(set(withdrawn) - set(at.tags), key=key)
    if unknown or release in withdrawn:
        raise Broken(f"`{BRANCH}` withdraws {', '.join(unknown) or release}, which {'has no tag' if unknown else 'it is on'}")
    held = top(repo, at.tags[release])
    differ = [name for name in KEPT if entries[name] != held.get(name)]
    if differ:
        raise Broken(f"`{BRANCH}` says it is on {release}, and its {', '.join(differ)} is not that release's")
    try:
        whole = inspect(repo, at, release)
    except Refused as exc:
        raise Broken(f"`{BRANCH}` is on {release}, which is not a whole release: {exc}") from None
    parents = git(repo, "rev-list", "--parents", "-n", "1", tip).decode().split()[1:]
    if len(parents) > 1:
        raise Broken(f"`{BRANCH}` is on a merge: every move is one commit on top of the last")
    if record["moved"] > now + SKEW:
        raise Broken(f"`{BRANCH}` says it was moved to {release} at {stamp(record['moved'])}, a time still to come")
    if record["how"] == SCHEDULE:
        if record["moved"] < whole.due:
            raise Broken(f"`{BRANCH}` says the schedule moved it to {release} at {stamp(record['moved'])}, before "
                         f"{release} was due at {stamp(whole.due)}")
        if parents:
            try:
                under = top(repo, parents[0])
                if set(under) != {*KEPT, STATE_FILE}:
                    raise Broken("other entries")
                before = read_record(repo, under)
            except (Broken, Refused):
                raise Broken(f"`{BRANCH}` says the schedule moved it on top of a commit that is not a move") from None
            if key(before["release"]) >= key(release) or set(before["withdrawn"]) != set(withdrawn):
                raise Broken(f"`{BRANCH}` says the schedule moved it from {before['release']} to {release}, "
                             f"which the schedule does not do: it moves forward and withdraws nothing")
    return State(tip, release, tuple(sorted(set(withdrawn), key=key)))


def by_schedule(repo: Path, at: Origin, now_on: State | None, now: datetime) -> tuple[Release | None, list[str], list[str]]:
    """(the release to move to or None, what to say, findings). Newest first: the first release
    that is whole and due is the one. A release that is not whole is a finding, due or not —
    one only cut before the index existed too, once `stable` is on a release or moves to one
    older than it; while the branch does not exist and nothing is moved to, such a release is
    only said."""
    said: list[str] = []
    found: list[str] = []
    quiet: list[str] = []
    floor = key(now_on.release) if now_on else (-1, -1, -1)
    withdrawn = set(now_on.withdrawn) if now_on else set()
    for tag in sorted((t for t in at.tags if key(t) > floor), key=key, reverse=True):
        if tag in withdrawn:
            said.append(f"{tag} is withdrawn: passed over")
            continue
        try:
            release = inspect(repo, at, tag)
        except Refused as exc:
            (quiet if exc.before_index else found).append(str(exc))
            continue
        if now < release.due:
            said.append(f"{tag} is due at {stamp(release.due)}, not yet")
            continue
        return release, said, found + quiet     # a release without an index, newer than one moved to: a finding
    return None, said + ([] if now_on else quiet), found + (quiet if now_on else [])


def by_hand(repo: Path, at: Origin, now_on: State | None, to: str, repair: bool = False) -> tuple[Release, tuple[str, ...]]:
    """(the release to move to, the releases withdrawn from then on). Refused when `to` is not a
    whole release. `repair` — the branch is not what this code left: every release newer than
    `to` is then withdrawn, whatever the branch says of itself — after a push to the branch
    nothing newer goes out until a hand moves to it. What the branch held withdrawn before the
    push is not looked for: anything newer than `to` is withdrawn anew, and what is older than
    `to` no move can reach without withdrawing it again."""
    if to not in at.tags:
        raise Refused(f"{to} has no tag in this repository")
    release = inspect(repo, at, to)
    withdrawn = set(now_on.withdrawn) if now_on else set()
    if repair or (now_on and key(to) <= key(now_on.release)):       # back, «stay here», or a repair: what is newer stays away
        withdrawn |= {t for t in at.tags if key(t) > key(to)}
    withdrawn.discard(to)
    return release, tuple(sorted(withdrawn, key=key))


def move(repo: Path, tip: str | None, release: Release, was: str | None, withdrawn: tuple[str, ...],
         how: str, reason: str, now: datetime, push: bool = True) -> str:
    """One more commit on `stable`: the release as its tag has it, and the record. Pushed without
    force, so a branch that moved meanwhile refuses it. Returns the commit. `was` — what the
    commit's first line says the branch came from: a release, «none» while there was no branch,
    or, for a repair, that the branch was not this code's (`NOT_LEFT`)."""
    record = {"release": release.tag, "commit": release.commit, "moved": stamp(now), "how": how, "reason": reason,
              "withdrawn": list(withdrawn)}
    text = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    record_sha = git(repo, "hash-object", "-w", "--stdin", stdin=text.encode()).decode().strip()
    listing = "".join(f"{mode} {kind} {sha}\t{name}\n" for name, (mode, kind, sha) in sorted(release.kept.items()))
    listing += f"{REGULAR} blob {record_sha}\t{STATE_FILE}\n"
    tree = git(repo, "mktree", stdin=listing.encode()).decode().strip()
    message = f"stable: {was or 'none'} -> {release.tag} ({how})\n\n{reason}\n"
    parent = ("-p", tip) if tip else ()
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp(now), "GIT_COMMITTER_DATE": stamp(now)}
    commit = git(repo, *WHO, "commit-tree", tree, *parent, "-m", message, env=env).decode().strip()
    if push:
        git(repo, "push", "--quiet", "origin", f"{commit}:refs/heads/{BRANCH}")
    return commit


def plain(reason: str) -> bool:
    """One line a person can read: 1 to 200 characters, a letter or a digit that shows among
    them, and none that is a control, a format mark, a line or paragraph separator, a mark that
    only sits on another character, or a character that prints as nothing."""
    return (0 < len(reason) <= 200 and any(c.isalnum() and c not in BLANKS for c in reason)
            and not any(unicodedata.category(c) in NO_KINDS or unicodedata.category(c).startswith("M") or c in BLANKS
                        for c in reason))


class _Arguments(argparse.ArgumentParser):
    """What the command line is refused for, on one line: argparse repeats a stranger's argument
    as it came, line breaks and all."""

    def error(self, message: str):
        _error(f"ERROR {message}")
        raise SystemExit(2)


def _error(text: str) -> None:
    """The line of a run that failed, after everything the run said before it: the workflow reads
    both streams as one, and a stream that is not a terminal keeps its lines until it is full —
    without this the error stood above the lines it speaks of. A stream that is closed or not
    there is passed over: saying the error must not be what ends the run, with another code."""
    for stream, line in ((sys.stdout, None), (sys.stderr, shown(text))):
        try:                                # a stream closed before the start is None, one closed since refuses
            if line is not None:
                stream.write(line + "\n")
            stream.flush()
        except (OSError, ValueError, AttributeError):
            pass


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    ap = _Arguments(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path("."), help="a checkout of the repository of releases")
    ap.add_argument("--repository", required=True, help="that repository's owner/name: the one an index must name")
    ap.add_argument("--to", help="by hand: the release to move `stable` to, vMAJOR.MINOR.PATCH")
    ap.add_argument("--reason", help="by hand: why, in one line — it is kept on the branch")
    ap.add_argument("--dry-run", action="store_true", help="say what would be done; commit and push nothing")
    args = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    for stream in (sys.stdout, sys.stderr):     # a release's own words are printed: none of them may stop the run
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    try:
        if not _REPOSITORY.fullmatch(args.repository):
            raise Failed(f"--repository {args.repository!r} is not an owner/name")
        if (args.to is None) != (args.reason is None):
            raise Failed("the fields «to» and «reason» (--to, --reason) go together: a move by hand names its "
                         "release and says why")
        if args.to is not None and not TAG_RE.fullmatch(args.to):
            raise Failed(f"the field «to» (--to) {args.to!r} is not a release tag vMAJOR.MINOR.PATCH")
        if args.reason is not None and not plain(args.reason.strip()):
            raise Failed("the field «reason» (--reason) is one line a person can read, 1 to 200 characters")
        at = origin(args.repo, args.repository)
        found: list[str] = []
        repaired = False
        try:
            now_on = state(args.repo, at, at.tip, now)
        except Broken as exc:
            if args.to is None:
                print(shown(f"STABLE finding: {exc} — the schedule does not move a branch it did not leave; "
                            f"repair it by one run by hand: the field «to» (--to) — the release the branch "
                            f"should be on, the field «reason» (--reason) — why, in one line"))
                return 1
            now_on, repaired = None, True
            print(shown(f"STABLE note: {exc} — not as this code left it; the move asked for repairs it if it is made, "
                        f"and withdraws every release newer than {args.to}"))
        was = now_on.release if now_on else None
        came_from = NOT_LEFT if repaired else was or "none"     # what the move says it came from
        if args.to is not None:
            try:
                release, withdrawn = by_hand(args.repo, at, now_on, args.to, repaired)
            except Refused as exc:
                print(shown(f"STABLE finding: not moved to {args.to}: {exc}"))
                return 1
            how, reason = HAND, args.reason.strip()
            if now_on and not repaired and release.tag == was and withdrawn == now_on.withdrawn:
                print(shown(f"STABLE stays on {was}: it is there already, nothing newer to withdraw"))
                return 0
        else:
            release, said, found = by_schedule(args.repo, at, now_on, now)
            for line in said:
                print(shown(f"STABLE note: {line}"))
            for line in found:
                print(shown(f"STABLE finding: {line}"))
            if release is None:
                print(shown(f"STABLE stays on {was or 'no release — the branch does not exist yet'}: nothing to move to"))
                return 1 if found else 0
            how, reason = SCHEDULE, f"{release.tag} is whole and at least {DELAY.days} days old"
            withdrawn = now_on.withdrawn if now_on else ()
        if args.dry_run:
            print(shown(f"STABLE would move: {came_from} -> {release.tag} ({how}); "
                        f"withdrawn: {', '.join(withdrawn) or 'none'}"))
            return 1 if found else 0
        commit = move(args.repo, at.tip, release, came_from, withdrawn, how, reason, now)
        print(shown(f"STABLE moved: {came_from} -> {release.tag} ({how}), commit {commit}; "
                    f"withdrawn: {', '.join(withdrawn) or 'none'}"))
        return 1 if found else 0
    except Failed as exc:
        _error(f"ERROR {exc}")
        return 2
    except Exception as exc:        # not a finding: a 1 says a release or the branch was judged, and this run judged nothing
        at = exc.__traceback__
        while at is not None and at.tb_next is not None:
            at = at.tb_next
        where = f", at {Path(at.tb_frame.f_code.co_filename).name} line {at.tb_lineno}" if at is not None else ""
        _error(f"ERROR this code died on what it did not expect ({type(exc).__name__}: {str(exc)[:300]}{where}): "
               f"the run failed — what it said before this line is not its answer")
        return 2


if __name__ == "__main__":
    sys.exit(main())

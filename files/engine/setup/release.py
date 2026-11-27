"""A release of the engine: its files in a person's repository and its manifest
(docs/DESIGN.md §4, §5).

    python -m engine.setup.release manifest --rev COMMIT --tag v0.1.0 > release-manifest.json
    python -m engine.setup.release cut --rev COMMIT --tag v0.1.6 --into RELEASES_CHECKOUT
    python -m engine.setup.release install --rev COMMIT --tag v0.1.0 --into PERSON_REPO
    python -m engine.setup.release template --rev COMMIT --tag v0.1.12 --releases RELEASES_CHECKOUT \
        --into TEMPLATE_CHECKOUT

A release is the template's `engine/` at one commit, read as git objects: an untracked
or uncommitted file never ships. Each `engine/workflows/<name>` ships a second time as
`.github/workflows/<name>`, where GitHub runs it. The manifest maps every path the
release puts into a person's repository to its sha256 — exactly the set C1 holds
`engine/` and `.github/` to. It is published as `release-manifest.json` at the tag of
the release in the public repository `release.manifests` names (`engine/defaults.toml`),
where the gate reads it without a token.

`cut` writes a release into a checkout of that public repository: the manifest, and the
release's files themselves under `files/` — the engine's code is public there, so an update can
fetch it without a token. `files/` is replaced whole, so a file the release dropped is gone;
`files/.github/workflows/` is not where GitHub runs workflows, so nothing there runs in that repository.

`cut` also holds the link in each chat field (`engine/rules/fields/chat.md`, `solo.md`): the chat
reads its topic rules from this public repository at the tag the link names, so the link
names a release, its files exist, and they are the release's own — a release that changes
one of them names itself in the link, so the chat field changes with it (§10).

The one text for everybody (`engine/rules/fields/fixed.md`; docs/DESIGN_PHASE3.md §4) names no
release: it sends the chat to the topic index at the branch `stable` of this public repository.
`cut` holds that address against a slip (`fixed_findings`): plain signs only, what stands in code
marks, the one web address, no other word of letters with a slash in it or a dot inside it, no release. It
is no wall against a text written to get past it — an address spelled in pieces, a sentence that
sends a chat elsewhere in plain words: those are the reader's of the text's pull request, and a
test holds the text to its fingerprint so that no change of it goes by unseen.

`cut` writes the topic index as well — `rules-index.md` beside `files/`, built from
`engine/rules/topics.toml` (`engine/setup/topics.py`): which rules files a chat reads for which
topic, each named in full, at this release's tag. It is not one of the release's files — a
person's repository never holds it — so it stands outside the manifest's `files`; the manifest
holds its sha256 under `index`. A release whose topics name a file it lacks, leave a rules
file out, or have parted from the table in `engine/rules/README.md` is refused. And `cut`
holds the code that moves the branch `stable` of that repository (`engine/setup/stable.py`):
that repository runs its own copy, `.github/scripts/stable.py`, brought there by a pull
request of its own and never by a release — the code that decides when a release is trusted
must not come with the release it judges — so a copy that differs from this release's file
refuses the cut until the two are one again (§5, «The topic index and the branch stable»).

`cut` holds the workflow files too. They are frozen (`engine/frozen.toml`): GITHUB_TOKEN cannot
write workflow files, so a release that changes one is not carried by the weekly update and
waits, in every installed repository, for somebody to apply it by hand (§5). A release whose
workflow files differ from those of the release before is refused unless the cut is told so —
`--changes-workflows`, the owner's decision, with a higher `version` on record — and the flag
is refused where nothing differs, so it is never given by habit. The release before is read
from `into`, a checkout with its tags — and held to the release that checkout's own history
held last, so a checkout whose newest tag was never fetched is refused, not believed.

`install` is the first installation only: it refuses a repository that already has
`engine/`, `.github/` or `user/engine.lock`. Updates come through the update PR (§5).

`template` writes the public template a person makes a repository from with «Use this
template» (DESIGN_PHASE3 §5, step 1): the release's files, its lock, and under `user/` nothing
else — anything more there would make every repository made from it «installed» from its first
commit, and `setup` would refuse it — plus the template's README (`docs/template/README.md`,
the steps on a phone) and the licence. Only a release already published, and only from the
commit it was cut from: the manifest at its tag, read from a checkout of the repository of
releases that has fetched its tags from GitHub, names that commit and its files, so C1 in the user's
repository holds the engine that came to the release it came with, and the README is that release's.
The template's `engine/`, `.github/` and `user/` are replaced whole at every release — nothing
added there by hand survives; any other entry at its top refuses the run before anything is
removed.
It refuses, too, before anything is written, a checkout of the repository of releases with no
branch `stable`, or whose `stable` holds rules written for a newer engine than the release
(`stable_floor`): a repository made from the template holds the one text for everybody, which
sends the user's AI to the rules on `stable`, so a template is never written before a `stable`
its engine can follow — the code holds that, not the hand. Whether `stable` is on that very
release it does not ask: rules of an older release whose minimum the engine meets serve as well.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from engine.checks import gitio, min_engine, numbers_check
from engine.checks.lock_check import LOCK_FILE, release_key
from engine.setup import topics

SOURCE = "engine"
WORKFLOWS = "engine/workflows/"
INSTALLED_WORKFLOWS = ".github/workflows/"
PUBLISHED_FILES = "files"
PUBLISHED_TAG_RE = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")   # as the workflow `tag`
MANIFEST_FILE = "release-manifest.json"
CHAT_FIELD = "engine/rules/fields/chat.md"
SOLO_FIELD = "engine/rules/fields/solo.md"       # the chat that writes itself (DESIGN_PHASE3 §6a)
CHAT_FIELDS = (CHAT_FIELD, SOLO_FIELD)
FIXED_FIELD = "engine/rules/fields/fixed.md"     # the one text for everybody: no tag, the branch `stable` (DESIGN_PHASE3 §4)
ADDENDUM_FIELD = "engine/rules/fields/addendum.md"  # set before the text's mark where engine/platforms.toml says so
PLATFORMS_FILE = "engine/platforms.toml"
STABLE_BRANCH = topics.stable.BRANCH             # the branch a chat finds the topic index at
FROZEN = "engine/frozen.toml"                    # the record of the shipped workflow files (§5)
INDEX_FILE = topics.INDEX_FILE                   # the topic index, beside files/ in the repository of releases
STABLE = "engine/setup/stable.py"                # the code that moves the branch `stable` there (§5)
STABLE_COPY = ".github/scripts/stable.py"        # that repository's own copy of it: the one it runs
RULES = "engine/rules/"
_CHAT_RULES_RE = re.compile(r"https://raw\.githubusercontent\.com/([^/\s]+/[^/\s]+)/(?:refs/tags/)?([^/\s]+)/"
                            + PUBLISHED_FILES + "/" + RULES)
_RULE_FILE_RE = re.compile(r"(?<![\w./-])([A-Za-z][\w-]*\.md)(?![\w/-])")
_LINK_HINT_RE = re.compile(r"raw\.githubusercontent\.com|memory-engine-releases|grafyla/releases", re.IGNORECASE)


def release_files(repo: Path, rev: str) -> dict[str, bytes]:
    """Path in a person's repository → content, for everything the release ships.

    Refuses what C1 would hold red for ever after installation: a file that is not a
    regular 100644 file, a path that is not UTF-8, a workflow in a subfolder (GitHub
    reads only the top level of `.github/workflows/`)."""
    files: dict[str, bytes] = {}
    for path_b, (mode, sha) in sorted(gitio.tree(repo, rev, SOURCE).items()):
        try:
            path = path_b.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"path {path_b!r} is not valid UTF-8") from None
        if mode != gitio.REGULAR:
            raise ValueError(f"{path} has mode {mode}; a release ships regular files only ({gitio.REGULAR})")
        content = gitio.blob(repo, sha)
        files[path] = content
        if path.startswith(WORKFLOWS):
            name = path[len(WORKFLOWS):]
            if "/" in name:
                raise ValueError(f"{path} is in a subfolder; GitHub runs only the top level of {INSTALLED_WORKFLOWS}")
            files[INSTALLED_WORKFLOWS + name] = content
    if not files:
        raise ValueError(f"no {SOURCE}/ at {rev}")
    return files


def commit_of(repo: Path, rev: str) -> str:
    return gitio.git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").decode().strip()


def manifest(files: dict[str, bytes], tag: str, source: str, published: datetime) -> dict:
    """The manifest in the form lock_check.load_manifest reads. `source` is the template
    commit the release was cut from; `published` is when it was cut — C1 holds an update
    to the release until its channel's delay has passed from then (§5). A release that has
    topics (`engine/rules/topics.toml`) names its topic index under `index`: the path beside
    `files/` and the sha256 — outside `files`, which is what a person's repository holds."""
    release_key(tag)
    if not PUBLISHED_TAG_RE.fullmatch(tag):
        raise ValueError(f"release tag {tag!r} has a leading zero: the releases repository's workflow `tag` refuses it")
    if published.tzinfo is None:
        raise ValueError("the publish time needs its UTC offset")
    data = {
        "release": tag,
        "source": source,
        "published": published.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": {path: hashlib.sha256(files[path]).hexdigest() for path in sorted(files)},
    }
    record = topics.index_record(files, tag)
    if record is not None:
        data["index"] = record
    return data


def dumps(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


# What `cut` lets go public. Every number of
# the kinds C4 knows and every e-mail address in the release must be one of these test
# values; anything else stops the publication — a public tag cannot be taken back. A new
# test value goes in here on purpose, never a real one.
PUBLIC_TEST_NUMBERS = frozenset({
    "4111111111111111", "4012888888881881", "378282246310005",      # public test cards
    "41111111111111113", "4111111111111111003",                       # longer cards built on the Visa test number
    "1000000000000008",                                               # a long fraction that passes Luhn
    "2026022220260301",                                               # a date range the free form reads as a card
    "GB82WEST12345698765432", "IL620108000000099999999",              # textbook IBANs
    "123456782", "000000018",                                         # IDs that pass their check digit
    "0501234567", "031234567", "972501234567", "00972501234567",      # made-up local numbers
    "14155552671",                                                    # an example number from US docs
}) | frozenset({
    # the country table's tests: document numbers from the python-stdnum documentation and tests,
    # textbook values or sequential digits, and the wrong forms built on them
    "536904399", "36574261809", "11234567890", "11123456786", "11987654321", "621498320257",
    "295109912611193", "295109912611194", "253072A07300443", "253072B07300470", "39053344705", "12345678909",
    "123456789047", "11223344595", "4506123456", "9001011234567", "0002293234567", "9912312234567",
    "1990010100015", "2000022901234", "9434765870", "9434765919", "046454286", "AB123456C", "NL91ABNA0417164300",
    # their phones: sequential digits, or a range kept for fiction (555, 07700 900)
    "89123456789", "84951234567", "88121234567", "0441234567", "0991234567", "2123456789", "2125551234",
    "12125551234", "015112345678", "01711234567", "01601234567", "07700900123", "01012345678", "0101234567",
    "021234567", "03112345678", "0312345678", "0361234567", "0451234567", "07012345678", "09012345678",
    "0612345678", "0123456789",
})
PUBLIC_TEST_ADDRESSES = frozenset({"alex@example.com\u0431\u044b\u043b",            # glued to a Cyrillic word
                                   "alex@\u043f\u0440\u0438\u043c\u0435\u0440.\u0440\u0444",  # a Cyrillic domain
                                   "noreply@calendar.google.com"})
_RESERVED_DOMAINS = ("example.com", "example.org", "example.net", ".example", ".invalid", ".test", ".localhost")
_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|ntn_|secret_|sk-ant-|xox[abp]-|AKIA)[A-Za-z0-9_-]{10,}")


def _reserved(address: str) -> bool:
    domain = address.rsplit("@", 1)[-1].lower()
    return any(domain == d or domain.endswith("." + d.lstrip(".")) for d in _RESERVED_DOMAINS)


def publication_findings(files: dict[str, bytes]) -> list[str]:
    """Where the release holds something that may not go public: a number of a kind C4
    knows that is not a listed test value, an e-mail address off the reserved test domains
    and the listed ones, what looks like a token — or a file that is not UTF-8 text, which
    cannot be checked and so may not go either. Names the place and
    the kind, never the value. The .github/workflows/ copies are the same bytes as
    engine/workflows/."""
    found = []
    for path in sorted(p for p in files if not p.startswith(INSTALLED_WORKFLOWS)):
        try:
            text = files[path].decode("utf-8")
        except UnicodeDecodeError:
            found.append(f"{path}: not valid UTF-8, so it cannot be checked")    # a check that cannot run is red (P5)
            continue
        for no, line in enumerate(text.splitlines(), start=1):
            kinds = []
            for regex, card in numbers_check.CARD_RES:
                kinds += [("a card number", numbers_check._digits(m.group()[:c])) for m in regex.finditer(line) if (c := card(m))]
            kinds += [("an ID number", d) for m in numbers_check.IL_ID_RE.finditer(line)
                      if numbers_check._il_id_ok(d := numbers_check._digits(m.group()))]
            kinds += [("an IBAN", s) for m in numbers_check.IBAN_RE.finditer(line)
                      if 15 <= len(s := m.group().replace(" ", "")) <= 34 and numbers_check._iban_ok(s)]
            kinds += [("a phone number", numbers_check._digits(m.group())) for rx in numbers_check.PHONE_RES for m in rx.finditer(line)]
            kinds += numbers_check.national_values(line)
            for kind, value in kinds:
                if value not in PUBLIC_TEST_NUMBERS:
                    found.append(f"{path} line {no}: {kind} that is not a listed test value")
            for m in numbers_check.EMAIL_RE.finditer(line):
                if not _reserved(m.group()) and m.group() not in PUBLIC_TEST_ADDRESSES:
                    found.append(f"{path} line {no}: an e-mail address off the test domains")
            if _SECRET_RE.search(line):
                found.append(f"{path} line {no}: what looks like a token")
    return found


def chat_rules(files: dict[str, bytes], field: str = CHAT_FIELD) -> tuple[str, str, list[str]] | None:
    """The public folder a chat field sends the chat to for its topic rules: (repository,
    tag, the files named on the link's line), or None when the field has no such link."""
    try:
        text = files.get(field, b"").decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{field} is not valid UTF-8") from None
    links, rest = [], []
    for line in text.splitlines():
        for m in _CHAT_RULES_RE.finditer(line):
            names = sorted(set(_RULE_FILE_RE.findall(line[:m.start()] + " " + line[m.end():])))
            links.append((m.group(1), m.group(2), names))
        rest.append(_CHAT_RULES_RE.sub(" ", line))
    if len(links) > 1:
        raise ValueError(f"{field} links the public rules {len(links)} times; one link, one tag")
    # A mention the pattern did not read — beside a link it did read, or alone — is refused,
    # never skipped: the chat could follow it to rules no check has held.
    if _LINK_HINT_RE.search("\n".join(rest)):
        raise ValueError(f"{field} points to the public rules, but not as "
                         f"https://raw.githubusercontent.com/<repository>/<tag>/{PUBLISHED_FILES}/{RULES}")
    return links[0] if links else None


def chat_rules_findings(files: dict[str, bytes], tag: str, into: Path, repository: str) -> list[str]:
    """Why a chat field's link — `fields/chat.md`'s, and `fields/solo.md`'s, the chat that writes
    itself (docs/DESIGN_PHASE3.md §6a) — would send the chat to rules this release does not hold."""
    found: list[str] = []
    for field in CHAT_FIELDS:
        if field in files:
            found += _field_rules_findings(files, field, tag, into, repository)
    return found


def _field_rules_findings(files: dict[str, bytes], field: str, tag: str, into: Path, repository: str) -> list[str]:
    """Why this chat field's link would send the chat to rules this release does not hold:
    another repository, not a release tag, a newer release, a named file the release lacks,
    or a file that changed since the release the link names. A link to this very release
    is right by construction. The older release is read from `into`, a checkout with its
    tags: a tag missing there is a finding too, never a pass."""
    link = chat_rules(files, field)
    if link is None:
        return []
    linked_repo, at, names = link
    found = []
    if linked_repo != repository:
        found.append(f"{field} links {linked_repo}, not {repository}")
    if not PUBLISHED_TAG_RE.fullmatch(at):
        return found + [f"{field} links {at!r}, which is not a release tag"]
    if not names:
        found.append(f"{field} names no rules file on the line of its link")
    found += [f"{field} names {name}, which is not in {RULES}" for name in names if RULES + name not in files]
    if found or at == tag:
        return found
    if release_key(at) > release_key(tag):
        return [f"{field} links {at}, newer than {tag}"]
    for name in names:
        try:
            old = gitio.git(into, "show", f"refs/tags/{at}:{PUBLISHED_FILES}/{RULES}{name}")
        except gitio.GitError:
            found.append(f"{PUBLISHED_FILES}/{RULES}{name} at the tag {at} is not in {into}: fetch its tags")
            continue
        if old != files[RULES + name]:
            found.append(f"{RULES}{name} changed since {at}, the copy the chat reads: link {tag} in {field}")
    return found


_CODE_RE = re.compile(r"`([^`\n]*)`")                    # what stands in code marks
_SIGNS = "\u00ab\u00bb\u2014\u2192\u00b7"                # « » — → ·: the signs the text uses beside plain ASCII
_EDGES = ".,;:!?()[]{}\"'*" + _SIGNS                       # what a sentence puts around a word
_INSIDE_RE = re.compile(r"[\w-]\.[\w-]")                 # a dot inside a word: a host, a file's name, a number


def _reads_as_an_address(word: str) -> bool:
    """Whether a word of the text, outside code marks, reads as somewhere to go: a slash in it, or
    a dot inside it. Strict on purpose — «and/or», «e.g.» and «3.5» are refused too."""
    return any(ch.isalnum() for ch in word) and ("/" in word or "\\" in word or bool(_INSIDE_RE.search(word)))


def fixed_findings(files: dict[str, bytes], repository: str) -> list[str]:
    """Why the one text for everybody would send a chat somewhere else than where this release
    goes — a guard against a slip, not a wall against a text written to get past it.

    Held: the text is plain ASCII and the five signs « » — → · — so a sign that looks like a
    slash or a dot, or one that cannot be seen, is not in it; in code marks, each pair closed on
    its own line, stand this repository, the branch `stable` and the index's name, each at least
    once, and nothing else; outside them the one address is
    https://raw.githubusercontent.com/<this repository>/, and no other word that holds a letter
    or a digit has a slash in it, or a dot between two letters, digits, dashes or underscores —
    which a host, a path, an owner and a name, and a release with or without its «v» all have. A release without the text is refused too:
    `setup` gives it to every new installation, and where it is missing C6 is red every night
    and nothing is ever handed over.

    Not held, and a test says each: the meaning — «at the branch main (not `stable`)» passes, a
    plain word is no address; an address spelled in pieces, the way the text itself spells the
    path («mallory + / + rules», «evil..com») — a slash or dots with no letter beside them are
    signs, as the text's own «+ / +» is; one written in percent signs or as HTML entities. What stands
    against those is that the text does not change by the way: a test holds it to its
    fingerprint, so a change of one sign is a change of two files, and its reader sees a text
    every installed person would have to paste again."""
    if FIXED_FIELD not in files:
        return [f"{FIXED_FIELD} is missing: setup gives it to every new installation"]
    try:
        text = files[FIXED_FIELD].decode("utf-8")
    except UnicodeDecodeError:
        return [f"{FIXED_FIELD} is not valid UTF-8"]
    odd_signs = sorted({ch for ch in text if not (ch == "\n" or " " <= ch <= "~" or ch in _SIGNS)})
    if odd_signs:
        return [f"{FIXED_FIELD} holds {', '.join(f'U+{ord(ch):04X}' for ch in odd_signs[:5])}: the text is plain "
                f"ASCII and the signs « » — → · — a sign that looks like another, or cannot be seen, has no place in it"]
    if any(line.count("`") % 2 for line in text.split("\n")):
        return [f"{FIXED_FIELD} opens a code mark on one line and does not close it there: each name in code "
                f"marks stands whole on one line"]
    address = f"https://raw.githubusercontent.com/{repository}/"
    wanted = (repository, STABLE_BRANCH, INDEX_FILE)
    marked = _CODE_RE.findall(text)
    found = [f"{FIXED_FIELD} does not name `{word}`" for word in wanted if word not in marked]
    odd = sorted(set(marked) - set(wanted))
    if odd:
        found.append(f"{FIXED_FIELD} holds {', '.join(repr(o[:60]) for o in odd[:3])} in code marks: only "
                     f"{', '.join(wanted)} stand there — as a chat's GitHub app takes them")
    words = [word.strip(_EDGES) for word in _CODE_RE.sub(" ", text).split()]
    if address not in words:
        found.append(f"{FIXED_FIELD} does not give the address {address} as a word of its own, for a chat without "
                     f"a GitHub app")
    others = sorted({word for word in words if word != address and _reads_as_an_address(word)})
    if others:
        found.append(f"{FIXED_FIELD} holds {', '.join(o[:60] for o in others[:3])}: outside code marks no word of "
                     f"this text has a slash in it or a dot inside it but its one address, {address} — an "
                     f"address, a repository or a release reads so; say it in other words")
    tags = sorted(set(m.group(0) for m in PUBLISHED_TAG_RE.finditer(text)))
    if tags:
        found.append(f"{FIXED_FIELD} names {', '.join(tags)}: the text names no release")
    return found


def addendum_findings(files: dict[str, bytes]) -> list[str]:
    """What the addendum must be, as the one text is (`fixed_findings`), where a platform's row asks for it
    (`addendum = true`, `setup.field_text`): there, plain ASCII and the signs « » — → ·, no code mark, no
    word that reads as an address, a repository or a release, no release named — it sends a chat nowhere;
    the one text does that. A release whose table asks for it and has none is refused: setup would give
    those platforms a text it cannot build."""
    try:
        table = tomllib.loads(files[PLATFORMS_FILE].decode("utf-8")) if PLATFORMS_FILE in files else {}
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return [f"{PLATFORMS_FILE} cannot be read: whether a platform's text has an addendum is not known"]
    wanted = sorted(k for k, row in table.items() if isinstance(row, dict) and row.get("addendum") is True)
    if not wanted:
        return []
    if ADDENDUM_FIELD not in files:
        return [f"{ADDENDUM_FIELD} is missing, and {', '.join(wanted)} give it to every new installation"]
    try:
        text = files[ADDENDUM_FIELD].decode("utf-8")
    except UnicodeDecodeError:
        return [f"{ADDENDUM_FIELD} is not valid UTF-8"]
    odd_signs = sorted({ch for ch in text if not (ch == "\n" or " " <= ch <= "~" or ch in _SIGNS)})
    if odd_signs:
        return [f"{ADDENDUM_FIELD} holds {', '.join(f'U+{ord(ch):04X}' for ch in odd_signs[:5])}: the addendum is plain "
                f"ASCII and the signs « » — → ·, as the one text is"]
    found = [f"{ADDENDUM_FIELD} holds a code mark: it names nothing in code, the one text does"] if "`" in text else []
    words = [word.strip(_EDGES) for word in text.split()]
    others = sorted({word for word in words if _reads_as_an_address(word)})
    if others:
        found.append(f"{ADDENDUM_FIELD} holds {', '.join(o[:60] for o in others[:3])}: no word of the addendum has a "
                     f"slash in it or a dot inside it — an address, a repository or a release reads so")
    tags = sorted(set(m.group(0) for m in PUBLISHED_TAG_RE.finditer(text)))
    if tags:
        found.append(f"{ADDENDUM_FIELD} names {', '.join(tags)}: the addendum names no release")
    if re.search(r"(?m)^TEXT MARK", text):
        found.append(f"{ADDENDUM_FIELD} holds a line of a text's mark: the one text's mark stays its last line")
    return found


def frozen_workflows(files: dict[str, bytes]) -> tuple[int, dict[str, str]]:
    """(version, name → sha256): the record of the workflow files this release ships, held to
    the files themselves — a record that does not match them is refused, never read."""
    raw = files.get(FROZEN)
    if raw is None:
        raise ValueError(f"{FROZEN} is missing: the record of the shipped workflow files")
    try:
        table = tomllib.loads(raw.decode("utf-8")).get("workflows")
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{FROZEN} cannot be read: {exc}") from None
    version = table.get("version") if isinstance(table, dict) else None
    record = table.get("sha256") if isinstance(table, dict) else None
    if isinstance(version, bool) or not isinstance(version, int) or version < 1 or not isinstance(record, dict):
        raise ValueError(f"{FROZEN} needs [workflows] with a whole `version` of 1 or more and a `sha256` table")
    shipped = {path[len(WORKFLOWS):]: hashlib.sha256(content).hexdigest()
               for path, content in files.items() if path.startswith(WORKFLOWS)}
    differ = sorted(set(shipped) ^ set(record) | {n for n in set(shipped) & set(record) if shipped[n] != record[n]})
    if differ:
        raise ValueError(f"{FROZEN} does not hold the workflow files of this release: {', '.join(differ)}")
    return version, record


def release_before(into: Path, tag: str) -> str | None:
    """The newest published release older than `tag`, by the tags of the checkout."""
    tags = [t for t in gitio.git(into, "tag", "--list").decode("utf-8", "replace").split()
            if PUBLISHED_TAG_RE.fullmatch(t) and release_key(t) < release_key(tag)]
    return max(tags, key=release_key) if tags else None


HISTORY = 200           # commits of the checkout's history read for the release it held before


def release_held(into: Path, tag: str) -> str | None:
    """The release the checkout itself held before `tag`: the newest commit of its history whose
    manifest names another release. None when its history holds no manifest but `tag`'s own — a
    repository of releases before its first release, or a branch without one."""
    try:
        commits = gitio.git(into, "rev-list", "--first-parent", "-n", str(HISTORY), "HEAD").decode().split()
    except gitio.GitError:
        return None                         # no commit yet
    for commit in commits:
        raw = gitio.file_at(into, commit, MANIFEST_FILE)
        if raw is None:
            continue
        try:
            held = json.loads(raw.decode("utf-8")).get("release")
        except (UnicodeDecodeError, ValueError, AttributeError):
            raise ValueError(f"{MANIFEST_FILE} at {commit[:12]} in {into} cannot be read") from None
        if held != tag:
            return str(held)
    return None


def workflow_change(files: dict[str, bytes], tag: str, into: Path) -> tuple[str | None, list[str], int | None]:
    """(the release before, the workflow files that differ from its own, its record's version).
    No release before: (None, [], None) — the first release has nothing to change.

    The release before is the newest older tag of the checkout, and it must be the release the
    checkout's own history held last: the tag of a release is set on GitHub after the push that
    brings it, so a checkout that was not fetched since lacks the newest tag, and a comparison
    with the one before it would pass a change as «nothing differs» — or refuse a release that
    changes nothing, and teach whoever cuts to give the flag. A checkout that holds published
    files but no older tag is refused too: «no tag» must not read as «first»."""
    before, held = release_before(into, tag), release_held(into, tag)
    if held is not None and held != before:
        raise ValueError(f"{into} held {held} last, and its newest tag older than {tag} is {before or 'none'}: fetch "
                         f"its tags — the workflow files of {tag} are compared with those of the release before")
    if before is None:
        try:
            gitio.git(into, "rev-parse", "--verify", "--quiet", f"HEAD:{PUBLISHED_FILES}")
        except gitio.GitError:
            return None, [], None
        raise ValueError(f"{into} holds a release but no tag older than {tag}: fetch its tags — the workflow "
                         f"files of {tag} are compared with those of the release before")
    held = f"{PUBLISHED_FILES}/{INSTALLED_WORKFLOWS}"
    old = {path.decode("utf-8", "replace")[len(held):]: gitio.blob(into, sha)
           for path, (_mode, sha) in gitio.tree(into, f"refs/tags/{before}", held.rstrip("/")).items()}
    new = {path[len(INSTALLED_WORKFLOWS):]: content for path, content in files.items()
           if path.startswith(INSTALLED_WORKFLOWS)}
    differ = sorted(set(old) ^ set(new) | {n for n in set(old) & set(new) if old[n] != new[n]})
    version = None
    raw = gitio.file_at(into, f"refs/tags/{before}", f"{PUBLISHED_FILES}/{FROZEN}")
    if raw is not None:                 # a release cut before the files were frozen has no record
        try:
            version = tomllib.loads(raw.decode("utf-8"))["workflows"]["version"]
        except (UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError):
            raise ValueError(f"{PUBLISHED_FILES}/{FROZEN} at the tag {before} cannot be read") from None
    return before, differ, version


def workflow_findings(files: dict[str, bytes], tag: str, into: Path, changes_workflows: bool) -> list[str]:
    """Why this release may not be cut as asked: its record does not hold its workflow files;
    they differ from the release before and the cut was not told so; the cut was told so and
    nothing differs; or the record's version did not rise with the change."""
    version, _record = frozen_workflows(files)
    before, differ, was = workflow_change(files, tag, into)
    if before is None:
        return ["--changes-workflows was given, but there is no release before this one"] if changes_workflows else []
    if differ and not changes_workflows:
        return [f"{tag} changes the workflow files against {before}: {', '.join(differ)}. GITHUB_TOKEN cannot write "
                f"workflow files, so no installed repository takes such a release by its weekly update: each waits "
                f"for somebody to apply it by hand. If that is decided, cut with --changes-workflows"]
    if not differ and changes_workflows:
        return [f"--changes-workflows was given, but the workflow files of {tag} are those of {before}: cut without it"]
    if differ and was is not None and (isinstance(was, bool) or not isinstance(was, int) or version <= was):
        return [f"{tag} changes the workflow files against {before}, and `version` in {FROZEN} is {version}, "
                f"not above {before}'s {was}: raise it"]
    return []


def stable_copy(into: Path) -> bytes | None:
    """The copy of the code that moves `stable`, as the checkout of the repository of releases
    has it committed; None when it has none, or no commit yet."""
    try:
        return gitio.file_at(into, "HEAD", STABLE_COPY)
    except gitio.GitError:
        return None


def stable_copy_findings(files: dict[str, bytes], into: Path) -> list[str]:
    """Why the repository of releases would move its branch `stable` by other code than this
    release's, or why this release's commit would change what that repository runs: its own
    copy (`.github/scripts/stable.py`, committed there) differs from `engine/setup/stable.py`;
    the checkout holds anything under `.github/` that is not committed — changed, added,
    removed, ignored: the release's commit takes all the checkout holds —; or `.github/` or the
    folder of the copy is not a plain folder there, so the copy could be another file by a
    link. No copy there yet — that repository has no branch `stable` to move — is no finding:
    `main` says so aloud."""
    source = files.get(STABLE)
    if source is None:
        return [f"{STABLE} is missing: the code that moves the branch `stable`"]
    folders = (".github", ".github/scripts")
    for folder in folders:
        if (into / folder).is_symlink():
            return [f"{into / folder} is a link"]
    try:
        entries = {name: gitio.git(into, "ls-tree", "HEAD", "--", name).decode("utf-8", "replace").split()[:2]
                   for name in (*folders, STABLE_COPY)}
        dirty = gitio.git(into, "status", "--porcelain", "--untracked-files=all", "--ignored", "--", ".github").decode(
            "utf-8", "replace").splitlines()
    except gitio.GitError:                      # no commit yet: nothing is committed there
        entries, dirty = {}, ([".github"] if (into / ".github").exists() else [])
    if dirty:
        return [f"{into} holds a change under .github/ that is not committed ({dirty[0].strip()[:60]}): a release "
                f"brings no change to what that repository runs — that comes by a pull request of its own"]
    for folder in folders:
        if entries.get(folder) and entries[folder] != ["040000", "tree"]:
            return [f"{folder} in {into} is not a plain folder"]
    if entries.get(STABLE_COPY) and entries[STABLE_COPY] != [gitio.REGULAR, "blob"]:
        return [f"{STABLE_COPY} in {into} is not a regular file"]
    copy = stable_copy(into)
    if copy is not None and copy != source:
        return [f"{STABLE_COPY} in {into} is not this release's {STABLE}: that repository moves `stable` by "
                f"its own copy, so bring the copy there by a pull request of its own first, then cut"]
    return []


def cut(files: dict[str, bytes], tag: str, source: str, published: datetime, into: Path,
        repository: str, changes_workflows: bool = False) -> list[str]:
    """Write the release into a checkout of the public repository of releases: its files under
    files/, its topic index and its manifest. Returns the paths written, the manifest last.
    Everything is checked before anything is removed or written: the tag, the release's content
    (`publication_findings`), that `into` is a checkout of `repository` — the one
    `release.manifests` names —, the topics (`topics.findings`), the workflow files against the
    release before (`workflow_findings`), that repository's copy of the code that moves `stable`
    (`stable_copy_findings`), and that neither files/, the index nor the manifest is a link out
    of it. files/, the index and the manifest are each written beside the old one first and put
    in place only when all three are whole, so a write that fails leaves the old release in
    place."""
    data = manifest(files, tag, source, published)          # checks the tag and the time
    found = publication_findings(files)
    if found:
        raise ValueError("not published, it would make public: " + "; ".join(found[:10]))
    try:
        origin = gitio.git(into, "remote", "get-url", "origin").decode().strip()
    except gitio.GitError:
        raise ValueError(f"{into} is not a checkout of {repository}") from None
    if not re.fullmatch(rf"(https://github\.com/|git@github\.com:){re.escape(repository)}(\.git)?/?", origin):
        raise ValueError(f"{into} is not a checkout of {repository}: its origin is {origin}")
    found = chat_rules_findings(files, tag, into, repository)
    if found:
        raise ValueError("not published, the chat would read other rules: " + "; ".join(found))
    found = [f"{topics.TOPICS} is missing: the release has no topics to index"] if topics.TOPICS not in files \
        else topics.findings(files)
    if found:
        raise ValueError("not published, the topic index would send a chat wrong: " + "; ".join(found[:10]))
    if topics.repository(files) != repository:
        raise ValueError(f"not published: the index would name {topics.repository(files)}, and the release goes to {repository}")
    found = fixed_findings(files, repository) + addendum_findings(files)   # after the index: the text sends a chat to it
    if found:
        raise ValueError("not published, the chat would read other rules: " + "; ".join(found))
    index = topics.index(files, tag).encode("utf-8")
    found = publication_findings({INDEX_FILE: index})
    if found:
        raise ValueError("not published, it would make public: " + "; ".join(found[:10]))
    found = workflow_findings(files, tag, into, changes_workflows) + stable_copy_findings(files, into)
    if found:
        raise ValueError("not published: " + "; ".join(found))
    target, staging, manifest_path = into / PUBLISHED_FILES, into / f".{PUBLISHED_FILES}.new", into / MANIFEST_FILE
    index_path, new_index, new_manifest = into / INDEX_FILE, into / f".{INDEX_FILE}.new", into / f".{MANIFEST_FILE}.new"
    for path in (target, staging, manifest_path, index_path, new_index, new_manifest):
        if path.is_symlink():
            raise ValueError(f"{path} is a link")
    if target.exists() and not target.is_dir():
        raise ValueError(f"{target} is not a folder")
    if index_path.exists() and not index_path.is_file():
        raise ValueError(f"{index_path} is not a file")
    if staging.exists():
        shutil.rmtree(staging)
    written = []
    try:
        for path in sorted(files):
            out = staging / path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(files[path])
            out.chmod(0o644)
            written.append(f"{PUBLISHED_FILES}/{path}")
        new_index.write_bytes(index)
        new_index.chmod(0o644)
        new_manifest.write_bytes(dumps(data).encode("utf-8"))
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        new_index.unlink(missing_ok=True)
        new_manifest.unlink(missing_ok=True)
        raise
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    new_index.replace(index_path)
    new_manifest.replace(manifest_path)
    return [*written, INDEX_FILE, MANIFEST_FILE]


def install(files: dict[str, bytes], tag: str, into: Path) -> list[str]:
    """Write the release and its lock into a person's repository that has neither yet.
    Returns the paths written. Touches nothing under user/ but the lock."""
    release_key(tag)
    if not into.is_dir():
        raise ValueError(f"{into} is not a folder")
    for taken in ("engine", ".github", LOCK_FILE):
        if (into / taken).exists() or (into / taken).is_symlink():
            raise ValueError(f"{into / taken} exists; install is for a repository without the engine")
    written = []
    for path in sorted(files):
        target = into / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(files[path])
        target.chmod(0o644)
        written.append(path)
    lock = into / LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f'release = "{tag}"\n', encoding="utf-8")
    written.append(LOCK_FILE)
    return written


TEMPLATE_README = "docs/template/README.md"
TEMPLATE_ROOT = ("README.md", "LICENSE")
TEMPLATE_TOP = frozenset({SOURCE, ".github", "user", *TEMPLATE_ROOT})


def published_manifest(releases: Path, tag: str) -> dict:
    """The manifest at the tag of a published release, from a checkout of the repository of
    releases that has fetched its tags."""
    release_key(tag)
    try:
        raw = gitio.file_at(releases, f"refs/tags/{tag}", MANIFEST_FILE)
    except gitio.GitError:
        raw = None
    if raw is None:
        raise ValueError(f"{releases} has no tag {tag} with a {MANIFEST_FILE}: fetch its tags, or cut and publish it first")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("release") != tag or not isinstance(data.get("files"), dict):
        raise ValueError(f"the manifest at {tag} does not name {tag} and its files")
    return data


def stable_floor(releases: Path, tag: str) -> str:
    """The minimal engine of the rules on the branch `stable` of a checkout of the repository of
    releases — refused unless that branch is there, holds an index that names it, and it is not
    newer than `tag`. A repository made from the template holds the one text for everybody, which
    sends its AI to the rules on `stable` (DESIGN_PHASE3 §4): with no `stable` it reads no rules at
    all, and with rules for a newer engine than the template's it follows what its checks do not
    hold. So a template is never written before a `stable` its engine can follow; whether
    `stable` is on this very release is not asked — an older release's rules whose minimum the
    engine meets serve as well. The branch as the checkout last fetched it (`origin/stable`), else a local one."""
    release_key(tag)
    for ref in (f"refs/remotes/origin/{STABLE_BRANCH}", f"refs/heads/{STABLE_BRANCH}"):
        try:
            gitio.git(releases, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        except gitio.GitError:
            continue
        index = gitio.file_at(releases, ref, INDEX_FILE)
        if index is None:
            raise ValueError(f"{STABLE_BRANCH} in {releases} has no {INDEX_FILE}: the template waits until a "
                             f"release with its index is on {STABLE_BRANCH}")
        try:
            needs = min_engine.least(index.decode("utf-8"))
        except UnicodeDecodeError:
            needs = None
        if needs is None:
            raise ValueError(f"the index on {STABLE_BRANCH} in {releases} names no minimal engine this code reads")
        if release_key(needs) > release_key(tag):
            raise ValueError(f"the rules on {STABLE_BRANCH} are written for the engine {needs} or newer, and the "
                             f"template would install {tag}: write it from {needs} or a newer release")
        return needs
    raise ValueError(f"{releases} has no branch {STABLE_BRANCH}: a repository made from the template would read no "
                     f"rules — publish the release, let {STABLE_BRANCH} move onto it, fetch it, then write the "
                     f"template")


def template_tree(repo: Path, rev: str, files: dict[str, bytes], tag: str, published: dict) -> dict[str, bytes]:
    """Path → content of the template at this release; refused unless rev is the commit the
    release was cut from and the files are the published ones."""
    release_key(tag)
    if published.get("release") != tag:
        raise ValueError(f"the published manifest is of {published.get('release')!r}, not {tag}")
    if published.get("source") != commit_of(repo, rev):
        raise ValueError(f"{tag} was cut from {str(published.get('source'))[:12]}, not from {rev[:12]}: the template "
                         f"is written from the release's own commit")
    shipped = {path: hashlib.sha256(content).hexdigest() for path, content in files.items()}
    if shipped != published["files"]:
        differ = sorted(set(shipped) ^ set(published["files"]) |
                        {p for p in set(shipped) & set(published["files"]) if shipped[p] != published["files"][p]})
        raise ValueError(f"these files are not the ones published as {tag}: {', '.join(differ[:10])}")
    tree = dict(files)
    tree[LOCK_FILE] = f'release = "{tag}"\n'.encode()
    for target, source in (("README.md", TEMPLATE_README), ("LICENSE", "LICENSE")):
        content = gitio.file_at(repo, rev, source)
        if content is None:
            raise ValueError(f"{source} is missing at {rev[:12]}")
        tree[target] = content
    return tree


def template(tree: dict[str, bytes], into: Path) -> list[str]:
    """Write the template into a checkout of its repository: `engine/`, `.github/` and `user/`
    replaced whole, README and LICENSE rewritten. Everything is looked at before anything is
    removed: an entry at the top that is not the template's, a link, a folder where a file
    belongs or the other way round, or a `user/` holding more than the lock (a person's part
    above all) refuses the run."""
    git_dir = into / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise ValueError(f"{into} is not a checkout of the template's repository")
    for entry in sorted(into.iterdir()):
        if entry.name == ".git":
            continue
        if entry.is_symlink():
            raise ValueError(f"{entry} is a link")
        if entry.name not in TEMPLATE_TOP:
            raise ValueError(f"{entry} is not the template's: the checkout holds only what `template` writes")
        if entry.name in TEMPLATE_ROOT and not entry.is_file():
            raise ValueError(f"{entry} is not a file")
        if entry.name not in TEMPLATE_ROOT and not entry.is_dir():
            raise ValueError(f"{entry} is not a folder")
    user = into / "user"
    if user.exists():
        if not user.is_dir() or any(p.name != "engine.lock" for p in user.iterdir()):
            raise ValueError(f"{user} holds more than the lock: a template with a person's part would make every "
                             f"repository made from it «installed» (DESIGN_PHASE3 §5)")
    for top in (SOURCE, ".github", "user"):
        if (into / top).exists():
            shutil.rmtree(into / top)
    for name in TEMPLATE_ROOT:
        (into / name).unlink(missing_ok=True)
    written = []
    for path in sorted(tree):
        out = into / path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(tree[path])
        out.chmod(0o644)
        written.append(path)
    return written


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("manifest", "cut", "install", "template"):
        p = sub.add_parser(name)
        p.add_argument("--repo", type=Path, default=Path("."), help="the template")
        p.add_argument("--rev", required=True, help="the template commit the release is cut from")
        p.add_argument("--tag", required=True, help="vMAJOR.MINOR.PATCH")
    sub.choices["install"].add_argument("--into", type=Path, required=True, help="the person's repository")
    sub.choices["cut"].add_argument("--into", type=Path, required=True,
                                    help="a checkout of the public repository of releases")
    sub.choices["cut"].add_argument("--changes-workflows", action="store_true",
                                    help="this release changes the frozen workflow files against the release "
                                         "before: every installed repository applies it by hand (the owner's decision)")
    sub.choices["template"].add_argument("--into", type=Path, required=True,
                                         help="a checkout of the template's public repository")
    sub.choices["template"].add_argument("--releases", type=Path, required=True,
                                         help="a checkout of the repository of releases, with its tags and "
                                              "its branch stable")
    args = ap.parse_args(argv)
    try:
        source = commit_of(args.repo, args.rev)
        files = release_files(args.repo, source)
        if args.command == "manifest":
            sys.stdout.write(dumps(manifest(files, args.tag, source, now or datetime.now(timezone.utc))))
        elif args.command == "cut":
            repository = tomllib.loads(files["engine/defaults.toml"].decode("utf-8"))["release"]["manifests"]
            for path in cut(files, args.tag, source, now or datetime.now(timezone.utc), args.into, repository,
                            args.changes_workflows):
                print(path)
            if stable_copy(args.into) is None:                         # said aloud: nothing there moves `stable` yet
                print(f"STABLE {args.into} has no {STABLE_COPY}: that repository has no code to move its branch "
                      f"`stable` yet — it comes by a pull request of its own, from this release's {STABLE}",
                      file=sys.stderr)
            if args.changes_workflows:      # loud, on its own stream: the list above is what was written
                before, differ, _was = workflow_change(files, args.tag, args.into)
                print(f"HANDS {args.tag} changes the workflow files against {before}: {', '.join(differ)} — no "
                      f"installed repository takes it by its weekly update; each one is applied by hand",
                      file=sys.stderr)
        elif args.command == "template":
            tree = template_tree(args.repo, source, files, args.tag, published_manifest(args.releases, args.tag))
            stable_floor(args.releases, args.tag)
            for path in template(tree, args.into):
                print(path)
        else:
            for path in install(files, args.tag, args.into):
                print(path)
    except (ValueError, KeyError, tomllib.TOMLDecodeError, json.JSONDecodeError, gitio.GitError, OSError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

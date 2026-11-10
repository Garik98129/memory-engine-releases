"""A release of the engine: its files in a person's repository and its manifest
(docs/DESIGN.md §4, §5).

    python -m engine.setup.release manifest --rev COMMIT --tag v0.1.0 > release-manifest.json
    python -m engine.setup.release cut --rev COMMIT --tag v0.1.6 --into RELEASES_CHECKOUT
    python -m engine.setup.release install --rev COMMIT --tag v0.1.0 --into PERSON_REPO

A release is the template's `engine/` at one commit, read as git objects: an untracked
or uncommitted file never ships. Each `engine/workflows/<name>` ships a second time as
`.github/workflows/<name>`, where GitHub runs it. The manifest maps every path the
release puts into a person's repository to its sha256 — exactly the set C1 holds
`engine/` and `.github/` to. It is published as `release-manifest.json` at the tag of
the release in the public repository `release.manifests` names (`engine/defaults.toml`),
where the gate reads it without a token.

`cut` writes a release into a checkout of that public repository: the manifest, and the
release's files themselves under `files/` — the engine's code is public there (owner,
09.11.2026, §13 item 7), so an update can fetch it without a token. `files/` is replaced
whole, so a file the release dropped is gone; `files/.github/workflows/` is not where
GitHub runs workflows, so nothing there runs in that repository.

`cut` also holds the link in the chat field (`engine/rules/fields/chat.md`): the chat
reads its topic rules from this public repository at the tag the link names, so the link
names a release, its files exist, and they are the release's own — a release that changes
one of them names itself in the link, so the chat field changes with it (§10; Issue #38,
step 3).

`install` is the first installation only: it refuses a repository that already has
`engine/`, `.github/` or `user/engine.lock`. Updates come through the update PR (§5).
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

from engine.checks import gitio, numbers_check
from engine.checks.lock_check import LOCK_FILE, release_key

SOURCE = "engine"
WORKFLOWS = "engine/workflows/"
INSTALLED_WORKFLOWS = ".github/workflows/"
PUBLISHED_FILES = "files"
PUBLISHED_TAG_RE = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")   # as the workflow `tag`
MANIFEST_FILE = "release-manifest.json"
CHAT_FIELD = "engine/rules/fields/chat.md"
RULES = "engine/rules/"
_CHAT_RULES_RE = re.compile(r"https://raw\.githubusercontent\.com/([^/\s]+/[^/\s]+)/(?:refs/tags/)?([^/\s]+)/"
                            + PUBLISHED_FILES + "/" + RULES)
_RULE_FILE_RE = re.compile(r"(?<![\w./-])([A-Za-z][\w-]*\.md)(?![\w/-])")
_LINK_HINT_RE = re.compile(r"raw\.githubusercontent\.com|memory-engine-releases", re.IGNORECASE)


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
    to the release until its channel's delay has passed from then (§5)."""
    release_key(tag)
    if not PUBLISHED_TAG_RE.fullmatch(tag):
        raise ValueError(f"release tag {tag!r} has a leading zero: the releases repository's workflow `tag` refuses it")
    if published.tzinfo is None:
        raise ValueError("the publish time needs its UTC offset")
    return {
        "release": tag,
        "source": source,
        "published": published.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": {path: hashlib.sha256(files[path]).hexdigest() for path in sorted(files)},
    }


def dumps(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


# What `cut` lets go public (owner, 09.11.2026: the engine's code is public). Every number of
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
    cannot be checked and so may not go either (Kimi, review of PR #29). Names the place and
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
            for kind, value in kinds:
                if value not in PUBLIC_TEST_NUMBERS:
                    found.append(f"{path} line {no}: {kind} that is not a listed test value")
            for m in numbers_check.EMAIL_RE.finditer(line):
                if not _reserved(m.group()) and m.group() not in PUBLIC_TEST_ADDRESSES:
                    found.append(f"{path} line {no}: an e-mail address off the test domains")
            if _SECRET_RE.search(line):
                found.append(f"{path} line {no}: what looks like a token")
    return found


def chat_rules(files: dict[str, bytes]) -> tuple[str, str, list[str]] | None:
    """The public folder the chat field sends the chat to for its topic rules: (repository,
    tag, the files named on the link's line), or None when the field has no such link."""
    try:
        text = files.get(CHAT_FIELD, b"").decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{CHAT_FIELD} is not valid UTF-8") from None
    links, rest = [], []
    for line in text.splitlines():
        for m in _CHAT_RULES_RE.finditer(line):
            names = sorted(set(_RULE_FILE_RE.findall(line[:m.start()] + " " + line[m.end():])))
            links.append((m.group(1), m.group(2), names))
        rest.append(_CHAT_RULES_RE.sub(" ", line))
    if len(links) > 1:
        raise ValueError(f"{CHAT_FIELD} links the public rules {len(links)} times; one link, one tag")
    # A mention the pattern did not read — beside a link it did read, or alone — is refused,
    # never skipped: the chat could follow it to rules no check has held (Kimi, PR #39).
    if _LINK_HINT_RE.search("\n".join(rest)):
        raise ValueError(f"{CHAT_FIELD} points to the public rules, but not as "
                         f"https://raw.githubusercontent.com/<repository>/<tag>/{PUBLISHED_FILES}/{RULES}")
    return links[0] if links else None


def chat_rules_findings(files: dict[str, bytes], tag: str, into: Path, repository: str) -> list[str]:
    """Why the chat field's link would send the chat to rules this release does not hold:
    another repository, not a release tag, a newer release, a named file the release lacks,
    or a file that changed since the release the link names. A link to this very release
    is right by construction. The older release is read from `into`, a checkout with its
    tags: a tag missing there is a finding too, never a pass."""
    link = chat_rules(files)
    if link is None:
        return []
    linked_repo, at, names = link
    found = []
    if linked_repo != repository:
        found.append(f"{CHAT_FIELD} links {linked_repo}, not {repository}")
    if not PUBLISHED_TAG_RE.fullmatch(at):
        return found + [f"{CHAT_FIELD} links {at!r}, which is not a release tag"]
    if not names:
        found.append(f"{CHAT_FIELD} names no rules file on the line of its link")
    found += [f"{CHAT_FIELD} names {name}, which is not in {RULES}" for name in names if RULES + name not in files]
    if found or at == tag:
        return found
    if release_key(at) > release_key(tag):
        return [f"{CHAT_FIELD} links {at}, newer than {tag}"]
    for name in names:
        try:
            old = gitio.git(into, "show", f"refs/tags/{at}:{PUBLISHED_FILES}/{RULES}{name}")
        except gitio.GitError:
            found.append(f"{PUBLISHED_FILES}/{RULES}{name} at the tag {at} is not in {into}: fetch its tags")
            continue
        if old != files[RULES + name]:
            found.append(f"{RULES}{name} changed since {at}, the copy the chat reads: link {tag} in {CHAT_FIELD}")
    return found


def cut(files: dict[str, bytes], tag: str, source: str, published: datetime, into: Path,
        repository: str) -> list[str]:
    """Write the release into a checkout of the public repository of releases: its files under
    files/ and its manifest. Returns the paths written, the manifest last. Everything is
    checked before anything is removed or written: the tag, the release's content
    (`publication_findings`), that `into` is a checkout of `repository` — the one
    `release.manifests` names — and that neither files/ nor the manifest is a link out of it.
    files/ is built beside the old one and swapped in, so a write that fails half-way leaves
    the old release in place."""
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
    target, staging, manifest_path = into / PUBLISHED_FILES, into / f".{PUBLISHED_FILES}.new", into / MANIFEST_FILE
    for path in (target, staging, manifest_path):
        if path.is_symlink():
            raise ValueError(f"{path} is a link")
    if target.exists() and not target.is_dir():
        raise ValueError(f"{target} is not a folder")
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
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    manifest_path.write_text(dumps(data), encoding="utf-8")
    written.append(MANIFEST_FILE)
    return written


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


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("manifest", "cut", "install"):
        p = sub.add_parser(name)
        p.add_argument("--repo", type=Path, default=Path("."), help="the template")
        p.add_argument("--rev", required=True, help="the template commit the release is cut from")
        p.add_argument("--tag", required=True, help="vMAJOR.MINOR.PATCH")
    sub.choices["install"].add_argument("--into", type=Path, required=True, help="the person's repository")
    sub.choices["cut"].add_argument("--into", type=Path, required=True,
                                    help="a checkout of the public repository of releases")
    args = ap.parse_args(argv)
    try:
        source = commit_of(args.repo, args.rev)
        files = release_files(args.repo, source)
        if args.command == "manifest":
            sys.stdout.write(dumps(manifest(files, args.tag, source, now or datetime.now(timezone.utc))))
        elif args.command == "cut":
            repository = tomllib.loads(files["engine/defaults.toml"].decode("utf-8"))["release"]["manifests"]
            for path in cut(files, args.tag, source, now or datetime.now(timezone.utc), args.into, repository):
                print(path)
        else:
            for path in install(files, args.tag, args.into):
                print(path)
    except (ValueError, KeyError, tomllib.TOMLDecodeError, gitio.GitError, OSError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

`install` is the first installation only: it refuses a repository that already has
`engine/`, `.github/` or `user/engine.lock`. Updates come through the update PR (§5).

`template` writes the public template a person makes her repository from with «Use this
template» (DESIGN_PHASE3 §5, step 1): the release's files, its lock, and under `user/` nothing
else — anything more there would make every repository made from it «installed» from its first
commit, and `setup` would refuse it — plus the template's README (`docs/template/README.md`,
the steps on a phone) and the licence. Only a release already published, and only from the
commit it was cut from: the manifest at its tag, read from a checkout of the repository of
releases that has fetched its tags from GitHub, names that commit and its files, so C1 in her
repository holds the engine she got to the release she got, and the README is that release's.
The template's `engine/`, `.github/` and `user/` are replaced whole at every release — nothing
added there by hand survives; any other entry at its top refuses the run before anything is
removed.
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
SOLO_FIELD = "engine/rules/fields/solo.md"       # the chat that writes itself (DESIGN_PHASE3 §6a)
CHAT_FIELDS = (CHAT_FIELD, SOLO_FIELD)
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
    sub.choices["template"].add_argument("--into", type=Path, required=True,
                                         help="a checkout of the template's public repository")
    sub.choices["template"].add_argument("--releases", type=Path, required=True,
                                         help="a checkout of the repository of releases, with its tags")
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
        elif args.command == "template":
            tree = template_tree(args.repo, source, files, args.tag, published_manifest(args.releases, args.tag))
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

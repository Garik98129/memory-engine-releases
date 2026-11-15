#!/usr/bin/env bash
# Runs tag-release.sh against a throwaway origin: bash .github/scripts/test-tag-release.sh
# Every release here is a whole one in small — files/ with its topics, the manifest, the index
# its topics give — because the script asks the code that moves `stable` (stable.py beside it)
# whether a release is whole before it tags.
set -euo pipefail
scripts="$(cd "$(dirname "$0")" && pwd)"
script="$scripts/tag-release.sh"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid \
       GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
export GITHUB_REPOSITORY=owner/name
src=0123456789abcdef0123456789abcdef01234567
git init -q --bare "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/w" 2>/dev/null
cd "$tmp/w"
git commit -q --allow-empty -m start && git push -q origin HEAD:main
start=$(git rev-parse HEAD)
mkdir -p .github/scripts && cp "$scripts/stable.py" .github/scripts/stable.py      # as this repository holds it: for copy-holds.py, below
git add -A && git commit -q -m "the copy of the code that moves stable" && git push -q origin HEAD:main
fails=0
commit() {    # a commit that is the release $1 cut from source $2 — whole, unless the caller's environment says
              # otherwise (below); $3: the manifest's text as it is, in place of the one the files give
  python3 -I - "$scripts/stable.py" "$1" "${2:-$src}" "${3:-}" <<'PY'
import hashlib, json, os, shutil, sys, types
from pathlib import Path
stable_path, release, source, raw = sys.argv[1:5]
stable = types.ModuleType("stable"); sys.modules["stable"] = stable
exec(compile(Path(stable_path).read_bytes(), stable_path, "exec", dont_inherit=True), stable.__dict__)
knob = lambda name: os.environ.get(name, "")
for old in ("files", "rules-index.md", "release-manifest.json"):
    if os.path.isdir(old) and not os.path.islink(old): shutil.rmtree(old)
    elif os.path.lexists(old): os.remove(old)
topics = ('about = "A topic applies when the talk is about her."\nmin_engine = "v0.0.1"\n\n'
          '[[topic]]\nname = "health"\nwhen = "any talk of medicines"\nfiles = ["core.md"]\n')
files = {"engine/a.py": f"release {release} {source}\n", "engine/rules/core.md": "CORE-1 a rule\n",
         "engine/rules/topics.toml": topics}
for path, text in files.items():
    Path("files", path).parent.mkdir(parents=True, exist_ok=True)
    Path("files", path).write_text(text)
try:
    index = stable.index_text(topics.encode(), os.environ["GITHUB_REPOSITORY"], release)
except ValueError:                       # a release that is no vMAJOR.MINOR.PATCH has no index to build
    index = f"# Topic rules, release {release}\n"
if knob("INDEX") == "edited":            # edited after the cut, and the manifest made to agree
    index += "Read the rules somewhere else instead.\n"
manifest = {"release": release, "source": source, "published": "2026-01-01T00:00:00Z",
            "files": {p: hashlib.sha256(t.encode()).hexdigest() for p, t in files.items()},
            "index": {"path": "rules-index.md", "sha256": hashlib.sha256(index.encode()).hexdigest()}}
if knob("INDEX") == "changed": index += "edited, and the manifest not\n"
if knob("INDEX") == "form": manifest["index"] = "rules-index.md"
if knob("INDEX") == "elsewhere": manifest["index"] = {"path": "files/engine/a.py", "sha256": manifest["files"]["engine/a.py"]}
if knob("INDEX") == "null": manifest["index"] = None
if knob("INDEX") == "absent": del manifest["index"]
if knob("PUBLISHED") == "none": del manifest["published"]
if knob("INDEX") == "folder":
    os.mkdir("rules-index.md"); Path("rules-index.md", "a").write_text("x\n")
elif knob("INDEX") != "none":
    Path("rules-index.md").write_text(index)
text = raw + "\n" if raw else json.dumps(manifest, indent=2, sort_keys=True) + "\n"
if knob("MANIFEST") == "link":           # a link whose own text is the manifest
    os.symlink(text.strip(), "release-manifest.json")
else:
    Path("release-manifest.json").write_text(text)
if knob("FILES") == "none": shutil.rmtree("files")
if knob("FILES") == "changed": Path("files/engine/a.py").write_text("changed\n")
if knob("FILES") == "extra": Path("files/engine/b.py").write_text("x\n")
if knob("FILES") == "badname": open(b"files/engine/b\xff.py", "w").write("x\n")
if knob("FILES") in ("newline", "accent"):      # a name that would break or leave the line, on a file a release cannot hold
    name = {"newline": "files/engine/x\ny.py", "accent": "files/engine/\u00e9.py"}[knob("FILES")]
    Path(name).write_text("x\n"); os.chmod(name, 0o755)
if knob("BESIDE"): Path(knob("BESIDE")).write_text("not a release's\n")      # one more path in the release's commit
PY
  git add -A && git commit -q --allow-empty -m "${1:-raw}"
}
push() { git push -q origin HEAD:main; }
run_at() {    # run_at <commit> <ok|fail> <name> [words the run must say] — as the workflow runs, in a fresh checkout
  local at=$1 want=$2 name=$3 says=${4:-} got=ok
  rm -rf "$tmp/ci" && git clone -q "$tmp/origin.git" "$tmp/ci" 2>/dev/null
  (cd "$tmp/ci" && git checkout -q "$at" && bash "${SCRIPT:-$script}" "$at") >"$tmp/out" 2>&1 || got=fail
  if [ "$got" != "$want" ]; then echo "FAIL $name: wanted $want, got $got"; cat "$tmp/out"; fails=$((fails + 1)); return; fi
  # a red run's reason is looked for on its own last line — «::error::…» — not among the warnings
  # of the commits refused before it, which say other things
  local where="$tmp/out"
  if [ "$want" = fail ] && grep -q '^::error::' "$tmp/out"; then grep '^::error::' "$tmp/out" > "$tmp/out.error"; where="$tmp/out.error"; fi
  if [ -n "$says" ] && ! grep -qF -- "$says" "$where"; then
    echo "FAIL $name: the run did not say «$says»"; cat "$tmp/out"; fails=$((fails + 1)); return
  fi
  echo "ok   $name"
}
expect() { run_at "$(git rev-parse HEAD)" "$@"; }
tag_of() { git ls-remote origin "refs/tags/$1" | cut -f1; }
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; cat "$tmp/out"; fails=$((fails + 1)); fi; }

for v in v0.1.0 v0.1.1 v0.1.9; do commit "$v"; git tag "$v"; done; push; git push -q origin --tags
git tag vtest HEAD && git tag v1.0 HEAD && git push -q origin vtest v1.0          # stray tags, not releases
commit v0.1.10; push; expect ok "a new release is tagged on its commit (v0.1.10 after v0.1.9; vtest and v1.0 ignored)"
check "v0.1.10 is on its commit" '[ "$(tag_of v0.1.10)" = "$(git rev-parse HEAD)" ]'
expect ok "a run again on a tagged release does nothing" "nothing to tag"
commit v0.1.10 fedcba9876543210fedcba9876543210fedcba98; push; expect fail "a release whose tag is on another commit is refused" "exists already"
commit v0.1.2;  push; expect fail "a release older than the newest is refused" "is not newer than v0.1.10"
commit 0.2.0;   push; expect fail "a release without the v is refused" "is not vMAJOR.MINOR.PATCH"
commit v0.2;    push; expect fail "a release without its patch number is refused" "is not vMAJOR.MINOR.PATCH"
commit x "" '{"release": "v0.2.0\n0123456789abcdef0123456789abcdef01234567", "source": "junk"}'
                push; expect fail "a release with a line break in it is refused" "is not vMAJOR.MINOR.PATCH"
commit v0.2.0 notacommit; push; expect fail "a manifest whose source is not a commit sha is refused" "is not a 40-hex commit sha"
commit x "" '{not json'; push; expect fail "a manifest that does not parse is refused" "does not parse"
FILES=none    commit v0.2.0 1111111111111111111111111111111111111111; push; expect fail "a release without its files is refused" "has no files/"
FILES=changed commit v0.2.0 2222222222222222222222222222222222222222; push; expect fail "a release whose file differs from the manifest is refused" "different ['engine/a.py']"
FILES=extra   commit v0.2.0 3333333333333333333333333333333333333333; push; expect fail "a release with a file the manifest does not list is refused" "extra ['engine/b.py']"
FILES=badname commit v0.2.0 4444444444444444444444444444444444444444; push; expect fail "a path under files/ that is not valid UTF-8 is refused" "is not valid UTF-8"
check "no refused release left a tag" '[ "$(git ls-remote --tags origin "v0.2*" | wc -l)" = 0 ]'
first=$(commit v0.2.0 && git rev-parse HEAD); second=$(commit v0.2.1 && git rev-parse HEAD)
push; expect ok "two releases in one push are both tagged; the refusals before them are superseded"
check "v0.2.0 is on the first and v0.2.1 on the second" '[ "$(tag_of v0.2.0)" = "$first" ] && [ "$(tag_of v0.2.1)" = "$second" ]'
a=$(commit v0.3.0 && git rev-parse HEAD); push
b=$(commit v0.3.1 && git rev-parse HEAD); push
run_at "$b" ok "the later push's run comes first and tags both releases"
run_at "$a" ok "the earlier push's run, coming late, finds its release tagged" "nothing to tag"
check "v0.3.0 is on its commit and v0.3.1 on its" '[ "$(tag_of v0.3.0)" = "$a" ] && [ "$(tag_of v0.3.1)" = "$b" ]'
commit x "" 'null'; push; expect fail "a manifest that is not a JSON object is refused, not a crash" "is not a JSON object"
v040=$(commit v0.4.0 && git rev-parse HEAD); push
expect ok "a release after a manifest that is not an object is tagged"
check "v0.4.0 is on its commit" '[ "$(tag_of v0.4.0)" = "$v040" ]'
git rm -q release-manifest.json && git commit -q -m "manifest removed"; push
expect fail "a commit that removes the manifest is refused, not a crash" "has no release-manifest.json"
v041=$(commit v0.4.1 && git rev-parse HEAD); push
expect ok "a release after a removed manifest is tagged"
check "v0.4.1 is on its commit" '[ "$(tag_of v0.4.1)" = "$v041" ]'
git commit -q --allow-empty -m "readme only"; push; expect ok "a push that does not change the manifest tags nothing" "nothing to tag"

# whole, as the code that moves `stable` reads a release: what it would refuse a week later gets no tag today
# (each refused commit has a source of its own: only a commit that changes the manifest is looked at)
INDEX=changed   commit v0.5.0 5555555555555555555555555555555555555550; push; expect fail "an index edited after the cut is refused" "is not the one its manifest holds"
INDEX=edited    commit v0.5.0 5555555555555555555555555555555555555551; push; expect fail "an index edited and its manifest made to agree is refused" "is not the index of its own topics"
INDEX=none      commit v0.5.0 5555555555555555555555555555555555555552; push; expect fail "a manifest that names an index the commit lacks is refused" "has no rules-index.md"
INDEX=form      commit v0.5.0 5555555555555555555555555555555555555553; push; expect fail "an index the manifest names in another form is refused" "does not name its index as rules-index.md"
INDEX=elsewhere commit v0.5.0 5555555555555555555555555555555555555554; push; expect fail "an index the manifest names at another path is refused" "does not name its index as rules-index.md"
INDEX=folder    commit v0.5.0 5555555555555555555555555555555555555555; push; expect fail "an index that is a folder is refused" "is not a regular file"
INDEX=absent    commit v0.5.0 5555555555555555555555555555555555555556; push; expect fail "a release with no topic index is refused" "names no topic index"
INDEX=null      commit v0.5.0 5555555555555555555555555555555555555557; push; expect fail "and so is one whose manifest says the index is nothing" "names no topic index"
PUBLISHED=none  commit v0.5.0 5555555555555555555555555555555555555558; push; expect fail "a manifest that does not say when it was published is refused" "has no valid"
MANIFEST=link   commit v0.5.0 5555555555555555555555555555555555555559; push; expect fail "a manifest that is a link whose text is a manifest is refused" "is not a regular file"
BESIDE=README.md commit v0.5.0 555555555555555555555555555555555555555a; push; expect fail "a release whose commit changes one more path is refused" "changes more than a release: README.md"
git rm -q README.md && git commit -q -m "that path, taken away again"; push
GITHUB_REPOSITORY=other/name commit v0.5.0 555555555555555555555555555555555555555b; push
expect fail "an index built for another repository is refused" "built for another repository"
commit v0.5.0 555555555555555555555555555555555555555c; push
mkdir "$tmp/alone" && cp "$script" "$tmp/alone/tag-release.sh"
SCRIPT="$tmp/alone/tag-release.sh" expect fail "without the code that moves stable beside it, the script tags nothing" "could not be read by the code that moves stable"
check "no refused release left a tag" '[ -z "$(tag_of v0.5.0)" ]'
(unset GITHUB_REPOSITORY; was=$fails; expect fail "with no repository named, the script tags nothing" "GITHUB_REPOSITORY is not set"; [ "$fails" = "$was" ]) || fails=$((fails + 1))
check "still no tag" '[ -z "$(tag_of v0.5.0)" ]'
check "and reading a release left no compiled file beside the scripts" '[ ! -e "$scripts/__pycache__" ] && [ ! -e "$tmp/ci/.github/scripts/__pycache__" ]'
GITHUB_REPOSITORY=other/name expect fail "a whole release is not this repository's when the run is another repository's" "built for another repository"
expect ok "the same commit, read by the script as it stands in this repository, is tagged"
check "v0.5.0 is on its commit" '[ "$(tag_of v0.5.0)" = "$(git rev-parse HEAD)" ]'

# what a refusal says reaches the log as one plain line
commit x "" '{"release": "v0.5.1%0A::error::forged ##[error]old form \u001b[31m", "source": "junk"}'
push; expect fail "a refusal's words reach the log escaped" "v0.5.1%250A::error::forged \\x23\\x23[error]old form \\x1b[31m"
check "on the warning's line as on the error's, and with no «#» left to open the runner's older form of a command" \
  'grep -q "^::warning::.*v0.5.1%250A::error::forged" "$tmp/out" && ! grep -q "##\[" "$tmp/out"'
FILES=newline commit v0.5.1 5555555555555555555555555555555555555561; push
expect fail "a name with a line break in it does not cut the reason short" "files/engine/x y.py has mode 100755"
FILES=accent  commit v0.5.1 5555555555555555555555555555555555555562; push
expect fail "a name outside ASCII is said escaped" "files/engine/\\xe9.py has mode 100755"

# a file of the checkout named like a module: brought by a commit of its own, there when the release is read
printf 'import sys\nprint("OK v9.9.9 %s")\nsys.exit(0)\n' "$src" > json.py
printf 'raise SystemExit("planted")\n' > re.py        # and one the listing of the tags would import
git add json.py re.py && git commit -q -m "two files named like modules"
v051=$(commit v0.5.1 && git rev-parse HEAD); push
expect ok "a file of the checkout named like a module is not run in the module's place"
check "v0.5.1 is on its commit, and the planted answer set no tag" '[ "$(tag_of v0.5.1)" = "$v051" ] && [ -z "$(tag_of v9.9.9)" ]'
git rm -q json.py re.py && git commit -q -m "and taken away"; push

# main's own line: a branch merged by a merge commit is tagged on the merge, and what it held on its way is not
git checkout -q -b branch
draft=$(commit v0.6.0 6666666666666666666666666666666666666660 && git rev-parse HEAD)      # nobody read this one as the release
final=$(commit v0.6.0 6666666666666666666666666666666666666661 && git rev-parse HEAD)
git checkout -q -B main origin/main && git merge -q --no-ff --no-edit branch && push
merge=$(git rev-parse HEAD)
expect ok "a release merged by a merge commit is tagged"
check "on the merge, which holds what main took — not on a commit of its branch" '[ "$(tag_of v0.6.0)" = "$merge" ] && [ "$merge" != "$final" ] && [ "$merge" != "$draft" ]'
git checkout -q -b two
commit v0.6.1 6666666666666666666666666666666666666662; inner=$(git rev-parse HEAD)
commit v0.6.2 6666666666666666666666666666666666666663
git checkout -q -B main origin/main && git merge -q --no-ff --no-edit two && push
expect ok "a branch that held two releases is one release: the one main took"
check "v0.6.2 is on the merge and v0.6.1 was never tagged" '[ "$(tag_of v0.6.2)" = "$(git rev-parse HEAD)" ] && [ -z "$(tag_of v0.6.1)" ] && [ -n "$inner" ]'
git checkout -q -b mixed
commit v0.6.3 6666666666666666666666666666666666666664; printf 'x\n' > NOTES.md && git add NOTES.md && git commit -q -m "and a file of its own"
git checkout -q -B main origin/main && git merge -q --no-ff --no-edit mixed && push
expect fail "a merge that brings a release and one more path is refused" "changes more than a release: NOTES.md"
check "and has no tag" '[ -z "$(tag_of v0.6.3)" ]'
git revert -m 1 --no-edit HEAD >/dev/null && push
expect ok "main taken back to the release before, exactly as its tag has it, is nothing to tag and no red run" "holds v0.6.2 again"
check "v0.6.2 stays where it was" '[ "$(tag_of v0.6.2)" != "$(git rev-parse HEAD)" ] && [ -n "$(tag_of v0.6.2)" ]'

# commits that land on main's line one by one — a branch merged by rebase, or pushed: a release named twice is the later cut
draft=$(commit v0.7.0 7777777777777777777777777777777777777770 && git rev-parse HEAD)
final=$(commit v0.7.0 7777777777777777777777777777777777777771 && git rev-parse HEAD)
push; expect ok "of two commits that name one release the later is the release" "passed over $draft"
check "v0.7.0 is on the cut main ended with" '[ "$(tag_of v0.7.0)" = "$final" ]'
draft=$(commit v0.7.1 7777777777777777777777777777777777777772 && git rev-parse HEAD)
FILES=changed commit v0.7.1 7777777777777777777777777777777777777773; push
expect fail "and when the later cut is broken, the earlier one does not get the tag in its place" "different ['engine/a.py']"
check "v0.7.1 has no tag" '[ -z "$(tag_of v0.7.1)" ] && [ -n "$draft" ]'
git checkout -q "$final" -- files release-manifest.json && printf 'and a line more\n' >> rules-index.md && git add -A && git commit -q -m "v0.7.0 again, but for its index"; push
expect fail "a commit that holds a tagged release in all but its index is not that release again" "tag v0.7.0 exists already"
git checkout -q "$final" -- rules-index.md && git commit -q -m "and its index too" && push      # the manifest is not changed: nothing to look at

# copy-holds.py: a changed copy of the code that moves stable, against the releases tagged above
holds="$scripts/copy-holds.py"
rm -rf "$tmp/hold" && git clone -q "$tmp/origin.git" "$tmp/hold" 2>/dev/null
copy() {      # copy <exit wanted> <name> <words it must say> <base> <the copy the pull request brings>
  local want=$1 name=$2 says=$3 got=0
  (cd "$tmp/hold" && python3 -I "$holds" "$4" --repository owner/name --copy "$5") >"$tmp/out" 2>&1 || got=$?
  if [ "$got" != "$want" ]; then echo "FAIL $name: wanted exit $want, got $got"; cat "$tmp/out"; fails=$((fails + 1)); return; fi
  if ! grep -qF -- "$says" "$tmp/out"; then echo "FAIL $name: it did not say «$says»"; cat "$tmp/out"; fails=$((fails + 1)); return; fi
  echo "ok   $name"
}
copy 0 "the copy main has is nothing to compare" "nothing changed in it" refs/remotes/origin/main "$scripts/stable.py"
copy 0 "before this repository had a copy there is nothing to compare" "has no .github/scripts/stable.py yet" "$start" "$scripts/stable.py"
{ cat "$scripts/stable.py"; printf '# a comment more\n'; } > "$tmp/copy-comment.py"
copy 0 "a copy changed in what judges nothing holds every release" "COPY holds: every one of the 15 releases" refs/remotes/origin/main "$tmp/copy-comment.py"
sed 's/Not at the branch this index came/Never at the branch this index came/' "$scripts/stable.py" > "$tmp/copy-words.py"
check "(the copy for the next case differs in one word of the index)" '! cmp -s "$scripts/stable.py" "$tmp/copy-words.py"'
copy 1 "a copy that builds the index in other words strands the releases cut before it, and is red" \
  "v0.6.2 is whole by the copy before and refused by this one" refs/remotes/origin/main "$tmp/copy-words.py"
check "every one of them is named, and what it means is said" '[ "$(grep -c "is whole by the copy before" "$tmp/out")" = 15 ] && grep -q "could not stay on or move back to 15 of the releases" "$tmp/out"'
sed 's/^DELAY = .*/DELAY = timedelta(days=1)/' "$scripts/stable.py" > "$tmp/copy-delay.py"
copy 0 "a copy that changes when a release is due, and not what a whole one is, holds them" "COPY holds" refs/remotes/origin/main "$tmp/copy-delay.py"
printf 'def broken(:\n' > "$tmp/copy-broken.py"
copy 2 "a copy that is no code cannot be compared" "COPY ERROR" refs/remotes/origin/main "$tmp/copy-broken.py"
printf 'x = 1\n' > "$tmp/copy-empty.py"
copy 2 "nor one that no longer has what the comparison reads" "COPY ERROR" refs/remotes/origin/main "$tmp/copy-empty.py"
copy 2 "nor one that is not there" "COPY ERROR" refs/remotes/origin/main "$tmp/no-such-copy.py"
# the branch stable as it stands: moved here by the copy itself, then read by a copy whose record has another form
(cd "$tmp/hold" && python3 -I "$scripts/stable.py" --repository owner/name --to v0.7.0 --reason "the branch, for the test") >"$tmp/out" 2>&1 || true
check "(the copy moved the branch stable of the test's origin)" 'grep -q "^STABLE moved: none -> v0.7.0" "$tmp/out"'
copy 0 "with a branch stable there, a copy changed in what judges nothing still holds" "and the branch stable is read as before" refs/remotes/origin/main "$tmp/copy-comment.py"
sed 's/^RECORD = frozenset({/RECORD = frozenset({"one more", /' "$scripts/stable.py" > "$tmp/copy-record.py"
check "(the copy for the next case wants one more key in the record)" '! cmp -s "$scripts/stable.py" "$tmp/copy-record.py"'
copy 1 "a copy that would not read the branch as it stands is red" "the branch stable as it stands is read by the copy before and is not this one's" refs/remotes/origin/main "$tmp/copy-record.py"
{ grep -v '^from __future__ import annotations' "$scripts/stable.py"; printf 'def probe(x: NoSuchName = 1): pass\n'; } > "$tmp/copy-annotation.py"
copy 2 "a copy is compiled as it would run: one that leans on a future import it does not make is no code" "COPY ERROR" refs/remotes/origin/main "$tmp/copy-annotation.py"
check "and comparing left no compiled file" '[ ! -e "$scripts/__pycache__" ] && [ -z "$(find "$tmp" -name __pycache__)" ]'
exit $fails

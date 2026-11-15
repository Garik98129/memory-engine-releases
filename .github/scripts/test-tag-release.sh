#!/usr/bin/env bash
# Runs tag-release.sh against a throwaway origin: bash .github/scripts/test-tag-release.sh
set -euo pipefail
script="$(cd "$(dirname "$0")" && pwd)/tag-release.sh"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid \
       GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
src=0123456789abcdef0123456789abcdef01234567
git init -q --bare "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/w" 2>/dev/null
cd "$tmp/w"
git commit -q --allow-empty -m start && git push -q origin HEAD:main
fails=0
commit() {    # a commit whose manifest has release $1 and source $2 (raw JSON text: $3), with files/
              # INDEX: its topic index and the manifest's word of it; PLANT: a file beside them named like a module
  rm -rf files rules-index.md json.py && mkdir -p files/engine && printf 'release %s %s\n' "$1" "${2:-$src}" > files/engine/a.py
  local sha index=""; sha=$(sha256sum files/engine/a.py | cut -d' ' -f1)
  if [ -n "${INDEX:-}" ]; then
    printf '# Topic rules, release %s\n' "$1" > rules-index.md
    index=", \"index\": {\"path\": \"rules-index.md\", \"sha256\": \"$(sha256sum rules-index.md | cut -d' ' -f1)\"}"
    [ "$INDEX" = form ] && index=', "index": "rules-index.md"'
    [ "$INDEX" = elsewhere ] && index=", \"index\": {\"path\": \"files/engine/a.py\", \"sha256\": \"$sha\"}"
    [ "$INDEX" = changed ] && printf 'edited after the cut\n' >> rules-index.md
    [ "$INDEX" = none ] && rm rules-index.md
    [ "$INDEX" = folder ] && rm rules-index.md && mkdir rules-index.md && printf 'x\n' > rules-index.md/a
  fi
  if [ -n "${3:-}" ]; then printf '%s\n' "$3" > release-manifest.json
  else printf '{"release": "%s", "source": "%s", "files": {"engine/a.py": "%s"}%s}\n' "$1" "${2:-$src}" "$sha" "$index" > release-manifest.json; fi
  [ "${FILES:-same}" = none ] && rm -rf files
  [ "${FILES:-same}" = changed ] && printf 'changed\n' > files/engine/a.py
  [ "${FILES:-same}" = extra ] && printf 'x\n' > files/engine/b.py
  [ "${FILES:-same}" = badname ] && printf 'x\n' > "files/engine/$(printf 'b\377').py"
  [ -n "${PLANT:-}" ] && printf 'import sys\nprint("OK v9.9.9 %s")\nsys.exit(0)\n' "$src" > json.py
  git add -A && git commit -q --allow-empty -m "${1:-raw}"
}
push() { git push -q origin HEAD:main; }
run_at() {    # run_at <commit> <ok|fail> <name> [words the run must say] — as the workflow runs, in a fresh checkout
  local at=$1 want=$2 name=$3 says=${4:-} got=ok
  rm -rf "$tmp/ci" && git clone -q "$tmp/origin.git" "$tmp/ci" 2>/dev/null
  (cd "$tmp/ci" && git checkout -q "$at" && bash "$script" "$at") >"$tmp/out" 2>&1 || got=fail
  if [ "$got" != "$want" ]; then echo "FAIL $name: wanted $want, got $got"; cat "$tmp/out"; fails=$((fails + 1)); return; fi
  if [ -n "$says" ] && ! grep -qF -- "$says" "$tmp/out"; then
    echo "FAIL $name: the run did not say «$says»"; cat "$tmp/out"; fails=$((fails + 1)); return
  fi
  echo "ok   $name"
}
expect() { run_at "$(git rev-parse HEAD)" "$@"; }
tag_of() { git ls-remote origin "refs/tags/$1" | cut -f1; }
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }

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

# the topic index, beside files/: the manifest names it, and it is that one or the release has no tag
# (each refused commit has a source of its own: only a commit that changes the manifest is looked at)
v050=$(INDEX=same commit v0.5.0 && git rev-parse HEAD); push
expect ok "a release with its topic index is tagged"
check "v0.5.0 is on its commit" '[ "$(tag_of v0.5.0)" = "$v050" ]'
INDEX=changed   commit v0.5.1 4444444444444444444444444444444444444444; push; expect fail "an index edited after the cut is refused" "is not the one release-manifest.json names"
INDEX=none      commit v0.5.1 5555555555555555555555555555555555555555; push; expect fail "a manifest that names an index the commit lacks is refused" "has no rules-index.md"
INDEX=form      commit v0.5.1 6666666666666666666666666666666666666666; push; expect fail "an index the manifest names in another form is refused" "does not name its index as rules-index.md"
INDEX=elsewhere commit v0.5.1 7777777777777777777777777777777777777777; push; expect fail "an index the manifest names at another path is refused" "does not name its index as rules-index.md"
INDEX=folder    commit v0.5.1 8888888888888888888888888888888888888888; push; expect fail "an index that is a folder is refused" "the index is a regular file"
FILES=badname   commit v0.5.1 9999999999999999999999999999999999999999; push; expect fail "a path under files/ that is not valid UTF-8 is refused" "is not valid UTF-8"
check "no refused release left a tag" '[ -z "$(tag_of v0.5.1)" ]'
commit x "" '{"release": "v0.5.1%0A::error::forged", "source": "junk"}'
push; expect fail "a refusal's words reach the log escaped" "v0.5.1%250A::error::forged"
check "and no line of that run is a command the manifest wrote" '! grep -q "^::error::forged" "$tmp/out"'
v051=$(PLANT=json INDEX=same commit v0.5.1 && git rev-parse HEAD); push
expect ok "a file of the checkout named like a module is not run in the module's place"
check "v0.5.1 is on its commit, and the planted answer set no tag" '[ "$(tag_of v0.5.1)" = "$v051" ] && [ -z "$(tag_of v9.9.9)" ]'
exit $fails

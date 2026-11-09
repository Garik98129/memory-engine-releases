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
fails=0
release() {   # a commit on main whose manifest names $1 (and source $2)
  printf '{"release": "%s", "source": "%s", "files": {}}\n' "$1" "${2:-$src}" > release-manifest.json
  git add release-manifest.json && git commit -q --allow-empty -m "$1" && git push -q origin HEAD:main
}
expect() {    # expect <ok|fail> <tag that must exist afterwards or "-"> <name> [words the run must say]
  local want=$1 tag=$2 name=$3 says=${4:-} got=ok
  bash "$script" "$(git rev-parse HEAD)" >"$tmp/out" 2>&1 || got=fail
  if [ "$got" != "$want" ]; then echo "FAIL $name: wanted $want, got $got"; cat "$tmp/out"; fails=$((fails + 1)); return; fi
  if [ -n "$says" ] && ! grep -qF -- "$says" "$tmp/out"; then
    echo "FAIL $name: the run did not say «$says»"; cat "$tmp/out"; fails=$((fails + 1)); return
  fi
  if [ "$tag" != - ] && [ "$(git ls-remote origin "refs/tags/$tag" | cut -f1)" != "$(git rev-parse HEAD)" ]; then
    echo "FAIL $name: $tag is not on HEAD"; fails=$((fails + 1)); return
  fi
  echo "ok   $name"
}
for v in v0.1.0 v0.1.1 v0.1.9; do release "$v"; git tag "$v"; git push -q origin "refs/tags/$v"; done
release v0.1.10;           expect ok   v0.1.10 "a new release is tagged on its commit (v0.1.10 after v0.1.9)"
release v0.1.10;           expect fail -       "a release whose tag exists is refused" "exists already"
release v0.1.2;            expect fail -       "a release older than the newest is refused" "is not newer than v0.1.10"
release 0.2.0;             expect fail -       "a release without the v is refused" "is not vMAJOR.MINOR.PATCH"
release v0.2;              expect fail -       "a release without its patch number is refused" "is not vMAJOR.MINOR.PATCH"
release "v0.2.0 x";        expect fail -       "a release with more in it is refused" "is not vMAJOR.MINOR.PATCH"
release v0.2.0 notacommit; expect fail -       "a manifest whose source is not a commit is refused" "is not a commit"
[ "$(git ls-remote --tags origin 'v0.2*' | wc -l)" = 0 ] && echo "ok   no refused release left a tag" || { echo "FAIL a refused release left a tag"; fails=$((fails + 1)); }
release v0.2.0;            expect ok   v0.2.0  "the next minor release is tagged"
exit $fails

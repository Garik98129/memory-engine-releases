#!/usr/bin/env bash
# Runs pr-paths.py on pull requests made up in a throwaway repository: bash .github/scripts/test-pr-paths.sh
set -euo pipefail
script="$(cd "$(dirname "$0")" && pwd)/pr-paths.py"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid \
       GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
git init -q -b main "$tmp/w"
cd "$tmp/w"
mkdir -p files/engine .github/workflows .github/scripts
printf 'a\n' > files/engine/a.py; printf '{}\n' > release-manifest.json; printf 'index\n' > rules-index.md
printf 'readme\n' > README.md; printf 'on: push\n' > .github/workflows/tag.yml; printf 'code\n' > .github/scripts/stable.py
git add -A && git commit -q -m "main as a release left it"
printf 'raise SystemExit("planted")\n' > "$tmp/w/subprocess.py"      # in the working directory, never committed: -I keeps it out
fails=0
pr() {        # pr <name>: a new branch off main for the next pull request
  git checkout -q main && git checkout -q -b "$1"
}
save() { git add -A -- . ':!subprocess.py' && git commit -q -m "${1:-change}"; }
expect() {    # expect <exit> <name> [words it must say] — the branch checked out against main, as the workflow runs it
  local want=$1 name=$2 says=${3:-} got=0
  python3 -I "$script" refs/heads/main "$(git rev-parse HEAD)" >"$tmp/out" 2>&1 || got=$?
  if [ "$got" != "$want" ]; then echo "FAIL $name: wanted exit $want, got $got"; cat "$tmp/out"; fails=$((fails + 1)); return; fi
  if [ -n "$says" ] && ! grep -qF -- "$says" "$tmp/out"; then
    echo "FAIL $name: it did not say «$says»"; cat "$tmp/out"; fails=$((fails + 1)); return
  fi
  if grep -qv '^PR ' "$tmp/out"; then echo "FAIL $name: a line of the output does not open with «PR »"; cat "$tmp/out"; fails=$((fails + 1)); return; fi
  echo "ok   $name"
}

pr release; printf 'b\n' > files/engine/a.py; printf 'new\n' > files/engine/b.py; printf '{"release": 1}\n' > release-manifest.json
printf 'index 2\n' > rules-index.md; save
expect 0 "a release — files/, the manifest and the index — is a release and nothing else" "is a release and nothing else: 4 paths"
pr old-release; printf 'b\n' > files/engine/a.py; printf '{"release": 1}\n' > release-manifest.json; save
expect 0 "a release cut before the index existed too" "is a release and nothing else: 2 paths"
pr code; printf 'code 2\n' > .github/scripts/stable.py; printf 'on: schedule\n' > .github/workflows/stable.yml; printf 'more\n' >> README.md; save
expect 0 "a change to what this repository runs, with no release in it, is not a release" "is not a release"
pr empty; git commit -q --allow-empty -m nothing
expect 0 "a pull request that changes nothing" "changes nothing"

pr with-workflow; printf 'b\n' > files/engine/a.py; printf '{"release": 1}\n' > release-manifest.json; printf 'on: push\njobs: {}\n' > .github/workflows/tag.yml; save
expect 1 "a release that also changes a workflow is refused" "also: '.github/workflows/tag.yml'"
pr with-code; printf '{"release": 1}\n' > release-manifest.json; printf 'other code\n' > .github/scripts/stable.py; save
expect 1 "a manifest with a change to the code that judges releases is refused" "'.github/scripts/stable.py'"
pr with-readme; printf 'index 2\n' > rules-index.md; printf 'more\n' >> README.md; save
expect 1 "an index with the README is refused" "also: 'README.md'"
pr new-top; printf 'b\n' > files/engine/a.py; printf 'x\n' > stable.json; save
expect 1 "a file of the release with a new file at the top is refused" "'stable.json'"
pr moved-out; git mv files/engine/a.py a.py; save
expect 1 "a file moved out of files/ is its two paths, and refused" "also: 'a.py'"
pr folder-gone; git rm -q -r files; printf 'not a folder\n' > files; save
expect 1 "files/ replaced by a file named files is refused" "also: 'files'"
pr other-case; printf '{"release": 1}\n' > release-manifest.json; mkdir Files && printf 'x\n' > Files/a.py; save
expect 1 "a folder that only looks like files/ is not a release's" "'Files/a.py'"
pr many; printf '{"release": 1}\n' > release-manifest.json; for i in 01 02 03 04 05 06 07 08 09 10 11 12; do printf 'x\n' > "extra-$i"; done; save
expect 1 "many other paths are counted, ten of them named" "and 2 more"
pr strange; printf '{"release": 1}\n' > release-manifest.json
printf 'x\n' > "$(printf 'a\n::error::forged')"; printf 'x\n' > "$(printf 'b\377')"; printf 'x\n' > 'c%0A::warning::d'; save
expect 1 "a path with a line break, a byte that is no text and a percent sign is printed escaped" "'a\\n::error::forged'"
check_out() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; cat "$tmp/out"; fails=$((fails + 1)); fi; }
check_out "and stays on the one line of the finding" '[ "$(wc -l < "$tmp/out")" = 1 ] && grep -qF "b\\\\xff" "$tmp/out"'

# what the pull request brings, not what main got meanwhile
pr early; printf 'b\n' > files/engine/a.py; printf '{"release": 1}\n' > release-manifest.json; save
git checkout -q main; printf 'on: push # changed on main\n' > .github/workflows/tag.yml; save "main moved on"
git checkout -q early
expect 0 "a release whose main moved on meanwhile is judged by what it brings" "is a release and nothing else: 2 paths"
git merge -q --no-edit main
expect 0 "and so is one that took main in" "is a release and nothing else: 2 paths"

git checkout -q --orphan elsewhere && git rm -q -rf . && printf 'x\n' > x && git add x && git commit -q -m "no common commit"
expect 2 "a pull request with no commit in common with main cannot be told" "PR ERROR"
got=0; python3 -I "$script" refs/heads/main "no-such-thing" >"$tmp/out" 2>&1 || got=$?
check_out "a head that is no sha or ref cannot be told" '[ "$got" = 2 ] && grep -q "^PR ERROR" "$tmp/out"'
got=0; python3 -I "$script" refs/heads/main "--output=$tmp/written" >"$tmp/out" 2>&1 || got=$?
check_out "a head made to look like an option is not handed to git" '[ "$got" = 2 ] && [ ! -e "$tmp/written" ]'
got=0; python3 -I "$script" refs/heads/main 0123456789abcdef0123456789abcdef01234567 >"$tmp/out" 2>&1 || got=$?
check_out "a head this repository does not have cannot be told" '[ "$got" = 2 ] && grep -q "^PR ERROR" "$tmp/out"'
got=0; python3 -I "$script" refs/heads/main >"$tmp/out" 2>&1 || got=$?
check_out "one argument is not two" '[ "$got" = 2 ]'
exit $fails

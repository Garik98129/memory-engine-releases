#!/usr/bin/env bash
# Runs this repository's own copy of the code that moves the branch `stable` (workflow `stable`):
# .github/scripts/stable.py, beside this file — the schedule when TO and REASON are empty, a move
# by hand when they are given, and only words when DRY_RUN is «true». The rule itself — which
# release, when, what is refused — is that file's, with its tests in the template; this one only
# hands it what the run was started with, and passes on its exit code: 0 — moved, or nothing to
# move; 1 — a finding; 2 — the run failed.
#
# The values come from the environment and go on as arguments, never through a shell's eyes: a
# reason is a person's sentence. What that code prints carries a release's own words — a path, a
# name out of a manifest — so the runner is told to read no command in it until this run's
# one-time word comes; a line a release made to look like a command is then a line of the log.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

trim() {   # the value without the blanks around it: a phone's keyboard leaves one after a word
  local s=$1
  s="${s#"${s%%[![:space:]]*}"}"
  printf '%s' "${s%"${s##*[![:space:]]}"}"
}

args=(--repository "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set, the owner/name of this repository}")
to=$(trim "${TO:-}")
reason=$(trim "${REASON:-}")
if [ -n "$to" ]; then args+=(--to "$to"); fi
if [ -n "$reason" ]; then args+=(--reason "$reason"); fi
if [ "${DRY_RUN:-}" = true ]; then args+=(--dry-run); fi

word=$(python3 -I -c 'import secrets; print(secrets.token_hex(16))')
echo "::stop-commands::$word"
code=0
python3 -I "$here/stable.py" "${args[@]}" || code=$?
echo "::$word::"
exit "$code"

#!/usr/bin/env bash
# Runs this repository's own copy of the code that moves the branch `stable` (workflow `stable`):
# .github/scripts/stable.py, beside this file — the schedule when TO and REASON are empty, a move
# by hand when they are given, and only words when DRY_RUN is «true». The rule itself — which
# release, when, what is refused — is that file's, with its tests in the template; this one only
# hands it what the run was started with, and passes on its exit code: 0 — moved, or nothing to
# move; 1 — a finding; 2 — the run failed. That code ends with 1 only beside a line «STABLE
# finding: …», and with 2 when it died on what it did not expect; a copy that does not load —
# a line Python cannot read — ends as Python does, with 1. So a 1 without such a line is still
# passed on as 2: a run that died is not a finding about a release.
# A run that is not green ends with one line of this file's own, in plain words, that says
# where to read and what is done about it: the lines above it are written for whoever repairs.
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

if [ -z "${GITHUB_REPOSITORY:-}" ]; then
  echo "::error::GITHUB_REPOSITORY is not set, the owner/name of this repository: the code that moves stable was not started"
  exit 2
fi
args=(--repository "$GITHUB_REPOSITORY")
to=$(trim "${TO:-}")
reason=$(trim "${REASON:-}")
if [ -n "$to" ]; then args+=("--to=$to"); fi                  # one word each: a reason may open with a dash
if [ -n "$reason" ]; then args+=("--reason=$reason"); fi
if [ "${DRY_RUN:-}" = true ]; then args+=(--dry-run); fi

word=$(python3 -I -c 'import secrets; print(secrets.token_hex(16))')
echo "::stop-commands::$word"
code=0
said=$(mktemp)
python3 -I "$here/stable.py" "${args[@]}" >"$said" 2>&1 || code=$?
cat "$said"
if [ "$code" = 1 ] && ! grep -q '^STABLE finding: ' "$said"; then
  echo "ERROR the code that moves stable ended with 1 and named no finding: it died, it did not judge"
  code=2
fi
rm -f "$said"
echo "::$word::"
if [ "$code" = 1 ]; then
  echo "::error::The branch stable has a finding: see the lines «STABLE finding» in this step. Nothing is broken for a chat — the branch stays on the release it was on. What is done about it is in README.md, «The branch stable»: a release that is not whole is withdrawn, and a branch this code did not leave is repaired, by one run by hand — Run workflow, field «to»: the release the branch should be on, field «reason»: one line."
elif [ "$code" != 0 ]; then
  echo "::error::The run that moves the branch stable failed before it could judge: see the line «ERROR» in this step. The branch stays where it was. The next night's run tries again; a failure that repeats is a fault in this repository's scripts or settings, not in a release."
fi
exit "$code"

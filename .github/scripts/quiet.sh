#!/usr/bin/env bash
# Runs a command whose output carries a stranger's text — a path out of a pull request, a
# name out of a manifest — so that the runner reads no command in it:
#   bash .github/scripts/quiet.sh <command> [arguments]
# The runner is told to stop reading commands until this run's one-time word comes, and the
# word comes when the command has ended, whatever it ended with; its exit code is passed on.
set -uo pipefail
word=$(python3 -I -c 'import secrets; print(secrets.token_hex(16))') || exit 2
echo "::stop-commands::$word"
code=0
"$@" || code=$?
echo "::$word::"
exit "$code"

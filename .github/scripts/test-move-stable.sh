#!/usr/bin/env bash
# Runs move-stable.sh with a stand-in for stable.py that only says what it was handed:
#   bash .github/scripts/test-move-stable.sh
# The rule that moves the branch is stable.py's, and its tests are the template's
# (engine/setup/tests/test_stable.py); this holds what the workflow adds around it.
set -euo pipefail
script="$(cd "$(dirname "$0")" && pwd)/move-stable.sh"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/scripts" "$tmp/checkout"
cp "$script" "$tmp/scripts/move-stable.sh"
cat > "$tmp/scripts/stable.py" <<'PY'
import json, os, sys
print("ARGS", json.dumps(sys.argv[1:]))
print("JSON", "planted" if "planted" in (json.__file__ or "") else "the library's")
print("::error::a line a release wrote")
if os.environ.get("STUB_DIES"):
    raise RuntimeError("an error the code did not expect")
if os.environ.get("STUB_EXIT") == "1":
    print("STABLE finding: a release is not whole")
if os.environ.get("STUB_MOVED"):
    print("STABLE moved: v0.1.12 -> v0.1.13 (the schedule), commit 0; withdrawn: none")
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
PY
printf 'raise SystemExit("planted")\n' > "$tmp/scripts/json.py"      # beside the copy: never imported
printf 'raise SystemExit("planted")\n' > "$tmp/checkout/json.py"   # nor from the checkout
fails=0
run() {       # run <name> <exit wanted> <the ARGS line wanted, as JSON> — the environment is the caller's
  local name=$1 want=$2 args=$3 got=0
  (cd "$tmp/checkout" && bash "$tmp/scripts/move-stable.sh") >"$tmp/out" 2>&1 || got=$?
  if [ "$got" != "$want" ]; then echo "FAIL $name: wanted exit $want, got $got"; cat "$tmp/out"; fails=$((fails + 1)); return; fi
  if [ -n "$args" ] && ! grep -qxF -- "ARGS $args" "$tmp/out"; then
    echo "FAIL $name: wanted «ARGS $args»"; cat "$tmp/out"; fails=$((fails + 1)); return
  fi
  echo "ok   $name"
}
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; cat "$tmp/out"; fails=$((fails + 1)); fi; }
export GITHUB_REPOSITORY=owner/name
unset TO REASON DRY_RUN STUB_EXIT STUB_DIES STUB_MOVED

run "the schedule: the repository and nothing more" 0 '["--repository", "owner/name"]'
check "the copy beside the script is the one run, with the library's modules" 'grep -qx "JSON the library'"'"'s" "$tmp/out"'
closing() { grep -n -xF "::$(head -n 1 "$tmp/out" | cut -d: -f5)::" "$tmp/out" | cut -d: -f1; }    # the line the word comes back on
check "the output opens with the word that stops commands" 'head -n 1 "$tmp/out" | grep -qE "^::stop-commands::[0-9a-f]{32}$"'
check "and closes with the same word, once, as the last line of a green run" '[ "$(closing)" = "$(wc -l < "$tmp/out")" ]'
check "a line a release wrote stands between the two" '[ "$(sed -n 4p "$tmp/out")" = "::error::a line a release wrote" ] && [ "$(wc -l < "$tmp/out")" = 5 ]'
first=$(head -n 1 "$tmp/out")
run "a second run" 0 ''
check "has a word of its own" '[ "$(head -n 1 "$tmp/out")" != "$first" ]'
TO="" REASON="" DRY_RUN=false run "a run by hand with nothing filled in is the schedule" 0 '["--repository", "owner/name"]'
TO=v0.1.13 REASON="a fix that cannot wait" run "by hand: the release and the reason" 0 \
  '["--repository", "owner/name", "--to=v0.1.13", "--reason=a fix that cannot wait"]'
TO=" v0.1.13 " REASON="  back: v0.1.14 breaks the calendar " run "the blanks around a value are dropped, those inside kept" 0 \
  '["--repository", "owner/name", "--to=v0.1.13", "--reason=back: v0.1.14 breaks the calendar"]'
TO=v0.1.13 REASON='$(touch '"$tmp"'/ran) `id` "q" '"'"'z'"'"' ; rm -rf * && --dry-run' run "a reason is a sentence, not a command" 0 \
  '["--repository", "owner/name", "--to=v0.1.13", "--reason=$(touch '"$tmp"'/ran) `id` \"q\" '"'"'z'"'"' ; rm -rf * && --dry-run"]'
check "and nothing in it ran" '[ ! -e "$tmp/ran" ] && [ -e "$tmp/checkout/json.py" ]'
TO=v0.1.13 run "a release without a reason goes on as it is: stable.py refuses it" 0 '["--repository", "owner/name", "--to=v0.1.13"]'
REASON="why" run "a reason without a release too" 0 '["--repository", "owner/name", "--reason=why"]'
TO="   " REASON="   " run "values of blanks only are no values" 0 '["--repository", "owner/name"]'
DRY_RUN=true run "a dry run" 0 '["--repository", "owner/name", "--dry-run"]'
DRY_RUN=true TO=v0.1.13 REASON=why run "a dry run by hand" 0 '["--repository", "owner/name", "--to=v0.1.13", "--reason=why", "--dry-run"]'
DRY_RUN=True run "only «true» is a dry run" 0 '["--repository", "owner/name"]'
STUB_EXIT=1 run "a finding is passed on" 1 '["--repository", "owner/name"]'
check "the commands are started again after it, and one line in plain words says where what is done about a finding is written" \
  '[ "$(closing)" = "$(($(wc -l < "$tmp/out") - 1))" ] && tail -n 1 "$tmp/out" | grep -q "^::error::The branch stable has a finding.*README.md.*field «to».*field «reason»"'
check "that line does not say the branch stayed: it says it does not say, and names the line «STABLE moved»" \
  'tail -n 1 "$tmp/out" | grep -q "does not say whether the branch moved: a line «STABLE moved» in this step means this run moved it" && ! tail -n 1 "$tmp/out" | grep -qi "stays"'
STUB_EXIT=1 STUB_MOVED=1 run "the run stable.py makes when one release is broken and it moves to another: red, with its «STABLE moved»" 1 '["--repository", "owner/name"]'
check "the line «STABLE moved» stands in the log, and the last line does not deny it" \
  'grep -qx "STABLE moved: v0.1.12 -> v0.1.13 (the schedule), commit 0; withdrawn: none" "$tmp/out" && tail -n 1 "$tmp/out" | grep -q "does not say whether the branch moved" && ! tail -n 1 "$tmp/out" | grep -qi "stays"'
STUB_EXIT=2 run "a failed run is passed on" 2 ''
check "with a line of its own, after the commands are started again" '[ "$(closing)" = "$(($(wc -l < "$tmp/out") - 1))" ] && tail -n 1 "$tmp/out" | grep -q "^::error::The run that moves the branch stable failed: see what this step printed above"'
check "that line does not say the branch stayed either, nor that an «ERROR» line is there" \
  'tail -n 1 "$tmp/out" | grep -q "a line «ERROR» where there is one.*does not say whether the branch moved.*no such line is no proof that it did not" && ! tail -n 1 "$tmp/out" | grep -qi "stays"'
STUB_EXIT=120 run "any other code — Python's 120, say — is passed on as it is" 120 ''
check "with the same line of a failed run" 'tail -n 1 "$tmp/out" | grep -q "^::error::The run that moves the branch stable failed: see what this step printed above.*does not say whether the branch moved"'
STUB_DIES=1 run "code that died on an error is a failed run, not a finding" 2 '["--repository", "owner/name"]'
check "and the run says so, with the error above it" \
  'grep -q "RuntimeError" "$tmp/out" && grep -q "^ERROR .* it died" "$tmp/out" && tail -n 1 "$tmp/out" | grep -q "^::error::The run that moves the branch stable failed"'
TO=v0.1.13 REASON="-v0.1.14" run "a reason that opens with a dash is still the reason" 0 '["--repository", "owner/name", "--to=v0.1.13", "--reason=-v0.1.14"]'
GITHUB_REPOSITORY="" run "no repository named: the run fails — a failure, not a finding — before the code is started" 2 ''
check "and says so" 'grep -q "^::error::GITHUB_REPOSITORY is not set" "$tmp/out" && ! grep -q "^ARGS" "$tmp/out"'

# quiet.sh: any command, between the two lines, its exit code passed on
quiet="$(cd "$(dirname "$script")" && pwd)/quiet.sh"
got=0; bash "$quiet" bash -c 'echo "::error::a stranger wrote this"; exit 7' >"$tmp/out" 2>&1 || got=$?
check "quiet.sh passes the exit code on" '[ "$got" = 7 ]'
check "and frames what the command printed with one word, opened before it and closed after" \
  'head -n 1 "$tmp/out" | grep -qE "^::stop-commands::[0-9a-f]{32}$" && [ "$(sed -n 2p "$tmp/out")" = "::error::a stranger wrote this" ] && [ "$(closing)" = 3 ] && [ "$(wc -l < "$tmp/out")" = 3 ]'
got=0; bash "$quiet" no-such-command-here >"$tmp/out" 2>&1 || got=$?
check "a command that cannot be started still gets its closing line" '[ "$got" != 0 ] && [ "$(closing)" = "$(wc -l < "$tmp/out")" ]'

# stable-rules.py: GitHub's answer about the rules of the branch, as the workflow pipes it in
rules="$(cd "$(dirname "$script")" && pwd)/stable-rules.py"
answer() {    # answer <name> <exit wanted> <words it must say> <GitHub's answer>
  local name=$1 want=$2 says=$3 got=0
  printf '%s' "$4" | (cd "$tmp/checkout" && python3 -I "$rules") >"$tmp/out" 2>&1 || got=$?
  if [ "$got" != "$want" ]; then echo "FAIL $name: wanted exit $want, got $got"; cat "$tmp/out"; fails=$((fails + 1)); return; fi
  if ! grep -qF -- "$says" "$tmp/out"; then echo "FAIL $name: it did not say «$says»"; cat "$tmp/out"; fails=$((fails + 1)); return; fi
  echo "ok   $name"
}
rule() { printf '{"type": "%s", "ruleset_source_type": "Repository", "ruleset_source": "owner/name", "ruleset_id": 1}' "$1"; }
answer "both rules: the branch is closed" 0 "forbids a forced push to the branch stable and its deletion" "[$(rule deletion), $(rule non_fast_forward)]"
answer "other rules beside them do not matter" 0 "RULES an active ruleset forbids" "[$(rule required_linear_history), $(rule non_fast_forward), $(rule deletion)]"
answer "no ruleset at all is a finding" 1 "forbids its deletion or a forced push to it" "[]"
answer "deletion allowed is a finding" 1 "forbids its deletion:" "[$(rule non_fast_forward)]"
answer "a forced push allowed is a finding" 1 "forbids a forced push to it:" "[$(rule deletion), $(rule update)]"
answer "a rule without a type is no rule" 1 "RULES finding" '[{"type": null}, {"type": ["deletion"]}, {}]'
answer "an error GitHub answered with is not a list of rules" 2 "RULES ERROR" '{"message": "Not Found", "status": "404"}'
answer "an empty answer" 2 "RULES ERROR" ""
answer "an answer that is no JSON" 2 "RULES ERROR" "<html>"
answer "a list of what is no rule" 2 "RULES ERROR" '["deletion", "non_fast_forward"]'
answer "rules that are not in a list" 2 "RULES ERROR" "{\"a\": $(rule deletion), \"b\": $(rule non_fast_forward)}"
answer "an answer nested without end" 2 "RULES ERROR" "$(python3 -I -c 'print("[" * 200000)')"
got=0; printf '[{"type": "deletion"}, {"type": "non_fast_forward"}, {"type": "\377"}]' | python3 -I "$rules" >"$tmp/out" 2>&1 || got=$?
check "an answer that is not text is not read as one" '[ "$got" = 2 ] && grep -q "^RULES ERROR" "$tmp/out"'
exit $fails

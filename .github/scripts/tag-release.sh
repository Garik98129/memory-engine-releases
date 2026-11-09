#!/usr/bin/env bash
# Tags each commit in <before>..<after> that changed release-manifest.json with the release
# that manifest names, oldest first (workflow `tag`). Only creates tags: a release whose tag
# exists, one not newer than the newest release tag, or a manifest that is not a release is
# an error, and nothing more is pushed. The tag ruleset forbids moving or deleting a tag.
set -euo pipefail
before="${1:?usage: tag-release.sh <before> <after>}"
after="${2:?usage: tag-release.sh <before> <after>}"
release_re='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'

if [[ "$before" =~ ^0+$ ]]; then
  commits="$after"
else
  commits=$(git rev-list --reverse "$before..$after" -- release-manifest.json)
fi
[ -n "$commits" ] || { echo "no commit in this push changed release-manifest.json"; exit 0; }

for sha in $commits; do
  # The release and its source, both checked here; anything else is a refusal.
  if ! read -r tag source < <(git show "$sha:release-manifest.json" | python3 -c '
import json, re, sys
try:
    m = json.load(sys.stdin)
except ValueError as exc:
    print(f"::error::release-manifest.json does not parse: {exc}", file=sys.stderr); sys.exit(1)
tag, source = m.get("release"), m.get("source")
if not isinstance(tag, str) or not re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag):
    print(f"::error::release {tag!r} in release-manifest.json is not vMAJOR.MINOR.PATCH", file=sys.stderr); sys.exit(1)
if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
    print(f"::error::source {source!r} in release-manifest.json is not a 40-hex commit sha", file=sys.stderr); sys.exit(1)
print(tag, source)'); then
    exit 1
  fi
  [ -n "${tag:-}" ] || exit 1
  if git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null; then
    echo "::error::tag $tag exists already: a release is cut once"; exit 1
  else
    rc=$?
    [ "$rc" = 2 ] || { echo "::error::could not read the tags of origin (git ls-remote exit $rc)"; exit 1; }
  fi
  latest=$(git ls-remote --tags --refs origin 'v*' | sed 's#.*refs/tags/##' | { grep -E "$release_re" || true; } | sort -V | tail -n 1)
  if [ -n "$latest" ] && [ "$(printf '%s\n%s\n' "$latest" "$tag" | sort -V | tail -n 1)" != "$tag" ]; then
    echo "::error::$tag is not newer than $latest: releases go on one line, newest last"; exit 1
  fi
  git tag "$tag" "$sha"
  git push origin "refs/tags/$tag"
  echo "tagged $sha as $tag (template commit $source)"
done

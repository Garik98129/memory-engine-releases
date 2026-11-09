#!/usr/bin/env bash
# Tags commit $1 with the release its release-manifest.json names (workflow `tag`).
# Only creates a tag: a tag that exists already, or one not newer than the newest
# v* tag, is an error and nothing is pushed. The tag ruleset forbids moving or deleting.
set -euo pipefail
sha="${1:?usage: tag-release.sh <commit>}"
{ IFS= read -r tag; IFS= read -r source; } < <(python3 -c '
import json
m = json.load(open("release-manifest.json"))
print(m.get("release", "")); print(m.get("source", ""))')
if ! [[ "$tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "::error::release «$tag» in release-manifest.json is not vMAJOR.MINOR.PATCH"; exit 1
fi
if ! [[ "$source" =~ ^[0-9a-f]{40}$ ]]; then
  echo "::error::source «$source» in release-manifest.json is not a commit"; exit 1
fi
if git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null; then
  echo "::error::tag $tag exists already: a release is cut once"; exit 1
fi
latest=$(git ls-remote --tags --refs origin 'v*' | sed 's#.*refs/tags/##' | sort -V | tail -n 1)
if [ -n "$latest" ] && [ "$(printf '%s\n%s\n' "$latest" "$tag" | sort -V | tail -n 1)" != "$tag" ]; then
  echo "::error::$tag is not newer than $latest"; exit 1
fi
git tag "$tag" "$sha"
git push origin "refs/tags/$tag"
echo "tagged $sha as $tag (template commit $source)"

#!/usr/bin/env bash
# Tags each commit in <before>..<after> that changed release-manifest.json with the release
# that manifest names, oldest first (workflow `tag`). Only creates tags: a release whose tag
# exists, one not newer than the newest release tag, a manifest that is not a release, or a
# files/ that is not exactly the manifest's files is an error, and nothing more is pushed.
# The tag ruleset forbids moving or deleting a tag.
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
  # The release, its source and its files, all checked here; anything else is a refusal.
  if ! read -r tag source < <(python3 - "$sha" <<'PY'
import hashlib, json, re, subprocess, sys
sha = sys.argv[1]
def fail(msg):
    print(f"::error::{msg}", file=sys.stderr); sys.exit(1)
def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout
try:
    m = json.loads(git("show", f"{sha}:release-manifest.json"))
except ValueError as exc:
    fail(f"release-manifest.json does not parse: {exc}")
tag, source, files = m.get("release"), m.get("source"), m.get("files")
if not isinstance(tag, str) or not re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag):
    fail(f"release {tag!r} in release-manifest.json is not vMAJOR.MINOR.PATCH")
if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
    fail(f"source {source!r} in release-manifest.json is not a 40-hex commit sha")
if not isinstance(files, dict) or not files:
    fail("release-manifest.json lists no files")
# files/ at this commit must be exactly the manifest's files, byte for byte.
have = {}
for entry in git("ls-tree", "-r", "-z", sha, "--", "files/").split(b"\0"):
    if not entry:
        continue
    meta, path = entry.split(b"\t", 1)
    mode, kind, blob = meta.decode().split()
    path = path.decode("utf-8", "replace")[len("files/"):]
    if mode != "100644":
        fail(f"files/{path} has mode {mode}; a release holds regular files only")
    have[path] = hashlib.sha256(git("cat-file", "blob", blob)).hexdigest()
if not have:
    fail("this commit has no files/: a release carries its files beside its manifest")
missing = sorted(set(files) - set(have))
extra = sorted(set(have) - set(files))
changed = sorted(p for p in set(files) & set(have) if files[p] != have[p])
if missing or extra or changed:
    fail(f"files/ is not the manifest: missing {missing[:3]}, extra {extra[:3]}, different {changed[:3]}")
print(tag, source)
PY
); then
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

#!/usr/bin/env bash
# Tags the releases on main that have no tag yet (workflow `tag`): every commit after the newest
# release tag, up to <after>, that changed release-manifest.json, oldest first, gets the tag its
# manifest names. A run that came late — after a newer push's run — finds those releases tagged
# already and does nothing, so no release is skipped whatever order the runs take. A commit
# whose tag is already on it is passed over. A manifest that is not a release, a files/ that is
# not exactly the manifest's files, a tag that exists on another commit or one not newer than
# the newest is refused; the run is red only when the newest manifest commit was refused —
# an older refusal a later release superseded is a warning. Only creates tags: the tag
# ruleset forbids moving or deleting one.
set -euo pipefail
after="${1:?usage: tag-release.sh <after>}"
release_re='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'

tags() {   # «sha tag» of every release tag on origin, a lightweight tag's own commit
  local listing
  listing=$(git ls-remote --tags origin 'v*') || { echo "::error::could not read the tags of origin"; exit 1; }
  printf '%s\n' "$listing" | python3 -c '
import re, sys
commits = {}
for line in sys.stdin:
    if not line.strip():
        continue
    sha, ref = line.split()
    name = ref[len("refs/tags/"):]
    peeled = name.endswith("^{}")
    name = name[:-3] if peeled else name
    if re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", name) and (peeled or name not in commits):
        commits[name] = sha
for name, sha in commits.items():
    print(sha, name)'
}
newest_of() { sort -V | tail -n 1; }

declare -A tag_commit
while read -r sha name; do [ -n "${name:-}" ] && tag_commit[$name]=$sha; done < <(tags)
newest=$(printf '%s\n' "${!tag_commit[@]}" | grep -E "$release_re" | newest_of || true)
if [ -n "$newest" ] && git cat-file -e "${tag_commit[$newest]}^{commit}" 2>/dev/null; then
  range="${tag_commit[$newest]}..$after"
else
  range="$after"
fi
commits=$(git rev-list --reverse "$range" -- release-manifest.json)
[ -n "$commits" ] || { echo "no release on main after ${newest:-the start} — nothing to tag"; exit 0; }
last=$(printf '%s\n' "$commits" | tail -n 1)

refused_last=""
for sha in $commits; do
  verdict=$(python3 - "$sha" <<'PY'
import hashlib, json, re, subprocess, sys
sha = sys.argv[1]
def refuse(msg):
    print("REFUSED", msg); sys.exit(0)
def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout
try:
    m = json.loads(git("show", f"{sha}:release-manifest.json"))
except ValueError as exc:
    refuse(f"release-manifest.json does not parse: {exc}")
tag, source, files = m.get("release"), m.get("source"), m.get("files")
if not isinstance(tag, str) or not re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag):
    refuse(f"release {tag!r} in release-manifest.json is not vMAJOR.MINOR.PATCH")
if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
    refuse(f"source {source!r} in release-manifest.json is not a 40-hex commit sha")
if not isinstance(files, dict) or not files:
    refuse("release-manifest.json lists no files")
have = {}
for entry in git("ls-tree", "-r", "-z", sha, "--", "files/").split(b"\0"):
    if not entry:
        continue
    meta, path = entry.split(b"\t", 1)
    mode, _kind, blob = meta.decode().split()
    path = path.decode("utf-8", "replace")[len("files/"):]
    if mode != "100644":
        refuse(f"files/{path} has mode {mode}; a release holds regular files only")
    have[path] = hashlib.sha256(git("cat-file", "blob", blob)).hexdigest()
if not have:
    refuse("this commit has no files/: a release carries its files beside its manifest")
missing = sorted(set(files) - set(have))
extra = sorted(set(have) - set(files))
changed = sorted(p for p in set(files) & set(have) if files[p] != have[p])
if missing or extra or changed:
    refuse(f"files/ is not the manifest: missing {missing[:3]}, extra {extra[:3]}, different {changed[:3]}")
print("OK", tag, source)
PY
  )
  read -r word rest <<<"$verdict"
  if [ "$word" = OK ]; then
    read -r tag source <<<"$rest"
    if [ -n "${tag_commit[$tag]:-}" ]; then
      if [ "${tag_commit[$tag]}" = "$sha" ]; then echo "$tag is on $sha already"; continue; fi
      word=REFUSED; rest="tag $tag exists already, on ${tag_commit[$tag]}: a release is cut once"
    elif [ -n "$newest" ] && [ "$(printf '%s\n%s\n' "$newest" "$tag" | newest_of)" != "$tag" ]; then
      word=REFUSED; rest="$tag is not newer than $newest: releases go on one line, newest last"
    fi
  fi
  if [ "$word" != OK ]; then
    echo "::warning::refused $sha: $rest"
    [ "$sha" = "$last" ] && refused_last="$rest"
    continue
  fi
  git tag "$tag" "$sha"
  git push origin "refs/tags/$tag"
  tag_commit[$tag]=$sha
  newest=$tag
  echo "tagged $sha as $tag (template commit $source)"
done

if [ -n "$refused_last" ]; then
  echo "::error::the newest release on main was refused: $refused_last"; exit 1
fi

#!/usr/bin/env bash
# Tags the releases on main that have no tag yet (workflow `tag`): every commit after the newest
# release tag, up to <after>, that changed release-manifest.json, oldest first, gets the tag its
# manifest names. A run that came late — after a newer push's run — finds those releases tagged
# already and does nothing, so no release is skipped whatever order the runs take. A commit
# whose tag is already on it is passed over. A manifest that is not a release, a files/ that is
# not exactly the manifest's files — a path there that is not valid UTF-8 among it —, a topic
# index (rules-index.md) that is not the one the manifest names, a tag that exists on another
# commit or one not newer than the newest is refused; the run is red only when the newest
# manifest commit was refused — an older refusal a later release superseded is a warning. Only
# creates tags: the tag ruleset forbids moving or deleting one — which is why what can be seen
# here is refused here: a release tagged and then found broken stays tagged.
#
# Python runs with -I: the checkout is the working directory, and without it a file of the
# checkout named like a module (json.py) would be imported in the module's place. What is said
# of a refusal is printed escaped: it carries a manifest's own words.
set -euo pipefail
after="${1:?usage: tag-release.sh <after>}"
release_re='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'

tags() {   # «sha tag» of every release tag on origin, a lightweight tag's own commit
  local listing
  listing=$(git ls-remote --tags origin 'v*') || { echo "::error::could not read the tags of origin"; exit 1; }
  printf '%s\n' "$listing" | python3 -I -c '
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
said() {   # a refusal as a workflow command's text may carry it: «%» and line ends escaped
  local s=${1//%/%25}
  s=${s//$'\r'/%0D}
  printf '%s' "${s//$'\n'/%0A}"
}

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
  verdict=$(python3 -I - "$sha" <<'PY'
import hashlib, json, re, subprocess, sys
sha = sys.argv[1]
def refuse(msg):          # one line of plain ASCII, whatever the manifest's words were
    print("REFUSED", " ".join(msg.split()).encode("ascii", "backslashreplace").decode()); sys.exit(0)
def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout
def check():
    try:
        raw = git("show", f"{sha}:release-manifest.json")
    except subprocess.CalledProcessError:
        refuse("this commit has no release-manifest.json")
    try:
        m = json.loads(raw)
    except ValueError as exc:
        refuse(f"release-manifest.json does not parse: {exc}")
    if not isinstance(m, dict):
        refuse(f"release-manifest.json is not a JSON object but {type(m).__name__}")
    return m
try:
    m = check()
except SystemExit:
    raise
except Exception as exc:            # the check itself must never stop the run: a refusal instead
    refuse(f"the manifest could not be checked: {type(exc).__name__}: {exc}")
tag, source, files = m.get("release"), m.get("source"), m.get("files")
if not isinstance(tag, str) or not re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag):
    refuse(f"release {tag!r} in release-manifest.json is not vMAJOR.MINOR.PATCH")
if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
    refuse(f"source {source!r} in release-manifest.json is not a 40-hex commit sha")
if not isinstance(files, dict) or not files:
    refuse("release-manifest.json lists no files")
have = {}
try:
    tree = git("ls-tree", "-r", "-z", sha, "--", "files/")
except subprocess.CalledProcessError as exc:
    refuse(f"files/ could not be read: {exc}")
for entry in tree.split(b"\0"):
    if not entry:
        continue
    meta, path = entry.split(b"\t", 1)
    mode, _kind, blob = meta.decode().split()
    try:
        path = path.decode("utf-8")[len("files/"):]
    except UnicodeDecodeError:
        refuse(f"a path under files/ is not valid UTF-8: {path!r}")
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
index = m.get("index")          # the topic index, beside files/; a release cut before it existed names none
if index is not None:
    if not isinstance(index, dict) or set(index) != {"path", "sha256"} or index["path"] != "rules-index.md":
        refuse("release-manifest.json does not name its index as rules-index.md with a sha256")
    try:
        entry = git("ls-tree", "-z", sha, "--", "rules-index.md").split(b"\0")[0]
    except subprocess.CalledProcessError as exc:
        refuse(f"rules-index.md could not be read: {exc}")
    if not entry:
        refuse("release-manifest.json names an index and this commit has no rules-index.md")
    mode, kind, blob = entry.split(b"\t", 1)[0].decode().split()
    if (mode, kind) != ("100644", "blob"):
        refuse(f"rules-index.md has mode {mode}; the index is a regular file")
    if hashlib.sha256(git("cat-file", "blob", blob)).hexdigest() != index["sha256"]:
        refuse("rules-index.md is not the one release-manifest.json names: their sha256 differ")
print("OK", tag, source)
PY
  ) || verdict="REFUSED the check of this commit failed"
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
    echo "::warning::refused $sha: $(said "$rest")"
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
  echo "::error::the newest release on main was refused: $(said "$refused_last")"; exit 1
fi

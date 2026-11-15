#!/usr/bin/env bash
# Tags the releases on main that have no tag yet (workflow `tag`): every commit of main's own
# line after the newest release tag, up to <after>, that changed release-manifest.json, oldest
# first, gets the tag its manifest names. A run that came late — after a newer push's run —
# finds those releases tagged already and does nothing, so no release is skipped whatever order
# the runs take. A commit whose tag is already on it is passed over.
#
# Main's own line — its first parents — and no other commit: what a merged branch held on its
# way is never tagged. A pull request merged by a merge commit is tagged on that merge, which
# holds what main took; its branch's commits, which nobody read as the release, are not looked
# at. (Before, every commit that touched the manifest was: a branch whose earlier commit named
# the same release got the tag on that commit — the adversarial reading of this change.)
#
# Refused: a manifest that is not a release; a files/ that is not exactly the manifest's files,
# a path there that is not valid UTF-8 among it; a tag that exists on another commit or one not
# newer than the newest; and a release that is not whole as the code that moves the branch
# `stable` reads it — this repository's own copy, stable.py beside this file, asked here before
# the tag and not a week after it: a manifest that is no regular file or has no «published»
# time, an index that is not the manifest's or not the very text its release's topics give for
# this repository, a commit that changed more than files/, the manifest and the index — and a
# release with no topic index at all, which that code would be red on for good. The tag ruleset
# forbids moving or deleting a tag, so a release tagged and then found broken stays tagged:
# what can be seen here is refused here. The run is red only when the newest manifest commit
# was refused — an older refusal a later release superseded is a warning.
#
# Python runs with -I: the checkout is the working directory, and without it a file of the
# checkout named like a module (json.py) would be imported in the module's place. What is said
# of a refusal is printed escaped: it carries a manifest's own words.
set -euo pipefail
after="${1:?usage: tag-release.sh <after>}"
here="$(cd "$(dirname "$0")" && pwd)"
repository="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set, the owner/name of this repository}"
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
said() {   # a refusal as a workflow command's text may carry it: «%» escaped — it is one line of plain ASCII already
  printf '%s' "${1//%/%25}"
}

declare -A tag_commit
while read -r sha name; do [ -n "${name:-}" ] && tag_commit[$name]=$sha; done < <(tags)
newest=$(printf '%s\n' "${!tag_commit[@]}" | grep -E "$release_re" | newest_of || true)
if [ -n "$newest" ] && git cat-file -e "${tag_commit[$newest]}^{commit}" 2>/dev/null; then
  range="${tag_commit[$newest]}..$after"
else
  range="$after"
fi
commits=$(git rev-list --reverse --first-parent "$range" -- release-manifest.json)
[ -n "$commits" ] || { echo "no release on main after ${newest:-the start} — nothing to tag"; exit 0; }
last=$(printf '%s\n' "$commits" | tail -n 1)

refused_last=""
for sha in $commits; do
  verdict=$(python3 -I - "$sha" "$after" "$here/stable.py" "$repository" <<'PY'
import hashlib, json, re, subprocess, sys, types
from pathlib import Path
sha, after, stable_path, repository = sys.argv[1:5]
def refuse(msg):          # one line of plain printable ASCII, whatever the manifest's words were, and no «#»
    line = " ".join(str(msg).split()).encode("ascii", "backslashreplace").decode()
    print("REFUSED", "".join(c if " " <= c <= "~" and c != "#" else f"\\x{ord(c):02x}" for c in line)); sys.exit(0)
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
def whole():              # as the code that moves `stable` reads a release: this repository's copy of it,
    stable = types.ModuleType("stable")     # its source as it stands — no compiled file beside it is read or left
    stable.__file__ = stable_path
    sys.modules["stable"] = stable
    exec(compile(Path(stable_path).read_bytes(), stable_path, "exec"), stable.__dict__)
    line = tuple(git("rev-list", "--first-parent", after).decode().split())
    try:
        stable.inspect(Path("."), stable.Origin(repository, {tag: sha}, None, line), tag)
    except stable.Refused as exc:
        if exc.before_index:
            refuse("release-manifest.json names no topic index: a release is cut with its rules-index.md, "
                   "and one without it would keep the branch stable red for good")
        refuse(f"not whole: {exc}")
try:
    whole()
except SystemExit:
    raise
except Exception as exc:            # a copy that is missing or broken, git failing: no tag, and the reason
    refuse(f"the release could not be read by the code that moves stable (stable.py beside tag-release.sh): {type(exc).__name__}")
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

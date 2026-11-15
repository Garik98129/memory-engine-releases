#!/usr/bin/env bash
# Tags the releases on main that have no tag yet (workflow `tag`): every commit of main's own
# line after the newest release tag, up to <after>, that changed release-manifest.json, oldest
# first, gets the tag its manifest names. A run that came late — after a newer push's run —
# finds those releases tagged already and does nothing, so no release is skipped whatever order
# the runs take. A commit whose tag is already on it is passed over.
#
# Main's own line — its first parents — and no other commit. A pull request merged by a merge
# commit is tagged on that merge, which holds what main took; its branch's commits, which nobody
# read as the release, are not looked at. (Before, every commit that touched the manifest was: a
# branch whose earlier commit named the same release got the tag on that commit — the
# adversarial reading of this change.) A branch merged by rebase, or pushed, puts its commits on
# that line one by one, and each is looked at as two pull requests merged in a row are: the
# first commit to name a release gets its tag, a later one that names it again is refused — a
# release is cut once —, whatever order the runs take. So a draft and a final cut of one
# release, landed one by one, give the tag to the draft, and this script does not try to tell
# them apart: a rule that took the later cut for the release lost another release's tag when
# the runs came out of order (the last reading of this change). What holds it is that a pull
# request is one commit of that line — merged by squash or by a merge commit — and that merging
# by rebase is switched off in this repository's settings, the owner's to keep (README.md).
#
# A commit that holds, exactly, a release already tagged — main went back to it, a revert — is
# nothing to tag and no refusal.
#
# Refused: a manifest that is not a release; a files/ that is not exactly the manifest's files,
# a path there that is not valid UTF-8 among it; a tag that exists on another commit or one not
# newer than the newest; and a release that is not whole as the code that moves the branch
# `stable` reads it — this repository's own copy, stable.py beside this file, asked here before
# the tag and not a week after it: a manifest that is no regular file or has no «published»
# time, an index that is not the manifest's or not a text its release's topics give for this
# repository (the copy keeps the words an index was published in), a commit that changed
# more than files/, the manifest and the index — and a
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

formed() {    # «OK <tag> <source>» when the commit holds a manifest of the form and exactly its files, else «REFUSED <why>»
  python3 -I - "$1" <<'PY' || echo "REFUSED the check of this commit failed"
import hashlib, json, re, subprocess, sys
sha = sys.argv[1]
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
print("OK", tag, source)
PY
}

whole() {     # «OK» when the code that moves `stable` holds the release at <sha>, named <tag>, whole — else «REFUSED <why>»
  python3 -I - "$1" "$2" "$after" "$here/stable.py" "$repository" <<'PY' || echo "REFUSED the check of this commit failed"
import subprocess, sys, types
from pathlib import Path
sha, tag, after, stable_path, repository = sys.argv[1:6]
def refuse(msg):          # one line of plain printable ASCII, whatever the release's words were, and no «#»
    line = " ".join(str(msg).split()).encode("ascii", "backslashreplace").decode()
    print("REFUSED", "".join(c if " " <= c <= "~" and c != "#" else f"\\x{ord(c):02x}" for c in line)); sys.exit(0)
try:
    stable = types.ModuleType("stable")     # this repository's copy, from its source as it stands: no compiled
    stable.__file__ = stable_path           # file beside it is read or left, and nothing of this script's is inherited
    sys.modules["stable"] = stable
    exec(compile(Path(stable_path).read_bytes(), stable_path, "exec", dont_inherit=True), stable.__dict__)
    line = tuple(subprocess.run(["git", "rev-list", "--first-parent", after], check=True, capture_output=True).stdout.decode().split())
    try:
        stable.inspect(Path("."), stable.Origin(repository, {tag: sha}, None, line), tag)
    except stable.Refused as exc:
        if exc.before_index:
            refuse("release-manifest.json names no topic index: a release is cut with its rules-index.md, "
                   "and one without it would keep the branch stable red for good")
        refuse(f"not whole: {exc}")
except SystemExit:
    raise
except Exception as exc:            # a copy that is missing or broken, git failing: no tag, and the reason
    refuse(f"the release could not be read by the code that moves stable (stable.py beside tag-release.sh): {type(exc).__name__}")
print("OK")
PY
}

kept() {      # what a commit holds of a release: its files/, manifest and index, as git names them
  local name
  for name in files release-manifest.json rules-index.md; do git rev-parse -q --verify "$1:$name" || echo none; done
}

refused_last=""
for sha in $commits; do
  read -r word rest <<<"$(formed "$sha")"
  if [ "$word" = OK ]; then
    read -r tag source <<<"$rest"
    if [ -n "${tag_commit[$tag]:-}" ]; then
      if [ "${tag_commit[$tag]}" = "$sha" ]; then echo "$tag is on $sha already"; continue; fi
      if [ "$(kept "$sha")" = "$(kept "${tag_commit[$tag]}")" ]; then
        echo "$sha holds $tag again, exactly as its tag has it (on ${tag_commit[$tag]}): main went back to it — nothing to tag"
        continue
      fi
      word=REFUSED; rest="tag $tag exists already, on ${tag_commit[$tag]}: a release is cut once"
    elif [ -n "$newest" ] && [ "$(printf '%s\n%s\n' "$newest" "$tag" | newest_of)" != "$tag" ]; then
      word=REFUSED; rest="$tag is not newer than $newest: releases go on one line, newest last"
    else
      read -r word rest <<<"$(whole "$sha" "$tag")"
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

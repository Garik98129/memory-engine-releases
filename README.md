# memory-engine-releases
Releases of memory-engine: at each tag its manifest and, from v0.1.6, its files — the engine's
code, public on the owner's word of 09.11.2026. No personal data.

One tag per release, `vMAJOR.MINOR.PATCH`. At each tag, `release-manifest.json` lists every
file the release puts into a person's repository, with its sha256, and the template commit it
was cut from (`source`). A person's gate (check C1) reads it without a token at
`https://raw.githubusercontent.com/Garik98129/memory-engine-releases/refs/tags/<tag>/release-manifest.json`.
From v0.1.6, `files/` at the same tag holds those files themselves, byte for byte, for an
update to fetch without a token; `files/.github/workflows/` is not where GitHub runs
workflows, so nothing there runs here. Both are written by `python -m engine.setup.release cut`
in the template, which refuses to publish any number, address or token but listed test values.

The workflow `tag` sets each tag: every commit on `main` after the newest release tag that
changed `release-manifest.json` gets the tag its manifest names, oldest first, whatever
order the runs take — only a release `vMAJOR.MINOR.PATCH` whose tag is new and newer than
the newest release tag, and whose `files/` is exactly the manifest's files; releases go on
one line; a path under `files/` that is not valid UTF-8 and a topic index that is not the one the
manifest names are refused too (`.github/scripts/tag-release.sh`; its test:
`bash .github/scripts/test-tag-release.sh`).
A run is red when the newest manifest on `main` was refused; an older refusal a later
release superseded is a warning. A red run that refused a release is a finding to fix in
the manifest, not a tag to set by hand. Only when the tag could not be pushed at all (a permission or an HTTP error) does the
owner set it by hand; the steps are in the template's `docs/DESIGN.md` §5 («Releases and
channels»).

Tags `v*` are never moved or deleted: a tag ruleset forbids it. Old manifests stay here for
good; the gate refuses a rollback by the order of releases, not by a missing manifest.

## The branch `stable`

A chat that has no repository of its own reads the engine's rules here, at one address that
never changes: the branch `stable`, the file `rules-index.md`. From the release that brings it
on, a release has that index beside `files/` — which rules files a topic needs, all of them,
and the release they belong to. The index tells the chat to read those files at its release's
tag, not at the branch: a tag never moves, so a move of the branch between two reads mixes
nothing.

The workflow `stable` moves the branch, with this repository's own copy of the template's
code, `.github/scripts/stable.py`:

- **Every night** — to the newest release that is whole and at least 7 days old, the delay of
  the channel `stable` in a person's repository. Whole is read again here: the tag is on
  `main`, `files/` is the manifest's files byte for byte, the index is the manifest's and is
  the very text its release's topics give, and the commit that brought the release to `main`
  changed nothing but `files/`, the manifest and the index.
- **By hand** — Actions → `stable` → «Run workflow», on `main`, with the release and a reason
  in one line. For a fix that cannot wait its week, or to move back. Moving back, or naming
  the release the branch is on already, withdraws every release newer than it: the night's
  run passes those over, and one of them returns only by a move to it by hand. «Only say what
  would be done» moves nothing.

Every move is one more commit on the branch — the release as its tag has it, and `stable.json`:
the release, when, how, why, the releases withdrawn. Moving back is a commit too; the branch
is never rewritten, and a ruleset forbids a forced push to it and its deletion. Until a
release with an index is a week old the branch does not exist, and the night's run says so
and is green.

A red run of `stable` is a finding, and it stays red until it is answered: a release newer
than the one the branch is on that is not whole — a tag cannot be taken back, so a hand
withdraws it by moving to the release the branch should be on —, or a branch that is not what
this code left, which a move by hand repairs. Nobody pushes to `stable` but that workflow; the
code tells a push it did not make from its own, with one exception it names — a whole release
pushed with a record that says «by hand».

GitHub switches a schedule off in a public repository after 60 days without activity in it,
and writes to the owner; a workflow switched off stays off until it is enabled again (Actions
→ `stable` → «Enable workflow»). While it sleeps the branch stays where it is — a chat reads
the release it read before, never a broken one.

## Pull requests

`main` changes by pull requests of two kinds, never mixed: a **release** — `files/`,
`release-manifest.json`, `rules-index.md`, written by the template's `release cut` and nothing
else — and a change to **what this repository runs** — `.github/`, this file. The workflow
`pr` holds the line (`.github/scripts/pr-paths.py`): a pull request that changes a release's
paths and anything else is red. The code that tags a release and decides when one is trusted
must not come in with the release it judges; and a release whose commit changed more is never
moved to by `stable`, with a tag that cannot be taken back. `release cut` refuses a release
while `.github/scripts/stable.py` here is not the release's own `engine/setup/stable.py`: when
the template changes that file, a pull request brings the copy here first.

The workflow `tests` runs the scripts' tests on a pull request that changes `.github/`:
`bash .github/scripts/test-tag-release.sh`, `test-pr-paths.sh`, `test-move-stable.sh`, then
one dry run of the code that moves `stable` and one reading of the branch's rules, both
against this repository. The tests of that code itself are the template's
(`engine/setup/tests/test_stable.py`).

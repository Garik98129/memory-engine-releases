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

The workflow `tag` sets each tag: every commit of `main`'s own line after the newest release
tag that changed `release-manifest.json` gets the tag its manifest names, oldest first,
whatever order the runs take — only a release `vMAJOR.MINOR.PATCH` whose tag is new and newer
than the newest release tag, and whose `files/` is exactly the manifest's files; releases go
on one line (`.github/scripts/tag-release.sh`; its test:
`bash .github/scripts/test-tag-release.sh`). `main`'s own line: a pull request merged by a
merge commit is tagged on the merge, which holds what `main` took, and what its branch held
on the way is not looked at. Where commits land on that line one by one — a branch merged by
rebase, a push — each is looked at, as two pull requests merged in a row are: the first to
name a release has its tag, and the script does not tell a draft from the cut meant. So a
release's pull request is merged by squash or by a merge commit, and merging by rebase is
switched off in this repository's settings; the script cannot see that setting. A commit that
holds exactly a release already tagged — `main` taken back to it — is nothing to tag. And only
a release that is whole as the code that moves the branch `stable` reads it (below) — asked
before the tag, because a tag cannot be taken back: a release with no topic index, with an
index that is no text its own topics give, or whose commit changed anything beside `files/`, the
manifest and the index gets none.
A run is red when the newest manifest on `main` was refused; an older refusal a later
release superseded is a warning. A red run that refused a release is a finding to fix in
the manifest, not a tag to set by hand. Only when the tag could not be pushed at all (a permission or an HTTP error) does the
owner set it by hand — on the release's own commit, which is not `main` once `main` has gone
on: nothing checks a tag set by hand before it is set, and one on another commit is held to
what it holds and to what the commit of `main`'s own line that brought it changed — the
tagged commit itself where it is on that line, else the merge that brought its branch —, not
to the cut that was read. Where that commit changed anything beside a release, or the
manifest at the tag names another release, the tagged release is one the branch `stable`
refuses for good; a later commit of that line that cut the same release again is one the
branch would move to. The steps are in the template's `docs/DESIGN.md` §5 («Releases and
channels»).

Tags `v*` are never moved or deleted: a tag ruleset forbids it. Old manifests stay here for
good; the gate refuses a rollback by the order of releases, not by a missing manifest.

## The branch `stable`

The branch `stable` is one address that never changes, for a chat that has no repository of
its own and reads the engine's rules here: the branch `stable`, the file `rules-index.md`.
(The chats' instruction fields still name a release's tag; the template's step that replaces
them will name this address.) From the release that brings it on, a release has that index
beside `files/` — which rules files a topic needs, all of them, and the release they belong
to. The index tells the chat to read those files at its release's tag, not at the branch: a
tag never moves, so a move of the branch between two reads mixes nothing.

The workflow `stable` moves the branch, with this repository's own copy of the template's
code, `.github/scripts/stable.py`:

- **Every night** — to the newest release that is whole and at least 7 days old, the delay of
  the channel `stable` in a person's repository. Whole is read again here: the tag is on
  `main`, `files/` is the manifest's files byte for byte, the index is the manifest's and is
  a text its release's topics give — in any of the words the copy keeps: today's, or those
  an index was published in before —, and the commit that brought the release to `main` changed
  nothing but `files/`, the manifest and the index.
- **By hand** — Actions → `stable` → «Run workflow», on `main`: field «to», the release;
  field «reason», why, in one line. For a fix that cannot wait its week, or to move back.
  Moving back, or naming the release the branch is on already, withdraws every release newer
  than it: the night's run passes those over, and one of them returns only by a move to it by
  hand. «Only say what would be done» moves nothing.

Every move is one more commit on the branch — the release as its tag has it, and `stable.json`:
the release, when, how, why, the releases withdrawn. Moving back is a commit too: the code
never rewrites the branch. That nobody else does is a setting of this repository, not a
property of the code: a branch ruleset for `stable`, active, with «Restrict deletions» and
«Block force pushes» and an empty bypass list. The same run reads that ruleset every night
and is red without it (`.github/scripts/stable-rules.py`; it cannot see the bypass list). A
ruleset that lets only that workflow push is not set up here, so a push by somebody with
write access is not prevented; the code tells such a push from its own moves and stops
building on it, with one exception it names: a whole release pushed with a record that says
«by hand».

Until a release with an index is a week old — or a hand moves to one sooner — the branch does
not exist: the night's run says so, and is green while the ruleset is there and no release
with an index is found broken.

A red run of `stable` ends with one line in plain words. A finding stays red until it is
answered. A release newer than the one the branch is on that is not whole: a hand withdraws
it by a run to the release the branch should be on, or a newer whole release comes due and
the branch moves past it. A branch that is not what this code left: a run by hand repairs
it. A red run does not mean the branch stood still: when a release is found broken and a
whole one older than it is due, the same run moves to the older one and is red for the
newer — its line «STABLE moved» says so, and `stable.json` on the branch says where it is.
What a run moves to is a release it has just read as whole.

GitHub switches a schedule off in a public repository after 60 days without activity in it,
and writes to the owner; a workflow switched off stays off until it is enabled again (Actions
→ `stable` → «Enable workflow»). While it sleeps the branch stays where it is.

## Pull requests

`main` is meant to change by pull requests of two kinds, never mixed: a **release** —
`files/`, `release-manifest.json`, `rules-index.md`, written by the template's `release cut`
and nothing else — and a change to **what this repository runs** — `.github/`, this file.
The code that tags a release and decides when one is trusted must not come in with the
release it judges.

What holds that, and what does not:

- The workflow `pr` says, on every pull request, whether it mixes the two
  (`.github/scripts/pr-paths.py`): red when it changes a release's paths and anything else.
  It runs `main`'s own code, never the pull request's. It only says: a red pull request can
  still be merged unless a ruleset on `main` requires the check `paths` — a setting of this
  repository.
- A commit that mixes the two gets no tag: the script that tags refuses a release whose commit
  changed more than a release — a mixed pull request merged all the same by squash or by a
  merge commit, a direct push. This holds a mistake. It does not hold a pull request made to
  change that script itself: GitHub runs the workflow of the commit that was pushed, so the
  code that judges such a release is the code it brought. That one is the reader's of the
  pull request, and the ruleset's that requires `paths`.
- `release cut` refuses a release while `.github/scripts/stable.py` in the checkout it writes
  into is not the release's own `engine/setup/stable.py` — so a release is cut into a checkout
  of `main`, and when the template changes that file, a pull request brings the copy here
  first.
- That pull request is the one place where the copy changes, and the workflow `tests` reads
  it for what it would do to what is already out (`.github/scripts/copy-holds.py`): a copy by
  which a release whole today would be refused — a kept text of the index changed by one
  word, or taken out, is enough —
  or the branch `stable` as it stands would not be read is red, because the branch could then
  neither stay where it is nor move back. A copy that holds more than the one before is not
  judged there: that is read as code.

Settings of this repository that the words above lean on, the owner's to make — the code
cannot make them, and only the first is read by a run:

- a branch ruleset for `stable`: active, «Restrict deletions», «Block force pushes», nobody
  on the bypass list, and no other rule (a rule that restricts creations or updates, or wants
  a pull request, would stop the workflow's own push);
- a branch ruleset for `main`: «Require a pull request before merging», the status check
  `paths` required, «Block force pushes», «Restrict deletions»;
- pull requests merged by squash or by a merge commit: «Allow rebase merging» off — with it
  on, a pull request's commits land on `main` one by one and an earlier cut can take the tag;
- the tag ruleset for `v*`, as it is: no move, no deletion, nobody past it.

The workflow `tests` runs on a pull request that changes `.github/`: the scripts' tests
(`bash .github/scripts/test-tag-release.sh`, `test-pr-paths.sh`, `test-move-stable.sh`), one
dry run of the code that moves `stable` and one reading of the branch's rules, both against
this repository, and the comparison of the copy. The tests of that code itself are the
template's (`engine/setup/tests/test_stable.py`).

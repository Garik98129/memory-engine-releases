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

The workflow `tag` sets each tag: every commit of a push to `main` that changes
`release-manifest.json` gets the tag its manifest names, oldest first — only a release
`vMAJOR.MINOR.PATCH` whose tag is new and newer than the newest release tag, and whose
`files/` is exactly the manifest's files; releases go on one line (`.github/scripts/tag-release.sh`; its test: `bash .github/scripts/test-tag-release.sh`).
A red run that refused a release is a finding to fix in the manifest, not a tag to set by
hand. Only when the tag could not be pushed at all (a permission or an HTTP error) does the
owner set it by hand; the steps are in the template's `docs/DESIGN.md` §5 («Releases and
channels»).

Tags `v*` are never moved or deleted: a tag ruleset forbids it. Old manifests stay here for
good; the gate refuses a rollback by the order of releases, not by a missing manifest.

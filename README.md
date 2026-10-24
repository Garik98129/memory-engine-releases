# memory-engine-releases
Release manifests of memory-engine: file paths and sha256 only, no personal data.

One tag per release, `vMAJOR.MINOR.PATCH`. At each tag, `release-manifest.json` lists every
file the release puts into a person's repository, with its sha256, and the template commit it
was cut from (`source`). A person's gate (check C1) reads it without a token at
`https://raw.githubusercontent.com/Garik98129/memory-engine-releases/refs/tags/<tag>/release-manifest.json`.
It is built by `python -m engine.setup.release manifest` in the template.

Tags `v*` are never moved or deleted: a tag ruleset forbids it. Old manifests stay here for
good; the gate refuses a rollback by the order of releases, not by a missing manifest.

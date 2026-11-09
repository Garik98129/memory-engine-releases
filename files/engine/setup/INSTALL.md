# Installing the memory engine for a person

The steps to set up a person's repository from a published release. This is the one place
the installation is written down: it ships with every release, so the guide at a release's
tag is the guide for that release (owner, 09.11.2026). It is written for the installer —
today the person's trusted contact (`alarm.trusted_contact`, docs/DESIGN.md §3); the
product's direction is that the person needs nobody else for it (Issue #11). Later the same
steps become the script of a guided installer.

References like §5 or C3 point into `docs/DESIGN.md` of the template, which is not shipped
with a release; the checks themselves are under `engine/checks/`.

What you end with: a private repository that holds the engine as released and the
person's part under `user/`; her two AIs (the chat that talks, the bot that writes) set up
from it; checks that let in only what passes; a nightly copy of her Notion; alarms that
reach the owner and the trusted contact; updates that arrive by themselves.

**Who does what.** The installer: GitHub and Notion settings, the files, the chat field.
The bot: its token, its own field, its routines, its pull requests. The person: her «yes»
and a few test messages; she never opens GitHub (P2, P11).

**Time.** Two sittings. The first (half an hour, without her): parts 0 and 1. The second
(about an hour): from part 2 to the end of part 7, in one go — from the push in part 2 the
watch counts the hours, and 36 hours later it expects the record of the installation
(part 7); without it, the bot's next pull request is red.

**Phone or computer.** Everything in a browser works from a phone, in the full GitHub
site. Part 2 (the files) and the fingerprints in part 6 need a computer with `git` and
`python3`, or a Claude Code session.

## 0. What you need

- **A GitHub account that owns her repository.** In the pilot that is the owner's; for a
  person on her own, her own. On GitHub's free plan a private repository has neither
  branch rulesets nor environments: set `github.protection = "none"` (part 2), skip the
  rulesets of parts 3 and 4, keep the Notion token as a plain repository secret (part 4),
  and tell her once, plainly, that the engine then catches mistakes but cannot prevent
  them (§7).
- **A GitHub account for her bot**, with two-factor authentication: her own account, or —
  as in the pilot, where she never uses GitHub — a separate account the installer holds
  for her bot (the pilot's plan, decision 60, 25.10.2026). The bot acts only under it, with
  its own token — never under the owner's account or the trusted contact's (§13,
  question 3 (a)).
- **Her Notion workspace.** Only the owner of a workspace can create an integration for
  it: that is her account.
- **The AI platform** with two instruction fields, the chat's and the bot's. Their limits
  go into `user/config.toml`.
- **The release** to install: the newest tag of the public repository of releases,
  `https://github.com/Garik98129/memory-engine-releases/tags`.

## 1. The repository and its settings (installer, without her)

1. **Create the repository**: private, empty — no README, no licence, no `.gitignore`.
2. **Settings → Actions → General.**
   - «Actions permissions»: allow actions. The engine's workflows use one action,
     `actions/checkout`, pinned by its full sha.
   - «Fork pull request workflows»: «Run workflows from fork pull requests» off.
   - «Workflow permissions»: «Read repository contents and packages permissions». The
     token only reads by default; every workflow asks for what it needs
     (§13, question 3 (c)).
   - In the same block, «Allow GitHub Actions to create and approve pull requests»: on,
     then «Save». The weekly update opens its pull request with the workflow's token; with
     this off, the update run is red with HTTP 403 (§5; §13, question 3 (g)).
   - «Access»: «Not accessible».
3. **Invite the bot.** Settings → Collaborators → Add people → the bot's login. On a
   personal account a collaborator gets write access — there is no role to choose; the bot
   is never the owner. Accept the invitation under the bot's account (a private browser
   window keeps you signed in as yourself). A trusted contact who is not the repository's
   owner is invited the same way: the watch checks that they are a collaborator, or the
   alarms would reach nobody but the owner (N1).

## 2. The engine and her part — one push (installer)

1. **The release files**, into a checkout of the empty repository (`<tag>` is the release,
   e.g. `v0.1.7`), from the folder that holds that checkout:

       git clone --depth 1 --branch <tag> https://github.com/Garik98129/memory-engine-releases releases
       cp -R releases/files/engine releases/files/.github <her repository>/
       mkdir -p <her repository>/user
       printf 'release = "<tag>"\n' > <her repository>/user/engine.lock

   Then, inside her repository's checkout, hold every file to the release's manifest:

       python3 - <<'EOF'
       import hashlib, json, pathlib
       m = json.loads(pathlib.Path("../releases/release-manifest.json").read_text())
       bad = [p for p, sha in m["files"].items()
              if hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() != sha]
       print(m["release"], len(m["files"]), "files; differ:", bad)
       EOF

   `differ: []` — go on. Anything else — stop: the gate would hold every save red (C1).

2. **`user/config.toml`.** C3 checks it on every pull request: the keys it knows and
   their types are in `engine/checks/config_check.py` (`SCHEMA`). A start:

       [person]
       language = "ru"                 # her language: the AIs answer her in it
       work_languages = []             # a question in one of these is answered in it
       country = "IL"                  # sets the document patterns C4 looks for
       timezone = "Asia/Jerusalem"
       github_login = "<the bot's login>"

       [platform]
       name = "grok"
       chat_field_limit = 4000         # the platform's limits, measured
       bot_field_limit = 10000
       routine_limit = 4000            # optional: a routine's limit, where the platform has one
       read_budget_bytes = 16384

       [engine]
       channel = "stable"              # stable: a release arrives 7 days after it is cut

       [alarm]
       trusted_contact = "<the trusted contact's GitHub login>"
       marks_issue = <number>          # step 4 below

       [github]
       protection = "required"         # "none" on GitHub's free plan

   `github_login`, `trusted_contact`, `marks_issue`, `channel` and `protection` are
   protected: after this push they change only in a push of the trusted contact's own
   (C3; part 8). Her document numbers, accounts, phones and addresses — hers and her
   family's — live in her Documents base in Notion, never in the repository (C4 blocks
   them). The trusted contact's e-mail addresses go into the bot's part (step 3) and
   nowhere under `user/routines/` (C4).

3. **Her part of each field** — `user/fields/chat.md` and `user/fields/bot.md`. The first
   line names the part and its own version, not the engine's release — which release is
   installed is `user/engine.lock`'s, and a release does not change her part:

       PERSON PART — <her name> (v1).

   Then, in plain lines: who she is and how to address her; her language and work
   language; her time zone (the fields convert «now» to it); how the AIs talk to her; her
   words for yes, «buy?», «send?», «sign?» and «was → is»; her country's emergency number;
   her Notion bases with their exact names and what each holds; the bot's name — and, for
   a language with grammatical gender, which gender the bot uses for itself, with an
   example of the wrong form (Issue #18, item 3); the trusted contact, and in the bot's
   part their e-mail (the bot invites them to the events she asks for) and the mailbox of
   their own bot (the bot's fallback letter when GitHub cannot take a save, REPO-6); the
   repository. Each field's engine part names what it expects «below». The engine part
   (`engine/rules/fields/*.md`) and her part together must fit the platform's limit
   (C6 counts the joined text).

4. **The Issue for the bot's daily mark.** Issues → New issue, title exactly
   `routine marks`, no body; then «Pin issue». Put its number into `alarm.marks_issue`.

5. **Commit and push to `main`.** This push does not go through the gate — there is no
   gate yet.

   → Workflow `watch` runs (tab Actions). A minute later, two expected alarms, as Issues
   from `github-actions` that name the owner and the trusted contact:
   - «Alarm N1» — a line for each push and branch creation of this installation, «not
     through the gate» (the first push into an empty repository creates `main`: that is
     the line `branch_creation`); and, until part 3, a line that `main` does not require
     `gate`;
   - «Alarm N4» — no mark from the bot yet. It closes itself after the bot's first mark
     (part 5).

   An alarm reaching the owner's phone is itself a check of the installation: the GitHub
   app with notifications on.

## 3. Protect `main` and acknowledge the installation (installer)

On a paid plan, first the protection. Settings → Rules → Rulesets → New ruleset → New
branch ruleset:
- name `main`, status Active;
- Bypass list → Add bypass → «Repository admin» — the owner: the path for a release that
  changes `.github/` (§5). Nobody else;
- Target branches → Add a target → «Include default branch»;
- rules: «Restrict deletions»; «Require a pull request before merging», 0 approvals;
  «Require status checks to pass» → add `gate`, and choose «GitHub Actions» as its source
  — choosable once Actions has run in the repository, as `watch` did in part 2; «Block
  force pushes»;
- Create.

Then, on any plan, **acknowledge the installation**: Actions → `watch` → Run workflow. When
it is done, the Issue «Alarm N1» has a new comment, «The findings changed», whose list
holds only the lines of this installation — with the protection set, the line about
`gate` is gone (on the free plan there was no such line, and the list stays as it was).
Only then close the Issue: closing says «known, not fixing», whoever closes
it, for the findings of the Issue's last list (§6) — a line still in that list would stay
silent for good. Without this step the bot's first pull request is red: the gate runs the
watch, and an alarm nobody acknowledged holds every save.

## 4. The nightly copy of her Notion

1. **The integration** — under her Notion account, best from a computer: the developer
   portal, «Internal connections» → create one for her workspace. Capabilities: read
   content only; no update, no insert, no comments; user information without e-mail
   addresses. Copy its token.
2. **What it may read.** In the connection's «Content access», choose her pages and
   bases. Only what is chosen is copied; a base under a chosen page comes with it.
3. **The secret.** On a paid plan: her repository → Settings → Environments → New
   environment `backup`: deployment branches «Selected branches and tags» → add `main`;
   Environment secrets → `NOTION_TOKEN` = the token. On the free plan: Settings → Secrets
   and variables → Actions → New repository secret, `NOTION_TOKEN` (§9) — a path not yet
   tried live.
   Ask her once, plainly, which of her pages the copy should hold: the defaults are the
   template's, the choice is hers (P10).
4. **The first copy now**: Actions → `backup` → Run workflow. → A green run and a new
   branch `notion-backup` with `pages/`, `data/` and `copy.json`.
5. **Protect the copy** (paid plan): a branch ruleset `notion-backup`, Active, empty bypass list,
   target «Include by pattern» `notion-backup`, rules «Restrict deletions» and «Block force
   pushes». The nightly commits go on; rewriting and deleting do not.

A repository with no Notion — a test repository — disables the workflow instead: Actions →
`backup` → «Show workflow options» (three dots) → «Disable workflow».

## 5. The bot's token and its first mark (installer and bot)

1. **The token.** Under the bot's account: Settings → Developer settings → Personal
   access tokens → Tokens (classic) → Generate new token (classic). Expiration: «No
   expiration» — a token that expires stops the bot's daily mark, and the watch raises N4.
   Scope: `repo` only — never `workflow`, `admin:*` or `delete_repo`. It is shown once.
2. **Give it to the bot** the way its platform keeps secrets. Never press the platform's
   own «Connect GitHub» button under her account: the platform's app may reach more than
   her repository (§13, question 3 (e)).
3. **The probes and the first mark**: send the bot the task «Probes of your token» (below).
   → The login is the bot's; `X-OAuth-Scopes` is `repo`; GitHub refused the file under
   `.github/workflows/` (§13, question 3 (b)); the first `ROUTINE-MARK` is in the Issue
   «routine marks»; the branch `probe/workflow` is deleted either way — an old branch can
   later carry a file back. The file was accepted after all → you revoke the token, stop.
   The next run of `watch` closes «Alarm N4» itself.

## 6. The two fields (installer and bot)

1. **The bot's field**: its engine part `engine/rules/fields/bot.md`, one empty line, her
   part `user/fields/bot.md`. Send the bot: «Set your description to the text below,
   character for character: do not shorten, translate, reformat or fix anything, and no
   empty line at the end. Then reply with its length in characters and its sha256.», then
   the text. → Its numbers equal the ones below.
2. **The chat's field**: the same join of `engine/rules/fields/chat.md` and
   `user/fields/chat.md`, pasted whole into the platform's chat instructions. Today the
   installer does it; the aim is that she does it herself, guided by the bot (Issue #11).
3. **The fingerprints**, in her repository — the text exactly as C6 joins it:

       python3 - <<'EOF'
       import hashlib, pathlib
       for name in ("chat", "bot"):
           engine = pathlib.Path(f"engine/rules/fields/{name}.md").read_bytes().decode("utf-8").strip("\n")
           person = pathlib.Path(f"user/fields/{name}.md").read_bytes().decode("utf-8").strip("\n")
           text = engine + "\n\n" + person
           print(name, len(text.encode("utf-16-le")) // 2, hashlib.sha256(text.encode("utf-8")).hexdigest())
       EOF

   The bot's reply must match the line `bot`. The line `chat` goes into the record in part 7.
4. **Try them**: in her chat, a question about her; a record the chat hands to the bot,
   and the bot's «Recorded: …»; a plain «hello» to each — the tone.

## 7. The routines and the record of the installation (with her)

1. **The first pull request — the routines**: send the bot the task «First pull request —
   your routines» (below). → The bot tells her in plain words what each routine reads,
   does and when, and asks; on her yes it opens a pull request under `user/routines/`. On
   GitHub: the check `gate` turns green, and the repository merges the pull request
   itself (merged by `github-actions`).
2. **The routines on**: before turning each on, the bot runs once the check its task
   gives and shows you the result (ROUT-5); then it turns them on exactly as merged, and
   runs «mark» once.
3. **The record**: send the bot the task «Record of the installation» (below), with the
   chat's fingerprint from part 6. → A second pull request that changes only
   `user/fields/installed.toml`; green; merged by the repository. Open the file on `main`:
   `[fields.chat]` and `[fields.bot]` must equal the lines of part 6. The gate holds only
   the routines' entries to their masters (C2); a wrong field entry would surface only as
   an N5 alarm 36 hours later.
4. **The watch by hand**: Actions → `watch` → Run workflow. → Green, no new alarm.
5. **The update by hand**: Actions → `update` → Run workflow. → «NONE <tag> is the newest
   release due on the <channel> channel». Red with HTTP 403 → the setting of part 1 is
   off (§13, question 3 (g)).

## 8. From then on

- **Updates arrive by themselves.** Every Monday at 04:23 UTC the workflow `update` takes
  the newest release her channel allows — on `stable`, 7 days after it was cut — and opens
  a pull request; the gate checks it and the repository merges it. A release that changes
  `.github/` cannot come that way: the workflow opens an Issue «Engine update vX.Y.Z has to
  be applied by hand» instead. The owner then removes `engine/` and `.github/`, copies the
  release's files as in part 2 and writes the new lock — a file the release dropped must go,
  or C1 holds it red —, pushes, and closes the expected N1 alarm.
- **A protected key changes** (`alarm.trusted_contact`, `alarm.marks_issue`,
  `person.github_login`, `engine.channel`, `github.protection`, `review.second_reader`; a
  page newly left out of the copy; a document pattern dropped): the trusted contact opens
  the pull request first and makes the change in one push of their own. A later push, or
  a check started again after `main` moved, turns it red for good: close it and open a new
  one (C3).
- **Alarms** come as Issues that name the owner and the trusted contact. Closing one says
  «known, not fixing». A base that vanished from the copy holds its alarm until it is back
  or she says she removed it (the bot asks her; §6, N3).
- **Probes in the next days**, one at a time, each written down as «expected / happened /
  what held»: the bot disables and enables `watch` with its token (§13, question 3 (f));
  the hostile probes of §13.

## Tasks for the bot

Copy them as they are; fill the parts in angle brackets.

### Probes of your token

    PROBES OF YOUR GITHUB TOKEN — installation

    1. Call GET https://api.github.com/user with your own GitHub token. Tell me the login
       from the response and the response header X-OAuth-Scopes exactly as given. If you
       cannot see response headers, say so.
    2. In <owner>/<repository>, with the same token, create a branch probe/workflow from
       main and try to add the file .github/workflows/probe.yml with one line: name: probe.
       GitHub is expected to refuse. Tell me exactly what it answered.
    3. Delete the branch probe/workflow in either case. If the file was accepted after
       all, tell me at once.
    4. Your first daily mark: in the open Issue «routine marks» (number <n>), with the same
       token, add one comment, one line and nothing else:
       ROUTINE-MARK <the current time, ISO 8601 with offset> scopes=<the X-OAuth-Scopes header, spaces removed>
    Change nothing else.

### First pull request — your routines

    FIRST PULL REQUEST — your routines

    Your repository: <owner>/<repository>. Read engine/rules/routines.md there (the hard
    rules of every routine and its tasks) and engine/rules/repository.md (how you open a
    pull request). The Issue «routine marks» is number <n>.

    1. Write a routine for each task of routines.md, in your own format, every hard rule
       kept word for word in meaning, each within the routine limit. Their names: morning,
       mail, mark, invitations.
    2. Tell her in her language, with no technical words, what each routine reads, what it
       does and when; the full texts only if she asks. Ask her. Note the time of her yes.
    3. On her yes, with your own token only: a branch from main; user/routines/<name>.md for
       each, the full text exactly as you will install it; a pull request to main whose
       body follows REPO-2 of repository.md.
    4. Do not merge it. When the check «gate» is green, the repository merges it itself.
       Tell her in one plain line that it is saved, and wait.
    5. When it is merged: run each routine's check from its task once and tell me the
       result (ROUT-5); then turn the routines on, the text exactly as merged; run «mark»
       once; tell me.

### Record of the installation

    RECORD OF THE INSTALLATION — after your routines are merged and on

    Open a pull request to main in <owner>/<repository> that changes only
    user/fields/installed.toml (REPO-3 of repository.md), with this content:

    [fields.chat]
    sha256 = "<the chat's fingerprint the installer gives you>"

    [fields.bot]
    sha256 = "<sha256 of your description as you set it, UTF-8>"

    [routines.<name>]
    sha256 = "<sha256 of the installed text of the routine, UTF-8, without leading and trailing newlines>"

    — one [routines.<name>] table per routine. The body: one line «Record of the
    installation». Do not merge it yourself; tell me in one line when it is open.

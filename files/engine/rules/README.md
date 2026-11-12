# Rules of the engine — what the chat and the bot read

The behaviour of a person's two AIs — the chat that talks and the bot that writes —
written once, for everyone, in English (the machine layer; the person is answered in her
own language, `person.language` in `user/config.toml`). Nothing personal lives here: who
the person is, her bases or her map of Notion, her languages, her country and the words she
says yes with are the person's part, under `user/`. Three layers:

1. **The hard part of each instruction field** — `fields/chat.md`, `fields/bot.md`.
   Installed on the AI platform word for word, in front of the person's part
   (`user/fields/chat.md`, `user/fields/bot.md`; C6 joins them, N5 checks the install).
   The safety rules nobody can drop by a PR: C1 holds them to the release. When the chat
   writes itself (`platform.writer = "chat"` — the way out if the bot's field is gone,
   docs/DESIGN_PHASE3.md §6a), there is one field: `fields/solo.md` in front of
   `user/fields/chat.md`, and no bot's field. On a platform whose chat did not open the rules'
   raw link (`BLOB_RULES` in `engine/checks/limits_check.py` — ChatGPT), `fields/solo-blob.md`
   stands in its place: the same text, the same repository, release and files, the link to
   GitHub's own pages of them. It is built by `python -m engine.setup.blob --write`, never by
   hand; a test holds it to `solo.md`.
2. **The core and the topic files** — the full rules behind the fields: `core.md`,
   `health.md`, `mail.md`, `shopping.md`, `documents.md`, `notion.md`, `calendar.md`,
   `repository.md`. The bot reads them in her repository. The chat cannot: its field
   links them in the public repository of releases, at one release's tag, and names the
   files it opens there — once a conversation, before it answers on that topic. The
   field keeps what every answer or safety needs; topic details live only in these files.
   A release that changes a file the chat opens links itself in
   the chat field — `engine/setup/release.py` refuses the cut otherwise — so the chat
   never reads rules her release does not hold, and she reinstalls the chat field.
   Each file is short enough to be read whole (F4, `platform.read_budget_bytes`).
3. **The routines** — `routines.md`: the hard rules of every routine and the tasks of the
   four standard ones — the morning calendar, the mail, the daily mark that N4 watches,
   and the invitations. The bot writes each routine itself from its task and, before turning it on,
   tells her in plain words what it does and when; the master lands in `user/routines/`.

Every rule has an id, `FILE-N`, unique across this folder (F3); a rule is removed by a
PR that names the id in its body. A path named in a rule must exist, and every file here
is in this index (F2).

| File | Read by | When |
|---|---|---|
| `core.md` | bot; chat by its link | bot: every turn; chat: a work file, app menus, a text for others |
| `health.md` | chat by its link, bot | any talk of medicines, procedures, doctors, symptoms |
| `mail.md` | chat by its link, bot | mail, letters, messages to others |
| `shopping.md` | chat by its link, bot | finding, comparing, ordering, paying; advising a food or a cosmetic |
| `documents.md` | bot (the chat: its field's line «Numbers»); the chat that writes, by its link | document numbers, forms, PDF, her signature |
| `notion.md` | bot; the chat that writes, by its link | every write to Notion; «continue setup» (NOTION-12) |
| `calendar.md` | chat by its link, bot | reading her calendar, creating events, invitations to her |
| `routines.md` | bot | writing or changing a routine; the daily mark; updating after a release |
| `repository.md` | bot | every pull request; a read-back the trusted contact asks for |
| `install.md` | the chat that writes, by the link its installation Issue gives | the first conversation after `setup`, and again when she connects or changes her Notion: the map of her anchors, then a few questions |
| `fields/chat.md` | the platform: the chat's instruction field | — |
| `fields/bot.md` | the platform: the bot's instruction field | — |
| `fields/solo.md` | the platform: the chat's instruction field when the chat writes | — |
| `fields/solo-blob.md` | the platform: the same, where the chat reads the rules on GitHub's pages | — |

Room in the fields: the person's part gets what the hard part leaves of the platform
limit (`platform.chat_field_limit`, `platform.bot_field_limit`), measured as a browser
text box counts (`engine.checks.limits_check.length`); C6 refuses an assembly over the
limit. The hard parts as released leave the person's part at least 1 030 (chat) and 2 900
(bot) of the platform's limits 4 000 and 10 000, after the two newlines C6 joins with — today
they are 2 967 and 7 097 characters, so the rooms are 1 031 and 2 901; `solo.md` promises
the same 1 030 of 4 000, and at least the room `chat.md` leaves, so her part moves over whole; it is
2 964, a room of 1 034 (`solo-blob.md`, 2 954, leaves more); the test
`engine/checks/tests/test_rules_budget.py` holds the promise.

# Rules of the engine — what the chat and the bot read

The behaviour of a person's two AIs — the chat that talks and the bot that writes —
written once, for everyone, in English (the machine layer; the person is answered in her
own language, `person.language` in `user/config.toml`). Nothing personal lives here: who
the person is, her bases, her languages, her country's emergency number and the words she
says yes with are the person's part, under `user/`. Three layers:

1. **The hard part of each instruction field** — `fields/chat.md`, `fields/bot.md`.
   Installed on the AI platform word for word, in front of the person's part
   (`user/fields/chat.md`, `user/fields/bot.md`; C6 joins them, N5 checks the install).
   The safety rules nobody can drop by a PR: C1 holds them to the release.
2. **The core and the topic files** — the full rules behind the fields, for the bot when
   it works on that topic and for a chat that can read files: `core.md` (both AIs, every
   turn), `health.md`, `mail.md`, `shopping.md`, `documents.md`, `notion.md`,
   `calendar.md`, `repository.md`. Each is short enough to be read whole (F4,
   `platform.read_budget_bytes`).
3. **The routines** — `routines.md`: the hard rules of every routine and the tasks of the
   four standard ones — the morning calendar, the mail, the daily mark that N4 watches,
   and the invitations. The bot writes each routine itself from its task and, before turning it on,
   tells her in plain words what it does and when; the master lands in `user/routines/`.

Every rule has an id, `FILE-N`, unique across this folder (F3); a rule is removed by a
PR that names the id in its body. A path named in a rule must exist, and every file here
is in this index (F2).

| File | Read by | When |
|---|---|---|
| `core.md` | chat, bot | every turn |
| `health.md` | chat, bot | any talk of medicines, procedures, doctors, symptoms |
| `mail.md` | chat, bot | mail, letters, messages to others |
| `shopping.md` | chat, bot | finding, comparing, ordering, paying |
| `documents.md` | bot (chat reads only the first section) | document numbers, forms, PDF, her signature |
| `notion.md` | bot | every write to Notion |
| `calendar.md` | bot | reading her calendar, creating events, invitations to her |
| `routines.md` | bot | writing or changing a routine; the daily mark |
| `repository.md` | bot | every pull request; a read-back the trusted contact asks for |
| `fields/chat.md` | the platform: the chat's instruction field | — |
| `fields/bot.md` | the platform: the bot's instruction field | — |

Room in the fields: the person's part gets what the hard part leaves of the platform
limit (`platform.chat_field_limit`, `platform.bot_field_limit`), measured as a browser
text box counts (`engine.checks.limits_check.length`); C6 refuses an assembly over the
limit. The hard parts as released leave the person's part at least 990 (chat) and 2 900
(bot) of the pilot's limits 4 000 and 10 000, after the two newlines C6 joins with — today
they are 3 005 and 7 091 characters, so the rooms are 993 and 2 907; the test
`engine/checks/tests/test_rules_budget.py` holds the promise.

# Notion — the bot's writes

- **NOTION-1 Read the base first.** Before writing to a base — read its description and
  the hints of its fields and follow them. The base names and field names are in her
  language, used exactly as they are.
- **NOTION-2 One fact — one place.** A fact lives where its base puts it; the inbox base
  is only for what has no home yet, and she is told where a thing went.
- **NOTION-3 Read back.** After every write — read it back and tell her one line:
  «Recorded: what → where» in her language.
- **NOTION-4 Her Notion is alive.** Add a field, an option, a page or a view when she asks
  or when you advise it — after her yes, showing «was → is». The rest of the base stays
  as it was.
- **NOTION-5 Never delete or rename a field or an option that already holds data.** Say
  it needs the trusted contact. Deleting a row — only on «Delete?» and a yes to it,
  after showing exactly what goes.
- **NOTION-6 «Was → is» after every change** that deletes something, records a new
  medicine, dose or allergy, or changes a base.
- **NOTION-7 Nothing secret** (CORE-6) and no company data (CORE-11) in her Notion.
- **NOTION-8 What is not in Notion is not known.** The nightly copy and your own memory
  are not the record: when a fact matters, read Notion.
- **NOTION-9 A base the nightly copy lost.** When she answers your question of routine
  task 3 (step 4) about a base:
  - she removed it herself → show her «was → is» («I keep asking about „…“ → I stop
    asking»), and after her yes to that, a PR that adds `<id>@<day>` to
    `notion.removed_bases` in `user/config.toml` — the id and the day exactly as the N3
    line gives them (`data/<id>` … `since <day>`); not the id in the base's link, which
    is another one (REPO-2, C7). The alarm closes the next night;
  - she did not → tell her in plain words where it can come back from: Notion's Trash; or
    the base's connections, if the nightly copy's own integration was removed from it.
  Never restore, share or delete anything in Notion yourself for this, and never record a
  base she has not confirmed (Issue #18, item 43).

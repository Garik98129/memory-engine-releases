# Installation — what the AI makes in her Notion, and what it tells her

Read when she sends the first message her installation Issue gives her (docs/DESIGN_PHASE3.md
§5, step 5): the chat that writes itself (`fields/solo.md`) makes her Notion ready. She makes
nothing there by hand. The same message may come again after a run that stopped halfway.

- **INSTALL-1 Check, then ask once.** First, in her language: read her the emergency number her
  part names and ask whether it is right; a wrong one — tell her it must be corrected in her
  instructions field and in her repository, and go on only with her answer. Then search her
  Notion for each key of INSTALL-2. Tell her in plain words what is already there and what you
  will make, and ask one «yes?». Her yes covers the missing items of INSTALL-2 and nothing else.
  Never move, rename or delete a page of hers, and never make an item that is already there.
- **INSTALL-2 The four items.** Each title is in her language and ends with « · » and the key
  in English, exactly — the key is how you find the item in every later conversation, since
  her part names the keys, not the ids. Each goes to the top level of her workspace, under no
  page of hers:
  - a page «About me · about-me»: who she is, how to address her, her family, what she likes
    and avoids — empty headings, she fills them by talking to you;
  - a page «Health · health»: her health profile — medicines with doses, allergies, illnesses,
    her doctors and clinic; empty headings (HEALTH-1 reads it);
  - a base «Documents · documents», columns: name (title), whose, number, valid until (date),
    notes, file (files); one row «Signature» with an empty file (DOC-9, DOC-12);
  - a base «Inbox · inbox», columns: name (title), date (date), note — what has no home yet
    (NOTION-2).
  Each base gets a description of what it holds, in her language, and each column a hint
  (NOTION-1).
- **INSTALL-3 Read back.** After making them, read each one back and give her one line per item
  with its link: «Made: what → link» (NOTION-3). An item that could not be made — say so in one
  plain line and what she can do: send the same first message again later. Two items with the
  same key — ask her which one to keep; never delete either yourself.
- **INSTALL-4 No routine yet.** Set up no scheduled task at installation, even if her platform
  runs them and she asks: the tasks of `routines.md` are written for a bot that keeps a
  repository, and a later release brings tasks for her platform's own scheduler. Offer a
  calendar event instead when she wants a reminder (CORE-3).
- **INSTALL-5 What works here.** End with what works on her platform and what does not, in
  plain words, as the line «Her platform: …» of her part says it — never a promise the platform
  cannot keep. Nothing about the repository, files or checks (CORE-14).

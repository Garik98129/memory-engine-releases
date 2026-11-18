"""The field's Issue: what the user's AI is to hold reaches the user again when it changes (docs/DESIGN_PHASE3.md §6).

Not an alarm. An installation without a bot has nobody who installs anything for the user. The user's AI
holds two things from the Issue «Your assistant's instructions» (`engine.setup.setup_run`): the
text the user pasted — one text for everybody, `engine/rules/fields/fixed.md`, which names no release
and nothing of the user's — and the user's part, which the user sent in the first message of the installation and
that AI keeps on the map. A release that changes the rules changes neither, so it opens no page;
a change of settings changes the user's part, and a release that changes the text's own core — a rare thing —
changes the text. So the
watch, on every run that judges main, reads both as main holds them and the alarms hand over
what is not the one the user was given:

- «given» is the two fingerprints in the body of the newest Issue the workflow opened under the
  title — of the text and of the user's part; `setup` writes them into the Issue it opens, this module
  into every one after;
- both the same → nothing: one Issue per change, not one a night;
- another text, or no such Issue → a new Issue that names the owner, with the whole text to put in
  the old one's place and the message that carries the user's part; only the user's part is another → a new
  Issue with the one message to send, and the text below it as it stands, said to be unchanged.
  Creating an Issue triggers GitHub's notifications and a mention notifies the one named
  (GitHub's documentation); nothing there promises a notification for an edited body. Whether
  the user's mail shows the whole text is the test installation's to see (DESIGN_PHASE3 §8). Then
  every older Issue under the title that is still open is closed with a pointer to the new
  one, so that no page with an old text waits to be pasted. A run that fails between the two
  leaves an old page open; the next run closes it. Every page carries the first message of
  the installation too — the open page is the one the user finds, and the user's AI may ask for
  that message again (NOTION-10).

Both are handed over only from a main that passes its checks. N1 runs the gate's checks on
main itself every time (C1 to C6: the engine is the release's, the user's part holds no number it must
not, fits the limit, reads as text); while it lists such a line, the text on main is one no
check would let in, and «send this to your AI» is the last thing to say about it. Nothing is
handed over then; an open N1 alarm gets one comment that says what is wrong, and once main is
put right the next run hands it over by itself. Nobody has to close anything for it:
closing an alarm neither releases a page nor is needed to get one (the owner's decision,
13.11.2026, quoted in docs/DESIGN_PHASE3.md §6). How the text reached main
is not asked — a push or a PR: without a bot the gate holds what a change contains and the
form of its record, not who wrote it, so a push that passes the same checks is handed over
like a PR, in the run that also raises N1's alarm about the push.

Only an installation where the chat writes and no bot runs (`platform.writer = "chat"`,
`platform.bot = false`): with a bot, the bot or the trusted contact installs the field and N5
and N7 watch it (docs/DESIGN.md §6) — nothing here touches such an installation. Not installed
yet → nothing: `setup` opens the first Issue.

What it cannot see, said: whether the user pasted the text or sent the message. A closed Issue is
neither; the fingerprints say what the user was given, not what the user's AI or map holds. A
collaborator can edit the Issue's body and so its fingerprints — the price the alarms take too
(`engine.checks.alarm`). The fingerprints are of the texts, not of where they go: a platform
changed is seen by the user's part, which names it. A page opened before the user's part had a fingerprint —
by a release that still joined that part to the field — counts as another text: the user is given the
one text and the part's message once. Two runs that both read
the list before either wrote open two Issues with one text; the next run closes the older. And
GitHub refusing this Issue alone, night after night, is a line in the run's log and nothing
more: it is not an alarm.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from . import config_check, gitio, limits_check
from .drift import digest
from .integrity import BOT
from .lock_check import LOCK_FILE
from ..setup import setup as installer

TITLE = "Your assistant's instructions"
PLATFORMS = "engine/platforms.toml"
DIGEST_RE = re.compile(r"<!-- field-digest: ([0-9a-f]{64}) -->")
PART_RE = re.compile(r"<!-- part-digest: ([0-9a-f]{64}) -->")
RELEASE_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
HELD_RE = re.compile(r"<!-- field-held: ([0-9a-f]{64}) -->")
# What goes into the Issue outside the code block is the engine's own short text, never markup:
# a platform's title and where its field is (engine/platforms.toml), a release tag.
PLAIN_RE = re.compile(r"[A-Za-z0-9 ,.:;'()/&-]{1,120}")
# the public repository of releases, as engine/defaults.toml names it: owner/name
MANIFESTS_RE = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
# The user's part is a few lines `setup` builds, and C6 holds it to the user's field's limit: a part over that
# limit is main's failed check, said in the alarm (`hold`). Far beyond it, it is no part at all.
PART_MAX = 16384
DEFAULTS = "engine/defaults.toml"
ANYWHERE = "your AI's instructions field"


def marker(field: str, part: str = "") -> str:
    """The fingerprints of what a page hands over — the text, and the user's part when the page carries
    it — for the end of an Issue's body."""
    return f"<!-- field-digest: {digest(field)} -->\n" + (f"<!-- part-digest: {digest(part)} -->\n" if part else "")


def _toml(repo: Path, rev: str, path: str) -> dict:
    raw = gitio.file_at(repo, rev, path)
    try:
        table = tomllib.loads(raw.decode("utf-8")) if raw is not None else {}
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    return table


def check(repo: Path, main: str, config: dict) -> dict | None:
    """What the alarms hand over, for the run's report: the text on main as the user pastes it
    (`limits_check.installed`), the user's part, where the user's platform keeps the text, and the release the user's
    repository holds. None when this installation is not one whose field goes through an Issue,
    or when the text or the user's part cannot be read — N1 reports that (C1, C5, C6), and a run that
    cannot read them hands nothing over."""
    if limits_check.writer(config) != "chat" or limits_check.has_bot(config):
        return None
    engine_path, person_path, _key, _floor = limits_check.fields(config)["chat"]
    engine_part = gitio.file_at(repo, main, engine_path)
    person_part = gitio.file_at(repo, main, person_path)
    if engine_part is None or person_part is None:
        return None
    try:
        part = person_part.decode("utf-8")
        text = limits_check.installed(config, engine_part.decode("utf-8"), part)
    except UnicodeDecodeError:
        return None
    platform = config.get("platform") if isinstance(config.get("platform"), dict) else {}
    row = _toml(repo, main, PLATFORMS).get(str(platform.get("name")))
    row = row if isinstance(row, dict) else {}
    release = _toml(repo, main, LOCK_FILE).get("release")
    section = _toml(repo, main, DEFAULTS).get("release")
    manifests = section.get("manifests") if isinstance(section, dict) else None
    return entry_of(text, row.get("title"), where_of(row), release, part, manifests)


def _plain(value: object) -> bool:
    """Plain words of the expected shape, without what GitHub turns into a link from a bare
    address: no scheme, no «www.». A reference GitHub links by its form — an Issue's number, a
    commit's sha — would still pass: these words come from the platform table, which C1 holds to
    the release's."""
    return (isinstance(value, str) and bool(PLAIN_RE.fullmatch(value)) and "//" not in value
            and "www." not in value.lower())


def where_of(row: dict) -> object:
    """Where the user's platform keeps the text, for a page that cannot know which of two places the user
    chose: both, when the table names two (`field_all`, engine/platforms.toml)."""
    both = [place for place in (row.get("field"), row.get("field_all")) if isinstance(place, str) and place]
    return ", or ".join(both) if both else None


def entry_of(text: str, title: object, where: object, release: object, part: object = "",
             manifests: object = "") -> dict:
    """The report's entry; whatever is not plain text of the expected shape is left out, so
    nothing but the engine's own words stands outside a code block — no mention, no fence,
    no line break, whichever file or report it came from. The user's part goes into a code block of its
    own, as the text does; a part that is no few lines of text — not one letter or digit in it
    that can be seen (`setup.INVISIBLE_LETTERS`: the fillers that are letters and show nothing),
    or far too long — is left out, and the page then hands over the text alone: «send this to your AI» with nothing in it
    would have the user's AI put nothing in the user's part's place."""
    return {"text": text,
            "title": title if _plain(title) else "",
            "where": where if _plain(where) else ANYWHERE,
            "release": release if isinstance(release, str) and RELEASE_RE.fullmatch(release) else "",
            "part": (part.strip("\n") if isinstance(part, str) and limits_check.length(part) <= PART_MAX
                     and any(ch.isalnum() and ch not in installer.INVISIBLE_LETTERS for ch in part) else ""),
            "manifests": manifests if isinstance(manifests, str) and MANIFESTS_RE.fullmatch(manifests) else ""}


def from_report(report: object) -> dict | None:
    """The run's field, or None when the run did not judge it or its entry is not of `check`'s
    shape. Read as strictly as `check` writes it: the report is a file between two steps."""
    entry = report.get("field") if isinstance(report, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("text"), str) or not entry["text"]:
        return None
    return entry_of(entry["text"], entry.get("title"), entry.get("where"), entry.get("release"), entry.get("part"),
                    entry.get("manifests"))


def names(owner: str, contact: str) -> str:
    people: dict[str, str] = {}
    for name in (owner, contact):
        if name and config_check.LOGIN_RE.fullmatch(name):      # never an unchecked string in a mention
            people.setdefault(name.lower(), name)
    return " ".join(f"@{n}" for n in people.values())


_fenced = installer.fenced       # one fence for every page: this one's and setup's


def body(entry: dict, names: str, run_url: str, first: bool, lead: str | None = None,
         text_changed: bool = True) -> str:
    """The Issue the user reads, in plain English, as `setup`'s Issue. `text_changed` — the text is not
    the one the user was given: the user puts it in the old one's place, and sends the user's part again. Otherwise
    only the user's part is another: one message to send, and the text below as it stands. `lead` — its
    first sentence, when the one who hands it over knows why it changed."""
    to = f"{names} — " if names else ""
    # The user's settings changed → «My settings changed»; the text changed → the user's part as it stands: on such a
    # page the user's settings may be what they were, and a map made before the one text holds no part yet.
    words = installer.PART_MESSAGE if text_changed else installer.SETTINGS_MESSAGE
    message = f"{_fenced(installer.first_message(entry['part'], words))}\n\n" if entry["part"] else ""
    again = (
        "If your AI ever asks you to send the first message of your installation again — or you have not sent "
        "it yet — send it this, in a new conversation:\n\n"
        f"{_fenced(installer.first_message(entry['part']))}\n\n") if entry["part"] else ""
    if not text_changed:
        lead = lead or "Your settings changed."
        return (
            f"{to}{lead}\n\n"
            "**One message to send.** Send your AI this, in a new conversation — inside the project, if your "
            "assistant lives in one. It puts the lines on your map in place of the old ones:\n\n"
            f"{message}"
            "Until you send it, your AI goes on with what it was told of you before. Your assistant's "
            "instructions have not changed since the page before this one: if you did with them what that page "
            "asked, there is nothing to paste; if you have not yet, do it now, with the text below — it is the "
            "same text. Of the two pages' messages send only this page's. Nothing else in your Notion changes. "
            "Close this Issue when you have sent the message, or leave it: the next change opens a new one and "
            "closes this.\n\n"
            f"{_fenced(entry['text'])}\n\n"
            f"{again}"
            f"Run: {run_url}\n\n{marker(entry['text'], entry['part'])}")
    if lead is None:
        release = f" Your copy holds release {entry['release']} now." if entry["release"] else ""
        lead = ("These are your assistant's instructions as they stand now." if first
                else "Your assistant's instructions changed.") + release
        stays = "your Notion and your settings stay as they are"
    else:
        stays = "your Notion stays as it is"         # the caller's lead says what changed: the user's settings may have
    send = ("**Then one message.** Send your AI this, in a new conversation — inside the project, if your "
            "assistant lives in one — so that your map holds your part as it stands:\n\n"
            f"{message}") if message else ""
    return (
        f"{to}{lead}\n\n"
        f"**Put them in the old text's place.** Open the place you pasted your assistant's instructions into — "
        f"{entry['where']}. Replace the text you pasted from a page like this one with the text below, exactly "
        "as it is — what you wrote there yourself stays — and save. Check that its last line came in too. Then "
        "start a new conversation: one that is already open may keep the old instructions.\n\n"
        f"{_fenced(entry['text'])}\n\n"
        f"{send}"
        "Until you paste it, your AI goes on with the instructions it had. Nothing else changes: "
        f"{stays}. Close this Issue when you have done it, or "
        "leave it: the next change opens a new one and closes this.\n\n"
        f"{again}"
        f"Run: {run_url}\n\n{marker(entry['text'], entry['part'])}")


def _own(issue: dict) -> bool:
    return ("pull_request" not in issue and (issue.get("user") or {}).get("login") == BOT
            and str(issue.get("title") or "").startswith(TITLE))


def _given(issue: dict) -> str | None:
    found = DIGEST_RE.findall(issue.get("body") or "")
    return found[-1] if found else None         # the marker is the last thing the workflow writes


def _given_part(issue: dict) -> str | None:
    found = PART_RE.findall(issue.get("body") or "")
    return found[-1] if found else None


def _same(last: dict | None, entry: dict) -> bool:
    """Whether the newest page gave the user this very text and this very part. A page with no
    fingerprint of the user's part — one whose part could not be handed over — gave the user another part.
    A part that cannot be handed over now is not a change: a page with nothing to send is not
    opened for it."""
    if last is None or _given(last) != digest(entry["text"]):
        return False
    return not entry["part"] or _given_part(last) == digest(entry["part"])


def _pages_of(api) -> tuple[list[dict], dict | None]:
    """(the workflow's open Issues under the title, its newest one of any state). Two bounded
    reads: the open ones, and the list newest first up to the first of ours — never every Issue
    and PR the workflow ever opened."""
    path = f"repos/{api.repository}/issues"
    still_open = sorted((i for i in api.pages(path, {"state": "open", "creator": BOT}) if _own(i)),
                        key=lambda i: i["number"])
    last = next((i for i in api.pages(path, {"state": "all", "creator": BOT, "sort": "created",
                                             "direction": "desc"}) if _own(i)), None)
    return still_open, last


def hold(api, entry: dict, alarm: int | None, reasons: list[str], run_url: str) -> list[str]:
    """main does not pass its checks: hand nothing over, and say so once — with what is wrong —
    in the N1 alarm that lists it, if one is open: a comment the user is notified of, since the alarm
    named the user. Nothing to hand over → nothing said. No open alarm (somebody closed it as
    «known») → a line in the run's log; the field still waits for main to be put right."""
    _still_open, last = _pages_of(api)
    want = digest(f"{entry['text']}\n\n{entry['part']}")       # one mark for the pair that waits
    if _same(last, entry):
        return []
    if alarm is None:
        return [f"FIELD held: main does not pass its checks ({len(reasons)} lines of N1) — nothing is handed over"]
    comments = f"repos/{api.repository}/issues/{alarm}/comments"
    if any((c.get("user") or {}).get("login") == BOT and want in HELD_RE.findall(c.get("body") or "")
           for c in api.pages(comments)):
        return [f"held field: main does not pass its checks, said in alarm #{alarm}"]
    listed = "\n".join(line.replace("`", "'").replace("<!--", "<!- -") for line in reasons)
    api.post(comments, {"body": (
        f"Your assistant's instructions or your settings in this repository changed, but the new text is not "
        f"handed to you yet: a check does not let it in —\n\n```text\n{listed}\n```\n\n"
        f"When that is put right, the next run opens the Issue «{TITLE}» with the new text by itself. "
        f"You do not have to close this alarm for it.\n\nRun: {run_url}\n\n"
        f"<!-- field-held: {want} -->\n")})
    return [f"held field: main does not pass its checks, said in alarm #{alarm}"]


def another_ai(entry: dict, to: str) -> str | None:
    """The page for an AI the user did not have before: not «one message to send» but that platform's own
    three steps — the plan it needs, the text, the connections, the first message — as `setup`'s
    Issue says them. None where the platform, the user's part or the repository of releases is not known:
    the plain page then."""
    row = next((r for r in installer.platforms().values() if r.get("title") == entry["title"]), None)
    if row is None or not entry["part"] or not entry["manifests"]:
        return None
    try:
        steps = installer.issue(row, entry["text"], entry["part"], entry["manifests"])
    except (KeyError, installer.SetupError):
        return None
    steps = steps.split("\n", 2)[2] if steps.startswith("# ") else steps       # the Issue's title carries the heading
    return (f"{to}your AI is {row['title']} now. Do the three steps below in it; your Notion and what it holds "
            f"stay as they are.\n\n{steps}")


def sync(api, entry: dict, owner: str, contact: str, run_url: str, lead: str | None = None) -> list[str]:
    """Hand the text and the user's part over when they are not the ones the user was given; what was done.
    Only Issues the workflow opened under the title count; an Issue anybody else opens under it is
    ignored. `lead` — the page's first sentence, from a caller that knows why they changed. When the
    page before this one was for another AI — its title says which — the page is that of
    `another_ai`, whoever hands the field over: the form's run or the night's."""
    own, last = _pages_of(api)
    done: list[str] = []
    page = None
    if not _same(last, entry):
        title = f"{TITLE} — {entry['title']}" if entry["title"] else TITLE
        # A page without a fingerprint gave the user a text this cannot compare: «as they stand», not «changed».
        first = last is None or _given(last) is None
        text_changed = first or _given(last) != digest(entry["text"])
        to = names(owner, contact)
        before = str((last or {}).get("title") or "")
        if entry["title"] and before.startswith(f"{TITLE} — ") and before != title:
            page = another_ai(entry, f"{to} — " if to else "")
        text = (body(entry, to, run_url, first=first, lead=lead, text_changed=text_changed) if page is None
                else f"{page.rstrip()}\n\nRun: {run_url}\n\n{marker(entry['text'], entry['part'])}")
        made = api.post(f"repos/{api.repository}/issues", {"title": title, "body": text})
        current = made["number"]
        done.append(f"opened #{current} field")
    else:
        current = last["number"]
    for old in own:
        if old["number"] == current or old.get("state") != "open":
            continue
        try:
            # The older page keeps what else it said — setup's holds the connections and the first
            # message — so the pointer says so; said once, however often the page is closed again.
            comments = f"repos/{api.repository}/issues/{old['number']}/comments"
            pointer = f"A newer page of your assistant's instructions is #{current}: do what that one says."
            if not any((c.get("user") or {}).get("login") == BOT and (c.get("body") or "").startswith(pointer)
                       for c in api.pages(comments)):
                rest = ("That page has every step." if page is not None
                        else "A message this page gave you to send is replaced by that page's; anything else "
                             "this page asks of you still holds.")
                api.post(comments, {"body": f"{pointer} {rest} Closing."})
            api.patch(f"repos/{api.repository}/issues/{old['number']}", {"state": "closed", "state_reason": "completed"})
            done.append(f"closed #{old['number']} field, an older text")
        except Exception as exc:    # noqa: BLE001 — the new page is the user's already; the next run closes this one
            done.append(f"FIELD ERROR #{old['number']}, an older text, could not be closed: {type(exc).__name__}: {exc}")
    return done

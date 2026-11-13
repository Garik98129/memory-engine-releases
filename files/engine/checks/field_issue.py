"""The field's Issue: a changed instruction field is handed to her again (docs/DESIGN_PHASE3.md §6).

Not an alarm. An installation without a bot has nobody who installs a field for her: she pasted
the text of the Issue «Your assistant's instructions» once (`engine.setup.setup_run`), and her AI
keeps that text until she pastes another. A release that changes the field's hard part — the
link to the rules of a new release above all — or a change of her own part leaves her AI on the
old text, and with N5 off nothing would say so. So the watch, on every run that judges main,
reads the field as C6 joins it and the alarms hand it over when it is not the one she was given:

- «given» is the fingerprint in the body of the newest Issue the workflow opened under the
  title — `setup` writes it into the Issue it opens, this module into every one after;
- the same fingerprint → nothing: one Issue per change, not one a night;
- another one, or no such Issue → a new Issue that names the owner, with the whole new text:
  creating an Issue triggers GitHub's notifications and a mention notifies the one named
  (GitHub's documentation); nothing there promises a notification for an edited body. Whether
  her mail shows the whole text is the test installation's to see (DESIGN_PHASE3 §8). Then
  every older Issue under the title that is still open is closed with a pointer to the new
  one, so that no page with an old text waits to be pasted. A run that fails between the two
  leaves an old page open; the next run closes it. Every page carries the first message of
  her installation too — the open page is the one she finds, and her AI may ask her to send
  that line again (NOTION-10).

A field is handed over only from a main that passes its checks. N1 runs the gate's checks on
main itself every time (C1 to C6: the engine is the release's, her part holds no number it must
not, fits the limit, reads as text); while it lists such a line, the text on main is one no
check would let in, and «paste this into your AI» is the last thing to say about it. Nothing is
handed over then; an open N1 alarm gets one comment that says what is wrong, and once main is
put right the next run hands the field over by itself. Nobody has to close anything for it:
closing an alarm neither releases a field nor is needed to get one (the owner's decision,
13.11.2026, quoted in docs/DESIGN_PHASE3.md §6). How the text reached main
is not asked — a push or a PR: without a bot the gate holds what a change contains and the
form of its record, not who wrote it, so a push that passes the same checks is handed over
like a PR, in the run that also raises N1's alarm about the push.

Only an installation where the chat writes and no bot runs (`platform.writer = "chat"`,
`platform.bot = false`): with a bot, the bot or the trusted contact installs the field and N5
and N7 watch it (docs/DESIGN.md §6) — nothing here touches such an installation. Not installed
yet → nothing: `setup` opens the first Issue.

What it cannot see, said: whether she pasted the text. A closed Issue is not «pasted»; the
fingerprint says what she was given, not what her AI holds. A collaborator can edit the Issue's
body and so its fingerprint — the price the alarms take too (`engine.checks.alarm`). The
fingerprint is of the text, not of where it goes: a platform changed to one with the same text
opens no Issue by the watch (the form, when she changes her AI with it, opens its own page:
`engine.setup.setup_run.change`). Two runs that both read
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
RELEASE_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
HELD_RE = re.compile(r"<!-- field-held: ([0-9a-f]{64}) -->")
# What goes into the Issue outside the code block is the engine's own short text, never markup:
# a platform's title and where its field is (engine/platforms.toml), a release tag.
PLAIN_RE = re.compile(r"[A-Za-z0-9 ,.:;'()/&-]{1,120}")
# install.md of a release in a repository of releases, by the raw link or on GitHub's page
FIRST_RE = re.compile(r"https://(?:raw\.githubusercontent\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/|"
                      r"github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/blob/)v[0-9]+\.[0-9]+\.[0-9]+/files/engine/rules/install\.md")
DEFAULTS = "engine/defaults.toml"
ANYWHERE = "your AI's instructions field"


def marker(field: str) -> str:
    """The fingerprint of a field as handed over, for the end of an Issue's body."""
    return f"<!-- field-digest: {digest(field)} -->\n"


def _toml(repo: Path, rev: str, path: str) -> dict:
    raw = gitio.file_at(repo, rev, path)
    try:
        table = tomllib.loads(raw.decode("utf-8")) if raw is not None else {}
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    return table


def check(repo: Path, main: str, config: dict) -> dict | None:
    """What the alarms hand over, for the run's report: the field on main as C6 joins it, where
    her platform keeps it, and the release her repository holds. None when this installation is
    not one whose field goes through an Issue, or when a part of the field cannot be read —
    N1 reports that (C1, C5, C6), and a run that cannot read the field hands nothing over."""
    if limits_check.writer(config) != "chat" or limits_check.has_bot(config):
        return None
    engine_path, person_path, _key, _floor = limits_check.fields(config)["chat"]
    engine_part = gitio.file_at(repo, main, engine_path)
    person_part = gitio.file_at(repo, main, person_path)
    if engine_part is None or person_part is None:
        return None
    try:
        text = limits_check.assemble(engine_part.decode("utf-8"), person_part.decode("utf-8"))
    except UnicodeDecodeError:
        return None
    platform = config.get("platform") if isinstance(config.get("platform"), dict) else {}
    row = _toml(repo, main, PLATFORMS).get(str(platform.get("name")))
    row = row if isinstance(row, dict) else {}
    release = _toml(repo, main, LOCK_FILE).get("release")
    section = _toml(repo, main, DEFAULTS).get("release")
    manifests = section.get("manifests") if isinstance(section, dict) else None
    first = (installer.first_message_link(manifests, release, str(platform.get("name")))
             if isinstance(manifests, str) and isinstance(release, str) else None)
    return entry_of(text, row.get("title"), row.get("field"), release, first)


def entry_of(text: str, title: object, where: object, release: object, first: object) -> dict:
    """The report's entry; whatever is not plain text of the expected shape is left out, so
    nothing but the engine's own words stands outside the code block — no mention, no fence,
    no line break, whichever file or report it came from."""
    return {"text": text,
            "title": title if isinstance(title, str) and PLAIN_RE.fullmatch(title) else "",
            "where": where if isinstance(where, str) and PLAIN_RE.fullmatch(where) else ANYWHERE,
            "release": release if isinstance(release, str) and RELEASE_RE.fullmatch(release) else "",
            "first": first if isinstance(first, str) and FIRST_RE.fullmatch(first) else ""}


def from_report(report: object) -> dict | None:
    """The run's field, or None when the run did not judge it or its entry is not of `check`'s
    shape. Read as strictly as `check` writes it: the report is a file between two steps."""
    entry = report.get("field") if isinstance(report, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("text"), str) or not entry["text"]:
        return None
    return entry_of(entry["text"], entry.get("title"), entry.get("where"), entry.get("release"), entry.get("first"))


def names(owner: str, contact: str) -> str:
    people: dict[str, str] = {}
    for name in (owner, contact):
        if name and config_check.LOGIN_RE.fullmatch(name):      # never an unchecked string in a mention
            people.setdefault(name.lower(), name)
    return " ".join(f"@{n}" for n in people.values())


_fenced = installer.fenced       # one fence for every page: this one's and setup's


def body(entry: dict, names: str, run_url: str, first: bool, lead: str | None = None) -> str:
    """The Issue she reads: the field to paste again and where; plain English, as `setup`'s Issue.
    `lead` — its first sentence, when the one who hands the field over knows why it changed."""
    if lead is None:
        release = f" Your copy holds release {entry['release']} now." if entry["release"] else ""
        lead = ("These are your assistant's instructions as they stand now." if first
                else "Your assistant's instructions changed.") + release
        stays = "your Notion and your settings stay as they are"
    else:
        stays = "your Notion stays as it is"         # the caller's lead says what changed: her settings may have
    to = f"{names} — " if names else ""
    again = (
        "If your AI asks you to send the first message of your installation again — or you have not sent it "
        "yet — send it this, in a new conversation:\n\n"
        f"```\n{installer.FIRST_MESSAGE.format(link=entry['first'])}\n```\n\n") if entry["first"] else ""
    return (
        f"{to}{lead}\n\n"
        f"**Paste them again.** Open {entry['where']}. Replace the whole old text with the text below, exactly "
        "as it is, and save. Check that its last line came in too. Then start a new "
        "conversation: one that is already open may keep the old instructions.\n\n"
        f"{_fenced(entry['text'])}\n\n"
"Until you paste it, your AI goes on with the instructions and the rules it had. Nothing else changes: "
        f"{stays}. Close this Issue when you have pasted the text, or "
        "leave it: the next change opens a new one and closes this.\n\n"
        f"{again}"
        f"Run: {run_url}\n\n{marker(entry['text'])}")


def _own(issue: dict) -> bool:
    return ("pull_request" not in issue and (issue.get("user") or {}).get("login") == BOT
            and str(issue.get("title") or "").startswith(TITLE))


def _given(issue: dict) -> str | None:
    found = DIGEST_RE.findall(issue.get("body") or "")
    return found[-1] if found else None         # the marker is the last thing the workflow writes


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
    in the N1 alarm that lists it, if one is open: a comment she is notified of, since the alarm
    named her. Nothing to hand over → nothing said. No open alarm (somebody closed it as
    «known») → a line in the run's log; the field still waits for main to be put right."""
    _still_open, last = _pages_of(api)
    want = digest(entry["text"])
    if last is not None and _given(last) == want:
        return []
    if alarm is None:
        return [f"FIELD held: main does not pass its checks ({len(reasons)} lines of N1) — nothing is handed over"]
    comments = f"repos/{api.repository}/issues/{alarm}/comments"
    if any((c.get("user") or {}).get("login") == BOT and want in HELD_RE.findall(c.get("body") or "")
           for c in api.pages(comments)):
        return [f"held field: main does not pass its checks, said in alarm #{alarm}"]
    listed = "\n".join(line.replace("`", "'").replace("<!--", "<!- -") for line in reasons)
    api.post(comments, {"body": (
        f"Your assistant's instructions in this repository changed, but the new text is not handed to you "
        f"yet: a check does not let it in —\n\n```text\n{listed}\n```\n\n"
        f"When that is put right, the next run opens the Issue «{TITLE}» with the new text by itself. "
        f"You do not have to close this alarm for it.\n\nRun: {run_url}\n\n"
        f"<!-- field-held: {want} -->\n")})
    return [f"held field: main does not pass its checks, said in alarm #{alarm}"]


def another_ai(entry: dict, to: str) -> str | None:
    """The page for an AI she did not have before: not «paste again» but that platform's own three
    steps — the plan it needs, the connections, the first message — as `setup`'s Issue says them.
    None where the platform or its first message is not known: the plain page then."""
    row = next((r for r in installer.platforms().values() if r.get("title") == entry["title"]), None)
    if row is None or not entry["first"]:
        return None
    try:
        steps = installer.issue(row, entry["text"], entry["first"])
    except (KeyError, installer.SetupError):
        return None
    steps = steps.split("\n", 2)[2] if steps.startswith("# ") else steps       # the Issue's title carries the heading
    return (f"{to}your AI is {row['title']} now. Do the three steps below in it; your Notion and what it holds "
            f"stay as they are.\n\n{steps}")


def sync(api, entry: dict, owner: str, contact: str, run_url: str, lead: str | None = None) -> list[str]:
    """Hand the field over when it is not the one she was given; what was done. Only Issues the
    workflow opened under the title count; an Issue anybody else opens under it is ignored.
    `lead` — the page's first sentence, from a caller that knows why the field changed. When the
    page before this one was for another AI — its title says which — the page is that of
    `another_ai`, whoever hands the field over: the form's run or the night's."""
    own, last = _pages_of(api)
    done: list[str] = []
    page = None
    if last is None or _given(last) != digest(entry["text"]):
        title = f"{TITLE} — {entry['title']}" if entry["title"] else TITLE
        # A page without a fingerprint gave her a text this cannot compare: «as they stand», not «changed».
        first = last is None or _given(last) is None
        to = names(owner, contact)
        before = str((last or {}).get("title") or "")
        if entry["title"] and before.startswith(f"{TITLE} — ") and before != title:
            page = another_ai(entry, f"{to} — " if to else "")
        text = (body(entry, to, run_url, first=first, lead=lead) if page is None
                else f"{page.rstrip()}\n\nRun: {run_url}\n\n{marker(entry['text'])}")
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
            pointer = f"A newer text of your assistant's instructions is in #{current}: paste that one."
            if not any((c.get("user") or {}).get("login") == BOT and (c.get("body") or "").startswith(pointer)
                       for c in api.pages(comments)):
                rest = ("That page has every step." if page is not None
                        else "Anything else this page asks of you still holds.")
                api.post(comments, {"body": f"{pointer} {rest} Closing."})
            api.patch(f"repos/{api.repository}/issues/{old['number']}", {"state": "closed", "state_reason": "completed"})
            done.append(f"closed #{old['number']} field, an older text")
        except Exception as exc:    # noqa: BLE001 — the new page is hers already; the next run closes this one
            done.append(f"FIELD ERROR #{old['number']}, an older text, could not be closed: {type(exc).__name__}: {exc}")
    return done

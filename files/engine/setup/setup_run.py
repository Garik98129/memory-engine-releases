"""The workflows `setup` and `settings` of a repository made from the template (DESIGN_PHASE3 §5,
step 2; §6).

    python -m engine.setup.setup_run --main MAIN_SHA --run-url URL --platform chatgpt \\
        --language ru --country IL --timezone Asia/Jerusalem --name Anna --gender female \\
        [--work-languages en,he]
    python -m engine.setup.setup_run --keep --main MAIN_SHA --run-url URL --timezone Europe/Berlin

Run by `.github/workflows/setup.yml`, on main's code, when the repository's owner presses
«Run workflow»: her first installation. It builds her config and her part of the field with
`engine.setup.setup` — every refusal of a bad choice included — and lands them the way every
change lands: a commit on the branch `setup/installation`, a PR this workflow opens, the
gate started through workflow_dispatch (a PR opened with GITHUB_TOKEN starts no
pull_request_target run), and job `merge`. The gate knows the PR by its branch (kind
`setup`) and trusts neither that nor its author: C2 lets a setup PR only add these two files
to a repository never installed, and holds them byte for byte to what `setup` builds from
seven choices (`setup.held`). So a head somebody else pushed onto the branch can differ only
in those choices. The Issue «Your assistant's instructions» — the field to paste, where, and
the first message — is opened before the gate is started: a run that fails after it leaves
her with her instructions.

On a repository already installed her settings change by the form `settings` (`--keep`,
`.github/workflows/settings.yml`; DESIGN_PHASE3 §6). Every answer there already stands on
«keep as it is» — the first item of its lists, an empty text field — so she gives only the
one that changes, and the rest is read from her config on main, where `setup` keeps all seven
(her name and the grammar too); «none» takes her work languages away. The form `setup` installs
once: on an installed repository it refuses and names `settings` — its lists stand on a real
answer, and one left untouched would change her AI unasked. Answers that are her settings
already are no error: she is pointed to the page that holds her instructions as they stand.
Otherwise the run builds her two files anew, commits them on `setup/settings`, opens the PR and
starts the gate — which lets such a PR only change the two files where they stand, held byte
for byte as at installation, and only where that takes nothing from main's config: no bot's
installation, no protected key, no key of somebody's own hand
(`config_check.settings_findings`; the run asks the same before it pushes). Her part of the
field is `setup`'s text and is written anew. Then the run waits a few minutes and says what
became of the PR: merged, and main is that merge alone — her new field goes to her at once, in
a new Issue (`engine.checks.field_issue`), with the steps of her new AI when she changed it
(`field_issue.another_ai`); the gate said no to this very head (its «gate» status on the
commit), the PR was closed, or it merged with a head this run did not push — the run ends red,
so GitHub tells her; still waiting, or main moved on meanwhile — no error, the night's run
hands the field over. She edits no file and closes no alarm: the change comes through the gate,
not past it.

Her answers are taken as a phone types them: the letter case of a language, a country and a
time zone is put right before `setup` reads them («Ru», «il», «europe/berlin» — a phone's
keyboard starts a field with a capital). What the run did is written on the run's own page too
(GitHub's job summary), so that a refusal is read there and not four taps down in a step's log.

Refuses — «ERROR», exit 1, before anything is pushed or written — a run not started by the
repository's owner (a re-run somebody else pressed included: GitHub keeps the first actor on
a re-run, so both names are held), a run whose main is no longer main (a re-run after the
installation landed), `setup` on a repository already installed and `settings` on one not
installed yet, an installed repository where building her settings again would take something
from main's config, an installation with a bot, a choice `setup` refuses, a PR somebody else
opened from the branch. An organisation's repository has no owner who can press the button:
the refusal says to make it under her own account. GitHub's own refusal to let the workflow
open a pull request — off on a new repository — comes after the push, so the branch is already
there; it is said with the setting that allows it, and the next run goes on from that branch.
"""
from __future__ import annotations

import argparse
import itertools
import os
import sys
import tempfile
import textwrap
import time
import tomllib
from pathlib import Path
from typing import Callable
from zoneinfo import available_timezones

from engine.checks import config_check, field_issue, gitio, limits_check, paths_check
from engine.checks.github import ApiError, GitHub
from engine.setup import setup, update
from engine.setup.update import BOT, BOT_EMAIL, _git

BRANCH = "setup/installation"
PR_TITLE = "Installation: her config and her part of the field"
PR_BODY = ("Installation by the workflow «setup»: her config and her part of the chat field, into a "
           "repository not installed yet.\n\nThe gate checks it like any PR: C2 lets a setup PR only add "
           "these two files where there were none, C1 to C6 hold them as they hold every change.\n")
# A change of her settings on an installed repository (DESIGN_PHASE3 §6). No numbers in the texts:
# a PR's body becomes the merge commit's message, and C4 reads it like any PR body.
SETTINGS_BRANCH = "setup/settings"
SETTINGS_TITLE = "Settings: her config and her part of the field, by the form again"
SETTINGS_BODY = ("A change of her settings by a form: her config and her part of the chat field, built again "
                 "from her answers.\n\nThe gate checks it like any PR: C2 lets a setup PR only change these "
                 "two files where they stand, C3 lets it take nothing from the config main holds, and C1 to C6 "
                 "hold them as they hold every change.\n")
KEEP = "keep as it is"                  # the form «settings»: the first item of its lists, and what an empty field means
NONE = "none"                           # …and how she takes her work languages away there
ANSWERS = ("platform", "language", "country", "timezone", "name", "gender")     # every one but her work languages
ISSUE_TITLE = field_issue.TITLE      # one title: the watch hands a changed field over under it (field_issue.py)
SETTING = ("Settings → Actions → General → Workflow permissions: tick «Allow GitHub Actions to create "
           "and approve pull requests», Save, and run the form again")


def setup_commit(repo: Path, main: str, files: dict[str, str], title: str = PR_TITLE) -> str:
    """main with her two files added, or put in place of the ones it holds — a commit of its
    own, built in a scratch index; the checkout is not touched. Dated with main's commit time:
    the same main and the same choices give the same commit, so a second run pushes nothing new."""
    entries = []
    for path, text in sorted(files.items()):
        blob = _git(repo, "hash-object", "-w", "--stdin", stdin=text.encode("utf-8")).decode().strip()
        entries.append(f"100644 {blob}\t{path}\0".encode())
    stamp = f"{_git(repo, 'log', '-1', '--format=%ct', main).decode().strip()} +0000"
    with tempfile.TemporaryDirectory() as tmp:
        index = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(repo, "read-tree", main, env=index)
        _git(repo, "update-index", "-z", "--index-info", stdin=b"".join(entries), env=index)
        tree = _git(repo, "write-tree", env=index).decode().strip()
    who = {"GIT_AUTHOR_NAME": BOT, "GIT_AUTHOR_EMAIL": BOT_EMAIL, "GIT_AUTHOR_DATE": stamp,
           "GIT_COMMITTER_NAME": BOT, "GIT_COMMITTER_EMAIL": BOT_EMAIL, "GIT_COMMITTER_DATE": stamp}
    return _git(repo, "commit-tree", tree, "-p", main, "-m", title, env=who).decode().strip()


def open_pr(api, prs: list[dict], branch: str = BRANCH, title: str = PR_TITLE, body: str = PR_BODY) -> int:
    """The setup PR — the open one this workflow opened before, or a new one. No numbers in
    the body: it becomes the merge commit's message, and C4 reads it like any PR body."""
    mine = update.existing_pr(api, prs, branch)
    if mine is not None:
        return mine["number"]
    try:
        return api.post(f"repos/{api.repository}/pulls",
                        {"title": title, "head": branch, "base": "main", "body": body})["number"]
    except ApiError as exc:
        if "not permitted to create" in exc.reason:
            raise setup.SetupError(f"GitHub does not let this workflow open a pull request yet. {SETTING}") from None
        raise


def open_issue(api, title: str, text: str, number: int, field: str) -> tuple[int, str]:
    """The Issue with her instructions — the open one this workflow opened before, brought up
    to date, or a new one. It ends with the fingerprint of the field it gives her: the watch
    opens no second Issue for the same text (`engine.checks.field_issue`)."""
    body = text.split("\n", 2)[2] if text.startswith("# ") else text      # the title carries the heading
    body += (f"\nYour settings land with PR #{number}: it merges itself in a few minutes, once its "
             f"checks pass.\n\n{field_issue.marker(field)}")
    for item in api.pages(f"repos/{api.repository}/issues", {"state": "open"}):
        if ((item.get("user") or {}).get("login") == BOT and "pull_request" not in item
                and str(item.get("title", "")).startswith(ISSUE_TITLE)):
            api.patch(f"repos/{api.repository}/issues/{item['number']}", {"title": title, "body": body})
            return item["number"], "brought up to date"
    return api.post(f"repos/{api.repository}/issues", {"title": title, "body": body})["number"], "opened"


def _push_and_open(repo: Path, main: str, api, origin: Callable[[str], str], token: str, files: dict[str, str],
                   branch: str, title: str, body: str, sleep: Callable[[float], None]) -> tuple[int, str, bool]:
    """Her files as one commit on the workflow's branch and the PR that shows it: (number, commit,
    whether the PR was open already with another head — an earlier change this one replaces)."""
    commit = setup_commit(repo, main, files, title)
    prs = update.open_prs(api)
    others = update.taken_by_others(api, prs, branch)
    if others:
        pr = others[0]
        raise setup.SetupError(f"PR #{pr.get('number')} by {(pr.get('user') or {}).get('login')} is open from "
                               f"{branch}; setup does not push into a PR it did not open")
    mine = update.existing_pr(api, prs, branch)
    replaced = mine is not None and (mine.get("head") or {}).get("sha") != commit
    if mine is None or replaced:
        update.push(repo, origin(api.repository), commit, branch, token)
    number = open_pr(api, prs, branch, title, body)
    update.wait_for_head(api, number, commit, sleep)
    return number, commit, replaced


class NotLanded(Exception):
    """Her new settings did not land as asked, and waiting will not change that: the run ends
    red, so GitHub tells her it failed. `lines` — what the run did before that."""

    def __init__(self, lines: list[str], why: str):
        super().__init__(why)
        self.lines = lines


UNREAD = object()        # the «gate» status could not be read: not the same as «there is none»


def _gate_said(api, commit: str) -> object:
    """The newest «gate» status on the commit — job `check` writes it on a PR's head, so it is this
    PR's and nobody else's —, None when there is none, UNREAD when it cannot be read here."""
    try:
        return next((status for status in api.pages(f"repos/{api.repository}/commits/{commit}/statuses")
                     if status.get("context") == "gate"), None)
    except ApiError:
        return UNREAD


def _gate_refused(api, commit: str, before: object = None) -> str | None:
    """Where the gate said no to this very head, in the run this run started: the address its newest
    «gate» status carries, when that status is a failure. `before` — the id of the «gate» status the
    commit carried before the gate was started: the same answers on the same main give the same
    commit, and what an earlier run's gate said to it is not the word of this one; UNREAD — it
    could not be read then, so no failure can be told to be this run's, and the run waits. None
    while there is no newer word, or it is pending or a success, or cannot be read."""
    status = _gate_said(api, commit)
    if before is UNREAD or not isinstance(status, dict) or status.get("state") not in ("failure", "error"):
        return None
    if before is not None and status.get("id") == before:
        return None
    return str(status.get("target_url") or "")


def _alone(api, pr: dict, main: str) -> bool:
    """Whether main is now exactly this PR's merge on top of the main this run built on —
    nothing else landed in between, so main's field is the one this run built."""
    merge = pr.get("merge_commit_sha")
    if not merge or api.get(f"repos/{api.repository}/git/ref/heads/main")["object"]["sha"] != merge:
        return False
    parents = api.get(f"repos/{api.repository}/git/commits/{merge}").get("parents") or []
    return [p.get("sha") for p in parents] == [main]


def landed(api, number: int, commit: str, main: str, sleep: Callable[[float], None],
           tries: int = 20, pause: float = 15.0, before: object = None) -> tuple[str, str]:
    """What became of the PR within a few minutes: «alone» — merged with this head, and main is
    that merge and nothing more; «moved» — merged with this head, but that could not be shown;
    «other» — merged with a head this run did not push; «closed» — closed unmerged; «refused» —
    the gate said no to this head (with its run's address); «waiting» — none of these yet."""
    for attempt in range(tries):
        pr = api.get(f"repos/{api.repository}/pulls/{number}")
        if pr.get("merged") is True:
            if (pr.get("head") or {}).get("sha") != commit:
                return "other", ""
            return ("alone" if _alone(api, pr, main) else "moved"), ""
        if pr.get("state") == "closed":
            return "closed", ""
        refused = _gate_refused(api, commit, before)
        if refused is not None:
            return "refused", refused
        if attempt + 1 < tries:
            sleep(pause)
    return "waiting", ""


def _close_own(api, branch: str) -> tuple[str, int | None]:
    """Close the PR this workflow left open from the branch, if any: an older change nobody wants
    now. What came of it, and its number: «none» — there was no such PR; «closed»; «open» —
    GitHub would not close it, so it is not withdrawn and may still come in; «unknown» — GitHub
    would not say whether there is one. Neither of the last two is a reason to fail her run, and
    neither may pass for «nothing was on its way»."""
    try:
        mine = update.existing_pr(api, update.open_prs(api), branch)
    except ApiError:
        return "unknown", None
    if mine is None:
        return "none", None
    try:
        api.patch(f"repos/{api.repository}/pulls/{mine['number']}", {"state": "closed"})
    except ApiError:
        return "open", mine["number"]
    return "closed", mine["number"]


def _entry(repo: Path, platform: str, field: str) -> dict:
    """The field as `field_issue` hands it over: the text, where her platform keeps it, her release."""
    row = setup.platforms()[platform]
    _manifests, tag = setup._release(repo)
    return field_issue.entry_of(field, row.get("title"), row.get("field"), tag, setup.install_link(repo, platform))


def note(api, owner: str, text: str, run_url: str) -> str:
    """A word to her on the page that holds her instructions as they stand — said when it happens,
    whatever page comes later and whoever opens it. The line for the run's log."""
    try:
        _open, last = field_issue._pages_of(api)
        if last is None:
            return "FIELD no page of hers to say it on"
        to = field_issue.names(owner, "")
        api.post(f"repos/{api.repository}/issues/{last['number']}/comments",
                 {"body": f"{to + ' — ' if to else ''}{text}\n\nRun: {run_url}\n"})
        return f"FIELD said in #{last['number']}"
    except Exception as exc:    # noqa: BLE001 — a word to her that could not be said is no reason to fail her run
        return f"FIELD could not say it on her page ({type(exc).__name__}: {exc})"


WITHDRAWN = ("The change you asked for before (PR #{number}) had not come in, and is withdrawn now: if you still want "
             "it, run the form again with that answer.")
STILL_OPEN = ("An earlier change you asked for (PR #{number}) is still open, and it could not be withdrawn now: it may "
              "still come in. To withdraw it, run the form again in a few minutes.")
NOT_LOOKED = ("Whether an earlier change of yours is still on its way could not be checked now: if one is, it is not "
              "withdrawn. To be sure, run the form again in a few minutes.")
REPLACED = ("An earlier change of yours that had not come in yet (PR #{number}) is replaced by the one you asked for "
            "now. An answer that only the earlier one changed is not changed: give it in the form again if you "
            "still want it.")
ANEW = ("Your assistant's instructions are being built anew from your settings: when the new text comes, it may "
        "differ in more places than your answers did.")


def nothing_to_change(repo: Path, api, owner: str, platform: str, field: str, run_url: str) -> list[str]:
    """Her answers are her settings already. Not an error — she may have run the form to find her
    instructions again: a PR an earlier run left open is closed, and she is pointed to the page that
    holds her field as it stands (opened now, if she never was given this text). A PR that could
    not be closed, or not be looked for, is said as that — never as «nothing was changed» alone."""
    state, number = _close_own(api, SETTINGS_BRANCH)
    first = "SETTINGS these are her settings already: nothing to change"
    word = {"closed": WITHDRAWN, "open": STILL_OPEN, "unknown": NOT_LOOKED}.get(state, "").format(number=number)
    lines = [first + {"closed": f" (PR #{number}, left open by an earlier run, is closed)",
                      "open": f" — but PR #{number}, an earlier change still open, could not be closed: it is not "
                              f"withdrawn and may still come in",
                      "unknown": " — whether an earlier change of hers is still open could not be seen: if one is, "
                                 "it is not withdrawn"}.get(state, "")]
    try:
        done = field_issue.sync(api, _entry(repo, platform, field), owner, "", run_url)
        if done:
            return lines + [f"FIELD {line}" for line in done] + ([note(api, owner, word, run_url)] if word else [])
        _open, last = field_issue._pages_of(api)
        to = field_issue.names(owner, "")
        api.post(f"repos/{api.repository}/issues/{last['number']}/comments", {"body": (
            f"{to + ' — you' if to else 'You'} ran the form, and its answers are your settings already: nothing was "
            f"changed.{' ' + word if word else ''} Your assistant's instructions, as they stand, are the text on this "
            f"page.\n\nRun: {run_url}\n")})
        return lines + [f"FIELD said in #{last['number']}: her instructions as they stand are there"]
    except Exception as exc:    # noqa: BLE001 — nothing was to change; pointing her to the page is a kindness
        return lines + [f"FIELD could not point her to her page ({type(exc).__name__}: {exc})"]


GOOD = {"platform": "chatgpt", "language": "en", "country": "US", "timezone": "Europe/Berlin", "name": "Anna",
        "gender": "female", "work_languages": ""}      # answers setup takes, to tell whose answer it refuses
LEAD = "Your settings changed, and your assistant's instructions with them."
LEAD_ANEW = ("Your assistant's instructions were built anew from your settings: the text may have changed in more "
             "places than your answers did.")


def _built(repo: Path, answers: dict) -> dict[str, str] | None:
    """Her two files as `setup` builds them from these answers; None when it does not — one is
    missing, or `setup` refuses one."""
    try:
        return setup.prepare(repo, **{"work_languages": "", **answers})[0]
    except (setup.SetupError, TypeError):
        return None


def _beside(codes: list[str]) -> str:
    """A language code that is none of these — whatever a hand put on main, and however many: a
    thousand to choose from, and one to fall back on, so that telling her what is wrong with her
    config never fails on that config itself (found by the second reader, 13.11.2026)."""
    taken = set(codes)
    return next((code for code in map("".join, itertools.product("qrstuvwxyz", repeat=3)) if code not in taken), "zxx")


def _refusal(repo: Path, answers: dict) -> Exception | None:
    """Why `setup` does not build from these answers, if it does not."""
    try:
        setup.prepare(repo, **{"work_languages": "", **answers})
    except (setup.SetupError, TypeError) as exc:
        return exc
    return None


WITH_A_BOT = ("this installation runs with a bot: her settings change through the bot and its record of her yes, "
              "not through this form")


def _with_a_bot(config: dict) -> bool:
    return limits_check.writer(config) != "chat" or limits_check.has_bot(config)


def change(repo: Path, main: str, api, origin: Callable[[str], str], token: str, owner: str,
           choices: dict[str, str], run_url: str, sleep: Callable[[float], None]) -> list[str]:
    """Her settings changed by the form «settings» on an installed repository (DESIGN_PHASE3 §6): an
    answer left empty, or left on «keep as it is», is the one her config on main holds."""
    on_main = limits_check.config_at(repo, main)
    if not on_main:
        raise setup.SetupError("her config on main cannot be read, so the form cannot tell what building it again "
                               "would change. The file has to be put right first, by someone who can edit it")
    if _with_a_bot(on_main):
        raise setup.SetupError(WITH_A_BOT)
    asked = {key: value for key, value in choices.items() if value.lower() not in ("", KEEP)}
    held_now = setup.answers_in(on_main)
    current = {key: ",".join(value) if key == "work_languages" else value for key, value in held_now.items()}
    missing = [key for key in ANSWERS if key not in asked and key not in current]
    person = on_main.get("person")
    if isinstance(person, dict) and "work_languages" in person and not {"work_languages"} & {*current, *asked}:
        missing.append("work_languages")            # held, but not as a list of codes: not dropped without a word
    if missing:
        raise setup.SetupError(f"her config on main does not hold {', '.join(missing)}, so «keep as it is» has nothing "
                               f"to keep there: give {'that answer' if len(missing) == 1 else 'those answers'} in "
                               f"this form too")
    choices = {"work_languages": "", **current, **asked}
    if choices["work_languages"].lower() == NONE:
        choices["work_languages"] = ""
    try:
        files, field, limit, _text = setup.prepare(repo, **choices)
    except setup.SetupError as exc:
        # Whose answer is refused is told by the answer, not by the words of the refusal: each one
        # alone among answers setup takes — her own first, then those she left on «keep».
        for key in ANSWERS:
            mine = _refusal(repo, {**GOOD, key: asked[key]}) if key in asked else None
            if isinstance(mine, setup.SetupError):
                raise mine from None
        # Her work languages among them, beside a language that is none of them: «her own language is
        # hers already» is then never the probe's own doing.
        beside = _beside(current.get("work_languages", "").split(","))
        for key in (*ANSWERS, "work_languages"):
            kept = (_refusal(repo, {**GOOD, "language": beside, key: current[key]})
                    if key not in asked and key in current else None)
            if isinstance(kept, setup.SetupError):
                raise setup.SetupError(f"{kept} — that is what her config on main holds, not an answer of this run: "
                                       f"give that answer in this form to put it right") from None
        raise exc
    problems = config_check.settings_findings(on_main, tomllib.loads(files[setup.CONFIG]))
    if problems:
        raise setup.SetupError(f"the form cannot build her settings again here — {problems[0].message}. Here they "
                               f"change only in the file itself, by someone who can edit it")
    if all(gitio.file_at(repo, main, path) == content.encode("utf-8") for path, content in files.items()):
        return nothing_to_change(repo, api, owner, choices["platform"], field, run_url)
    # Main's two files not what `setup` builds from the answers its config holds — another release's
    # wording, a line or a value of somebody's hand: this run builds them anew, and says so.
    as_built = _built(repo, current)
    anew = as_built is None or any(gitio.file_at(repo, main, path) != text.encode("utf-8")
                                   for path, text in as_built.items())
    number, commit, replaced = _push_and_open(repo, main, api, origin, token, files, SETTINGS_BRANCH,
                                              SETTINGS_TITLE, SETTINGS_BODY, sleep)
    said = _gate_said(api, commit)                          # an earlier run's word on this same commit, if any
    before = UNREAD if said is UNREAD else (said or {}).get("id")
    update.start_gate(api, number)
    lines = [f"SETTINGS pr=#{number} commit={commit}; the gate is started"]
    plain = lambda value: " ".join(str(value).split())[:60] or "none"        # a value of main's, on one line
    changes = [f"{key} {plain(current.get(key, ''))} → {plain(choices[key])}" for key in (*ANSWERS, "work_languages")
               if choices[key] != current.get(key, "")]
    if changes:
        lines.append("SETTINGS her answers, as they were → as they are now: " + "; ".join(changes))
    if anew:
        lines.append("SETTINGS her two files on main were not what setup builds from her settings: this run builds "
                     "them anew, so more of her instructions may change than her answers did")
    if replaced:
        lines.append(f"SETTINGS pr=#{number} held an earlier change that had not come in: this run's answers replace "
                     f"it — what only that one changed stays as main holds it; to have it, give that answer again")
    told = " ".join(([REPLACED.format(number=number)] if replaced else []) + ([ANEW] if anew else []))
    if told:                # on the page she has now: the next one may be the night's, or another AI's
        lines.append(note(api, owner, told, run_url))
    later = (f"when main holds her new settings and passes its checks, the night's run hands her the new "
             f"instructions in an Issue «{ISSUE_TITLE}»")
    try:
        outcome, where = landed(api, number, commit, main, sleep, before=before)
    except Exception as exc:    # noqa: BLE001 — the gate is started: what became of the PR is the night's to see
        return lines + [f"SETTINGS could not see what became of pr=#{number} ({type(exc).__name__}: {exc}): {later}"]
    if outcome == "refused":
        raise NotLanded(lines, f"her settings did not change: the checks did not let PR #{number} in"
                               f"{' — ' + where if where else ''}. Their run says what is wrong; when it is put "
                               f"right, run the form again")
    if outcome == "closed":
        raise NotLanded(lines, f"her settings did not change: PR #{number} was closed without being merged")
    if outcome == "other":
        raise NotLanded(lines, f"PR #{number} was merged with another head than this run pushed: main may hold "
                               f"other answers than hers in any of the seven — run the form again and give every "
                               f"answer, not only the one that changes")
    if outcome == "waiting":
        return lines + [f"SETTINGS pr=#{number} is not merged yet: {later}. If none comes by the next day, look "
                        f"under Actions — the run of the checks or of the watch says why — then run the form again"]
    if outcome == "moved":
        return lines + [f"SETTINGS pr=#{number} is merged; that main is this merge and nothing more could not be "
                        f"shown, so her page is left to the night: {later}"]
    # main is this PR's merge and nothing more, let in by the gate: her new field goes to her now.
    try:
        lead = LEAD_ANEW if anew else LEAD
        done = field_issue.sync(api, _entry(repo, choices["platform"], field), owner, "", run_url, lead=lead)
    except Exception as exc:    # noqa: BLE001 — her settings are on main: the page is the night's to open
        return lines + [f"SETTINGS pr=#{number} is merged; her page could not be opened now "
                        f"({type(exc).__name__}: {exc}): {later}"]
    return lines + [f"SETTINGS pr=#{number} is merged: the field, {limits_check.length(field)} of {limit}",
                    *(f"FIELD {line}" for line in done)]


def run(repo: Path, main: str, api, origin: Callable[[str], str], token: str, actors: tuple[str, str],
        owner: str, choices: dict[str, str], sleep: Callable[[float], None] = time.sleep,
        run_url: str = "", keep: bool = False) -> list[str]:
    """One run; the lines it prints. Raises SetupError on a refusal, anything else on a failure.
    `actors` — who started the first attempt of this run and who started this attempt."""
    for actor in actors:
        if not owner or not actor or actor.lower() != owner.lower():
            raise setup.SetupError(
                f"setup is run by the repository's owner. This repository belongs to {owner or 'nobody'}, and "
                f"{actor or 'nobody'} pressed the button. If {owner or 'the owner'} is an organisation: make "
                f"the repository from the template under your own account — «Owner» in «Use this template»")
    live = api.get(f"repos/{api.repository}/git/ref/heads/main")["object"]["sha"]
    if live != main:
        # A re-run keeps the commit of its first attempt: after the installation landed it would
        # read the old main as not installed, push again and tell her of a PR that changes nothing.
        raise setup.SetupError("main changed after this run was started — an earlier run may have just landed — and "
                               "this run changed nothing: start the form again from Actions — «Run workflow», not "
                               "«Re-run»")
    if paths_check.installed(repo, main):
        if gitio.file_at(repo, main, setup.CONFIG) is None or gitio.file_at(repo, main, setup.PART) is None:
            raise setup.SetupError("this repository was installed once, but it does not hold her settings now, so "
                                   "there is nothing to change. If that was a stray file and never an installation: "
                                   "make a new repository from the template")
        if not keep:
            on_main = limits_check.config_at(repo, main)
            if on_main and _with_a_bot(on_main):         # a config that cannot be read is `settings`' to say
                raise setup.SetupError(WITH_A_BOT)
            # The lists of the form `setup` stand on a real answer: one left untouched would change her AI unasked.
            raise setup.SetupError("this repository is installed already, and setup installs only once. To change "
                                   "your settings run the workflow «settings» instead: every answer there already "
                                   "stands on «keep as it is», so you give only the one that changes")
        return change(repo, main, api, origin, token, owner, choices, run_url, sleep)
    if keep:
        raise setup.SetupError("this repository is not installed yet: run the workflow «setup» first — «settings» "
                               "changes what setup wrote")
    files, field, limit, text = setup.prepare(repo, **choices)
    number, commit, _again = _push_and_open(repo, main, api, origin, token, files, BRANCH, PR_TITLE, PR_BODY, sleep)
    # Her instructions first: once the gate is started the PR lands within minutes, and a run
    # after that is refused as installed — she must not be left without the field.
    title = f"{ISSUE_TITLE} — {setup.platforms()[choices['platform']]['title']}"
    issue_number, what = open_issue(api, title, text, number, field)
    update.start_gate(api, number)
    return [f"ISSUE #{issue_number} {what}: the field, {limits_check.length(field)} of {limit}",
            f"SETUP pr=#{number} commit={commit}; the gate is started"]


def _language(code: str) -> str:
    """A language code in its usual letter case, however it was typed: ru, pt-BR, zh-Hant-TW."""
    first, *rest = code.split("-")
    return "-".join([first.lower()] + [part.title() if len(part) == 4 and part.isalpha() else
                                        part.upper() if len(part) == 2 and part.isalpha() else part.lower()
                                        for part in rest])


def _timezone(name: str) -> str:
    """A time zone under its own name, whatever the letter case it was typed in, a space for its
    underscore included; what is no zone stays as typed, for `setup` to refuse."""
    wanted = name.replace(" ", "_").casefold()
    return next((zone for zone in sorted(available_timezones()) if zone.casefold() == wanted), name)


def as_typed(choices: dict[str, str]) -> dict[str, str]:
    """Her answers with the letter case put right, as a phone's keyboard gets it wrong: «Ru» for ru —
    a field starts with a capital there —, «il», «europe/berlin». Only the case: what is then no
    code and no zone is refused as before, and «keep as it is» and «none» pass through."""
    work = ",".join(_language(part.strip()) for part in choices["work_languages"].split(","))
    return {**choices, "language": _language(choices["language"]), "country": choices["country"].upper(),
            "timezone": _timezone(choices["timezone"]), "work_languages": work}


def tell(lines: list[str]) -> None:
    """What the run did, on the run's own page too — GitHub's job summary: whoever comes there from
    GitHub's notice of a failed run reads why without opening a step's log. In a code block, its
    lines folded at 72 characters: an answer or a value of main in a line is shown, never read as
    markup or a mention. How that reads on a phone the test installation shows (DESIGN_PHASE3 §8).
    Never a reason to fail."""
    path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not path:
        return
    try:
        folded = "\n\n".join(textwrap.fill(line, 72, break_long_words=False, break_on_hyphens=False) for line in lines)
        with open(path, "a", encoding="utf-8") as page:
            page.write(f"{setup.fenced(folded)}\n")
    except OSError:
        pass


def main(argv: list[str] | None = None, api=None, origin: Callable[[str], str] = update.github_url,
         sleep: Callable[[float], None] = time.sleep) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--main", required=True, help="main, as the workflow checked it out")
    ap.add_argument("--run-url", required=True)
    ap.add_argument("--keep", action="store_true",
                    help="the form «settings»: an answer left empty or on «keep as it is» stays as main holds it")
    for name in ANSWERS:
        ap.add_argument(f"--{name}", default="")
    ap.add_argument("--work-languages", default="")
    args = ap.parse_args(argv)
    token = os.environ.get("GH_TOKEN", "")
    form = "settings" if args.keep else "setup"
    choices = as_typed({"platform": args.platform.strip(), "language": args.language.strip(),
                        "country": args.country.strip(), "timezone": args.timezone.strip(),
                        "name": args.name.strip(), "gender": args.gender.strip(),
                        "work_languages": args.work_languages.strip()})
    code, said = 1, True
    try:
        if choices["work_languages"] == "no":
            raise setup.SetupError("«no» is the code of Norwegian: for no other language write «none»" +
                                   ("" if args.keep else " or leave the field empty") + "; for Norwegian write nb or nn")
        missing = [name for name in ANSWERS if not choices[name]]
        if missing and not args.keep:
            raise setup.SetupError(f"the form came without {', '.join(missing)}: setup needs every answer but the "
                                   f"work languages")
        if api is None:
            api = GitHub(os.environ.get("GITHUB_REPOSITORY", ""), token)
        actors = (os.environ.get("GITHUB_ACTOR", ""), os.environ.get("GITHUB_TRIGGERING_ACTOR", ""))
        lines = run(args.repo, args.main, api, origin, token, actors,
                    os.environ.get("GITHUB_REPOSITORY_OWNER", ""), choices, sleep, args.run_url, args.keep)
        code = 0
    except setup.SetupError as exc:
        lines, said = [f"ERROR {form} refused: {exc}"], False
    except NotLanded as exc:
        lines = [*exc.lines, f"ERROR {exc}"]
    except Exception as exc:    # noqa: BLE001 — anything is a loud ERROR line, never a traceback (P5)
        reason = getattr(exc, "reason", "")
        lines, said = [f"ERROR {form} could not run: {type(exc).__name__}: {exc}"
                       + (f" — {reason}" if reason else "")], False
    for line in lines:
        print(line)
    if said:
        print(f"Run: {args.run_url}")
    tell(lines)
    return code


if __name__ == "__main__":
    sys.exit(main())

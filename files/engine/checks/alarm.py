"""Alarms: one Issue per check, for the repository owner and the trusted contact (docs/DESIGN.md §6).

An alarm does not pass through the bot. It is an Issue opened by the workflow
(github-actions[bot]) that names the owner and the trusted contact, so GitHub
notifies both. Runs after every check of main — in the gate and nightly — and after
every run of the Notion copy (`--what backup`: N3, N6 and the copy's own error title).
A check the report does not mention was not judged by that run and its Issue is left
alone: the copy's run says nothing about N1, the gate's run nothing about N3. One open
Issue per check:

- findings and no open Issue → a new Issue, unless every finding was already listed in
  an Issue that someone closed: closing is «known, not fixing», and the same findings
  do not come back (the closer does not matter — the bot too,
  that price is taken); a new finding does come back;
- findings that differ from the last ones posted → a comment with the new list;
- the same findings → nothing: one alarm, not one a run;
- no findings and an open N5 or ERROR Issue → a closing comment, the Issue is closed;
- no findings and an open N1 Issue → one comment that the run was clean; the Issue
  stays open until someone closes it. A change that bypassed the gate stays on main
  after N1's window has passed, and the open Issue is what holds «there was one»;
- a run with an error in a check closes nothing of that check: it proved nothing clean;
- a check the run skipped (a stale run; N5 on a PR that records an installation) is
  left alone: nothing opened, nothing closed.

What counts as «listed»: the workflow marks every finding it posts with a hash of that
line, in the Issue body and in its own comments; closing acknowledges the findings of the
Issue's last list — the latest list the workflow wrote, the body or a comment; a «Clean …»
comment is an empty list — because that is what was open when someone closed it. A run in
which a part of a check failed posts no changed list for that check: its list is partial,
and a line the failed part could not judge has not left it. A line that left the list before
the close was not «known» then: an installation's first alarm listed «main does not
require gate» until the protection was set, and closing it later must not silence that
line for good. A collaborator — the bot's token is one —
can edit those texts and so «acknowledge» a finding before it exists; this is taken
as a price rather than more machinery. An Issue the
workflow closed itself («Clean») counts for nothing: that close meant «clean», not
«known». A closed error Issue counts only until the next run: an error that is still
there opens a new Issue. An Issue anybody else opens under the same
title is ignored.

The bot's to-do after a release (N7, `behind.py`) is kept here too, but it is not an alarm:
one Issue «To-do for the bot: …», no one named in it, its body always the whole current list
(the bot reads the body); a body that is not the list — the list changed, or somebody edited
it — is written again, an empty list closes the Issue, and a run that left the list out of
its report — the copy's run, a record PR, a stale run, a record that does not parse — or
that had an error in the checks leaves it alone. It never turns the run red, not even when GitHub refuses to keep it.

The field's Issue of an installation without a bot (`field_issue.py`) is kept here as well, and
is no alarm either: a run whose report carries the field hands it to her when it is not the one
she was given; a run that left it out, or that had an error in the checks, leaves it alone. While
N1 lists a line of the gate's own checks on main (C1 to C6), nothing is handed over — the text
is one no check would let in — and an open N1 alarm gets one comment saying what is wrong; nobody
has to close an alarm to get a field. An installation with a bot is never touched, whatever the
report says.

The exit code says whether the checks found something not acknowledged — the gate goes
red on it.

    python -m engine.checks.alarm --repo . --main SHA --report report.json --run-url URL \\
        [--what backup] [--error "what failed after the report"]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from . import behind, config_check, field_issue, limits_check
from .github import ApiError, GitHub
from .integrity import BOT

CHECK_TITLES = {
    "N1": "Alarm N1: main is not what the gate let in",
    "N2": "Alarm N2: the Notion copy stopped or its history was rewritten",
    "N3": "Alarm N3: the Notion copy shrank",
    "N4": "Alarm N4: the bot's daily routine is silent, or its token grew",
    "N5": "Alarm N5: an instruction field or a routine is not reinstalled",
    "N6": "Alarm N6: a card number was typed into Notion",
}
ERROR_TITLES = {
    "checks": "Alarm: the checks of main could not run",
    "backup": "Alarm: the Notion copy could not run",
}
ABOUT = {"checks": "the checks of main", "backup": "the Notion copy"}     # who found it, in the Issue text
TITLES = {**CHECK_TITLES, "ERROR": ERROR_TITLES["checks"]}
PEOPLE_CLOSE = frozenset({"N1"})        # never closed by the machine (see above)


def titles_for(what: str) -> dict[str, str]:
    """The Issue titles one run may open, update or close: the checks' and its own error."""
    return {**CHECK_TITLES, "ERROR": ERROR_TITLES[what]}
TREE_LINE_RE = re.compile(r"C[1-7] ")           # an N1 line of the gate's checks on main itself (integrity, «Tree»)
DIGEST_RE = re.compile(r"<!-- alarm-digest: ([0-9a-f]{64}) -->")
LINE_RE = re.compile(r"<!-- alarm-line: ([0-9a-f]{64}) -->")
# The machine's closing comment before it carried a digest — the only form it ever had: an
# empty list too, so that an old Issue someone reopens and closes acknowledges nothing.
LEGACY_CLEAN_CLOSE_RE = re.compile(r"Clean in \S+\. Closing\.")


def digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def line_hash(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _names(owner: str, contact: str) -> str:
    people: dict[str, str] = {}
    for name in (contact, owner):
        if name:
            people.setdefault(name.lower(), name)               # GitHub logins ignore case
    return " ".join(f"@{n}" for n in people.values())


def _block(lines: list[str]) -> str:
    # The findings go in a code block: a branch name or a path may carry «@someone», a
    # fence or a marker, and an alarm must not ping strangers, render as something else
    # or pass for the markers below.
    return "\n".join(line.replace("`", "'").replace("<!--", "<!- -") for line in lines)


def _markers(lines: list[str]) -> str:
    marks = "\n".join(f"<!-- alarm-line: {line_hash(line)} -->" for line in lines)
    return f"{marks}\n<!-- alarm-digest: {digest(lines)} -->\n"


def _text(lines: list[str], owner: str, contact: str, run_url: str, about: str = ABOUT["checks"]) -> str:
    return (f"{_names(owner, contact)} — {about} found this.\n\n"
            f"```text\n{_block(lines)}\n```\n\nRun: {run_url}\n\n{_markers(lines)}")


def _clean_text(run_url: str) -> str:
    return (f"Clean in {run_url}: nothing found. This alarm stays open until someone closes it — "
            f"the machine never closes an N1 alarm; closing it says «known, not fixing».\n\n"
            f"<!-- alarm-digest: {digest([])} -->\n")


def _texts(api, issue: dict) -> list[str]:
    """The Issue body and every comment the workflow wrote."""
    return [issue.get("body") or ""] + [
        c.get("body") or "" for c in api.pages(f"repos/{api.repository}/issues/{issue['number']}/comments")
        if (c.get("user") or {}).get("login") == BOT]


def _last_digest(api, issue: dict) -> str | None:
    """The set digest the workflow wrote last — for «did the findings change»; an edit
    here costs one extra comment at most."""
    last = None
    for text in _texts(api, issue):
        found = DIGEST_RE.findall(text)
        if found:
            last = found[-1]        # the marker is the last thing the workflow writes
        elif LEGACY_CLEAN_CLOSE_RE.fullmatch(text.strip()):
            last = digest([])       # an old «Clean … Closing.»: an empty list, as `acknowledged` reads it
    return last


def _own(issue: dict) -> bool:
    return "pull_request" not in issue and (issue.get("user") or {}).get("login") == BOT


def acknowledged(api, title: str) -> set[str]:
    """Hashes of the findings in the last list of a closed Issue under this title that the
    workflow did not close itself: «known, not fixing», whoever closed it."""
    found: set[str] = set()
    for issue in api.pages(f"repos/{api.repository}/issues",
                           {"state": "closed", "creator": BOT, "sort": "updated", "direction": "desc"}):
        if not _own(issue) or issue.get("title") != title:
            continue
        if ((issue.get("closed_by") or {}).get("login") or "") == BOT:
            continue                # closed by the machine: that meant «clean», not «known»
        last: list[str] = []
        for text in _texts(api, issue):
            if DIGEST_RE.search(text):          # every list the workflow writes ends with its digest
                last = LINE_RE.findall(text)
            elif LEGACY_CLEAN_CLOSE_RE.fullmatch(text.strip()):
                last = []                       # an old «Clean … Closing.»: an empty list
        found.update(last)
    return found


def _concerns(errors: list[str], check: str) -> bool:
    """Whether an error line stops the machine from judging this check."""
    return any(e.startswith(check) or e.startswith("the checks could not start")
               or e.startswith("the copy could not be made") for e in errors)


def sync(api, findings: dict[str, list[str]], owner: str, contact: str, run_url: str,
         skipped: set[str] = frozenset(), titles: dict[str, str] | None = None,
         about: str = ABOUT["checks"]) -> tuple[list[str], bool]:
    """Bring the alarm Issues in line with findings {check: [lines]}; return what was
    done and whether something found is not acknowledged (the gate goes red on it).
    Only the Issues under `titles` are touched — the other run's error Issue is not
    this run's to close."""
    titles = TITLES if titles is None else titles
    done: list[str] = []
    red = False
    errors = findings.get("ERROR", [])
    open_issues = {}
    for issue in api.pages(f"repos/{api.repository}/issues", {"state": "open", "creator": BOT}):
        if _own(issue) and issue.get("title") in titles.values():
            open_issues.setdefault(issue["title"], issue)
    for check, title in titles.items():
        if check in skipped:
            continue                # not judged this run: nothing opened, nothing closed
        lines = findings.get(check, [])
        issue = open_issues.get(title)
        if lines and issue is None:
            # A closed error Issue silences nothing beyond its run.
            acked = acknowledged(api, title) if check != "ERROR" else set()
            if all(line_hash(line) in acked for line in lines):
                done.append(f"acknowledged {check}")
                continue
            red = True
            made = api.post(f"repos/{api.repository}/issues",
                            {"title": title, "body": _text(lines, owner, contact, run_url, about)})
            done.append(f"opened #{made['number']} {check}")
        elif lines:
            red = True
            if check != "ERROR" and _concerns(errors, check):
                continue            # a part of this check failed: a partial list must not stand as the last one
            if _last_digest(api, issue) != digest(lines):
                api.post(f"repos/{api.repository}/issues/{issue['number']}/comments",
                         {"body": "The findings changed:\n\n" + _text(lines, owner, contact, run_url, about)})
                done.append(f"updated #{issue['number']} {check}")
        elif issue is not None and check != "ERROR" and _concerns(errors, check):
            continue                # an open alarm, and this run proved nothing about it
        elif issue is not None:
            if check in PEOPLE_CLOSE:
                if _last_digest(api, issue) != digest([]):
                    api.post(f"repos/{api.repository}/issues/{issue['number']}/comments",
                             {"body": _clean_text(run_url)})
                    done.append(f"clean #{issue['number']} {check}, left open")
            else:
                api.post(f"repos/{api.repository}/issues/{issue['number']}/comments",
                         {"body": f"Clean in {run_url}. Closing.\n\n<!-- alarm-digest: {digest([])} -->\n"})
                api.patch(f"repos/{api.repository}/issues/{issue['number']}",
                          {"state": "closed", "state_reason": "completed"})
                done.append(f"closed #{issue['number']} {check}")
    return done, red


RUN_LINE_RE = re.compile(r"^Run: \S+$", re.M)


def _todo_text(body: str) -> str:
    """A to-do body as compared: its Run line only says which run wrote it."""
    return RUN_LINE_RE.sub("Run:", body.replace("\r\n", "\n")).strip()


def todo_sync(api, lines: list[str], run_url: str) -> list[str]:
    """Bring the bot's to-do Issue in line with the list: open it, write its body again when
    it is not the list — the list changed, or somebody edited the body —, close it when
    nothing is left. Only Issues the workflow opened under the title count; of two (two runs
    at once) the older stays and the other is closed. The bot closing it by hand only means
    a new one opens while work is left."""
    own = sorted((i for i in api.pages(f"repos/{api.repository}/issues", {"state": "open", "creator": BOT})
                  if _own(i) and i.get("title") == behind.TITLE), key=lambda i: i["number"])
    done: list[str] = []
    for extra in own[1:]:
        api.post(f"repos/{api.repository}/issues/{extra['number']}/comments",
                 {"body": f"A second list; #{own[0]['number']} is the one. Closing."})
        api.patch(f"repos/{api.repository}/issues/{extra['number']}", {"state": "closed", "state_reason": "not_planned"})
        done.append(f"closed #{extra['number']} todo, a second one")
    open_issue = own[0] if own else None
    text = behind.body(lines, run_url)
    if lines and open_issue is None:
        made = api.post(f"repos/{api.repository}/issues", {"title": behind.TITLE, "body": text})
        return done + [f"opened #{made['number']} todo"]
    if lines:
        if _todo_text(open_issue.get("body") or "") != _todo_text(text):
            api.patch(f"repos/{api.repository}/issues/{open_issue['number']}", {"body": text})
            return done + [f"updated #{open_issue['number']} todo"]
        return done
    if open_issue is not None:
        api.post(f"repos/{api.repository}/issues/{open_issue['number']}/comments",
                 {"body": f"Nothing left in {run_url}. Closing."})
        api.patch(f"repos/{api.repository}/issues/{open_issue['number']}",
                  {"state": "closed", "state_reason": "completed"})
        return done + [f"closed #{open_issue['number']} todo"]
    return done


def todo_from_report(path: Path) -> list[str] | None:
    """The run's to-do list, or None when the run did not judge it (or the report is unreadable:
    from_report raises that as an alarm)."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _lines(report.get("todo")) if isinstance(report, dict) else None


def open_alarm(api, title: str) -> int | None:
    """The number of the open alarm Issue under this title, if the workflow has one."""
    for issue in api.pages(f"repos/{api.repository}/issues", {"state": "open", "creator": BOT}):
        if _own(issue) and issue.get("title") == title:
            return issue["number"]
    return None


def field_from_report(path: Path) -> dict | None:
    """The run's field for her Issue, or None when the run did not judge it."""
    try:
        return field_issue.from_report(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _lines(value: object) -> list[str] | None:
    return [str(x) for x in value] if isinstance(value, list) else None


def from_report(path: Path) -> tuple[dict[str, list[str]], set[str]]:
    """({check: [lines]}, skipped checks) from a run's report. A missing, unreadable or
    malformed report is itself an alarm, and judges nothing: the shape of a run is an
    object with list-valued `error` and `skipped` and at least one check's list (a
    bare `{}` must not read as «everything skipped, all clean»). A check the
    report does not mention is skipped: that run did not judge it."""
    broken = {"ERROR": ["no readable report from the checks: %s"]}, set(CHECK_TITLES)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ERROR": [broken[0]["ERROR"][0] % type(exc).__name__]}, broken[1]
    if not isinstance(report, dict):
        return {"ERROR": [broken[0]["ERROR"][0] % "not an object"]}, broken[1]
    error, skipped_raw = _lines(report.get("error")), _lines(report.get("skipped"))
    checks = {k: _lines(report.get(k)) for k in CHECK_TITLES if k in report}
    if error is None or skipped_raw is None or not checks or any(v is None for v in checks.values()):
        return {"ERROR": [broken[0]["ERROR"][0] % "not the shape of a run"]}, broken[1]
    findings: dict[str, list[str]] = {k: v for k, v in checks.items() if v is not None}
    if error:
        findings["ERROR"] = error
    skipped = set(skipped_raw) & set(CHECK_TITLES)
    skipped |= set(CHECK_TITLES) - set(findings)
    return findings, skipped


def main(argv: list[str] | None = None, api=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--main", required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--run-url", required=True)
    ap.add_argument("--what", choices=sorted(ERROR_TITLES), default="checks",
                    help="whose run this is: the checks of main, or the Notion copy")
    ap.add_argument("--error", action="append", default=[],
                    help="a failure the workflow saw after the report was written (a step that failed)")
    args = ap.parse_args(argv)

    findings, skipped = from_report(args.report)
    if args.error:
        findings["ERROR"] = findings.get("ERROR", []) + list(args.error)
    try:
        config = config_check.read(args.repo, args.main)
    except Exception as exc:    # noqa: BLE001 — no checkout: the alarm still goes to the owner
        findings.setdefault("ERROR", []).append(f"the config could not be read: {type(exc).__name__}")
        config = {}
    contact = str(config_check.protected_value(config, "alarm", "trusted_contact") or "")
    if not config_check.LOGIN_RE.fullmatch(contact):
        contact = ""                # never put an unchecked string into a mention
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    try:
        if api is None:
            api = GitHub(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", ""))
        done, red = sync(api, findings, owner, contact, args.run_url, skipped, titles_for(args.what), ABOUT[args.what])
        todo = todo_from_report(args.report)
        if todo is not None and not findings.get("ERROR"):   # an error in the run proves nothing about the list
            try:
                done += todo_sync(api, todo, args.run_url)
            except Exception as exc:    # noqa: BLE001 — the to-do is not an alarm: the next run keeps it
                done.append(f"TODO ERROR the to-do could not be kept: {type(exc).__name__}: {exc}")
        handed = field_from_report(args.report)
        solo = limits_check.writer(config) == "chat" and not limits_check.has_bot(config)   # main's own config, not the report's word
        if handed is not None and solo and not findings.get("ERROR"):  # as the to-do: an error proves nothing about main
            try:
                # What decides is main's content, not an alarm's state: while N1 lists a line of the
                # gate's own checks on main — an engine file that is not the release's, a field over
                # its limit — the text is one no check would let in. Closing the alarm changes
                # nothing here, and nobody has to close one to be handed a field.
                broken = [line for line in findings.get("N1", []) if TREE_LINE_RE.match(line)]
                live = api.get(f"repos/{api.repository}/git/ref/heads/main")["object"]["sha"]
                if live != args.main:
                    # main moved between the watch and this step: the run for the new main hands its
                    # field over, and this one must not put the older text on top of it.
                    done.append(f"FIELD left alone: main is at {live[:12]} now, this run read {args.main[:12]}")
                elif broken:
                    done += field_issue.hold(api, handed, open_alarm(api, CHECK_TITLES["N1"]), broken, args.run_url)
                else:
                    done += field_issue.sync(api, handed, owner, contact, args.run_url)
            except Exception as exc:    # noqa: BLE001 — not an alarm: the next run does what this one could not
                reason = getattr(exc, "reason", "")
                done.append(f"FIELD ERROR the field's Issue could not be kept: {type(exc).__name__}: {exc}"
                            + (f" — {reason}" if reason else ""))
        for line in done:
            print(line)
    except ApiError as exc:
        print(f"ERROR the alarm could not be raised: {exc}")
        return 2
    if red:
        print("ALARM open")
        return 1
    print("ALARM none")
    return 0


if __name__ == "__main__":
    sys.exit(main())

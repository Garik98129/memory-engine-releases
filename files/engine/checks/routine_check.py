"""N4 routine-alive: the bot's daily routine lives, and its token has not grown (docs/DESIGN.md §6).

The watch cannot see the AI platform. Once a day the bot's own routine
(engine/rules/routines.md, task 3) posts one comment in the open Issue titled
«routine marks»:

    ROUTINE-MARK <time, ISO 8601> scopes=<X-OAuth-Scopes of GET /user, as the header gives them>

N4 reads the newest mark by the person's account (`person.github_login`; any account but
the workflow's when the key is unset) and alarms when: there is no mark at all; the
newest is older than 36 h by GitHub's clock (the comment's created_at — the time in the
text is the bot's word); it names no scopes, or `unknown`; or the scopes hold `workflow`,
`admin:*` or `delete_repo` — more than the `repo` the token was given.

Which Issue: the one `alarm.marks_issue` names — a protected key installation sets, so a
collaborator who closes or renames the Issue silences nothing: a closed, renamed, missing
or moved Issue is an alarm. With the key unset (before installation) the oldest open Issue
titled «routine marks» is used, and none at all is a note, never a silent green; an
installation does not start before this check runs. N1 holds no copy of the
token; the scopes are the bot's own report, and a bot that lies about them is seen by
N1's branches part and the monthly audit, not here.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import config_check
from .integrity import BOT, _when
from .report import ALARM, Finding

TITLE = "routine marks"
MARK_RE = re.compile(r"^\s*ROUTINE-MARK\s+(\S+)\s+scopes=(\S*)\s*$", re.M)
WINDOW = timedelta(hours=36)
FORBIDDEN = frozenset({"workflow", "delete_repo"})
FORBIDDEN_PREFIX = "admin:"


def _alarm(message: str) -> Finding:
    return Finding("N4", message, ALARM)


def person_login(config: dict) -> str:
    """The person's GitHub login from the config, or «» when unset or not a login."""
    value = config.get("person", {}).get("github_login") if isinstance(config.get("person"), dict) else None
    return value if isinstance(value, str) and config_check.LOGIN_RE.fullmatch(value) else ""


def marks_issue(config: dict) -> int | None:
    value = config_check.protected_value(config, "alarm", "marks_issue")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def find_issue(api, number: int | None) -> tuple[dict | None, Finding | None]:
    """The marks Issue: by its number when the config names one — closed, renamed, gone or
    moved is an alarm (the routine looks for the exact title, task 3); otherwise the oldest
    open Issue titled «routine marks»."""
    if number is not None:
        issue = api.get(f"repos/{api.repository}/issues/{number}", allow_404=True)
        if not isinstance(issue, dict) or "pull_request" in issue:
            return None, _alarm(f"Issue #{number}, named by alarm.marks_issue for the bot's marks, is gone")
        if issue.get("state") != "open":
            return None, _alarm(f"Issue #{number} «{issue.get('title', '')}», where the bot's marks go, is closed")
        if issue.get("title") != TITLE:
            return None, _alarm(f"Issue #{number}, where the bot's marks go, is titled «{issue.get('title', '')}», not «{TITLE}»: "
                                f"the routine looks for that title (task 3) and will not find it")
        return issue, None
    found = [i for i in api.pages(f"repos/{api.repository}/issues", {"state": "open"})
             if isinstance(i, dict) and i.get("title") == TITLE and "pull_request" not in i]
    return (min(found, key=lambda i: i.get("number", 0)) if found else None), None


def marks(api, issue: dict, login: str) -> list[tuple[datetime, str, str]]:
    """(when, who, scopes) for every mark in the Issue by the account that may make one."""
    out = []
    for comment in api.pages(f"repos/{api.repository}/issues/{issue['number']}/comments"):
        if not isinstance(comment, dict):
            continue
        who = (comment.get("user") or {}).get("login") or ""
        if who == BOT or (login and who.lower() != login.lower()):
            continue
        hit = MARK_RE.search(comment.get("body") or "")
        stamp = comment.get("created_at")
        if hit is None or not isinstance(stamp, str):
            continue
        out.append((_when(stamp), who, hit.group(2)))
    return out


def check(api, config: dict, now: datetime) -> tuple[list[Finding], list[str]]:
    found: list[Finding] = []
    login = person_login(config)
    issue, problem = find_issue(api, marks_issue(config))
    if problem is not None:
        return [problem], []
    if issue is None:
        return found, ["N4 NOTE no open Issue «routine marks» and no alarm.marks_issue: the bot's daily mark "
                       "has nowhere to go — installation opens the Issue and names it"]
    seen = marks(api, issue, login)
    whose = f" by {login}" if login else ""
    if not seen:
        found.append(_alarm(f"no mark from the bot's routine{whose} in Issue #{issue['number']} «routine marks»"))
        return found, []
    when, who, raw = max(seen, key=lambda m: m[0])
    if when < now - WINDOW:
        found.append(_alarm(f"the bot's daily routine stopped: its last mark is from "
                            f"{when.strftime('%Y-%m-%dT%H:%M:%SZ')}, more than 36 h ago"))
    scopes = [s for s in raw.split(",") if s]
    if not scopes or scopes == ["unknown"]:
        found.append(_alarm(f"the bot's last mark names no token scopes (scopes={raw or 'empty'}): "
                            f"the routine could not read them, or the mark is not what task 3 asks for"))
    bad = sorted(s for s in scopes if s in FORBIDDEN or s.startswith(FORBIDDEN_PREFIX))
    if bad:
        found.append(_alarm(f"the bot's token has the scope(s) {', '.join(bad)} — more than repo (reported by {who})"))
    return found, []

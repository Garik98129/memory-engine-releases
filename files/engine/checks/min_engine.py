"""N7 without a bot: is the engine of this repository old for the rules its AI reads (docs/DESIGN.md §5, §6)?

An installation where the chat writes and no bot runs (`limits_check.fixed`) holds the one text
for everybody, and that text sends its AI to the rules on the branch `stable` of the public
repository of releases (`release.manifests` in main's `engine/defaults.toml`): the topic index
`rules-index.md` there, and the files of the release it names. The index names, too, the oldest
release of the engine those rules are written for («These rules are written for the engine of
release `vX.Y.Z` or newer…», from `min_engine` in `engine/rules/topics.toml`). A chat cannot see
the user's repository; this module can. The watch reads the index on `stable`, compares that
release with `user/engine.lock` on main, and the alarms keep one Issue for the user when the
engine is the older: not an alarm — the page never turns the run red, and nobody is mailed every
night (this check crashing is an error line of the run, as for any part of the watch).

In the ordinary course it never opens. The schedule moves `stable` to a release only once it is
seven days old (`engine.setup.stable`), the slowest channel takes a release seven days after it is
published (`lock_check.CHANNEL_DELAY`), and the weekly update brings it. So an engine still older
than the rules' minimum a week and a day after its channel took that release
(`GRACE`, from the minimum release's own manifest) is an update that stopped — a release that
changes `.github/` and waits for hands, an update PR the gate holds back, a workflow switched off.
The page says so in plain words and points at the update's own page, which is where it is put
right; the run's log line names the releases.

What it does not judge — never as «clean»:

- an installation with a bot, or one not installed yet: not this check's, and the watch does not
  call it there — the chat field links the rules at a tag the bot keeps (N5, N7's list for the
  bot), and `setup` has not written anything yet. The rest are said in the run's log:
- the repository of releases could not be read (the network, GitHub), or it has no branch
  `stable` yet, or no index there, or an index with no line this code can read — the Issue is
  left as it is: an unread `stable` proves neither «behind» nor «caught up»;
- an engine older than the minimum, but within its grace: the update may simply not have come
  round yet.

The index's words change with its release (`engine.setup.stable.INDEX_TEXTS`); `least` reads the
line in every text so far, and a test holds that to every one of them.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

from . import gitio, limits_check, lock_check
from .integrity import BOT

STABLE = "stable"
INDEX = "rules-index.md"
MANIFEST = "release-manifest.json"
DEFAULTS = "engine/defaults.toml"
# the public repository of releases, as engine/defaults.toml names it: owner/name
MANIFESTS_RE = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
LEAST_RE = re.compile(r"^These rules are written for the engine of release `(v[0-9]+\.[0-9]+\.[0-9]+)` or newer",
                      re.M)
RELEASE_RE = re.compile(r"\A# Topic rules — release (v[0-9]+\.[0-9]+\.[0-9]+)\n")
TIMEOUT = 60                    # seconds a git call to the repository of releases may take
GRACE = timedelta(days=8)       # a week of the weekly update, and a day for its gate
TITLE = "Your engine is older than the rules your AI reads"
MARK_RE = re.compile(r"<!-- rules-behind: (v[0-9]+\.[0-9]+\.[0-9]+) (v[0-9]+\.[0-9]+\.[0-9]+) -->")
CAUGHT_UP = "<!-- rules-caught-up -->"


def ORIGIN(repository: str) -> str:
    """Where the repository of releases is fetched from. The tests point it at their own bare
    repositories, or at nothing (F1: never the network)."""
    return f"https://github.com/{repository}.git"


class Unread(Exception):
    """The repository of releases, its branch `stable` or its index could not be read: nothing judged."""


def least(index: str) -> str | None:
    """The oldest release of the engine an index's rules are written for, or None when the index
    has no such line, or two."""
    found = LEAST_RE.findall(index)
    return found[0] if len(found) == 1 else None


def index_release(index: str) -> str | None:
    """The release an index is of, from its first line."""
    m = RELEASE_RE.match(index)
    return m.group(1) if m else None


def repository_of(repo: Path, main: str) -> str | None:
    """owner/name of the repository of releases, as main's `engine/defaults.toml` names it."""
    raw = gitio.file_at(repo, main, DEFAULTS)
    try:
        section = tomllib.loads(raw.decode("utf-8")).get("release") if raw is not None else None
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    value = section.get("manifests") if isinstance(section, dict) else None
    return value if isinstance(value, str) and MANIFESTS_RE.fullmatch(value) else None


def _said(stderr: bytes) -> str:
    """git's complaint on one line: a note is one line of the run's log, and a line break in it
    could pass for a line of its own."""
    return " ".join(stderr.decode(errors="replace").split())[:300]


def _run(store: Path, *args: str) -> subprocess.CompletedProcess:
    """A git call that reaches the network: bounded in time, and never asking for a password — a
    stalled GitHub must not hold the watch until its job's limit, which leaves no report and turns
    the run red (the update fetches the same repository on its own schedule)."""
    try:
        return subprocess.run(["git", "-C", str(store), *args], capture_output=True, timeout=TIMEOUT,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired as exc:
        raise Unread(f"git {args[0]} took more than {TIMEOUT} s") from exc


def _fetch(store: Path, url: str, *refspecs: str) -> None:
    """Commits of these refs, without their history, into a scratch repository; no token — the
    repository of releases is public."""
    proc = _run(store, "fetch", "--quiet", "--no-tags", "--depth", "1", url, *refspecs)
    if proc.returncode != 0:
        raise Unread(f"git fetch {url}: {_said(proc.stderr)}")


def _has_stable(store: Path, url: str) -> bool:
    proc = _run(store, "ls-remote", "--heads", url, f"refs/heads/{STABLE}")
    if proc.returncode != 0:
        raise Unread(f"git ls-remote {url}: {_said(proc.stderr)}")
    return bool(proc.stdout.strip())


def read_stable(store: Path, url: str) -> str | None:
    """The index on `stable`, or None when the repository has no such branch yet."""
    if not _has_stable(store, url):
        return None
    _fetch(store, url, f"+refs/heads/{STABLE}:refs/heads/{STABLE}")
    raw = gitio.file_at(store, f"refs/heads/{STABLE}", INDEX)
    if raw is None:
        raise Unread(f"{STABLE} has no {INDEX}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Unread(f"{INDEX} on {STABLE} is not UTF-8") from exc


def published(store: Path, url: str, tag: str) -> datetime:
    """When the release `tag` was published, from its own manifest."""
    _fetch(store, url, f"+refs/tags/{tag}:refs/tags/{tag}")
    raw = gitio.file_at(store, f"refs/tags/{tag}", MANIFEST)
    try:
        data = json.loads(raw.decode("utf-8")) if raw is not None else None
    except (UnicodeDecodeError, ValueError) as exc:
        raise Unread(f"the manifest at {tag} does not read") from exc
    when = lock_check.published_at(data) if isinstance(data, dict) and data.get("release") == tag else None
    if when is None:
        raise Unread(f"the manifest at {tag} has no time it was published")
    return when


TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")      # ASCII digits only: `\d` takes others too


def _installed(repo: Path, main: str) -> str | None:
    """The release main's lock names, or None when it names none this code can compare (N1 says so)."""
    try:
        value = lock_check.lock_release(repo, main)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) and TAG_RE.fullmatch(value) else None


def check(repo: Path, main: str, config: dict, now: datetime,
          url: str | None = None) -> tuple[dict | None, list[str]]:
    """(state for the alarms, notes for the run's log). The state: {"engine": the release main's
    lock names, "needs": the minimum the index on `stable` names, "of": the release that index is
    of, "late": whether the engine is older than that minimum and past its grace}. None — not
    judged: the page is left as it is, and a note says why. `url` replaces the repository's
    address on GitHub (the tests' bare repositories)."""
    if not limits_check.fixed(config):
        return None, []
    installed = _installed(repo, main)
    if installed is None:
        return None, ["N7 NOTE the engine is not compared with the rules: user/engine.lock names no release (N1)"]
    repository = repository_of(repo, main)
    if repository is None:
        return None, [f"N7 NOTE the engine is not compared with the rules: {DEFAULTS} names no repository of releases"]
    url = url or ORIGIN(repository)
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp)
        gitio.git(store, "init", "--quiet", "--bare")
        try:
            index = read_stable(store, url)
            if index is None:
                return None, [f"N7 NOTE {repository} has no branch {STABLE} yet: no rules to compare the engine with"]
            needs = least(index)
            if needs is None:
                return None, [f"N7 NOTE the index on {STABLE} names no minimal engine this code reads: "
                              f"the engine is not compared with the rules"]
            state = {"engine": installed, "needs": needs, "of": index_release(index) or "?", "late": False}
            if lock_check.release_key(installed) >= lock_check.release_key(needs):
                return state, [f"N7 NOTE the engine {installed} is not older than the rules on {STABLE} need ({needs})"]
            due = published(store, url, needs) + lock_check.CHANNEL_DELAY[lock_check.channel(repo, main)] + GRACE
        except (Unread, gitio.GitError) as exc:
            return None, [f"N7 NOTE the rules on {STABLE} could not be read, the engine is not compared: "
                          f"{' '.join(str(exc).split())[:400]}"]
    if now < due:
        return state, [f"N7 NOTE the engine {installed} is older than the rules on {STABLE} need ({needs}); its "
                       f"update has until {due:%Y-%m-%d} to come"]
    return {**state, "late": True}, []


def line(state: dict) -> str:
    """The run's log line for an engine past its grace."""
    return (f"the engine {state['engine']} is older than {state['needs']}, the oldest the rules on {STABLE} "
            f"(release {state['of']}) are written for")


def from_report(value: object) -> dict | None:
    """The state a report carries, held to its shape: tags of the shape `check` writes, a bool."""
    if not isinstance(value, dict) or set(value) != {"engine", "needs", "of", "late"}:
        return None
    tags = all(isinstance(value[k], str) and TAG_RE.fullmatch(value[k]) for k in ("engine", "needs"))
    of = isinstance(value["of"], str) and (value["of"] == "?" or TAG_RE.fullmatch(value["of"]))
    return dict(value) if tags and of and isinstance(value["late"], bool) else None


def body(state: dict, to: str, run_url: str) -> str:
    """The Issue's text, for the user: what it means, what to do, and nothing to paste."""
    lead = f"{to} — " if to else ""
    installed, needs = state["engine"], state["needs"]
    return (f"{lead}Your AI reads its rules from the public repository of releases, and those rules are "
            f"written for a newer engine than your repository has: yours is {installed}, they need {needs} or "
            f"newer. Your engine is updated every week by itself, so this means its update stopped.\n\n"
            f"What to do: look in this repository's Issues and pull requests for «Engine update» — a page "
            f"«Engine update … has to be applied by hand», or an update pull request that is not merged — and "
            f"follow it, or give it to whoever helps you with this repository. Until then your AI may follow a "
            f"rule your repository's checks do not hold yet. You do not need to paste or send anything to your "
            f"AI.\n\nThis page closes by itself once your engine has caught up. If you close it, no page says "
            f"this again for these same two releases, unless your engine catches up first and then falls behind "
            f"again.\n\n```text\n{line(state)}\n```\n\nRun: {run_url}\n\n"
            f"<!-- rules-behind: {installed} {needs} -->\n")


def _own(issue: dict) -> bool:
    return "pull_request" not in issue and (issue.get("user") or {}).get("login") == BOT and issue.get("title") == TITLE


def _mark(issue: dict) -> tuple[str, str] | None:
    m = MARK_RE.search(issue.get("body") or "")
    return (m.group(1), m.group(2)) if m else None


def _settled(issue: dict) -> bool:
    """A page the workflow closed itself, or one an engine caught up with after: it holds back nothing."""
    return CAUGHT_UP in (issue.get("body") or "")


def _key(tag: str) -> tuple[int, int, int]:
    return lock_check.release_key(tag)


def sync(api, state: dict, to: str, run_url: str) -> list[str]:
    """Keep the one Issue in line with the run's state:

    - the engine not older than the minimum → an open page closes, marked; a page the user closed
      before is marked too, so «known» ends with the catching up;
    - older but within its grace → an open page whose own minimum the engine has reached closes,
      marked (it would say what is no longer so); another one stays as it is, and nothing opens;
    - older past its grace → a page for these two releases opens, unless the newest page for them
      is one the user closed while the engine was behind — «known»; an open page for other releases
      is written again, with a comment, since an edited body notifies nobody.

    Only Issues the workflow opened under the title count; of two open (two runs at once) the older
    stays, the other is closed and marked."""
    done: list[str] = []
    issues = f"repos/{api.repository}/issues"
    mine = sorted((i for i in api.pages(issues, {"state": "all", "creator": BOT}) if _own(i)), key=lambda i: i["number"])
    opened = [i for i in mine if i.get("state") == "open"]

    def settle(issue: dict, comment: str, reason: str) -> None:
        """Close an open page with a comment, and mark it; a closed one is only marked — the user's
        reason for closing it stays as it was."""
        marked = (issue.get("body") or "") + ("" if _settled(issue) else CAUGHT_UP)
        if issue.get("state") == "open":
            api.post(f"{issues}/{issue['number']}/comments", {"body": comment})
            api.patch(f"{issues}/{issue['number']}", {"state": "closed", "state_reason": reason, "body": marked})
        else:
            api.patch(f"{issues}/{issue['number']}", {"body": marked})
        issue["state"], issue["body"] = "closed", marked

    for extra in opened[1:]:
        settle(extra, f"A second page; #{opened[0]['number']} is the one. Closing.", "not_planned")
        done.append(f"closed #{extra['number']} rules, a second one")
    current = opened[0] if opened else None
    engine, needs = state["engine"], state["needs"]
    caught = f"Your engine ({engine}) has caught up with the rules your AI reads ({run_url}). Closing."
    if _key(engine) >= _key(needs):
        for issue in mine:
            if issue.get("state") == "open" or not _settled(issue):       # one the user reopened closes too
                was_open = issue.get("state") == "open"
                settle(issue, caught, "completed")
                if was_open:
                    done.append(f"closed #{issue['number']} rules, caught up")
        return done
    if not state["late"]:
        marked = _mark(current) if current is not None else None
        if current is not None and marked is None:
            # Somebody edited the page's mark: what it asked for cannot be read, so it is not said
            # either way (Tolik's reading of #64).
            settle(current, f"This page's record of what it asked for was changed by hand and no longer reads, so "
                            f"it is closed. If your engine is still older than the rules your AI reads once its "
                            f"update has had its week, a new page says so ({run_url}). Closing.", "not_planned")
            done.append(f"closed #{current['number']} rules, its mark does not read")
        elif current is not None and _key(engine) >= _key(marked[1]):
            settle(current, f"Your engine ({engine}) has reached what this page asked for; the rules your AI reads "
                            f"now may need a newer one still, and if its update does not come, a new page says so "
                            f"({run_url}). Closing.", "completed")
            done.append(f"closed #{current['number']} rules, caught up with what it said")
        return done
    same = [i for i in mine if _mark(i) == (engine, needs)]
    known = bool(same) and same[-1].get("state") != "open" and not _settled(same[-1])
    if current is not None:
        if _mark(current) == (engine, needs):
            return done
        if known:       # the user closed a page for these releases: no other page says it again
            settle(current, f"The rules now need {needs} again — the page you closed for that said so ({run_url}). "
                            f"Closing.", "not_planned")
            return done + [f"closed #{current['number']} rules, its releases are a page the user closed"]
        api.patch(f"{issues}/{current['number']}", {"body": body(state, to, run_url)})
        api.post(f"{issues}/{current['number']}/comments",
                 {"body": f"Now: your engine is {engine}, the rules need {needs} or newer ({run_url})."})
        current["body"] = body(state, to, run_url)
        return done + [f"updated #{current['number']} rules"]
    if known:
        return done + [f"rules: {engine} older than {needs} — the user closed that page, it stays closed"]
    made = api.post(issues, {"title": TITLE, "body": body(state, to, run_url)})
    return done + [f"opened #{made['number']} rules"]

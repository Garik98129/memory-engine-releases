"""The copy itself: what Notion shares with the integration, as files; payment data cut
out (N6); the count of records against the previous night (N3) (docs/DESIGN.md §9).

Everything is collected first and written only when Notion answered in full: a copy
that stopped half-way would read as a mass deletion, or hide one (P5). Layout of the
branch `notion-backup`:

    copy.json                  the stamp of the run: when, what release, how many records,
                               and the bases N3 holds
    data/<data source id>/_source.md     the data source: title, parent, schema
    data/<data source id>/<page id>.md   one row
    pages/<page id>.md         a page that is not a row of a copied data source

Ids are the file names, so a renamed page is a changed file, not a moved one.

With exclusions set, a page or data source whose place in Notion cannot be followed to the
top of the workspace stops the copy instead of being copied: it may sit under an excluded
page.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..checks import numbers_check
from . import render
from .notion import NotionError

STAMP = "copy.json"
README = "README.md"
MARKER = "[card number removed]"
README_TEXT = """# Notion copy

The nightly text copy of the person's Notion, written by the backup workflow only
(docs/DESIGN.md §9). Never merged into `main`. `copy.json` is the stamp of the last run.
"""


ID_RE = re.compile(r"[0-9a-z]{1,64}")      # a Notion id, dashes removed; nothing a path could hide in


def norm(notion_id: object) -> str:
    return str(notion_id).replace("-", "").strip().lower() if isinstance(notion_id, str) else ""


def _usable(ident: str, kind: str, copy: "Copy") -> bool:
    """An id becomes a file name: only a plain one does; anything else is named and skipped."""
    if ID_RE.fullmatch(ident):
        return True
    copy.notes.append(f"NOTE a {kind} with an id that is not a Notion id was skipped")
    return False


@dataclass
class Copy:
    files: dict[str, str] = field(default_factory=dict)
    pages: int = 0
    rows: int = 0
    sources: int = 0
    excluded: int = 0
    bases: dict[str, dict] = field(default_factory=dict)  # data source id → {rows}; no titles: machine output carries none (§11)
    excluded_bases: set[str] = field(default_factory=set)  # data sources notion.backup_exclude names, or their databases
    redacted: list[str] = field(default_factory=list)     # N6 lines, no digits
    notes: list[str] = field(default_factory=list)

    @property
    def records(self) -> int:
        return self.pages + self.rows


class _Exclusion:
    """Whether a page or data source is excluded by `notion.backup_exclude` — by its own
    id, or by an ancestor's: a row of an excluded data source, a page under an excluded
    page, a data source under an excluded page.

    With exclusions set, every chain is walked to the top of the workspace, and a chain
    that cannot be followed stops the copy: a block that cannot be read, an ancestor not
    shared with the integration, a parent of no known kind. Such an item may hang under an
    excluded page, and copying it «just in case» would put it in git. Before, such an item
    was copied: with a note when a block could not be read, silently when an ancestor was
    not shared."""

    HOPS = 50
    TOP = ("workspace", "")
    UNKNOWN = ("unknown", "")

    def __init__(self, api, exclude: set[str], pages: list[dict], sources: list[dict], notes: list[str]):
        self.api = api
        self.exclude = {norm(x) for x in exclude} - {""}      # any spelling of an id
        self.notes = notes
        self.parent: dict[str, tuple[str, str]] = {}      # id → (kind, parent id)
        self._blocks: dict[str, tuple[str, str]] = {}
        for p in pages:
            self.parent[norm(p.get("id"))] = self._of(p.get("parent"))
        for s in sources:
            above = self._of(s.get("parent"))
            self.parent[norm(s.get("id"))] = above
            if above[0] == "database_id":
                # The database is a node of its own: excluded by its id, or by its page's.
                self.parent.setdefault(above[1], self._of(s.get("database_parent")))

    @classmethod
    def _of(cls, parent: object) -> tuple[str, str]:
        if not isinstance(parent, dict):
            return cls.UNKNOWN
        kind = str(parent.get("type") or "")
        if kind == "workspace":
            return cls.TOP
        ident = norm(parent.get(kind))
        return (kind, ident) if kind and ident else cls.UNKNOWN

    def _through_block(self, block_id: str, who: str) -> tuple[str, str]:
        """A page inside a block (a column, a toggle) has a block as its parent; the
        block's chain leads to the page, one request a hop, cached."""
        seen = 0
        kind, ident = "block_id", block_id
        while kind == "block_id" and seen < self.HOPS:
            seen += 1
            if ident not in self._blocks:
                try:
                    self._blocks[ident] = self._of(self.api.block(ident).get("parent"))
                except Exception as exc:    # noqa: BLE001
                    raise NotionError(f"{who}: a block above it could not be read ({type(exc).__name__}), "
                                      f"so whether it is excluded is unknown") from exc
            kind, ident = self._blocks[ident]
        if kind == "block_id":
            raise NotionError(f"{who}: more than {self.HOPS} blocks above it, so whether it is excluded is unknown")
        return (kind, ident)

    def excluded(self, obj: dict) -> bool:
        if not self.exclude:
            return False
        who = norm(obj.get("id"))
        if who in self.exclude:
            return True
        kind, ident = self._of(obj.get("parent"))
        for _ in range(self.HOPS):
            if (kind, ident) == self.TOP:
                return False
            if kind == self.UNKNOWN[0]:
                raise NotionError(f"{who}: a parent of no known kind above it, so whether it is excluded is unknown")
            if ident in self.exclude:
                return True
            if kind == "block_id":
                kind, ident = self._through_block(ident, who)
                continue
            if ident not in self.parent:
                raise NotionError(f"{who}: the {kind.removesuffix('_id').replace('_', ' ')} {ident} above it is not "
                                  f"shared with the integration, so whether it is excluded is unknown")
            kind, ident = self.parent[ident]
        raise NotionError(f"{who}: more than {self.HOPS} levels above it, so whether it is excluded is unknown")


def _live(obj: dict) -> bool:
    return not obj.get("archived") and not obj.get("in_trash")


def _scrub(path: str, title: str, text: str, payment: list[str], copy: Copy) -> str:
    text, n = numbers_check.redact(text, payment)
    if n:
        copy.redacted.append(f"{path}: {n} card number(s) removed "
                             f"from the copy — remove it from Notion")
    return text


def collect(api, exclude: set[str], payment: list[str]) -> Copy:
    """Everything the integration can see, rendered; raises on any failure to read."""
    copy = Copy()
    sources = sorted((s for s in api.search("data_source") if isinstance(s, dict) and s.get("object") == "data_source"),
                     key=lambda s: norm(s.get("id")))
    pages = sorted((p for p in api.search("page") if isinstance(p, dict) and p.get("object") == "page"),
                   key=lambda p: norm(p.get("id")))
    rule = _Exclusion(api, exclude, pages, sources, copy.notes)
    copied: set[str] = set()

    for source in sources:
        sid = norm(source.get("id"))
        if not _usable(sid, "data source", copy):
            continue
        if not _live(source):
            continue            # in the trash: nothing to copy, nothing to decide (as for pages)
        if rule.excluded(source):
            copy.excluded += 1
            kind, above = rule._of(source.get("parent"))
            if sid in rule.exclude or (kind == "database_id" and above in rule.exclude):
                copy.excluded_bases.add(sid)            # named in the config itself, not only sitting under a named page
            continue
        copy.sources += 1
        title = render.title_of(source)
        copy.bases[sid] = {"rows": 0}
        copy.files[f"data/{sid}/_source.md"] = _scrub(f"data/{sid}/_source.md", title,
                                                      render.source_markdown(source), payment, copy)
        for row in sorted((r for r in api.rows(source["id"]) if isinstance(r, dict)), key=lambda r: norm(r.get("id"))):
            rid = norm(row.get("id"))
            if not _usable(rid, "row", copy):
                continue
            # A row the search has not listed yet (its index lags) is still a known parent
            # for the pages under it.
            rule.parent.setdefault(rid, rule._of(row.get("parent")))
            if rid in copied or not _live(row):
                continue
            if rule.excluded(row):
                copy.excluded += 1
                continue
            path = f"data/{sid}/{rid}.md"
            body = render.content(api.children, row["id"])
            copy.files[path] = _scrub(path, render.title_of(row), render.page_markdown(row, body, title), payment, copy)
            copied.add(rid)
            copy.rows += 1
            copy.bases[sid]["rows"] += 1

    for page in pages:
        pid = norm(page.get("id"))
        if not _usable(pid, "page", copy):
            continue
        if pid in copied or not _live(page):
            continue
        if rule.excluded(page):
            copy.excluded += 1
            continue
        path = f"pages/{pid}.md"
        body = render.content(api.children, page["id"])
        copy.files[path] = _scrub(path, render.title_of(page), render.page_markdown(page, body), payment, copy)
        copied.add(pid)
        copy.pages += 1
    return copy


def shrink(previous: dict | None, records: int, threshold: float) -> str | None:
    """N3: the copy fell by more than the threshold share since the previous run."""
    if not isinstance(previous, dict):
        return None
    before = previous.get("records")
    if not isinstance(before, int) or isinstance(before, bool) or before <= 0:
        return None
    drop = (before - records) / before
    if drop > threshold:
        return (f"the copy fell from {before} to {records} records ({int(round(drop * 100))} %) since the "
                f"previous run — more than the alarm share of {int(round(threshold * 100))} %")
    return None


BASE_MIN_ROWS = 10    # a base this big that loses more than half of its rows is an alarm; a smaller one only when emptied


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Her decision that she removed a base: the base's id as N3's line names it (data/<id>) and
# the day the line says it went — so it ends that one loss, not a later one of the same base.
REMOVED_RE = re.compile(r"([0-9A-Za-z-]{1,80})@(\d{4}-\d{2}-\d{2})")


def removed_decisions(values: object) -> set[tuple[str, str]]:
    """`notion.removed_bases` as the copy reads it: (id, day) pairs. An entry of another form
    ends nothing — C3 refuses it on a PR, and here it only keeps the alarm."""
    found = set()
    for value in values if isinstance(values, list) else []:
        m = REMOVED_RE.fullmatch(value) if isinstance(value, str) else None
        if m and norm(m.group(1)):
            found.add((norm(m.group(1)), m.group(2)))
    return found


def held_bases(previous: dict | None, bases: dict[str, dict], excluded: set[str], removed: set[tuple[str, str]],
               today: str) -> tuple[list[str], dict[str, dict]]:
    """N3, per base, whatever the total did: a data source gone from the copy, emptied, or —
    at 10 rows or more — down by more than half. A small base is a small share of the
    total: the Documents base is ten rows in a thousand, and its loss moves the total by 1 %;
    a clean-up of a few rows in a small base is not an alarm.

    Such a loss is held night after night until the cause is gone. Before, the next night
    compared with a stamp that already lacked the rows, the finding disappeared and the
    alarm closed itself: a loss nobody saw in a day was silent for good. The stamp keeps the base under `held` — its rows before and
    the day it went — and every night reports it in the same words, so the alarm stays
    open and the held lines bring no new comment. It ends when the base has its rows back
    (any, for a small base; at least half, for a big one), or when `notion.removed_bases`
    records her decision for that base and that day (`<id>@<day>`) — taken only for a base
    already held, so the night a base goes is always reported, and an old decision does
    not end a later loss. A base the config excludes by its own id or its database's is
    reported the night it goes and not held: that exclusion is a protected change (C3). A
    base left out because it now sits under an excluded page is held like any other.

    A base is named by its path, data/<id>: machine output carries no titles (§11); the
    title is in the copy's data/<id>/_source.md. Returns (lines, the new `held`)."""
    if not isinstance(previous, dict):
        return [], {}
    carried: dict[str, tuple[int, str]] = {}
    if isinstance(previous.get("held"), dict):
        for sid, info in previous["held"].items():
            if (isinstance(info, dict) and _count(info.get("rows")) and isinstance(info.get("since"), str)
                    and DAY_RE.fullmatch(info["since"])):
                carried[sid] = (info["rows"], info["since"])
    candidates = dict(carried)
    if isinstance(previous.get("bases"), dict):
        for sid, before in previous["bases"].items():
            if isinstance(before, dict) and _count(before.get("rows")) and sid not in candidates:
                candidates[sid] = (before["rows"], today)
    out: list[str] = []
    held: dict[str, dict] = {}
    until = "held until {back} or notion.removed_bases records that she removed it"
    for sid, (rows, since) in sorted(candidates.items()):
        now = bases.get(sid)
        n = now.get("rows", 0) if isinstance(now, dict) else 0
        if n and (rows < BASE_MIN_ROWS or n * 2 >= rows):
            continue                                    # its rows are back: the cause is gone
        if sid in carried and ((sid, since) in removed or sid in excluded):
            continue                                    # her recorded decision, or a protected exclusion
        plural = "s" if rows != 1 else ""
        if n:
            out.append(f"the base data/{sid} lost more than half of its {rows} rows since {since}; "
                       + until.format(back="it has half of them back"))
        elif isinstance(now, dict):
            out.append(f"the base data/{sid} is empty since {since}: {rows} row{plural} → 0; "
                       + until.format(back="it has rows again"))
        else:
            out.append(f"the base data/{sid} ({rows} row{plural}) is gone from the copy since {since} — deleted in "
                       f"Notion, unshared from the copy's integration, or excluded in the config; "
                       + until.format(back="it is back"))
        if sid not in excluded:
            held[sid] = {"rows": rows, "since": since}
    return out, held


def read_stamp(worktree: Path) -> dict | None:
    path = worktree / STAMP
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write(copy: Copy, worktree: Path, stamp: dict) -> None:
    """The tree of the branch: the old copy goes, the new one lands, then the stamp."""
    for folder in ("pages", "data"):
        shutil.rmtree(worktree / folder, ignore_errors=True)
    for rel, text in sorted(copy.files.items()):
        path = worktree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (worktree / README).write_text(README_TEXT, encoding="utf-8")
    (worktree / STAMP).write_text(json.dumps(stamp, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                  encoding="utf-8")

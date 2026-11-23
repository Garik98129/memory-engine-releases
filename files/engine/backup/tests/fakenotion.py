"""A stand-in for Notion in the tests (F1: fixtures only, never the network).

State is a dict of pages, data sources and blocks in the shapes API 2025-09-03 returns;
a test changes it between nights: a page added, rows deleted, a card number typed in."""
from __future__ import annotations

from engine.backup.notion import NotionError


def rich(s: str, link: str | None = None) -> list[dict]:
    item = {"type": "text", "plain_text": s, "text": {"content": s, "link": {"url": link} if link else None}}
    return [item]


def page(pid: str, title: str, parent: dict, props: dict | None = None, archived: bool = False, **extra) -> dict:
    return {"object": "page", "id": pid, "created_time": "2026-10-20T10:00:00.000Z",
            "last_edited_time": "2026-10-21T10:00:00.000Z", "archived": archived, "in_trash": archived,
            "url": f"https://www.notion.so/{pid.replace('-', '')}", "parent": parent,
            "properties": {"Name": {"id": "title", "type": "title", "title": rich(title)}, **(props or {})}, **extra}


def source(sid: str, title: str, database_id: str, page_parent: str, schema: dict | None = None) -> dict:
    return {"object": "data_source", "id": sid, "title": rich(title), "url": f"https://www.notion.so/{sid}",
            "parent": {"type": "database_id", "database_id": database_id},
            "database_parent": {"type": "page_id", "page_id": page_parent},
            "properties": schema or {"Name": {"id": "title", "name": "Name", "type": "title", "title": {}}}}


def paragraph(bid: str, text: str, children: list[dict] | None = None) -> dict:
    return {"object": "block", "id": bid, "type": "paragraph", "has_children": bool(children),
            "paragraph": {"rich_text": rich(text), "color": "default"}, "_children": children or []}


class FakeNotion:
    def __init__(self, pages: list[dict] | None = None, sources: list[dict] | None = None,
                 blocks: dict[str, list[dict]] | None = None, rows: dict[str, list[dict]] | None = None):
        self.pages = list(pages or [])          # what search("page") lists
        self.sources = list(sources or [])      # what search("data_source") lists
        self.row_lists = dict(rows or {})       # data source id → its pages
        self.blocks = dict(blocks or {})        # page or block id → children
        self.parents: dict[str, dict] = {}      # block id → parent, for block()
        self.down = False                       # the API is unreachable
        self.fail_at: int | None = None         # ... or dies on this request (mid-run)
        self.requests = 0

    def _up(self):
        self.requests += 1
        if self.down or (self.fail_at is not None and self.requests >= self.fail_at):
            raise NotionError("POST search: HTTP 503 after 6 attempts")

    def search(self, kind):
        self._up()
        yield from (self.pages if kind == "page" else self.sources)

    def rows(self, data_source_id):
        self._up()
        yield from self.row_lists.get(data_source_id, [])

    def children(self, block_id):
        self._up()
        out = []
        for b in self.blocks.get(block_id, []):
            kids = b.get("_children") or []
            if kids:
                self.blocks[b["id"]] = kids
            out.append({k: v for k, v in b.items() if k != "_children"})
        return out

    def block(self, block_id):
        self._up()
        if block_id not in self.parents:
            raise NotionError(f"GET blocks/{block_id}: HTTP 404 object_not_found")
        return {"object": "block", "id": block_id, "parent": self.parents[block_id]}

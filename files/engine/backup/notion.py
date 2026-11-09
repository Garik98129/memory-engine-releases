"""A read-only Notion client on the standard library, for the nightly copy (docs/DESIGN.md §9).

Pinned to API version 2025-09-03 — the version that introduced data sources (a database
holds one or more; the search lists data sources, a database id and a data source id are
not interchangeable); the official JavaScript SDK (5.28) defaults to it, and Notion keeps
older versions working (Notion reference and upgrade guide, read 23.10.2026). Search
lists what is shared with the integration; a data source is read with its query
endpoint, a page's content with the block-children endpoint, one level at a time; every
list is paged by 100, the maximum Notion serves.

Budget: 180 requests a minute per integration on an ordinary plan — about 3 a second —
and a separate limit of about 3 a second on data source queries. This client keeps under
that pace, honours Retry-After on 429 and 529, and retries a 5xx a few times with a
pause. The token goes in the Authorization header and never into a message or a log.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterator

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"
PAGE_SIZE = 100         # the maximum, and the default (Notion reference, «Pagination»)
MAX_PAGES = 500         # 50 000 items in one list: more than a person's Notion holds
MIN_INTERVAL = 0.35     # seconds between requests: under 3 a second
ATTEMPTS = 6            # on 429, 529 and 5xx
MAX_WAIT = 90.0         # the longest single pause Retry-After may ask for (seconds)
TIMEOUT = 60


class NotionError(RuntimeError):
    """Notion could not be read. The message names the request and the status, never
    the token and never the content."""


class Notion:
    def __init__(self, token: str, api: str = API, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if not token:
            raise NotionError("no Notion token")
        self._token = token
        self.api = api.rstrip("/")
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self.requests = 0

    # -- transport --------------------------------------------------------------------

    def _once(self, method: str, path: str, body: dict | None = None):
        """One HTTP exchange: (status, headers, raw bytes). Replaced in the tests."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self.api}/{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Notion-Version", VERSION)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def _pace(self) -> None:
        if self._last is not None:
            wait = self._last + MIN_INTERVAL - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    @staticmethod
    def _retry_after(headers, default: float) -> float:
        value = headers.get("Retry-After") if headers is not None else None
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            seconds = default
        return min(max(seconds, 1.0), MAX_WAIT)

    def _send(self, method: str, path: str, body: dict | None = None) -> dict:
        what = f"{method} {path.split('?')[0]}"
        for attempt in range(ATTEMPTS):
            self._pace()
            self.requests += 1
            try:
                status, headers, raw = self._once(method, path, body)
            except (OSError, ValueError) as exc:        # network, timeout, a bad URL
                raise NotionError(f"{what}: {type(exc).__name__}") from exc
            if status == 200:
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise NotionError(f"{what}: not JSON") from exc
                if not isinstance(data, dict):
                    raise NotionError(f"{what}: not an object")
                return data
            if status in (429, 529) or status >= 500:
                if attempt == ATTEMPTS - 1:
                    break
                pause = self._retry_after(headers, 2.0 * (attempt + 1))
                self._sleep(pause)
                continue
            raise NotionError(f"{what}: HTTP {status} {self._code(raw)}".rstrip())
        raise NotionError(f"{what}: HTTP {status} after {ATTEMPTS} attempts")

    @staticmethod
    def _code(raw: bytes) -> str:
        """Notion's error code («object_not_found», «restricted_resource»), not its message —
        the message may quote content."""
        try:
            code = json.loads(raw.decode("utf-8")).get("code")
        except (UnicodeDecodeError, ValueError, AttributeError):
            return ""
        return str(code) if isinstance(code, str) else ""

    # -- lists ------------------------------------------------------------------------

    def _pages(self, method: str, path: str, body: dict | None = None) -> Iterator[dict]:
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            if method == "GET":
                params = {"page_size": PAGE_SIZE, **({"start_cursor": cursor} if cursor else {})}
                data = self._send(method, f"{path}?{urllib.parse.urlencode(params)}")
            else:
                data = self._send(method, path, {**(body or {}), "page_size": PAGE_SIZE,
                                                 **({"start_cursor": cursor} if cursor else {})})
            results = data.get("results")
            if not isinstance(results, list):
                raise NotionError(f"{method} {path}: no results list")
            for item in results:
                if isinstance(item, dict):
                    yield item
            status = data.get("request_status")
            if isinstance(status, dict) and status.get("type") == "incomplete":
                # Notion caps a query at 10 000 results and says so here: a silent
                # shorter copy would look like a mass deletion, or hide one (P5).
                raise NotionError(f"{method} {path}: Notion cut the list short "
                                  f"({status.get('incomplete_reason', 'incomplete')})")
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")
            if not isinstance(cursor, str) or not cursor:
                # More promised, no way to ask for it: a short list is not a copy (P5).
                raise NotionError(f"{method} {path}: has_more without next_cursor")
        raise NotionError(f"{method} {path}: more than {MAX_PAGES} pages")

    def search(self, kind: str) -> Iterator[dict]:
        """Every page or data source shared with the integration (kind: «page» or
        «data_source»); what is in the trash is not listed."""
        yield from self._pages("POST", "search", {"filter": {"property": "object", "value": kind}})

    def rows(self, data_source_id: str) -> Iterator[dict]:
        """The pages of a data source — the non-archived ones, which is the default."""
        yield from self._pages("POST", f"data_sources/{data_source_id}/query")

    def children(self, block_id: str) -> list[dict]:
        """The first level of a block's (or a page's) children."""
        return list(self._pages("GET", f"blocks/{block_id}/children"))

    def block(self, block_id: str) -> dict:
        return self._send("GET", f"blocks/{block_id}")

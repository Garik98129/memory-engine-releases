"""A small GitHub REST client for the nightly checks — standard library only (docs/DESIGN.md §4).

The token comes from the environment (GH_TOKEN, the workflow's GITHUB_TOKEN). A
redirect — a job log lives on another host — is followed by hand and without the
token: the log host needs no credentials, and the token must not travel there.

Every method fails loudly (P5): a response the checks cannot read is an ApiError,
never an empty answer.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

API = "https://api.github.com"
VERSION = "2022-11-28"
NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
MAX_PAGES = 20      # 2000 items at 100 a page; more than any list the checks read


class ApiError(RuntimeError):
    """GitHub answered something the checks cannot use. `reason` holds GitHub's own words,
    kept out of the text: an alarm line is the text, and GitHub's wording can change from one
    request to the next (a request id), so a line that carried it would change every run and
    post «The findings changed» each time. The update prints the reason in its run's log."""

    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason


REASON_MAX = 200


def refused(method: str, path: str, status: int, raw: bytes) -> ApiError:
    """The error for an answer the checks cannot use, with GitHub's own words where it gives
    them as `reason` — the `message` of its JSON error body, on one line, cut at REASON_MAX
    characters. A bare «HTTP 403» did not say why the update's PR was refused on a test
    repository: a repository setting."""
    reason = ""
    try:
        message = json.loads(raw.decode("utf-8")).get("message")
    except (UnicodeDecodeError, ValueError, AttributeError):
        message = None
    if isinstance(message, str) and message.strip():
        text = " ".join(message.split())
        reason = text if len(text) <= REASON_MAX else text[:REASON_MAX - 1] + "…"
    return ApiError(f"{method} {path}: HTTP {status}", reason)


def _without_query(url: str) -> str:
    """A URL for an error line: no query string — a log's address after a redirect is signed
    there, and an error line can land in an alarm Issue."""
    return urllib.parse.urlsplit(url)._replace(query="", fragment="").geturl()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):   # noqa: D102
        return None


class GitHub:
    def __init__(self, repository: str, token: str, api: str = API):
        if not token:
            raise ApiError("no token: GH_TOKEN is empty")
        self.repository = repository
        self._token = token
        self._api = api.rstrip("/")
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _url(self, path: str, params: dict | None = None) -> str:
        url = path if path.startswith("https://") else f"{self._api}/{path.lstrip('/')}"
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        return url

    def _send(self, method: str, url: str, body: object = None, auth: bool = True):
        """One request, retried once after a server error or a network error: a single
        hiccup must not turn into a red save and a mention."""
        try:
            status, headers, raw = self._once(method, url, body, auth)
        except ApiError:
            time.sleep(self.retry_after)
            return self._once(method, url, body, auth)
        if status >= 500:
            time.sleep(self.retry_after)
            return self._once(method, url, body, auth)
        return status, headers, raw

    retry_after = 2.0

    def _once(self, method: str, url: str, body: object = None, auth: bool = True):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": VERSION,
                   "User-Agent": "memory-engine-checks"}
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=30) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()
        except urllib.error.URLError as exc:
            raise ApiError(f"{method} {_without_query(url)}: {exc.reason}") from None
        except OSError as exc:              # a timeout, a reset: the network, not the checks
            raise ApiError(f"{method} {_without_query(url)}: {exc}") from None

    @staticmethod
    def _decode(raw: bytes, what: str):
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiError(f"{what}: not JSON ({exc})") from None

    def _json(self, method: str, path: str, params: dict | None = None, body: object = None,
              allow_404: bool = False):
        status, headers, raw = self._send(method, self._url(path, params), body)
        if status == 404 and allow_404:
            return None
        if status not in (200, 201):
            raise refused(method, path, status, raw)
        return self._decode(raw, f"{method} {path}"), headers

    def get(self, path: str, params: dict | None = None, allow_404: bool = False):
        got = self._json("GET", path, params, allow_404=allow_404)
        return None if got is None else got[0]

    def status(self, path: str) -> int:
        """Only the HTTP status: 204 or 404 answers a yes-or-no question."""
        return self._send("GET", self._url(path))[0]

    def pages(self, path: str, params: dict | None = None, key: str | None = None) -> Iterator[dict]:
        """Every item of a list endpoint, page after page. `key` names the array
        when the endpoint wraps it in an object (the jobs of a run)."""
        url: str | None = self._url(path, {"per_page": 100, **(params or {})})
        for _ in range(MAX_PAGES):
            if url is None:
                return
            status, headers, raw = self._send("GET", url)
            if status != 200:
                raise refused("GET", path, status, raw)
            data = self._decode(raw, f"GET {path}")
            items = data.get(key, []) if key else data
            if not isinstance(items, list):
                raise ApiError(f"GET {path}: not a list")
            yield from items
            found = NEXT_RE.search(headers.get("Link", "") or "")
            url = found.group(1) if found else None
        if url is not None:
            raise ApiError(f"GET {path}: more than {MAX_PAGES} pages")

    def text(self, path: str) -> str:
        """A text resource behind a redirect (a job log)."""
        status, headers, raw = self._send("GET", self._url(path))
        if status in (301, 302, 303, 307, 308):
            location = headers.get("Location", "")
            if not location.startswith("https://"):
                raise ApiError(f"GET {path}: redirect to {location!r}")
            status, headers, raw = self._send("GET", location, auth=False)
        if status != 200:
            raise refused("GET", path, status, raw)
        return raw.decode("utf-8", "replace")

    def post(self, path: str, body: dict):
        return self._json("POST", path, body=body)[0]

    def post_no_content(self, path: str, body: dict) -> None:
        """A POST whose answer the caller does not read: a workflow dispatch answers 204, or
        200 with the run's details where GitHub is asked for them."""
        status, _headers, raw = self._send("POST", self._url(path), body)
        if status not in (200, 204):
            raise refused("POST", path, status, raw)

    def patch(self, path: str, body: dict):
        return self._json("PATCH", path, body=body)[0]

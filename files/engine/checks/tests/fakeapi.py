"""A stand-in for the GitHub API in the tests (F1: fixtures only, never the network)."""
from __future__ import annotations

from engine.checks.github import ApiError

REPO = "owner/person-memory"
BOT = "github-actions[bot]"


class FakeApi:
    """Answers from dicts keyed by path; records what was written."""

    def __init__(self, repository: str = REPO):
        self.repository = repository
        self.objects: dict[str, object] = {}       # get(path) → JSON
        self.lists: dict[str, list] = {}           # pages(path) → items
        self.texts: dict[str, str] = {}            # text(path) → str
        self.statuses: dict[str, int] = {}         # status(path) → HTTP code
        self.writes: list[tuple[str, str, dict]] = []
        self._next_issue = 100
        self.head_of = None                        # branch → its head sha, where a test has a git remote

    def _pr(self, item):
        """A PR shows its branch's head as git has it, as GitHub does once it has caught up."""
        if self.head_of and isinstance(item, dict) and isinstance(item.get("head"), dict) and "ref" in item["head"]:
            sha = self.head_of(item["head"]["ref"])
            if sha:
                item["head"]["sha"] = sha
        return item

    def get(self, path, params=None, allow_404=False):
        if path in self.objects:
            return self.objects[path]
        listed, _, number = path.rpartition("/")
        for item in self.lists.get(listed, []):
            if isinstance(item, dict) and str(item.get("number")) == number:
                return self._pr(item)
        if allow_404:
            return None
        raise ApiError(f"GET {path}: HTTP 404")

    def pages(self, path, params=None, key=None):
        if path not in self.lists:
            raise ApiError(f"GET {path}: HTTP 404")
        state = (params or {}).get("state")
        ref = (params or {}).get("ref")
        for item in self.lists[path]:
            # An item that names its state is served only for that state (the issues list);
            # one that names its ref only for that ref (the activity log).
            if state and state != "all" and isinstance(item, dict) and item.get("state", state) != state:
                continue
            if ref and isinstance(item, dict) and item.get("ref", "refs/heads/main") != ref:
                continue
            yield self._pr(item)

    def text(self, path):
        if path not in self.texts:
            raise ApiError(f"GET {path}: HTTP 404")
        return self.texts[path]

    def status(self, path):
        return self.statuses.get(path, 404)

    # Writes are recorded, and the Issues they make are served back: a scenario can run
    # the alarms day after day against what the previous runs wrote.

    def post(self, path, body):
        self.writes.append(("POST", path, body))
        if path.endswith("/issues"):
            self._next_issue += 1
            issue = {"number": self._next_issue, "title": body.get("title"), "body": body.get("body"),
                     "user": {"login": BOT}, "state": "open", "closed_by": None}
            self.lists.setdefault(path, []).append(issue)
            self.lists[f"{path}/{issue['number']}/comments"] = []
            return {"number": issue["number"]}
        if path.endswith("/comments"):
            self.lists.setdefault(path, []).append({"user": {"login": BOT}, "body": body.get("body")})
        if path.endswith("/pulls"):
            self._next_issue += 1
            pr = {"number": self._next_issue, "title": body.get("title"), "body": body.get("body"),
                  "user": {"login": BOT}, "state": "open", "base": {"ref": body.get("base")},
                  "head": {"ref": body.get("head"), "repo": {"full_name": self.repository}}}
            self.lists.setdefault(path, []).append(pr)
            return {"number": pr["number"]}
        return {}

    def post_no_content(self, path, body):
        self.writes.append(("POST", path, body))

    def patch(self, path, body):
        self.writes.append(("PATCH", path, body))
        issues_path, _, number = path.rpartition("/")
        for issue in self.lists.get(issues_path, []):
            if str(issue.get("number")) == number and "state" in body:
                issue["state"] = body["state"]
                issue["closed_by"] = {"login": BOT} if body["state"] == "closed" else None
        return {}

    def close(self, path, number, by):
        """Somebody closes an Issue by hand."""
        for issue in self.lists.get(path, []):
            if issue.get("number") == number:
                issue["state"] = "closed"
                issue["closed_by"] = {"login": by}

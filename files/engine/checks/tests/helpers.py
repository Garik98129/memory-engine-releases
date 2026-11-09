"""Throwaway git repositories for the tests (F1: fixtures only, never live files)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

DEFAULTS = (Path(__file__).resolve().parents[2] / "defaults.toml").read_bytes()

VALID_CONFIG = b"""\
[person]
language = "ru"
country = "IL"
timezone = "Asia/Jerusalem"

[platform]
name = "grok"
chat_field_limit = 4000
bot_field_limit = 7900
read_budget_bytes = 16384

[engine]
channel = "stable"
"""

BASE_FILES: dict[str, bytes] = {
    "engine/defaults.toml": DEFAULTS,
    "engine/rules/core.md": b"Core rules.\n",
    "engine/rules/fields/chat.md": b"Chat safety rules.\n",
    "engine/rules/fields/bot.md": b"Bot safety rules.\n",
    ".github/workflows/check.yml": b"name: check\n",
    "user/config.toml": VALID_CONFIG,
    "user/engine.lock": b'release = "v0.1.0"\n',
    "user/fields/chat.md": b"Who: a test person.\n",
    "user/fields/bot.md": b"Who: a test person, for the bot.\n",
}

APPROVED = "2026-10-23T11:08+03:00"
COMMITTED = "2026-10-23T09:00:00+00:00"    # every fixture commit: tests do not depend on the day they run
PUBLISHED = "2026-10-01T09:00:00Z"         # a fixture release: due on every channel by every test's NOW


def body(*files: str, change: str = "Was: one. Is: another.", approved: str = APPROVED) -> str:
    """A PR body in the form C7 reads."""
    listed = "\n".join(f"- {f}" for f in files)
    return f"## Change\n{change}\n\n## Files\n{listed}\n\n## Approved\n{approved}\n"


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_AUTHOR_DATE": COMMITTED, "GIT_COMMITTER_DATE": COMMITTED}
    out = subprocess.run(["git", "-C", str(repo), *args], check=True,
                         capture_output=True, env=env)
    return out.stdout.decode().strip()


class Repo:
    def __init__(self, files: dict[str, bytes] | None = None):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        _git(self.path, "init", "-q", "-b", "main")
        _git(self.path, "config", "core.autocrlf", "false")
        self.base = self.commit(files if files is not None else BASE_FILES)

    def close(self) -> None:
        self._tmp.cleanup()

    def write(self, path: str | bytes, content: bytes | None) -> None:
        p = Path(os.fsdecode(path)) if isinstance(path, bytes) else Path(path)
        full = self.path / p
        if content is None:
            _git(self.path, "rm", "-q", "--", os.fsdecode(path) if isinstance(path, bytes) else path)
            return
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)

    def commit(self, changes: dict[str, bytes | None]) -> str:
        for path, content in changes.items():
            self.write(path, content)
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-q", "--allow-empty", "-m", "x")
        return _git(self.path, "rev-parse", "HEAD")

    def checkout(self, rev: str) -> None:
        """Move the work tree to rev; the next commit branches off there."""
        _git(self.path, "checkout", "-q", "--detach", rev)

    def branch(self, name: str, rev: str) -> None:
        """A branch ref at rev, as a fetched branch looks to the nightly checks."""
        _git(self.path, "branch", "-f", name, rev)

    def symlink(self, path: str, target: str) -> str:
        full = self.path / path
        if full.exists() or full.is_symlink():
            full.unlink()
        full.symlink_to(target)
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-q", "-m", "link")
        return _git(self.path, "rev-parse", "HEAD")

    def chmod_exec(self, path: str) -> str:
        _git(self.path, "update-index", "--chmod=+x", path)
        _git(self.path, "commit", "-q", "-m", "exec")
        return _git(self.path, "rev-parse", "HEAD")

    def manifest(self, rev: str, release: str = "v0.1.0", published: str | None = PUBLISHED) -> dict:
        """The manifest the factory would publish for rev; `published=None` leaves the time out."""
        files = {}
        for p in _git(self.path, "ls-tree", "-r", "--name-only", rev, "--", "engine", ".github").splitlines():
            blob = subprocess.run(["git", "-C", str(self.path), "show", f"{rev}:{p}"],
                                  check=True, capture_output=True).stdout
            files[p] = hashlib.sha256(blob).hexdigest()
        data: dict = {"release": release, "files": files}
        if published is not None:
            data["published"] = published
        return data

    def body_file(self, text: str) -> Path:
        path = self.path.parent / f"{self.path.name}-body.md"
        path.write_text(text, encoding="utf-8")
        return path

    def manifest_file(self, rev: str, release: str = "v0.1.0") -> Path:
        path = self.path.parent / f"{self.path.name}-manifest.json"
        path.write_text(json.dumps(self.manifest(rev, release)), encoding="utf-8")
        return path

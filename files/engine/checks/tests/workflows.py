"""The shipped workflow files (engine/workflows/), read for the tests with the standard library
only — no YAML library ships with Python. It reads the one shape these files have and refuses
anything else, so a test that holds a step to its contract cannot be fooled by a layout it does
not know: two spaces a level, jobs under `jobs:`, a step's keys one level under its `- name:`,
`env:` and `with:` as plain `KEY: value` lines, a script only as one folded line (`run: >-`) or
on the key's own line."""
from __future__ import annotations

from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"
NAMES = ("backup.yml", "check.yml", "settings.yml", "setup.yml", "update.yml", "watch.yml")


def text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def comments(name: str) -> list[str]:
    """Every comment of the file: whole lines, and what follows « #» after a value."""
    out = []
    for line in text(name).splitlines():
        if line.lstrip().startswith("#"):
            out.append(line.lstrip()[1:].strip())
        elif " #" in line and "${{" not in line.split(" #", 1)[1]:
            out.append(line.split(" #", 1)[1].strip())
    return out


TOP_KEYS = ("name", "on", "permissions", "concurrency", "jobs")
JOB_KEYS = ("if", "needs", "runs-on", "timeout-minutes", "environment", "permissions", "concurrency", "outputs", "steps")
JOB_TABLES = ("permissions", "concurrency", "outputs")
STEP_KEYS = ("name", "id", "if", "uses", "with", "env", "run")


def _pair(body: str) -> tuple[str, str]:
    key, colon, value = body.partition(":")
    if not colon:
        raise AssertionError(f"not `key: value`: {body!r}")
    return key.strip(), value.strip()


def _bare(line: str) -> str:
    """A line without the comment that follows its value."""
    cut = line.find(" #")
    return (line if cut < 0 or "${{" in line[cut:] else line[:cut]).rstrip()


def top(name: str) -> dict[str, list[str]]:
    """Every top-level key → the lines of its block as written, comments and empty lines left
    out. A top-level key this reader does not know is refused: `env:`, `defaults:` or a second
    `on:` would change what every job runs with."""
    out: dict[str, list[str]] = {}
    key = None
    for number, raw in enumerate(text(name).splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = _bare(raw)
        if not line.startswith(" "):
            key = _pair(line)[0]
            if key not in TOP_KEYS or key in out:
                raise AssertionError(f"{name}:{number}: a top-level key this reader does not take: {key!r}")
            out[key] = []
        elif key is None:
            raise AssertionError(f"{name}:{number}: a line under no key")
        out[key].append(line)
    return out


def jobs(name: str) -> dict[str, dict]:
    """job → {"keys": its own plain keys (`if`, `needs`, `runs-on`, `timeout-minutes`,
    `environment`), "permissions": {…}, "concurrency": {…}, "outputs": {…}, "steps": [{"name",
    "id", "if", "uses", "run", "env": {…}, "with": {…}}, …]}. A folded value (`>-`) is joined into
    one line, as GitHub folds it. Every key is one of a short list, at every level, and a line the
    reader cannot place is refused, never skipped: `continue-on-error`, `shell`, a job's `env`,
    a condition that goes on in a second line would change what runs without changing what a
    test sees."""
    top(name)                                       # the top level is held to its own list
    out: dict[str, dict] = {}
    job: dict | None = None
    step: dict | None = None
    block: tuple[dict, str, int] | None = None      # (where, key, its indent): a folded value being read
    table: dict | None = None                       # env, with, or a job's permissions, concurrency, outputs
    in_jobs = in_steps = False
    for number, raw in enumerate(text(name).splitlines(), start=1):
        line = raw.rstrip()
        body = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        where = f"{name}:{number}"
        if "\t" in line:
            raise AssertionError(f"{where}: a tab")
        if block is not None and body and indent > block[2]:
            block[0][block[1]] = (block[0][block[1]] + " " + body).strip()
            continue
        block = None
        if not body or body.startswith("#"):
            continue
        if indent == 0:
            in_jobs, job, step, table, in_steps = body == "jobs:", None, None, None, False
            continue
        if not in_jobs:
            continue
        if indent == 2:
            key, value = _pair(body)
            if value:
                raise AssertionError(f"{where}: a job with a value")
            job = out[key] = {"keys": {}, "permissions": {}, "concurrency": {}, "outputs": {}, "steps": []}
            step, table, in_steps = None, None, False
            continue
        if job is None:
            raise AssertionError(f"{where}: outside a job")
        if indent == 4:
            key, value = _pair(_bare(line).strip())
            if key not in JOB_KEYS or key in job["keys"] or (key == "steps" and in_steps):
                raise AssertionError(f"{where}: a job's key this reader does not take: {key!r}")
            step, table, in_steps = None, None, key == "steps"
            if key in JOB_TABLES or key == "steps":
                if value:
                    raise AssertionError(f"{where}: {key} with a value")
                table = job[key] if key in JOB_TABLES else None
            elif value == ">-" and key == "if":
                job["keys"][key] = ""
                block = (job["keys"], key, indent)
            elif not value or value[0] in "|>&*!{[":
                raise AssertionError(f"{where}: {key} is not one plain value")
            else:
                job["keys"][key] = value
            continue
        if indent == 6 and in_steps:
            if not body.startswith("- name: "):
                raise AssertionError(f"{where}: a step starts with its name")
            step = {"env": {}, "with": {}, "name": _pair(body[2:])[1]}
            job["steps"].append(step)
            table = None
            continue
        if indent == 6:                              # a job's permissions, outputs, concurrency
            if table is None:
                raise AssertionError(f"{where}: {body!r} belongs to no table of the job")
            key, value = _pair(body)
            table[key] = value if "${{" in value else _bare(value)
            continue
        if step is None:
            raise AssertionError(f"{where}: {body!r} belongs to no step")
        if indent == 8:
            key, value = _pair(body)
            if key not in STEP_KEYS or key == "name" or key in step and key not in ("env", "with") or \
                    key in ("env", "with") and step[key]:
                raise AssertionError(f"{where}: a step's key this reader does not take: {key!r}")
            table = None
            if key in ("env", "with"):
                if value:
                    raise AssertionError(f"{where}: {key} with a value")
                table = step[key]
            elif value == ">-" and key == "run":
                step[key] = ""
                block = (step, key, indent)
            elif not value or value[0] in "|>&*!{[":
                raise AssertionError(f"{where}: {key} is not one line")
            else:
                step[key] = value
            continue
        if indent == 10 and table is not None:
            key, value = _pair(body)
            if key in table:
                raise AssertionError(f"{where}: {key} twice")
            table[key] = value
            continue
        raise AssertionError(f"{where}: a line this reader does not know: {body!r}")
    return out


def steps(name: str, job: str) -> list[dict]:
    return jobs(name)[job]["steps"]


def step(name: str, job: str, step_name: str) -> dict:
    found = [s for s in steps(name, job) if s.get("name") == step_name]
    if len(found) != 1:
        raise AssertionError(f"{name}: {len(found)} steps «{step_name}» in job {job}")
    return found[0]

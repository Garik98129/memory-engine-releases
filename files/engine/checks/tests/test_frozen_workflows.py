"""The workflow files a release ships are frozen (docs/DESIGN.md §5), and thin.

GITHUB_TOKEN cannot write workflow files, so a release that changes one is not carried by the
weekly update: it waits, in every installed repository, for somebody to apply it by hand. Two
releases of four did that, one of them for the wording of comments. So the files hold only what
GitHub reads from a workflow file, what a step does is code under engine/, and these tests hold
both halves:

- **Frozen.** Every file under engine/workflows/ is held to its sha256 in engine/frozen.toml. A
  change there fails here first — with what it costs — and at `release cut` again.
- **Thin.** A step is the pinned checkout, one of the gate's four status lines, or one call of
  main's code. No script, and nothing GitHub would paste into a command line.
- **The shape.** The record holds the bytes, and a change of them is a decision; once it is
  taken and the record renewed, these tests are what still reads the files. So they hold all of
  what GitHub reads there, not a sample of it: the triggers word for word, every key of every
  job and step from a short list (`workflows.py` refuses any other — `continue-on-error`,
  `shell`, a job's `env`, a second line of a condition), every `if:`, and where a key may stand.
- **The contract.** What a step is given is exactly what its code names (`STEPS`), so a key
  cannot reach a step that must not hold it, and code cannot wait for a variable no step sets.
  The steps stay apart where the keys differ: the one that reads a PR's content gets no token,
  the one that holds the Notion key gets no GitHub token.
- **What others know the files by.** N1 knows a lawful check by the file `check.yml`, its job
  `check` and the line it logs; the update, the forms and job `merge` start the gate by that
  file's name.
"""
from __future__ import annotations

import hashlib
import re
import tomllib
import unittest
import unittest.mock

from engine.backup import night as copy_night
from engine.checks import gate, integrity, night
from engine.checks.tests import workflows
from engine.setup import release, update

FROZEN = workflows.WORKFLOWS.parent / "frozen.toml"
CHECKOUT = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803  # v6.1.0"
STATUS = ('gh api "repos/$GITHUB_REPOSITORY/statuses/$HEAD" -f state={state} -f context=gate -f description="{said}" '
          '-f target_url="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"')
RUN_URL = '--run-url "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"'
ANSWERS = ('--platform="$PLATFORM" --language="$LANGUAGE" --country="$COUNTRY" --timezone="$TIMEZONE" '
           '--name="$NAME" --gender="$GENDER" --work-languages="$WORK_LANGUAGES"')
FORM = {"GH_TOKEN": "${{ github.token }}", "PLATFORM": "${{ inputs.platform }}", "LANGUAGE": "${{ inputs.language }}",
        "COUNTRY": "${{ inputs.country }}", "TIMEZONE": "${{ inputs.timezone }}", "NAME": "${{ inputs.name }}",
        "GENDER": "${{ inputs.gender }}", "WORK_LANGUAGES": "${{ inputs.work_languages }}"}
# The steps whose code is one module with named steps: workflow → that module.
MODULES = {"check.yml": gate, "watch.yml": night, "backup.yml": copy_night}
# The three that were one call from the start: workflow → (the call, what it is given).
ONE_CALL = {
    "update.yml": (f'python3 -m engine.setup.update --main "$GITHUB_SHA" {RUN_URL}', {"GH_TOKEN": "${{ github.token }}"}),
    "setup.yml": (f'python3 -m engine.setup.setup_run --main "$GITHUB_SHA" {RUN_URL} {ANSWERS}', FORM),
    "settings.yml": (f'python3 -m engine.setup.setup_run --keep --main "$GITHUB_SHA" {RUN_URL} {ANSWERS}', FORM),
}
# The gate's verdict is a commit status, and these four lines write it. They stay in the file:
# the first runs before any code is checked out, the last must run when the checkout failed.
STATUS_STEPS = {
    "Gate pending at once": STATUS.format(state="pending", said="checking"),
    "Gate pending": STATUS.format(state="pending", said="checking"),
    "Gate passed": STATUS.format(state="success", said="all checks passed"),
    "Gate failed": STATUS.format(state="failure", said="a check blocked or could not run"),
}
CALL_RE = re.compile(r"python3 -m (engine(?:\.[a-z_]+)+) ([a-z]+)")
RIGHTS = {
    ("check.yml", "check"): {"contents": "read", "pull-requests": "read", "statuses": "write", "actions": "read",
                             "issues": "write"},
    ("check.yml", "merge"): {"contents": "write", "pull-requests": "write", "statuses": "write", "actions": "write"},
    ("watch.yml", "watch"): {"contents": "read", "pull-requests": "read", "statuses": "read", "actions": "read",
                             "issues": "write"},
    ("backup.yml", "backup"): {"contents": "write", "issues": "write"},
    ("update.yml", "update"): {"contents": "write", "pull-requests": "write", "issues": "write", "actions": "write"},
    ("setup.yml", "setup"): {"contents": "write", "pull-requests": "write", "issues": "write", "actions": "write"},
    ("settings.yml", "settings"): {"contents": "write", "pull-requests": "write", "issues": "write", "actions": "write",
                                   "statuses": "read"},
}
MAIN_ONLY = "github.ref == 'refs/heads/main'"
FORM = ["  workflow_dispatch:", "    inputs:"]
ON = {
    "check.yml": ["on:", "  pull_request_target:", "    branches: [main]",
                  "    types: [opened, synchronize, reopened, edited]", "  workflow_dispatch:", "    inputs:", "      pr:",
                  "        description: Number of the PR to check", "        required: true", "        type: number"],
    "watch.yml": ["on:", "  schedule:", '    - cron: "41 0 * * *"', "  push:", "    branches: [main]",
                  "  workflow_dispatch:"],
    "backup.yml": ["on:", "  schedule:", '    - cron: "11 1 * * *"', "  workflow_dispatch:"],
    "update.yml": ["on:", "  schedule:", '    - cron: "23 4 * * 1"', "  workflow_dispatch:"],
}
CONCURRENCY = {
    "check.yml": ["concurrency:", "  group: check-${{ github.event.pull_request.number || inputs.pr }}",
                  "  cancel-in-progress: true"],
    "watch.yml": ["concurrency:", "  group: notion-copy", "  cancel-in-progress: false", "  queue: max"],
    "backup.yml": ["concurrency:", "  group: notion-copy", "  cancel-in-progress: false", "  queue: max"],
    "update.yml": ["concurrency:", "  group: update", "  cancel-in-progress: false"],
    "setup.yml": ["concurrency:", "  group: setup", "  cancel-in-progress: false"],
    "settings.yml": ["concurrency:", "  group: setup", "  cancel-in-progress: false"],
}
JOBS = {        # every plain key of every job: nothing else decides whether, where and how long it runs
    ("check.yml", "check"): {"if": None, "runs-on": "ubuntu-24.04", "timeout-minutes": "10"},
    ("check.yml", "merge"): {"needs": "check", "runs-on": "ubuntu-24.04", "timeout-minutes": "5"},
    ("watch.yml", "watch"): {"if": MAIN_ONLY, "runs-on": "ubuntu-24.04", "timeout-minutes": "15"},
    ("backup.yml", "backup"): {"if": MAIN_ONLY, "runs-on": "ubuntu-24.04", "timeout-minutes": "30", "environment": "backup"},
    ("update.yml", "update"): {"if": MAIN_ONLY, "runs-on": "ubuntu-24.04", "timeout-minutes": "10"},
    ("setup.yml", "setup"): {"if": MAIN_ONLY, "runs-on": "ubuntu-24.04", "timeout-minutes": "10"},
    ("settings.yml", "settings"): {"if": MAIN_ONLY, "runs-on": "ubuntu-24.04", "timeout-minutes": "10"},
}
STEP_IFS = {    # every step that does not simply run when the steps before it passed
    ("check.yml", "Gate pending at once"): "github.event_name == 'pull_request_target'",
    ("check.yml", "Gate failed"): "failure() && (steps.pr.outputs.head != '' || github.event_name == 'pull_request_target')",
    ("watch.yml", "Alarms"): "always()",
    ("backup.yml", "Alarms"): "always()",
}
GATE_IF = ("(github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main') || "
           "(github.event_name == 'pull_request_target' && github.event.pull_request.state == 'open')")


def every_step():
    for name in workflows.NAMES:
        for job, parts in workflows.jobs(name).items():
            for step in parts["steps"]:
                yield name, job, step


class Frozen(unittest.TestCase):
    def test_every_shipped_workflow_file_is_the_one_on_record(self):
        record = tomllib.loads(FROZEN.read_text(encoding="utf-8"))["workflows"]
        self.assertIsInstance(record["version"], int)
        shipped = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(workflows.WORKFLOWS.iterdir())}
        self.assertEqual(sorted(shipped), sorted(workflows.NAMES), "a workflow file was added or removed")
        changed = sorted(n for n in shipped if record["sha256"].get(n) != shipped[n]) + \
            sorted(n for n in record["sha256"] if n not in shipped)
        self.assertEqual(changed, [], (
            f"\n\nThe shipped workflow files changed: {', '.join(changed)}.\n"
            "A release that changes a workflow file is NOT carried by the weekly update: GITHUB_TOKEN cannot "
            "write\nworkflow files, so every installed repository waits for somebody to apply that release by "
            "hand.\nIf a step should do something else, change its code under engine/ — not the file.\n"
            "If the file itself has to change (a trigger, a right, a form's field, the pinned checkout), that is "
            "a decision\nfor the owner: then raise `version` in engine/frozen.toml and record there:\n"
            + "\n".join(f'"{n}" = "{shipped[n]}"' for n in changed if n in shipped)))

    def test_the_record_is_read_by_the_release_as_the_test_reads_it(self):
        files = {f"engine/workflows/{name}": workflows.text(name).encode() for name in workflows.NAMES}
        files["engine/frozen.toml"] = FROZEN.read_bytes()
        version, on_record = release.frozen_workflows(files)
        self.assertEqual(version, tomllib.loads(FROZEN.read_text(encoding="utf-8"))["workflows"]["version"])
        self.assertEqual(sorted(on_record), sorted(workflows.NAMES))


class Thin(unittest.TestCase):
    def test_a_step_is_the_checkout_a_status_line_or_one_call(self):
        for name, job, step in every_step():
            with self.subTest(workflow=name, job=job, step=step.get("name")):
                self.assertEqual(("uses" in step) + ("run" in step), 1, "a step either uses the checkout or runs one line")
                if "uses" in step:
                    self.assertEqual(step["uses"], CHECKOUT)
                    self.assertEqual(step["env"], {})
                    whole = {} if (name, job) == ("check.yml", "merge") else {"fetch-depth": "0"}   # merge reads no history
                    self.assertEqual(step["with"], {"ref": "${{ github.sha }}", "persist-credentials": "false", **whole},
                                     "main's code as the event saw it, all of its history where a step reads it, "
                                     "and no credentials left in the checkout")
                elif step["name"] in STATUS_STEPS and name == "check.yml" and job == "check":
                    self.assertEqual(step["run"], STATUS_STEPS[step["name"]])
                elif name in ONE_CALL:
                    self.assertEqual((step["run"], step["env"]), ONE_CALL[name])
                else:
                    self.assertRegex(step["run"], rf"^{CALL_RE.pattern}$")

    def test_nothing_github_would_paste_into_a_command_line(self):
        for name, job, step in every_step():
            self.assertNotIn("${{", step.get("run", ""), f"{name}: {step.get('name')}")

    def test_the_gate_writes_its_verdict_in_these_four_steps_and_no_other(self):
        named = [s["name"] for s in workflows.steps("check.yml", "check") if s.get("run", "").startswith("gh ")]
        self.assertEqual(named, list(STATUS_STEPS))
        for name, job, step in every_step():
            if (name, job) != ("check.yml", "check"):
                self.assertFalse(step.get("run", "").startswith("gh "), f"{name}: {step.get('name')}")

    def test_the_comments_point_at_no_issue_no_pr_no_paragraph_and_no_check(self):
        """A comment that points at a discussion, a design paragraph or a check by its number goes
        stale, and putting it right is a release applied by hand; the comments say what the file
        itself fixes — a trigger, a right and what it is for, the order of the steps."""
        for name in workflows.NAMES:
            for comment in workflows.comments(name):
                self.assertNotRegex(comment, r"§|#\d|\b(?:Issue|PR) \d|docs/|DESIGN|\(owner|\d\d\.\d\d\.\d{4}|\b[CNF]\d\b", name)

    def test_no_step_is_named_after_the_checks_it_runs_today(self):
        """«C1–C7», «N1, N5»: a check added tomorrow would make the name untrue, and the file with it."""
        for name, _job, step in every_step():
            self.assertNotRegex(step["name"], r"\b[CNF]\d", name)

    def test_every_right_says_in_a_word_what_it_is_for(self):
        for name in workflows.NAMES:
            rights = re.findall(r"^      [a-z-]+: (?:read|write)\b(.*)$", workflows.text(name), re.MULTILINE)
            self.assertTrue(rights, name)
            for rest in rights:
                self.assertRegex(rest, r"^ +# \S.{5,}$", name)


class Shape(unittest.TestCase):
    def test_the_triggers_are_these_word_for_word(self):
        for name in workflows.NAMES:
            top = workflows.top(name)
            self.assertEqual(sorted(top), sorted(workflows.TOP_KEYS), f"{name}: nothing at the top but these five")
            self.assertEqual(top["name"], [f"name: {name[:-4]}"])
            self.assertEqual(top["permissions"], ["permissions: {}"], name)
            self.assertEqual(top["concurrency"], CONCURRENCY[name], name)
            if name in ON:
                self.assertEqual(top["on"], ON[name], name)
            else:                   # a form: started by hand only; its fields are held to setup's in test_setup_run
                self.assertEqual(top["on"][:3], ["on:", *FORM], name)
                self.assertTrue(all(line.startswith("      ") for line in top["on"][3:]), f"{name}: only the form's fields")

    def test_every_key_of_every_job_is_one_of_these(self):
        seen = {}
        for name in workflows.NAMES:
            for job, parts in workflows.jobs(name).items():
                seen[(name, job)] = parts["keys"]
        self.assertEqual(seen, {**JOBS, ("check.yml", "check"): {**JOBS[("check.yml", "check")], "if": GATE_IF}})
        check = workflows.jobs("check.yml")
        self.assertEqual(check["check"]["outputs"], {key: f"${{{{ steps.pr.outputs.{key} }}}}"
                                                     for key in ("number", "head", "base", "body_sha")})
        self.assertEqual(check["merge"]["concurrency"], {"group": "merge-main", "cancel-in-progress": "false"})
        for name in workflows.NAMES:
            for job, parts in workflows.jobs(name).items():
                if (name, job) != ("check.yml", "merge"):
                    self.assertEqual(parts["concurrency"], {}, f"{name}: {job}")
                if (name, job) != ("check.yml", "check"):
                    self.assertEqual(parts["outputs"], {}, f"{name}: {job}")

    def test_a_step_runs_when_the_steps_before_it_passed_but_for_these_four(self):
        seen = {(name, step["name"]): step["if"] for name, _job, step in every_step() if "if" in step}
        self.assertEqual(seen, STEP_IFS)

    def test_no_key_reaches_a_step_but_through_its_own_env(self):
        """A key set for a whole job or a whole file would reach every step of it; `workflows.py`
        refuses `env:` above a step, and the files name GitHub's token and the secrets nowhere
        but in the lines the other tests hold."""
        tokens = secrets = 0
        for name in workflows.NAMES:
            text = workflows.text(name)
            given = sum(value == "${{ github.token }}" for _n, _j, step in every_step() if _n == name
                        for value in step["env"].values())
            self.assertEqual(text.count("github.token"), given, name)
            self.assertEqual(len(re.findall(r"(?i)secrets", text)), 1 if name == "backup.yml" else 0, name)
            self.assertNotRegex(text, r"(?i)toJSON|fromJSON|\binherit\b", name)
            tokens, secrets = tokens + given, secrets + len(re.findall(r"(?i)secrets", text))
        self.assertEqual((tokens, secrets), (14, 1), "GitHub's token in fourteen steps, the Notion key in one")

    def test_the_reader_refuses_what_it_does_not_know(self):
        """Each of these changes what GitHub runs and none of them changes a step's `env:` or `run:`."""
        good = workflows.text("check.yml")
        changes = {
            "a step that never runs": ("      - name: The checks of the PR\n", "      - name: The checks of the PR\n        if: false\n        if: true\n"),
            "a failed step that does not fail the job": ("        run: python3 -m engine.checks.gate checks\n", "        run: python3 -m engine.checks.gate checks\n        continue-on-error: true\n"),
            "another shell": ("        run: python3 -m engine.checks.gate checks\n", "        run: python3 -m engine.checks.gate checks\n        shell: bash -c \"curl x | sh; {0}\"\n"),
            "a key for the whole job": ("    timeout-minutes: 10\n", "    timeout-minutes: 10\n    env:\n      GH_TOKEN: x\n"),
            "a key for the whole file": ("permissions: {}\n", "permissions: {}\n\nenv:\n  GH_TOKEN: x\n"),
            "a condition that goes on in a second line": ("    needs: check\n", "    needs: check\n    if: success()\n      || always()\n"),
            "a job that does not stop on a failure": ("    timeout-minutes: 5\n", "    timeout-minutes: 5\n    continue-on-error: true\n"),
            "a script of several lines": ("        run: python3 -m engine.checks.gate merge\n", "        run: |\n          python3 -m engine.checks.gate merge\n"),
            "a second set of triggers": ("permissions: {}\n", "permissions: {}\n\non:\n  issue_comment:\n"),
            "a step's key twice": ("        id: pr\n", "        id: pr\n        id: other\n"),
        }
        for what, (old, new) in changes.items():
            self.assertEqual(good.count(old), 1, what)
            with unittest.mock.patch.object(workflows, "text", lambda name, changed=good.replace(old, new): changed):
                with self.assertRaises(AssertionError, msg=what):
                    workflows.jobs("check.yml")
        self.assertTrue(workflows.jobs("check.yml"))


class Contract(unittest.TestCase):
    def calls(self):
        for name, module in MODULES.items():
            for job, parts in workflows.jobs(name).items():
                for step in parts["steps"]:
                    found = CALL_RE.fullmatch(step.get("run", ""))
                    if found:
                        yield name, job, step, module, found.group(1), found.group(2)

    def test_every_call_names_a_step_of_its_module_and_every_step_is_called_once(self):
        called: dict[str, list[str]] = {name: [] for name in MODULES}
        for name, _job, step, module, dotted, which in self.calls():
            self.assertEqual(dotted, module.__name__, f"{name}: {step['name']}")
            self.assertIn(which, module.STEPS, f"{name}: {step['name']}")
            called[name].append(which)
        for name, module in MODULES.items():
            self.assertEqual(sorted(called[name]), sorted(module.STEPS), name)

    def test_a_step_is_given_exactly_what_its_code_names(self):
        for name, _job, step, module, _dotted, which in self.calls():
            self.assertEqual(sorted(step["env"]), sorted(module.STEPS[which].env), f"{name}: {step['name']}")

    def test_the_step_that_reads_the_prs_content_is_given_no_token(self):
        step = workflows.step("check.yml", "check", "The checks of the PR")
        self.assertEqual(step["run"], "python3 -m engine.checks.gate checks")
        for value in step["env"].values():
            self.assertRegex(value, r"^\$\{\{ steps\.pr\.outputs\.[a-z_]+ \}\}$")
        self.assertFalse(set(gate.STEPS["checks"].env) & set(gate.TOKENS))

    def test_the_step_that_asks_another_host_is_given_no_token(self):
        step = workflows.step("watch.yml", "watch", "The release manifest of main")
        self.assertEqual((step["run"], step["env"]), ("python3 -m engine.checks.night manifest", {}))
        self.assertEqual(night.STEPS["manifest"].env, ())

    def test_the_notion_key_reaches_one_step_and_that_step_holds_no_other(self):
        holders = [(name, step["name"]) for name, _job, step in every_step()
                   if any("secrets." in value for value in step["env"].values())]
        self.assertEqual(holders, [("backup.yml", "Copy Notion")])
        self.assertEqual(workflows.step("backup.yml", "backup", "Copy Notion")["env"],
                         {"NOTION_TOKEN": "${{ secrets.NOTION_TOKEN }}"})
        for name in workflows.NAMES:
            self.assertEqual(workflows.text(name).count("secrets."), 1 if name == "backup.yml" else 0, name)

    def test_githubs_token_is_only_ever_a_steps_gh_token(self):
        for name, _job, step in every_step():
            for key, value in step["env"].items():
                if "github.token" in value:
                    self.assertEqual((key, value), ("GH_TOKEN", "${{ github.token }}"), f"{name}: {step['name']}")
            self.assertNotIn("github.token", " ".join(step["with"].values()), name)

    def test_the_rights_are_exactly_these_and_none_by_default(self):
        seen = {}
        for name in workflows.NAMES:
            self.assertEqual(workflows.text(name).count("\npermissions: {}\n"), 1, name)
            for job, parts in workflows.jobs(name).items():
                seen[(name, job)] = parts["permissions"]
        self.assertEqual(seen, RIGHTS)

    def test_every_job_runs_mains_copy_of_its_file_only(self):
        for name in workflows.NAMES:
            for job, parts in workflows.jobs(name).items():
                if (name, job) == ("check.yml", "merge"):
                    self.assertEqual(parts["keys"]["needs"], "check")         # it runs only after job `check` of its own run
                else:
                    self.assertEqual(parts["keys"]["if"], GATE_IF if name == "check.yml" else MAIN_ONLY, name)
                self.assertEqual(parts["keys"]["runs-on"], "ubuntu-24.04", name)
                self.assertEqual(parts["steps"][0 if (name, job) != ("check.yml", "check") else 1]["uses"], CHECKOUT)


class GateMarkedFirst(unittest.TestCase):
    """A run of the gate that fails early never leaves the head's earlier `gate` standing.

    «Gate pending» and «Gate failed» both took the head from «The PR as data», so a run that
    failed before that step — the checkout, reading the PR, fetching its objects — set no status
    at all, and an earlier `success` of the same head (one from before an edit of the body, say)
    stayed. So the first step marks the event's head pending before anything is read, and «Gate
    failed» falls back to the event's head. A dispatch carries no head: it is marked once its PR
    is read."""

    def setUp(self):
        self.steps = workflows.steps("check.yml", "check")
        self.names = [step["name"] for step in self.steps]

    def step(self, name: str) -> dict:
        return workflows.step("check.yml", "check", name)

    def test_the_first_step_marks_the_events_head_pending(self):
        first = self.steps[0]
        self.assertEqual(first["name"], "Gate pending at once")
        self.assertEqual(first["if"], "github.event_name == 'pull_request_target'")
        self.assertEqual(first["env"], {"GH_TOKEN": "${{ github.token }}", "HEAD": "${{ github.event.pull_request.head.sha }}"})
        self.assertIn('statuses/$HEAD" -f state=pending -f context=gate', first["run"])

    def test_nothing_is_read_or_checked_out_before_it(self):
        first = self.names.index("Gate pending at once")
        self.assertLess(first, self.names.index("Code from main — the commit whose copy of this workflow runs"))
        self.assertLess(first, self.names.index("The PR as data"))
        self.assertNotIn("uses", self.steps[0], "no code of the repository is needed to mark the head")

    def test_a_failed_run_falls_back_to_the_events_head(self):
        failed = self.step("Gate failed")
        self.assertEqual(failed["if"], "failure() && (steps.pr.outputs.head != '' || github.event_name == 'pull_request_target')")
        self.assertEqual(failed["env"]["HEAD"], "${{ steps.pr.outputs.head || github.event.pull_request.head.sha }}")
        self.assertIn('statuses/$HEAD" -f state=failure -f context=gate', failed["run"])
        self.assertEqual(self.names[-1], "Gate failed", "after every step that can fail")

    def test_a_dispatch_is_still_marked_once_its_pr_is_read(self):
        later = self.step("Gate pending")
        self.assertGreater(self.names.index("Gate pending"), self.names.index("The PR as data"))
        self.assertEqual(later["env"]["HEAD"], "${{ steps.pr.outputs.head }}")
        self.assertNotIn("if", later)

    def test_the_verdict_is_written_only_after_the_checks_and_the_watch_and_for_the_head_that_was_read(self):
        passed = self.step("Gate passed")
        self.assertNotIn("if", passed, "it runs only when no step before it failed")
        self.assertEqual(passed["env"]["HEAD"], "${{ steps.pr.outputs.head }}")
        self.assertGreater(self.names.index("Gate passed"), self.names.index("The watch of main, inside the gate"))
        self.assertGreater(self.names.index("The watch of main, inside the gate"), self.names.index("The checks of the PR"))


class OneGroup(unittest.TestCase):
    """The watch and the copy share one concurrency group, and no waiting run is cancelled by a
    newer one: the watch does not read the copy's branch while the copy pushes to it, and a push
    to main cannot cancel a night's copy."""

    @staticmethod
    def concurrency(name: str) -> dict[str, str]:
        """The top-level `concurrency:` block as key → value (comments left out)."""
        lines = workflows.text(name).splitlines()
        out = {}
        for line in lines[lines.index("concurrency:") + 1:]:
            if line and not line.startswith(" "):
                break
            found = re.match(r"\s+([a-z-]+):\s*(\S+)", line)
            if found:
                out[found.group(1)] = found.group(2)
        return out

    def test_the_copy_and_the_watch_wait_for_each_other_and_a_newer_run_cancels_none(self):
        watch, backup = self.concurrency("watch.yml"), self.concurrency("backup.yml")
        self.assertEqual(watch, backup)
        self.assertEqual(watch, {"group": "notion-copy", "cancel-in-progress": "false", "queue": "max"})

    def test_a_newer_check_of_a_pr_replaces_the_older_and_merges_go_one_at_a_time(self):
        self.assertEqual(self.concurrency("check.yml"), {"group": "check-${{", "cancel-in-progress": "true"})
        self.assertIn("\n    concurrency:\n      group: merge-main\n      cancel-in-progress: false\n",
                      workflows.text("check.yml"))


class KnownBy(unittest.TestCase):
    def test_n1_knows_a_lawful_check_by_this_file_this_job_and_this_line(self):
        self.assertEqual(integrity.CHECK_WORKFLOW, ".github/workflows/check.yml")
        self.assertEqual(release.INSTALLED_WORKFLOWS + "check.yml", integrity.CHECK_WORKFLOW)
        self.assertEqual(list(workflows.jobs("check.yml")), ["check", "merge"])
        self.assertIn("\nname: check\n", workflows.text("check.yml"))
        self.assertEqual(list(workflows.jobs("check.yml")["check"]["outputs"]), ["number", "head", "base", "body_sha"])

    def test_the_gate_is_started_by_its_files_name(self):
        class Api:
            repository = "owner/repo"

            def post_no_content(self, path, body):
                self.sent = (path, body)

        for start in (update.start_gate, gate.start_check):
            api = Api()
            start(api, 7)
            self.assertEqual(api.sent, ("repos/owner/repo/actions/workflows/check.yml/dispatches",
                                        {"ref": "main", "inputs": {"pr": "7"}}))
        self.assertIn("\n  workflow_dispatch:\n    inputs:\n      pr:\n", workflows.text("check.yml"))

    def test_the_steps_run_in_the_order_the_gate_depends_on(self):
        names = [s["name"] for s in workflows.steps("check.yml", "check")]
        self.assertEqual(names, ["Gate pending at once", "Code from main — the commit whose copy of this workflow runs",
                                 "The PR as data", "Gate pending", "The checks of the PR", "The watch of main, inside the gate",
                                 "Gate passed", "Gate failed"])
        self.assertEqual([s["name"] for s in workflows.steps("check.yml", "merge")][1:],
                         ["Merge exactly the head and the record that check verified, onto the main it read"])
        self.assertEqual([s.get("id") for s in workflows.steps("backup.yml", "backup")][1:],
                         ["worktree", "copy", "push", None])
        self.assertEqual([s["name"] for s in workflows.steps("watch.yml", "watch")][1:],
                         ["The release manifest of main", "The watch of main", "Alarms"])
        for name, job in (("watch.yml", "watch"), ("backup.yml", "backup")):
            last = workflows.steps(name, job)[-1]
            self.assertEqual((last["name"], last["if"]), ("Alarms", "always()"), "a night that failed is an alarm too")


if __name__ == "__main__":
    unittest.main()

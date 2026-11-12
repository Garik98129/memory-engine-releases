"""The gate's workflow (engine/workflows/check.yml): a run that fails early never leaves the head's
earlier `gate` standing.

«Gate pending» and «Gate failed» both took the head from «The PR as data», so a run that failed before
that step — the checkout, reading the PR, fetching its objects — set no status at all, and an earlier
`success` of the same head (one from before an edit of the body, say) stayed. Now the first step marks
the event's head pending before anything is read, and «Gate failed» falls back to the event's head. A
dispatch carries no head: it is marked once its PR is read, as before."""
from __future__ import annotations

import unittest

from engine.backup.tests.test_workflow import WORKFLOWS, step_run

CHECK = WORKFLOWS / "check.yml"


def job_steps(job: str) -> list[tuple[str, str]]:
    """The steps of `job` in order: (name, the step's lines joined)."""
    lines = CHECK.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"  {job}:")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("  ") and not lines[i].startswith("   ")
                and lines[i].strip()), len(lines))
    steps: list[tuple[str, list[str]]] = []
    for line in lines[start:end]:
        if line.startswith("      - name: "):
            steps.append((line.split("- name: ", 1)[1].strip(), []))
        elif steps:
            steps[-1][1].append(line.strip())
    return [(name, "\n".join(body)) for name, body in steps]


class GateMarkedFirst(unittest.TestCase):
    def setUp(self):
        self.steps = job_steps("check")
        self.names = [name for name, _ in self.steps]
        self.block = dict(self.steps)

    def test_the_first_step_marks_the_events_head_pending(self):
        self.assertEqual(self.names[0], "Gate pending at once")
        block = self.block["Gate pending at once"]
        self.assertIn("if: github.event_name == 'pull_request_target'", block)
        self.assertIn("HEAD: ${{ github.event.pull_request.head.sha }}", block)
        command = step_run("Gate pending at once", CHECK)
        self.assertIn('statuses/$HEAD" -f state=pending -f context=gate', command)

    def test_nothing_is_read_before_it(self):
        first = self.names.index("Gate pending at once")
        self.assertLess(first, self.names.index("Code from main — the commit whose copy of this workflow runs"))
        self.assertLess(first, self.names.index("The PR as data"))

    def test_a_failed_run_falls_back_to_the_events_head(self):
        block = self.block["Gate failed"]
        self.assertIn("if: failure() && (steps.pr.outputs.head != '' || github.event_name == 'pull_request_target')",
                      block)
        self.assertIn("HEAD: ${{ steps.pr.outputs.head || github.event.pull_request.head.sha }}", block)
        self.assertIn('statuses/$HEAD" -f state=failure -f context=gate', step_run("Gate failed", CHECK))

    def test_a_dispatch_is_still_marked_once_its_pr_is_read(self):
        later = self.names.index("Gate pending")
        self.assertGreater(later, self.names.index("The PR as data"))
        self.assertIn("HEAD: ${{ steps.pr.outputs.head }}", self.block["Gate pending"])
        self.assertNotIn("if:", self.block["Gate pending"])


if __name__ == "__main__":
    unittest.main()

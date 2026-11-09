"""Findings and the one-line-per-finding report (docs/DESIGN.md §11)."""
from __future__ import annotations

from dataclasses import dataclass

BLOCK = "BLOCK"     # a PR check: the PR does not merge
ALARM = "ALARM"     # a nightly check: an Issue for the owner and the trusted contact


@dataclass(frozen=True)
class Finding:
    check: str          # C1, C2, ..., N1, N5
    message: str        # one short English line for the maintainers
    effect: str = BLOCK

    def line(self) -> str:
        return f"{self.check} {self.effect} {self.message}"


def render(findings: list[Finding], ran: list[str], not_applicable: dict[str, str] | None = None,
           notes: list[str] | None = None, word: str = "block") -> str:
    """One line per finding, «Cn OK» for a check that ran clean, «Cn N/A reason» for
    one that does not apply, a NOTE for what a check cannot see — stated, never a
    silent green (P5)."""
    lines = [f.line() for f in findings]
    for check in ran:
        if not any(f.check == check for f in findings):
            lines.append(f"{check} OK")
    for check, reason in (not_applicable or {}).items():
        lines.append(f"{check} N/A {reason}")
    lines += notes or []
    lines.append(f"SUMMARY {word}={len(findings)} ran={','.join(ran)}")
    return "\n".join(lines)

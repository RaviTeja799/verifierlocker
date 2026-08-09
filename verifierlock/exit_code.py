"""Pure interpretation of pytest exit codes (Task 1.2).

This module implements `interpret_exit_code`, the single, load-bearing pure
function that maps a pytest process's exit code to a `ProbeOutcome` plus an
optional reason string. It has no knowledge of engines, worktrees, or
orchestration: given an int, it returns exactly one `(ProbeOutcome, reason)`
pair, deterministically.

Exit-code mapping (design.md, Probe_Runner / Exit_Code_Interpreter section):

| pytest exit code | Outcome      | Note                                    |
|-------------------|--------------|------------------------------------------|
| 0                 | ALL_PASSED   | the only pass (Req 8.1, 8.7)             |
| 1                 | TESTS_FAILED | Req 8.2                                  |
| 2                 | INCONCLUSIVE | abort signal; CLI aborts (Req 8.4)       |
| 3                 | INCONCLUSIVE | internal error, cite code (Req 8.5)      |
| 4                 | INCONCLUSIVE | usage error, cite code (Req 8.5)         |
| 5                 | INCONCLUSIVE | zero tests collected (Req 8.3)           |
| 6                 | INCONCLUSIVE | max-warnings limit (Req 8.6)             |
| any other int     | INCONCLUSIVE | unrecognised exit code                   |
"""

from __future__ import annotations

from . import reasons
from .types import ProbeOutcome


def interpret_exit_code(code: int) -> tuple[ProbeOutcome, str | None]:
    """Pure mapping of pytest exit codes to outcomes (Req 8).

    Exit code 0 is the ONLY code that classifies as ALL_PASSED (Req 8.1,
    8.7). Exit code 1 is TESTS_FAILED (Req 8.2). Exit code 2 signals an
    abort (Req 8.4); this pure function reports it as INCONCLUSIVE with the
    ABORT_SIGNAL reason so orchestration (built later) can detect it and
    abort the run without producing a verdict. Exit codes 3-6 and any other
    integer are INCONCLUSIVE with a reason that cites the exit code.
    """
    if code == 0:
        return ProbeOutcome.ALL_PASSED, None
    if code == 1:
        return ProbeOutcome.TESTS_FAILED, None
    if code == 2:
        return ProbeOutcome.INCONCLUSIVE, f"{reasons.ABORT_SIGNAL}:{code}"
    if code in (3, 4):
        return ProbeOutcome.INCONCLUSIVE, f"{reasons.PROBE_INTERNAL_ERROR}:{code}"
    if code == 5:
        return ProbeOutcome.INCONCLUSIVE, f"{reasons.NO_TESTS_COLLECTED}:{code}"
    if code == 6:
        return ProbeOutcome.INCONCLUSIVE, f"{reasons.MAX_WARNINGS_LIMIT}:{code}"
    return ProbeOutcome.INCONCLUSIVE, f"{reasons.UNRECOGNISED_EXIT_CODE}:{code}"

"""Shared frozen types and enums used across VerifierLock's deterministic
engine stages.

These types are intentionally minimal at this stage of the build (Task 1.1):
they define the shapes that later stages (exit-code interpretation, probe
running, verdict deciding, evidence recording) will consume and produce.
`ProbeOutcome` and `ProbeResult` are defined here per the design's
Probe_Runner and Exit_Code_Interpreter section; `Diff` is added in Task 4.1
as the minimal shared input type consumed by `File_Classifier.classify`.
Other components' types (VerdictInputs, etc.) are introduced in their own
tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProbeOutcome(Enum):
    """Classification of a single probe's result.

    A probe is classified ALL_PASSED if and only if the underlying pytest
    exit code is 0 (see `interpret_exit_code`, implemented in Task 1.2).
    """

    ALL_PASSED = "all_passed"
    TESTS_FAILED = "tests_failed"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class Diff:
    """The set of file paths changed between a base and head revision.

    Minimal for now: `File_Classifier.classify` (Task 4.1) only needs the
    changed paths. `Static_Analyzer.analyze` (Task 11) will later need
    hunk-level detail; extending this type then is expected and will not
    change `classify`'s contract, which only reads `changed_paths`.
    """

    changed_paths: tuple[str, ...]


@dataclass(frozen=True)
class ProbeResult:
    """The recorded outcome of a single probe execution.

    Frozen so that a probe's result cannot be mutated after capture,
    preserving determinism guarantees across the pipeline.
    """

    probe_id: str          # "P0", "P1", "P2", "P3"
    repetition: int        # 0 for non-P0 / first P0
    command: tuple[str, ...]
    exit_code: int | None  # None if terminated (timeout)
    outcome: ProbeOutcome
    collected: int
    passed: int
    failed: int
    skipped: int
    elapsed_seconds: float
    reason: str | None     # reason code + detail for INCONCLUSIVE
    worktree_path: str

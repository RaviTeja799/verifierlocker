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
class DiffHunk:
    """One hunk of a unified diff for a single file.

    Carries the three line kinds `Static_Analyzer.analyze` (Task 11) needs to
    reason about a change: `added_lines` are the lines present in head and
    absent in base (unified-diff `+` lines, with the leading `+` stripped),
    `removed_lines` are present in base and absent in head (`-` lines,
    stripped), and `context_lines` are the unchanged lines shown around the
    change (needed to tell, e.g., whether a removed `raise` sits inside a
    `@pytest.fixture`). `header` is the raw `@@ -a,b +c,d @@` hunk header and
    doubles as the finding's hunk-location string (Req 5.5).
    """

    header: str
    added_lines: tuple[str, ...] = ()
    removed_lines: tuple[str, ...] = ()
    context_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileDiff:
    """The unified-diff hunks for a single changed file."""

    path: str
    hunks: tuple[DiffHunk, ...] = ()


@dataclass(frozen=True)
class Diff:
    """The changes between a base and head revision.

    `changed_paths` is the flat set of changed file paths and is the only
    field `File_Classifier.classify` (Task 4.1) reads -- its contract is
    unchanged. `file_diffs` carries the optional hunk-level detail that
    `Static_Analyzer.analyze` (Task 11) inspects for weakening patterns; it
    defaults to empty so every existing `Diff(changed_paths=...)` construction
    keeps working. When a caller populates both, `file_diffs[*].path` are
    expected to be a subset of `changed_paths`, but neither this type nor
    `classify` enforce or depend on that.
    """

    changed_paths: tuple[str, ...]
    file_diffs: tuple[FileDiff, ...] = ()


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

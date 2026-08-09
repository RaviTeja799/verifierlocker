"""Smoke tests for the shared types/enums scaffolded in Task 1.1.

These are not property tests (those come with the pure functions that
consume these types, starting in Task 1.2); they only verify that the
package imports and the frozen dataclasses/enums construct and behave
as expected.
"""

from __future__ import annotations

import dataclasses

import pytest

from verifierlock import reasons
from verifierlock.types import ProbeOutcome, ProbeResult


def test_probe_outcome_members():
    assert {o.value for o in ProbeOutcome} == {
        "all_passed",
        "tests_failed",
        "inconclusive",
    }


def test_probe_outcome_all_passed_value():
    assert ProbeOutcome.ALL_PASSED.value == "all_passed"
    assert ProbeOutcome.TESTS_FAILED.value == "tests_failed"
    assert ProbeOutcome.INCONCLUSIVE.value == "inconclusive"


def test_probe_result_constructs():
    result = ProbeResult(
        probe_id="P0",
        repetition=0,
        command=("python", "-m", "pytest"),
        exit_code=0,
        outcome=ProbeOutcome.ALL_PASSED,
        collected=12,
        passed=12,
        failed=0,
        skipped=0,
        elapsed_seconds=3.41,
        reason=None,
        worktree_path="/tmp/verifierlock/run/worktrees/p0-rep0",
    )
    assert result.probe_id == "P0"
    assert result.outcome is ProbeOutcome.ALL_PASSED
    assert result.command == ("python", "-m", "pytest")


def test_probe_result_is_frozen():
    result = ProbeResult(
        probe_id="P1",
        repetition=0,
        command=("python", "-m", "pytest"),
        exit_code=1,
        outcome=ProbeOutcome.TESTS_FAILED,
        collected=5,
        passed=4,
        failed=1,
        skipped=0,
        elapsed_seconds=1.0,
        reason=None,
        worktree_path="/tmp/verifierlock/run/worktrees/p1",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.exit_code = 0  # type: ignore[misc]


def test_reason_codes_are_distinct_strings():
    names = [
        "BASELINE_REF_UNRESOLVED",
        "HEAD_REF_UNRESOLVED",
        "NOT_A_GIT_REPO",
        "HAS_SUBMODULES",
        "UNCLASSIFIABLE_FILE",
        "DEPS_UNDISCOVERABLE",
        "ENV_INCOMPATIBLE",
        "WORKTREE_CREATE_FAILED",
        "IMPORT_LIMITATION",
        "PROBE_TIMEOUT",
        "BASELINE_NOT_GREEN",
        "BASELINE_NONDETERMINISTIC",
        "COVERAGE_UNAVAILABLE",
        "HEAD_NOT_GREEN",
        "PROBE_INTERNAL_ERROR",
        "NO_TESTS_COLLECTED",
        "MAX_WARNINGS_LIMIT",
        "UNRECOGNISED_EXIT_CODE",
        "ABORT_SIGNAL",
        "P2_PASS_P3_FAIL",
        "P2_FAIL_P3_FAIL",
        "P2_FAIL_P3_PASS",
    ]
    values = [getattr(reasons, name) for name in names]
    # Every constant exists, is a non-empty string, and matches its own name.
    for name, value in zip(names, values):
        assert isinstance(value, str)
        assert value == name
    # No accidental duplicate values.
    assert len(values) == len(set(values))

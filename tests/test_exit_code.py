"""Unit tests for `interpret_exit_code` (Task 1.2).

Covers pytest exit codes 0, 1, 2, 3, 4, 5, 6, and arbitrary other integers,
and asserts that only exit code 0 classifies as ALL_PASSED.
"""

from __future__ import annotations

import pytest

from verifierlock import reasons
from verifierlock.exit_code import interpret_exit_code
from verifierlock.types import ProbeOutcome


def test_exit_code_0_is_all_passed_with_no_reason():
    outcome, reason = interpret_exit_code(0)
    assert outcome is ProbeOutcome.ALL_PASSED
    assert reason is None


def test_exit_code_1_is_tests_failed_with_no_reason():
    outcome, reason = interpret_exit_code(1)
    assert outcome is ProbeOutcome.TESTS_FAILED
    assert reason is None


def test_exit_code_2_is_inconclusive_with_abort_signal_reason():
    outcome, reason = interpret_exit_code(2)
    assert outcome is ProbeOutcome.INCONCLUSIVE
    assert reason is not None
    assert reasons.ABORT_SIGNAL in reason
    assert "2" in reason


@pytest.mark.parametrize("code", [3, 4])
def test_exit_codes_3_and_4_are_inconclusive_with_internal_error_reason_citing_code(code):
    outcome, reason = interpret_exit_code(code)
    assert outcome is ProbeOutcome.INCONCLUSIVE
    assert reason is not None
    assert reasons.PROBE_INTERNAL_ERROR in reason
    assert str(code) in reason


def test_exit_code_5_is_inconclusive_with_no_tests_collected_reason():
    outcome, reason = interpret_exit_code(5)
    assert outcome is ProbeOutcome.INCONCLUSIVE
    assert reason is not None
    assert reasons.NO_TESTS_COLLECTED in reason
    assert "5" in reason


def test_exit_code_6_is_inconclusive_with_max_warnings_limit_reason():
    outcome, reason = interpret_exit_code(6)
    assert outcome is ProbeOutcome.INCONCLUSIVE
    assert reason is not None
    assert reasons.MAX_WARNINGS_LIMIT in reason
    assert "6" in reason


@pytest.mark.parametrize("code", [7, 8, 100, -1, 255])
def test_other_integers_are_inconclusive_with_unrecognised_exit_code_reason(code):
    outcome, reason = interpret_exit_code(code)
    assert outcome is ProbeOutcome.INCONCLUSIVE
    assert reason is not None
    assert reasons.UNRECOGNISED_EXIT_CODE in reason
    assert str(code) in reason


@pytest.mark.parametrize("code", [1, 2, 3, 4, 5, 6, 7, 100, -1])
def test_only_exit_code_0_is_all_passed(code):
    outcome, _ = interpret_exit_code(code)
    assert outcome is not ProbeOutcome.ALL_PASSED


@pytest.mark.parametrize("code", [3, 4, 5, 6])
def test_codes_3_4_5_6_are_never_all_passed(code):
    outcome, _ = interpret_exit_code(code)
    assert outcome is ProbeOutcome.INCONCLUSIVE

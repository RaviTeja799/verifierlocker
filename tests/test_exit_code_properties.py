"""Property-based tests for `interpret_exit_code` (Task 1.3).

**Property 1: Exit-code interpretation is exact and total**

Uses Hypothesis (min 100 examples) to prove, across a wide range of
integers, that:

- pytest exit codes 3, 4, 5, and 6 NEVER classify as ALL_PASSED, and
- ONLY exit code 0 classifies as ALL_PASSED (i.e. outcome == ALL_PASSED
  if and only if code == 0), for any integer exit code.

It also exercises the full total mapping (Requirements 8.1, 8.2, 8.3, 8.5,
8.6, 8.7) with explicit example-based assertions for the special codes.
"""

from __future__ import annotations

from hypothesis import example, given, settings
from hypothesis import strategies as st

from verifierlock import reasons
from verifierlock.exit_code import interpret_exit_code
from verifierlock.types import ProbeOutcome

# A wide range of integers, including negative and large values, to prove
# totality of the mapping (every integer produces exactly one outcome).
exit_code_strategy = st.integers(min_value=-1_000_000, max_value=1_000_000)


@settings(max_examples=200)
@given(code=exit_code_strategy)
@example(code=0)
@example(code=1)
@example(code=2)
@example(code=3)
@example(code=4)
@example(code=5)
@example(code=6)
@example(code=-1)
@example(code=255)
def test_only_exit_code_zero_is_all_passed(code: int) -> None:
    """ONLY exit code 0 classifies as ALL_PASSED (Requirements 8.1, 8.7)."""
    outcome, _ = interpret_exit_code(code)
    assert (outcome is ProbeOutcome.ALL_PASSED) == (code == 0)


@settings(max_examples=200)
@given(code=st.sampled_from([3, 4, 5, 6]))
def test_codes_3_4_5_6_never_classify_as_passed(code: int) -> None:
    """Exit codes 3, 4, 5, and 6 NEVER classify as passed.

    Requirements 8.3, 8.5, 8.6: these codes are internal/usage errors, zero
    tests collected, or the max-warnings limit -- never a pass.
    """
    outcome, reason = interpret_exit_code(code)
    assert outcome is not ProbeOutcome.ALL_PASSED
    assert outcome is ProbeOutcome.INCONCLUSIVE
    assert reason is not None
    assert str(code) in reason


@settings(max_examples=200)
@given(code=exit_code_strategy)
def test_mapping_is_total_and_returns_exactly_one_pair(code: int) -> None:
    """`interpret_exit_code` is total: every integer maps to exactly one
    (ProbeOutcome, reason) pair, and the outcome is always a valid member
    of ProbeOutcome (Requirements 8.1-8.7 as a whole)."""
    result = interpret_exit_code(code)
    assert isinstance(result, tuple)
    assert len(result) == 2
    outcome, reason = result
    assert isinstance(outcome, ProbeOutcome)
    assert reason is None or isinstance(reason, str)


@settings(max_examples=200)
@given(code=exit_code_strategy)
def test_full_exit_code_mapping_matches_specification(code: int) -> None:
    """Full total mapping per Requirements 8.1, 8.2, 8.3, 8.5, 8.6, 8.7:

    0 -> ALL_PASSED; 1 -> TESTS_FAILED; 2 -> INCONCLUSIVE (abort signal);
    3, 4 -> INCONCLUSIVE (internal/usage error, cite code);
    5 -> INCONCLUSIVE (zero collected); 6 -> INCONCLUSIVE (max-warnings);
    any other integer -> INCONCLUSIVE (unrecognised exit code).
    """
    outcome, reason = interpret_exit_code(code)

    if code == 0:
        assert outcome is ProbeOutcome.ALL_PASSED
        assert reason is None
    elif code == 1:
        assert outcome is ProbeOutcome.TESTS_FAILED
        assert reason is None
    elif code == 2:
        assert outcome is ProbeOutcome.INCONCLUSIVE
        assert reasons.ABORT_SIGNAL in reason
        assert "2" in reason
    elif code in (3, 4):
        assert outcome is ProbeOutcome.INCONCLUSIVE
        assert reasons.PROBE_INTERNAL_ERROR in reason
        assert str(code) in reason
    elif code == 5:
        assert outcome is ProbeOutcome.INCONCLUSIVE
        assert reasons.NO_TESTS_COLLECTED in reason
        assert "5" in reason
    elif code == 6:
        assert outcome is ProbeOutcome.INCONCLUSIVE
        assert reasons.MAX_WARNINGS_LIMIT in reason
        assert "6" in reason
    else:
        assert outcome is ProbeOutcome.INCONCLUSIVE
        assert reasons.UNRECOGNISED_EXIT_CODE in reason
        assert str(code) in reason


@settings(max_examples=200)
@given(code=exit_code_strategy.filter(lambda c: c != 0))
def test_every_non_zero_code_is_never_all_passed(code: int) -> None:
    """Converse of the totality property: any code other than 0 is
    guaranteed to never classify as ALL_PASSED."""
    outcome, _ = interpret_exit_code(code)
    assert outcome is not ProbeOutcome.ALL_PASSED

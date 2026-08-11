"""Property-based tests for the pure `Verdict_Engine` (Tasks 6.2-6.6).

Covers five design correctness properties over `verifierlock.verdict`:

- **Property 2:** Exactly one verdict, deterministically.
- **Property 3:** Verdict rule ordering matches the specification.
- **Property 4:** VERIFIER_WEAKENED requires P2 all-passed.
- **Property 5:** Probe selection follows classification.
- **Property 19:** Baseline nondeterminism is detected from repeated P0.

All property tests use Hypothesis with a minimum of 100 examples. Generators
build `VerdictInputs` across the full field space (including `None` probes,
every coverage state, and disagreeing P0 repetitions) plus classification
results for the probe-selection helper.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from verifierlock import reasons
from verifierlock.classifier import (
    ClassificationResult,
    ClassifiedFile,
    FileClass,
)
from verifierlock.coverage import ChangedLine, CoverageResult
from verifierlock.types import ProbeOutcome
from verifierlock.verdict import (
    Verdict,
    VerdictInputs,
    decide,
    p2_p3_required,
)

# --- Shared generators -----------------------------------------------------

_probe_outcome = st.sampled_from(list(ProbeOutcome))
_optional_probe_outcome = st.one_of(st.none(), _probe_outcome)


@st.composite
def coverage_results(draw: st.DrawFn) -> CoverageResult:
    """Generate a `CoverageResult`: unavailable, or available with a mix of
    covered / uncovered changed lines (including the empty and all-covered
    edge cases)."""
    available = draw(st.booleans())
    if not available:
        return CoverageResult(
            lines=(),
            available=False,
            reason=f"{reasons.COVERAGE_UNAVAILABLE}:generated",
        )
    n = draw(st.integers(min_value=0, max_value=6))
    lines = tuple(
        ChangedLine(
            file=draw(st.sampled_from(["a.py", "pkg/b.py", "src/c.py"])),
            line=draw(st.integers(min_value=1, max_value=200)),
            covered=draw(st.booleans()),
        )
        for _ in range(n)
    )
    return CoverageResult(lines=lines, available=True, reason=None)


_optional_coverage = st.one_of(st.none(), coverage_results())


@st.composite
def verdict_inputs(draw: st.DrawFn) -> VerdictInputs:
    """Generate an arbitrary `VerdictInputs` across the whole field space."""
    return VerdictInputs(
        base_resolved=draw(st.booleans()),
        head_resolved=draw(st.booleans()),
        is_git_repo=draw(st.booleans()),
        has_submodules=draw(st.booleans()),
        unclassifiable_files=tuple(
            draw(st.lists(st.sampled_from(["x.bin", "y.png", "z.dat"]), max_size=3))
        ),
        has_production_change=draw(st.booleans()),
        has_test_or_verifier_change=draw(st.booleans()),
        p0_outcomes=tuple(
            draw(st.lists(_probe_outcome, min_size=2, max_size=4))
        ),
        p1=draw(_probe_outcome),
        p2=draw(_optional_probe_outcome),
        p3=draw(_optional_probe_outcome),
        required_probe_inconclusive=draw(st.booleans()),
        coverage=draw(_optional_coverage),
    )


# --- Reference rule ordering (independent transcription of the design table) ---


def _reference_decide(inputs: VerdictInputs) -> Verdict:
    """A deliberately linear re-transcription of the design's row 0a-11 table,
    returning only the `Verdict` (not the reason). Kept independent of the
    implementation so a transcription error in either surfaces as a mismatch.
    Encodes the same "assess vs unstable" baseline refinement (an INCONCLUSIVE
    P0 repetition is INCONCLUSIVE, not BASELINE_INVALID)."""
    # Rows 0a-0e
    if not inputs.base_resolved:
        return Verdict.BASELINE_INVALID
    if not inputs.head_resolved:
        return Verdict.INCONCLUSIVE
    if not inputs.is_git_repo:
        return Verdict.INCONCLUSIVE
    if inputs.has_submodules:
        return Verdict.INCONCLUSIVE
    if inputs.unclassifiable_files:
        return Verdict.INCONCLUSIVE

    # Row 1: baseline
    p0 = inputs.p0_outcomes
    if not p0 or ProbeOutcome.INCONCLUSIVE in p0:
        return Verdict.INCONCLUSIVE
    if len(set(p0)) > 1:
        return Verdict.BASELINE_INVALID
    if p0[0] is not ProbeOutcome.ALL_PASSED:
        return Verdict.BASELINE_INVALID

    # Row 2: P1 green
    if inputs.p1 is not ProbeOutcome.ALL_PASSED:
        return Verdict.INCONCLUSIVE

    # Rows 3-4: structural
    if not inputs.has_test_or_verifier_change:
        return Verdict.NO_VERIFIER_CHANGE
    if not inputs.has_production_change:
        return Verdict.VERIFIER_CHANGED_REVIEW_REQUIRED

    # Row 5: required probe inconclusive / missing
    if (
        inputs.required_probe_inconclusive
        or inputs.p2 is None
        or inputs.p3 is None
        or inputs.p2 is ProbeOutcome.INCONCLUSIVE
        or inputs.p3 is ProbeOutcome.INCONCLUSIVE
    ):
        return Verdict.INCONCLUSIVE

    p2, p3 = inputs.p2, inputs.p3
    # Rows 6-8
    if p2 is ProbeOutcome.ALL_PASSED and p3 is ProbeOutcome.TESTS_FAILED:
        return Verdict.VERIFIER_WEAKENED
    if p2 is ProbeOutcome.TESTS_FAILED and p3 is ProbeOutcome.TESTS_FAILED:
        return Verdict.VERIFIER_CHANGED_REVIEW_REQUIRED
    if p2 is ProbeOutcome.TESTS_FAILED and p3 is ProbeOutcome.ALL_PASSED:
        return Verdict.INDEPENDENT_EVIDENCE
    # Rows 9-11: both all-passed
    coverage = inputs.coverage
    if coverage is None or not coverage.available:
        return Verdict.INCONCLUSIVE
    if all(cl.covered for cl in coverage.lines):
        return Verdict.INDEPENDENT_EVIDENCE
    return Verdict.NO_INDEPENDENT_EVIDENCE


# --- Property 2: Exactly one verdict, deterministically --------------------


# Feature: verifierlock, Property 2: For any VerdictInputs, decide returns
# exactly one Verdict without raising, and two equal inputs always yield the
# identical verdict and reason (the function is pure and total).
@settings(max_examples=300)
@given(inputs=verdict_inputs())
def test_decide_returns_exactly_one_verdict_deterministically(
    inputs: VerdictInputs,
) -> None:
    verdict, reason = decide(inputs)

    # Exactly one member of the Verdict enum, and a non-empty reason string.
    assert isinstance(verdict, Verdict)
    assert isinstance(reason, str)
    assert reason != ""

    # Determinism: re-deciding the same (frozen) inputs yields identical output.
    assert decide(inputs) == (verdict, reason)

    # A separately-constructed but equal VerdictInputs yields the same result.
    same_inputs = VerdictInputs(
        base_resolved=inputs.base_resolved,
        head_resolved=inputs.head_resolved,
        is_git_repo=inputs.is_git_repo,
        has_submodules=inputs.has_submodules,
        unclassifiable_files=inputs.unclassifiable_files,
        has_production_change=inputs.has_production_change,
        has_test_or_verifier_change=inputs.has_test_or_verifier_change,
        p0_outcomes=inputs.p0_outcomes,
        p1=inputs.p1,
        p2=inputs.p2,
        p3=inputs.p3,
        required_probe_inconclusive=inputs.required_probe_inconclusive,
        coverage=inputs.coverage,
    )
    assert decide(same_inputs) == (verdict, reason)


# --- Property 3: Verdict rule ordering matches the specification -----------


# Feature: verifierlock, Property 3: For any VerdictInputs, the verdict returned
# by decide equals the verdict produced by the reference rule ordering (rows
# 0a-11); the first matching row wins.
@settings(max_examples=500)
@given(inputs=verdict_inputs())
def test_verdict_matches_reference_rule_ordering(inputs: VerdictInputs) -> None:
    verdict, _reason = decide(inputs)
    assert verdict is _reference_decide(inputs)


# Feature: verifierlock, Property 3 (reason spot-checks): the reason code
# attached to each pre-probe / baseline / structural row is the documented one.
@settings(max_examples=200)
@given(inputs=verdict_inputs())
def test_reason_codes_match_matched_row(inputs: VerdictInputs) -> None:
    verdict, reason = decide(inputs)

    if not inputs.base_resolved:
        assert (verdict, reason) == (
            Verdict.BASELINE_INVALID,
            reasons.BASELINE_REF_UNRESOLVED,
        )
    elif not inputs.head_resolved:
        assert (verdict, reason) == (
            Verdict.INCONCLUSIVE,
            reasons.HEAD_REF_UNRESOLVED,
        )
    elif not inputs.is_git_repo:
        assert (verdict, reason) == (Verdict.INCONCLUSIVE, reasons.NOT_A_GIT_REPO)
    elif inputs.has_submodules:
        assert (verdict, reason) == (Verdict.INCONCLUSIVE, reasons.HAS_SUBMODULES)
    elif inputs.unclassifiable_files:
        assert verdict is Verdict.INCONCLUSIVE
        assert reason.startswith(reasons.UNCLASSIFIABLE_FILE + ":")


# --- Property 4: VERIFIER_WEAKENED requires P2 all-passed ------------------


# Feature: verifierlock, Property 4: For any VerdictInputs in which P2 is not
# classified ALL_PASSED, decide never returns VERIFIER_WEAKENED, regardless of
# any other field.
@settings(max_examples=300)
@given(inputs=verdict_inputs())
def test_verifier_weakened_requires_p2_all_passed(inputs: VerdictInputs) -> None:
    verdict, _reason = decide(inputs)
    if inputs.p2 is not ProbeOutcome.ALL_PASSED:
        assert verdict is not Verdict.VERIFIER_WEAKENED
    # And whenever VERIFIER_WEAKENED IS produced, P2 must have been all-passed.
    if verdict is Verdict.VERIFIER_WEAKENED:
        assert inputs.p2 is ProbeOutcome.ALL_PASSED


# --- Property 5: Probe selection follows classification --------------------

_file_class = st.sampled_from(list(FileClass))


@st.composite
def classification_results(draw: st.DrawFn) -> ClassificationResult:
    """Generate a ClassificationResult with an arbitrary mix of file classes
    and some unclassifiable paths."""
    n = draw(st.integers(min_value=0, max_value=8))
    files = tuple(
        ClassifiedFile(path=f"f{i}.py", classification=draw(_file_class))
        for i in range(n)
    )
    unclassifiable = tuple(
        draw(st.lists(st.sampled_from(["u1.bin", "u2.png"]), max_size=2, unique=True))
    )
    return ClassificationResult(files=files, unclassifiable=unclassifiable)


# Feature: verifierlock, Property 5: For any classification of changed files,
# P2 and P3 are required if and only if at least one changed file is production
# source AND at least one changed file is test code or verifier configuration.
@settings(max_examples=300)
@given(classification=classification_results())
def test_probe_selection_follows_classification(
    classification: ClassificationResult,
) -> None:
    classes = {cf.classification for cf in classification.files}
    expected = (FileClass.PRODUCTION in classes) and (
        FileClass.TEST in classes or FileClass.VERIFIER_CONFIG in classes
    )
    assert p2_p3_required(classification) is expected


def test_probe_selection_examples() -> None:
    """Concrete boundary cases for the probe-selection helper."""
    prod = ClassifiedFile("src/a.py", FileClass.PRODUCTION)
    test = ClassifiedFile("tests/test_a.py", FileClass.TEST)
    cfg = ClassifiedFile("pytest.ini", FileClass.VERIFIER_CONFIG)

    # Production + test -> required.
    assert p2_p3_required(ClassificationResult((prod, test), ())) is True
    # Production + verifier config -> required.
    assert p2_p3_required(ClassificationResult((prod, cfg), ())) is True
    # Production only -> not required.
    assert p2_p3_required(ClassificationResult((prod,), ())) is False
    # Test only -> not required.
    assert p2_p3_required(ClassificationResult((test,), ())) is False
    # Verifier config only -> not required.
    assert p2_p3_required(ClassificationResult((cfg,), ())) is False
    # Empty -> not required.
    assert p2_p3_required(ClassificationResult((), ())) is False


# --- Property 19: Baseline nondeterminism is detected from repeated P0 ------


def _valid_pre_probe_inputs(
    p0_outcomes: tuple[ProbeOutcome, ...],
    **overrides: object,
) -> VerdictInputs:
    """Build a VerdictInputs that clears every pre-probe short-circuit so the
    baseline row (row 1) is the first rule that can match."""
    base = dict(
        base_resolved=True,
        head_resolved=True,
        is_git_repo=True,
        has_submodules=False,
        unclassifiable_files=(),
        has_production_change=True,
        has_test_or_verifier_change=True,
        p0_outcomes=p0_outcomes,
        p1=ProbeOutcome.ALL_PASSED,
        p2=ProbeOutcome.ALL_PASSED,
        p3=ProbeOutcome.ALL_PASSED,
        required_probe_inconclusive=False,
        coverage=CoverageResult(lines=(), available=True, reason=None),
    )
    base.update(overrides)
    return VerdictInputs(**base)  # type: ignore[arg-type]


# Feature: verifierlock, Property 19: For any two assessed P0 repetition
# outcomes, if they differ then decide produces BASELINE_INVALID with the
# nondeterministic-baseline reason.
@settings(max_examples=200)
@given(
    a=st.sampled_from([ProbeOutcome.ALL_PASSED, ProbeOutcome.TESTS_FAILED]),
    b=st.sampled_from([ProbeOutcome.ALL_PASSED, ProbeOutcome.TESTS_FAILED]),
    extra=st.lists(
        st.sampled_from([ProbeOutcome.ALL_PASSED, ProbeOutcome.TESTS_FAILED]),
        max_size=2,
    ),
)
def test_disagreeing_assessed_p0_is_nondeterministic(
    a: ProbeOutcome, b: ProbeOutcome, extra: list[ProbeOutcome]
) -> None:
    p0 = (a, b, *extra)
    inputs = _valid_pre_probe_inputs(p0)
    verdict, reason = decide(inputs)
    if len(set(p0)) > 1:
        # Repetitions disagree -> nondeterministic baseline (Req 8c.2).
        assert verdict is Verdict.BASELINE_INVALID
        assert reason == reasons.BASELINE_NONDETERMINISTIC
    else:
        # All identical and assessed: green -> proceeds; not-green -> not-green.
        if a is ProbeOutcome.ALL_PASSED:
            assert verdict is not Verdict.BASELINE_INVALID
        else:
            assert (verdict, reason) == (
                Verdict.BASELINE_INVALID,
                reasons.BASELINE_NOT_GREEN,
            )


def test_all_passed_p0_repetitions_are_valid_baseline() -> None:
    """Agreeing, green P0 repetitions do not produce BASELINE_INVALID."""
    inputs = _valid_pre_probe_inputs(
        (ProbeOutcome.ALL_PASSED, ProbeOutcome.ALL_PASSED)
    )
    verdict, _reason = decide(inputs)
    assert verdict is not Verdict.BASELINE_INVALID


def test_reproducibly_not_green_p0_is_baseline_not_green() -> None:
    """Agreeing but failing P0 repetitions -> BASELINE_INVALID / not-green."""
    inputs = _valid_pre_probe_inputs(
        (ProbeOutcome.TESTS_FAILED, ProbeOutcome.TESTS_FAILED)
    )
    assert decide(inputs) == (Verdict.BASELINE_INVALID, reasons.BASELINE_NOT_GREEN)


def test_inconclusive_p0_repetition_is_not_assessed_inconclusive() -> None:
    """An INCONCLUSIVE P0 repetition means the baseline was never assessed:
    INCONCLUSIVE (BASELINE_NOT_ASSESSED), NOT BASELINE_INVALID (decision log
    4.7 / design Error Handling 'assess vs unstable')."""
    inputs = _valid_pre_probe_inputs(
        (ProbeOutcome.ALL_PASSED, ProbeOutcome.INCONCLUSIVE)
    )
    assert decide(inputs) == (
        Verdict.INCONCLUSIVE,
        reasons.BASELINE_NOT_ASSESSED,
    )

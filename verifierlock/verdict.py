"""Pure, deterministic Verdict_Engine (Task 6.1).

Implements `decide`, the total first-match rule ordering (rows 0a-11 from
design.md's Verdict_Engine table) that turns a bundle of already-computed
inputs into exactly one `(Verdict, reason)` pair (Req 10.1, 10.16). No
language model participates; the function is pure and total, so identical
inputs always yield the identical verdict and reason (Req 13.1, 10.16).

Also implements the probe-selection helper `p2_p3_required` (Req 6.1, 10.14,
10.15): P2 and P3 are required for a run if and only if at least one changed
file is production source AND at least one changed file is test code or
verifier configuration.

## Rule ordering (first matching row wins)

| # | Condition | Verdict | Req |
|---|---|---|---|
| 0a | base ref unresolved | BASELINE_INVALID (`BASELINE_REF_UNRESOLVED`) | 1.3 |
| 0b | head ref unresolved | INCONCLUSIVE (`HEAD_REF_UNRESOLVED`) | 1.4 |
| 0c | not a git repo | INCONCLUSIVE (`NOT_A_GIT_REPO`) | 2.2 |
| 0d | has submodules | INCONCLUSIVE (`HAS_SUBMODULES`) | 2.3 |
| 0e | any unclassifiable file | INCONCLUSIVE (`UNCLASSIFIABLE_FILE`, cite path) | 4b.5 |
| 1  | P0 not all-passed OR P0 reps disagree | BASELINE_INVALID | 8b.1, 8c.2, 10.2 |
| 2  | P1 not all-passed | INCONCLUSIVE (`HEAD_NOT_GREEN`) | 10.3 |
| 3  | no test or verifier-config change | NO_VERIFIER_CHANGE | 10.4 |
| 4  | no production-source change | VERIFIER_CHANGED_REVIEW_REQUIRED | 10.5 |
| 5  | any required probe INCONCLUSIVE | INCONCLUSIVE (aggregated) | 10.6 |
| 6  | P2 all-passed AND P3 tests-failed | VERIFIER_WEAKENED | 10.7, 10.13 |
| 7  | P2 tests-failed AND P3 tests-failed | VERIFIER_CHANGED_REVIEW_REQUIRED | 10.8 |
| 8  | P2 tests-failed AND P3 all-passed | INDEPENDENT_EVIDENCE | 10.9 |
| 9  | P2 & P3 all-passed AND all changed lines covered | INDEPENDENT_EVIDENCE | 10.10 |
| 10 | P2 & P3 all-passed AND some changed line uncovered | NO_INDEPENDENT_EVIDENCE | 10.11 |
| 11 | P2 & P3 all-passed AND coverage undetermined | INCONCLUSIVE (`COVERAGE_UNAVAILABLE`) | 10.12 |

## Row 1 refinement: "assess vs unstable" (decision log 4.7)

Requirement 10.2 read literally would make an INCONCLUSIVE P0 (which is "not
classified as all tests passed") BASELINE_INVALID. The design's Error Handling
section overrides that: BASELINE_INVALID is reserved for a baseline that *was*
assessed and found not-green (`BASELINE_NOT_GREEN`) or nondeterministic
(`BASELINE_NONDETERMINISTIC`). A baseline that could not be *assessed* at all
(an INCONCLUSIVE P0 repetition -- a timeout, or a base environment that could
not be built) is INCONCLUSIVE (`BASELINE_NOT_ASSESSED`), not BASELINE_INVALID.
So the P0 stage checks INCONCLUSIVE FIRST, and only then distinguishes
disagreement (nondeterministic) from a reproducible not-green baseline.

## Design-note: `is_git_repo` / `has_submodules` instead of `repo_supported`

design.md's illustrative `VerdictInputs` sketch collapses repository support
into a single `repo_supported: bool`. This module intentionally carries the two
underlying booleans `is_git_repo` and `has_submodules` instead, because
Requirements 2.2 and 2.3 mandate DISTINCT reason codes (`NOT_A_GIT_REPO` vs
`HAS_SUBMODULES`) for what is otherwise the same INCONCLUSIVE verdict; a single
boolean cannot express which reason applies. Both still map to INCONCLUSIVE, so
the verdict ordering is unchanged -- only the recorded reason is more precise.

## Totality

`decide` never raises and always returns exactly one verdict (Property 2). The
pre-probe rows 0a-0e and the baseline/P1/structural rows 1-4 are simple guards.
By the time rows 6-11 are reached, rows 3-4 guarantee both a production change
and a test/verifier change exist (so P2/P3 are structurally required), and row 5
has already caught any missing (`None`) or INCONCLUSIVE P2/P3. Rows 6-11
therefore see `p2, p3 in {ALL_PASSED, TESTS_FAILED}`, whose four combinations
are exhaustively handled. Row 5 additionally guards defensively against
inconsistent inputs (a `None`/INCONCLUSIVE probe with `required_probe_inconclusive`
left False), so no input can fall through without a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import reasons
from .classifier import ClassificationResult, FileClass
from .coverage import CoverageResult
from .types import ProbeOutcome


class Verdict(Enum):
    """The seven mutually-exclusive run verdicts (Req 10, Glossary)."""

    INDEPENDENT_EVIDENCE = "INDEPENDENT_EVIDENCE"
    NO_INDEPENDENT_EVIDENCE = "NO_INDEPENDENT_EVIDENCE"
    NO_VERIFIER_CHANGE = "NO_VERIFIER_CHANGE"
    VERIFIER_WEAKENED = "VERIFIER_WEAKENED"
    VERIFIER_CHANGED_REVIEW_REQUIRED = "VERIFIER_CHANGED_REVIEW_REQUIRED"
    INCONCLUSIVE = "INCONCLUSIVE"
    BASELINE_INVALID = "BASELINE_INVALID"


@dataclass(frozen=True)
class VerdictInputs:
    """Everything `decide` needs, already computed by upstream stages.

    Frozen so the same inputs cannot be mutated between stages, protecting the
    determinism guarantee (Req 10.16). See the module docstring for why
    repository support is carried as the two booleans `is_git_repo` /
    `has_submodules` rather than a single `repo_supported`.

    `p0_outcomes` holds the outcome of each P0 repetition (there are at least
    two, Req 8c.1). `p2` / `p3` are `None` when the probe was not required for
    the run (Req 10.14, 10.15). `coverage` is `None` when no coverage result is
    available. `required_probe_inconclusive` is set by the orchestrator when a
    structurally-required probe (P2/P3) came back INCONCLUSIVE; the engine also
    defends against `None`/INCONCLUSIVE probes directly so it stays total even
    on inconsistent inputs.
    """

    base_resolved: bool
    head_resolved: bool
    is_git_repo: bool
    has_submodules: bool
    unclassifiable_files: tuple[str, ...]
    has_production_change: bool
    has_test_or_verifier_change: bool
    p0_outcomes: tuple[ProbeOutcome, ...]
    p1: ProbeOutcome
    p2: ProbeOutcome | None
    p3: ProbeOutcome | None
    required_probe_inconclusive: bool
    coverage: CoverageResult | None


def p2_p3_required(classification: ClassificationResult) -> bool:
    """Probe-selection helper (Req 6.1, 10.14, 10.15, Property 5).

    P2 and P3 are required for a run if and only if at least one changed file is
    production source AND at least one changed file is test code or verifier
    configuration. P0 and P1 are always required and are not decided here.
    Unclassifiable paths never count toward either side of the conjunction.
    """
    classes = {cf.classification for cf in classification.files}
    has_production = FileClass.PRODUCTION in classes
    has_test_or_verifier = (
        FileClass.TEST in classes or FileClass.VERIFIER_CONFIG in classes
    )
    return has_production and has_test_or_verifier


def _baseline_verdict(
    p0_outcomes: tuple[ProbeOutcome, ...],
) -> tuple[Verdict, str] | None:
    """Row 1: evaluate the P0 baseline, or return None if it is valid.

    Applies the "assess vs unstable" distinction (decision log 4.7): an
    INCONCLUSIVE (or missing) repetition means the baseline was never assessed
    -> INCONCLUSIVE (`BASELINE_NOT_ASSESSED`); differing assessed outcomes mean
    a nondeterministic baseline -> BASELINE_INVALID (`BASELINE_NONDETERMINISTIC`,
    Req 8c.2); a reproducible not-green baseline -> BASELINE_INVALID
    (`BASELINE_NOT_GREEN`, Req 8b.1). Returns None when the baseline is
    reproducibly all-passed and the run may proceed.
    """
    if not p0_outcomes or ProbeOutcome.INCONCLUSIVE in p0_outcomes:
        return Verdict.INCONCLUSIVE, reasons.BASELINE_NOT_ASSESSED
    if len(set(p0_outcomes)) > 1:
        # Repetitions disagree (e.g. one ALL_PASSED, one TESTS_FAILED): the
        # baseline suite is nondeterministic (Req 8c.2, Property 19).
        return Verdict.BASELINE_INVALID, reasons.BASELINE_NONDETERMINISTIC
    if p0_outcomes[0] is not ProbeOutcome.ALL_PASSED:
        # All repetitions agree but are not green (all TESTS_FAILED).
        return Verdict.BASELINE_INVALID, reasons.BASELINE_NOT_GREEN
    return None


def _required_probe_inconclusive_reason(inputs: VerdictInputs) -> str:
    """Build the aggregated reason for row 5, naming the offending probe(s)."""
    offending: list[str] = []
    if inputs.p2 is None or inputs.p2 is ProbeOutcome.INCONCLUSIVE:
        offending.append("P2")
    if inputs.p3 is None or inputs.p3 is ProbeOutcome.INCONCLUSIVE:
        offending.append("P3")
    if not offending:
        # required_probe_inconclusive was set by the orchestrator without a
        # locally-visible INCONCLUSIVE probe; report the bare code.
        return reasons.REQUIRED_PROBE_INCONCLUSIVE
    return f"{reasons.REQUIRED_PROBE_INCONCLUSIVE}:{'+'.join(offending)}"


def decide(inputs: VerdictInputs) -> tuple[Verdict, str]:
    """Pure, total verdict decision (Req 10.1, 10.16).

    Returns exactly one `(Verdict, reason)` pair by applying the rule ordering
    in the module docstring; the first matching row wins. Never raises.
    """
    # --- Pre-probe short-circuits (rows 0a-0e) ---
    if not inputs.base_resolved:
        return Verdict.BASELINE_INVALID, reasons.BASELINE_REF_UNRESOLVED  # 0a
    if not inputs.head_resolved:
        return Verdict.INCONCLUSIVE, reasons.HEAD_REF_UNRESOLVED           # 0b
    if not inputs.is_git_repo:
        return Verdict.INCONCLUSIVE, reasons.NOT_A_GIT_REPO                # 0c
    if inputs.has_submodules:
        return Verdict.INCONCLUSIVE, reasons.HAS_SUBMODULES                # 0d
    if inputs.unclassifiable_files:
        cited = inputs.unclassifiable_files[0]
        return Verdict.INCONCLUSIVE, f"{reasons.UNCLASSIFIABLE_FILE}:{cited}"  # 0e

    # --- Row 1: baseline validity ---
    baseline = _baseline_verdict(inputs.p0_outcomes)
    if baseline is not None:
        return baseline

    # --- Row 2: head must be green ---
    if inputs.p1 is not ProbeOutcome.ALL_PASSED:
        return Verdict.INCONCLUSIVE, reasons.HEAD_NOT_GREEN

    # --- Rows 3-4: structural checks (before inconclusive aggregation) ---
    if not inputs.has_test_or_verifier_change:
        return Verdict.NO_VERIFIER_CHANGE, reasons.NO_TEST_OR_VERIFIER_CHANGE  # 3
    if not inputs.has_production_change:
        return (
            Verdict.VERIFIER_CHANGED_REVIEW_REQUIRED,
            reasons.NO_PRODUCTION_CHANGE,
        )  # 4

    # Rows 3-4 having passed, both a production change and a test/verifier change
    # exist, so P2 and P3 are structurally required for this run.

    # --- Row 5: any required probe INCONCLUSIVE (or missing) ---
    if (
        inputs.required_probe_inconclusive
        or inputs.p2 is None
        or inputs.p3 is None
        or inputs.p2 is ProbeOutcome.INCONCLUSIVE
        or inputs.p3 is ProbeOutcome.INCONCLUSIVE
    ):
        return Verdict.INCONCLUSIVE, _required_probe_inconclusive_reason(inputs)

    # From here p2, p3 in {ALL_PASSED, TESTS_FAILED}: the four combinations are
    # exhaustive (rows 6-11).
    p2, p3 = inputs.p2, inputs.p3

    # --- Row 6: VERIFIER_WEAKENED (reachable only with P2 all-passed, Req 10.13) ---
    if p2 is ProbeOutcome.ALL_PASSED and p3 is ProbeOutcome.TESTS_FAILED:
        return Verdict.VERIFIER_WEAKENED, reasons.P2_PASS_P3_FAIL
    # --- Row 7: both fail -> behaviour change, needs review ---
    if p2 is ProbeOutcome.TESTS_FAILED and p3 is ProbeOutcome.TESTS_FAILED:
        return Verdict.VERIFIER_CHANGED_REVIEW_REQUIRED, reasons.P2_FAIL_P3_FAIL
    # --- Row 8: base tests catch head source -> independent evidence ---
    if p2 is ProbeOutcome.TESTS_FAILED and p3 is ProbeOutcome.ALL_PASSED:
        return Verdict.INDEPENDENT_EVIDENCE, reasons.P2_FAIL_P3_PASS

    # --- Rows 9-11: P2 all-passed AND P3 all-passed -> coverage decides ---
    coverage = inputs.coverage
    if coverage is None or not coverage.available:
        return Verdict.INCONCLUSIVE, reasons.COVERAGE_UNAVAILABLE          # 11
    if all(changed_line.covered for changed_line in coverage.lines):
        return Verdict.INDEPENDENT_EVIDENCE, reasons.ALL_CHANGED_LINES_COVERED  # 9
    return Verdict.NO_INDEPENDENT_EVIDENCE, reasons.CHANGED_LINES_UNCOVERED     # 10


# --- Verdict-to-exit-code mapping (design "Verdict-to-Exit-Code Mapping", Req 15) ---

# The documented, distinct exit code for each verdict plus the aborted-no-verdict
# outcome. `0` is reserved for the clean verdict (INDEPENDENT_EVIDENCE), matching
# Unix convention. This is the single source of truth reused by the Evidence
# Record's `verdict.exit_code` and by the CLI (Task 18).
VERDICT_EXIT_CODES: dict[Verdict, int] = {
    Verdict.INDEPENDENT_EVIDENCE: 0,          # Req 15.2
    Verdict.NO_INDEPENDENT_EVIDENCE: 10,      # Req 15.3
    Verdict.NO_VERIFIER_CHANGE: 11,           # Req 15.4
    Verdict.VERIFIER_WEAKENED: 12,            # Req 15.5
    Verdict.VERIFIER_CHANGED_REVIEW_REQUIRED: 13,  # Req 15.6
    Verdict.INCONCLUSIVE: 14,                 # Req 15.7
    Verdict.BASELINE_INVALID: 15,             # Req 15.8
}

# The distinct process exit code used when a probe returned pytest exit code 2
# and the run aborted with no verdict (Req 8.4, 15.9). It is intentionally not a
# `Verdict`, since no verdict was produced.
ABORTED_NO_VERDICT_EXIT_CODE = 16


def verdict_exit_code(verdict: Verdict) -> int:
    """The documented process exit code for `verdict` (Req 15.1-15.8)."""
    return VERDICT_EXIT_CODES[verdict]


# --- Reason-code to matched-rule mapping (design Verdict_Engine table) -------

# Maps the reason code (the part of `decide`'s reason before any ``:detail``) to
# the rule row that produced it, recorded as `verdict.matched_rule` in the
# Evidence Record. Rows 0a-0e are the pre-probe short-circuits (string labels);
# rows 1-11 are the numbered rows. Both `BASELINE_NOT_ASSESSED` (the assess-vs-
# unstable refinement) and the two assessed-baseline codes belong to row 1.
_REASON_TO_RULE: dict[str, int | str] = {
    reasons.BASELINE_REF_UNRESOLVED: "0a",
    reasons.HEAD_REF_UNRESOLVED: "0b",
    reasons.NOT_A_GIT_REPO: "0c",
    reasons.HAS_SUBMODULES: "0d",
    reasons.UNCLASSIFIABLE_FILE: "0e",
    reasons.BASELINE_NOT_ASSESSED: 1,
    reasons.BASELINE_NONDETERMINISTIC: 1,
    reasons.BASELINE_NOT_GREEN: 1,
    reasons.HEAD_NOT_GREEN: 2,
    reasons.NO_TEST_OR_VERIFIER_CHANGE: 3,
    reasons.NO_PRODUCTION_CHANGE: 4,
    reasons.REQUIRED_PROBE_INCONCLUSIVE: 5,
    reasons.P2_PASS_P3_FAIL: 6,
    reasons.P2_FAIL_P3_FAIL: 7,
    reasons.P2_FAIL_P3_PASS: 8,
    reasons.ALL_CHANGED_LINES_COVERED: 9,
    reasons.CHANGED_LINES_UNCOVERED: 10,
    reasons.COVERAGE_UNAVAILABLE: 11,
}


def reason_code_of(reason: str) -> str:
    """The bare reason code (the part before any ``:detail`` suffix)."""
    return reason.split(":", 1)[0]


def matched_rule_of(reason: str) -> int | str | None:
    """The verdict-table rule row for `reason`, or None if unrecognised."""
    return _REASON_TO_RULE.get(reason_code_of(reason))

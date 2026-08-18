"""Shared helpers for the CLI / report / explanation tests (Task 18).

The CLI is a thin shell over the Orchestrator, so its argument handling,
artifact writing, exit-code policy, and explanation isolation are all testable
with an injected `run_pipeline` that returns a canned `OrchestratorResult`. That
keeps these tests fast and hermetic: no repository code is executed, and the
assertions are about the CLI's own behaviour rather than a probe outcome.
"""

from __future__ import annotations

from verifierlock.classifier import ClassificationResult, ClassifiedFile, FileClass
from verifierlock.coverage import ChangedLine, CoverageResult
from verifierlock.environment import BuiltEnv
from verifierlock.evidence import RunMetadata, build_full_evidence_record
from verifierlock.orchestrator import OrchestratorResult
from verifierlock.repository import RepoValidation
from verifierlock.static_analyzer import StaticFinding
from verifierlock.types import DiffHunk, FileDiff, ProbeOutcome, ProbeResult
from verifierlock.verdict import (
    ABORTED_NO_VERDICT_EXIT_CODE,
    Verdict,
    verdict_exit_code,
)

RUN_ROOT = "/tmp/verifierlock/run-fixed"

# A reason code per verdict, so the record carries a realistic reason/rule pair.
REASON_FOR_VERDICT = {
    Verdict.INDEPENDENT_EVIDENCE: "ALL_CHANGED_LINES_COVERED",
    Verdict.NO_INDEPENDENT_EVIDENCE: "CHANGED_LINES_UNCOVERED",
    Verdict.NO_VERIFIER_CHANGE: "NO_TEST_OR_VERIFIER_CHANGE",
    Verdict.VERIFIER_WEAKENED: "P2_PASS_P3_FAIL",
    Verdict.VERIFIER_CHANGED_REVIEW_REQUIRED: "P2_FAIL_P3_FAIL",
    Verdict.INCONCLUSIVE: "HEAD_NOT_GREEN",
    Verdict.BASELINE_INVALID: "BASELINE_NOT_GREEN",
}


def _probe(probe_id: str, repetition: int, outcome: ProbeOutcome) -> ProbeResult:
    worktree = f"{RUN_ROOT}/worktrees/{probe_id.lower()}-{repetition}"
    return ProbeResult(
        probe_id=probe_id,
        repetition=repetition,
        command=("/usr/bin/python", "-m", "pytest", "--rootdir", worktree),
        exit_code=0 if outcome is ProbeOutcome.ALL_PASSED else 1,
        outcome=outcome,
        collected=2,
        passed=2 if outcome is ProbeOutcome.ALL_PASSED else 1,
        failed=0 if outcome is ProbeOutcome.ALL_PASSED else 1,
        skipped=0,
        elapsed_seconds=1.5,
        reason=None if outcome is not ProbeOutcome.INCONCLUSIVE else "PROBE_TIMEOUT:timeout=1s",
        worktree_path=worktree,
    )


def make_record(
    verdict: Verdict,
    *,
    reason: str | None = None,
    aborted: bool = False,
) -> dict:
    """Build a complete, deterministic Evidence Record for `verdict`.

    Identical for identical arguments (fixed run id / timestamp), so two CLI
    invocations over it must produce a byte-identical reproducible core.
    """
    run = RunMetadata(
        run_id="run-fixed",
        timestamp="2026-01-01T00:00:00Z",
        repo_path="/repo",
        base_ref="main",
        head_ref="feature",
        base_commit="a" * 40,
        head_commit="b" * 40,
        timeout_seconds=600.0,
        run_root=RUN_ROOT,
    )
    classification = ClassificationResult(
        files=(
            ClassifiedFile(path="authz/__init__.py", classification=FileClass.PRODUCTION),
            ClassifiedFile(path="tests/test_authz.py", classification=FileClass.TEST),
        ),
        unclassifiable=(),
    )
    file_diffs = (
        FileDiff(
            path="authz/__init__.py",
            hunks=(DiffHunk(header="@@ -10,3 +10,3 @@", added_lines=("+ changed",)),),
        ),
    )
    record = build_full_evidence_record(
        run=run,
        validation=RepoValidation(
            is_git_repo=True, has_submodules=False, determination="supported"
        ),
        environments=(
            BuiltEnv(
                revision="base",
                tool="uv",
                python_path=None,
                discovery="pyproject.toml",
                built=True,
            ),
            BuiltEnv(
                revision="head",
                tool="uv",
                python_path=None,
                discovery="pyproject.toml",
                built=True,
            ),
        ),
        classification=classification,
        file_diffs=file_diffs,
        static_findings=(
            StaticFinding(
                kind="weakened_assertion",
                file="tests/test_authz.py",
                hunk="@@ -4,4 +4,4 @@",
                detail="assertion relaxed",
            ),
        ),
        probes=(
            _probe("P0", 0, ProbeOutcome.ALL_PASSED),
            _probe("P0", 1, ProbeOutcome.ALL_PASSED),
            _probe("P1", 0, ProbeOutcome.ALL_PASSED),
            _probe("P1-COV", 0, ProbeOutcome.ALL_PASSED),
            _probe("P2", 0, ProbeOutcome.ALL_PASSED),
            _probe("P3", 0, ProbeOutcome.TESTS_FAILED),
        ),
        coverage=CoverageResult(
            lines=(
                ChangedLine(file="authz/__init__.py", line=10, covered=True),
                ChangedLine(file="authz/__init__.py", line=11, covered=False),
            ),
            available=True,
            reason=None,
        ),
        verdict=verdict,
        reason=reason or REASON_FOR_VERDICT[verdict],
    )
    if aborted:
        record["verdict"] = {
            "value": "ABORTED_NO_VERDICT",
            "reason_code": "ABORT_SIGNAL",
            "reason": "ABORT_SIGNAL:probe P1 returned pytest exit code 2",
            "matched_rule": None,
            "exit_code": ABORTED_NO_VERDICT_EXIT_CODE,
        }
    return record


def make_result(verdict: Verdict | None) -> OrchestratorResult:
    """A canned `OrchestratorResult`; `verdict is None` means an aborted run."""
    if verdict is None:
        return OrchestratorResult(
            record=make_record(Verdict.INCONCLUSIVE, aborted=True),
            verdict=None,
            reason="ABORT_SIGNAL",
            exit_code=ABORTED_NO_VERDICT_EXIT_CODE,
            aborted=True,
        )
    return OrchestratorResult(
        record=make_record(verdict),
        verdict=verdict,
        reason=REASON_FOR_VERDICT[verdict],
        exit_code=verdict_exit_code(verdict),
        aborted=False,
    )


def pipeline_returning(verdict: Verdict | None, *, calls: list | None = None):
    """An injectable `run_pipeline` that returns a canned result for `verdict`.

    Appends each invocation's kwargs to `calls` (when given) so tests can assert
    what the CLI passed through, and builds a FRESH record per call so one test
    cannot observe another's mutations.
    """

    def run_pipeline(repo, **kwargs):
        if calls is not None:
            calls.append({"repo": repo, **kwargs})
        return make_result(verdict)

    return run_pipeline

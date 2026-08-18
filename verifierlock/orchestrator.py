"""Orchestrator: drive the fixed VerifierLock pipeline (Task 15.1).

The Orchestrator is the conductor of design.md's End-to-End Sequence. It wires
every deterministic-engine component together in a fixed order, applies the
pre-probe short-circuits (so no probe runs when the run can be decided up
front), selects P2/P3 from the file classification, runs P0/P1 plus the
instrumented P1 coverage run, runs the structurally-required P2/P3, feeds each
probe the right built environment, decides the single verdict, assembles the
full Evidence Record, and guarantees worktree cleanup on any exit.

## Pre-probe short-circuits (Req 1.3, 1.4, 2.2, 2.3, 4b.5)

Five conditions decide the run before any probe executes:

1. repository not a Git work tree / has submodules -> INCONCLUSIVE (rows 0c/0d);
2. base ref unresolved -> BASELINE_INVALID (row 0a);
3. head ref unresolved -> INCONCLUSIVE (row 0b);
4. any unclassifiable changed file -> INCONCLUSIVE (row 0e).

Each short-circuit still assembles a complete Evidence Record with the verdict
and the facts gathered so far, then returns; the worktree stage is never
entered, so no repository code runs (satisfying the Probe_Runner "SHALL NOT
execute any probe" requirement for these cases).

## Probe wiring and environment selection

P0 (base) and P3 run under the BASE environment; P1, its coverage run, and P2
run under the HEAD environment -- the environment follows the tests, not the
source (Req 4.4, 4.8). Each probe's PYTHONPATH is pinned to the worktree it
executes in so the on-disk source wins over site-packages (Concern 3). The
coverage run is a separate fifth run whose pass/fail never feeds the verdict
(Concern 2); only its Cobertura XML, mapped by the pure `map_coverage`, does.

## Determinism-engine boundary

No language model participates. Every field that feeds the verdict is computed
here or in a pure downstream stage, so identical inputs yield an identical
verdict (Req 10.16). The optional Explanation_Model (Task 18) reads the finished
record only.

The side-effecting collaborators (`Environment_Builder`, the worktree backend,
the probe launcher) are injectable so the pipeline can be driven with fakes;
they default to the real implementations for end-to-end runs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import gitio, reasons
from .classifier import ClassificationResult, FileClass, classify
from .coverage import CoverageResult, map_coverage
from .environment import BuiltEnv, Environment_Builder, probe_env
from .evidence import RunMetadata, build_full_evidence_record
from .probe import (
    Launcher,
    ProbeAbort,
    run_p1,
    run_p1_coverage,
    run_p2,
    run_p3,
    run_probe,
)
from .repository import RepoValidation, validate
from .revision import ResolvedRevisions, resolve
from .static_analyzer import analyze
from .types import Diff, ProbeOutcome, ProbeResult
from .verdict import (
    ABORTED_NO_VERDICT_EXIT_CODE,
    Verdict,
    VerdictInputs,
    decide,
    p2_p3_required,
    verdict_exit_code,
)
from .worktree import (
    P1_SLOT,
    P2_SLOT,
    P3_SLOT,
    Worktree_Manager,
    WorktreeBackend,
    WorktreeCreationError,
    p0_slot,
    worktree_failure_result,
    worktree_root,
)


@dataclass(frozen=True)
class OrchestratorResult:
    """The outcome of one pipeline run.

    `record` is the full Evidence Record dict. `verdict` is the decided
    `Verdict`, or `None` when the run aborted on pytest exit code 2 (Req 8.4);
    `exit_code` is then the distinct aborted-no-verdict code (Req 15.9).
    """

    record: dict
    verdict: Verdict | None
    reason: str | None
    exit_code: int
    aborted: bool


def run(
    repo: Path,
    base_ref: str,
    head_ref: str,
    *,
    timeout: float | None = None,
    install_cmd: str | None = None,
    run_id: str | None = None,
    p0_repetitions: int = 2,
    env_builder: Environment_Builder | None = None,
    worktree_backend: WorktreeBackend | None = None,
    launcher: Launcher | None = None,
    tool_version: str = "0.1.0",
) -> OrchestratorResult:
    """Run the full pipeline against `repo` and return the Evidence Record.

    Applies the pre-probe short-circuits, then (when the run proceeds) creates
    the per-slot worktrees, builds the base/head deps-only environments, runs
    P0xN + P1 + the P1 coverage run + structurally-required P2/P3, decides the
    verdict, and assembles the record. Worktrees are always cleaned up.
    """
    repo = Path(repo)
    run_id = run_id or str(uuid.uuid4())
    run_root = str(worktree_root(run_id).parent)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Repository validation (rows 0c/0d) ---
    repo_validation = validate(repo)
    if not repo_validation.is_git_repo or repo_validation.has_submodules:
        return _short_circuit(
            run_meta=_run_meta(run_id, timestamp, repo, base_ref, head_ref, None, None, timeout, run_root),
            validation=repo_validation,
            inputs=_prevalidation_inputs(
                base_resolved=True,
                head_resolved=True,
                validation=repo_validation,
            ),
            tool_version=tool_version,
        )

    # --- Revision resolution (rows 0a/0b) ---
    revisions = resolve(repo, base_ref, head_ref)
    run_meta = _run_meta(
        run_id, timestamp, repo, base_ref, head_ref,
        revisions.base_hash, revisions.head_hash, timeout, run_root,
    )
    if not revisions.base_resolved or not revisions.head_resolved:
        return _short_circuit(
            run_meta=run_meta,
            validation=repo_validation,
            inputs=_prevalidation_inputs(
                base_resolved=revisions.base_resolved,
                head_resolved=revisions.head_resolved,
                validation=repo_validation,
            ),
            tool_version=tool_version,
        )

    # --- Diff extraction + classification (row 0e) ---
    base_commit = revisions.base_hash
    head_commit = revisions.head_hash
    assert base_commit is not None and head_commit is not None

    changed = gitio.changed_paths(repo, base_commit, head_commit)
    file_diffs = gitio.parse_diff(repo, base_commit, head_commit)
    diff = Diff(changed_paths=changed, file_diffs=file_diffs)
    pytest_config = gitio.read_pytest_config(repo)
    classification = classify(diff, pytest_config)

    if classification.unclassifiable:
        cited = sorted(classification.unclassifiable)[0]
        inputs = _prevalidation_inputs(
            base_resolved=True,
            head_resolved=True,
            validation=repo_validation,
            unclassifiable_files=tuple(sorted(classification.unclassifiable)),
        )
        return _short_circuit(
            run_meta=run_meta,
            validation=repo_validation,
            inputs=inputs,
            classification=classification,
            file_diffs=file_diffs,
            tool_version=tool_version,
            _cited=cited,
        )

    # --- Probe stage ---
    env_builder = env_builder or Environment_Builder()
    probes: list[ProbeResult] = []
    environments: list[BuiltEnv] = []
    static_findings: tuple = ()
    coverage_result: CoverageResult | None = None

    production_paths = frozenset(
        cf.path for cf in classification.files if cf.classification is FileClass.PRODUCTION
    )
    test_paths = _graft_test_paths(pytest_config, classification, repo)
    p2p3 = p2_p3_required(classification)

    try:
        with Worktree_Manager(repo, run_id, backend=worktree_backend) as manager:
            # Create worktrees per slot (base commit: p0-rep*, p2; head: p1, p3).
            p0_handles = []
            p0_errors = []
            for i in range(p0_repetitions):
                handle, err = _create(manager, p0_slot(i), base_commit)
                p0_handles.append(handle)
                p0_errors.append(err)
            p1_handle, p1_err = _create(manager, P1_SLOT, head_commit)
            p2_handle = p3_handle = None
            p2_err = p3_err = None
            if p2p3:
                p2_handle, p2_err = _create(manager, P2_SLOT, base_commit)
                p3_handle, p3_err = _create(manager, P3_SLOT, head_commit)

            # Build environments from a pristine worktree of each revision.
            base_src = next((h for h in p0_handles if h is not None), None)
            base_env = _build_env(env_builder, base_src, "base", install_cmd)
            head_env = _build_env(env_builder, p1_handle, "head", install_cmd)
            environments = [base_env, head_env]

            # --- P0 baseline repetitions (base env, per-worktree PYTHONPATH) ---
            for i, (handle, err) in enumerate(zip(p0_handles, p0_errors)):
                probes.append(
                    _run_single(
                        handle, err, base_env, "P0", i, timeout, launcher, revision="base"
                    )
                )

            # --- P1 verdict probe (head env) ---
            if p1_handle is None:
                probes.append(worktree_failure_result("P1", head_commit, p1_err or "unknown"))
                p1_outcome = ProbeOutcome.INCONCLUSIVE
            elif not head_env.ok:
                probes.append(_env_failure_probe("P1", 0, p1_handle.path, head_env))
                p1_outcome = ProbeOutcome.INCONCLUSIVE
            else:
                p1 = run_p1(
                    p1_handle.path,
                    interpreter=head_env.python_path,
                    timeout=timeout,
                    base_env=probe_env(head_env, p1_handle.path),
                    launcher=launcher,
                )
                probes.append(p1)
                p1_outcome = p1.outcome

                # --- P1 coverage run (fifth run; never feeds the verdict) ---
                # coverage.py is a measurement tool, not a declared project
                # dependency, so make it available in the head env before the
                # instrumented run (Concern 2). Best-effort: if it cannot be
                # installed the run yields COVERAGE_UNAVAILABLE.
                env_builder.ensure_packages(head_env, ["coverage"])
                cov = run_p1_coverage(
                    p1_handle.path,
                    sorted(production_paths),
                    interpreter=head_env.python_path,
                    timeout=timeout,
                    base_env=probe_env(head_env, p1_handle.path),
                    launcher=launcher,
                )
                probes.append(cov.probe)
                changed_head = gitio.changed_head_lines(file_diffs, production_paths)
                coverage_result = map_coverage(cov.cobertura_xml, changed_head)

            # --- Static analysis (advisory; needs collected node IDs) ---
            static_findings = _static_findings(
                diff, base_src, p1_handle, base_env, head_env
            )

            # --- P2 / P3 (only when structurally required) ---
            p2_outcome: ProbeOutcome | None = None
            p3_outcome: ProbeOutcome | None = None
            if p2p3:
                p2_result = _run_p2(
                    p2_handle, p2_err, p1_handle, test_paths, head_env, base_commit,
                    timeout, launcher,
                )
                probes.append(p2_result)
                p2_outcome = p2_result.outcome

                p3_result = _run_p3(
                    p3_handle, p3_err, base_src, test_paths, base_env, head_commit,
                    timeout, launcher,
                )
                probes.append(p3_result)
                p3_outcome = p3_result.outcome

            # --- Verdict ---
            inputs = VerdictInputs(
                base_resolved=True,
                head_resolved=True,
                is_git_repo=repo_validation.is_git_repo,
                has_submodules=repo_validation.has_submodules,
                unclassifiable_files=(),
                has_production_change=FileClass.PRODUCTION
                in {cf.classification for cf in classification.files},
                has_test_or_verifier_change=bool(
                    {FileClass.TEST, FileClass.VERIFIER_CONFIG}
                    & {cf.classification for cf in classification.files}
                ),
                p0_outcomes=tuple(
                    p.outcome for p in probes if p.probe_id == "P0"
                ),
                p1=p1_outcome,
                p2=p2_outcome,
                p3=p3_outcome,
                required_probe_inconclusive=_any_required_inconclusive(
                    p2p3, p2_outcome, p3_outcome
                ),
                coverage=coverage_result,
            )
            chosen, reason = decide(inputs)

    except ProbeAbort as abort:
        # pytest exit code 2 aborts the run with no verdict (Req 8.4, 15.9).
        record = build_full_evidence_record(
            run=run_meta,
            validation=repo_validation,
            environments=environments,
            classification=classification,
            file_diffs=file_diffs,
            static_findings=static_findings,
            probes=probes,
            coverage=coverage_result,
            verdict=Verdict.INCONCLUSIVE,  # placeholder; overridden below
            reason=reasons.ABORT_SIGNAL,
            tool_version=tool_version,
        )
        record["verdict"] = {
            "value": "ABORTED_NO_VERDICT",
            "reason_code": reasons.ABORT_SIGNAL,
            "reason": f"{reasons.ABORT_SIGNAL}:probe {abort.probe_id} returned pytest exit code 2",
            "matched_rule": None,
            "exit_code": ABORTED_NO_VERDICT_EXIT_CODE,
        }
        return OrchestratorResult(
            record=record,
            verdict=None,
            reason=reasons.ABORT_SIGNAL,
            exit_code=ABORTED_NO_VERDICT_EXIT_CODE,
            aborted=True,
        )

    record = build_full_evidence_record(
        run=run_meta,
        validation=repo_validation,
        environments=environments,
        classification=classification,
        file_diffs=file_diffs,
        static_findings=static_findings,
        probes=probes,
        coverage=coverage_result,
        verdict=chosen,
        reason=reason,
        tool_version=tool_version,
    )
    return OrchestratorResult(
        record=record,
        verdict=chosen,
        reason=reason,
        exit_code=verdict_exit_code(chosen),
        aborted=False,
    )


# --- Short-circuit helpers -------------------------------------------------


def _run_meta(
    run_id, timestamp, repo, base_ref, head_ref, base_commit, head_commit, timeout, run_root
) -> RunMetadata:
    return RunMetadata(
        run_id=run_id,
        timestamp=timestamp,
        repo_path=str(repo),
        base_ref=base_ref,
        head_ref=head_ref,
        base_commit=base_commit,
        head_commit=head_commit,
        timeout_seconds=timeout,
        run_root=run_root,
    )


def _prevalidation_inputs(
    *,
    base_resolved: bool,
    head_resolved: bool,
    validation: RepoValidation,
    unclassifiable_files: tuple[str, ...] = (),
) -> VerdictInputs:
    """VerdictInputs for a pre-probe short-circuit.

    Only the pre-probe facts (rows 0a-0e) matter; downstream fields carry inert
    placeholders because `decide` returns on the first matching pre-probe row.
    """
    return VerdictInputs(
        base_resolved=base_resolved,
        head_resolved=head_resolved,
        is_git_repo=validation.is_git_repo,
        has_submodules=validation.has_submodules,
        unclassifiable_files=unclassifiable_files,
        has_production_change=False,
        has_test_or_verifier_change=False,
        p0_outcomes=(),
        p1=ProbeOutcome.INCONCLUSIVE,
        p2=None,
        p3=None,
        required_probe_inconclusive=False,
        coverage=None,
    )


def _short_circuit(
    *,
    run_meta: RunMetadata,
    validation: RepoValidation,
    inputs: VerdictInputs,
    classification: ClassificationResult | None = None,
    file_diffs: tuple = (),
    tool_version: str = "0.1.0",
    _cited: str | None = None,
) -> OrchestratorResult:
    chosen, reason = decide(inputs)
    record = build_full_evidence_record(
        run=run_meta,
        validation=validation,
        environments=(),
        classification=classification,
        file_diffs=file_diffs,
        static_findings=(),
        probes=(),
        coverage=None,
        verdict=chosen,
        reason=reason,
        tool_version=tool_version,
    )
    return OrchestratorResult(
        record=record,
        verdict=chosen,
        reason=reason,
        exit_code=verdict_exit_code(chosen),
        aborted=False,
    )


# --- Worktree / environment / probe helpers --------------------------------


def _create(
    manager: Worktree_Manager, slot: str, commit: str
):
    """Create a worktree, returning `(handle, None)` or `(None, error_detail)`."""
    try:
        return manager.create(slot, commit), None
    except WorktreeCreationError as exc:
        return None, exc.detail


def _build_env(
    builder: Environment_Builder, handle, revision: str, install_cmd: str | None
) -> BuiltEnv:
    """Build the deps-only environment for `revision`, or a failed BuiltEnv when
    the worktree could not be created."""
    if handle is None:
        return BuiltEnv(
            revision=revision,
            tool=builder.tool,
            python_path=None,
            discovery="none",
            built=False,
            error=f"{reasons.WORKTREE_CREATE_FAILED}:{revision} worktree unavailable",
        )
    return builder.build(handle.path, revision, install_cmd=install_cmd)


def _env_failure_probe(
    probe_id: str, repetition: int, worktree_path: Path, env: BuiltEnv
) -> ProbeResult:
    """An INCONCLUSIVE probe for a probe whose environment could not be built."""
    return ProbeResult(
        probe_id=probe_id,
        repetition=repetition,
        command=(),
        exit_code=None,
        outcome=ProbeOutcome.INCONCLUSIVE,
        collected=0,
        passed=0,
        failed=0,
        skipped=0,
        elapsed_seconds=0.0,
        reason=env.error or reasons.DEPS_UNDISCOVERABLE,
        worktree_path=str(worktree_path),
    )


def _run_single(
    handle, err, env: BuiltEnv, probe_id: str, repetition: int, timeout, launcher, *, revision: str
) -> ProbeResult:
    """Run a single P0-style probe (source == tests == this revision)."""
    commit_placeholder = revision
    if handle is None:
        return worktree_failure_result(probe_id, commit_placeholder, err or "unknown", repetition)
    if not env.ok:
        return _env_failure_probe(probe_id, repetition, handle.path, env)
    return run_probe(
        handle.path,
        probe_id=probe_id,
        repetition=repetition,
        interpreter=env.python_path,
        timeout=timeout,
        base_env=probe_env(env, handle.path),
        launcher=launcher,
    )


def _run_p2(
    p2_handle, p2_err, p1_handle, test_paths, head_env, base_commit, timeout, launcher
) -> ProbeResult:
    if p2_handle is None:
        return worktree_failure_result("P2", base_commit, p2_err or "unknown")
    if p1_handle is None:
        return worktree_failure_result("P2", base_commit, "head worktree unavailable for graft")
    if not head_env.ok:
        return _env_failure_probe("P2", 0, p2_handle.path, head_env)
    return run_p2(
        p2_handle.path, p1_handle.path, test_paths, head_env,
        timeout=timeout, launcher=launcher,
    )


def _run_p3(
    p3_handle, p3_err, base_src, test_paths, base_env, head_commit, timeout, launcher
) -> ProbeResult:
    if p3_handle is None:
        return worktree_failure_result("P3", head_commit, p3_err or "unknown")
    if base_src is None:
        return worktree_failure_result("P3", head_commit, "base worktree unavailable for graft")
    if not base_env.ok:
        return _env_failure_probe("P3", 0, p3_handle.path, base_env)
    return run_p3(
        p3_handle.path, base_src.path, test_paths, base_env,
        timeout=timeout, launcher=launcher,
    )


def _any_required_inconclusive(
    required: bool, p2: ProbeOutcome | None, p3: ProbeOutcome | None
) -> bool:
    if not required:
        return False
    return (
        p2 is None
        or p3 is None
        or p2 is ProbeOutcome.INCONCLUSIVE
        or p3 is ProbeOutcome.INCONCLUSIVE
    )


def _static_findings(diff, base_src, p1_handle, base_env: BuiltEnv, head_env: BuiltEnv):
    """Collect node IDs from the base/head worktrees and run the Static_Analyzer.

    Best-effort: node-ID collection failures degrade to empty sets. Findings are
    advisory and never affect the verdict (Req 5.6), so partial collection is
    safe.
    """
    base_ids = frozenset()
    head_ids = frozenset()
    if base_src is not None and base_env.ok and base_env.python_path is not None:
        base_ids = gitio.collect_node_ids(base_env.python_path, base_src.path)
    if p1_handle is not None and head_env.ok and head_env.python_path is not None:
        head_ids = gitio.collect_node_ids(head_env.python_path, p1_handle.path)
    return analyze(diff, base_ids, head_ids)


def _graft_test_paths(pytest_config, classification: ClassificationResult, repo: Path):
    """Determine the test paths to graft for P2/P3.

    Prefers declared `testpaths`; else a top-level `tests/` directory when it
    exists; else the individual classified TEST file paths. These are the
    worktree-relative paths copied wholesale between revisions (Req 6.5, 6.6).
    """
    if pytest_config.testpaths:
        return list(pytest_config.testpaths)
    if (Path(repo) / "tests").is_dir():
        return ["tests"]
    return [cf.path for cf in classification.files if cf.classification is FileClass.TEST]

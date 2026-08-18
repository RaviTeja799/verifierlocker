"""Property-based test for the full Evidence Record (Task 15.3).

**Property 22: Evidence Record is complete and reproducible**

For any run result:

- every executed probe records its command, exit code, and collected / passed /
  failed / skipped counts;
- every INCONCLUSIVE outcome (and every BASELINE_INVALID / skipped probe) records
  an explicit reason (Req 11.4); and
- serialising the **normalised reproducible core** twice, or from two equal run
  results assembled from inputs supplied in ANY order, yields byte-identical
  output with canonically ordered arrays -- and the ephemeral per-run
  `<RUN_ROOT>` / `<WORKTREE>` paths never leak into the core (Req 11.5, 10.16).

Validates: Requirements 11.2, 11.4, 11.5.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from verifierlock.classifier import (
    ClassificationResult,
    ClassifiedFile,
    FileClass,
)
from verifierlock.coverage import ChangedLine, CoverageResult
from verifierlock.environment import BuiltEnv
from verifierlock.evidence import (
    RunMetadata,
    build_full_evidence_record,
    serialize_reproducible_core,
)
from verifierlock.repository import RepoValidation
from verifierlock.static_analyzer import StaticFinding
from verifierlock.types import DiffHunk, FileDiff, ProbeOutcome, ProbeResult
from verifierlock.verdict import Verdict

_RUN_ROOT = "/tmp/verifierlock/run-abcdef"

_INCONCLUSIVE_REASONS = st.sampled_from(
    ["PROBE_TIMEOUT:timeout=5s", "ENV_INCOMPATIBLE:no module", "IMPORT_LIMITATION:x"]
)


@st.composite
def probe_results(draw: st.DrawFn) -> ProbeResult:
    probe_id = draw(st.sampled_from(["P0", "P1", "P1-COV", "P2", "P3"]))
    repetition = draw(st.integers(min_value=0, max_value=2))
    slot = f"{probe_id.lower()}-{repetition}"
    worktree_path = f"{_RUN_ROOT}/worktrees/{slot}"
    outcome = draw(st.sampled_from(list(ProbeOutcome)))
    # Executed probes carry a command embedding the run-specific paths, so
    # normalisation is exercised. INCONCLUSIVE outcomes always carry a reason.
    command = (
        "/usr/bin/python",
        "-m",
        "pytest",
        "--rootdir",
        worktree_path,
        f"--data-file={_RUN_ROOT}/worktrees/{slot}-coverage/.coverage",
    )
    reason = (
        draw(_INCONCLUSIVE_REASONS)
        if outcome is ProbeOutcome.INCONCLUSIVE
        else draw(st.none())
    )
    return ProbeResult(
        probe_id=probe_id,
        repetition=repetition,
        command=command,
        exit_code=draw(st.sampled_from([0, 1, None, 5])),
        outcome=outcome,
        collected=draw(st.integers(min_value=0, max_value=20)),
        passed=draw(st.integers(min_value=0, max_value=20)),
        failed=draw(st.integers(min_value=0, max_value=5)),
        skipped=draw(st.integers(min_value=0, max_value=5)),
        elapsed_seconds=draw(st.floats(min_value=0, max_value=10)),
        reason=reason,
        worktree_path=worktree_path,
    )


@st.composite
def built_envs(draw: st.DrawFn) -> BuiltEnv:
    revision = draw(st.sampled_from(["base", "head"]))
    return BuiltEnv(
        revision=revision,
        tool=draw(st.sampled_from(["uv", "venv+pip"])),
        python_path=None,
        discovery=draw(st.sampled_from(["pyproject.toml", "requirements.txt", "none"])),
        built=draw(st.booleans()),
        error=draw(st.none() | st.just("DEPS_UNDISCOVERABLE:x")),
    )


_PATHS = st.sampled_from(["a.py", "pkg/b.py", "tests/test_c.py", "src/d.py"])


@st.composite
def classifications(draw: st.DrawFn) -> ClassificationResult:
    paths = draw(st.lists(_PATHS, min_size=0, max_size=4, unique=True))
    files = tuple(
        ClassifiedFile(path=p, classification=draw(st.sampled_from(list(FileClass))))
        for p in paths
    )
    unclassifiable = tuple(
        draw(st.lists(st.sampled_from(["x.bin", "y.png"]), max_size=2, unique=True))
    )
    return ClassificationResult(files=files, unclassifiable=unclassifiable)


@st.composite
def file_diffs(draw: st.DrawFn) -> FileDiff:
    path = draw(_PATHS)
    new_start = draw(st.integers(min_value=1, max_value=50))
    header = f"@@ -{new_start},2 +{new_start},3 @@"
    hunk = DiffHunk(
        header=header,
        added_lines=("+ new line",),
        removed_lines=(),
        context_lines=(" ctx",),
    )
    return FileDiff(path=path, hunks=(hunk,))


@st.composite
def static_findings(draw: st.DrawFn) -> StaticFinding:
    return StaticFinding(
        kind=draw(st.sampled_from(["new_skip", "deleted_assertion", "lowered_fail_under"])),
        file=draw(_PATHS),
        hunk=draw(st.sampled_from(["@@ -1,2 +1,3 @@", ""])),
        detail=draw(st.text(min_size=0, max_size=12)),
    )


@st.composite
def coverage_results(draw: st.DrawFn) -> CoverageResult | None:
    if draw(st.booleans()):
        return None
    available = draw(st.booleans())
    if not available:
        return CoverageResult(lines=(), available=False, reason="COVERAGE_UNAVAILABLE:x")
    lines = tuple(
        ChangedLine(
            file=draw(_PATHS),
            line=draw(st.integers(min_value=1, max_value=100)),
            covered=draw(st.booleans()),
        )
        for _ in range(draw(st.integers(min_value=0, max_value=5)))
    )
    return CoverageResult(lines=lines, available=True, reason=None)


@st.composite
def scenarios(draw: st.DrawFn):
    run = RunMetadata(
        run_id=draw(st.text(min_size=1, max_size=8)),
        timestamp="2026-01-01T00:00:00Z",
        repo_path="/repo",
        base_ref="main",
        head_ref="feature",
        base_commit=draw(st.sampled_from(["9f8e", None])),
        head_commit="1a2b",
        timeout_seconds=draw(st.sampled_from([None, 600])),
        run_root=_RUN_ROOT,
    )
    validation = RepoValidation(
        is_git_repo=True, has_submodules=False, determination="supported"
    )
    verdict = draw(st.sampled_from(list(Verdict)))
    reason = draw(
        st.sampled_from(
            ["P2_PASS_P3_FAIL", "HEAD_NOT_GREEN", "BASELINE_NOT_GREEN", "UNCLASSIFIABLE_FILE:a.py"]
        )
    )
    return {
        "run": run,
        "validation": validation,
        "environments": draw(st.lists(built_envs(), max_size=2)),
        "classification": draw(classifications()),
        "file_diffs": draw(st.lists(file_diffs(), max_size=3)),
        "static_findings": draw(st.lists(static_findings(), max_size=4)),
        "probes": draw(st.lists(probe_results(), max_size=6)),
        "coverage": draw(coverage_results()),
        "verdict": verdict,
        "reason": reason,
    }


def _build(scenario, *, reverse: bool = False) -> dict:
    def maybe_reverse(seq):
        return list(reversed(seq)) if reverse else list(seq)

    return build_full_evidence_record(
        run=scenario["run"],
        validation=scenario["validation"],
        environments=maybe_reverse(scenario["environments"]),
        classification=scenario["classification"],
        file_diffs=maybe_reverse(scenario["file_diffs"]),
        static_findings=maybe_reverse(scenario["static_findings"]),
        probes=maybe_reverse(scenario["probes"]),
        coverage=scenario["coverage"],
        verdict=scenario["verdict"],
        reason=scenario["reason"],
    )


@settings(max_examples=150)
@given(scenario=scenarios())
def test_evidence_record_is_complete(scenario) -> None:
    """Every probe records command + exit code + counts; every INCONCLUSIVE
    probe records an explicit reason (Req 11.2, 11.4)."""
    record = _build(scenario)

    for probe in record["probes"]:
        # Command and counts are always recorded.
        assert "command" in probe and isinstance(probe["command"], list)
        assert "exit_code" in probe
        for count in ("collected", "passed", "failed", "skipped"):
            assert count in probe and isinstance(probe[count], int)
        # No silent failure: an INCONCLUSIVE outcome names a reason (Req 11.4).
        if probe["outcome"] == ProbeOutcome.INCONCLUSIVE.value:
            assert probe["reason"] is not None

    # The verdict always carries a reason code (Req 11.4).
    assert record["verdict"]["reason_code"]


@settings(max_examples=150)
@given(scenario=scenarios())
def test_reproducible_core_is_stable_and_order_independent(scenario) -> None:
    """Serialising the normalised core twice, or from inputs in reversed order,
    is byte-identical, and no ephemeral run path leaks into the core (Req 11.5,
    10.16)."""
    record = _build(scenario)
    reversed_record = _build(scenario, reverse=True)

    core_a = serialize_reproducible_core(record)
    core_a_again = serialize_reproducible_core(record)
    core_b = serialize_reproducible_core(reversed_record)

    # Deterministic serialization and order-independent assembly.
    assert core_a == core_a_again
    assert core_a == core_b

    # The ephemeral per-run paths are normalised away entirely.
    assert _RUN_ROOT not in core_a
    if scenario["probes"]:
        assert "<RUN_ROOT>" in core_a or "<WORKTREE>" in core_a


def test_command_normalisation_replaces_run_specific_paths() -> None:
    """A concrete check that the reproducible core uses placeholders, not the
    ephemeral run root / worktree paths."""
    probe = ProbeResult(
        probe_id="P1",
        repetition=0,
        command=("py", "--rootdir", f"{_RUN_ROOT}/worktrees/p1"),
        exit_code=0,
        outcome=ProbeOutcome.ALL_PASSED,
        collected=1,
        passed=1,
        failed=0,
        skipped=0,
        elapsed_seconds=1.0,
        reason=None,
        worktree_path=f"{_RUN_ROOT}/worktrees/p1",
    )
    run = RunMetadata(
        run_id="r1",
        timestamp="2026-01-01T00:00:00Z",
        repo_path="/repo",
        base_ref="a",
        head_ref="b",
        base_commit="9f8e",
        head_commit="1a2b",
        timeout_seconds=None,
        run_root=_RUN_ROOT,
    )
    record = build_full_evidence_record(
        run=run,
        validation=RepoValidation(True, False, "supported"),
        environments=(),
        classification=None,
        file_diffs=(),
        static_findings=(),
        probes=(probe,),
        coverage=None,
        verdict=Verdict.INDEPENDENT_EVIDENCE,
        reason="ALL_CHANGED_LINES_COVERED",
    )
    core = serialize_reproducible_core(record)
    assert "<WORKTREE>" in core
    assert _RUN_ROOT not in core
    # The raw record still keeps the real command for auditability.
    assert record["probes"][0]["command"] == ["py", "--rootdir", f"{_RUN_ROOT}/worktrees/p1"]

"""Property-based tests for the CLI exit-code mapping and policy (Task 18.5).

**Property 24: Verdict-to-exit-code mapping is injective and documented**

For any of the seven verdicts plus the aborted-no-verdict outcome, the CLI
returns the documented exit code, and the mapping assigns a distinct code to
each of the eight outcomes (Req 15.1-15.9).

Alongside the property, this module pins the CLI's other contractual behaviour:
the untrusted-code warning is printed BEFORE the pipeline starts (Req 14.1), the
gating policies change only the process exit status and never the documented
code recorded in the Evidence Record (design "CI ergonomics"), remote
repositories are refused (Req 14.2), and the JSON / report artifacts are written
locally (Req 11.1, 12.2).

The pipeline is injected, so these tests never execute repository code: the
subject under test is the CLI, not a probe.

Validates: Requirements 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifierlock import cli
from verifierlock.verdict import (
    ABORTED_NO_VERDICT_EXIT_CODE,
    VERDICT_EXIT_CODES,
    Verdict,
)

from .cli_support import pipeline_returning

# The eight outcomes: the seven verdicts plus "aborted, no verdict" (None).
_OUTCOMES = list(Verdict) + [None]

# The documented mapping, restated here from the design's table rather than
# imported, so a silent change to the table fails this test.
_DOCUMENTED = {
    "INDEPENDENT_EVIDENCE": 0,
    "NO_INDEPENDENT_EVIDENCE": 10,
    "NO_VERIFIER_CHANGE": 11,
    "VERIFIER_WEAKENED": 12,
    "VERIFIER_CHANGED_REVIEW_REQUIRED": 13,
    "INCONCLUSIVE": 14,
    "BASELINE_INVALID": 15,
    "ABORTED_NO_VERDICT": 16,
}


def _run(argv: list[str], verdict: Verdict | None, *, calls=None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    status = cli.main(
        argv,
        run_pipeline=pipeline_returning(verdict, calls=calls),
        stdout=out,
        stderr=err,
    )
    return status, out.getvalue(), err.getvalue()


def _label(verdict: Verdict | None) -> str:
    return verdict.value if verdict is not None else "ABORTED_NO_VERDICT"


def test_documented_mapping_is_injective_over_all_eight_outcomes() -> None:
    """Each of the eight outcomes has its own distinct documented code
    (Req 15.2-15.9)."""
    codes = [cli.documented_exit_code(outcome) for outcome in _OUTCOMES]

    assert len(codes) == 8
    assert len(set(codes)) == 8, f"exit codes are not distinct: {codes}"
    assert {_label(o): cli.documented_exit_code(o) for o in _OUTCOMES} == _DOCUMENTED
    # The clean verdict is 0, matching Unix convention; nothing else is 0.
    assert cli.documented_exit_code(Verdict.INDEPENDENT_EVIDENCE) == 0
    assert all(code != 0 for outcome, code in zip(_OUTCOMES, codes)
               if outcome is not Verdict.INDEPENDENT_EVIDENCE)
    # No documented code collides with the CLI's own usage-error status.
    assert cli.USAGE_ERROR_EXIT_CODE not in codes
    # The engine's table and the aborted code are the single source of truth.
    assert set(VERDICT_EXIT_CODES.values()) | {ABORTED_NO_VERDICT_EXIT_CODE} == set(codes)


@settings(max_examples=100, deadline=None)
@given(outcome=st.sampled_from(_OUTCOMES))
def test_cli_returns_the_documented_exit_code(outcome, tmp_path_factory) -> None:
    """Property 24: for any outcome the CLI returns its documented code."""
    repo = tmp_path_factory.mktemp("repo")
    status, _, err = _run(["--repo", str(repo), "--base", "main"], outcome)

    expected = _DOCUMENTED[_label(outcome)]
    assert status == expected
    # The record and the human-facing summary agree with the returned status.
    assert f"documented exit code {expected}" in err
    assert f"process exit status {expected}" in err


@settings(max_examples=100, deadline=None)
@given(outcome=st.sampled_from(_OUTCOMES))
def test_documented_code_is_recorded_whatever_the_policy(outcome, tmp_path_factory) -> None:
    """A gating policy changes only the process status; the documented code is
    always recorded in the Evidence Record (design "CI ergonomics")."""
    repo = tmp_path_factory.mktemp("repo")
    expected = _DOCUMENTED[_label(outcome)]

    for policy in ("documented", "lenient", "strict"):
        json_path = Path(repo) / f"evidence-{policy}.json"
        status, _, _ = _run(
            [
                "--repo", str(repo),
                "--base", "main",
                "--exit-policy", policy,
                "--json", str(json_path),
                "--quiet",
            ],
            outcome,
        )
        record = json.loads(json_path.read_text())

        # The documented mapping is never rewritten.
        assert record["verdict"]["exit_code"] == expected
        assert record["exit_status"]["documented_exit_code"] == expected
        # Both the true code and the policy-applied status are auditable.
        assert record["exit_status"]["process_exit_status"] == status
        assert record["exit_status"]["policy"] == policy
        assert record["exit_status"]["build_blocking"] is (status != 0)
        # A blocking outcome keeps its own documented code; nothing is remapped
        # to some other verdict's code.
        assert status in (0, expected)


@pytest.mark.parametrize(
    "policy,expected_blocking",
    [
        ("lenient", {Verdict.VERIFIER_WEAKENED}),
        (
            "strict",
            {Verdict.VERIFIER_WEAKENED, Verdict.VERIFIER_CHANGED_REVIEW_REQUIRED},
        ),
    ],
)
def test_gating_policies_block_exactly_the_documented_verdicts(
    policy: str, expected_blocking: set, tmp_path: Path
) -> None:
    """`lenient` blocks only on VERIFIER_WEAKENED; `strict` adds
    VERIFIER_CHANGED_REVIEW_REQUIRED. Everything else exits 0 so a CI build is
    not failed by an informational verdict."""
    blocking = set()
    for outcome in _OUTCOMES:
        status, _, _ = _run(
            ["--repo", str(tmp_path), "--base", "main", "--exit-policy", policy, "--quiet"],
            outcome,
        )
        if status != 0:
            blocking.add(outcome)
            assert status == _DOCUMENTED[_label(outcome)]

    assert blocking == expected_blocking


def test_strict_flag_is_shorthand_for_the_strict_policy(tmp_path: Path) -> None:
    status_flag, _, _ = _run(
        ["--repo", str(tmp_path), "--base", "main", "--strict", "--quiet"],
        Verdict.VERIFIER_CHANGED_REVIEW_REQUIRED,
    )
    status_policy, _, _ = _run(
        ["--repo", str(tmp_path), "--base", "main", "--exit-policy", "strict", "--quiet"],
        Verdict.VERIFIER_CHANGED_REVIEW_REQUIRED,
    )
    assert status_flag == status_policy == 13

    # Under strict, an informational verdict does not fail the build...
    lenient_status, _, _ = _run(
        ["--repo", str(tmp_path), "--base", "main", "--strict", "--quiet"],
        Verdict.NO_INDEPENDENT_EVIDENCE,
    )
    assert lenient_status == 0
    # ...but the default (documented) policy still returns its distinct code.
    default_status, _, _ = _run(
        ["--repo", str(tmp_path), "--base", "main", "--quiet"],
        Verdict.NO_INDEPENDENT_EVIDENCE,
    )
    assert default_status == 10


def test_default_policy_is_the_documented_mapping(tmp_path: Path) -> None:
    """With no flags the CLI satisfies Req 15 directly: every verdict returns
    its own distinct documented code."""
    statuses = {
        _label(outcome): _run(
            ["--repo", str(tmp_path), "--base", "main", "--quiet"], outcome
        )[0]
        for outcome in _OUTCOMES
    }
    assert statuses == _DOCUMENTED


def test_conflicting_policy_flags_are_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        _run(
            ["--repo", str(tmp_path), "--base", "main", "--strict", "--exit-policy", "lenient"],
            Verdict.INCONCLUSIVE,
        )
    assert excinfo.value.code == cli.USAGE_ERROR_EXIT_CODE


# --- Req 14: the untrusted-code warning and local-only operation ------------


def test_warning_is_printed_before_the_pipeline_starts(tmp_path: Path) -> None:
    """Req 14.1: the warning that repository code will run must be visible
    before any probe is launched."""
    seen: list[str] = []
    err = io.StringIO()

    def run_pipeline(repo, **kwargs):
        # Capture what the user has already been told at the moment the pipeline
        # (and therefore the first probe) starts.
        seen.append(err.getvalue())
        return pipeline_returning(Verdict.INCONCLUSIVE)(repo, **kwargs)

    cli.main(
        ["--repo", str(tmp_path), "--base", "main", "--quiet"],
        run_pipeline=run_pipeline,
        stdout=io.StringIO(),
        stderr=err,
    )

    assert seen, "pipeline was never invoked"
    assert cli.UNTRUSTED_CODE_WARNING in seen[0]
    assert "EXECUTES REPOSITORY CODE" in seen[0]


@pytest.mark.parametrize(
    "repo",
    ["https://example.com/repo.git", "git@example.com:org/repo.git", "/nonexistent/path"],
)
def test_remote_or_missing_repositories_are_refused(repo: str) -> None:
    """Req 14.2: v1 operates on local repositories only."""
    with pytest.raises(SystemExit) as excinfo:
        _run(["--repo", repo, "--base", "main"], Verdict.INCONCLUSIVE)
    assert excinfo.value.code == cli.USAGE_ERROR_EXIT_CODE


# --- Argument plumbing and artifacts ---------------------------------------


def test_arguments_are_passed_through_to_the_pipeline(tmp_path: Path) -> None:
    calls: list[dict] = []
    _run(
        [
            "--repo", str(tmp_path),
            "--base", "v1.0",
            "--head", "feature",
            "--timeout", "42.5",
            "--install-cmd", "pip install -r requirements.txt",
            "--quiet",
        ],
        Verdict.INCONCLUSIVE,
        calls=calls,
    )

    assert len(calls) == 1
    call = calls[0]
    assert Path(call["repo"]) == tmp_path
    assert call["base_ref"] == "v1.0"
    assert call["head_ref"] == "feature"
    assert call["timeout"] == 42.5
    assert call["install_cmd"] == "pip install -r requirements.txt"


def test_default_timeout_and_head_are_applied(tmp_path: Path) -> None:
    calls: list[dict] = []
    _run(["--repo", str(tmp_path), "--base", "main", "--quiet"], Verdict.INCONCLUSIVE, calls=calls)
    assert calls[0]["timeout"] == cli.DEFAULT_TIMEOUT_SECONDS
    assert calls[0]["head_ref"] == "HEAD"


def test_json_and_report_artifacts_are_written(tmp_path: Path) -> None:
    json_path = tmp_path / "out" / "evidence.json"
    report_path = tmp_path / "out" / "report.txt"

    status, stdout, _ = _run(
        [
            "--repo", str(tmp_path),
            "--base", "main",
            "--json", str(json_path),
            "--report", str(report_path),
        ],
        Verdict.VERIFIER_WEAKENED,
    )

    assert status == 12
    record = json.loads(json_path.read_text())
    assert record["verdict"]["value"] == "VERIFIER_WEAKENED"
    assert record["verdict"]["exit_code"] == 12
    report = report_path.read_text()
    assert "VERIFIER_WEAKENED" in report
    # The report also goes to stdout unless --quiet is given.
    assert "VERIFIER_WEAKENED" in stdout


def test_quiet_suppresses_stdout_but_still_writes_artifacts(tmp_path: Path) -> None:
    report_path = tmp_path / "report.txt"
    _, stdout, _ = _run(
        ["--repo", str(tmp_path), "--base", "main", "--report", str(report_path), "--quiet"],
        Verdict.INDEPENDENT_EVIDENCE,
    )
    assert stdout == ""
    assert "INDEPENDENT_EVIDENCE" in report_path.read_text()


def test_p0_repetitions_below_two_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        _run(
            ["--repo", str(tmp_path), "--base", "main", "--p0-repetitions", "1"],
            Verdict.INCONCLUSIVE,
        )
    assert excinfo.value.code == cli.USAGE_ERROR_EXIT_CODE

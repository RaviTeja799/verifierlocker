"""Property-based test for the Report_Generator (Task 18.4).

**Property 23: Report contains the required elements**

For any Evidence Record, the rendered human-readable report contains the
verdict, the per-probe outcomes, the static findings, and the changed-line
coverage (Req 12.1). Two supporting guarantees are asserted alongside it: the
renderer never mutates the record it reads, and `write_report` puts the report
on the local filesystem and nowhere else (Req 12.2, 14.3).

The Evidence Records under test are assembled by the REAL
`build_full_evidence_record` from Hypothesis-generated inputs (reusing the
Task 15.3 strategies), so the report is exercised against exactly the record
shapes the pipeline can produce -- including records with no probes, no
findings, and unavailable coverage.

Validates: Requirements 12.1.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypothesis import given, settings

from verifierlock.evidence import RunMetadata, build_full_evidence_record
from verifierlock.report import render_report, write_report
from verifierlock.repository import RepoValidation
from verifierlock.types import ProbeOutcome, ProbeResult
from verifierlock.verdict import Verdict

from .test_evidence_properties import _build, scenarios


def _probe_lines(report: str, label: str) -> list[str]:
    return [line for line in report.splitlines() if line.strip().startswith(label + " ")]


@settings(max_examples=150)
@given(scenario=scenarios())
def test_report_contains_the_required_elements(scenario) -> None:
    """Verdict + per-probe outcomes + static findings + changed-line coverage
    all appear in the rendered report (Req 12.1)."""
    record = _build(scenario)
    report = render_report(record)

    # --- The verdict ---
    assert "Verdict:" in report
    assert record["verdict"]["value"] in report
    assert record["verdict"]["reason_code"] in report

    # --- The per-probe outcomes ---
    assert "Probes" in report
    for probe in record["probes"]:
        label = f"{probe['probe_id']}#{probe['repetition']}"
        lines = _probe_lines(report, label)
        assert lines, f"probe {label} missing from the report"
        # At least one line for this probe carries its outcome and its kind.
        assert any(
            probe["outcome"] in line and probe["kind"] in line for line in lines
        ), f"probe {label} outcome {probe['outcome']} missing from {lines}"
        # An INCONCLUSIVE probe's reason is never silently dropped (Req 11.4's
        # human-readable counterpart).
        if probe["outcome"] == ProbeOutcome.INCONCLUSIVE.value and probe["reason"]:
            assert probe["reason"] in report

    # --- The static findings ---
    assert "Static findings" in report
    for finding in record["static_findings"]:
        assert finding["kind"] in report
        assert finding["file"] in report
    if not record["static_findings"]:
        assert "(none)" in report

    # --- The changed-line coverage ---
    assert "Changed-line coverage" in report
    coverage = record["coverage"]
    assert f"available: {'yes' if coverage['available'] else 'no'}" in report
    for line in coverage["changed_lines"]:
        assert f"{line['file']}:{line['line']}" in report
        marker = "covered" if line["covered"] else "UNCOVERED"
        assert any(
            marker in text and f"{line['file']}:{line['line']}" in text
            for text in report.splitlines()
        )


@settings(max_examples=100)
@given(scenario=scenarios())
def test_rendering_never_mutates_the_record(scenario) -> None:
    """The report is a pure view of the record: rendering cannot perturb the
    verdict or the reproducible core."""
    record = _build(scenario)
    before = json.dumps(record, sort_keys=True)
    render_report(record)
    assert json.dumps(record, sort_keys=True) == before


@settings(max_examples=25)
@given(scenario=scenarios())
def test_rendering_is_deterministic(scenario) -> None:
    """Identical records render identically, so the report can be diffed."""
    record = _build(scenario)
    assert render_report(record) == render_report(_build(scenario, reverse=True))


def _minimal_record(verdict: Verdict, probes=()) -> dict:
    return build_full_evidence_record(
        run=RunMetadata(
            run_id="r1",
            timestamp="2026-01-01T00:00:00Z",
            repo_path="/repo",
            base_ref="main",
            head_ref="feature",
            base_commit="a" * 40,
            head_commit="b" * 40,
            timeout_seconds=600.0,
            run_root="/tmp/verifierlock/r1",
        ),
        validation=RepoValidation(True, False, "supported"),
        environments=(),
        classification=None,
        file_diffs=(),
        static_findings=(),
        probes=probes,
        coverage=None,
        verdict=verdict,
        reason="HEAD_NOT_GREEN",
    )


def test_report_is_written_as_a_local_artifact(tmp_path: Path) -> None:
    """`write_report` writes the rendered text to a local path (Req 12.2, 14.3)."""
    record = _minimal_record(Verdict.INCONCLUSIVE)
    target = tmp_path / "nested" / "report.txt"

    written = write_report(record, target)

    assert written == target
    assert target.is_file()
    assert target.read_text() == render_report(record)
    assert "INCONCLUSIVE" in target.read_text()


def test_short_circuited_run_reports_that_no_probe_ran() -> None:
    """A run decided before the probe stage says so explicitly, instead of
    rendering an empty probe section."""
    report = render_report(_minimal_record(Verdict.INCONCLUSIVE))
    assert "Probes (0)" in report
    assert "no probe executed" in report
    assert "(no changed production lines measured)" in report


def test_killed_probe_reports_no_exit_code() -> None:
    """A timed-out probe has no exit code; the report says `killed` rather than
    printing a bare `None` (Req 7b.2's human-readable counterpart)."""
    killed = ProbeResult(
        probe_id="P1",
        repetition=0,
        command=("py", "-m", "pytest"),
        exit_code=None,
        outcome=ProbeOutcome.INCONCLUSIVE,
        collected=0,
        passed=0,
        failed=0,
        skipped=0,
        elapsed_seconds=600.0,
        reason="PROBE_TIMEOUT:timeout=600s,elapsed=600.00s",
        worktree_path="/tmp/verifierlock/r1/worktrees/p1",
    )
    report = render_report(_minimal_record(Verdict.INCONCLUSIVE, probes=(killed,)))
    assert "exit=killed" in report
    assert "PROBE_TIMEOUT:timeout=600s,elapsed=600.00s" in report

"""End-to-end integration tests for the bundled labelled fixtures (Task 17.3).

Each scenario is materialised into a real two-commit Git repo and run through
the full VerifierLock pipeline; the test asserts the documented verdict
(Requirements 16.2-16.6). These use REAL environment builds and REAL pytest
subprocesses -- they skip only if an environment cannot be built in this
sandbox (e.g. no `uv`/network), never masking a wrong verdict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifierlock import orchestrator
from verifierlock.types import ProbeOutcome
from verifierlock.verdict import Verdict

from .fixture_repo import build_scenario_repo

# (scenario directory, expected verdict, requirement reference)
_SCENARIOS = [
    ("weakened_authz", Verdict.VERIFIER_WEAKENED, "16.2/16.6"),
    ("deleted_test", Verdict.VERIFIER_WEAKENED, "16.5"),
    ("independent_evidence", Verdict.INDEPENDENT_EVIDENCE, "16.3"),
    ("review_required", Verdict.VERIFIER_CHANGED_REVIEW_REQUIRED, "16.4"),
]


def _run_scenario(scenario: str, tmp_path: Path):
    repo = build_scenario_repo(scenario, tmp_path / scenario)
    result = orchestrator.run(repo, base_ref="HEAD~1", head_ref="HEAD", timeout=300)

    if result.aborted:
        pytest.fail(f"{scenario}: pipeline aborted unexpectedly: {result.reason}")
    # Skip only if the sandbox could not build an environment at all.
    if any(not env["built"] for env in result.record["environments"]):
        errors = [env["error"] for env in result.record["environments"]]
        pytest.skip(f"{scenario}: could not build environments in this sandbox: {errors}")
    return result


@pytest.mark.parametrize("scenario,expected,req", _SCENARIOS)
def test_fixture_scenarios_produce_expected_verdict(
    scenario: str, expected: Verdict, req: str, tmp_path: Path
) -> None:
    result = _run_scenario(scenario, tmp_path)
    assert result.verdict is expected, (
        f"{scenario} (Req {req}): expected {expected.value}, got "
        f"{result.verdict.value if result.verdict else None} "
        f"(reason {result.record['verdict']['reason']})"
    )


def _probe(record: dict, probe_id: str, kind: str = "verdict") -> dict:
    matches = [
        p for p in record["probes"] if p["probe_id"] == probe_id and p["kind"] == kind
    ]
    assert matches, f"no {probe_id} ({kind}) probe in record"
    return matches[0]


def test_flagship_weakened_authz_probe_signature(tmp_path: Path) -> None:
    """The flagship fixture must show the VERIFIER_WEAKENED signature: the
    non-discriminating weakened test lets P2 pass while P3 fails (Req 16.6)."""
    result = _run_scenario("weakened_authz", tmp_path)
    record = result.record

    assert result.verdict is Verdict.VERIFIER_WEAKENED
    assert record["verdict"]["exit_code"] == 12

    # P0 baseline is green (twice), P1 head-as-submitted is green.
    for p0 in [p for p in record["probes"] if p["probe_id"] == "P0"]:
        assert p0["outcome"] == ProbeOutcome.ALL_PASSED.value
    assert _probe(record, "P1")["outcome"] == ProbeOutcome.ALL_PASSED.value

    # The load-bearing signature: P2 all-passed (weakened test is
    # non-discriminating) AND P3 failed (base test catches the defect).
    assert _probe(record, "P2")["outcome"] == ProbeOutcome.ALL_PASSED.value
    assert _probe(record, "P3")["outcome"] == ProbeOutcome.TESTS_FAILED.value

    # The production defect and the test change are both classified.
    classes = {cf["path"]: cf["classification"] for cf in record["changed_files"]}
    assert classes.get("authz/__init__.py") == "production"
    assert classes.get("tests/test_authz.py") == "test"

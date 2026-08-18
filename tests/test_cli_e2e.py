"""End-to-end CLI demo test against the bundled weakened-test fixture (Task 18.7).

Runs the REAL CLI over the flagship fixture -- real worktrees, real dependency
installs, real pytest processes -- and asserts the demo claim the submission
rests on: the non-discriminating weakened test yields VERIFIER_WEAKENED with the
documented exit code 12 (Req 16.2, 16.6, 15.5).

Nothing here is stubbed except the sandbox escape hatch: if an environment
cannot be built at all (no `uv`/`venv` or no network) the test skips rather than
reporting a wrong verdict. A built environment plus a wrong verdict always
fails.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from verifierlock import cli, demo
from verifierlock.verdict import VERDICT_EXIT_CODES, Verdict

from .fixture_repo import build_scenario_repo


def _run_cli(repo: Path, tmp_path: Path, extra: list[str] | None = None) -> tuple[int, dict, str, str]:
    json_path = tmp_path / "evidence.json"
    report_path = tmp_path / "report.txt"
    out, err = io.StringIO(), io.StringIO()
    status = cli.main(
        [
            "--repo", str(repo),
            "--base", "HEAD~1",
            "--head", "HEAD",
            "--timeout", "300",
            "--json", str(json_path),
            "--report", str(report_path),
            *(extra or []),
        ],
        stdout=out,
        stderr=err,
    )
    record = json.loads(json_path.read_text())
    return status, record, report_path.read_text(), out.getvalue() + err.getvalue()


def _skip_if_no_environment(record: dict) -> None:
    unbuilt = [env for env in record["environments"] if not env["built"]]
    if unbuilt or not record["environments"]:
        pytest.skip(
            "could not build probe environments in this sandbox: "
            f"{[env.get('error') for env in unbuilt]}"
        )


def test_cli_demo_yields_verifier_weakened_with_exit_code_12(tmp_path: Path) -> None:
    """The bundled non-discriminating weakened-test fixture, run through the
    CLI, produces VERIFIER_WEAKENED and exit code 12 (Req 16.2, 16.6)."""
    repo = build_scenario_repo("weakened_authz", tmp_path / "weakened_authz")
    status, record, report, console = _run_cli(repo, tmp_path)

    _skip_if_no_environment(record)

    # --- The verdict and the documented exit code ---
    assert record["verdict"]["value"] == "VERIFIER_WEAKENED", (
        f"expected VERIFIER_WEAKENED, got {record['verdict']['value']} "
        f"(reason {record['verdict']['reason']})"
    )
    assert record["verdict"]["exit_code"] == 12
    assert status == 12, "the CLI must exit with the documented VERIFIER_WEAKENED code"
    assert record["exit_status"] == {
        "documented_exit_code": 12,
        "process_exit_status": 12,
        "policy": "documented",
        "build_blocking": True,
    }

    # --- The report artifact carries the verdict and the probe evidence ---
    assert "VERIFIER_WEAKENED" in report
    assert "P2" in report and "P3" in report
    assert "Changed-line coverage" in report

    # --- The user was warned before any repository code ran (Req 14.1) ---
    assert cli.UNTRUSTED_CODE_WARNING in console


def test_cli_strict_policy_still_blocks_the_weakened_fixture(tmp_path: Path) -> None:
    """`--strict` is only ever stricter: the weakening verdict still exits 12."""
    repo = build_scenario_repo("weakened_authz", tmp_path / "weakened_authz")
    status, record, _, _ = _run_cli(repo, tmp_path, extra=["--strict", "--quiet"])

    _skip_if_no_environment(record)

    assert record["verdict"]["value"] == "VERIFIER_WEAKENED"
    assert status == 12
    assert record["exit_status"]["policy"] == "strict"
    assert record["exit_status"]["build_blocking"] is True


def test_demo_lists_every_bundled_scenario_with_its_documented_code() -> None:
    """`python -m verifierlock.demo --list` is the quickstart's entry point, so
    it must name every bundled scenario and agree with the documented codes."""
    completed = subprocess.run(
        [sys.executable, "-m", "verifierlock.demo", "--list"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    for scenario, (verdict, code) in demo.SCENARIOS.items():
        assert scenario in completed.stdout
        assert f"{verdict} (exit {code})" in completed.stdout
    # The demo's expectations must match the engine's documented mapping.
    for verdict_name, code in [(v, c) for v, c in demo.SCENARIOS.values()]:
        assert VERDICT_EXIT_CODES[Verdict(verdict_name)] == code


def test_demo_materialises_a_two_commit_repository(tmp_path: Path) -> None:
    """The fixture snapshots become a real base..head history, which is what
    makes the demo reproducible from a cold clone."""
    repo = demo.build_scenario_repo("weakened_authz", tmp_path / "repo")

    log = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.split() == ["head", "base"]
    assert (repo / "authz" / "__init__.py").is_file()
    assert (repo / "tests").is_dir()


def test_cli_is_invokable_as_a_module() -> None:
    """`python -m verifierlock` is a working entry point (packaging check)."""
    completed = subprocess.run(
        [sys.executable, "-m", "verifierlock", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--repo" in completed.stdout
    # The documented exit-code table is discoverable from the CLI itself (Req 15.1).
    assert "12 VERIFIER_WEAKENED" in completed.stdout

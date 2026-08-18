"""Property-based test for the pytest exit-code-2 abort path (Task 18.6).

**Property 20: pytest exit code 2 aborts with no verdict**

For any probe returning exit code 2, the run aborts immediately, produces no
verdict, and the process exit code is the distinct "aborted, no verdict" code
(Req 8.4, 15.9).

"Aborts immediately" is the load-bearing half: it is asserted structurally, by
checking that no probe scheduled AFTER the aborting one appears in the Evidence
Record. Every probe stage is exercised in turn (both P0 repetitions, P1, P2, P3)
by driving the REAL Orchestrator over a real two-commit fixture repository with
three fakes in place of the slow, side-effecting parts:

- a worktree backend that just creates directories (the abort path does not
  depend on real checkouts),
- a deps-only environment builder that reports success with no interpreter (so
  no environment is built and no node-ID collection subprocess runs), and
- a scripted launcher that returns exit code 2 for the targeted probe and a
  clean pass for every other probe.

The instrumented P1 coverage run is deliberately included as a target: it must
NOT abort the run, because that run never feeds the verdict (design Concern 2).

Validates: Requirements 8.4, 15.9.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from verifierlock import cli, orchestrator
from verifierlock.environment import BuiltEnv
from verifierlock.probe import LaunchResult
from verifierlock.verdict import ABORTED_NO_VERDICT_EXIT_CODE

from .fixture_repo import build_scenario_repo

# The probe stages in the fixed order the Orchestrator runs them, as
# `(probe_id, kind, repetition)` keys into the Evidence Record's probe entries.
_STAGES: list[tuple[str, str, int]] = [
    ("P0", "verdict", 0),
    ("P0", "verdict", 1),
    ("P1", "verdict", 0),
    ("P1", "coverage", 0),
    ("P2", "verdict", 0),
    ("P3", "verdict", 0),
]

_PASSING_OUTPUT = "collected 1 item\n\n1 passed in 0.01s\n"


class _DirBackend:
    """Worktree backend that creates plain directories instead of checkouts."""

    def add(self, path: Path, commit: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def remove(self, path: Path) -> None:
        shutil.rmtree(Path(path), ignore_errors=True)

    def prune(self) -> None:
        return None


class _FakeEnvBuilder:
    """Environment builder that succeeds without building anything.

    `python_path=None` keeps the run hermetic: probes fall back to the scripted
    launcher and the Static_Analyzer's node-ID collection is skipped, so no real
    subprocess is spawned anywhere in the pipeline.
    """

    tool = "fake"

    def build(self, worktree: Path, revision: str, install_cmd: str | None = None) -> BuiltEnv:
        return BuiltEnv(
            revision=revision,
            tool=self.tool,
            python_path=None,
            discovery="pyproject.toml",
            built=True,
        )

    def ensure_packages(self, env: BuiltEnv, packages) -> bool:
        return True


def _stage_of(argv: tuple[str, ...], cwd: Path) -> tuple[str, str, int] | None:
    """Identify which probe stage a launch belongs to, from its slot and argv."""
    slot = Path(cwd).name
    is_coverage = "coverage" in argv
    if slot.startswith("p0-rep"):
        return ("P0", "verdict", int(slot.removeprefix("p0-rep")))
    if slot == "p1":
        return ("P1", "coverage" if is_coverage else "verdict", 0)
    if slot == "p2":
        return ("P2", "verdict", 0)
    if slot == "p3":
        return ("P3", "verdict", 0)
    return None


def _launcher_returning_two_at(target: tuple[str, str, int]):
    """A launcher that returns pytest exit code 2 for `target`, else a pass."""

    def launcher(argv, cwd, env, timeout):
        stage = _stage_of(tuple(argv), Path(cwd))
        # `coverage xml` post-processing is not a probe stage; let it pass.
        if stage == target and "xml" not in argv:
            return LaunchResult(2, "INTERNAL ERROR / interrupted\n", "", False, 0.01)
        return LaunchResult(0, _PASSING_OUTPUT, "", False, 0.01)

    return launcher


@pytest.fixture(scope="module")
def fixture_repo(tmp_path_factory) -> Path:
    """One real two-commit repository (production + test change, so P2/P3 are
    structurally required) shared by every example."""
    return build_scenario_repo(
        "weakened_authz", tmp_path_factory.mktemp("abort") / "weakened_authz"
    )


def _probe_keys(record: dict) -> set[tuple[str, str, int]]:
    return {
        (probe["probe_id"], probe["kind"], probe["repetition"])
        for probe in record["probes"]
    }


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(target_index=st.integers(min_value=0, max_value=len(_STAGES) - 1))
def test_exit_code_two_aborts_with_no_verdict(target_index: int, fixture_repo: Path) -> None:
    """Property 20: exit code 2 on any verdict probe aborts the run immediately
    with no verdict and the distinct aborted exit code (Req 8.4, 15.9)."""
    target = _STAGES[target_index]
    result = orchestrator.run(
        fixture_repo,
        base_ref="HEAD~1",
        head_ref="HEAD",
        timeout=30,
        env_builder=_FakeEnvBuilder(),
        worktree_backend=_DirBackend(),
        launcher=_launcher_returning_two_at(target),
    )
    executed = _probe_keys(result.record)

    if target == ("P1", "coverage", 0):
        # The instrumented coverage run never feeds the verdict, so exit code 2
        # there must NOT abort the run (design Concern 2).
        assert result.aborted is False
        assert result.verdict is not None
        assert result.exit_code != ABORTED_NO_VERDICT_EXIT_CODE
        return

    # --- No verdict ---
    assert result.aborted is True
    assert result.verdict is None
    assert result.reason == "ABORT_SIGNAL"

    # --- The distinct documented exit code (Req 15.9) ---
    assert result.exit_code == ABORTED_NO_VERDICT_EXIT_CODE == 16
    assert result.record["verdict"]["value"] == "ABORTED_NO_VERDICT"
    assert result.record["verdict"]["reason_code"] == "ABORT_SIGNAL"
    assert result.record["verdict"]["matched_rule"] is None
    assert result.record["verdict"]["exit_code"] == ABORTED_NO_VERDICT_EXIT_CODE

    # --- Immediately: the aborting probe and every later stage are absent ---
    for stage in _STAGES[target_index:]:
        assert stage not in executed, f"{stage} ran at or after the abort {target}"
    # ...while every stage before it was recorded, so the abort happened exactly
    # where the exit code 2 was returned.
    for stage in _STAGES[:target_index]:
        assert stage in executed, f"{stage} should have run before the abort {target}"

    # --- The CLI surfaces the aborted-no-verdict code (Req 15.9) ---
    def run_pipeline(repo, **kwargs):
        return result

    status = cli.main(
        ["--repo", str(fixture_repo), "--base", "HEAD~1", "--quiet"],
        run_pipeline=run_pipeline,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert status == ABORTED_NO_VERDICT_EXIT_CODE


def test_abort_still_cleans_up_worktrees(fixture_repo: Path) -> None:
    """Cleanup runs on the abort path too (Req 3.2, 3.5): the per-run worktrees
    are gone even though the run raised."""
    result = orchestrator.run(
        fixture_repo,
        base_ref="HEAD~1",
        head_ref="HEAD",
        timeout=30,
        run_id="abort-cleanup-check",
        env_builder=_FakeEnvBuilder(),
        worktree_backend=_DirBackend(),
        launcher=_launcher_returning_two_at(("P1", "verdict", 0)),
    )

    assert result.aborted is True
    from verifierlock.worktree import worktree_root

    assert not worktree_root("abort-cleanup-check").exists()


def test_abort_record_is_still_complete(fixture_repo: Path) -> None:
    """An aborted run still emits an auditable Evidence Record (Req 11.4): the
    probes that DID run are recorded, with their commands and counts."""
    result = orchestrator.run(
        fixture_repo,
        base_ref="HEAD~1",
        head_ref="HEAD",
        timeout=30,
        env_builder=_FakeEnvBuilder(),
        worktree_backend=_DirBackend(),
        launcher=_launcher_returning_two_at(("P2", "verdict", 0)),
    )
    record = result.record

    assert result.aborted is True
    assert record["run"]["base_commit"] and record["run"]["head_commit"]
    assert _probe_keys(record) == {
        ("P0", "verdict", 0),
        ("P0", "verdict", 1),
        ("P1", "verdict", 0),
        ("P1", "coverage", 0),
    }
    for probe in record["probes"]:
        assert probe["command"], "every recorded probe carries its exact command"
        assert probe["exit_code"] == 0
    assert record["verdict"]["reason"].startswith("ABORT_SIGNAL:")

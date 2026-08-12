"""Property-based tests for the Probe_Runner core (Tasks 10.4-10.7).

- **Property 10:** Every probe command carries the determinism controls.
- **Property 11:** Bytecode is purged around every probe (normal + timeout).
- **Property 17:** Timeout produces INCONCLUSIVE with a recorded duration.
- **Property 18:** The inter-test import limitation is INCONCLUSIVE.

The subprocess launch is isolated behind an injectable `launcher`, so the
timeout / import-limitation / abort behaviour is exercised with scripted
`LaunchResult`s (no real pytest). Each property test uses Hypothesis with a
minimum of 100 examples. Example tests cover the abort path and the plain
pass/fail classification.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifierlock import reasons
from verifierlock.probe import (
    LaunchResult,
    ProbeAbort,
    compose_probe_command,
    detect_import_limitation,
    purge_bytecode,
    run_probe,
)
from verifierlock.types import ProbeOutcome


def _scripted_launcher(result: LaunchResult, *, on_launch=None):
    """A launcher that ignores its inputs and returns a scripted result.

    `on_launch(argv, cwd, env, timeout)` may run a side effect first (used to
    simulate pytest writing bytecode into the worktree)."""

    def launcher(argv, cwd, env, timeout):
        if on_launch is not None:
            on_launch(argv, cwd, env, timeout)
        return result

    return launcher


# --- Property 10: Every probe command carries the determinism controls -----

_path_segment = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=8
)


# Feature: verifierlock, Property 10: For any probe composition, the composed
# command and environment include --import-mode=importlib, a disabled cache
# provider, neutralised repository addopts, a fixed PYTHONHASHSEED, disabled
# bytecode writing, and a fixed rootdir.
@settings(max_examples=200)
@given(
    interpreter=st.sampled_from(["python", "python3", "/usr/bin/python3.11", "py"]),
    segments=st.lists(_path_segment, min_size=1, max_size=5),
    extra_args=st.lists(st.text(min_size=0, max_size=6), max_size=4),
)
def test_probe_command_carries_determinism_controls(
    interpreter: str, segments: list[str], extra_args: list[str]
) -> None:
    worktree = Path("/tmp").joinpath(*segments)
    command = compose_probe_command(interpreter, worktree, extra_args=extra_args)
    argv = command.argv

    # Import mode (Req 6.2).
    assert "--import-mode=importlib" in argv
    # Cache provider disabled and random ordering disabled (Req 7.1, 7.2).
    assert _has_adjacent(argv, "-p", "no:cacheprovider")
    assert _has_adjacent(argv, "-p", "no:randomly")
    # Repository addopts neutralised (Req 7.3).
    assert _has_adjacent(argv, "-o", "addopts=")
    # Fixed rootdir pointing at the worktree (Req 7.5).
    assert _has_adjacent(argv, "--rootdir", str(worktree))
    # Interpreter drives pytest as a module.
    assert argv[0] == interpreter
    assert argv[1:3] == ("-m", "pytest")

    # Fixed hash seed + no bytecode writing (Req 7.4).
    assert command.env.get("PYTHONHASHSEED") == "0"
    assert command.env.get("PYTHONDONTWRITEBYTECODE") == "1"

    # extra_args are appended AFTER the controls, never displacing them.
    if extra_args:
        assert argv[-len(extra_args):] == tuple(extra_args)


def _has_adjacent(argv: tuple[str, ...], flag: str, value: str) -> bool:
    """True if `flag` is immediately followed by `value` somewhere in argv."""
    return any(
        argv[i] == flag and argv[i + 1] == value for i in range(len(argv) - 1)
    )


# --- Property 11: Bytecode is purged around every probe --------------------


@st.composite
def _bytecode_tree(draw: st.DrawFn) -> list[str]:
    """Generate relative directory paths in which to plant cache artifacts."""
    depth_dirs = draw(
        st.lists(
            st.lists(_path_segment, min_size=1, max_size=3).map(lambda p: "/".join(p)),
            min_size=1,
            max_size=5,
        )
    )
    return depth_dirs


def _plant_bytecode(root: Path, rel_dirs: list[str]) -> None:
    """Create __pycache__/.pytest_cache dirs and loose .pyc files under root."""
    (root / "__pycache__").mkdir(parents=True, exist_ok=True)
    (root / "__pycache__" / "mod.cpython-311.pyc").write_bytes(b"\x00")
    (root / ".pytest_cache").mkdir(parents=True, exist_ok=True)
    (root / ".pytest_cache" / "CACHEDIR.TAG").write_text("x")
    for rel in rel_dirs:
        d = root / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "__pycache__").mkdir(exist_ok=True)
        (d / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        (d / "stale.pyo").write_bytes(b"\x00")


def _has_bytecode(root: Path) -> bool:
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath).name in ("__pycache__", ".pytest_cache"):
            return True
        if any(name.endswith((".pyc", ".pyo")) for name in filenames):
            return True
    return False


# Feature: verifierlock, Property 11: For any probe end state - normal
# completion or forced termination (timeout) - the post-probe purge leaves no
# cached bytecode or pytest-cache directories under that probe's worktree.
@settings(max_examples=150, deadline=None)
@given(rel_dirs=_bytecode_tree(), timed_out=st.booleans())
def test_bytecode_purged_around_probe(rel_dirs: list[str], timed_out: bool) -> None:
    worktree = Path(tempfile.mkdtemp(prefix="vl-purge-"))
    try:
        # The scripted launcher plants bytecode mid-"execution", as real pytest
        # would, on both the normal and the timed-out path.
        launch = LaunchResult(
            returncode=None if timed_out else 0,
            stdout="1 passed",
            stderr="",
            timed_out=timed_out,
            elapsed_seconds=0.5,
        )
        launcher = _scripted_launcher(
            launch, on_launch=lambda *a: _plant_bytecode(worktree, rel_dirs)
        )

        result = run_probe(worktree, probe_id="P1", timeout=1.0, launcher=launcher)

        # Whatever the end state, no cache artifacts remain (Req 7.6, 7.7).
        assert not _has_bytecode(worktree)
        if timed_out:
            assert result.outcome is ProbeOutcome.INCONCLUSIVE
    finally:
        shutil.rmtree(worktree, ignore_errors=True)


def test_purge_bytecode_directly_removes_all_artifacts(tmp_path: Path) -> None:
    _plant_bytecode(tmp_path, ["a", "a/b", "c"])
    assert _has_bytecode(tmp_path)
    purge_bytecode(tmp_path)
    assert not _has_bytecode(tmp_path)


def test_purge_bytecode_on_missing_root_is_noop(tmp_path: Path) -> None:
    purge_bytecode(tmp_path / "does-not-exist")  # must not raise


# --- Property 17: Timeout produces INCONCLUSIVE with a recorded duration ----


# Feature: verifierlock, Property 17: For any probe that exceeds its timeout, the
# outcome is INCONCLUSIVE, the reason cites the timeout, and the elapsed
# duration is recorded.
@settings(max_examples=200)
@given(
    elapsed=st.floats(min_value=0.01, max_value=3600.0),
    timeout=st.floats(min_value=0.01, max_value=3600.0),
    leftover_output=st.text(max_size=40),
)
def test_timeout_is_inconclusive_with_recorded_duration(
    elapsed: float, timeout: float, leftover_output: str
) -> None:
    launch = LaunchResult(
        returncode=None,
        stdout=leftover_output,
        stderr="",
        timed_out=True,
        elapsed_seconds=elapsed,
    )
    result = run_probe(
        Path("/tmp/vl-timeout-nonexistent"),
        probe_id="P0",
        repetition=1,
        timeout=timeout,
        launcher=_scripted_launcher(launch),
    )

    assert result.outcome is ProbeOutcome.INCONCLUSIVE
    assert result.exit_code is None  # terminated -> no exit code
    assert result.reason is not None
    assert result.reason.startswith(reasons.PROBE_TIMEOUT)
    assert result.elapsed_seconds == elapsed
    assert result.probe_id == "P0"
    assert result.repetition == 1


# --- Property 18: The inter-test import limitation is INCONCLUSIVE ----------

_TEST_MODULE_NAMES = ["test_helpers", "test_utils", "conftest", "tests", "test_common"]
_THIRD_PARTY_NAMES = ["numpy", "requests", "django", "yaml", "pydantic"]


# Feature: verifierlock, Property 18: For any probe that cannot import a test
# module because it depends on another test module or a test-utility module
# inside the tests tree, the probe outcome is INCONCLUSIVE citing the import
# limitation.
@settings(max_examples=200)
@given(
    module=st.sampled_from(_TEST_MODULE_NAMES),
    returncode=st.sampled_from([1, 2, 3]),  # collection failures surface as various codes
)
def test_inter_test_import_limitation_is_inconclusive(
    module: str, returncode: int
) -> None:
    output = (
        f"ImportError while importing test module '/repo/tests/test_x.py'.\n"
        f"Hint: ...\n"
        f"ModuleNotFoundError: No module named '{module}'\n"
    )
    launch = LaunchResult(
        returncode=returncode,
        stdout=output,
        stderr="",
        timed_out=False,
        elapsed_seconds=0.2,
    )
    result = run_probe(
        Path("/tmp/vl-import-nonexistent"),
        probe_id="P1",
        launcher=_scripted_launcher(launch),
    )

    assert result.outcome is ProbeOutcome.INCONCLUSIVE
    assert result.reason is not None
    assert result.reason.startswith(reasons.IMPORT_LIMITATION)
    assert module in result.reason


# Feature: verifierlock, Property 18 (negative): a genuine missing third-party
# dependency during collection is NOT the inter-test import limitation (it is
# ENV_INCOMPATIBLE territory), so detect_import_limitation must not fire.
@settings(max_examples=200)
@given(module=st.sampled_from(_THIRD_PARTY_NAMES))
def test_third_party_import_failure_is_not_import_limitation(module: str) -> None:
    output = (
        f"ImportError while importing test module '/repo/tests/test_x.py'.\n"
        f"ModuleNotFoundError: No module named '{module}'\n"
    )
    assert detect_import_limitation(output) is None


def test_no_collection_marker_means_no_import_limitation() -> None:
    # A plain runtime ModuleNotFoundError without a collection marker is not a
    # collection-time import limitation.
    assert detect_import_limitation("No module named 'test_helpers'") is None


# --- Example tests: abort path and plain classification --------------------


def test_exit_code_2_raises_probe_abort() -> None:
    launch = LaunchResult(
        returncode=2, stdout="", stderr="", timed_out=False, elapsed_seconds=0.1
    )
    with pytest.raises(ProbeAbort) as excinfo:
        run_probe(
            Path("/tmp/vl-abort-nonexistent"),
            probe_id="P1",
            launcher=_scripted_launcher(launch),
        )
    assert excinfo.value.probe_id == "P1"


def test_exit_code_0_is_all_passed_with_counts() -> None:
    launch = LaunchResult(
        returncode=0,
        stdout="collected 12 items\n\n12 passed in 1.20s",
        stderr="",
        timed_out=False,
        elapsed_seconds=1.2,
    )
    result = run_probe(
        Path("/tmp/vl-pass-nonexistent"),
        probe_id="P0",
        launcher=_scripted_launcher(launch),
    )
    assert result.outcome is ProbeOutcome.ALL_PASSED
    assert (result.collected, result.passed, result.failed, result.skipped) == (12, 12, 0, 0)
    assert result.reason is None


def test_exit_code_1_is_tests_failed_with_counts() -> None:
    launch = LaunchResult(
        returncode=1,
        stdout="collected 12 items\n\n1 failed, 10 passed, 1 skipped in 1.30s",
        stderr="",
        timed_out=False,
        elapsed_seconds=1.3,
    )
    result = run_probe(
        Path("/tmp/vl-fail-nonexistent"),
        probe_id="P1",
        launcher=_scripted_launcher(launch),
    )
    assert result.outcome is ProbeOutcome.TESTS_FAILED
    assert (result.collected, result.passed, result.failed, result.skipped) == (12, 10, 1, 1)

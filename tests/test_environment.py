"""Unit tests for the Environment_Builder dependency discovery and the
dependency-only guarantee (Task 13.8).

Two layers:

1. **Planning (pure, offline, always runs).** The planned command sequence for
   every discovery source must install dependencies ONLY and never install the
   project package (`pip install .`, `-e .`, `setup.py install`). The
   `pyproject.toml` path must use the verified `uv pip compile` -> `install -r`
   scheme (the design's provisional `--only-deps` flag does not exist).
2. **Real build (offline, zero-dependency project).** A really-built venv must
   record `installed_project=False` / `install_kind="deps_only"` and must NOT
   contain the project package in its site-packages.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from verifierlock import reasons
from verifierlock.environment import (
    DiscoveryInputs,
    Environment_Builder,
    RunResult,
    SRC_NONE,
    SRC_PYPROJECT,
    SRC_REQUIREMENTS,
    SRC_SETUP_PY,
    gather_discovery,
    plan_deps_only_commands,
    select_source,
)

# Command fragments that would install the PROJECT package (forbidden).
_PROJECT_INSTALL_MARKERS = ("setup.py", "-e")


def _flatten(steps) -> list[str]:
    out: list[str] = []
    for step in steps:
        out.extend(step)
    return out


def _asserts_no_project_install(plan) -> None:
    assert plan.installs_project is False
    tokens = _flatten(plan.install_steps)
    # No editable / setup.py install anywhere in the planned steps.
    for marker in _PROJECT_INSTALL_MARKERS:
        assert marker not in tokens, f"planned steps install the project ({marker!r}): {tokens}"
    # A bare `install .` (project install) must not appear either.
    for step in plan.install_steps:
        if "install" in step:
            idx = step.index("install")
            assert "." not in step[idx + 1 :], f"planned step installs project: {step}"


# --- Discovery precedence (pure) -------------------------------------------


def test_discovery_precedence_install_cmd_wins() -> None:
    inputs = DiscoveryInputs(
        install_cmd="uv pip install -r reqs.txt",
        has_pyproject_deps=True,
        requirement_files=("requirements.txt",),
        has_setup_py=True,
    )
    assert select_source(inputs) == "install_cmd"


def test_discovery_precedence_pyproject_over_requirements() -> None:
    inputs = DiscoveryInputs(
        has_pyproject_deps=True, requirement_files=("requirements.txt",), has_setup_py=True
    )
    assert select_source(inputs) == SRC_PYPROJECT


def test_discovery_precedence_requirements_over_setup() -> None:
    inputs = DiscoveryInputs(requirement_files=("requirements.txt",), has_setup_py=True)
    assert select_source(inputs) == SRC_REQUIREMENTS


def test_discovery_setup_py_last() -> None:
    assert select_source(DiscoveryInputs(has_setup_py=True)) == SRC_SETUP_PY


def test_discovery_none_maps_to_undiscoverable() -> None:
    assert select_source(DiscoveryInputs()) == SRC_NONE


# --- Planning is deps-only for every source (pure) -------------------------


def test_pyproject_plan_uses_compile_then_install_never_project() -> None:
    inputs = DiscoveryInputs(has_pyproject_deps=True, project_name="authpkg", extras=("test",))
    plan = plan_deps_only_commands(
        "uv", SRC_PYPROJECT, Path("/tmp/env"), Path("/repo"), inputs
    )
    tokens = _flatten(plan.install_steps)
    assert "compile" in tokens  # resolve deps
    assert "-r" in tokens        # install the resolved deps only
    assert "--extra" in tokens and "test" in tokens
    _asserts_no_project_install(plan)


def test_requirements_plan_is_deps_only() -> None:
    inputs = DiscoveryInputs(requirement_files=("requirements.txt", "requirements-dev.txt"))
    plan = plan_deps_only_commands(
        "uv", SRC_REQUIREMENTS, Path("/tmp/env"), Path("/repo"), inputs
    )
    tokens = _flatten(plan.install_steps)
    assert tokens.count("-r") == 2  # both requirement files
    _asserts_no_project_install(plan)


def test_install_cmd_plan_runs_verbatim() -> None:
    inputs = DiscoveryInputs(install_cmd="uv pip install -r custom.txt")
    plan = plan_deps_only_commands(
        "uv", "install_cmd", Path("/tmp/env"), Path("/repo"), inputs
    )
    assert plan.install_steps == (("uv", "pip", "install", "-r", "custom.txt"),)
    _asserts_no_project_install(plan)


def test_none_plan_carries_deps_undiscoverable_error() -> None:
    plan = plan_deps_only_commands(
        "uv", SRC_NONE, Path("/tmp/env"), Path("/repo"), DiscoveryInputs()
    )
    assert plan.error == reasons.DEPS_UNDISCOVERABLE


# --- Discovery reads pyproject correctly -----------------------------------


def test_gather_discovery_reads_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "mypkg"\nversion = "1.0"\n'
        "dependencies = []\n\n"
        '[project.optional-dependencies]\ntest = ["pytest"]\ndev = ["ruff"]\n'
    )
    inputs = gather_discovery(tmp_path)
    assert inputs.has_pyproject_deps is True
    assert inputs.project_name == "mypkg"
    assert inputs.extras == ("dev", "test")


def test_gather_discovery_none_when_empty(tmp_path: Path) -> None:
    assert select_source(gather_discovery(tmp_path)) == SRC_NONE


# --- DEPS_UNDISCOVERABLE build result (uses a fake runner, offline) ---------


def test_build_undiscoverable_is_not_built(tmp_path: Path) -> None:
    def _runner(argv, cwd, env):  # should never be called
        raise AssertionError("no commands should run when deps are undiscoverable")

    env = Environment_Builder(tool="uv", runner=_runner).build(tmp_path, "base")
    assert env.built is False
    assert env.python_path is None
    assert env.error is not None and env.error.startswith(reasons.DEPS_UNDISCOVERABLE)


def test_build_records_deps_only_flags_with_fake_runner(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "mypkg"\nversion = "1.0"\ndependencies = []\n'
    )
    ran: list[tuple[str, ...]] = []

    def _runner(argv, cwd, env):
        ran.append(argv)
        return RunResult(0, "", "")

    built = Environment_Builder(tool="uv", runner=_runner).build(tmp_path, "head")
    assert built.built is True
    assert built.installed_project is False
    assert built.install_kind == "deps_only"
    assert built.discovery == SRC_PYPROJECT
    # No planned command installed the project package.
    flat = [tok for argv in ran for tok in argv]
    assert "-e" not in flat


# --- Real offline build: project package stays out of site-packages ---------


def test_real_build_excludes_project_package(tmp_path: Path) -> None:
    """A really-built, zero-dependency environment must not contain the project
    package -- the core dependency-only guarantee (Concern 3)."""
    wt = tmp_path / "wt"
    (wt / "mypkg").mkdir(parents=True)
    (wt / "mypkg" / "__init__.py").write_text("VALUE = 1\n")
    (wt / "pyproject.toml").write_text(
        '[project]\nname = "mypkg"\nversion = "0.1.0"\ndependencies = []\n'
    )

    env = Environment_Builder().build(wt, "base")
    if not env.ok or env.python_path is None or not Path(env.python_path).exists():
        pytest.skip(f"environment could not be built in this sandbox: {env.error}")

    assert env.installed_project is False
    assert env.install_kind == "deps_only"

    # The project package must be importable ONLY via the worktree, never from
    # the env's own site-packages.
    from_site = subprocess.run(
        [str(env.python_path), "-c", "import mypkg"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert from_site.returncode != 0, "project package leaked into site-packages"

    from_worktree = subprocess.run(
        [str(env.python_path), "-c", "import mypkg; print(mypkg.VALUE)"],
        capture_output=True, text=True, cwd=str(wt),
        env={**_clean_env(), "PYTHONPATH": str(wt)},
    )
    assert from_worktree.returncode == 0 and "1" in from_worktree.stdout


def _clean_env() -> dict[str, str]:
    import os
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env

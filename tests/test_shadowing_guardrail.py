"""Source-shadowing guardrail integration tests (Tasks 13.6, 13.7).

These are the mandatory standing regression guard for design Concern 3: they
prove that each probe runs the source it is *supposed* to run, using REAL
environment builds and REAL pytest subprocesses (no fakes). If a future change
re-installed the project package into site-packages, it would shadow the
on-disk worktree source and these tests would fail.

- **13.6 P2-runs-base-source:** the base worktree defines a base-only sentinel;
  the test asserts that during P2 (base source + grafted head tests, head env)
  the BASE sentinel is observed.
- **13.7 P3-runs-head-source:** symmetric; during P3 (head source + grafted base
  tests, base env) the HEAD sentinel is observed.

The fixture project declares NO third-party dependencies, so the environment
build is fully offline. The tests skip only if `uv`/`venv` cannot build an
environment at all in this sandbox.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from verifierlock.environment import Environment_Builder
from verifierlock.probe import run_p2, run_p3
from verifierlock.types import ProbeOutcome

# The grafted guardrail test records the production sentinel it actually
# observed to the file named by VL_GUARD_OUT, and asserts the source loaded at
# all. Which value lands in that file proves which on-disk source ran.
_GUARD_TEST = """\
import os
import authpkg


def test_records_observed_sentinel():
    out = os.environ["VL_GUARD_OUT"]
    with open(out, "w") as fh:
        fh.write(authpkg.SENTINEL)
    assert authpkg.SENTINEL in ("base", "head")
"""

# pytest is a legitimate third-party test dependency, so the deps-only env
# installs it (Concern 3: dependencies only). The project package `authpkg` is
# still never installed -- that is exactly what the guardrail proves.
_PYPROJECT = """\
[project]
name = "authpkg"
version = "0.1.0"
dependencies = ["pytest"]
"""


def _make_worktree(root: Path, name: str, sentinel: str) -> Path:
    """Create a minimal installable worktree whose production sentinel is
    `sentinel` and whose test suite records the observed sentinel."""
    wt = root / name
    (wt / "authpkg").mkdir(parents=True)
    (wt / "authpkg" / "__init__.py").write_text(f'SENTINEL = "{sentinel}"\n')
    (wt / "pyproject.toml").write_text(_PYPROJECT)
    (wt / "tests").mkdir()
    (wt / "tests" / "test_guard.py").write_text(_GUARD_TEST)
    return wt


def _build_env(worktree: Path, revision: str):
    env = Environment_Builder().build(worktree, revision)
    if not env.ok or env.python_path is None or not Path(env.python_path).exists():
        pytest.skip(f"could not build {revision} environment in this sandbox: {env.error}")
    # The dependency-only guarantee must hold for the guardrail to be meaningful.
    assert env.installed_project is False
    assert env.install_kind == "deps_only"
    return env


# --- 13.6: P2 runs the base source -----------------------------------------


def test_p2_runs_base_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base_wt = _make_worktree(tmp_path, "base", "base")
    head_wt = _make_worktree(tmp_path, "head", "head")
    head_env = _build_env(head_wt, "head")

    out_file = tmp_path / "p2_observed.txt"
    monkeypatch.setenv("VL_GUARD_OUT", str(out_file))

    # P2 = BASE source (on disk) + grafted HEAD tests, under the HEAD env.
    result = run_p2(base_wt, head_wt, ["tests"], head_env, timeout=120)

    observed = out_file.read_text().strip() if out_file.exists() else "<none>"
    assert observed == "base", (
        f"P2 observed {observed!r}; expected 'base' -- P2 must execute the on-disk "
        "BASE worktree source, not an installed or head copy (Concern 3)."
    )
    # Base source is present and imports cleanly under the head env, so the
    # grafted test passes (Req 4.6: not INCONCLUSIVE when it collects).
    assert result.outcome is ProbeOutcome.ALL_PASSED
    assert result.probe_id == "P2"


# --- 13.7: P3 runs the head source -----------------------------------------


def test_p3_runs_head_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base_wt = _make_worktree(tmp_path, "base", "base")
    head_wt = _make_worktree(tmp_path, "head", "head")
    base_env = _build_env(base_wt, "base")

    out_file = tmp_path / "p3_observed.txt"
    monkeypatch.setenv("VL_GUARD_OUT", str(out_file))

    # P3 = HEAD source (on disk) + grafted BASE tests, under the BASE env.
    result = run_p3(head_wt, base_wt, ["tests"], base_env, timeout=120)

    observed = out_file.read_text().strip() if out_file.exists() else "<none>"
    assert observed == "head", (
        f"P3 observed {observed!r}; expected 'head' -- P3 must execute the on-disk "
        "HEAD worktree source (Concern 3)."
    )
    assert result.outcome is ProbeOutcome.ALL_PASSED
    assert result.probe_id == "P3"


def test_project_package_not_in_built_env_site_packages(tmp_path: Path) -> None:
    """Directly confirm the dependency-only guarantee at the venv level: the
    project package is importable from the worktree but NOT from the env's
    site-packages."""
    import subprocess

    head_wt = _make_worktree(tmp_path, "head", "head")
    env = _build_env(head_wt, "head")

    # With no worktree on sys.path, importing the project must fail (it was
    # never installed into the venv).
    proc = subprocess.run(
        [str(env.python_path), "-c", "import authpkg"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert proc.returncode != 0, (
        "authpkg must NOT be importable from the built env alone -- the project "
        "package was installed into site-packages, violating the dependency-only "
        "guarantee (Concern 3)."
    )
    shutil.rmtree(tmp_path / "head-head-env", ignore_errors=True)

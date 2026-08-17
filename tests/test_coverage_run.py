"""Integration tests for the instrumented P1 coverage run (Task 14.2).

These exercise `probe.run_p1_coverage` with a REAL `coverage run -m pytest`
subprocess (no fakes) and feed its emitted Cobertura XML into the pure
`coverage.map_coverage`, proving the two halves of design Concern 2 connect:

- **Emission + mapping (Req 9.1, 9.2):** the coverage run emits parseable
  Cobertura XML that maps onto changed head-revision production lines -- a
  changed line the tests exercise is `covered=True`, a changed line they do not
  is `covered=False`.
- **Unavailable coverage (Req 9.2, verdict row 10.12):** when no coverage can be
  produced, `run_p1_coverage` yields an empty document and a
  COVERAGE_UNAVAILABLE reason, and mapping that document reports
  `available=False` (COVERAGE_UNAVAILABLE).

The fixture package declares no third-party dependencies and runs under the
current interpreter (which has coverage.py + pytest installed), so the run is
fully offline. Tests skip only if coverage.py is unavailable in this sandbox.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from verifierlock import reasons
from verifierlock.coverage import map_coverage
from verifierlock.probe import derive_coverage_sources, run_p1_coverage

coverage = pytest.importorskip("coverage")


# Production module: `add`'s body is exercised by the test, `sub`'s body is not.
#  1: def add(a, b):
#  2:     return a + b
#  3: (blank)
#  4: (blank)
#  5: def sub(a, b):
#  6:     return a - b
_OPS = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"

_TEST = "from calc.ops import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def _make_p1_worktree(root: Path, *, with_test: bool = True) -> Path:
    """Create a minimal `p1` worktree: the `calc` package plus (optionally) a
    test that exercises `calc.ops.add`."""
    wt = root / "p1"
    (wt / "calc").mkdir(parents=True)
    (wt / "calc" / "__init__.py").write_text("")
    (wt / "calc" / "ops.py").write_text(_OPS)
    if with_test:
        (wt / "tests").mkdir()
        (wt / "tests" / "test_ops.py").write_text(_TEST)
    return wt


def _base_env(worktree: Path) -> dict[str, str]:
    """Pin PYTHONPATH to the worktree so `import calc` resolves on disk."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(worktree)] + ([existing] if existing else [])
    )
    return env


# --- derive_coverage_sources (pure part of Task 14.1) ----------------------


def test_derive_coverage_sources_from_production_files() -> None:
    assert derive_coverage_sources(["calc/ops.py", "calc/__init__.py"]) == ("calc",)
    # src-layout is stripped; a top-level module keeps its module name.
    assert derive_coverage_sources(["src/pkg/mod.py", "app.py"]) == ("app", "pkg")
    # Deterministic, de-duplicated, sorted.
    assert derive_coverage_sources(["b/x.py", "a/y.py", "b/z.py"]) == ("a", "b")


# --- Emission + mapping (Req 9.1, 9.2) -------------------------------------


def test_coverage_run_emits_cobertura_that_maps_onto_changed_lines(
    tmp_path: Path,
) -> None:
    worktree = _make_p1_worktree(tmp_path)

    result = run_p1_coverage(
        worktree,
        ["calc/ops.py"],
        interpreter=sys.executable,
        base_env=_base_env(worktree),
        timeout=120,
    )

    assert result.sources == ("calc",)
    assert result.reason is None, f"coverage was unavailable: {result.reason}"
    assert result.cobertura_xml.strip(), "expected a non-empty Cobertura document"

    # The emitted XML must parse and map onto the changed head lines: line 2
    # (add's body) is exercised; line 6 (sub's body) is not.
    changed = {"calc/ops.py": frozenset({2, 6})}
    mapping = map_coverage(result.cobertura_xml, changed)

    assert mapping.available is True
    assert mapping.reason is None
    covered = {cl.line: cl.covered for cl in mapping.lines}
    assert covered == {2: True, 6: False}


def test_coverage_run_result_records_the_run_without_feeding_the_verdict(
    tmp_path: Path,
) -> None:
    """The instrumented run records its own probe result (for the Evidence
    Record) but is labelled distinctly so it can never be mistaken for the
    verdict-bearing P1 probe (design Concern 2)."""
    worktree = _make_p1_worktree(tmp_path)

    result = run_p1_coverage(
        worktree,
        ["calc/ops.py"],
        interpreter=sys.executable,
        base_env=_base_env(worktree),
        timeout=120,
    )

    assert result.probe.probe_id == "P1-COV"
    assert result.probe.passed == 1
    assert result.probe.failed == 0


# --- Unavailable coverage (Req 9.2, verdict row 10.12) ---------------------


def test_no_measured_code_yields_coverage_unavailable(tmp_path: Path) -> None:
    """When nothing is measured (no source set and no tests exercising code),
    coverage collects no line data, so the run yields an empty/undeterminable
    document and mapping reports COVERAGE_UNAVAILABLE (verdict row 10.12)."""
    worktree = _make_p1_worktree(tmp_path, with_test=False)

    # No production files -> no `--source`, and no tests -> nothing is imported
    # or executed, so coverage has no line data to report.
    result = run_p1_coverage(
        worktree,
        [],
        interpreter=sys.executable,
        base_env=_base_env(worktree),
        timeout=120,
    )

    assert result.sources == ()

    mapping = map_coverage(result.cobertura_xml, {"calc/ops.py": frozenset({2, 6})})
    assert mapping.available is False
    assert mapping.lines == ()
    assert mapping.reason is not None
    assert mapping.reason.startswith(reasons.COVERAGE_UNAVAILABLE)

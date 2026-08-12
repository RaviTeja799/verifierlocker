"""Unit tests for the Static_Analyzer pattern detectors (Task 11.3).

Canonical weakening snippets produce the expected finding kinds (Req 5.1, 5.2,
5.4, 5.5). Each test drives `analyze` with a hand-built `Diff` carrying a single
`FileDiff`/`DiffHunk` so the offending line and its expected finding kind are
unambiguous.
"""

from __future__ import annotations

from verifierlock.static_analyzer import (
    CHANGED_SUCCESS_COMMAND,
    CONTINUE_ON_ERROR,
    DELETED_ASSERTION,
    DISABLED_LINT,
    DISABLED_TYPECHECK,
    FIXTURE_REMOVED_FAILING_CONDITION,
    LOWERED_FAIL_UNDER,
    NEW_COVERAGE_EXCLUSION,
    NEW_DESELECT,
    NEW_SKIP,
    NEW_XFAIL,
    WEAKENED_ASSERTION,
    analyze,
)
from verifierlock.types import Diff, DiffHunk, FileDiff

_EMPTY_NODES: frozenset[str] = frozenset()


def _analyze_one(
    path: str,
    *,
    header: str = "@@ -1,3 +1,3 @@",
    added: tuple[str, ...] = (),
    removed: tuple[str, ...] = (),
    context: tuple[str, ...] = (),
):
    diff = Diff(
        changed_paths=(path,),
        file_diffs=(
            FileDiff(
                path=path,
                hunks=(
                    DiffHunk(
                        header=header,
                        added_lines=added,
                        removed_lines=removed,
                        context_lines=context,
                    ),
                ),
            ),
        ),
    )
    return analyze(diff, _EMPTY_NODES, _EMPTY_NODES)


def _kinds(findings) -> set[str]:
    return {f.kind for f in findings}


def test_deleted_assertion_detected() -> None:
    findings = _analyze_one(
        "tests/test_auth.py",
        removed=("    assert response.status_code == 403",),
    )
    assert DELETED_ASSERTION in _kinds(findings)
    # No added assert in the hunk -> deleted, not weakened.
    assert WEAKENED_ASSERTION not in _kinds(findings)


def test_weakened_assertion_when_replacement_added() -> None:
    findings = _analyze_one(
        "tests/test_auth.py",
        removed=("    assert response.status_code == 403",),
        added=("    assert response.status_code in (200, 403)",),
    )
    assert WEAKENED_ASSERTION in _kinds(findings)
    assert DELETED_ASSERTION not in _kinds(findings)


def test_new_skip_marker_detected() -> None:
    findings = _analyze_one(
        "tests/test_auth.py",
        added=("@pytest.mark.skip(reason='flaky')",),
    )
    assert NEW_SKIP in _kinds(findings)


def test_new_skipif_marker_detected() -> None:
    findings = _analyze_one(
        "tests/test_auth.py",
        added=("@pytest.mark.skipif(True, reason='disabled')",),
    )
    assert NEW_SKIP in _kinds(findings)


def test_inline_pytest_skip_call_detected() -> None:
    findings = _analyze_one(
        "tests/test_auth.py",
        added=("    pytest.skip('not ready')",),
    )
    assert NEW_SKIP in _kinds(findings)


def test_new_xfail_marker_detected() -> None:
    findings = _analyze_one(
        "tests/test_auth.py",
        added=("@pytest.mark.xfail(reason='known bug')",),
    )
    assert NEW_XFAIL in _kinds(findings)


def test_new_deselect_detected() -> None:
    findings = _analyze_one(
        "pytest.ini",
        added=("    --deselect tests/test_auth.py::test_forbidden",),
    )
    assert NEW_DESELECT in _kinds(findings)


def test_new_coverage_exclusion_pragma_detected() -> None:
    findings = _analyze_one(
        "src/auth.py",
        added=("    if user.is_admin:  # pragma: no cover",),
    )
    assert NEW_COVERAGE_EXCLUSION in _kinds(findings)


def test_lowered_fail_under_detected() -> None:
    findings = _analyze_one(
        ".coveragerc",
        removed=("fail_under = 90",),
        added=("fail_under = 50",),
    )
    lowered = [f for f in findings if f.kind == LOWERED_FAIL_UNDER]
    assert lowered
    assert "90" in lowered[0].detail and "50" in lowered[0].detail


def test_fail_under_not_flagged_when_raised() -> None:
    findings = _analyze_one(
        ".coveragerc",
        removed=("fail_under = 50",),
        added=("fail_under = 90",),
    )
    assert LOWERED_FAIL_UNDER not in _kinds(findings)


def test_fail_under_removed_entirely_is_lowered() -> None:
    findings = _analyze_one(
        ".coveragerc",
        removed=("fail_under = 80",),
    )
    assert LOWERED_FAIL_UNDER in _kinds(findings)


def test_disabled_lint_noqa_detected() -> None:
    findings = _analyze_one(
        "src/auth.py",
        added=("import os  # noqa",),
    )
    assert DISABLED_LINT in _kinds(findings)


def test_disabled_typecheck_type_ignore_detected() -> None:
    findings = _analyze_one(
        "src/auth.py",
        added=("result = compute()  # type: ignore",),
    )
    assert DISABLED_TYPECHECK in _kinds(findings)


def test_disabled_typecheck_ignore_errors_config_detected() -> None:
    findings = _analyze_one(
        "mypy.ini",
        added=("ignore_errors = True",),
    )
    assert DISABLED_TYPECHECK in _kinds(findings)


def test_continue_on_error_detected() -> None:
    findings = _analyze_one(
        ".github/workflows/ci.yml",
        added=("        continue-on-error: true",),
    )
    assert CONTINUE_ON_ERROR in _kinds(findings)


def test_fixture_removed_failing_condition_detected() -> None:
    findings = _analyze_one(
        "tests/conftest.py",
        header="@@ -1,6 +1,4 @@ def db_fixture()",
        removed=("        raise RuntimeError('db unavailable')",),
        context=("@pytest.fixture", "def db_fixture():"),
    )
    assert FIXTURE_REMOVED_FAILING_CONDITION in _kinds(findings)


def test_raise_removed_outside_fixture_is_not_fixture_finding() -> None:
    findings = _analyze_one(
        "src/auth.py",
        header="@@ -10,4 +10,2 @@ def authorize()",
        removed=("        raise PermissionError('denied')",),
        context=("def authorize():",),
    )
    assert FIXTURE_REMOVED_FAILING_CONDITION not in _kinds(findings)


def test_changed_success_command_appended_true_detected() -> None:
    findings = _analyze_one(
        ".github/workflows/ci.yml",
        added=("        run: pytest tests/ || true",),
    )
    assert CHANGED_SUCCESS_COMMAND in _kinds(findings)


def test_findings_are_deterministically_ordered() -> None:
    diff = Diff(
        changed_paths=("tests/test_auth.py",),
        file_diffs=(
            FileDiff(
                path="tests/test_auth.py",
                hunks=(
                    DiffHunk(
                        header="@@ -1,5 +1,5 @@",
                        added_lines=(
                            "@pytest.mark.xfail",
                            "@pytest.mark.skip",
                        ),
                        removed_lines=("    assert x == 1",),
                    ),
                ),
            ),
        ),
    )
    findings = analyze(diff, _EMPTY_NODES, _EMPTY_NODES)
    keys = [(f.file, f.hunk, f.kind, f.detail) for f in findings]
    assert keys == sorted(keys)


def test_clean_diff_produces_no_findings() -> None:
    findings = _analyze_one(
        "src/auth.py",
        added=("    return user.is_authorized(resource)",),
        removed=("    return check(user, resource)",),
    )
    assert findings == ()

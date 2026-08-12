"""Static_Analyzer: inspect the diff without executing repository code (Task 11).

Implements `Static_Analyzer.analyze` from design.md: a pure pre-pass over the
base->head diff (plus the base/head collected pytest node-ID sets) that surfaces
suspicious *weakening* patterns and reduced test selection (Req 5). Every finding
records its file and hunk location (Req 5.5).

## Findings inform, they never decide

Static findings **raise suspicion and inform probe selection only**; they can
never by themselves produce a verdict, and specifically never VERIFIER_WEAKENED
(Req 5.6). That guarantee is enforced structurally: this module is a leaf that
returns `StaticFinding`s, and the `Verdict_Engine` (`decide`) reads probe
outcomes and coverage, not findings. Because findings are advisory, this analyzer
deliberately errs toward *over*-detection: a pattern that merely *looks* like a
weakening is flagged. A false positive costs a reviewer a glance; it can never
flip a verdict.

## Purity

`analyze` performs NO filesystem I/O, NO subprocess calls, and reads only its
arguments. The node-ID sets are supplied by the caller (collected elsewhere);
computing them is not this pure function's job. Findings are returned in a
deterministic order (sorted by `(file, hunk, kind, detail)`) so the Evidence
Record stays reproducible.

## What is detected (Req 5.1-5.4)

Diff-pattern detectors (per changed file / hunk):

- `deleted_assertion` / `weakened_assertion` (5.1) -- a removed `assert`; if the
  same hunk also adds an `assert`, the removed one is reported as *weakened*
  rather than deleted.
- `new_skip` / `new_xfail` / `new_deselect` (5.2) -- an added skip/xfail marker
  or a newly added deselection.
- `new_coverage_exclusion` (5.4) -- an added `# pragma: no cover` or coverage
  `exclude_lines` entry.
- `lowered_fail_under` (5.4) -- a coverage `fail_under` threshold lowered or
  removed outright.
- `disabled_lint` (5.4) -- an added `# noqa` / `flake8: noqa` / `pylint: disable`
  / `ruff: noqa`.
- `disabled_typecheck` (5.4) -- an added `# type: ignore` / `mypy: ignore-errors`
  / `ignore_errors = true` / `follow_imports = skip`.
- `continue_on_error` (5.4) -- an added CI `continue-on-error: true`.
- `fixture_removed_failing_condition` (5.4) -- a `raise`/`pytest.fail(` removed
  from inside a `@pytest.fixture`.
- `changed_success_command` (5.4) -- a CI/test command mutated so it reports
  success regardless (e.g. an appended `|| true` or `exit 0`).

Node-ID set comparison (5.3):

- `reduced_test_selection` -- one finding per node-ID present in the base
  collected set but absent from the head collected set (the base-minus-head
  difference).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .types import Diff, DiffHunk, FileDiff

# --- Finding kind constants (single source of truth) -----------------------

DELETED_ASSERTION = "deleted_assertion"
WEAKENED_ASSERTION = "weakened_assertion"
NEW_SKIP = "new_skip"
NEW_XFAIL = "new_xfail"
NEW_DESELECT = "new_deselect"
NEW_COVERAGE_EXCLUSION = "new_coverage_exclusion"
LOWERED_FAIL_UNDER = "lowered_fail_under"
DISABLED_LINT = "disabled_lint"
DISABLED_TYPECHECK = "disabled_typecheck"
CONTINUE_ON_ERROR = "continue_on_error"
FIXTURE_REMOVED_FAILING_CONDITION = "fixture_removed_failing_condition"
CHANGED_SUCCESS_COMMAND = "changed_success_command"
REDUCED_TEST_SELECTION = "reduced_test_selection"


@dataclass(frozen=True)
class StaticFinding:
    """One suspicious diff pattern, located by file and hunk (Req 5.5)."""

    kind: str
    file: str
    hunk: str
    detail: str


# --- Detection patterns ----------------------------------------------------

_ASSERT_RE = re.compile(r"^\s*assert\b")
_SKIP_RE = re.compile(
    r"@(?:pytest\.mark\.|unittest\.)skip(?:if|unless)?\b"
    r"|\bpytest\.skip\s*\("
    r"|@skip(?:if|unless)?\b"
)
_XFAIL_RE = re.compile(r"@pytest\.mark\.xfail\b|\bpytest\.xfail\s*\(")
_DESELECT_RE = re.compile(r"--deselect\b|\bdeselect\s*=|\bcollect_ignore\b")
_COVERAGE_EXCLUSION_RE = re.compile(
    r"#\s*pragma:\s*no\s*cover|\bexclude_lines\b|\bno cover\b"
)
_FAIL_UNDER_RE = re.compile(r"fail_under\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)")
_DISABLED_LINT_RE = re.compile(
    r"#\s*noqa|flake8:\s*noqa|#\s*pylint:\s*disable|#\s*ruff:\s*noqa"
)
_DISABLED_TYPECHECK_RE = re.compile(
    r"#\s*type:\s*ignore"
    r"|#\s*mypy:\s*ignore-errors"
    r"|\bignore_errors\s*=\s*(?:true|True|1)\b"
    r"|\bfollow_imports\s*=\s*skip\b"
)
_CONTINUE_ON_ERROR_RE = re.compile(r"continue-on-error\s*:\s*true", re.IGNORECASE)
_FAILING_CONDITION_RE = re.compile(r"\braise\b|\bpytest\.fail\s*\(")
_FIXTURE_RE = re.compile(r"@(?:pytest\.)?fixture\b")
_SUCCESS_MUTATION_RE = re.compile(r"\|\|\s*true\b|\bexit\s+0\b|;\s*true\b", re.IGNORECASE)
# A CI/shell "reporting-success" command line: something that runs a test/lint
# tool. Used only to scope `changed_success_command` so an appended `|| true`
# is read as neutralising a real command rather than incidental shell text.
_COMMAND_RE = re.compile(r"\b(pytest|tox|nox|coverage|flake8|ruff|mypy|make|python)\b")


def _detail(line: str) -> str:
    """A compact, stable representation of an offending line for the record."""
    return line.strip()


def _hunk_has_added_assert(hunk: DiffHunk) -> bool:
    return any(_ASSERT_RE.search(line) for line in hunk.added_lines)


def _hunk_mentions_fixture(hunk: DiffHunk) -> bool:
    """True if the hunk appears to sit inside a pytest fixture.

    A fixture's failing condition (a `raise`/`pytest.fail`) and its
    `@pytest.fixture` decorator commonly appear together in one hunk; we scan
    the header and every line kind so the decorator is found whether it was
    added, removed, or shown as unchanged context.
    """
    for line in (hunk.header, *hunk.added_lines, *hunk.removed_lines, *hunk.context_lines):
        if _FIXTURE_RE.search(line):
            return True
    return False


def _analyze_added_line(file: str, hunk: DiffHunk, line: str) -> list[StaticFinding]:
    """Detectors that fire on a line newly introduced in head."""
    findings: list[StaticFinding] = []
    detail = _detail(line)
    if _SKIP_RE.search(line):
        findings.append(StaticFinding(NEW_SKIP, file, hunk.header, detail))
    if _XFAIL_RE.search(line):
        findings.append(StaticFinding(NEW_XFAIL, file, hunk.header, detail))
    if _DESELECT_RE.search(line):
        findings.append(StaticFinding(NEW_DESELECT, file, hunk.header, detail))
    if _COVERAGE_EXCLUSION_RE.search(line):
        findings.append(StaticFinding(NEW_COVERAGE_EXCLUSION, file, hunk.header, detail))
    if _DISABLED_LINT_RE.search(line):
        findings.append(StaticFinding(DISABLED_LINT, file, hunk.header, detail))
    if _DISABLED_TYPECHECK_RE.search(line):
        findings.append(StaticFinding(DISABLED_TYPECHECK, file, hunk.header, detail))
    if _CONTINUE_ON_ERROR_RE.search(line):
        findings.append(StaticFinding(CONTINUE_ON_ERROR, file, hunk.header, detail))
    if _SUCCESS_MUTATION_RE.search(line) and _COMMAND_RE.search(line):
        findings.append(StaticFinding(CHANGED_SUCCESS_COMMAND, file, hunk.header, detail))
    return findings


def _analyze_removed_line(
    file: str, hunk: DiffHunk, line: str, *, hunk_adds_assert: bool, in_fixture: bool
) -> list[StaticFinding]:
    """Detectors that fire on a line removed from base."""
    findings: list[StaticFinding] = []
    detail = _detail(line)
    if _ASSERT_RE.search(line):
        # A removed assertion is *weakened* when the same hunk substitutes a
        # different assertion for it, otherwise it is outright *deleted*.
        kind = WEAKENED_ASSERTION if hunk_adds_assert else DELETED_ASSERTION
        findings.append(StaticFinding(kind, file, hunk.header, detail))
    # A `raise`/`pytest.fail` removed from inside a fixture drops a failing
    # condition. Guard on `in_fixture` so ordinary removed `raise`s elsewhere
    # are not misreported under this kind.
    if in_fixture and _FAILING_CONDITION_RE.search(line):
        findings.append(
            StaticFinding(FIXTURE_REMOVED_FAILING_CONDITION, file, hunk.header, detail)
        )
    return findings


def _lowered_fail_under(file: str, hunk: DiffHunk) -> list[StaticFinding]:
    """Detect a coverage `fail_under` threshold lowered or removed in a hunk."""
    removed_values = [
        float(m.group(1))
        for line in hunk.removed_lines
        if (m := _FAIL_UNDER_RE.search(line))
    ]
    added_values = [
        float(m.group(1))
        for line in hunk.added_lines
        if (m := _FAIL_UNDER_RE.search(line))
    ]
    if not removed_values:
        return []
    old = max(removed_values)
    if added_values:
        new = max(added_values)
        if new < old:
            return [
                StaticFinding(
                    LOWERED_FAIL_UNDER,
                    file,
                    hunk.header,
                    f"fail_under lowered from {old:g} to {new:g}",
                )
            ]
        return []
    # A fail_under present in base but gone in head -> the threshold was removed
    # entirely, which is a weakening.
    return [
        StaticFinding(
            LOWERED_FAIL_UNDER,
            file,
            hunk.header,
            f"fail_under threshold removed (was {old:g})",
        )
    ]


def _analyze_file(file_diff: FileDiff) -> list[StaticFinding]:
    findings: list[StaticFinding] = []
    file = file_diff.path
    for hunk in file_diff.hunks:
        hunk_adds_assert = _hunk_has_added_assert(hunk)
        in_fixture = _hunk_mentions_fixture(hunk)
        for line in hunk.added_lines:
            findings.extend(_analyze_added_line(file, hunk, line))
        for line in hunk.removed_lines:
            findings.extend(
                _analyze_removed_line(
                    file,
                    hunk,
                    line,
                    hunk_adds_assert=hunk_adds_assert,
                    in_fixture=in_fixture,
                )
            )
        findings.extend(_lowered_fail_under(file, hunk))
    return findings


def _reduced_test_selection(
    base_node_ids: frozenset[str], head_node_ids: frozenset[str]
) -> list[StaticFinding]:
    """Req 5.3 / Property 21: one finding per base node-ID missing from head.

    The reduced-selection finding set is exactly `base_node_ids - head_node_ids`.
    The finding's `file` is the path portion of the node-ID (the text before the
    first `::`); the node-ID has no hunk location, so `hunk` is empty.
    """
    findings: list[StaticFinding] = []
    for node_id in base_node_ids - head_node_ids:
        file = node_id.split("::", 1)[0]
        findings.append(
            StaticFinding(REDUCED_TEST_SELECTION, file, "", node_id)
        )
    return findings


def analyze(
    diff: Diff,
    base_node_ids: frozenset[str],
    head_node_ids: frozenset[str],
) -> tuple[StaticFinding, ...]:
    """Inspect the diff and node-ID sets for weakening patterns (Req 5).

    Pure and total: returns a deterministically ordered tuple of
    `StaticFinding`s (sorted by `(file, hunk, kind, detail)`) and never raises.
    Findings are advisory only and never produce a verdict (Req 5.6).
    """
    findings: list[StaticFinding] = []
    for file_diff in diff.file_diffs:
        findings.extend(_analyze_file(file_diff))
    findings.extend(_reduced_test_selection(base_node_ids, head_node_ids))

    findings.sort(key=lambda f: (f.file, f.hunk, f.kind, f.detail))
    return tuple(findings)

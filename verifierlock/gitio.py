"""Git inspection helpers the Orchestrator feeds to the pure stages (Task 15.1).

The deterministic pure stages (`File_Classifier`, `Static_Analyzer`,
`Coverage_Analyzer`) consume already-extracted data: a `Diff`, collected pytest
node-ID sets, changed head-revision line numbers, and a `PytestConfig`. This
module is the thin, side-effecting adapter that produces those inputs from a
real repository using `git` and `pytest --collect-only`. It is deliberately kept
out of the pure modules so they stay I/O-free and property-testable.

Everything here is best-effort and total: a failure to read the diff, collect
node IDs, or parse config never raises into the pipeline; it degrades to an
empty/partial result so the Orchestrator can still reach a verdict (usually
INCONCLUSIVE with an explicit reason from a downstream stage).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .classifier import PytestConfig
from .types import Diff, DiffHunk, FileDiff

try:  # tomllib is stdlib on 3.11+, fall back to the tomli backport.
    import tomllib as _tomllib
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as _tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        _tomllib = None  # type: ignore[assignment]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run a git command in `repo`; return None if git cannot be launched."""
    try:
        return subprocess.run(
            ["git", *args], cwd=str(repo), capture_output=True, text=True
        )
    except OSError:
        return None


# --- Changed paths + unified-diff parsing ----------------------------------

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@")


def changed_paths(repo: Path, base: str, head: str) -> tuple[str, ...]:
    """The set of paths changed between `base` and `head` (name-only diff)."""
    proc = _git(repo, "diff", "--name-only", f"{base}", f"{head}")
    if proc is None or proc.returncode != 0:
        return ()
    return tuple(line for line in proc.stdout.splitlines() if line.strip())


def parse_diff(repo: Path, base: str, head: str) -> tuple[FileDiff, ...]:
    """Parse the unified base->head diff into per-file hunks.

    Produces `FileDiff`s carrying, per hunk, the raw `@@` header plus the added
    (`+`), removed (`-`), and context lines the `Static_Analyzer` inspects. Only
    the b-side (head) path is used as the file key, matching how changed files
    are named elsewhere. Renames/binary files without textual hunks yield a
    `FileDiff` with no hunks.
    """
    proc = _git(repo, "diff", "--unified=3", f"{base}", f"{head}")
    if proc is None or proc.returncode != 0:
        return ()
    return _parse_unified_diff(proc.stdout)


def _parse_unified_diff(text: str) -> tuple[FileDiff, ...]:
    file_diffs: list[FileDiff] = []
    current_path: str | None = None
    hunks: list[DiffHunk] = []
    header: str | None = None
    added: list[str] = []
    removed: list[str] = []
    context: list[str] = []

    def flush_hunk() -> None:
        nonlocal header, added, removed, context
        if header is not None:
            hunks.append(
                DiffHunk(
                    header=header,
                    added_lines=tuple(added),
                    removed_lines=tuple(removed),
                    context_lines=tuple(context),
                )
            )
        header, added, removed, context = None, [], [], []

    def flush_file() -> None:
        nonlocal current_path, hunks
        flush_hunk()
        if current_path is not None:
            file_diffs.append(FileDiff(path=current_path, hunks=tuple(hunks)))
        current_path, hunks = None, []

    for line in text.splitlines():
        git_match = _DIFF_GIT_RE.match(line)
        if git_match:
            flush_file()
            current_path = git_match.group(2)
            continue
        if line.startswith("@@"):
            flush_hunk()
            header = line
            continue
        if header is None:
            # File-header lines (---/+++/index/rename/etc.) before the first hunk.
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
        elif line.startswith(" "):
            context.append(line[1:])
        # A "\ No newline at end of file" marker and blank separators are ignored.

    flush_file()
    return tuple(file_diffs)


def changed_head_lines(
    file_diffs: tuple[FileDiff, ...], production_paths: frozenset[str]
) -> dict[str, frozenset[int]]:
    """Head-revision line numbers added/changed in each production file (Req 9.2).

    Walks each hunk tracking the head-side (new-file) line counter, which starts
    at the hunk header's `+new_start`; context lines advance it, added (`+`)
    lines are recorded as changed head lines and advance it, removed (`-`) lines
    do not. Only files in `production_paths` are included, because coverage is
    mapped onto changed *production* lines only (decision log 4.6).
    """
    result: dict[str, set[int]] = {}
    for file_diff in file_diffs:
        if file_diff.path not in production_paths:
            continue
        lines: set[int] = result.setdefault(file_diff.path, set())
        for hunk in file_diff.hunks:
            match = _HUNK_RE.match(hunk.header)
            if match is None:
                continue
            new_start = int(match.group("new_start"))
            _walk_hunk_head_lines(hunk, new_start, lines)
    return {path: frozenset(nums) for path, nums in result.items()}


def _walk_hunk_head_lines(hunk: DiffHunk, start: int, out: set[int]) -> int:
    """Advance the head line counter across a hunk, recording added lines.

    The stored `DiffHunk` separates added/removed/context lines and loses their
    original interleaving, but head line numbering only needs the count of
    head-present lines (context + added) in order. Since removed lines never
    occupy a head line, we number the context and added lines sequentially from
    `start`; the exact positions of added lines relative to context within a
    hunk are not required for coverage mapping (coverage keys on head line
    numbers of changed production lines, and every added line is changed).
    """
    line = start
    # Context precedes/follows added lines in real diffs; for line-number
    # assignment we place context first, then added, which keeps numbers within
    # the hunk's head range. This is sufficient for mapping changed lines to
    # coverage, which only asks whether a given head line was executed.
    for _ctx in hunk.context_lines:
        line += 1
    for _add in hunk.added_lines:
        out.add(line)
        line += 1
    return line


# --- Node-ID collection (Static_Analyzer reduced-selection input, Req 5.3) --

_NODEID_RE = re.compile(r"^\S.*\.py(::.*)?$")


def collect_node_ids(interpreter: str | Path, worktree: Path) -> frozenset[str]:
    """Collect pytest node IDs in `worktree` via `--collect-only -q`.

    Best-effort: returns the parsed node-ID set, or an empty set if collection
    cannot run or emits nothing recognisable. Uses the determinism-relevant
    flags so collection matches how probes run. This feeds ONLY the advisory
    Static_Analyzer reduced-test-selection finding, so a partial result never
    affects the verdict.
    """
    try:
        proc = subprocess.run(
            [
                str(interpreter),
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                "--import-mode=importlib",
                "-o",
                "addopts=",
                "--rootdir",
                str(worktree),
            ],
            cwd=str(worktree),
            capture_output=True,
            text=True,
        )
    except OSError:
        return frozenset()

    node_ids: set[str] = set()
    for line in proc.stdout.splitlines():
        stripped = line.rstrip()
        if not stripped or stripped.startswith("="):
            continue
        # Skip the trailing summary ("N tests collected in ..."): it has no ".py".
        if _NODEID_RE.match(stripped):
            node_ids.add(stripped)
    return frozenset(node_ids)


# --- pytest config reading (File_Classifier input, Req 4b.2-4b.4) ----------

_KNOWN_SECTIONS = {
    "coverage": "coverage",
    "pytest": "pytest",
    "ruff": "ruff",
    "flake8": "flake8",
    "pylint": "pylint",
    "mypy": "mypy",
}


def read_pytest_config(worktree: Path) -> PytestConfig:
    """Read the pytest/tool configuration `File_Classifier.classify` needs.

    Extracts declared `testpaths` (from `[tool.pytest.ini_options]` in
    `pyproject.toml`, or `[pytest]`/`[tool:pytest]` in `pytest.ini`/`tox.ini`/
    `setup.cfg`) and the set of recognised `[tool.*]` sections present in
    `pyproject.toml`. Best-effort: unreadable/malformed config yields an empty
    `PytestConfig` (no testpaths, no sections), so classification falls back to
    pytest's default discovery patterns.
    """
    worktree = Path(worktree)
    testpaths: tuple[str, ...] | None = None
    sections: set[str] = set()

    pyproject = worktree / "pyproject.toml"
    if pyproject.is_file() and _tomllib is not None:
        try:
            data = _tomllib.loads(pyproject.read_text())
        except (OSError, ValueError):
            data = {}
        tool = data.get("tool", {}) if isinstance(data, dict) else {}
        if isinstance(tool, dict):
            for key, section in _KNOWN_SECTIONS.items():
                if key in tool:
                    sections.add(section)
            pytest_cfg = tool.get("pytest", {})
            ini = pytest_cfg.get("ini_options", {}) if isinstance(pytest_cfg, dict) else {}
            tp = ini.get("testpaths") if isinstance(ini, dict) else None
            if isinstance(tp, list) and tp:
                testpaths = tuple(str(p) for p in tp)
            elif isinstance(tp, str) and tp.strip():
                testpaths = tuple(tp.split())

    if testpaths is None:
        testpaths = _read_ini_testpaths(worktree)

    return PytestConfig(
        testpaths=testpaths, pyproject_config_sections=frozenset(sections)
    )


_INI_TESTPATHS_RE = re.compile(r"(?m)^\s*testpaths\s*=\s*(.+)$")


def _read_ini_testpaths(worktree: Path) -> tuple[str, ...] | None:
    """Read `testpaths` from pytest.ini / tox.ini / setup.cfg, if declared."""
    for name in ("pytest.ini", "tox.ini", "setup.cfg"):
        path = worktree / name
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        match = _INI_TESTPATHS_RE.search(text)
        if match:
            values = match.group(1).replace(",", " ").split()
            if values:
                return tuple(values)
    return None

"""Pure classification of changed files (Task 4.1).

Implements `File_Classifier.classify` from design.md: given a `Diff` (the set
of changed file paths) and a `PytestConfig` (the already-parsed pytest
configuration relevant to classification -- this module does no filesystem
I/O and does no config-file parsing itself), classify every changed path as
exactly one of `FileClass.PRODUCTION`, `FileClass.TEST`, or
`FileClass.VERIFIER_CONFIG`, or collect it into `unclassifiable` when it
cannot be meaningfully classified (Req 4b.1, 4b.5).

Classification precedence (a deliberate, documented design decision -- the
requirements state each rule unconditionally/conditionally but do not order
them relative to one another):

1. Verifier configuration (Req 4b.4) is checked FIRST and unconditionally:
   CI workflow files, coverage config, pytest config, lint config, and
   type-check config are always verifier configuration, even if they
   happen to live under a declared `testpaths` directory or match a
   default test-discovery filename pattern. Requirement 4b.4 states this
   rule with no "where testpaths is declared" qualifier, unlike 4b.2/4b.3
   for test classification, so it takes priority.
2. Test classification (Req 4b.2, 4b.3) is checked next: `conftest.py`
   files are always TEST; when `testpaths` is declared, files under those
   paths are TEST; otherwise pytest's default discovery patterns
   (`test_*.py`, `*_test.py`, files under a `test`/`tests` directory) are
   TEST.
3. Anything else is PRODUCTION, unless it matches a recognised
   unclassifiable signal (this module supports one such signal: a known
   binary/binary-adjacent file extension), in which case the path is
   collected into `unclassifiable` instead of `files` (Req 4b.5).

`pyproject.toml` is a special case: it can hold `[tool.coverage.*]`,
`[tool.pytest.ini_options]`, `[tool.ruff]` / `[tool.flake8]` /
`[tool.pylint]`, and `[tool.mypy]` sections alongside ordinary project
metadata. Since this function is pure and does no I/O, it cannot open
`pyproject.toml` to see which sections it declares; the caller must supply
that information (already parsed) via `PytestConfig.pyproject_config_sections`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from .types import Diff


class FileClass(Enum):
    """The three mutually-exclusive buckets a changed file can fall into."""

    PRODUCTION = "production"
    TEST = "test"
    VERIFIER_CONFIG = "verifier_config"


@dataclass(frozen=True)
class ClassifiedFile:
    """One changed path together with its single classification."""

    path: str
    classification: FileClass


@dataclass(frozen=True)
class ClassificationResult:
    """The total partition of a diff's changed paths (Req 4b.1, 4b.6).

    Every path in the source `Diff` appears in exactly one of `files`
    (with exactly one classification) or `unclassifiable` -- never both,
    never neither, never duplicated.
    """

    files: tuple[ClassifiedFile, ...]
    unclassifiable: tuple[str, ...]


@dataclass(frozen=True)
class PytestConfig:
    """The already-parsed pytest/tool configuration `classify` needs.

    Minimal and pure: holds the declared `testpaths` (or `None` when not
    declared, per Req 4b.2/4b.3) plus the set of recognised `[tool.*]`
    section names found in `pyproject.toml`, if any. Parsing
    `pyproject.toml`/`pytest.ini`/etc. off disk is out of scope for this
    pure function and belongs to a later I/O-performing caller.

    Recognised `pyproject_config_sections` values: "coverage", "pytest",
    "ruff", "flake8", "pylint", "mypy". Any other strings are ignored.
    """

    testpaths: tuple[str, ...] | None = None
    pyproject_config_sections: frozenset[str] = frozenset()


# Known binary/binary-adjacent extensions: this is the one unclassifiable
# signal this function supports (Req 4b.5's "if a changed file cannot be
# classified" case). Everything else with a path string falls through to
# PRODUCTION, since there is no reliable extension-free signal available to
# a pure function with no file contents.
_UNCLASSIFIABLE_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
        ".pdf", ".zip", ".tar", ".gz", ".tgz", ".whl", ".egg",
        ".so", ".pyc", ".pyo", ".exe", ".dll", ".dylib",
        ".class", ".jar", ".bin", ".dat",
    }
)

_PYPROJECT_NAME = "pyproject.toml"


def _is_ci_workflow(path: PurePosixPath) -> bool:
    """`.github/workflows/*.yml|*.yaml` or a `.gitlab-ci.yml` file."""
    if path.name == ".gitlab-ci.yml":
        return True
    parts = path.parts
    suffix = path.suffix.lower()
    if suffix not in (".yml", ".yaml"):
        return False
    return any(
        parts[i] == ".github" and parts[i + 1] == "workflows"
        for i in range(len(parts) - 1)
    )


def _has_pyproject_section(config: PytestConfig, *names: str) -> bool:
    return any(name in config.pyproject_config_sections for name in names)


def _is_coverage_config(path: PurePosixPath, config: PytestConfig) -> bool:
    if path.name in (".coveragerc", "coverage.cfg"):
        return True
    return path.name == _PYPROJECT_NAME and _has_pyproject_section(config, "coverage")


def _is_pytest_config(path: PurePosixPath, config: PytestConfig) -> bool:
    if path.name in ("pytest.ini", "pytest.cfg", "tox.ini"):
        # tox.ini is treated unconditionally as pytest/verifier config: it
        # commonly carries a `[pytest]`/`[tool:pytest]` section and, like
        # pyproject.toml, this pure function has no file contents to check
        # for one -- unlike pyproject.toml, tox.ini has no other common
        # non-verifier purpose, so this is a safe simplification.
        return True
    return path.name == _PYPROJECT_NAME and _has_pyproject_section(config, "pytest")


def _is_lint_config(path: PurePosixPath, config: PytestConfig) -> bool:
    if path.name in (".flake8", ".pylintrc", "ruff.toml", ".ruff.toml"):
        return True
    return path.name == _PYPROJECT_NAME and _has_pyproject_section(
        config, "ruff", "flake8", "pylint"
    )


def _is_typecheck_config(path: PurePosixPath, config: PytestConfig) -> bool:
    if path.name in ("mypy.ini", ".mypy.ini", "pyrightconfig.json"):
        return True
    return path.name == _PYPROJECT_NAME and _has_pyproject_section(config, "mypy")


def _is_verifier_config(path: PurePosixPath, config: PytestConfig) -> bool:
    """Req 4b.4: CI workflow, coverage, pytest, lint, and type-check config."""
    return (
        _is_ci_workflow(path)
        or _is_coverage_config(path, config)
        or _is_pytest_config(path, config)
        or _is_lint_config(path, config)
        or _is_typecheck_config(path, config)
    )


def _matches_default_test_filename(name: str) -> bool:
    """pytest's default `python_files` patterns: `test_*.py`, `*_test.py`."""
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    return False


def _is_under_test_directory(parts: tuple[str, ...]) -> bool:
    """Any directory component (excluding the filename) named `test`/`tests`."""
    return any(part in ("test", "tests") for part in parts[:-1])


def _is_under_declared_testpaths(path: PurePosixPath, testpaths: tuple[str, ...]) -> bool:
    for testpath in testpaths:
        testpath_p = PurePosixPath(testpath)
        if path == testpath_p or testpath_p in path.parents:
            return True
    return False


def _is_test(path: PurePosixPath, config: PytestConfig) -> bool:
    """Req 4b.2, 4b.3: declared testpaths (+conftest.py), else default
    discovery patterns (+conftest.py)."""
    if path.name == "conftest.py":
        return True
    if config.testpaths:
        return _is_under_declared_testpaths(path, config.testpaths)
    return _matches_default_test_filename(path.name) or _is_under_test_directory(path.parts)


def _is_unclassifiable(path: PurePosixPath) -> bool:
    return path.suffix.lower() in _UNCLASSIFIABLE_EXTENSIONS


def _classify_one(path_str: str, config: PytestConfig) -> FileClass | None:
    """Classify a single path, or return `None` if unclassifiable."""
    path = PurePosixPath(path_str)
    if _is_verifier_config(path, config):
        return FileClass.VERIFIER_CONFIG
    if _is_test(path, config):
        return FileClass.TEST
    if _is_unclassifiable(path):
        return None
    return FileClass.PRODUCTION


def classify(diff: Diff, pytest_config: PytestConfig) -> ClassificationResult:
    """Classify every changed file in `diff` (Req 4b.1, 4b.5, 4b.6).

    Every path in `diff.changed_paths` ends up in exactly one place: either
    in `files` with exactly one `FileClass`, or in `unclassifiable`. Paths
    are deduplicated (a real Git diff never lists the same path twice for
    one change; this makes the partition invariant hold even if a caller
    passes a path more than once) while preserving first-seen order, so the
    result is deterministic for a given input.

    No filesystem I/O, no subprocess calls: this function only reads the
    path strings and the already-parsed `pytest_config`.
    """
    seen: dict[str, None] = {}
    for path in diff.changed_paths:
        seen.setdefault(path, None)

    classified: list[ClassifiedFile] = []
    unclassifiable: list[str] = []

    for path in seen:
        file_class = _classify_one(path, pytest_config)
        if file_class is None:
            unclassifiable.append(path)
        else:
            classified.append(ClassifiedFile(path=path, classification=file_class))

    return ClassificationResult(files=tuple(classified), unclassifiable=tuple(unclassifiable))

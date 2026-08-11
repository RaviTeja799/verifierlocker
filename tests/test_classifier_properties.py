"""Property-based tests for `File_Classifier.classify` (Tasks 4.2, 4.3).

**Property 6: File classification is a total partition**
**Property 7: Test and verifier-config classification rules**

Uses Hypothesis (min 100 examples each) to prove, across varied generated
sets of changed file paths and pytest configurations, that `classify`
always produces a total partition and always obeys the documented test /
verifier-config classification rules.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from verifierlock.classifier import (
    FileClass,
    PytestConfig,
    classify,
)
from verifierlock.types import Diff

# --- Shared generators -----------------------------------------------------

# Safe path-segment alphabet: letters, digits, underscore, hyphen. Avoids
# path separators, dots-only segments, and other characters that would make
# the generated string ambiguous as a POSIX-style relative path component.
_segment_alphabet = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="_-",
    ),
    min_size=1,
    max_size=8,
)

_path_segment = _segment_alphabet.filter(lambda s: s not in (".", "..") and s.strip() != "")

_extension = st.sampled_from(
    [
        "py", "txt", "md", "json", "yaml", "yml", "cfg", "ini", "toml",
        "png", "jpg", "so", "pyc",
    ]
)


@st.composite
def arbitrary_path(draw: st.DrawFn) -> str:
    """A generated relative path with 1-4 directory segments plus a
    filename with a random extension, covering varied directory
    structures and extensions."""
    depth = draw(st.integers(min_value=0, max_value=3))
    dirs = [draw(_path_segment) for _ in range(depth)]
    name = draw(_path_segment)
    ext = draw(_extension)
    filename = f"{name}.{ext}"
    return "/".join([*dirs, filename])


@st.composite
def path_set(draw: st.DrawFn) -> tuple[str, ...]:
    paths = draw(st.lists(arbitrary_path(), min_size=0, max_size=15, unique=True))
    return tuple(paths)


@st.composite
def pytest_config_strategy(draw: st.DrawFn) -> PytestConfig:
    has_testpaths = draw(st.booleans())
    testpaths: tuple[str, ...] | None = None
    if has_testpaths:
        testpaths = tuple(
            draw(st.lists(_path_segment, min_size=1, max_size=3, unique=True))
        )
    sections = draw(
        st.frozensets(
            st.sampled_from(["coverage", "pytest", "ruff", "flake8", "pylint", "mypy"]),
            max_size=3,
        )
    )
    return PytestConfig(testpaths=testpaths, pyproject_config_sections=sections)


# --- Property 6: File classification is a total partition ------------------


@settings(max_examples=150)
@given(paths=path_set(), config=pytest_config_strategy())
def test_classification_is_a_total_partition(
    paths: tuple[str, ...], config: PytestConfig
) -> None:
    """Every changed path ends up in exactly one place: `files` (with
    exactly one classification) or `unclassifiable` -- no duplicates, none
    dropped (Requirements 4b.1, 4b.6)."""
    diff = Diff(changed_paths=paths)
    result = classify(diff, config)

    classified_paths = [f.path for f in result.files]
    unclassifiable_paths = list(result.unclassifiable)

    # No path classified twice, and none dropped: the union of both
    # buckets, deduplicated, equals the deduplicated input set.
    all_output_paths = classified_paths + unclassifiable_paths
    assert sorted(set(all_output_paths)) == sorted(set(paths))

    # No duplicates within or across buckets.
    assert len(classified_paths) == len(set(classified_paths))
    assert len(unclassifiable_paths) == len(set(unclassifiable_paths))
    assert set(classified_paths).isdisjoint(set(unclassifiable_paths))

    # Every classified file has exactly one classification value.
    for f in result.files:
        assert isinstance(f.classification, FileClass)


@settings(max_examples=150)
@given(paths=path_set(), config=pytest_config_strategy())
def test_classification_result_length_matches_unique_input(
    paths: tuple[str, ...], config: PytestConfig
) -> None:
    """The total number of classified + unclassifiable paths equals the
    number of unique input paths (Requirement 4b.1: every changed file is
    classified into exactly one bucket)."""
    diff = Diff(changed_paths=paths)
    result = classify(diff, config)
    total = len(result.files) + len(result.unclassifiable)
    assert total == len(set(paths))


# --- Property 7: Test and verifier-config classification rules -------------


@settings(max_examples=150)
@given(
    testpath=_path_segment,
    filename=_path_segment,
)
def test_files_under_declared_testpaths_are_always_test(
    testpath: str, filename: str
) -> None:
    """Files under a declared `testpaths` are always TEST, regardless of
    filename pattern (Requirement 4b.2)."""
    path = f"{testpath}/{filename}.py"
    config = PytestConfig(testpaths=(testpath,))
    diff = Diff(changed_paths=(path,))
    result = classify(diff, config)
    assert len(result.files) == 1
    assert result.files[0].classification == FileClass.TEST


@settings(max_examples=150)
@given(name=_path_segment)
def test_default_discovery_patterns_are_test_without_testpaths(name: str) -> None:
    """When `testpaths` is absent, files matching pytest's default
    discovery patterns (test_*.py, *_test.py, conftest.py, files under
    test/tests dirs) are always TEST (Requirement 4b.3)."""
    config = PytestConfig(testpaths=None)

    candidates = [
        f"test_{name}.py",
        f"{name}_test.py",
        "conftest.py",
        f"tests/{name}.py",
        f"test/{name}.py",
        f"pkg/tests/sub/{name}.py",
    ]
    for path in candidates:
        diff = Diff(changed_paths=(path,))
        result = classify(diff, config)
        assert len(result.files) == 1, f"expected {path} to be classified"
        assert result.files[0].classification == FileClass.TEST, (
            f"expected {path} to be TEST, got {result.files[0].classification}"
        )


@settings(max_examples=150)
@given(has_testpaths=st.booleans(), testpath=_path_segment)
def test_recognized_verifier_config_is_always_verifier_config(
    has_testpaths: bool, testpath: str
) -> None:
    """Recognized CI/coverage/pytest/lint/type-check config paths are
    always VERIFIER_CONFIG, regardless of whether `testpaths` is declared
    and regardless of whether the path happens to sit under a declared
    testpath (Requirement 4b.4)."""
    verifier_config_paths = [
        ".github/workflows/ci.yml",
        ".github/workflows/build.yaml",
        ".gitlab-ci.yml",
        ".coveragerc",
        "coverage.cfg",
        "pytest.ini",
        "pytest.cfg",
        "tox.ini",
        ".flake8",
        ".pylintrc",
        "ruff.toml",
        ".ruff.toml",
        "mypy.ini",
        ".mypy.ini",
        "pyrightconfig.json",
    ]
    testpaths = (testpath,) if has_testpaths else None
    config = PytestConfig(testpaths=testpaths)

    for path in verifier_config_paths:
        # Also check the same filename placed under the declared testpath,
        # to prove verifier-config classification wins over the testpaths
        # rule (Req 4b.4 has no testpaths qualifier, unlike 4b.2/4b.3).
        candidate_paths = [path]
        if has_testpaths:
            candidate_paths.append(f"{testpath}/{path}")

        for candidate in candidate_paths:
            diff = Diff(changed_paths=(candidate,))
            result = classify(diff, config)
            assert len(result.files) == 1, f"expected {candidate} to be classified"
            assert result.files[0].classification == FileClass.VERIFIER_CONFIG, (
                f"expected {candidate} to be VERIFIER_CONFIG, "
                f"got {result.files[0].classification}"
            )


@settings(max_examples=150)
@given(section=st.sampled_from(["coverage", "pytest", "ruff", "flake8", "pylint", "mypy"]))
def test_pyproject_toml_verifier_sections_are_verifier_config(section: str) -> None:
    """`pyproject.toml` is VERIFIER_CONFIG when it declares a recognised
    `[tool.*]` config section (Requirement 4b.4)."""
    config = PytestConfig(pyproject_config_sections=frozenset({section}))
    diff = Diff(changed_paths=("pyproject.toml",))
    result = classify(diff, config)
    assert len(result.files) == 1
    assert result.files[0].classification == FileClass.VERIFIER_CONFIG


def test_pyproject_toml_without_recognized_sections_is_production() -> None:
    """`pyproject.toml` with no recognised `[tool.*]` verifier section is
    ordinary production/project metadata, not verifier configuration."""
    config = PytestConfig(pyproject_config_sections=frozenset())
    diff = Diff(changed_paths=("pyproject.toml",))
    result = classify(diff, config)
    assert len(result.files) == 1
    assert result.files[0].classification == FileClass.PRODUCTION

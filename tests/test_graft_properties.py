"""Property-based test for P2/P3 graft composition (Task 13.3).

- **Property 9: Graft preserves source, grafts the right tests, and never
  copies verifier config.**

Uses real temporary directories (the graft is a filesystem operation, so
exercising it against a real tree is both faithful and fast). Hypothesis
generates disjoint sets of production, test, and verifier-configuration files
with deliberately DIFFERENT contents in the source and destination worktrees,
so any wrongful copy or mutation is detectable. Minimum 100 examples.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from verifierlock.probe import graft_tests

_name = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_", min_size=1, max_size=8
)


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _read_tree(root: Path) -> dict[str, str]:
    """Map every file under `root` to its contents, keyed by relative path."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = path.read_text()
    return out


# Feature: verifierlock, Property 9: For any sets of production, test, and
# verifier-configuration files, composing P2/P3 leaves every production-source
# file byte-identical to its origin revision, copies no verifier-configuration
# file between revisions, and after delete-before-copy the destination test
# paths are exactly the grafted revision's test set.
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    prod_files=st.lists(_name, min_size=0, max_size=4, unique=True),
    src_tests=st.lists(_name, min_size=0, max_size=4, unique=True),
    dest_only_tests=st.lists(_name, min_size=0, max_size=4, unique=True),
    verifier_files=st.lists(_name, min_size=0, max_size=3, unique=True),
)
def test_graft_preserves_source_grafts_tests_never_copies_verifier(
    tmp_path_factory,
    prod_files: list[str],
    src_tests: list[str],
    dest_only_tests: list[str],
    verifier_files: list[str],
) -> None:
    source = tmp_path_factory.mktemp("source")
    dest = tmp_path_factory.mktemp("dest")

    # Production source: present in both worktrees with DIFFERENT content.
    for name in prod_files:
        _write(source, f"src/{name}.py", "SRC production")
        _write(dest, f"src/{name}.py", "DEST production")

    # Tests live under tests/. The source has `src_tests`; the destination has
    # a mix of the same names plus `dest_only_tests` that must be removed by
    # delete-before-copy.
    for name in src_tests:
        _write(source, f"tests/test_{name}.py", "SRC test")
    for name in set(src_tests) | set(dest_only_tests):
        _write(dest, f"tests/test_{name}.py", "DEST test")

    # Verifier configuration: present in both with different content, must
    # NEVER be copied from source to dest (Req 6.7).
    for name in verifier_files:
        _write(source, f"{name}.cfg", "SRC verifier")
        _write(dest, f"{name}.cfg", "DEST verifier")

    dest_prod_before = {
        k: v for k, v in _read_tree(dest).items() if k.startswith("src/")
    }
    dest_verifier_before = {
        k: v for k, v in _read_tree(dest).items() if k.endswith(".cfg")
    }

    # Graft the source revision's test paths into the destination worktree.
    graft_tests(source, dest, ["tests"])

    after = _read_tree(dest)

    # 1. Production source is byte-identical to the destination's own revision.
    dest_prod_after = {k: v for k, v in after.items() if k.startswith("src/")}
    assert dest_prod_after == dest_prod_before
    assert all(v == "DEST production" for v in dest_prod_after.values())

    # 2. No verifier configuration was copied from source (dest keeps its own).
    dest_verifier_after = {k: v for k, v in after.items() if k.endswith(".cfg")}
    assert dest_verifier_after == dest_verifier_before
    assert all(v == "DEST verifier" for v in dest_verifier_after.values())

    # 3. After delete-before-copy the destination test tree equals the source
    #    test tree exactly: grafted content, and no residual dest-only tests.
    dest_tests_after = {
        k: v for k, v in after.items() if k.startswith("tests/")
    }
    expected_tests = {f"tests/test_{name}.py": "SRC test" for name in src_tests}
    assert dest_tests_after == expected_tests


def test_graft_deletes_destination_test_when_absent_in_source(tmp_path: Path) -> None:
    """A test path deleted in the grafted revision is removed from the
    destination (delete-before-copy), even though there is nothing to copy."""
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    _write(dest, "tests/test_gone.py", "DEST test")  # source has no tests/
    (source / "src").mkdir(parents=True)

    graft_tests(source, dest, ["tests"])

    assert not (dest / "tests").exists()


def test_graft_ignores_absolute_paths(tmp_path: Path) -> None:
    """Absolute entries are ignored so a graft spec can never escape the
    worktree."""
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    _write(source, "tests/test_a.py", "SRC")
    (dest).mkdir(parents=True, exist_ok=True)
    graft_tests(source, dest, ["/etc/passwd", "tests"])
    assert (dest / "tests" / "test_a.py").read_text() == "SRC"

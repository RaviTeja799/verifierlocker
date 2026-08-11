"""Unit tests for the Revision_Resolver (Task 9.2).

Exercises `resolve` against real temporary Git repositories covering branch,
tag, short-hash, and invalid references (Req 1.2, 1.5).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from verifierlock.revision import resolve

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(cwd: Path, *args: str) -> str:
    import os

    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **_GIT_ENV},
    )
    return result.stdout.strip()


def _init_repo_with_commit(path: Path) -> str:
    """Init a repo at `path`, make one commit, and return its full hash."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    (path / "file.txt").write_text("hello\n")
    _git(path, "add", "file.txt")
    _git(path, "commit", "-m", "initial")
    return _git(path, "rev-parse", "HEAD")


def test_resolve_branch_reference(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    full = _init_repo_with_commit(repo)
    _git(repo, "branch", "feature")

    result = resolve(repo, base_ref="feature", head_ref="feature")

    assert result.base_hash == full
    assert result.head_hash == full
    assert result.base_error is None
    assert result.head_error is None
    assert result.base_resolved and result.head_resolved


def test_resolve_tag_reference(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    full = _init_repo_with_commit(repo)
    # An annotated tag: the ^{commit} peel must dereference it to the commit.
    _git(repo, "tag", "-a", "v1", "-m", "release 1")

    result = resolve(repo, base_ref="v1", head_ref="v1")

    assert result.base_hash == full
    assert result.head_hash == full
    assert result.base_error is None


def test_resolve_short_hash(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    full = _init_repo_with_commit(repo)
    short = full[:8]

    result = resolve(repo, base_ref=short, head_ref="HEAD")

    assert result.base_hash == full
    assert result.head_hash == full


def test_resolve_invalid_base_reference(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    full = _init_repo_with_commit(repo)

    result = resolve(repo, base_ref="no-such-ref", head_ref="HEAD")

    assert result.base_hash is None
    assert result.base_error is not None
    assert not result.base_resolved
    # Head still resolves independently.
    assert result.head_hash == full
    assert result.head_resolved


def test_resolve_invalid_head_reference(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    full = _init_repo_with_commit(repo)

    result = resolve(repo, base_ref="HEAD", head_ref="definitely-not-a-ref")

    assert result.head_hash is None
    assert result.head_error is not None
    assert not result.head_resolved
    assert result.base_hash == full


def test_resolve_nonexistent_repo_path_is_error_not_crash(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    result = resolve(missing, base_ref="HEAD", head_ref="HEAD")
    assert result.base_hash is None
    assert result.head_hash is None
    assert result.base_error is not None
    assert result.head_error is not None

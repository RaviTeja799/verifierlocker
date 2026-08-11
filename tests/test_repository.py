"""Unit tests for the Repository_Validator (Task 9.4).

Exercises `validate` against a valid repository, a non-repository directory, a
nonexistent path, and a repository containing a submodule (Req 2.1, 2.4).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from verifierlock.repository import (
    HAS_SUBMODULES,
    NOT_A_GIT_REPO,
    SUPPORTED,
    validate,
)

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(cwd: Path, *args: str, allow_file: bool = False) -> str:
    prefix = ["git"]
    if allow_file:
        # Local-path submodules require the file transport to be allowed on
        # recent Git versions.
        prefix = ["git", "-c", "protocol.file.allow=always"]
    result = subprocess.run(
        [*prefix, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **_GIT_ENV},
    )
    return result.stdout.strip()


def _init_repo_with_commit(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    (path / "file.txt").write_text("hello\n")
    _git(path, "add", "file.txt")
    _git(path, "commit", "-m", "initial")


def test_valid_repo_is_supported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo_with_commit(repo)

    result = validate(repo)

    assert result.is_git_repo is True
    assert result.has_submodules is False
    assert result.determination == SUPPORTED


def test_non_repo_directory_is_not_a_git_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "note.txt").write_text("not a repo\n")

    result = validate(plain)

    assert result.is_git_repo is False
    assert result.determination == NOT_A_GIT_REPO


def test_nonexistent_path_is_not_a_git_repo(tmp_path: Path) -> None:
    result = validate(tmp_path / "missing")
    assert result.is_git_repo is False
    assert result.determination == NOT_A_GIT_REPO


def test_repo_with_submodule_is_rejected(tmp_path: Path) -> None:
    # A standalone repo to be vendored as a submodule.
    sub = tmp_path / "sub"
    _init_repo_with_commit(sub)

    # The outer repo that adds `sub` as a submodule.
    outer = tmp_path / "outer"
    _init_repo_with_commit(outer)
    _git(outer, "submodule", "add", str(sub), "vendored", allow_file=True)
    _git(outer, "commit", "-m", "add submodule")

    result = validate(outer)

    assert result.is_git_repo is True
    assert result.has_submodules is True
    assert result.determination == HAS_SUBMODULES

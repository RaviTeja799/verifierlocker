"""Repository_Validator: confirm a supported Git repository (Task 9.3).

Implements `validate` from design.md's Repository_Validator section: before any
probe runs, confirm the target path is a Git working tree (Req 2.1) and reject
repositories that contain submodules (Req 2.3), recording the determination
(Req 2.4).

Both failure modes map to INCONCLUSIVE in the Verdict_Engine (rows 0c/0d), with
distinct reason codes (`NOT_A_GIT_REPO`, `HAS_SUBMODULES`); this module only
records the facts and a `determination` string, leaving the reason-code mapping
to `decide`.

Submodules are unsupported by design: `git worktree` support for submodules is
incomplete, and VerifierLock's isolation model relies on clean detached
worktrees (decision log 4.2). So a repository with submodules is rejected up
front rather than mishandled mid-run.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# determination values (design.md Repository_Validator).
SUPPORTED = "supported"
NOT_A_GIT_REPO = "not_a_git_repo"
HAS_SUBMODULES = "has_submodules"


@dataclass(frozen=True)
class RepoValidation:
    """The repository validation determination (Req 2.4).

    `determination` is one of `supported`, `not_a_git_repo`, or `has_submodules`
    and is the single authoritative summary; `is_git_repo` / `has_submodules`
    expose the underlying facts the Verdict_Engine's rows 0c/0d consume.
    """

    is_git_repo: bool
    has_submodules: bool
    determination: str


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run a git command in `repo`, or return None if git cannot be launched
    (missing executable, or the path does not exist / is not a directory)."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None


def _is_git_work_tree(repo: Path) -> bool:
    """True iff `repo` is inside a Git working tree (Req 2.1)."""
    proc = _run_git(repo, "rev-parse", "--is-inside-work-tree")
    return proc is not None and proc.returncode == 0 and proc.stdout.strip() == "true"


def _has_submodules(repo: Path) -> bool:
    """True iff the repository declares any submodule (Req 2.3).

    Uses `git submodule status`, which lists every submodule configured in
    `.gitmodules` (initialised or not); non-empty output means submodules are
    present. Falls back to detecting a `.gitmodules` file when the command
    cannot run, so a submodule repository is conservatively rejected.
    """
    proc = _run_git(repo, "submodule", "status")
    if proc is not None and proc.returncode == 0:
        return bool(proc.stdout.strip())
    return (Path(repo) / ".gitmodules").is_file()


def validate(repo: Path) -> RepoValidation:
    """Validate that `repo` is a supported repository (Req 2.1-2.4).

    Checks are ordered: a path that is not a Git working tree is
    `not_a_git_repo`; a valid repository that declares submodules is
    `has_submodules`; otherwise it is `supported`.
    """
    repo = Path(repo)

    if not _is_git_work_tree(repo):
        return RepoValidation(
            is_git_repo=False,
            has_submodules=False,
            determination=NOT_A_GIT_REPO,
        )

    if _has_submodules(repo):
        return RepoValidation(
            is_git_repo=True,
            has_submodules=True,
            determination=HAS_SUBMODULES,
        )

    return RepoValidation(
        is_git_repo=True,
        has_submodules=False,
        determination=SUPPORTED,
    )

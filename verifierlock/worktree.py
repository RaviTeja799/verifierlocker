"""Minimal Worktree_Manager path scheme and worktree creation (Task 2.1).

Implements only the per-run path scheme and the ability to create a real
detached Git worktree at a given commit. This is deliberately narrow: the
full lifecycle (`remove_all`, `prune_stale`, crash-safe cleanup on any
termination) is implemented in Task 8.1. Task 2.1 only needs enough to prove
that two real worktrees (base, head) can be created under the documented
path scheme via `git worktree add --detach`.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeHandle:
    """A single created worktree (design.md Worktree_Manager section)."""

    probe_slot: str  # e.g. "base", "head", "p0-rep0"
    commit_hash: str
    path: Path


def worktree_root(run_id: str) -> Path:
    """Return the per-run worktrees root:

    `<system-temp>/verifierlock/<run-id>/worktrees/`
    """
    return Path(tempfile.gettempdir()) / "verifierlock" / run_id / "worktrees"


def worktree_path(run_id: str, slot: str) -> Path:
    """Return the per-slot worktree path:

    `<system-temp>/verifierlock/<run-id>/worktrees/<slot>/`
    """
    return worktree_root(run_id) / slot


class Worktree_Manager:
    """Creates detached Git worktrees under the per-run path scheme.

    Only `create` is implemented here (Task 2.1). Because every probe slot
    gets its own unique path under `worktree_path`, `git worktree add` never
    needs to check out a commit that is already checked out elsewhere, so
    `--force` is never required (design.md Concern 1).
    """

    def __init__(self, repo: Path, run_id: str) -> None:
        self.repo = Path(repo)
        self.run_id = run_id

    def create(self, slot: str, commit: str) -> WorktreeHandle:
        """Create a detached worktree at `commit` under this run's unique
        per-slot path (Req 3.1, 3.4). Never passes `--force`.
        """
        path = worktree_path(self.run_id, slot)
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(path), commit],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return WorktreeHandle(probe_slot=slot, commit_hash=commit, path=path)

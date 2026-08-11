"""Worktree_Manager: crash-safe lifecycle for isolated Git worktrees (Task 8.1).

Implements the full lifecycle from design.md's Worktree_Manager section and
Concern 1:

- **Per-run, per-slot unique paths** under
  `<system-temp>/verifierlock/<run-id>/worktrees/<slot>/`. Because every probe
  slot (each P0 repetition, P1, P2, P3) has its own path, `git worktree add`
  never needs `--force`, even though several slots target the same commit
  (Req 3.1, 8c.3, Concern 1).
- **`prune_stale`** at run start, so a worktree leaked by a crashed prior run
  cannot hold a ref (Req 3.6).
- **`create`** a detached worktree at a commit (Req 3.1). A creation failure is
  surfaced as `WorktreeCreationError` and the handle is NOT recorded, so cleanup
  never touches a worktree that was never made. The Probe_Runner (Task 10) maps
  that failure to INCONCLUSIVE for every dependent probe (Req 3.3) using the
  `worktree_failure_result` helper here.
- **`remove_all`** removes every created worktree, deletes the per-run worktrees
  subtree, and prunes metadata (Req 3.2). It is idempotent and best-effort so a
  partially-failed cleanup still makes maximal progress.
- **Crash safety.** The manager is a context manager whose `__exit__` always
  runs `remove_all`, and it installs SIGINT/SIGTERM handlers so an abort or kill
  still cleans up (Req 3.5). `prune_stale` runs on `__enter__` as the recovery
  path for a previously crashed run (Req 3.6).

The git operations are isolated behind a small injectable `WorktreeBackend`
Protocol so the lifecycle can be property-tested with an in-memory fake
(design Testing Strategy, Properties 12-14), while production uses
`GitWorktreeBackend`.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import reasons
from .types import ProbeOutcome, ProbeResult


@dataclass(frozen=True)
class WorktreeHandle:
    """A single created worktree (design.md Worktree_Manager section)."""

    probe_slot: str  # e.g. "base", "head", "p0-rep0", "p1", "p2", "p3"
    commit_hash: str
    path: Path


class WorktreeCreationError(RuntimeError):
    """Raised when a worktree cannot be created for a revision (Req 3.3).

    Carries the slot, the commit, and the underlying detail so the Probe_Runner
    can report INCONCLUSIVE (`WORKTREE_CREATE_FAILED`) for each dependent probe.
    """

    def __init__(self, slot: str, commit: str, detail: str) -> None:
        self.slot = slot
        self.commit = commit
        self.detail = detail
        super().__init__(f"worktree creation failed for slot {slot!r} at {commit}: {detail}")


# --- Path scheme -----------------------------------------------------------


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


# --- Slot naming (per-slot unique paths, Req 3.1, 8c.3) --------------------

P1_SLOT = "p1"
P2_SLOT = "p2"
P3_SLOT = "p3"


def p0_slot(repetition: int) -> str:
    """Slot name for a P0 baseline repetition, e.g. `p0-rep0`, `p0-rep1`."""
    return f"p0-rep{repetition}"


def run_slots(p0_repetitions: int) -> tuple[str, ...]:
    """All slot names used in a full run: the P0 repetitions then P1, P2, P3.

    Every name is distinct, so the derived worktree paths are pairwise unique
    even though `p0-rep*` and `p2` all target the base commit and `p1`/`p3`
    both target the head commit (Concern 1, Property 12).
    """
    return tuple(p0_slot(i) for i in range(p0_repetitions)) + (P1_SLOT, P2_SLOT, P3_SLOT)


# --- Backend abstraction ---------------------------------------------------


class WorktreeBackend(Protocol):
    """The narrow git surface the manager needs; injectable for testing."""

    def add(self, path: Path, commit: str) -> None: ...
    def remove(self, path: Path) -> None: ...
    def prune(self) -> None: ...


class GitWorktreeBackend:
    """Production backend driving real `git worktree` subcommands."""

    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)

    def _git(self, *args: str) -> None:
        subprocess.run(
            ["git", "worktree", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

    def add(self, path: Path, commit: str) -> None:
        # Detached, and never --force: uniqueness is guaranteed by the path
        # scheme (Concern 1).
        self._git("add", "--detach", str(path), commit)

    def remove(self, path: Path) -> None:
        # --force is required (and safe) here: a grafted worktree is "dirty",
        # and `git worktree remove` refuses a dirty worktree without it. This
        # is unrelated to the `add` --force ban in Concern 1.
        self._git("remove", "--force", str(path))

    def prune(self) -> None:
        self._git("prune")


# --- Manager ---------------------------------------------------------------


class Worktree_Manager:
    """Creates and destroys detached Git worktrees for one run, crash-safely.

    Intended to be used as a context manager so cleanup is guaranteed:

        with Worktree_Manager(repo, run_id) as manager:
            base = manager.create(p0_slot(0), base_commit)
            ...
        # all created worktrees removed here, on normal exit OR exception
    """

    def __init__(
        self,
        repo: Path,
        run_id: str,
        backend: WorktreeBackend | None = None,
    ) -> None:
        self.repo = Path(repo)
        self.run_id = run_id
        self._backend: WorktreeBackend = backend or GitWorktreeBackend(self.repo)
        self._handles: list[WorktreeHandle] = []
        self._used_slots: set[str] = set()
        self._cleaned = False
        self._prev_signal_handlers: dict[int, object] = {}

    @property
    def handles(self) -> tuple[WorktreeHandle, ...]:
        """The worktrees successfully created so far, in creation order."""
        return tuple(self._handles)

    # --- Lifecycle -------------------------------------------------------

    def prune_stale(self) -> None:
        """Prune stale worktree metadata at run start (Req 3.6)."""
        self._backend.prune()

    def create(self, slot: str, commit: str) -> WorktreeHandle:
        """Create a detached worktree at `commit` under this run's unique
        per-slot path (Req 3.1, 3.4, 8c.3). Never passes `--force`.

        Raises `WorktreeCreationError` if the backend cannot create the
        worktree (Req 3.3); on failure no handle is recorded, so cleanup will
        not try to remove a worktree that never existed. Reusing a slot within
        one run is a programming error (it would break path uniqueness) and
        raises `ValueError`.
        """
        if slot in self._used_slots:
            raise ValueError(f"worktree slot {slot!r} already used in this run")

        path = worktree_path(self.run_id, slot)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._backend.add(path, commit)
        except Exception as exc:  # noqa: BLE001 - wrap any backend failure
            detail = _describe_creation_failure(exc)
            raise WorktreeCreationError(slot, commit, detail) from exc

        self._used_slots.add(slot)
        handle = WorktreeHandle(probe_slot=slot, commit_hash=commit, path=path)
        self._handles.append(handle)
        return handle

    def remove_all(self) -> None:
        """Remove every created worktree and prune metadata (Req 3.2, 3.5).

        Idempotent and best-effort: each removal is attempted independently so
        one failure does not strand the rest. After removing the individual
        worktrees it deletes the per-run worktrees subtree (a single subtree
        removal, Concern 1) and prunes metadata. Anything the run wrote outside
        `worktrees/` (e.g. the Evidence Record) is left untouched.
        """
        if self._cleaned:
            return
        self._cleaned = True

        for handle in reversed(self._handles):
            try:
                self._backend.remove(handle.path)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass

        shutil.rmtree(worktree_root(self.run_id), ignore_errors=True)

        try:
            self._backend.prune()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass

    # --- Context manager + signal handling -------------------------------

    def __enter__(self) -> "Worktree_Manager":
        self.prune_stale()  # recovery path for a previously crashed run (Req 3.6)
        self._install_signal_handlers()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._restore_signal_handlers()
        self.remove_all()
        return False  # never suppress exceptions

    def _install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers that clean up on abort (Req 3.5).

        Best-effort: `signal.signal` only works in the main thread, so this is
        skipped silently elsewhere (the `__exit__`/`finally` path still
        guarantees cleanup for normal exits and exceptions).
        """
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._prev_signal_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                self._prev_signal_handlers.pop(sig, None)

    def _on_signal(self, signum: int, frame) -> None:
        self.remove_all()
        prev = self._prev_signal_handlers.get(signum, signal.SIG_DFL)
        try:
            signal.signal(signum, prev)  # type: ignore[arg-type]
        except (ValueError, OSError):
            pass
        if callable(prev):
            prev(signum, frame)
        elif prev == signal.SIG_DFL:
            # Restore default disposition and re-raise so the process still
            # terminates as it normally would for this signal.
            os.kill(os.getpid(), signum)

    def _restore_signal_handlers(self) -> None:
        for sig, prev in self._prev_signal_handlers.items():
            try:
                signal.signal(sig, prev)  # type: ignore[arg-type]
            except (ValueError, OSError):
                pass
        self._prev_signal_handlers.clear()


def _describe_creation_failure(exc: Exception) -> str:
    """Extract a concise detail string from a backend creation failure."""
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
        return stderr or f"git exited with status {exc.returncode}"
    return str(exc) or exc.__class__.__name__


def worktree_failure_result(
    probe_id: str,
    commit: str,
    error: WorktreeCreationError | str,
    repetition: int = 0,
) -> ProbeResult:
    """Build the INCONCLUSIVE ProbeResult for a probe whose worktree could not
    be created (Req 3.3, reason code `WORKTREE_CREATE_FAILED`).

    Consumed by the Probe_Runner (Task 10): when `create` raises
    `WorktreeCreationError` for a revision, every probe depending on that
    worktree is reported INCONCLUSIVE via this helper. No command ran and no
    worktree exists, so the command/counts are empty and there is no exit code.
    """
    detail = error.detail if isinstance(error, WorktreeCreationError) else str(error)
    return ProbeResult(
        probe_id=probe_id,
        repetition=repetition,
        command=(),
        exit_code=None,
        outcome=ProbeOutcome.INCONCLUSIVE,
        collected=0,
        passed=0,
        failed=0,
        skipped=0,
        elapsed_seconds=0.0,
        reason=f"{reasons.WORKTREE_CREATE_FAILED}:{detail}",
        worktree_path="",
    )

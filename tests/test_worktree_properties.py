"""Property-based tests for the Worktree_Manager lifecycle (Tasks 8.2-8.4).

- **Property 12:** Worktree paths are pairwise unique per run.
- **Property 13:** All created worktrees are removed on any termination.
- **Property 14:** Worktree-creation failure makes dependent probes INCONCLUSIVE.

The git side effects are isolated behind an in-memory `FakeBackend` so these
properties stay fast and deterministic (design Testing Strategy). Each property
test uses Hypothesis with a minimum of 100 examples.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifierlock import reasons
from verifierlock.types import ProbeOutcome
from verifierlock.worktree import (
    Worktree_Manager,
    WorktreeCreationError,
    p0_slot,
    run_slots,
    worktree_failure_result,
    worktree_path,
    worktree_root,
)


class FakeBackend:
    """In-memory `WorktreeBackend` that records calls instead of touching git.

    `fail_on_commit` makes `add` raise for a chosen commit, exercising the
    creation-failure path without a real repository.
    """

    def __init__(self, fail_on_commit: str | None = None) -> None:
        self.added: list[tuple[Path, str]] = []
        self.removed: list[Path] = []
        self.prune_calls = 0
        self.fail_on_commit = fail_on_commit

    def add(self, path: Path, commit: str) -> None:
        if self.fail_on_commit is not None and commit == self.fail_on_commit:
            raise RuntimeError(f"cannot add worktree for {commit}")
        self.added.append((Path(path), commit))

    def remove(self, path: Path) -> None:
        self.removed.append(Path(path))

    def prune(self) -> None:
        self.prune_calls += 1


def _fresh_run_id() -> str:
    return f"test-{uuid.uuid4()}"


# --- Property 12: Worktree paths are pairwise unique per run ---------------


# Feature: verifierlock, Property 12: For any set of probe slots in a run,
# including two or more P0 repetitions and any probes targeting the same commit,
# the derived worktree paths are pairwise distinct, so git worktree add never
# requires --force.
@settings(max_examples=200)
@given(p0_reps=st.integers(min_value=2, max_value=8), run_id=st.uuids())
def test_worktree_paths_are_pairwise_unique(p0_reps: int, run_id) -> None:
    slots = run_slots(p0_reps)
    paths = [worktree_path(str(run_id), slot) for slot in slots]

    # Slot names are unique, and paths derive purely from the slot name, so
    # paths are pairwise distinct even though several slots share a commit.
    assert len(set(slots)) == len(slots)
    assert len(set(paths)) == len(paths)
    # Expected slot roster: p0-rep0..p0-rep{n-1} then p1, p2, p3.
    assert slots[:p0_reps] == tuple(p0_slot(i) for i in range(p0_reps))
    assert slots[p0_reps:] == ("p1", "p2", "p3")


def test_same_commit_different_slots_have_distinct_paths() -> None:
    """p0-rep0, p0-rep1 and p2 all target the base commit yet get distinct
    paths (Concern 1: uniqueness comes from the path scheme, not --force)."""
    run_id = _fresh_run_id()
    base_commit = "b" * 40
    manager = Worktree_Manager(
        repo=Path("/nonexistent"), run_id=run_id, backend=FakeBackend()
    )
    h0 = manager.create(p0_slot(0), base_commit)
    h1 = manager.create(p0_slot(1), base_commit)
    h2 = manager.create("p2", base_commit)
    paths = {h0.path, h1.path, h2.path}
    assert len(paths) == 3
    manager.remove_all()


def test_reusing_a_slot_is_rejected() -> None:
    """A slot may not be created twice in one run (would break uniqueness)."""
    manager = Worktree_Manager(
        repo=Path("/nonexistent"), run_id=_fresh_run_id(), backend=FakeBackend()
    )
    manager.create("p1", "h" * 40)
    with pytest.raises(ValueError):
        manager.create("p1", "h" * 40)
    manager.remove_all()


# --- Property 13: All created worktrees removed on any termination ---------


@st.composite
def _slot_commit_plan(draw: st.DrawFn) -> list[tuple[str, str]]:
    """Generate a list of (slot, commit) creations with unique slots and a mix
    of commits (so some slots share a commit)."""
    n = draw(st.integers(min_value=1, max_value=6))
    commits = ["b" * 40, "h" * 40, "c" * 40]
    return [
        (f"slot-{i}", draw(st.sampled_from(commits)))
        for i in range(n)
    ]


# Feature: verifierlock, Property 13: For any sequence of worktree creations and
# any injected failure point (including an exception mid-run), after cleanup runs
# there are zero outstanding worktrees created by the run.
@settings(max_examples=200, deadline=None)
@given(plan=_slot_commit_plan(), raise_midway=st.booleans())
def test_all_worktrees_removed_on_any_termination(
    plan: list[tuple[str, str]], raise_midway: bool
) -> None:
    run_id = _fresh_run_id()
    backend = FakeBackend()

    class _Boom(Exception):
        pass

    created_paths: list[Path] = []
    try:
        with Worktree_Manager(
            repo=Path("/nonexistent"), run_id=run_id, backend=backend
        ) as manager:
            for slot, commit in plan:
                created_paths.append(manager.create(slot, commit).path)
            if raise_midway:
                raise _Boom()
    except _Boom:
        pass

    # Every created worktree was removed, and the per-run worktrees subtree is
    # gone (Req 3.2, 3.5).
    assert set(backend.removed) == set(created_paths)
    assert not worktree_root(run_id).exists()
    # prune ran at start (stale) and at cleanup.
    assert backend.prune_calls >= 2


def test_remove_all_is_idempotent() -> None:
    """Calling remove_all twice (e.g. explicit call then __exit__) removes each
    worktree exactly once and does not raise."""
    run_id = _fresh_run_id()
    backend = FakeBackend()
    manager = Worktree_Manager(
        repo=Path("/nonexistent"), run_id=run_id, backend=backend
    )
    p = manager.create("p1", "h" * 40).path
    manager.remove_all()
    manager.remove_all()
    assert backend.removed == [p]


# --- Property 14: Worktree-creation failure -> dependent probes INCONCLUSIVE ---


# Feature: verifierlock, Property 14: For any revision whose worktree creation
# fails, every probe depending on that worktree is reported INCONCLUSIVE.
@settings(max_examples=200)
@given(
    probe_ids=st.lists(
        st.sampled_from(["P0", "P1", "P2", "P3"]), min_size=1, max_size=4
    ),
    commit=st.text(alphabet="0123456789abcdef", min_size=7, max_size=40),
)
def test_creation_failure_yields_inconclusive_for_dependent_probes(
    probe_ids: list[str], commit: str
) -> None:
    run_id = _fresh_run_id()
    backend = FakeBackend(fail_on_commit=commit)
    manager = Worktree_Manager(
        repo=Path("/nonexistent"), run_id=run_id, backend=backend
    )

    # Creating a worktree at the failing commit raises WorktreeCreationError,
    # and no handle is recorded (so cleanup won't touch a phantom worktree).
    try:
        with pytest.raises(WorktreeCreationError) as excinfo:
            manager.create("p2", commit)
        error = excinfo.value
        assert manager.handles == ()

        # Every dependent probe maps to INCONCLUSIVE with WORKTREE_CREATE_FAILED.
        for probe_id in probe_ids:
            result = worktree_failure_result(probe_id, commit, error)
            assert result.outcome is ProbeOutcome.INCONCLUSIVE
            assert result.probe_id == probe_id
            assert result.exit_code is None
            assert result.command == ()
            assert result.reason is not None
            assert result.reason.startswith(reasons.WORKTREE_CREATE_FAILED)
    finally:
        manager.remove_all()


def test_creation_failure_does_not_strand_earlier_worktrees() -> None:
    """A later creation failure still leaves earlier worktrees cleanable."""
    run_id = _fresh_run_id()
    bad_commit = "d" * 40
    backend = FakeBackend(fail_on_commit=bad_commit)

    with Worktree_Manager(
        repo=Path("/nonexistent"), run_id=run_id, backend=backend
    ) as manager:
        good = manager.create(p0_slot(0), "b" * 40).path
        with pytest.raises(WorktreeCreationError):
            manager.create("p1", bad_commit)

    # The successfully created worktree was cleaned up; the failed one never
    # became a handle.
    assert backend.removed == [good]
    assert not worktree_root(run_id).exists()

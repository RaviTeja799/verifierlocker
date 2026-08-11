"""Walking skeleton (Task 2): real worktrees, one probe, one evidence
artifact.

This wires together Task 2.1 (Worktree_Manager path scheme + creation),
Task 2.2 (single real pytest probe), and Task 2.3 (Evidence Record JSON
emission) into one runnable, demoable path. It proves the real worktree
lifecycle and evidence emission end-to-end against disk, using real `git`
and a real `pytest` subprocess, with no engine, matrix, or verdict logic.

Not part of this skeleton (arrive in later tasks): the four-probe matrix,
coverage, the verdict engine, environment building, cleanup/pruning, and the
full Evidence Record schema.
"""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from pathlib import Path

from .evidence import build_evidence_record, write_evidence_record
from .probe import run_probe
from .worktree import Worktree_Manager


def _rev_parse(repo: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def run_walking_skeleton(
    repo: Path,
    base_ref: str = "HEAD",
    head_ref: str = "HEAD",
    run_id: str | None = None,
) -> Path:
    """Run the walking skeleton against `repo` and return the path to the
    written Evidence Record JSON.

    Resolves `base_ref`/`head_ref` to commit hashes, creates two detached
    worktrees (base, head) under the per-run path scheme, runs ONE real
    pytest probe in the head worktree, and writes a minimal Evidence Record
    to `<system-temp>/verifierlock/<run-id>/evidence.json`.
    """
    repo = Path(repo)
    run_id = run_id or str(uuid.uuid4())

    base_commit = _rev_parse(repo, base_ref)
    head_commit = _rev_parse(repo, head_ref)

    manager = Worktree_Manager(repo=repo, run_id=run_id)
    manager.create("base", base_commit)
    head_handle = manager.create("head", head_commit)

    probe_result = run_probe(head_handle.path, probe_id="P1")

    record = build_evidence_record(base_commit, head_commit, probe_result)
    evidence_path = Path(tempfile.gettempdir()) / "verifierlock" / run_id / "evidence.json"
    return write_evidence_record(record, evidence_path)


if __name__ == "__main__":
    import sys

    target_repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    written = run_walking_skeleton(target_repo)
    print(f"Evidence Record written to: {written}")

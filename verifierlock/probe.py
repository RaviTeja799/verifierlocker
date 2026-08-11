"""Minimal single-probe execution (Task 2.2).

Launches ONE real pytest process in a worktree, captures the exact command
and process exit code, and classifies it via `interpret_exit_code` (Task 1).
This is intentionally narrow: no four-probe matrix, no verdict engine, no
coverage, no determinism controls, and no timeout handling. Those arrive in
later tasks (10.1, 10.2, 10.3).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .exit_code import interpret_exit_code
from .types import ProbeResult


def run_probe(worktree_path: Path, probe_id: str = "P1") -> ProbeResult:
    """Run a single real pytest process in `worktree_path` and capture the
    result.

    Uses the current Python interpreter (`sys.executable -m pytest`) so the
    probe is runnable without any Environment_Builder (that arrives in
    Task 13). Captures the exact command and exit code, and classifies the
    outcome via `interpret_exit_code`.
    """
    command = (sys.executable, "-m", "pytest")
    completed = subprocess.run(
        command,
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    outcome, reason = interpret_exit_code(completed.returncode)
    return ProbeResult(
        probe_id=probe_id,
        repetition=0,
        command=command,
        exit_code=completed.returncode,
        outcome=outcome,
        collected=0,
        passed=0,
        failed=0,
        skipped=0,
        elapsed_seconds=0.0,
        reason=reason,
        worktree_path=str(worktree_path),
    )

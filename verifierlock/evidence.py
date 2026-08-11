"""Minimal Evidence Record assembly and JSON emission (Task 2.3).

Assembles a minimal Evidence Record (base/head commits, the single probe's
command, exit code, and outcome) and writes it as JSON to disk. This is
deliberately a small subset of the full Evidence Record schema in
design.md's Data Models section (run metadata, validation, environments,
changed_files, static_findings, coverage, and the full verdict object all
arrive in Task 15.2). The goal here is only to prove the skeleton runs
end-to-end and produces a real on-disk artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

from .types import ProbeResult


def build_evidence_record(
    base_commit: str,
    head_commit: str,
    probe_result: ProbeResult,
) -> dict:
    """Build a minimal Evidence Record dict for the walking skeleton."""
    return {
        "schema_version": "0-skeleton",
        "run": {
            "base_commit": base_commit,
            "head_commit": head_commit,
        },
        "probe": {
            "probe_id": probe_result.probe_id,
            "command": list(probe_result.command),
            "exit_code": probe_result.exit_code,
            "outcome": probe_result.outcome.value,
            "reason": probe_result.reason,
        },
    }


def write_evidence_record(record: dict, path: Path) -> Path:
    """Write the Evidence Record as JSON to `path`, creating parent
    directories as needed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return path

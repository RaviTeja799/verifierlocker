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
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .classifier import ClassificationResult
from .coverage import CoverageResult
from .environment import BuiltEnv
from .repository import RepoValidation
from .static_analyzer import StaticFinding
from .types import ProbeResult
from .verdict import (
    Verdict,
    matched_rule_of,
    reason_code_of,
    verdict_exit_code,
)

SCHEMA_VERSION = "1"


def _canonical(entry: dict) -> str:
    """A fully-ordered canonical string for an entry, used as a sort tie-breaker
    so array ordering is total and independent of input order (Req 11.5)."""
    return json.dumps(entry, sort_keys=True, ensure_ascii=False)


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


# ===========================================================================
# Full Evidence Record (Task 15.2)
# ===========================================================================
#
# The full Evidence Record is the authoritative machine-readable artifact
# (Req 11). It captures every input, probe outcome, finding, coverage line, and
# the verdict. Two guarantees shape its construction:
#
# 1. **Deterministic ordering (Req 11.5).** Every array is emitted in a fixed
#    order (files by path, findings by `(file, hunk, kind, detail)`, probes by
#    `(probe_id, repetition, kind)`, changed lines by `(file, line)`,
#    environments by revision), so two equal run results assembled from inputs
#    supplied in ANY order serialise identically.
#
# 2. **Reproducible core (Req 11.5, 10.16).** The full record embeds run-specific
#    values (`run_id`, timestamp, per-run temp `worktree_path`s, and commands
#    embedding those temp paths), so the WHOLE record is deliberately not
#    byte-stable. The `reproducible_core` is exactly the subset that is a pure
#    function of `(repo state, base commit, head commit)`; before comparison the
#    command paths are normalised to the `<RUN_ROOT>` / `<WORKTREE>` placeholders
#    so the command *shape* is compared, not the ephemeral path. The normalised
#    core is byte-identical for identical inputs (Property 22).


@dataclass(frozen=True)
class RunMetadata:
    """The per-run metadata block of the Evidence Record (Req 11.3).

    `run_id` / `timestamp` / the absolute `worktree_path`s are run-specific and
    are excluded from the reproducible core; the commits are part of it.
    `run_root` is the per-run temp root (`<system-temp>/verifierlock/<run-id>`)
    used to normalise command paths.
    """

    run_id: str
    timestamp: str
    repo_path: str
    base_ref: str
    head_ref: str
    base_commit: str | None
    head_commit: str | None
    timeout_seconds: float | None
    run_root: str = ""


_HUNK_HEADER_RE = re.compile(
    r"@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@"
)


def _parse_hunk_header(header: str) -> dict | None:
    """Parse a unified-diff hunk header `@@ -a,b +c,d @@` into its numbers.

    `old_lines` / `new_lines` default to 1 when omitted (unified-diff
    convention). Returns None when `header` is not a hunk header (e.g. a
    node-ID-derived finding carries no hunk), so callers can skip it.
    """
    match = _HUNK_HEADER_RE.search(header)
    if match is None:
        return None
    return {
        "old_start": int(match.group("old_start")),
        "old_lines": int(match.group("old_lines") or 1),
        "new_start": int(match.group("new_start")),
        "new_lines": int(match.group("new_lines") or 1),
    }


def normalise_command(
    command: Sequence[str], run_root: str, worktree_path: str
) -> list[str]:
    """Normalise run-specific paths in a probe command (Req 11.5).

    Replaces the probe's absolute worktree path with `<WORKTREE>` and the per-run
    temp root with `<RUN_ROOT>`, so the command *shape* is stable across runs
    even though the concrete temp paths differ every run. The worktree path is
    replaced first because it is a longer, more specific path that begins with
    the run root.
    """
    normalised: list[str] = []
    for arg in command:
        text = str(arg)
        if worktree_path:
            text = text.replace(worktree_path, "<WORKTREE>")
        if run_root:
            text = text.replace(run_root, "<RUN_ROOT>")
        normalised.append(text)
    return normalised


def _probe_kind(probe_id: str) -> tuple[str, str]:
    """Map a raw probe id to its `(probe_id, kind)` pair for the record.

    The instrumented coverage run is recorded as probe `P1` with kind
    `coverage`; every other probe is a `verdict` probe (Concern 2). This keeps
    the two P1 entries distinct and orderable by kind.
    """
    if probe_id == "P1-COV":
        return "P1", "coverage"
    return probe_id, "verdict"


def _probe_entry(probe: ProbeResult, run_root: str) -> dict:
    probe_id, kind = _probe_kind(probe.probe_id)
    return {
        "probe_id": probe_id,
        "repetition": probe.repetition,
        "kind": kind,
        "command": list(probe.command),
        "command_normalised": normalise_command(
            probe.command, run_root, probe.worktree_path
        ),
        "worktree_path": probe.worktree_path,
        "exit_code": probe.exit_code,
        "outcome": probe.outcome.value,
        "collected": probe.collected,
        "passed": probe.passed,
        "failed": probe.failed,
        "skipped": probe.skipped,
        "elapsed_seconds": probe.elapsed_seconds,
        "reason": probe.reason,
    }


def _environment_entry(env: BuiltEnv) -> dict:
    return {
        "revision": env.revision,
        "tool": env.tool,
        "discovery": env.discovery,
        "installed_project": env.installed_project,
        "install_kind": env.install_kind,
        "built": env.built,
        "error": env.error,
    }


def _changed_file_entries(
    classification: ClassificationResult,
    file_diffs: Sequence,
) -> list[dict]:
    """Build the `changed_files` array from classification + diff hunks.

    Each classified (non-unclassifiable) file becomes one entry carrying its
    classification and its parsed hunk numbers (when a diff was supplied for
    it). Ordered by path.
    """
    hunks_by_path: dict[str, list[dict]] = {}
    for file_diff in file_diffs:
        parsed = [
            h for hunk in file_diff.hunks if (h := _parse_hunk_header(hunk.header))
        ]
        hunks_by_path[file_diff.path] = parsed

    entries = [
        {
            "path": cf.path,
            "classification": cf.classification.value,
            "hunks": hunks_by_path.get(cf.path, []),
        }
        for cf in classification.files
    ]
    entries.sort(key=lambda e: (e["path"], _canonical(e)))
    return entries


def _static_finding_entries(findings: Sequence[StaticFinding]) -> list[dict]:
    entries = [
        {"kind": f.kind, "file": f.file, "hunk": f.hunk, "detail": f.detail}
        for f in findings
    ]
    entries.sort(key=lambda e: (e["file"], e["hunk"], e["kind"], e["detail"], _canonical(e)))
    return entries


def _coverage_block(coverage: CoverageResult | None) -> dict:
    if coverage is None:
        return {
            "available": False,
            "reason": None,
            "changed_lines": [],
            "uncovered_count": 0,
        }
    changed_lines = [
        {"file": cl.file, "line": cl.line, "covered": cl.covered}
        for cl in coverage.lines
    ]
    changed_lines.sort(key=lambda e: (e["file"], e["line"], _canonical(e)))
    uncovered = sum(1 for e in changed_lines if not e["covered"])
    return {
        "available": coverage.available,
        "reason": coverage.reason,
        "changed_lines": changed_lines,
        "uncovered_count": uncovered,
    }


def _verdict_block(verdict: Verdict, reason: str) -> dict:
    return {
        "value": verdict.value,
        "reason_code": reason_code_of(reason),
        "reason": reason,
        "matched_rule": matched_rule_of(reason),
        "exit_code": verdict_exit_code(verdict),
    }


def build_full_evidence_record(
    *,
    run: RunMetadata,
    validation: RepoValidation | None,
    environments: Sequence[BuiltEnv],
    classification: ClassificationResult | None,
    file_diffs: Sequence,
    static_findings: Sequence[StaticFinding],
    probes: Sequence[ProbeResult],
    coverage: CoverageResult | None,
    verdict: Verdict,
    reason: str,
    tool_version: str = "0.1.0",
) -> dict:
    """Assemble the complete Evidence Record (Req 11.1-11.4, Task 15.2).

    All arrays are ordered deterministically so equal inputs supplied in any
    order produce an identical record. Every probe records its command, exit
    code, and counts; every INCONCLUSIVE/skip/BASELINE_INVALID reason is carried
    through verbatim in `probes[].reason` / `verdict.reason` (Req 11.4). Command
    normalisation for the reproducible core is applied by `reproducible_core`.
    """
    environments_sorted = sorted(
        (_environment_entry(env) for env in environments),
        key=lambda e: (e["revision"], _canonical(e)),
    )
    probe_entries = sorted(
        (_probe_entry(p, run.run_root) for p in probes),
        key=lambda e: (e["probe_id"], e["repetition"], e["kind"], _canonical(e)),
    )
    unclassifiable = (
        sorted(classification.unclassifiable) if classification is not None else []
    )
    changed_files = (
        _changed_file_entries(classification, file_diffs)
        if classification is not None
        else []
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": tool_version,
        "run": {
            "run_id": run.run_id,
            "timestamp": run.timestamp,
            "repo_path": run.repo_path,
            "base_ref": run.base_ref,
            "head_ref": run.head_ref,
            "base_commit": run.base_commit,
            "head_commit": run.head_commit,
            "timeout_seconds": run.timeout_seconds,
        },
        "validation": (
            {
                "is_git_repo": validation.is_git_repo,
                "has_submodules": validation.has_submodules,
                "determination": validation.determination,
            }
            if validation is not None
            else {"is_git_repo": False, "has_submodules": False, "determination": None}
        ),
        "environments": environments_sorted,
        "changed_files": changed_files,
        "unclassifiable_files": unclassifiable,
        "static_findings": _static_finding_entries(static_findings),
        "probes": probe_entries,
        "coverage": _coverage_block(coverage),
        "verdict": _verdict_block(verdict, reason),
    }


# --- Reproducible core (Req 11.5, 10.16, Property 22) ----------------------


def reproducible_core(record: dict) -> dict:
    """Extract the normalised reproducible core from a full Evidence Record.

    The core is exactly the fields that are a pure function of
    `(repo state, base commit, head commit)`: the verdict value / reason code /
    matched rule, each probe's identity + normalised command + exit code +
    outcome + counts + reason, the changed-file classifications, static
    findings, coverage changed-lines, and the deps-only environment facts. The
    probe commands use the pre-normalised `command_normalised` field so the
    ephemeral `<RUN_ROOT>` / `<WORKTREE>` paths do not leak in (Req 11.5).
    """
    verdict = record["verdict"]
    probes = [
        {
            "probe_id": p["probe_id"],
            "repetition": p["repetition"],
            "kind": p["kind"],
            "command": p["command_normalised"],
            "exit_code": p["exit_code"],
            "outcome": p["outcome"],
            "collected": p["collected"],
            "passed": p["passed"],
            "failed": p["failed"],
            "skipped": p["skipped"],
            "reason": p["reason"],
        }
        for p in record["probes"]
    ]
    environments = [
        {
            "revision": e["revision"],
            "tool": e["tool"],
            "discovery": e["discovery"],
            "installed_project": e["installed_project"],
            "install_kind": e["install_kind"],
        }
        for e in record["environments"]
    ]
    changed_files = [
        {"path": cf["path"], "classification": cf["classification"]}
        for cf in record["changed_files"]
    ]
    return {
        "verdict": {
            "value": verdict["value"],
            "reason_code": verdict["reason_code"],
            "matched_rule": verdict["matched_rule"],
        },
        "probes": probes,
        "environments": environments,
        "changed_files": changed_files,
        "unclassifiable_files": list(record["unclassifiable_files"]),
        "static_findings": record["static_findings"],
        "coverage": {"changed_lines": record["coverage"]["changed_lines"]},
    }


def serialize_reproducible_core(record: dict) -> str:
    """Serialise the normalised reproducible core to canonical JSON.

    Byte-identical for identical inputs (Req 11.5, 10.16): keys are sorted and
    the arrays in the core are already canonically ordered by
    `build_full_evidence_record`.
    """
    return json.dumps(reproducible_core(record), sort_keys=True, ensure_ascii=False)

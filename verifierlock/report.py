"""Report_Generator: render a finished Evidence Record as human-readable text
(Task 18.1).

The Evidence Record (`evidence.build_full_evidence_record`) is the authoritative
machine-readable artifact; this module is its *presentation* layer. It renders
the four elements Requirement 12.1 mandates -- the verdict, the per-probe
outcomes, the static findings, and the changed-line coverage -- plus the run
metadata a reviewer needs to reproduce the run.

Two properties are load-bearing:

1. **Local only (Req 12.2, 14.3).** Rendering is a pure function of the record
   and `write_report` writes to a local path. This module imports nothing that
   can reach a network, so no report can leak repository contents off the
   machine.
2. **Read-only over the record.** The renderer never mutates the record it is
   given, so producing a report cannot perturb the verdict or the reproducible
   core (the same guarantee the optional Explanation_Model carries, Req 13.2).

Rendering is defensive about optional keys: the record grows over time (the CLI
appends an `exit_status` policy block and an optional `explanation` block), and
a short-circuited run legitimately carries no probes, no findings, and no
coverage. Missing sections render as an explicit "(none)" line rather than
disappearing, so a reviewer can tell "nothing found" from "not measured".
"""

from __future__ import annotations

from pathlib import Path

# One-line plain-English meaning per verdict, so a reviewer does not have to
# hold the rule table in their head. Keyed by the `verdict.value` string (which
# includes the CLI's non-verdict `ABORTED_NO_VERDICT` sentinel).
_VERDICT_MEANING: dict[str, str] = {
    "INDEPENDENT_EVIDENCE": (
        "the head test suite still discriminates base behaviour from head "
        "behaviour, so the change carries independent test evidence"
    ),
    "NO_INDEPENDENT_EVIDENCE": (
        "the tests pass both ways and at least one changed production line is "
        "uncovered, so nothing proves the new behaviour"
    ),
    "NO_VERIFIER_CHANGE": (
        "no changed file is test code or verifier configuration, so the "
        "verifier was not touched"
    ),
    "VERIFIER_WEAKENED": (
        "the head tests accept base behaviour while the base tests reject head "
        "behaviour: the change weakened its own verifier"
    ),
    "VERIFIER_CHANGED_REVIEW_REQUIRED": (
        "the verifier changed in a way that needs human review (both grafted "
        "probes disagree with their host revision, or only the verifier changed)"
    ),
    "INCONCLUSIVE": "the run could not be decided; see the reason below",
    "BASELINE_INVALID": (
        "the base revision is not reproducibly green, so no comparison is "
        "trustworthy"
    ),
    "ABORTED_NO_VERDICT": (
        "a probe returned pytest exit code 2 (user interrupt / internal abort), "
        "so the run was aborted and no verdict was produced"
    ),
}

_PROBE_PURPOSE: dict[tuple[str, str], str] = {
    ("P0", "verdict"): "base source + base tests (baseline)",
    ("P1", "verdict"): "head source + head tests (as submitted)",
    ("P1", "coverage"): "instrumented head run (coverage only, never a verdict)",
    ("P2", "verdict"): "base source + head tests (grafted)",
    ("P3", "verdict"): "head source + base tests (grafted)",
}

_INDENT = "  "


def _heading(title: str, char: str = "-") -> list[str]:
    return ["", title, char * len(title)]


def _one_line(text: object) -> str:
    """Collapse a value to a single display line.

    Findings and reasons come from repository content, so they can contain
    newlines or be very long; the report keeps one record per line so it stays
    scannable (and greppable) regardless of input.
    """
    flat = " ".join(str(text).split())
    return flat if len(flat) <= 300 else flat[:297] + "..."


def _format_verdict(record: dict) -> list[str]:
    verdict = record.get("verdict") or {}
    value = str(verdict.get("value", "UNKNOWN"))
    lines = [
        f"Verdict: {value}",
        f"{_INDENT}meaning:      {_one_line(_VERDICT_MEANING.get(value, 'unrecognised verdict'))}",
        f"{_INDENT}reason:       {_one_line(verdict.get('reason'))}",
        f"{_INDENT}reason code:  {_one_line(verdict.get('reason_code'))}",
        f"{_INDENT}matched rule: {_one_line(verdict.get('matched_rule'))}",
        f"{_INDENT}exit code:    {_one_line(verdict.get('exit_code'))}"
        + " (documented verdict-to-exit-code mapping)",
    ]
    # The CLI records the policy-applied process status alongside the documented
    # code when a gating policy remaps it (design "CI ergonomics").
    exit_status = record.get("exit_status")
    if isinstance(exit_status, dict):
        lines.append(
            f"{_INDENT}process exit: {_one_line(exit_status.get('process_exit_status'))}"
            f" (policy: {_one_line(exit_status.get('policy'))},"
            f" build-blocking: {'yes' if exit_status.get('build_blocking') else 'no'})"
        )
    return lines


def _format_run(record: dict) -> list[str]:
    run = record.get("run") or {}
    validation = record.get("validation") or {}
    lines = _heading("Run")
    lines += [
        f"{_INDENT}tool:       verifierlock {record.get('tool_version', 'unknown')}"
        f" (evidence schema {record.get('schema_version', 'unknown')})",
        f"{_INDENT}run id:     {_one_line(run.get('run_id'))}",
        f"{_INDENT}timestamp:  {_one_line(run.get('timestamp'))}",
        f"{_INDENT}repository: {_one_line(run.get('repo_path'))}",
        f"{_INDENT}base:       {_one_line(run.get('base_ref'))} -> {_one_line(run.get('base_commit'))}",
        f"{_INDENT}head:       {_one_line(run.get('head_ref'))} -> {_one_line(run.get('head_commit'))}",
        f"{_INDENT}timeout:    {_one_line(run.get('timeout_seconds'))}s per probe",
        f"{_INDENT}repository determination: {_one_line(validation.get('determination'))}",
    ]
    return lines


def _format_environments(record: dict) -> list[str]:
    environments = record.get("environments") or []
    lines = _heading("Environments (dependencies only, project package never installed)")
    if not environments:
        lines.append(f"{_INDENT}(none built)")
        return lines
    for env in environments:
        status = "built" if env.get("built") else "FAILED"
        lines.append(
            f"{_INDENT}{str(env.get('revision', '?')):5} {status:6} "
            f"tool={_one_line(env.get('tool'))} "
            f"discovery={_one_line(env.get('discovery'))} "
            f"installed_project={_one_line(env.get('installed_project'))} "
            f"install_kind={_one_line(env.get('install_kind'))}"
        )
        if env.get("error"):
            lines.append(f"{_INDENT * 2}error: {_one_line(env.get('error'))}")
    return lines


def _format_changed_files(record: dict) -> list[str]:
    changed = record.get("changed_files") or []
    unclassifiable = record.get("unclassifiable_files") or []
    lines = _heading(f"Changed files ({len(changed)})")
    if not changed:
        lines.append(f"{_INDENT}(none)")
    for entry in changed:
        hunks = entry.get("hunks") or []
        lines.append(
            f"{_INDENT}{str(entry.get('classification', '?')):16} "
            f"{_one_line(entry.get('path'))} ({len(hunks)} hunk(s))"
        )
    if unclassifiable:
        lines.append(f"{_INDENT}unclassifiable ({len(unclassifiable)}):")
        for path in unclassifiable:
            lines.append(f"{_INDENT * 2}{_one_line(path)}")
    return lines


def _format_probes(record: dict) -> list[str]:
    probes = record.get("probes") or []
    lines = _heading(f"Probes ({len(probes)})")
    if not probes:
        lines.append(f"{_INDENT}(no probe executed; the run was decided before the probe stage)")
        return lines
    for probe in probes:
        probe_id = str(probe.get("probe_id", "?"))
        kind = str(probe.get("kind", "verdict"))
        label = f"{probe_id}#{probe.get('repetition', 0)}"
        exit_code = probe.get("exit_code")
        lines.append(
            f"{_INDENT}{label:6} {kind:8} {str(probe.get('outcome', '?')):14} "
            f"exit={'killed' if exit_code is None else exit_code:<6} "
            f"collected={probe.get('collected', 0):<4} "
            f"passed={probe.get('passed', 0):<4} "
            f"failed={probe.get('failed', 0):<4} "
            f"skipped={probe.get('skipped', 0):<4} "
            f"{float(probe.get('elapsed_seconds', 0.0)):.2f}s"
        )
        purpose = _PROBE_PURPOSE.get((probe_id, kind))
        if purpose:
            lines.append(f"{_INDENT * 3}{purpose}")
        if probe.get("reason"):
            lines.append(f"{_INDENT * 3}reason: {_one_line(probe.get('reason'))}")
        command = probe.get("command_normalised") or probe.get("command") or []
        if command:
            lines.append(f"{_INDENT * 3}command: {_one_line(' '.join(str(a) for a in command))}")
    return lines


def _format_static_findings(record: dict) -> list[str]:
    findings = record.get("static_findings") or []
    lines = _heading(f"Static findings ({len(findings)})")
    lines.append(
        f"{_INDENT}(advisory only: findings inform probe selection and never produce a verdict)"
    )
    if not findings:
        lines.append(f"{_INDENT}(none)")
        return lines
    for finding in findings:
        location = _one_line(finding.get("file"))
        hunk = _one_line(finding.get("hunk"))
        if hunk:
            location = f"{location} {hunk}"
        lines.append(f"{_INDENT}{_one_line(finding.get('kind'))}: {location}")
        if finding.get("detail"):
            lines.append(f"{_INDENT * 3}{_one_line(finding.get('detail'))}")
    return lines


def _format_coverage(record: dict) -> list[str]:
    coverage = record.get("coverage") or {}
    changed_lines = coverage.get("changed_lines") or []
    available = bool(coverage.get("available"))
    uncovered = coverage.get("uncovered_count")
    if uncovered is None:
        uncovered = sum(1 for line in changed_lines if not line.get("covered"))
    lines = _heading("Changed-line coverage")
    lines.append(
        f"{_INDENT}available: {'yes' if available else 'no'}; "
        f"{len(changed_lines)} changed production line(s), {uncovered} uncovered"
    )
    if coverage.get("reason"):
        lines.append(f"{_INDENT}reason: {_one_line(coverage.get('reason'))}")
    if not changed_lines:
        lines.append(f"{_INDENT}(no changed production lines measured)")
        return lines
    for line in changed_lines:
        marker = "covered  " if line.get("covered") else "UNCOVERED"
        lines.append(f"{_INDENT}{marker} {_one_line(line.get('file'))}:{line.get('line')}")
    return lines


def _format_explanation(record: dict) -> list[str]:
    explanation = record.get("explanation")
    if not isinstance(explanation, dict) or not explanation.get("text"):
        return []
    lines = _heading("Explanation (narration only; never affects the verdict)")
    lines.append(f"{_INDENT}source: {_one_line(explanation.get('source'))}")
    for paragraph in str(explanation.get("text")).splitlines():
        lines.append(f"{_INDENT}{paragraph}" if paragraph.strip() else "")
    return lines


def render_report(record: dict) -> str:
    """Render `record` as a human-readable report (Req 12.1).

    Pure: reads the record and returns text, mutating nothing and contacting no
    remote system (Req 12.2, 14.3). Always includes the verdict, the per-probe
    outcomes, the static findings, and the changed-line coverage, even when a
    section is empty.
    """
    title = "VerifierLock report"
    lines: list[str] = [title, "=" * len(title), ""]
    lines += _format_verdict(record)
    lines += _format_run(record)
    lines += _format_environments(record)
    lines += _format_changed_files(record)
    lines += _format_probes(record)
    lines += _format_static_findings(record)
    lines += _format_coverage(record)
    lines += _format_explanation(record)
    lines.append("")
    return "\n".join(lines) + "\n"


def write_report(record: dict, path: Path) -> Path:
    """Render `record` and write it to the local file `path` (Req 12.2).

    Creates parent directories as needed and returns the written path. The only
    destination is the local filesystem; nothing is transmitted (Req 14.3).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(record))
    return path

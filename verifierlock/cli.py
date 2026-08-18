"""CLI: argument parsing, the untrusted-code warning, and the exit-code policy
(Task 18.2).

The CLI is a thin shell around the pipeline. It parses arguments, prints the
untrusted-code warning BEFORE any repository code can run (Req 14.1), invokes
the Orchestrator, writes the Evidence Record and the human-readable report as
local artifacts, and maps the outcome to a process exit code (Req 15).

## Exit codes

Two distinct things are kept separate on purpose:

* The **documented verdict-to-exit-code mapping** (`verdict.VERDICT_EXIT_CODES`
  plus `ABORTED_NO_VERDICT_EXIT_CODE`) assigns one distinct code to each of the
  eight outcomes (Req 15.1-15.9). It is always recorded in the Evidence Record
  as `verdict.exit_code`, whatever policy is in force.
* The **process exit status** is what the shell sees. By default it *is* the
  documented code, so Requirement 15 holds out of the box with no flags.

A CI gate usually wants something softer than the raw mapping, because
NO_VERIFIER_CHANGE (11) and NO_INDEPENDENT_EVIDENCE (10) are non-zero and would
fail builds on perfectly legitimate changes (design "CI ergonomics"). So the
gating policy is opt-in rather than the default:

| `--exit-policy` | process exit status |
|---|---|
| `documented` (default) | the documented code for the outcome (Req 15.1-15.9) |
| `lenient` | non-zero only for VERIFIER_WEAKENED; every other outcome exits 0 |
| `strict` (= `--strict`) | non-zero for VERIFIER_WEAKENED and VERIFIER_CHANGED_REVIEW_REQUIRED |

Under `lenient` / `strict` a build-blocking outcome still exits with its own
documented code (12 / 13), so the mapping is never rewritten -- only suppressed
to 0 for outcomes the policy treats as informational. Both the documented code
and the policy-applied status are recorded in the Evidence Record's
`exit_status` block for auditability, as the design requires.

Note that `documented` is the default even though the design sketch discusses a
lenient default: Requirements 15.1-15.9 mandate that the CLI set a distinct
documented code per verdict, so the requirement-conformant behaviour is what you
get unless you explicitly opt into a gating policy.

## Locality (Req 14.2, 14.3)

`--repo` must be an existing local directory; remote-looking references are
rejected before anything runs. No repository content and no secret is ever
transmitted, and the CLI writes only to the paths the caller names. The one piece
of outbound traffic a run can generate is the per-revision dependency install
that `uv`/`pip` performs against the configured package index (overridable with
`--install-cmd`); the engine, the report, and the optional explanation are local.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from . import orchestrator
from .explain import Narrator, explain
from .report import render_report, write_report
from .verdict import (
    ABORTED_NO_VERDICT_EXIT_CODE,
    VERDICT_EXIT_CODES,
    Verdict,
)

PROG = "verifierlock"

#: Default per-probe timeout in seconds (Req 7b.1).
DEFAULT_TIMEOUT_SECONDS = 600.0

#: Exit status for a CLI usage error. Deliberately outside the documented
#: verdict codes (0, 10-16) so it can never be mistaken for a verdict.
USAGE_ERROR_EXIT_CODE = 2

UNTRUSTED_CODE_WARNING = (
    "WARNING: VerifierLock runs this repository's test suite. Running "
    "repository tests EXECUTES REPOSITORY CODE with your privileges, including "
    "code from both the base and the head revision. Only point VerifierLock at "
    "a repository you trust. Nothing is transmitted off this machine."
)


class ExitPolicy(Enum):
    """Which outcomes are build-blocking (see the module docstring)."""

    DOCUMENTED = "documented"
    LENIENT = "lenient"
    STRICT = "strict"


#: Outcomes that fail the build under `lenient`: weakening the verifier is the
#: one finding that is never acceptable.
LENIENT_BLOCKING: frozenset[Verdict] = frozenset({Verdict.VERIFIER_WEAKENED})

#: Outcomes that fail the build under `strict`: the caller opts into also
#: blocking on changes that need human review.
STRICT_BLOCKING: frozenset[Verdict] = frozenset(
    {Verdict.VERIFIER_WEAKENED, Verdict.VERIFIER_CHANGED_REVIEW_REQUIRED}
)


def documented_exit_code(verdict: Verdict | None) -> int:
    """The documented exit code for `verdict`, or the aborted-no-verdict code.

    `verdict is None` means the run aborted on pytest exit code 2 and produced
    no verdict, which has its own distinct documented code (Req 8.4, 15.9).
    """
    if verdict is None:
        return ABORTED_NO_VERDICT_EXIT_CODE
    return VERDICT_EXIT_CODES[verdict]


def is_build_blocking(verdict: Verdict | None, policy: ExitPolicy) -> bool:
    """Whether `verdict` fails the build under `policy`.

    Under `documented` every non-zero documented code is build-blocking (the
    mapping is the policy). Under the gating policies only the listed verdicts
    block; an aborted run (`verdict is None`) is an operational failure, not
    evidence of weakening, so it does not block under a gating policy.
    """
    if policy is ExitPolicy.DOCUMENTED:
        return documented_exit_code(verdict) != 0
    if verdict is None:
        return False
    blocking = LENIENT_BLOCKING if policy is ExitPolicy.LENIENT else STRICT_BLOCKING
    return verdict in blocking


def process_exit_status(verdict: Verdict | None, policy: ExitPolicy) -> int:
    """The process exit status for `verdict` under `policy` (Req 15.1-15.9).

    Under the default `documented` policy this is exactly the documented code.
    Under a gating policy a blocking outcome keeps its documented code and every
    other outcome is reported as 0; the documented code is still recorded in the
    Evidence Record, so the mapping itself is never changed.
    """
    code = documented_exit_code(verdict)
    if policy is ExitPolicy.DOCUMENTED:
        return code
    return code if is_build_blocking(verdict, policy) else 0


def exit_status_block(verdict: Verdict | None, policy: ExitPolicy) -> dict:
    """The auditable record of both codes (design "CI ergonomics")."""
    return {
        "documented_exit_code": documented_exit_code(verdict),
        "process_exit_status": process_exit_status(verdict, policy),
        "policy": policy.value,
        "build_blocking": is_build_blocking(verdict, policy),
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (design CLI section)."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Determine whether a Git change carries independent test evidence "
            "or weakened its own verifier."
        ),
        epilog=(
            "Exit codes: 0 INDEPENDENT_EVIDENCE, 10 NO_INDEPENDENT_EVIDENCE, "
            "11 NO_VERIFIER_CHANGE, 12 VERIFIER_WEAKENED, "
            "13 VERIFIER_CHANGED_REVIEW_REQUIRED, 14 INCONCLUSIVE, "
            "15 BASELINE_INVALID, 16 aborted with no verdict, "
            f"{USAGE_ERROR_EXIT_CODE} usage error."
        ),
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="path to the local Git repository to analyse (default: %(default)s)",
    )
    parser.add_argument(
        "--base", required=True, help="base revision (branch, tag, or commit)"
    )
    parser.add_argument(
        "--head", default="HEAD", help="head revision (default: %(default)s)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="per-probe timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        metavar="PATH",
        help="write the machine-readable Evidence Record to PATH",
    )
    parser.add_argument(
        "--report",
        dest="report_path",
        metavar="PATH",
        help="write the human-readable report to PATH",
    )
    parser.add_argument(
        "--install-cmd",
        dest="install_cmd",
        metavar="CMD",
        help=(
            "override dependency discovery with CMD; it MUST install "
            "dependencies only and MUST NOT install the project package"
        ),
    )
    parser.add_argument(
        "--exit-policy",
        dest="exit_policy",
        choices=[policy.value for policy in ExitPolicy],
        default=None,
        help=(
            "which outcomes fail the build: documented (default, the documented "
            "verdict-to-exit-code mapping), lenient (only VERIFIER_WEAKENED), "
            "strict (VERIFIER_WEAKENED and VERIFIER_CHANGED_REVIEW_REQUIRED)"
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="shorthand for --exit-policy strict",
    )
    explain_group = parser.add_mutually_exclusive_group()
    explain_group.add_argument(
        "--explain",
        dest="explain",
        action="store_true",
        default=False,
        help=(
            "append a prose narration of the finished Evidence Record; it can "
            "never change the verdict, the exit code, or the reproducible core"
        ),
    )
    explain_group.add_argument(
        "--no-explain",
        dest="explain",
        action="store_false",
        help="do not narrate (default)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not print the report to stdout (artifacts are still written)",
    )
    parser.add_argument(
        "--p0-repetitions",
        dest="p0_repetitions",
        type=int,
        default=2,
        metavar="N",
        help="baseline repetitions used to detect a nondeterministic baseline (minimum 2)",
    )
    return parser


def _resolve_policy(parser: argparse.ArgumentParser, args: argparse.Namespace) -> ExitPolicy:
    if args.exit_policy is None:
        return ExitPolicy.STRICT if args.strict else ExitPolicy.DOCUMENTED
    policy = ExitPolicy(args.exit_policy)
    if args.strict and policy is not ExitPolicy.STRICT:
        parser.error(f"--strict conflicts with --exit-policy {policy.value}")
    return policy


def _validate_repo(parser: argparse.ArgumentParser, raw: str) -> Path:
    """Reject anything that is not an existing local directory (Req 14.2)."""
    if "://" in raw or raw.startswith("git@"):
        parser.error(
            f"--repo must be a local path; v1 does not clone remote repositories ({raw!r})"
        )
    repo = Path(raw).expanduser()
    if not repo.is_dir():
        parser.error(f"--repo is not an existing local directory: {raw!r}")
    return repo


def main(
    argv: list[str] | None = None,
    *,
    run_pipeline: Callable[..., orchestrator.OrchestratorResult] | None = None,
    narrator: Narrator | None = None,
    stdout=None,
    stderr=None,
) -> int:
    """Run VerifierLock and return the process exit status (Req 14.1, 15).

    `run_pipeline` defaults to `orchestrator.run` and is injectable so the CLI's
    argument handling, artifact writing, and exit-code policy can be tested
    without executing a repository's test suite. `narrator` is the optional
    Explanation_Model backend; with the default `None` the offline narration is
    used, so `--explain` needs no key and no network (Req 13.3).
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr

    policy = _resolve_policy(parser, args)
    repo = _validate_repo(parser, args.repo)
    if args.p0_repetitions < 2:
        parser.error("--p0-repetitions must be at least 2 (Req 8c.1)")

    # Req 14.1: warn BEFORE the pipeline starts, i.e. before any probe can run.
    print(UNTRUSTED_CODE_WARNING, file=err, flush=True)
    print(
        f"{PROG}: analysing {repo} ({args.base}..{args.head})",
        file=err,
        flush=True,
    )

    runner = run_pipeline if run_pipeline is not None else orchestrator.run
    result = runner(
        repo,
        base_ref=args.base,
        head_ref=args.head,
        timeout=args.timeout,
        install_cmd=args.install_cmd,
        p0_repetitions=args.p0_repetitions,
    )

    record = result.record
    verdict = result.verdict
    status = process_exit_status(verdict, policy)
    record["exit_status"] = exit_status_block(verdict, policy)

    # The explanation is attached AFTER the verdict, the documented code, and the
    # process status are fixed, and is excluded from the reproducible core, so it
    # cannot influence any of them (Req 13.2).
    if args.explain:
        record["explanation"] = explain(record, narrator=narrator).as_dict()

    report_text = render_report(record)
    if not args.quiet:
        print(report_text, end="", file=out)

    if args.json_path:
        json_path = Path(args.json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        print(f"{PROG}: evidence record written to {json_path}", file=err)
    if args.report_path:
        written = write_report(record, Path(args.report_path))
        print(f"{PROG}: report written to {written}", file=err)

    verdict_label = verdict.value if verdict is not None else "ABORTED_NO_VERDICT"
    print(
        f"{PROG}: {verdict_label} (documented exit code "
        f"{documented_exit_code(verdict)}, process exit status {status}, "
        f"policy {policy.value})",
        file=err,
    )
    return status


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py
    raise SystemExit(main())

"""One-command demo over the bundled labelled fixtures (`python -m verifierlock.demo`).

The fixtures under `fixtures/<scenario>/` are stored as two directory snapshots
(`base/` and `head/`) rather than a nested `.git`, so they stay committable
inside this repository. This module materialises a snapshot pair into a real
two-commit Git repository in a temp directory and runs the CLI over
`base..head`, so a cold clone can reproduce a documented verdict with a single
command:

    python -m verifierlock.demo                 # weakened_authz -> VERIFIER_WEAKENED (12)
    python -m verifierlock.demo independent_evidence
    python -m verifierlock.demo --list

It is a thin wrapper: it builds the repository and then calls `cli.main`, so the
verdict, the report, and the exit code are exactly what a normal CLI invocation
produces. `build_scenario_repo` is also the helper the integration tests use, so
the demo and the tests exercise one implementation.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import cli

#: Expected verdict and documented exit code per bundled scenario, for the
#: demo's own summary line (`fixtures/README.md` is the authoritative table).
SCENARIOS: dict[str, tuple[str, int]] = {
    "weakened_authz": ("VERIFIER_WEAKENED", 12),
    "deleted_test": ("VERIFIER_WEAKENED", 12),
    "independent_evidence": ("INDEPENDENT_EVIDENCE", 0),
    "review_required": ("VERIFIER_CHANGED_REVIEW_REQUIRED", 13),
}

DEFAULT_SCENARIO = "weakened_authz"


def fixtures_root() -> Path:
    """Locate the bundled `fixtures/` directory.

    Checks the repository root next to the installed package first (the cold
    clone and Docker image layouts), then the current working directory.
    """
    candidates = [
        Path(__file__).resolve().parent.parent / "fixtures",
        Path.cwd() / "fixtures",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        "could not locate the bundled fixtures/ directory; run from a clone of "
        "the repository or pass --fixtures-root"
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True, text=True
    )


def _copy_snapshot(snapshot: Path, dest: Path) -> None:
    for entry in snapshot.iterdir():
        target = dest / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)


def _clear_tree(repo: Path) -> None:
    """Remove every entry except `.git`, so the head commit is a clean tree."""
    for entry in repo.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def build_scenario_repo(
    scenario: str, dest: Path, *, root: Path | None = None
) -> Path:
    """Create a two-commit Git repo for `scenario` under `dest`, return its path.

    Commits the `base/` snapshot as commit `base`, then replaces the whole tree
    with the `head/` snapshot and commits it as `head`. Building the head commit
    from a cleared tree is what makes deletions between the snapshots (e.g. the
    wholesale test removal in `deleted_test`) show up as real deletions in the
    diff.
    """
    scenario_dir = (root or fixtures_root()) / scenario
    base_snapshot = scenario_dir / "base"
    head_snapshot = scenario_dir / "head"
    if not base_snapshot.is_dir() or not head_snapshot.is_dir():
        raise FileNotFoundError(f"fixture {scenario!r} is missing base/ or head/")

    repo = Path(dest)
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixtures@verifierlock.test")
    _git(repo, "config", "user.name", "VerifierLock Fixtures")
    _git(repo, "config", "commit.gpgsign", "false")

    _copy_snapshot(base_snapshot, repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    _clear_tree(repo)
    _copy_snapshot(head_snapshot, repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")

    return repo


def main(argv: list[str] | None = None) -> int:
    """Materialise a bundled fixture and run the CLI over it.

    Returns the CLI's process exit status, so `python -m verifierlock.demo`
    exits with the documented verdict code (12 for the flagship fixture).
    """
    parser = argparse.ArgumentParser(
        prog="python -m verifierlock.demo",
        description=(
            "Run VerifierLock against a bundled labelled fixture (a deliberately "
            "defective test fixture, not real software)."
        ),
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        default=DEFAULT_SCENARIO,
        choices=sorted(SCENARIOS),
        help=f"fixture scenario to analyse (default: {DEFAULT_SCENARIO})",
    )
    parser.add_argument(
        "--list", action="store_true", help="list the bundled scenarios and exit"
    )
    parser.add_argument(
        "--fixtures-root",
        dest="fixtures_root",
        metavar="PATH",
        help="override the location of the bundled fixtures/ directory",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the materialised fixture repository instead of deleting it",
    )
    parser.add_argument(
        "--json", dest="json_path", metavar="PATH", help="write the Evidence Record to PATH"
    )
    parser.add_argument(
        "--report", dest="report_path", metavar="PATH", help="write the report to PATH"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not print the report; useful when you only want the exit code",
    )
    args = parser.parse_args(argv)

    if args.list:
        print("Bundled fixture scenarios (see fixtures/README.md):")
        for name, (verdict, code) in sorted(SCENARIOS.items()):
            marker = " (default)" if name == DEFAULT_SCENARIO else ""
            print(f"  {name:22} -> {verdict} (exit {code}){marker}")
        return 0

    root = Path(args.fixtures_root) if args.fixtures_root else fixtures_root()
    expected_verdict, expected_code = SCENARIOS[args.scenario]
    workdir = Path(tempfile.mkdtemp(prefix="verifierlock-demo-"))
    repo = workdir / args.scenario

    print(
        f"VerifierLock demo: bundled fixture {args.scenario!r}\n"
        f"  fixture repository: {repo}\n"
        f"  expected verdict:   {expected_verdict} (documented exit code {expected_code})\n"
        "  NOTE: the fixture's defect is deliberate; it is a labelled test "
        "fixture, not real software.\n",
        file=sys.stderr,
    )

    try:
        build_scenario_repo(args.scenario, repo, root=root)
        argv_cli = ["--repo", str(repo), "--base", "HEAD~1", "--head", "HEAD"]
        if args.json_path:
            argv_cli += ["--json", args.json_path]
        if args.report_path:
            argv_cli += ["--report", args.report_path]
        if args.quiet:
            argv_cli.append("--quiet")
        status = cli.main(argv_cli)
    finally:
        if args.keep:
            print(f"demo: fixture repository kept at {repo}", file=sys.stderr)
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    if status != expected_code:
        print(
            f"demo: WARNING expected exit code {expected_code} "
            f"({expected_verdict}) but got {status}",
            file=sys.stderr,
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())

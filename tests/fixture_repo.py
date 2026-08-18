"""Build a throwaway Git repo from a bundled fixture's base/head snapshots.

The fixtures under `fixtures/<scenario>/` are stored as two directory snapshots
(`base/` and `head/`) rather than a nested `.git`, so they stay committable
inside this repository. This helper materialises a real two-commit Git history
(`base` then `head`) in a temp directory so the full VerifierLock pipeline can
run over `base..head`. Deletions between snapshots (e.g. a wholesale test file
removal) are captured because the head commit is built from a clean tree.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


def _copy_snapshot(snapshot: Path, dest: Path) -> None:
    for entry in snapshot.iterdir():
        target = dest / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)


def _clear_tree(repo: Path) -> None:
    """Remove every tracked entry except the `.git` directory."""
    for entry in repo.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def build_scenario_repo(scenario: str, dest: Path) -> Path:
    """Create a two-commit Git repo for `scenario` under `dest`, return its path.

    Commits the `base/` snapshot as commit `base`, then replaces the whole tree
    with the `head/` snapshot and commits it as `head`.
    """
    scenario_dir = FIXTURES_ROOT / scenario
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

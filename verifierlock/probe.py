"""Probe_Runner core: command composition + execution for P0/P1 (Task 10).

This module is the deterministic, side-effecting probe layer. It has three
responsibilities, mirroring design.md's Probe_Runner section and the
Determinism Controls table:

1. **Command composition (Task 10.1).** `compose_probe_command` builds every
   probe command through ONE function so the determinism controls cannot drift
   between probes (Req 6.2, 7.1-7.5):

   | Control | Mechanism |
   |---|---|
   | Import mode | `--import-mode=importlib` (Req 6.2) |
   | Disable cache provider | `-p no:cacheprovider` (Req 7.1) |
   | Disable random ordering | `-p no:randomly` (Req 7.2) |
   | Neutralise repo addopts | `-o addopts=` (Req 7.3) |
   | Fixed hash seed | env `PYTHONHASHSEED=0` (Req 7.4) |
   | No bytecode writing | env `PYTHONDONTWRITEBYTECODE=1` (Req 7.4) |
   | Fixed rootdir | `--rootdir=<worktree>` (Req 7.5) |

   The interpreter is injected, so probes are runnable before the
   Environment_Builder (Task 13) exists; callers pass the built env's
   interpreter later.

2. **Execution (Task 10.2).** `run_probe` purges cached bytecode before and
   after every probe including on timeout/abort (Req 7.6, 7.7), enforces a
   configurable per-probe timeout by killing the whole process tree and
   reporting INCONCLUSIVE with the elapsed duration (Req 7b.1-7b.3), classifies
   the exit code via `interpret_exit_code` (Req 8), detects the importlib
   inter-test import limitation and reports INCONCLUSIVE for it (Req 6.3), and
   raises `ProbeAbort` on pytest exit code 2 so the run aborts with no verdict
   (Req 8.4). The subprocess launch is isolated behind an injectable `launcher`
   so timeout/import-limitation/abort behaviour is property-testable without a
   real pytest.

3. **P0 / P1 (Task 10.3).** `run_p0` runs the baseline at least twice, each in a
   separately-created fresh worktree (Req 8c.1, 8c.3); `run_p1` runs the
   un-instrumented head verdict probe (Req 6.1). The P1 coverage run (Concern 2)
   and the P2/P3 graft compositions are later tasks.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import reasons
from .exit_code import interpret_exit_code
from .types import ProbeOutcome, ProbeResult

# --- Command composition (Task 10.1) ---------------------------------------


@dataclass(frozen=True)
class ProbeCommand:
    """A fully-composed probe invocation: the argv and the process env.

    `env` is a complete environment mapping (the base environment plus the
    determinism env vars) ready to hand to the launcher.
    """

    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)


def compose_probe_command(
    interpreter: str | Path,
    worktree: Path,
    *,
    extra_args: Sequence[str] = (),
    base_env: dict[str, str] | None = None,
) -> ProbeCommand:
    """Compose a probe command with every determinism control (Req 6.2, 7.1-7.5).

    `interpreter` is the Python executable to launch pytest with (injected so
    probes work before the Environment_Builder exists). `worktree` is the
    probe's isolated checkout, used as the fixed `--rootdir`. `extra_args` are
    appended after the controls (e.g. a coverage run's target selection); they
    are never allowed to displace the controls, which always precede them.
    """
    argv: tuple[str, ...] = (
        str(interpreter),
        "-m",
        "pytest",
        "--import-mode=importlib",  # Req 6.2
        "-p",
        "no:cacheprovider",  # Req 7.1 (also suppresses .pytest_cache writes)
        "-p",
        "no:randomly",  # Req 7.2 (harmless no-op when pytest-randomly absent)
        "-o",
        "addopts=",  # Req 7.3 (override repo addopts to empty)
        "--rootdir",
        str(worktree),  # Req 7.5
        *extra_args,
    )
    env = dict(base_env if base_env is not None else os.environ)
    env["PYTHONHASHSEED"] = "0"  # Req 7.4
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # Req 7.4
    return ProbeCommand(argv=argv, env=env)


# --- Bytecode purge (Task 10.2, Req 7.6, 7.7) ------------------------------

_PURGE_DIR_NAMES = ("__pycache__", ".pytest_cache")
_PURGE_FILE_SUFFIXES = (".pyc", ".pyo")


def purge_bytecode(root: Path) -> None:
    """Delete cached bytecode and pytest-cache under `root` (Req 7.6, 7.7).

    Removes every `__pycache__` / `.pytest_cache` directory and every loose
    `.pyc` / `.pyo` file beneath `root`. Best-effort and idempotent: it never
    raises if the tree is missing or a path cannot be removed, so it is safe to
    call both before a probe and in the post-probe `finally` (including after a
    timeout kill or an abort).
    """
    root = Path(root)
    if not root.exists():
        return
    # Walk bottom-up so nested __pycache__ dirs are removed before parents.
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        base = Path(dirpath)
        for name in filenames:
            if name.endswith(_PURGE_FILE_SUFFIXES):
                try:
                    (base / name).unlink()
                except OSError:
                    pass
        for name in dirnames:
            if name in _PURGE_DIR_NAMES:
                shutil.rmtree(base / name, ignore_errors=True)


# --- Launch abstraction (Task 10.2) ----------------------------------------


@dataclass(frozen=True)
class LaunchResult:
    """The raw outcome of launching a probe process, before interpretation.

    `returncode` is `None` when the process was killed for exceeding its
    timeout, in which case `timed_out` is True. `elapsed_seconds` is the
    wall-clock duration of the launch (Req 7b.3).
    """

    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    elapsed_seconds: float


# A launcher runs argv in cwd with env and an optional timeout (seconds).
Launcher = Callable[[tuple[str, ...], Path, dict[str, str], float | None], LaunchResult]


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill the process group started with the probe (Req 7b.2)."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        # Fall back to killing just the process if the group is gone.
        try:
            proc.kill()
        except OSError:
            pass


def _subprocess_launcher(
    argv: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    timeout: float | None,
) -> LaunchResult:
    """Default launcher: run the probe in a fresh session so the whole process
    tree can be killed on timeout (Req 7b.1, 7b.2), timing the run (Req 7b.3)."""
    start = time.monotonic()
    proc = subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # own process group -> killable as a tree
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        elapsed = time.monotonic() - start
        return LaunchResult(proc.returncode, stdout, stderr, False, elapsed)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            stdout, stderr = "", ""
        elapsed = time.monotonic() - start
        return LaunchResult(None, stdout or "", stderr or "", True, elapsed)


# --- Abort signal (Task 10.2, Req 8.4) -------------------------------------


class ProbeAbort(Exception):
    """Raised when a probe returns pytest exit code 2 (Req 8.4).

    Exit code 2 aborts the whole run with no verdict; the CLI maps this to the
    distinct "aborted, no verdict" exit code (Req 15.9). Carries the probe id
    and worktree so the orchestrator can record the abort.
    """

    def __init__(self, probe_id: str, worktree_path: str) -> None:
        self.probe_id = probe_id
        self.worktree_path = worktree_path
        super().__init__(f"probe {probe_id} returned pytest exit code 2 (abort, no verdict)")


# --- Inter-test import limitation detection (Task 10.2, Req 6.3) -----------

_MODULE_NOT_FOUND_RE = re.compile(r"No module named ['\"]([\w.]+)['\"]")
_COLLECTION_MARKERS = (
    "while importing test module",  # pytest's phrasing for a test-module import failure
    "error collecting",
    "errors during collection",
)


def _looks_like_test_module(module: str) -> bool:
    """Heuristic: is `module` a module inside the tests tree (a test module or
    a test-utility), as opposed to a third-party dependency?"""
    top = module.split(".", 1)[0].lower()
    return (
        top.startswith("test")
        or top.endswith("_test")
        or top == "conftest"
        or top == "tests"
        or "test" in top
    )


def detect_import_limitation(output: str) -> str | None:
    """Detect the importlib inter-test import limitation (Req 6.3, decision log 4.3).

    Under `--import-mode=importlib` a test module that imports a *sibling* test
    module or a test-utility module inside the tests tree fails to import,
    because those modules are not importable as top-level packages. This is a
    known limitation, distinct from a genuine missing third-party dependency
    (which is `ENV_INCOMPATIBLE`, handled by the Environment_Builder).

    Returns the offending module name when the failure is an inter-test import,
    else `None`. Detection is purely a function of the captured output, so it is
    property-testable with scripted output.
    """
    lowered = output.lower()
    if not any(marker in lowered for marker in _COLLECTION_MARKERS):
        return None
    for match in _MODULE_NOT_FOUND_RE.finditer(output):
        module = match.group(1)
        if _looks_like_test_module(module):
            return module
    return None


# --- ENV_INCOMPATIBLE detection for P2/P3 (Task 13.2, Req 4.5, 4.7) ---------

_CANNOT_IMPORT_NAME_RE = re.compile(r"cannot import name ['\"]([\w.]+)['\"]")


def detect_env_incompatible(output: str) -> str | None:
    """Detect that the *other revision's source* will not import/collect under
    the selected environment (Req 4.5, 4.7, reason code ENV_INCOMPATIBLE).

    This is the P2/P3 counterpart to `detect_import_limitation`. It fires on a
    collection-time import failure that is NOT an inter-test import: a missing
    module that is not a test module (a genuinely absent dependency or a
    production module that does not exist in this revision), or an
    `ImportError: cannot import name X` -- the signature of the disclosed
    additive-change limitation, where a head test imports a symbol that the base
    source does not yet define (design "Known Limitations").

    A genuine *test failure* has no collection marker and is never matched here;
    it is classified TESTS_FAILED per Requirement 8 (Req 4.6). Returns a detail
    string when env-incompatible, else `None`. Purely a function of the output,
    so it is property-testable with scripted output.
    """
    lowered = output.lower()
    if not any(marker in lowered for marker in _COLLECTION_MARKERS):
        return None
    # "cannot import name X from Y" -> additive symbol absent in this revision.
    name_match = _CANNOT_IMPORT_NAME_RE.search(output)
    if name_match is not None:
        return f"cannot import name {name_match.group(1)!r}"
    # A missing module that is NOT an inter-test module -> incompatible env.
    for match in _MODULE_NOT_FOUND_RE.finditer(output):
        module = match.group(1)
        if not _looks_like_test_module(module):
            return f"No module named {module!r}"
    return None


# --- pytest count parsing (Task 10.2, Req 6.4) -----------------------------

_COLLECTED_RE = re.compile(r"collected (\d+) item")
_PASSED_RE = re.compile(r"(\d+) passed")
_FAILED_RE = re.compile(r"(\d+) failed")
_SKIPPED_RE = re.compile(r"(\d+) skipped")


def _first_int(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


def parse_pytest_counts(output: str) -> tuple[int, int, int, int]:
    """Best-effort parse of `(collected, passed, failed, skipped)` from pytest
    output (Req 6.4). Missing values default to 0; when no explicit collected
    count is present it is inferred as passed + failed + skipped."""
    passed = _first_int(_PASSED_RE, output)
    failed = _first_int(_FAILED_RE, output)
    skipped = _first_int(_SKIPPED_RE, output)
    collected_match = _COLLECTED_RE.search(output)
    collected = int(collected_match.group(1)) if collected_match else passed + failed + skipped
    return collected, passed, failed, skipped


# --- Probe execution (Task 10.2) -------------------------------------------


def run_probe(
    worktree_path: Path,
    *,
    probe_id: str = "P1",
    repetition: int = 0,
    interpreter: str | Path | None = None,
    timeout: float | None = None,
    extra_args: Sequence[str] = (),
    base_env: dict[str, str] | None = None,
    launcher: Launcher | None = None,
    env_incompatible_detection: bool = False,
) -> ProbeResult:
    """Run one probe in `worktree_path` and return its `ProbeResult` (Task 10.2).

    Applies all determinism controls, purges bytecode before and after (even on
    timeout or abort), enforces the per-probe `timeout`, integrates
    `interpret_exit_code`, detects the inter-test import limitation, and raises
    `ProbeAbort` on pytest exit code 2.

    When `env_incompatible_detection` is set (the P2/P3 case, Task 13.2), a
    collection-time import failure of the other revision's source under the
    selected environment is reported INCONCLUSIVE `ENV_INCOMPATIBLE` (Req 4.5,
    4.7) instead of aborting; a genuine test failure is unaffected and stays
    TESTS_FAILED (Req 4.6).
    """
    worktree_path = Path(worktree_path)
    interpreter = interpreter or sys.executable
    launcher = launcher or _subprocess_launcher

    command = compose_probe_command(
        interpreter, worktree_path, extra_args=extra_args, base_env=base_env
    )

    # Req 7.6: purge before the probe.
    purge_bytecode(worktree_path)
    try:
        launch = launcher(command.argv, worktree_path, command.env, timeout)

        # Req 7b.2: a timed-out probe is INCONCLUSIVE with the elapsed duration.
        if launch.timed_out:
            return _timeout_result(command, worktree_path, probe_id, repetition, timeout, launch)

        combined_output = f"{launch.stdout}\n{launch.stderr}"

        # Req 6.3: the inter-test import limitation is INCONCLUSIVE, and takes
        # precedence over the generic exit-code handling (a collection failure
        # can surface as several exit codes).
        offending = detect_import_limitation(combined_output)
        if offending is not None:
            return _result(
                command,
                worktree_path,
                probe_id,
                repetition,
                exit_code=launch.returncode,
                outcome=ProbeOutcome.INCONCLUSIVE,
                reason=f"{reasons.IMPORT_LIMITATION}:{offending}",
                output=combined_output,
                elapsed=launch.elapsed_seconds,
            )

        # Req 4.5/4.7: for P2/P3, the other revision's source failing to
        # import/collect under the selected env is ENV_INCOMPATIBLE (not an
        # abort and not a test failure). Checked before the exit-2 abort so a
        # collection failure surfacing as exit 2 does not abort the run.
        if env_incompatible_detection:
            incompatible = detect_env_incompatible(combined_output)
            if incompatible is not None:
                return _result(
                    command,
                    worktree_path,
                    probe_id,
                    repetition,
                    exit_code=launch.returncode,
                    outcome=ProbeOutcome.INCONCLUSIVE,
                    reason=f"{reasons.ENV_INCOMPATIBLE}:{incompatible}",
                    output=combined_output,
                    elapsed=launch.elapsed_seconds,
                )

        # Req 8.4: exit code 2 aborts the whole run with no verdict.
        if launch.returncode == 2:
            raise ProbeAbort(probe_id, str(worktree_path))

        outcome, reason = interpret_exit_code(launch.returncode)
        return _result(
            command,
            worktree_path,
            probe_id,
            repetition,
            exit_code=launch.returncode,
            outcome=outcome,
            reason=reason,
            output=combined_output,
            elapsed=launch.elapsed_seconds,
        )
    finally:
        # Req 7.7: purge after completion OR termination (timeout / abort).
        purge_bytecode(worktree_path)


def _result(
    command: ProbeCommand,
    worktree_path: Path,
    probe_id: str,
    repetition: int,
    *,
    exit_code: int | None,
    outcome: ProbeOutcome,
    reason: str | None,
    output: str,
    elapsed: float,
) -> ProbeResult:
    collected, passed, failed, skipped = parse_pytest_counts(output)
    return ProbeResult(
        probe_id=probe_id,
        repetition=repetition,
        command=command.argv,
        exit_code=exit_code,
        outcome=outcome,
        collected=collected,
        passed=passed,
        failed=failed,
        skipped=skipped,
        elapsed_seconds=elapsed,
        reason=reason,
        worktree_path=str(worktree_path),
    )


def _timeout_result(
    command: ProbeCommand,
    worktree_path: Path,
    probe_id: str,
    repetition: int,
    timeout: float | None,
    launch: LaunchResult,
) -> ProbeResult:
    reason = (
        f"{reasons.PROBE_TIMEOUT}:timeout={timeout}s,"
        f"elapsed={launch.elapsed_seconds:.2f}s"
    )
    return ProbeResult(
        probe_id=probe_id,
        repetition=repetition,
        command=command.argv,
        exit_code=None,  # terminated -> no exit code
        outcome=ProbeOutcome.INCONCLUSIVE,
        collected=0,
        passed=0,
        failed=0,
        skipped=0,
        elapsed_seconds=launch.elapsed_seconds,
        reason=reason,
        worktree_path=str(worktree_path),
    )


# --- P0 / P1 (Task 10.3) ---------------------------------------------------


def run_p0(
    worktrees: Sequence[Path],
    *,
    interpreter: str | Path | None = None,
    timeout: float | None = None,
    extra_args: Sequence[str] = (),
    base_env: dict[str, str] | None = None,
    launcher: Launcher | None = None,
) -> tuple[ProbeResult, ...]:
    """Run the P0 baseline probe once per provided worktree (Req 8c.1, 8c.3).

    Each element of `worktrees` must be a separately-created fresh worktree at
    the base commit (the Worktree_Manager guarantees per-slot uniqueness), so
    side effects from one repetition cannot contaminate another. At least two
    repetitions are required so the Verdict_Engine can detect a nondeterministic
    baseline (Req 8c.1); the outcomes are returned for it to compare.
    """
    if len(worktrees) < 2:
        raise ValueError("P0 requires at least two repetitions (Req 8c.1)")
    return tuple(
        run_probe(
            worktree,
            probe_id="P0",
            repetition=index,
            interpreter=interpreter,
            timeout=timeout,
            extra_args=extra_args,
            base_env=base_env,
            launcher=launcher,
        )
        for index, worktree in enumerate(worktrees)
    )


def run_p1(
    worktree: Path,
    *,
    interpreter: str | Path | None = None,
    timeout: float | None = None,
    extra_args: Sequence[str] = (),
    base_env: dict[str, str] | None = None,
    launcher: Launcher | None = None,
) -> ProbeResult:
    """Run the un-instrumented P1 head verdict probe (Req 6.1).

    This is the outcome Requirement 10.3 consumes. Coverage is collected by a
    separate instrumented P1 run (Concern 2), implemented in a later task.
    """
    return run_probe(
        worktree,
        probe_id="P1",
        repetition=0,
        interpreter=interpreter,
        timeout=timeout,
        extra_args=extra_args,
        base_env=base_env,
        launcher=launcher,
    )


# --- P2/P3 graft composition (Task 13.2) -----------------------------------


def graft_tests(source_worktree: Path, dest_worktree: Path, test_paths: Sequence[str]) -> None:
    """Graft `source_worktree`'s test paths into `dest_worktree` (Req 6.5-6.7).

    Composes a probe's test set with three invariants:

    1. **Never modifies production source.** Only the entries in `test_paths`
       (already classified as TEST by the File_Classifier) are touched; no
       production file in `dest_worktree` is written or removed.
    2. **Never copies verifier configuration.** Callers pass test paths only;
       verifier-config paths are excluded upstream (Req 6.7).
    3. **Delete-before-copy.** Each destination test path is cleared before the
       source copy, so a test that exists only in the destination revision
       cannot linger and be counted alongside the grafted set -- after grafting,
       the destination test paths are exactly the source revision's test set
       (design Probe_Runner "Delete-before-copy", evidence-correctness measure).

    `test_paths` are worktree-relative and may be files or directories. Missing
    source entries are skipped (the destination copy is still cleared, honouring
    delete-before-copy for a test deleted in the grafted revision).
    """
    source_worktree = Path(source_worktree)
    dest_worktree = Path(dest_worktree)
    for rel in test_paths:
        rel_norm = str(rel).strip()
        if not rel_norm or os.path.isabs(rel_norm):
            continue
        dest = dest_worktree / rel_norm
        # Delete-before-copy: clear the destination entry first.
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest, ignore_errors=True)
        elif dest.exists() or dest.is_symlink():
            try:
                dest.unlink()
            except OSError:
                pass

        src = source_worktree / rel_norm
        if src.is_dir() and not src.is_symlink():
            shutil.copytree(src, dest)
        elif src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        # If src does not exist, the grafted revision deleted this test path;
        # leaving the destination cleared is correct.


# --- P2/P3 runners (Task 13.2) ---------------------------------------------


def run_p2(
    base_worktree: Path,
    head_worktree: Path,
    head_test_paths: Sequence[str],
    head_env,
    *,
    timeout: float | None = None,
    launcher: Launcher | None = None,
) -> ProbeResult:
    """Run P2: BASE production source + grafted HEAD tests, under the HEAD env.

    Grafts the head test paths into the base worktree (delete-before-copy) and
    runs pytest there using the head environment's interpreter, with PYTHONPATH
    pinned to the BASE worktree so the on-disk base source wins over
    site-packages (Concern 3). The environment follows the tests (head), the
    source is base (Req 4.4). A collection failure of the base source under the
    head env is ENV_INCOMPATIBLE (Req 4.5).
    """
    from .environment import probe_env  # local import avoids a cycle at import time

    graft_tests(head_worktree, base_worktree, head_test_paths)
    return run_probe(
        base_worktree,
        probe_id="P2",
        interpreter=head_env.python_path,
        timeout=timeout,
        base_env=probe_env(head_env, base_worktree),
        launcher=launcher,
        env_incompatible_detection=True,
    )


def run_p3(
    head_worktree: Path,
    base_worktree: Path,
    base_test_paths: Sequence[str],
    base_env,
    *,
    timeout: float | None = None,
    launcher: Launcher | None = None,
) -> ProbeResult:
    """Run P3: HEAD production source + grafted BASE tests, under the BASE env.

    Symmetric to `run_p2`: grafts base test paths into the head worktree and
    runs pytest there using the base environment's interpreter, with PYTHONPATH
    pinned to the HEAD worktree so the on-disk head source wins (Req 4.8). A
    collection failure of the head source under the base env is ENV_INCOMPATIBLE
    (Req 4.7).
    """
    from .environment import probe_env

    graft_tests(base_worktree, head_worktree, base_test_paths)
    return run_probe(
        head_worktree,
        probe_id="P3",
        interpreter=base_env.python_path,
        timeout=timeout,
        base_env=probe_env(base_env, head_worktree),
        launcher=launcher,
        env_incompatible_detection=True,
    )

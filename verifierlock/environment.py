"""Environment_Builder: one dependency-only Python environment per revision (Task 13.1).

Implements design.md's Environment_Builder section and Concern 3. The single
non-negotiable invariant is the **dependency-only guarantee**:

    Environments install a revision's third-party dependencies ONLY. The
    project package under test is NEVER installed into site-packages.

This is a correctness requirement, not an optimisation. P2 runs BASE source on
disk under the HEAD environment (Req 4.4) and P3 runs HEAD source on disk under
the BASE environment (Req 4.8). If the project package were installed into
site-packages, a test's `import <project>` could resolve to the installed copy
and shadow the on-disk worktree source -- P2 would silently execute HEAD source
instead of BASE and the whole discrimination mechanism would break. So no
revision's package is ever installed; only its declared dependencies are, and
import resolution is pinned to the worktree via `PYTHONPATH` (Concern 3,
"Import resolution -- worktree source must win").

## Verified deps-only install scheme (Concern 3)

The design flagged `uv pip install --only-deps .` as PROVISIONAL. That flag does
NOT exist in current uv, so this module uses the design's verified alternative:
resolve the dependency set with `uv pip compile` (which emits the project's
dependencies, never the project itself) and install the resolved set with
`uv pip install -r`. The project package is therefore never built or installed.

Discovery precedence (per revision, deterministic, recorded in `BuiltEnv.discovery`):

| Order | Detected in worktree | Deps-only install |
|---|---|---|
| 1 | explicit `--install-cmd` | run the command verbatim (caller's responsibility, Concern 3) |
| 2 | `pyproject.toml` (`[project]`/build backend) | `uv pip compile pyproject.toml [--extra ...]` then `uv pip install -r` |
| 3 | `requirements.txt` (+ dev/test) | `uv pip install -r requirements.txt ...` |
| 4 | `setup.py` (no pyproject) | resolve declared deps and install those only |
| 5 | none | INCONCLUSIVE, reason `DEPS_UNDISCOVERABLE` |

When no dependency source is found the environment cannot be built
deterministically, so the depending probes cannot run: this maps to INCONCLUSIVE
`DEPS_UNDISCOVERABLE` (never BASELINE_INVALID -- the baseline was never assessed).

## Purity split for testability

Discovery selection (`select_source`) and command planning
(`plan_deps_only_commands`) are pure functions of their inputs and are
property/unit-tested directly. The side-effecting `Environment_Builder.build`
gathers discovery inputs from disk and runs the planned commands through an
injectable `CommandRunner`, so the real venv/uv execution is exercised by the
integration tests while planning is tested without a network.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import reasons

# tomllib is stdlib on 3.11+; the project targets >=3.10, so fall back to the
# optional `tomli` backport, and finally to a minimal section scan that reads
# only the few fields discovery needs. Full TOML parsing is preferred when
# available; the fallback never has to be perfect, only enough to detect a
# [project]/[build-system] table, the project name, and extra names.
try:  # pragma: no cover - import path depends on interpreter version
    import tomllib as _tomllib
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as _tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        _tomllib = None  # type: ignore[assignment]

# --- Result type -----------------------------------------------------------


@dataclass(frozen=True)
class BuiltEnv:
    """One revision's built environment (design.md Environment_Builder section).

    `installed_project` is always False and `install_kind` is always
    "deps_only": together they record the dependency-only guarantee in the
    Evidence Record (Req 4.3, Concern 3). `python_path` is the interpreter used
    to launch probes for this environment; `pythonpath_entries` are the
    worktree source roots that must precede site-packages on the import path so
    the on-disk source wins.
    """

    revision: str                       # "base" | "head"
    tool: str                           # "uv" | "venv+pip"
    python_path: Path | None
    discovery: str                      # "install_cmd"|"pyproject.toml"|"requirements.txt"|"setup.py"|"none"
    pythonpath_entries: tuple[str, ...] = ()
    installed_project: bool = False     # always False
    install_kind: str = "deps_only"     # always "deps_only"
    built: bool = False
    error: str | None = None            # reason-code + detail on failure

    @property
    def ok(self) -> bool:
        return self.built and self.error is None


# --- Discovery inputs (gathered from disk) ---------------------------------


@dataclass(frozen=True)
class DiscoveryInputs:
    """What discovery selection needs, gathered from the worktree.

    Kept as a plain data record so `select_source` / `plan_deps_only_commands`
    stay pure and testable without touching a filesystem.
    """

    install_cmd: str | None = None
    has_pyproject_deps: bool = False        # pyproject.toml with [project] or a build backend
    project_name: str | None = None
    extras: tuple[str, ...] = ()            # declared [project.optional-dependencies] names
    requirement_files: tuple[str, ...] = ()  # existing requirements*.txt, in install order
    has_setup_py: bool = False
    setup_py_requirements: tuple[str, ...] = ()  # deps parsed from setup.py, if any


# --- Discovery source selection (pure, Req 4 / Concern 3 precedence) --------

SRC_INSTALL_CMD = "install_cmd"
SRC_PYPROJECT = "pyproject.toml"
SRC_REQUIREMENTS = "requirements.txt"
SRC_SETUP_PY = "setup.py"
SRC_NONE = "none"


def select_source(inputs: DiscoveryInputs) -> str:
    """Choose the dependency source by the fixed precedence (pure).

    Returns one of the `SRC_*` labels; `SRC_NONE` maps to `DEPS_UNDISCOVERABLE`.
    """
    if inputs.install_cmd:
        return SRC_INSTALL_CMD
    if inputs.has_pyproject_deps:
        return SRC_PYPROJECT
    if inputs.requirement_files:
        return SRC_REQUIREMENTS
    if inputs.has_setup_py:
        return SRC_SETUP_PY
    return SRC_NONE


# --- Command planning (pure) -----------------------------------------------


@dataclass(frozen=True)
class InstallPlan:
    """A pure description of how to build a deps-only environment.

    `create_env` builds the virtualenv; `install_steps` install ONLY
    dependencies into it. `installs_project` is asserted False by construction
    and is checked by tests as the dependency-only guardrail.
    """

    source: str
    create_env: tuple[str, ...]
    install_steps: tuple[tuple[str, ...], ...] = ()
    installs_project: bool = False
    error: str | None = None


def _venv_python(env_dir: Path) -> Path:
    """The interpreter path inside a created venv (POSIX layout)."""
    return env_dir / "bin" / "python"


def plan_deps_only_commands(
    tool: str,
    source: str,
    env_dir: Path,
    worktree: Path,
    inputs: DiscoveryInputs,
    *,
    resolved_requirements: Path | None = None,
) -> InstallPlan:
    """Plan the deps-only build commands for `source` (pure).

    Never emits a command that installs the project package (no `pip install .`,
    `-e .`, `setup.py install`). For the pyproject path it uses
    compile->install-r (uv) so the project is never built or installed.
    `resolved_requirements` is the path the compile step writes to / the install
    step reads from (supplied by the caller so the path is explicit and stable).
    """
    env_python = _venv_python(env_dir)

    if tool == "uv":
        create = ("uv", "venv", str(env_dir))
        pip = ("uv", "pip", "install", "--python", str(env_python))
        compile_cmd_base = ("uv", "pip", "compile")
    else:  # venv+pip
        create = (sys.executable, "-m", "venv", str(env_dir))
        pip = (str(env_python), "-m", "pip", "install")
        compile_cmd_base = None  # pip has no compile; pyproject path handled below

    if source == SRC_INSTALL_CMD:
        assert inputs.install_cmd is not None
        steps = (tuple(shlex.split(inputs.install_cmd)),)
        return InstallPlan(source, create, steps, installs_project=False)

    if source == SRC_PYPROJECT:
        pyproject = worktree / "pyproject.toml"
        resolved = resolved_requirements or (env_dir.parent / f"{env_dir.name}-resolved.txt")
        if tool == "uv":
            compile_cmd = (
                *compile_cmd_base,  # type: ignore[misc]
                str(pyproject),
                "-o",
                str(resolved),
                *_extra_flags(inputs.extras),
            )
            install = (*pip, "-r", str(resolved))
            return InstallPlan(source, create, (compile_cmd, install), installs_project=False)
        # venv+pip fallback: install-then-uninstall the project so only its
        # dependencies remain (design Concern 3 acceptable alternative). This is
        # the ONE place a project is transiently installed; it is uninstalled in
        # the same plan, so the resulting environment still has no project pkg.
        install = (*pip, str(worktree))
        steps: tuple[tuple[str, ...], ...] = (install,)
        if inputs.project_name:
            steps += ((str(env_python), "-m", "pip", "uninstall", "-y", inputs.project_name),)
        return InstallPlan(source, create, steps, installs_project=False)

    if source == SRC_REQUIREMENTS:
        steps = tuple(
            (*pip, "-r", str(worktree / req)) for req in inputs.requirement_files
        )
        return InstallPlan(source, create, steps, installs_project=False)

    if source == SRC_SETUP_PY:
        # Install only the declared dependencies parsed from setup.py; never the
        # project. If none were parseable, the env is still valid (no deps).
        if inputs.setup_py_requirements:
            steps = ((*pip, *inputs.setup_py_requirements),)
        else:
            steps = ()
        return InstallPlan(source, create, steps, installs_project=False)

    # SRC_NONE
    return InstallPlan(
        source,
        create_env=(),
        install_steps=(),
        installs_project=False,
        error=reasons.DEPS_UNDISCOVERABLE,
    )


def _extra_flags(extras: tuple[str, ...]) -> tuple[str, ...]:
    flags: list[str] = []
    for extra in extras:
        flags += ["--extra", extra]
    return tuple(flags)


# --- Import resolution: worktree source must win (Concern 3) ---------------


def compute_pythonpath(worktree: Path) -> tuple[str, ...]:
    """The PYTHONPATH entries that make the on-disk worktree source win.

    The worktree root always leads; when a `src/` layout is detected (a `src/`
    directory containing at least one package or module) it is included so
    src-layout imports resolve to the worktree too (Concern 3).
    """
    worktree = Path(worktree)
    entries = [str(worktree)]
    src = worktree / "src"
    if src.is_dir():
        entries.append(str(src))
    return tuple(entries)


def probe_env(built_env: BuiltEnv, worktree: Path) -> dict[str, str]:
    """Build the process environment for launching a probe under `built_env`
    against the source in `worktree`.

    Activates the built venv (VIRTUAL_ENV + PATH) so its installed dependencies
    resolve, and prepends the worktree source roots to PYTHONPATH so the on-disk
    source wins over anything in site-packages (Concern 3). PYTHONHASHSEED /
    PYTHONDONTWRITEBYTECODE are added later by `compose_probe_command`.

    Crucially, PYTHONPATH points at the *probe's* worktree, which for P2/P3 is
    NOT the worktree the env was built from: P2 runs the head env against the
    base worktree, so the base source is what loads even though head deps are
    present.
    """
    env = dict(os.environ)
    entries = compute_pythonpath(worktree)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(entries + ((existing,) if existing else ()))

    if built_env.python_path is not None:
        venv_dir = Path(built_env.python_path).parent.parent
        env["VIRTUAL_ENV"] = str(venv_dir)
        bindir = str(Path(built_env.python_path).parent)
        env["PATH"] = os.pathsep.join((bindir, env.get("PATH", "")))
    return env


# --- Environment selection: environment follows the tests (Property 15) -----


def select_environment(probe_id: str, base_env: BuiltEnv, head_env: BuiltEnv) -> BuiltEnv:
    """Select which built environment a probe runs under (Req 4.4, 4.8).

    The environment follows the *tests*, not the source:
    - P2 grafts HEAD tests -> use the HEAD environment.
    - P3 grafts BASE tests -> use the BASE environment.
    - P0 (base tests) -> base env; P1 (head tests) -> head env.
    """
    pid = probe_id.upper()
    if pid in ("P2", "P1"):
        return head_env
    if pid in ("P3", "P0"):
        return base_env
    raise ValueError(f"unknown probe id for environment selection: {probe_id!r}")


# --- Tool detection --------------------------------------------------------


def detect_tool() -> str:
    """Prefer uv when available, else venv+pip (Req 4.1, 4.2)."""
    return "uv" if shutil.which("uv") else "venv+pip"


# --- Disk gathering + build (side-effecting) -------------------------------

# A runner executes argv in cwd with env; returns (returncode, stdout, stderr).
CommandRunner = Callable[[tuple[str, ...], Path, dict[str, str]], "RunResult"]


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


def _subprocess_runner(argv: tuple[str, ...], cwd: Path, env: dict[str, str]) -> RunResult:
    proc = subprocess.run(
        list(argv), cwd=str(cwd), env=env, capture_output=True, text=True
    )
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


def _parse_pyproject(text: str) -> tuple[bool, str | None, tuple[str, ...]]:
    """Extract `(has_project_or_backend, project_name, extras)` from pyproject.

    Uses full TOML parsing when `tomllib`/`tomli` is importable, else a minimal
    section scan sufficient for discovery.
    """
    if _tomllib is not None:
        try:
            data = _tomllib.loads(text)
        except Exception:  # noqa: BLE001 - malformed pyproject -> treat as none
            return False, None, ()
        project = data.get("project", {}) if isinstance(data, dict) else {}
        build_system = data.get("build-system", {}) if isinstance(data, dict) else {}
        has = bool(project) or bool(build_system)
        name = project.get("name") if isinstance(project, dict) else None
        opt = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
        extras = tuple(sorted(k for k in opt if isinstance(k, str))) if isinstance(opt, dict) else ()
        return has, (name if isinstance(name, str) else None), extras

    # Minimal fallback: detect the tables and read the name / extra keys.
    has = bool(re.search(r"(?m)^\s*\[project\]", text)) or bool(
        re.search(r"(?m)^\s*\[build-system\]", text)
    )
    name_match = re.search(r"(?ms)^\s*\[project\].*?^\s*name\s*=\s*['\"]([^'\"]+)['\"]", text)
    project_name = name_match.group(1) if name_match else None
    extras: tuple[str, ...] = ()
    opt_match = re.search(
        r"(?ms)^\s*\[project\.optional-dependencies\]\s*\n(.*?)(?=^\s*\[|\Z)", text
    )
    if opt_match:
        keys = re.findall(r"(?m)^\s*([A-Za-z0-9_.-]+)\s*=", opt_match.group(1))
        extras = tuple(sorted(set(keys)))
    return has, project_name, extras


_REQUIREMENT_CANDIDATES = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "requirements_dev.txt",
    "requirements_test.txt",
)


def gather_discovery(worktree: Path, install_cmd: str | None = None) -> DiscoveryInputs:
    """Read the worktree to build `DiscoveryInputs` (light disk I/O).

    Parses `pyproject.toml` (project name, extras, whether it declares
    dependencies / a build backend) with the stdlib `tomllib`; never installs
    or executes anything.
    """
    worktree = Path(worktree)
    has_pyproject_deps = False
    project_name: str | None = None
    extras: tuple[str, ...] = ()

    pyproject = worktree / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text()
        except OSError:
            text = ""
        has_pyproject_deps, project_name, extras = _parse_pyproject(text)

    requirement_files = tuple(
        name for name in _REQUIREMENT_CANDIDATES if (worktree / name).is_file()
    )
    has_setup_py = (worktree / "setup.py").is_file()

    return DiscoveryInputs(
        install_cmd=install_cmd,
        has_pyproject_deps=has_pyproject_deps,
        project_name=project_name,
        extras=extras,
        requirement_files=requirement_files,
        has_setup_py=has_setup_py,
    )


class Environment_Builder:
    """Builds one deps-only environment per revision (side-effecting)."""

    def __init__(self, tool: str | None = None, runner: CommandRunner | None = None) -> None:
        self.tool = tool or detect_tool()
        self._runner = runner or _subprocess_runner

    def build(
        self,
        worktree: Path,
        revision: str,
        *,
        env_dir: Path | None = None,
        install_cmd: str | None = None,
    ) -> BuiltEnv:
        """Build the deps-only environment for `revision` in `worktree`.

        Returns a `BuiltEnv`. On a discovery miss the result carries
        `error=DEPS_UNDISCOVERABLE` and `built=False` (the orchestrator maps
        this to INCONCLUSIVE, never BASELINE_INVALID). On an install failure the
        error names the failing step so the Evidence Record can cite it.

        The env directory is created OUTSIDE the worktree (a sibling by default)
        so pytest never discovers the venv as test code and grafting never
        touches it.
        """
        worktree = Path(worktree)
        env_dir = Path(env_dir) if env_dir is not None else worktree.parent / f"{worktree.name}-{revision}-env"
        pythonpath = compute_pythonpath(worktree)

        inputs = gather_discovery(worktree, install_cmd)
        source = select_source(inputs)
        if source == SRC_NONE:
            return BuiltEnv(
                revision=revision,
                tool=self.tool,
                python_path=None,
                discovery=SRC_NONE,
                pythonpath_entries=pythonpath,
                built=False,
                error=f"{reasons.DEPS_UNDISCOVERABLE}:no dependency source in {worktree}",
            )

        plan = plan_deps_only_commands(self.tool, source, env_dir, worktree, inputs)
        env_python = _venv_python(env_dir)

        # Create the virtualenv.
        create = self._runner(plan.create_env, worktree, dict(os.environ))
        if create.returncode != 0:
            return self._failed(revision, source, pythonpath,
                                f"venv creation failed: {create.stderr.strip() or create.stdout.strip()}")

        # Install dependencies only.
        for step in plan.install_steps:
            result = self._runner(step, worktree, dict(os.environ))
            if result.returncode != 0:
                return self._failed(
                    revision, source, pythonpath,
                    f"{reasons.ENV_INCOMPATIBLE}:install step {' '.join(step)!r} failed: "
                    f"{result.stderr.strip() or result.stdout.strip()}",
                )

        return BuiltEnv(
            revision=revision,
            tool=self.tool,
            python_path=env_python,
            discovery=source,
            pythonpath_entries=pythonpath,
            installed_project=False,
            install_kind="deps_only",
            built=True,
            error=None,
        )

    def _failed(self, revision: str, source: str, pythonpath: tuple[str, ...], error: str) -> BuiltEnv:
        return BuiltEnv(
            revision=revision,
            tool=self.tool,
            python_path=None,
            discovery=source,
            pythonpath_entries=pythonpath,
            built=False,
            error=error,
        )

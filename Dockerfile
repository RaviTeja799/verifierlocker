# Reproducible judging environment for VerifierLock (Task 20.2).
#
# Pins the three things a run depends on: Python, uv (the preferred
# dependency-only environment builder), and git (worktree isolation). Everything
# else VerifierLock needs it builds per revision, at run time, inside the
# container.
#
# Build:
#   docker build -t verifierlock .
#
# Run the bundled demo (VERIFIER_WEAKENED, exit code 12):
#   docker run --rm verifierlock
#
# Analyse your own repository (read-only mount is enough: VerifierLock only ever
# writes to its own temp worktrees and to paths you name):
#   docker run --rm -v "$PWD:/repo:ro" verifierlock \
#       verifierlock --repo /repo --base main --head HEAD
#
# Run the test suite inside the image:
#   docker run --rm verifierlock pytest -q
#
# Note: the container is a reproducibility aid, NOT a security sandbox. It runs
# the analysed repository's test code, so only point it at repositories you
# trust (DECISIONS.md §7).

FROM python:3.14.4-slim

# --- System dependencies --------------------------------------------------
# git is required (worktree isolation, rev-parse, diff). ca-certificates is
# needed so per-revision dependency installs can reach a package index. The
# exact base-image tag above pins the Debian release, and therefore pins the git
# version this image resolves to.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Pinned uv ------------------------------------------------------------
# Copied from the official uv image at an exact tag rather than curl|sh, so the
# build is reproducible and does not execute a remote script.
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependency metadata first, so the layer cache survives source edits.
COPY pyproject.toml README.md DECISIONS.md ./
COPY verifierlock ./verifierlock

RUN pip install --no-cache-dir ".[dev]"

# Fixtures and tests are part of the deliverable: judges run the demo and the
# suite from inside the image.
COPY fixtures ./fixtures
COPY tests ./tests

# git refuses to operate on a repository owned by another user; the fixture
# harness creates its own repos under /tmp, and a mounted /repo may be owned by
# the host user.
RUN git config --system --add safe.directory '*' \
    && git config --system user.email "judge@verifierlock.local" \
    && git config --system user.name "VerifierLock Judge"

# Default: run the flagship demo end to end and exit 12 (VERIFIER_WEAKENED).
CMD ["python", "-m", "verifierlock.demo"]

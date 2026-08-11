"""Reason-code string constants used across VerifierLock's stages.

These constants name the explicit reasons attached to INCONCLUSIVE,
BASELINE_INVALID, and skipped outcomes throughout the pipeline (Req 11.4).
They are collected here as a single source of truth so later stages
(revision resolution, repository validation, environment building,
probe running, exit-code interpretation, and the verdict engine) all
reference the same strings instead of re-typing literals.

Consumption of most of these codes happens in later tasks (notably
`interpret_exit_code` in Task 1.2, and the Verdict_Engine in Task 6).
Defining them now lets those modules import from a single place.
"""

from __future__ import annotations

# --- Revision resolution / repository validation (Req 1, 2) ---
BASELINE_REF_UNRESOLVED = "BASELINE_REF_UNRESOLVED"
HEAD_REF_UNRESOLVED = "HEAD_REF_UNRESOLVED"
NOT_A_GIT_REPO = "NOT_A_GIT_REPO"
HAS_SUBMODULES = "HAS_SUBMODULES"

# --- File classification (Req 4b) ---
UNCLASSIFIABLE_FILE = "UNCLASSIFIABLE_FILE"

# --- Environment building (Req 4, Concern 3) ---
DEPS_UNDISCOVERABLE = "DEPS_UNDISCOVERABLE"
ENV_INCOMPATIBLE = "ENV_INCOMPATIBLE"

# --- Worktree management (Req 3) ---
WORKTREE_CREATE_FAILED = "WORKTREE_CREATE_FAILED"

# --- Probe running (Req 6, 7b) ---
IMPORT_LIMITATION = "IMPORT_LIMITATION"
PROBE_TIMEOUT = "PROBE_TIMEOUT"

# --- Baseline validity (Req 8b, 8c) ---
BASELINE_NOT_GREEN = "BASELINE_NOT_GREEN"
BASELINE_NONDETERMINISTIC = "BASELINE_NONDETERMINISTIC"

# --- Coverage (Req 9, 10.12) ---
COVERAGE_UNAVAILABLE = "COVERAGE_UNAVAILABLE"

# --- Verdict engine (Req 10.3) ---
HEAD_NOT_GREEN = "HEAD_NOT_GREEN"

# --- Exit-code interpretation (Req 8.5, 8.6, 8.3) ---
# Reason codes consumed by interpret_exit_code (Task 1.2): used to cite a
# specific pytest internal/usage error, zero tests collected, and the
# max-warnings limit, respectively.
PROBE_INTERNAL_ERROR = "PROBE_INTERNAL_ERROR"  # pytest exit 3 (internal error) or 4 (usage error)
NO_TESTS_COLLECTED = "NO_TESTS_COLLECTED"      # pytest exit 5
MAX_WARNINGS_LIMIT = "MAX_WARNINGS_LIMIT"      # pytest exit 6

# --- Anticipated additional exit-code reason codes (Task 1.2) ---
# These anticipate interpret_exit_code's needs for citing an unrecognised
# exit code, an import-limitation-flavoured collection failure, a timeout,
# or the exit-code-2 abort signal.
UNRECOGNISED_EXIT_CODE = "UNRECOGNISED_EXIT_CODE"
ABORT_SIGNAL = "ABORT_SIGNAL"  # pytest exit 2

# --- Verdict-level aggregated reasons (Req 10.6, 10.7) ---
P2_PASS_P3_FAIL = "P2_PASS_P3_FAIL"
P2_FAIL_P3_FAIL = "P2_FAIL_P3_FAIL"
P2_FAIL_P3_PASS = "P2_FAIL_P3_PASS"

# --- Verdict engine structural / matrix reason codes (Req 10, Task 6) ---
# Consumed by `Verdict_Engine.decide` (Task 6.1) as the `reason_code` for the
# rows that do not already have a dedicated reason above.
#
# Structural rows 3 and 4:
NO_TEST_OR_VERIFIER_CHANGE = "NO_TEST_OR_VERIFIER_CHANGE"  # row 3 -> NO_VERIFIER_CHANGE
NO_PRODUCTION_CHANGE = "NO_PRODUCTION_CHANGE"              # row 4 -> VERIFIER_CHANGED_REVIEW_REQUIRED
# Aggregated required-probe INCONCLUSIVE (row 5, Req 10.6):
REQUIRED_PROBE_INCONCLUSIVE = "REQUIRED_PROBE_INCONCLUSIVE"
# Coverage matrix outcomes (rows 9, 10, Req 10.10, 10.11):
ALL_CHANGED_LINES_COVERED = "ALL_CHANGED_LINES_COVERED"   # row 9 -> INDEPENDENT_EVIDENCE
CHANGED_LINES_UNCOVERED = "CHANGED_LINES_UNCOVERED"       # row 10 -> NO_INDEPENDENT_EVIDENCE
# Baseline could not be *assessed* (an INCONCLUSIVE P0 repetition, e.g. a
# timeout or DEPS_UNDISCOVERABLE base env): the run is INCONCLUSIVE, NOT
# BASELINE_INVALID, because the baseline was never validly assessed (design
# Error Handling "assess vs unstable" distinction / decision log 4.7).
BASELINE_NOT_ASSESSED = "BASELINE_NOT_ASSESSED"

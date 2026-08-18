# VerifierLock invariants (always included)

These are the project's non-negotiables. They are the same claims the
requirements make and the property tests enforce; they are restated here so any
agent working in this repository is bound by them before it writes a line.

## 1. No language model in the verdict path

Revision resolution, repository validation, file classification, static
analysis, worktree management, environment construction, probe execution,
exit-code interpretation, coverage mapping, verdict rules, and evidence capture
contain **no model call**. Identical inputs produce an identical verdict
(Req 10.16, 13.1). The only outbound traffic a run may generate is the
per-revision dependency install performed by `uv`/`pip`; no repository content
and no secret is ever transmitted (Req 14.3).

The optional Explanation_Model may narrate a *finished* Evidence Record. It gets
a deep copy, returns prose, and has no return path into the verdict, the exit
code, or the reproducible core (Req 13.2). If a change would give any model,
heuristic, or network response influence over a verdict, do not make the change.

## 2. Only pytest exit code 0 means passed

Exit codes 3, 4, 5, and 6 are INCONCLUSIVE, never passed; 5 (zero tests
collected) is the one that silently corrupts everything downstream if treated as
success. Exit code 2 aborts the run with no verdict and its own distinct exit
code (Req 8, 15.9).

## 3. The environment follows the tests, and never installs the project

Per-revision environments install **dependencies only** — never the project
package (`installed_project=false`, `install_kind="deps_only"`). PYTHONPATH
points at the probe's own worktree so on-disk source wins over site-packages.
P2 runs the head environment, P3 the base environment (Req 4).

## 4. Every probe goes through the shared command composition

Determinism controls (`--import-mode=importlib`, `-p no:cacheprovider`,
`-p no:randomly`, `-o addopts=`, fixed `--rootdir`, `PYTHONHASHSEED=0`,
`PYTHONDONTWRITEBYTECODE=1`) and the per-probe timeout are applied in one
function so they cannot drift between probes. Bytecode is purged before and
after every probe, including on timeout and abort (Req 6.2, 7).

## 5. Worktrees are always cleaned up

Every created worktree is removed on any termination (normal exit, exception,
signal), then metadata is pruned. A leaked worktree holding a ref breaks the
next run (Req 3).

## 6. Every run produces an auditable Evidence Record

No silent failure: every INCONCLUSIVE, skipped probe, and BASELINE_INVALID
carries an explicit reason code. Arrays are deterministically ordered, and the
reproducible core is byte-identical for identical inputs (Req 11).

## 7. Exit codes are documented and distinct

The verdict-to-exit-code mapping (0, 10-16) is the contract. A gating policy
(`--strict` / `--exit-policy`) may change the process exit *status*, but never
the documented code recorded in the Evidence Record.

## 8. Style

Python, standard library first. Frozen dataclasses for data crossing stage
boundaries. Side effects behind injectable seams so pure logic stays testable.
Property tests use Hypothesis with at least 100 examples. Comments explain *why*,
not *what*.

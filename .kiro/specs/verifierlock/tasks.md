# Implementation Plan: VerifierLock

## Overview

This plan builds VerifierLock incrementally in Python. It starts from the most
load-bearing pure function (pytest exit-code interpretation), then stands up a
runnable walking skeleton that proves the real worktree lifecycle and evidence
emission end-to-end against disk. From there it builds the deterministic pure
components (file classification, coverage mapping, verdict engine) with their
property tests, then the side-effecting components (worktree manager, probe
runner with determinism controls, environment builder) that consume them, and
finally wires the CLI, report, and bundled fixture demo together.

Each task builds on the previous ones and ends in something runnable and
demoable. No task leaves orphaned code: the probe runner accepts an injected
interpreter path so it is runnable before the full Environment_Builder exists,
and the Environment_Builder is then wired into every probe by the orchestrator.

Every probe command is composed through one shared function so determinism
controls (Req 7) and the per-probe timeout (Req 7b) cannot drift between probes.

Language: Python. Property-based tests use Hypothesis (min 100 examples each).
Test sub-tasks are marked with `*` and may be skipped for a faster MVP; core
implementation sub-tasks are never optional.

## Tasks

- [~] 1. Project scaffold and exact pytest exit-code interpretation
  - [x] 1.1 Scaffold the package, shared types, and test framework
    - Create the `verifierlock` package layout, `pyproject.toml`, and a
      `tests/` tree; add Hypothesis and pytest as dev dependencies.
    - Define shared frozen types/enums used across stages: `ProbeOutcome`
      (`ALL_PASSED`, `TESTS_FAILED`, `INCONCLUSIVE`) and reason-code constants.
    - _Requirements: 11.1_
  - [x] 1.2 Implement the pure `interpret_exit_code` function FIRST
    - Map pytest exit codes to outcomes: 0 -> ALL_PASSED (the only pass);
      1 -> TESTS_FAILED; 3, 4 -> INCONCLUSIVE (cite code); 5 -> INCONCLUSIVE
      (zero collected); 6 -> INCONCLUSIVE (max-warnings); 2 -> abort signal;
      any other integer -> INCONCLUSIVE.
    - Return exactly one `(ProbeOutcome, reason)` per code; no engine, worktree,
      or orchestration code yet.
    - _Requirements: 8.1, 8.2, 8.3, 8.5, 8.6, 8.7_
  - [x] 1.3 Write property test for exit-code interpretation (tests/test_exit_code_properties.py)
    - **Property 1: Exit-code interpretation is exact and total**
    - Assert pytest exit codes 3, 4, 5, and 6 NEVER classify as passed, and
      that ONLY exit code 0 classifies as all-passed.
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.5, 8.6, 8.7**

- [x] 2. Walking skeleton: real worktrees, one probe, one evidence artifact
  - [x] 2.1 Implement the Worktree_Manager path scheme and create two worktrees
    - Implement the per-run path scheme
      `<system-temp>/verifierlock/<run-id>/worktrees/<slot>/` and create two
      detached worktrees (base, head) via `git worktree add --detach` (never
      `--force`).
    - _Requirements: 3.1, 3.4_
  - [x] 2.2 Run ONE real pytest probe in one worktree and capture the result
    - Launch a single real pytest process in one worktree; capture the exact
      command and the process exit code, and classify it via
      `interpret_exit_code` from Task 1.
    - No four-probe matrix, no verdict engine, no coverage.
    - _Requirements: 6.4_
  - [x] 2.3 Emit ONE Evidence Record JSON artifact to disk
    - Assemble a minimal Evidence Record (base/head commits, the single probe's
      command, exit code, and outcome) and write it as JSON to disk so the
      skeleton runs end-to-end and produces a real artifact.
    - _Requirements: 11.1, 11.2_

- [x] 3. Checkpoint - Ensure the walking skeleton runs and emits an artifact
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement the File_Classifier (pure)
  - [x] 4.1 Implement `classify` over a diff and pytest configuration
    - Classify every changed file as production / test / verifier configuration,
      using declared `testpaths` when present, else pytest default discovery
      patterns and `conftest.py`; classify CI-workflow, coverage, pytest, lint,
      and type-check config as verifier configuration; collect unclassifiable
      paths.
    - _Requirements: 4b.1, 4b.2, 4b.3, 4b.4, 4b.6_
  - [x] 4.2 Write property test for classification partition
    - **Property 6: File classification is a total partition**
    - **Validates: Requirements 4b.1, 4b.6**
  - [x] 4.3 Write property test for test/verifier-config classification rules
    - **Property 7: Test and verifier-config classification rules**
    - **Validates: Requirements 4b.2, 4b.3, 4b.4**

- [x] 5. Implement the Coverage_Analyzer mapping (pure)
  - [x] 5.1 Implement `map_coverage` over Cobertura XML and changed head lines
    - Parse covered lines from P1 Cobertura XML and intersect with changed
      head-revision production lines; mark each changed line covered/uncovered
      and record uncovered locations; set `available=False` when undeterminable.
    - _Requirements: 9.2, 9.3, 9.4_
  - [x] 5.2 Write property test for coverage mapping
    - **Property 8: Coverage mapping equals the intersection with changed lines**
    - **Validates: Requirements 9.2, 9.3**

- [ ] 6. Implement the Verdict_Engine (pure) - must pass before any CLI wiring
  - [ ] 6.1 Implement `decide` with the full first-match rule ordering
    - Implement the total, pure rule ordering (rows 0a-11): pre-probe
      short-circuits, BASELINE_INVALID on P0 not-green or nondeterministic,
      INCONCLUSIVE on P1 not-green, structural NO_VERIFIER_CHANGE /
      VERIFIER_CHANGED_REVIEW_REQUIRED, aggregated required-probe INCONCLUSIVE,
      then the P2xP3xcoverage matrix; also implement the probe-selection helper.
    - _Requirements: 1.3, 1.4, 2.2, 2.3, 4b.5, 8b.1, 8b.2, 8c.2, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11, 10.12, 10.13, 10.14, 10.15, 10.16, 13.1_
  - [ ]* 6.2 Write property test for single deterministic verdict
    - **Property 2: Exactly one verdict, deterministically**
    - **Validates: Requirements 10.1, 10.16**
  - [ ]* 6.3 Write property test for rule ordering
    - **Property 3: Verdict rule ordering matches the specification**
    - **Validates: Requirements 1.3, 1.4, 2.2, 2.3, 4b.5, 8b.1, 8b.2, 8c.2, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11, 10.12**
  - [ ]* 6.4 Write property test for VERIFIER_WEAKENED precondition
    - **Property 4: VERIFIER_WEAKENED requires P2 all-passed**
    - **Validates: Requirements 10.13, 5.6**
  - [ ]* 6.5 Write property test for probe selection
    - **Property 5: Probe selection follows classification**
    - **Validates: Requirements 6.1, 10.14, 10.15**
  - [ ]* 6.6 Write property test for baseline nondeterminism
    - **Property 19: Baseline nondeterminism is detected from repeated P0**
    - **Validates: Requirements 8c.1, 8c.2**

- [ ] 7. Checkpoint - Ensure all pure-engine property tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Complete the Worktree_Manager lifecycle
  - [ ] 8.1 Implement create / remove_all / prune_stale with crash-safe cleanup
    - Implement per-slot unique paths (P0 repetitions, P1, P2, P3), start-of-run
      stale-metadata pruning, and removal of all created worktrees on any
      termination via context manager / `finally` / signal handlers, then
      `git worktree prune`.
    - _Requirements: 3.1, 3.2, 3.3, 3.5, 3.6, 8c.3_
  - [ ]* 8.2 Write property test for worktree path uniqueness
    - **Property 12: Worktree paths are pairwise unique per run**
    - **Validates: Requirements 3.1, 8c.3**
  - [ ]* 8.3 Write property test for cleanup on termination
    - **Property 13: All created worktrees are removed on any termination**
    - **Validates: Requirements 3.2, 3.5**
  - [ ]* 8.4 Write property test for worktree-creation failure handling
    - **Property 14: Worktree-creation failure makes dependent probes INCONCLUSIVE**
    - **Validates: Requirements 3.3**

- [ ] 9. Implement Revision_Resolver and Repository_Validator
  - [ ] 9.1 Implement `resolve` for base/head references
    - `git rev-parse` each ref to a full commit hash; record hashes and per-ref
      resolution errors used by verdict rows 0a/0b.
    - _Requirements: 1.1, 1.2, 1.5_
  - [ ]* 9.2 Write unit tests for revision resolution
    - Branch, tag, short-hash, and invalid refs.
    - _Requirements: 1.2, 1.5_
  - [ ] 9.3 Implement `validate` for the repository
    - Confirm the path is a Git repo and reject submodules before any probe;
      record the determination.
    - _Requirements: 2.1, 2.2, 2.3, 2.4_
  - [ ]* 9.4 Write unit tests for repository validation
    - Valid repo, non-repo, and submodule fixture.
    - _Requirements: 2.1, 2.4_

- [ ] 10. Implement the Probe_Runner core (P0/P1) with determinism controls
  - [ ] 10.1 Implement the shared command-composition function
    - Compose every probe command through one function applying
      `--import-mode=importlib`, `-p no:cacheprovider`, neutralised addopts
      (`-o addopts=""`), fixed `--rootdir`, and env `PYTHONHASHSEED=0` +
      `PYTHONDONTWRITEBYTECODE=1`; accept an injected interpreter path so probes
      are runnable before Environment_Builder exists.
    - _Requirements: 6.2, 7.1, 7.2, 7.3, 7.4, 7.5_
  - [ ] 10.2 Implement probe execution, exit-code integration, timeout, and purge
    - Launch a probe, purge bytecode/pytest-cache before and after (including on
      termination), enforce the configurable per-probe timeout by killing the
      process tree and reporting INCONCLUSIVE with elapsed duration, classify via
      `interpret_exit_code`, detect the inter-test-module import limitation, and
      raise the abort signal on exit code 2.
    - _Requirements: 6.3, 6.4, 7.6, 7.7, 7b.1, 7b.2, 7b.3, 8.4_
  - [ ] 10.3 Implement P0 (>=2 fresh worktrees) and the P1 verdict probe
    - Run P0 at least twice in freshly created worktrees for baseline validity
      and nondeterminism detection, and run the un-instrumented P1 verdict probe.
    - _Requirements: 6.1, 8b.1, 8c.1, 8c.3_
  - [ ]* 10.4 Write property test for determinism controls
    - **Property 10: Every probe command carries the determinism controls**
    - **Validates: Requirements 6.2, 7.1, 7.2, 7.3, 7.4, 7.5**
  - [ ]* 10.5 Write property test for bytecode purge
    - **Property 11: Bytecode is purged around every probe**
    - **Validates: Requirements 7.6, 7.7**
  - [ ]* 10.6 Write property test for timeout handling
    - **Property 17: Timeout produces INCONCLUSIVE with recorded duration**
    - **Validates: Requirements 7b.1, 7b.2, 7b.3**
  - [ ]* 10.7 Write property test for the inter-test import limitation
    - **Property 18: Inter-test import limitation is INCONCLUSIVE**
    - **Validates: Requirements 6.3**

- [ ] 11. Implement the Static_Analyzer
  - [ ] 11.1 Implement diff detectors and node-ID set comparison
    - Detect deleted/weakened assertions, new skip/xfail/deselect, new coverage
      exclusions, lowered `fail_under`, disabled lint/type checking,
      `continue-on-error` CI weakening, fixtures removing a failing condition,
      and changed success-reporting commands; compute reduced test selection as
      the base-minus-head node-ID difference; record each finding with file and
      hunk. Findings inform probe selection only and never produce a verdict.
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_
  - [ ]* 11.2 Write property test for reduced test selection
    - **Property 21: Reduced test selection equals the node-ID set difference**
    - **Validates: Requirements 5.3**
  - [ ]* 11.3 Write unit tests for static-analysis pattern detectors
    - Canonical weakening snippets produce the expected finding kinds.
    - _Requirements: 5.1, 5.2, 5.4, 5.5_

- [ ] 12. Checkpoint - Ensure worktree, probe runner, and analyzer tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. Environment_Builder + first P2/P3 implementation + shadowing guardrail
  - [ ] 13.1 Implement the dependency-only Environment_Builder and import resolution
    - Build each revision's environment preferring `uv` else `venv`+`pip`,
      installing third-party dependencies ONLY and NEVER the project package;
      set `PYTHONPATH` to the worktree root (and `src/` for src-layout) so the
      worktree source wins over site-packages; record tool, discovery source,
      `installed_project=false`, and `install_kind="deps_only"`; map
      DEPS_UNDISCOVERABLE and ENV_INCOMPATIBLE to INCONCLUSIVE.
    - NOTE: The deps-only command for the `pyproject.toml` path
      (`uv pip install --only-deps .`) is PROVISIONAL and MUST be verified
      against uv during implementation. Acceptable alternatives: use
      `uv pip compile` to resolve the dependency set then install the resolved
      set, or install-then-uninstall the project package so only its
      dependencies remain. The `requirements.txt` and `--install-cmd` paths are
      already clean and need no such verification. The design intent is fixed:
      install dependencies only, never the project package.
    - _Requirements: 4.1, 4.2, 4.3, 4.5, 4.7_
  - [ ] 13.2 Implement P2/P3 graft composition and environment selection
    - Compose P2 (copy head test paths into the base worktree) and P3 (copy base
      test paths into the head worktree) with delete-before-copy of destination
      test paths; never modify production source; never copy verifier
      configuration; select the head environment for P2 and the base environment
      for P3 (the environment follows the tests).
    - _Requirements: 4.4, 4.6, 4.8, 6.5, 6.6, 6.7_
  - [ ]* 13.3 Write property test for graft invariants
    - **Property 9: Graft preserves source, grafts the right tests, and never copies verifier config**
    - **Validates: Requirements 6.5, 6.6, 6.7, 4.4, 4.8**
  - [ ]* 13.4 Write property test for environment selection
    - **Property 15: Environment follows the tests, not the source**
    - **Validates: Requirements 4.4, 4.8**
  - [ ]* 13.5 Write property test for import-failure vs test-failure classification
    - **Property 16: Import failure is INCONCLUSIVE, test failure is tests-failed**
    - **Validates: Requirements 4.5, 4.6, 4.7**
  - [ ]* 13.6 Write the P2-runs-base-source guardrail integration test
    - Assert that during P2 the base-only sentinel is observed, proving P2 ran
      the on-disk BASE worktree source and not an installed or head copy.
    - _Requirements: 4.4_
  - [ ]* 13.7 Write the P3-runs-head-source guardrail integration test
    - Assert that during P3 the head-only sentinel is observed, proving P3 ran
      the on-disk HEAD worktree source.
    - _Requirements: 4.8_
  - [ ]* 13.8 Write unit test for the dependency-only guarantee
    - Assert a built environment installs declared dependencies but does NOT
      install the project package into site-packages.
    - _Requirements: 4.1, 4.2, 4.3_

- [ ] 14. Implement the P1 coverage run (separate instrumented fifth run)
  - [ ] 14.1 Implement the coverage.py-driven P1 coverage run
    - Run the identical head-source + head-test composition in the `p1` worktree
      under `coverage run -m pytest` (not `--cov`) with the shared determinism
      controls, derive the measured `--source` package set from classified
      production files, emit Cobertura XML, and feed only that XML into
      `map_coverage`; the coverage run never feeds the verdict.
    - _Requirements: 9.1, 9.4_
  - [ ]* 14.2 Write integration test for coverage emission and mapping
    - Coverage run emits parseable Cobertura XML that maps onto changed head
      lines; unavailable coverage yields COVERAGE_UNAVAILABLE.
    - _Requirements: 9.1, 9.2_

- [ ] 15. Wire the Orchestrator and assemble the full Evidence Record
  - [ ] 15.1 Implement the Orchestrator pipeline
    - Drive the fixed pipeline with pre-probe short-circuits (Req 1.3/1.4/2.2/
      2.3/4b.5), probe selection from classification, P0/P1 then structurally
      required P2/P3, environment wiring into every probe, and guaranteed
      worktree cleanup.
    - _Requirements: 10.14, 10.15_
  - [ ] 15.2 Implement the full Evidence_Recorder with reproducible-core normalisation
    - Assemble the complete Evidence Record with deterministically ordered
      arrays, explicit reasons for every INCONCLUSIVE/skip/BASELINE_INVALID, and
      command-path normalisation to `<RUN_ROOT>`/`<WORKTREE>` so the reproducible
      core is byte-identical for identical inputs.
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_
  - [ ]* 15.3 Write property test for the Evidence Record
    - **Property 22: Evidence Record is complete and reproducible**
    - **Validates: Requirements 11.2, 11.4, 11.5**

- [ ] 16. Checkpoint - Ensure the full pipeline runs against a real repo
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 17. Build the bundled labelled fixture repository
  - [ ] 17.1 Create the fixture with a real authorization defect and a NON-DISCRIMINATING weakened test
    - Create the fixture repo under `fixtures/`, labelled as a test fixture, with
      a real authorization defect; the weakened test MUST pass against BOTH
      base-revision and head-revision production source so P2 is classified
      all-passed and the demo yields VERIFIER_WEAKENED.
    - _Requirements: 16.1, 16.2, 16.6_
  - [ ] 17.2 Create the remaining labelled fixture scenarios
    - Add: weakened test replaced with an independent regression test
      (INDEPENDENT_EVIDENCE); a legitimate behaviour change with P2 and P3 both
      failing (VERIFIER_CHANGED_REVIEW_REQUIRED); and a wholesale deletion of a
      discriminating test (VERIFIER_WEAKENED).
    - _Requirements: 16.3, 16.4, 16.5_
  - [ ]* 17.3 Write integration tests for the fixture scenarios
    - Assert each scenario produces its expected verdict end-to-end.
    - _Requirements: 16.2, 16.3, 16.4, 16.5, 16.6_

- [ ] 18. Wire the CLI, Report_Generator, and exit codes (last)
  - [ ] 18.1 Implement the Report_Generator
    - Render a local human-readable report with the verdict, per-probe outcomes,
      static findings, and changed-line coverage; write a local artifact and
      contact no remote system.
    - _Requirements: 12.1, 12.2, 14.3_
  - [ ] 18.2 Implement the CLI with warning, arguments, and exit-code policy
    - Parse `--repo/--base/--head` plus `--timeout`, `--json`, `--report`,
      `--install-cmd`, and `--strict`; print the untrusted-code warning before
      any probe; map the verdict to the documented distinct exit codes including
      the aborted-no-verdict code; apply the `--strict`/default build-blocking
      policy without changing the documented mapping.
    - _Requirements: 14.1, 14.2, 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9_
  - [ ] 18.3 Implement the optional Explanation_Model
    - Behind `--explain`, read the finished Evidence Record and emit prose only,
      with no write path into the verdict; produce the verdict and record even
      when no key/network is available.
    - _Requirements: 13.1, 13.2, 13.3_
  - [ ]* 18.4 Write property test for report contents
    - **Property 23: Report contains the required elements**
    - **Validates: Requirements 12.1**
  - [ ]* 18.5 Write property test for the exit-code mapping
    - **Property 24: Verdict-to-exit-code mapping is injective and documented**
    - **Validates: Requirements 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9**
  - [ ]* 18.6 Write property test for the pytest exit-code-2 abort path
    - **Property 20: pytest exit code 2 aborts with no verdict**
    - **Validates: Requirements 8.4, 15.9**
  - [ ]* 18.7 Write the end-to-end fixture demo integration test
    - Run the CLI against the non-discriminating weakened-test fixture and assert
      it produces VERIFIER_WEAKENED with the documented exit code.
    - _Requirements: 16.2, 16.6_
  - [ ]* 18.8 Write the LLM-isolation test for the optional Explanation_Model
    - Assert the verdict and the reproducible core are byte-identical with
      `--explain` on and off; mutating or enabling the explanation output can
      never change the verdict or the reproducible core. This validates the
      design's LLM-isolation guarantee.
    - _Requirements: 13.1, 13.2_

- [ ] 19. Final checkpoint - Ensure all tests pass and the fixture demo works
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 20. Submission packaging
  - [ ] 20.1 Write the README
    - Cover the problem statement, quickstart from a cold clone, usage, the
      verdict-to-exit-code table, how Kiro was used (specs / steering / hooks /
      skills), costs and credit consumption (record actual credits used - 120 to
      date), third-party attribution, and testing instructions.
    - Include the Known Limitations section INCLUDING the additive-change
      INCONCLUSIVE limitation, reusing the DECISIONS.md section 8 text verbatim
      as README section 20.1.
    - _Requirements: 12.1, 14.2_
  - [ ] 20.2 Add a Dockerfile for a reproducible judging environment
    - Pin Python 3.14 + uv + git so judges get a reproducible environment;
      document `docker run` usage in the README.
    - _Requirements: 4.1, 14.2_
  - [ ] 20.3 Add a LICENSE and third-party attribution list
    - Add a permissive LICENSE (e.g. MIT/Apache-2.0) and a third-party
      attribution list covering Hypothesis, coverage.py, uv, and pytest.
    - _Requirements: 14.2_
  - [ ] 20.4 Finalize the committed .kiro/ directory and DECISIONS.md
    - Verify the `.kiro/` directory is committed and NOT gitignored, and
      finalize DECISIONS.md.
    - _Requirements: 14.2_
  - [ ]* 20.5 Add a GitHub Action running the plain CLI on pull requests
    - Run the plain CLI on a pull request (no Kiro / API key required),
      honouring the `--strict` build-blocking policy.
    - _Requirements: 15.1, 15.5_
  - [ ] 20.6 Record the 3-minute demo video
    - Show the problem statement, a live VERIFIER_WEAKENED run on the bundled
      fixture with the Evidence Record JSON visible on screen, Kiro usage shown
      in the IDE, and a brief close.
    - _Requirements: 16.2, 16.6_

## Notes

- Tasks marked with `*` are optional (tests) and can be skipped for a faster MVP;
  core implementation tasks are never optional.
- Each task references the specific requirements and design properties it
  implements for traceability.
- Task 1 implements the pure exit-code mapping first; Task 2 is a runnable
  walking skeleton (real worktrees, one probe, one on-disk Evidence Record).
- Pure/deterministic components (Tasks 1, 4, 5, 6) and their property tests come
  before or alongside the side-effecting components (Tasks 8, 10, 13) that
  consume them; the Verdict_Engine (Task 6) is complete before CLI wiring.
- Task 13 is the first task to implement P2/P3 and, per the design's Concern 3,
  bundles the dependency-only Environment_Builder build with the two
  source-shadowing guardrail integration tests in the same task.
- Determinism controls (Req 7, 7b) are integrated into the probe runner as it is
  built (Task 10); P0 baseline validity/nondeterminism is covered across
  Tasks 6 and 10.
- The CLI, report, and fixture demo are wired last (Tasks 17-18).
- Task group 20 packages the hackathon submission artifacts (README, Dockerfile,
  LICENSE, attribution, CI, demo video) and is separate from the tool
  implementation; these are documentation/packaging tasks, not tool code.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "4.1", "5.1", "6.1", "8.1", "9.1", "9.3", "11.1"] },
    { "id": 2, "tasks": ["1.3", "4.2", "4.3", "5.2", "6.2", "6.3", "6.4", "6.5", "6.6", "8.2", "8.3", "8.4", "9.2", "9.4", "11.2", "11.3", "2.1"] },
    { "id": 3, "tasks": ["2.2", "10.1"] },
    { "id": 4, "tasks": ["2.3", "10.2"] },
    { "id": 5, "tasks": ["10.3", "13.1"] },
    { "id": 6, "tasks": ["10.4", "10.5", "10.6", "10.7", "13.2"] },
    { "id": 7, "tasks": ["13.3", "13.4", "13.5", "13.6", "13.7", "13.8", "14.1"] },
    { "id": 8, "tasks": ["14.2", "15.1"] },
    { "id": 9, "tasks": ["15.2"] },
    { "id": 10, "tasks": ["15.3", "17.1"] },
    { "id": 11, "tasks": ["17.2", "18.1", "18.2", "18.3"] },
    { "id": 12, "tasks": ["17.3", "18.4", "18.5", "18.6", "18.7", "18.8"] },
    { "id": 13, "tasks": ["20.1", "20.3", "20.4"] },
    { "id": 14, "tasks": ["20.2", "20.5"] },
    { "id": 15, "tasks": ["20.6"] }
  ]
}
```

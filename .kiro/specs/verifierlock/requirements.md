# Requirements Document

## Introduction

VerifierLock detects self-validating changes in Python/pytest repositories — changes that make continuous integration (CI) pass by weakening, replacing, or disabling the very tests, coverage configuration, or CI workflow relied upon as proof of correctness. Given a base and a head Git revision, VerifierLock determines whether changed production behaviour has independent test evidence, or whether the change instead weakened its own verifier.

VerifierLock inverts the source of a mutation-testing mutant: the base revision is treated as the reference implementation, and the diff between base and head is treated as the mutant. It separates mechanism from interpretation. A deterministic engine performs revision parsing, worktree management, environment construction, probe execution, exit-code interpretation, coverage comparison, verdict rules, and evidence capture. Identical inputs produce identical verdicts. An optional language model may explain a produced report in prose but never creates, changes, suppresses, or overrides a verdict.

Scope for v1 is a local command-line tool operating over trusted local repositories plus a bundled fixture repository.

## Glossary

- **VerifierLock**: The complete system described by this document that detects self-validating changes.
- **CLI**: The command-line interface through which a caller invokes VerifierLock.
- **Revision_Resolver**: The component that resolves user-supplied Git references to commit hashes.
- **Repository_Validator**: The component that verifies a target path is a supported Git repository.
- **Worktree_Manager**: The component that creates and removes isolated Git worktrees per revision.
- **Environment_Builder**: The component that constructs a Python environment per revision.
- **File_Classifier**: The component that classifies each changed file as production source, test code, or verifier configuration.
- **Static_Analyzer**: The component that inspects the diff without executing repository code.
- **Probe_Runner**: The component that composes and executes the four-probe matrix.
- **Exit_Code_Interpreter**: The component that maps pytest process exit codes to probe outcomes.
- **Coverage_Analyzer**: The component that maps collected coverage to changed production lines.
- **Verdict_Engine**: The deterministic component that produces the run verdict.
- **Evidence_Recorder**: The component that captures the machine-readable Evidence Record.
- **Report_Generator**: The component that produces the human-readable local report.
- **Explanation_Model**: The optional language model that describes an already-produced Evidence Record in prose.
- **Probe**: One execution of a test suite over a defined combination of production source and test code.
  - **P0**: Base production source with base test code (the baseline).
  - **P1**: Head production source with head test code (the head-as-submitted suite).
  - **P2**: Base production source with head test code.
  - **P3**: Head production source with base test code.
- **Verdict**: One of seven mutually exclusive values produced per run: INDEPENDENT_EVIDENCE, NO_INDEPENDENT_EVIDENCE, NO_VERIFIER_CHANGE, VERIFIER_WEAKENED, VERIFIER_CHANGED_REVIEW_REQUIRED, INCONCLUSIVE, BASELINE_INVALID.
- **Changed production line**: A line of production source added or modified between the base and head revisions.
- **Evidence Record**: The machine-readable JSON artifact capturing all inputs, probe outcomes, findings, and the verdict of a run.
- **Required probe**: A probe the verdict rules depend on for this run. P0 and P1 are always required. P2 and P3 are required only when at least one changed file is production source AND at least one changed file is test code or verifier configuration.
- **Fixture Repository**: A bundled, labelled repository containing a known defect used for demonstration and testing.

## Requirements

### Requirement 1: Accept and resolve base and head revisions

**User Story:** As a developer, I want to supply a repository and two revisions, so that VerifierLock can compare a baseline against a proposed change.

#### Acceptance Criteria

1. WHEN the CLI is invoked, THE CLI SHALL accept a repository path, a base reference, and a head reference.
2. WHEN a base reference and a head reference are supplied, THE Revision_Resolver SHALL resolve each reference to a commit hash.
3. IF the base reference cannot be resolved to a commit hash, THEN THE Verdict_Engine SHALL produce BASELINE_INVALID and THE Probe_Runner SHALL NOT execute any probe.
4. IF the head reference cannot be resolved to a commit hash, THEN THE Verdict_Engine SHALL produce INCONCLUSIVE and THE Probe_Runner SHALL NOT execute any probe.
5. WHEN both references are resolved, THE Evidence_Recorder SHALL record the base commit hash and the head commit hash.

### Requirement 2: Validate that the repository is supported

**User Story:** As a developer, I want VerifierLock to confirm the repository is supported before running, so that unsupported repositories fail clearly rather than midway.

#### Acceptance Criteria

1. WHEN a repository path is supplied, THE Repository_Validator SHALL verify that the path is a Git repository before any probe executes.
2. IF the path is not a Git repository, THEN THE Verdict_Engine SHALL produce INCONCLUSIVE and THE Probe_Runner SHALL NOT execute any probe.
3. IF the repository contains submodules, THEN THE Verdict_Engine SHALL produce INCONCLUSIVE and THE Probe_Runner SHALL NOT execute any probe.
4. WHEN validation completes, THE Evidence_Recorder SHALL record the repository path and the validation determination.

### Requirement 3: Isolate each revision in a dedicated worktree

**User Story:** As a developer, I want each revision checked out in isolation, so that probes cannot contaminate one another or the caller's working tree.

#### Acceptance Criteria

1. WHEN a probe requires a revision, THE Worktree_Manager SHALL create a detached Git worktree for that revision's commit.
2. WHEN a run completes, THE Worktree_Manager SHALL remove all created worktrees and prune worktree metadata.
3. IF worktree creation fails for a revision, THEN THE Probe_Runner SHALL report INCONCLUSIVE for each probe that depends on that worktree.
4. WHEN a worktree is created, THE Evidence_Recorder SHALL record the worktree path for each probe.
5. WHEN a run terminates for any reason, including abort or unhandled failure, THE Worktree_Manager SHALL remove all worktrees it created.
6. WHEN a run begins, THE Worktree_Manager SHALL prune stale worktree metadata.

### Requirement 4: Construct one Python environment per revision

**User Story:** As a developer, I want each revision's tests to run against dependencies that match the test code being executed, so that probe results reflect real behaviour rather than dependency mismatches.

#### Acceptance Criteria

1. WHERE the uv tool is available, THE Environment_Builder SHALL construct each revision's Python environment using uv.
2. WHERE the uv tool is not available, THE Environment_Builder SHALL construct each revision's Python environment using venv with pip.
3. WHEN an environment is constructed for a revision, THE Evidence_Recorder SHALL record which tool constructed that environment.
4. WHEN probe P2 executes, THE Environment_Builder SHALL use the head-revision environment so that head test code can resolve its dependencies.
5. IF base-revision production source cannot be imported or collected under the head-revision environment, THEN THE Probe_Runner SHALL report INCONCLUSIVE for probe P2 with reason code ENV_INCOMPATIBLE, citing the unresolvable import.
6. WHERE base-revision source imports and collects successfully under the head-revision environment, THE Probe_Runner SHALL classify the P2 outcome solely per Requirement 8; a test failure SHALL be classified as tests failed, not INCONCLUSIVE.
7. THE Probe_Runner SHALL apply the symmetric rule to P3 under the base environment: IF head-revision production source cannot be imported or collected under the base-revision environment, THEN THE Probe_Runner SHALL report INCONCLUSIVE for probe P3 with reason code ENV_INCOMPATIBLE; WHERE it imports and collects successfully, THE Probe_Runner SHALL classify the P3 outcome solely per Requirement 8.
8. WHEN probe P3 executes, THE Environment_Builder SHALL use the base-revision environment so that base test code can resolve its dependencies.

### Requirement 4b: Classify changed files

**User Story:** As a developer, I want each changed file classified, so that VerifierLock can distinguish production changes from changes to the verifier itself.

#### Acceptance Criteria

1. WHEN the diff between base and head is available, THE File_Classifier SHALL classify every changed file as production source, test code, or verifier configuration.
2. WHERE a pytest testpaths configuration is declared, THE File_Classifier SHALL classify test code using the declared testpaths.
3. WHERE a pytest testpaths configuration is not declared, THE File_Classifier SHALL classify test code using pytest default discovery patterns and conftest.py files.
4. THE File_Classifier SHALL classify CI workflow files, coverage configuration, pytest configuration, lint configuration, and type-check configuration as verifier configuration.
5. IF a changed file cannot be classified, THEN THE Verdict_Engine SHALL produce INCONCLUSIVE citing the unclassifiable path.
6. WHEN classification completes, THE Evidence_Recorder SHALL record the classification of every changed file.

### Requirement 5: Perform a static pre-pass without executing repository code

**User Story:** As a reviewer, I want VerifierLock to surface suspicious diff patterns before executing anything, so that probe selection is informed and findings are traceable.

#### Acceptance Criteria

1. WHEN a diff is available, THE Static_Analyzer SHALL inspect the diff for deleted or weakened assertions.
2. WHEN a diff is available, THE Static_Analyzer SHALL inspect the diff for newly skipped, xfailed, or deselected tests.
3. WHEN comparing base and head, THE Static_Analyzer SHALL detect reduced test selection by comparing the base collected node-ID set against the head collected node-ID set.
4. WHEN a diff is available, THE Static_Analyzer SHALL detect new coverage exclusions, lowered fail_under thresholds, disabled lint checking, disabled type checking, continue-on-error CI weakening, changed fixtures that remove a failing condition, and changed commands responsible for reporting success.
5. WHEN a static finding is detected, THE Evidence_Recorder SHALL record the finding with its file and hunk location.
6. THE Verdict_Engine SHALL treat static findings as inputs that raise suspicion and select probes, AND SHALL NOT produce VERIFIER_WEAKENED from static findings alone.

### Requirement 6: Run the four-probe matrix

**User Story:** As a developer, I want VerifierLock to run the base and head suites in the four diagnostic combinations, so that self-validating changes become observable.

#### Acceptance Criteria

1. THE Probe_Runner SHALL execute the required probes for the run.
2. THE Probe_Runner SHALL pass --import-mode=importlib to every probe.
3. IF a probe cannot import a test module because that module depends on another test module or on a test-utility module inside the tests tree, THEN THE Probe_Runner SHALL report INCONCLUSIVE for that probe citing the import limitation.
4. WHEN a probe executes, THE Evidence_Recorder SHALL record for that probe the exact command, the collected count, the passed count, the failed count, and the skipped count.
5. WHEN composing a probe, THE Probe_Runner SHALL copy the head-revision test code paths into the base-revision worktree without modifying any base-revision production source file, AND SHALL NOT copy verifier configuration between revisions.
6. WHEN composing probe P3, THE Probe_Runner SHALL copy the base-revision test
   code paths into the head-revision worktree without modifying any
   head-revision production source file.
7. THE Probe_Runner SHALL NOT copy verifier configuration between revisions.


### Requirement 7: Apply determinism controls to every probe

**User Story:** As a developer, I want each probe run under controlled conditions, so that results are reproducible and free of ordering or caching effects.

#### Acceptance Criteria

1. WHEN a probe executes, THE Probe_Runner SHALL disable the pytest cache provider.
2. WHEN a probe executes, THE Probe_Runner SHALL disable random test ordering.
3. WHEN a probe executes, THE Probe_Runner SHALL neutralise repository addopts.
4. WHEN a probe executes, THE Probe_Runner SHALL set a fixed PYTHONHASHSEED and disable bytecode writing.
5. WHEN a probe executes, THE Probe_Runner SHALL use a fixed rootdir.
6. BEFORE each probe begins, THE Probe_Runner SHALL purge cached bytecode directories.
7. AFTER each probe completes or is terminated, THE Probe_Runner SHALL purge cached bytecode directories.

### Requirement 7b: Bound probe execution time

**User Story:** As a developer, I want each probe to have a time limit, so that a hanging or runaway suite cannot stall the run.

#### Acceptance Criteria

1. THE Probe_Runner SHALL enforce a configurable per-probe timeout.
2. IF a probe exceeds its timeout, THEN THE Probe_Runner SHALL terminate the probe process tree AND report INCONCLUSIVE citing the timeout and the elapsed duration.
3. THE Evidence_Recorder SHALL record the elapsed duration of every probe.

### Requirement 8: Interpret pytest exit codes precisely

**User Story:** As a developer, I want pytest exit codes mapped to precise outcomes, so that "passed" means exactly what it should.

#### Acceptance Criteria

1. WHEN a probe returns exit code 0, THE Exit_Code_Interpreter SHALL classify the probe as all tests passed.
2. WHEN a probe returns exit code 1, THE Exit_Code_Interpreter SHALL classify the probe as tests failed.
3. WHEN a probe returns exit code 5, THE Exit_Code_Interpreter SHALL classify the probe as INCONCLUSIVE because zero tests were collected.
4. WHEN a probe returns exit code 2, THE CLI SHALL abort the entire run immediately.
5. WHEN a probe returns exit code 3 or exit code 4, THE Exit_Code_Interpreter SHALL classify the probe as INCONCLUSIVE citing the returned code.
6. WHEN a probe returns exit code 6, THE Exit_Code_Interpreter SHALL classify the probe as INCONCLUSIVE citing the max-warnings limit.
7. THE Exit_Code_Interpreter SHALL classify a probe as passed only when the probe returns exit code 0.

### Requirement 8b: Require a clean baseline before any verdict

**User Story:** As a developer, I want a trustworthy baseline enforced, so that no verdict is drawn from an unstable reference.

#### Acceptance Criteria

1. IF probe P0 is not classified as all tests passed, THEN THE Verdict_Engine SHALL produce BASELINE_INVALID.
2. WHILE the run is BASELINE_INVALID, THE Verdict_Engine SHALL NOT produce any other verdict for the run.

### Requirement 8c: Detect a nondeterministic baseline

**User Story:** As a developer, I want a flaky baseline detected, so that VerifierLock does not build verdicts on nondeterministic evidence.

#### Acceptance Criteria

1. THE Probe_Runner SHALL execute probe P0 at least twice with identical inputs.
2. IF the repeated probe P0 executions do not produce identical outcomes, THEN THE Verdict_Engine SHALL produce BASELINE_INVALID with a reason stating that the baseline suite is nondeterministic.
3. EACH P0 repetition SHALL run in a freshly created worktree so that side effects from one execution cannot contaminate another.

### Requirement 9: Measure changed-line coverage

**User Story:** As a reviewer, I want to know which changed production lines are exercised by tests, so that I can judge whether the change is independently verified.

#### Acceptance Criteria

1. WHEN probe P1 executes, THE Coverage_Analyzer SHALL collect coverage as Cobertura XML from probe P1 only.
2. THE Coverage_Analyzer SHALL map probe P1 coverage onto head-revision line numbers to determine which changed production lines are exercised.
3. WHEN a changed production line is not exercised, THE Evidence_Recorder SHALL record that line with its location.
4. THE Coverage_Analyzer SHALL perform the coverage mapping inside the deterministic engine with no external tool in the verdict path.

### Requirement 10: Produce a deterministic verdict

**User Story:** As a developer, I want exactly one verdict per run drawn from an exhaustive rule set, so that results are unambiguous and reproducible.

#### Acceptance Criteria

1. THE Verdict_Engine SHALL produce exactly one verdict per run.
2. IF probe P0 is not classified as all tests passed OR probe P0 is nondeterministic, THEN THE Verdict_Engine SHALL produce BASELINE_INVALID.
3. IF no preceding condition holds AND probe P1 is not classified as all tests passed, THEN THE Verdict_Engine SHALL produce INCONCLUSIVE with the reason "head revision is not green".
4. IF no preceding condition holds AND no changed file is classified as test code or verifier configuration, THEN THE Verdict_Engine SHALL produce NO_VERIFIER_CHANGE.
5. IF no preceding condition holds AND no changed file is classified as production source, THEN THE Verdict_Engine SHALL produce VERIFIER_CHANGED_REVIEW_REQUIRED.
6. IF no preceding condition holds AND any required probe is INCONCLUSIVE, THEN THE Verdict_Engine SHALL produce INCONCLUSIVE with the aggregated reasons.
7. IF no preceding condition holds AND probe P2 is classified as all tests passed AND probe P3 is classified as tests failed, THEN THE Verdict_Engine SHALL produce VERIFIER_WEAKENED.
8. IF no preceding condition holds AND probe P2 is classified as tests failed AND probe P3 is classified as tests failed, THEN THE Verdict_Engine SHALL produce VERIFIER_CHANGED_REVIEW_REQUIRED.
9. IF no preceding condition holds AND probe P2 is classified as tests failed AND probe P3 is classified as all tests passed, THEN THE Verdict_Engine SHALL produce INDEPENDENT_EVIDENCE.
10. IF no preceding condition holds AND probe P2 is classified as all tests passed AND probe P3 is classified as all tests passed AND every changed production line is covered, THEN THE Verdict_Engine SHALL produce INDEPENDENT_EVIDENCE.
11. IF no preceding condition holds AND probe P2 is classified as all tests passed AND probe P3 is classified as all tests passed AND one or more changed production lines are uncovered, THEN THE Verdict_Engine SHALL produce NO_INDEPENDENT_EVIDENCE.
12. IF no preceding condition holds AND probe P2 is classified as all tests
    passed AND probe P3 is classified as all tests passed AND changed-line
    coverage could not be determined, THEN THE Verdict_Engine SHALL produce
    INCONCLUSIVE with reason code COVERAGE_UNAVAILABLE.
13. THE Verdict_Engine SHALL produce VERIFIER_WEAKENED only when probe P2 is classified as all tests passed.
14. WHEN no changed file is classified as test code or verifier configuration, THE Probe_Runner SHALL NOT execute probe P2 or probe P3.
15. WHEN no changed file is classified as production source, THE Probe_Runner SHALL NOT execute probe P2 or probe P3.
16. WHEN the same inputs are supplied across separate runs, THE Verdict_Engine SHALL produce the identical verdict.

### Requirement 11: Emit a machine-readable Evidence Record

**User Story:** As a developer, I want a complete machine-readable record, so that verdicts are auditable and reproducible.

#### Acceptance Criteria

1. THE Evidence_Recorder SHALL emit machine-readable JSON containing the base commit hash, the head commit hash, the changed files, and their hunk locations.
2. THE Evidence_Recorder SHALL record for each executed probe the exact command, the exit code, the collected count, the passed count, the failed count, and the skipped count.
3. THE Evidence_Recorder SHALL record the static findings with their locations, the changed-line coverage result, the tool version, and the run timestamp.
4. THE Evidence_Recorder SHALL record an explicit reason for any skipped probe, any INCONCLUSIVE outcome, and any BASELINE_INVALID outcome.
5. WHEN the same inputs are supplied across separate runs, THE Evidence_Recorder SHALL produce identical verdict and probe-outcome fields.

### Requirement 12: Produce a human-readable local report

**User Story:** As a reviewer, I want a readable summary, so that I can understand the outcome without parsing JSON.

#### Acceptance Criteria

1. THE Report_Generator SHALL present the verdict, the per-probe outcomes, the static findings, and the changed-line coverage.
2. THE Report_Generator SHALL write the report as a local artifact and SHALL NOT contact any remote system.

### Requirement 13: Keep language models out of the deterministic path

**User Story:** As a developer, I want verdicts produced without any language model, so that results are deterministic and trustworthy.

#### Acceptance Criteria

1. THE Verdict_Engine SHALL produce every verdict without any language model.
2. WHERE the Explanation_Model is enabled, THE Explanation_Model SHALL only describe an already-produced Evidence Record AND SHALL NOT create, change, suppress, or override the verdict.
3. WHERE no language-model key is configured and no network is available, THE VerifierLock SHALL still produce a verdict and an Evidence Record.

### Requirement 14: Warn before executing untrusted code

**User Story:** As a user, I want to be warned that running repository tests executes repository code, so that I understand the risk before proceeding.

#### Acceptance Criteria

1. WHEN a run begins, THE CLI SHALL display a warning that running repository tests executes repository code with the caller's privileges.
2. THE VerifierLock SHALL restrict v1 operation to local repositories and the bundled fixture repository.
3. THE VerifierLock SHALL NOT transmit repository contents or secrets to any remote system.

### Requirement 15: Document and set CLI exit codes

**User Story:** As a user, I want the process exit code to reflect the verdict, so that VerifierLock composes with scripts and automation.

#### Acceptance Criteria

1. WHEN a run completes, THE CLI SHALL set the process exit code according to a documented verdict-to-exit-code mapping.
2. THE CLI SHALL set a distinct documented exit code for the INDEPENDENT_EVIDENCE verdict.
3. THE CLI SHALL set a distinct documented exit code for the NO_INDEPENDENT_EVIDENCE verdict.
4. THE CLI SHALL set a distinct documented exit code for the NO_VERIFIER_CHANGE verdict.
5. THE CLI SHALL set a distinct documented exit code for the VERIFIER_WEAKENED verdict.
6. THE CLI SHALL set a distinct documented exit code for the VERIFIER_CHANGED_REVIEW_REQUIRED verdict.
7. THE CLI SHALL set a distinct documented exit code for the INCONCLUSIVE verdict.
8. THE CLI SHALL set a distinct documented exit code for the BASELINE_INVALID verdict.
9. WHEN a run is aborted per Requirement 8 criterion 4 and no verdict is produced, THE CLI SHALL set a distinct documented exit code for "aborted, no verdict."

### Requirement 16: Provide a bundled labelled fixture with a real defect

**User Story:** As an evaluator, I want a labelled fixture with a known defect, so that VerifierLock's verdicts can be demonstrated and tested end to end.

#### Acceptance Criteria

1. THE VerifierLock SHALL include a Fixture Repository under fixtures/ that contains a real authorization defect and is labelled as a test fixture.
2. WHEN VerifierLock analyses the fixture's self-validating test-weakening change, THE Verdict_Engine SHALL produce VERIFIER_WEAKENED.
3. WHEN the weakened test is replaced with an independent regression test, THE Verdict_Engine SHALL produce INDEPENDENT_EVIDENCE.
4. WHEN VerifierLock analyses a legitimate behaviour-change revision in which probe P2 and probe P3 both fail, THE Verdict_Engine SHALL produce VERIFIER_CHANGED_REVIEW_REQUIRED.
5. WHEN a change deletes a discriminating test wholesale so that probe P2 passes because the test is gone and probe P3 fails because the base still contains the test, THE Verdict_Engine SHALL produce VERIFIER_WEAKENED.
6. THE Fixture Repository's weakened test SHALL be non-discriminating: it SHALL
   pass against both base-revision and head-revision production source, so that
   probe P2 is classified as all tests passed.

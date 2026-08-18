# VerifierLock

**Green CI is not proof. VerifierLock produces proof.**

VerifierLock answers one question about a Git change:

> Does this change have independent test evidence, or did it weaken its own verifier?

It is a local CLI for Python/pytest repositories. It emits a deterministic
verdict, a machine-readable Evidence Record, a human-readable report, and a
documented process exit code. No language model participates in the verdict.

---

## The problem

An AI coding agent asked to "make the failing test pass" has two paths:

| Path | Result | Correct? |
|---|---|---|
| Fix the production code | CI green | Yes |
| Weaken the test until it passes | CI green | No |

Both produce an identical green check. The reviewer sees a passing build, the
weakened test ships, and from that point the repository has a test that proves
nothing. Coverage does not catch it: the line is still executed, just no longer
*checked*. A diff review often does not catch it either, because the test change
looks like a small, plausible cleanup.

## The mechanism

VerifierLock inverts mutation testing. It invents no mutants. It treats the
**base revision as the reference implementation** and asks whether the head test
suite can still tell base behaviour apart from head behaviour.

**The diff is the mutant.**

It runs four probes in isolated `git worktree` checkouts, each with its own
dependency-only environment:

| Probe | Composition | Question answered |
|---|---|---|
| P0 (x2) | base source + base tests | Is the baseline trustworthy and reproducible? |
| P1 | head source + head tests | Is the submitted change green? |
| P2 | base source + **head** tests | Do the new tests still discriminate? |
| P3 | head source + **base** tests | How does the old verifier react? |

P2 is load-bearing. If the new tests pass against the *old* code, they cannot
tell the revisions apart, so they prove nothing about the change. Pair that with
a P3 failure — the old tests reject the new behaviour — and you have the
signature of a weakened verifier:

```
P2 passed + P3 failed  ->  VERIFIER_WEAKENED
```

A fifth, separately instrumented P1 run measures changed-line coverage under
`coverage run -m pytest`, so coverage instrumentation can never perturb a
verdict.

---

## Quickstart from a cold clone

Requirements: Python 3.10+ (verified on 3.10 and 3.14), `git`, and optionally
[`uv`](https://docs.astral.sh/uv/) (used when present; falls back to
`venv` + `pip`).

```bash
git clone <this-repo> verifierlock && cd verifierlock
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# Run the flagship demo: a real authorization defect shipped with a gutted test.
python -m verifierlock.demo; echo "exit code: $?"
```

Expected: a report ending in `VERIFIER_WEAKENED` and **exit code 12**. The
verdict is reached in about 15 seconds on a warm cache.

The other bundled scenarios:

```bash
python -m verifierlock.demo --list
python -m verifierlock.demo independent_evidence   # exit 0
python -m verifierlock.demo review_required        # exit 13
python -m verifierlock.demo deleted_test           # exit 12
```

Then point it at a real change:

```bash
verifierlock --repo /path/to/repo --base main --head HEAD \
    --json evidence.json --report report.txt
```

> **Warning:** VerifierLock runs the analysed repository's test suite, which
> executes that repository's code with your privileges — from *both* revisions.
> The CLI prints this warning before every run. Only point it at repositories you
> trust. See [Security posture](#security-posture).

## Usage

```
verifierlock --repo PATH --base REF --head REF [options]
```

| Option | Meaning |
|---|---|
| `--repo PATH` | local Git repository to analyse (default `.`) |
| `--base REF` | base revision: branch, tag, or commit (required) |
| `--head REF` | head revision (default `HEAD`) |
| `--timeout SECONDS` | per-probe timeout; a timed-out probe is INCONCLUSIVE with its elapsed duration (default 600) |
| `--json PATH` | write the machine-readable Evidence Record |
| `--report PATH` | write the human-readable report |
| `--install-cmd CMD` | override dependency discovery; must install dependencies only, never the project package |
| `--exit-policy {documented,lenient,strict}` | which outcomes fail the build (default `documented`) |
| `--strict` | shorthand for `--exit-policy strict` |
| `--explain` / `--no-explain` | append prose narration of the finished record (default off) |
| `--quiet` | do not print the report to stdout; artifacts are still written |
| `--p0-repetitions N` | baseline repetitions used to detect a nondeterministic baseline (minimum 2) |

`python -m verifierlock` and the installed `verifierlock` script are equivalent.

## Verdicts and exit codes

Every run produces exactly one verdict, and every verdict has its own distinct,
documented exit code.

| Verdict | Exit code | Meaning |
|---|---|---|
| `INDEPENDENT_EVIDENCE` | 0 | the head tests still discriminate, or every changed line is covered |
| `NO_INDEPENDENT_EVIDENCE` | 10 | tests pass both ways and some changed production line is uncovered |
| `NO_VERIFIER_CHANGE` | 11 | no test code or verifier configuration changed |
| `VERIFIER_WEAKENED` | 12 | **head tests accept old behaviour while base tests reject new behaviour** |
| `VERIFIER_CHANGED_REVIEW_REQUIRED` | 13 | both grafted probes disagree with their host revision, or only the verifier changed |
| `INCONCLUSIVE` | 14 | the run could not be decided; the reason code says why |
| `BASELINE_INVALID` | 15 | the base revision is not reproducibly green |
| *aborted, no verdict* | 16 | a probe returned pytest exit code 2 (interrupt / internal abort) |
| *usage error* | 2 | bad arguments, or `--repo` is not a local directory |

### CI gating policy

The raw mapping is deliberately noisy for a build gate: `NO_VERIFIER_CHANGE` and
`NO_INDEPENDENT_EVIDENCE` are non-zero and would fail builds on legitimate
changes. So the *process exit status* can be softened without touching the
mapping:

| `--exit-policy` | process exit status |
|---|---|
| `documented` (default) | the documented code for the outcome |
| `lenient` | non-zero only for `VERIFIER_WEAKENED` |
| `strict` (= `--strict`) | non-zero for `VERIFIER_WEAKENED` and `VERIFIER_CHANGED_REVIEW_REQUIRED` |

A blocking outcome always keeps its own documented code (12 / 13); other
outcomes report 0. Both the documented code and the policy-applied status are
recorded in the Evidence Record's `exit_status` block, so the JSON consumer sees
the true verdict code regardless of gating.

The bundled GitHub Action (`.github/workflows/verifierlock.yml`) runs the plain
CLI on pull requests under `--strict`, with no Kiro and no API key involved.

## Output

Three artifacts, all local:

1. **The report** (stdout, or `--report PATH`) — verdict, meaning, per-probe
   outcomes with their compositions and exact commands, static findings, and
   changed-line coverage.
2. **The Evidence Record** (`--json PATH`) — the authoritative artifact: commits,
   changed files with classifications and hunks, per-probe command / exit code /
   collected / passed / failed / skipped / elapsed, environments (recording
   `installed_project: false`), static findings, coverage, and the verdict with
   its reason code and matched rule. Every INCONCLUSIVE, skipped probe, and
   BASELINE_INVALID carries an explicit reason: nothing fails silently.
3. **The reproducible core** — the subset of the record that is a pure function
   of `(repo state, base commit, head commit)`, with per-run temp paths
   normalised to `<RUN_ROOT>` / `<WORKTREE>`. It is byte-identical across runs of
   the same inputs, which is what makes the verdict auditable rather than
   anecdotal.

Excerpt from the flagship demo's report:

```
Verdict: VERIFIER_WEAKENED
  reason:       P2_PASS_P3_FAIL
  matched rule: 6
  exit code:    12 (documented verdict-to-exit-code mapping)

Probes (6)
  P0#0   verdict  all_passed     exit=0   collected=2  passed=2  failed=0  ...
  P1#0   verdict  all_passed     exit=0   collected=2  passed=2  failed=0  ...
  P2#0   verdict  all_passed     exit=0   collected=2  passed=2  failed=0  ...
      base source + head tests (grafted)
  P3#0   verdict  tests_failed   exit=1   collected=2  passed=1  failed=1  ...
      head source + base tests (grafted)
```

## Running with Docker

The image pins Python 3.14, uv 0.10.9, and git for a reproducible judging
environment:

```bash
docker build -t verifierlock .

# The flagship demo (exits 12):
docker run --rm verifierlock

# Your own repository, mounted read-only:
docker run --rm -v "$PWD:/repo:ro" verifierlock \
    verifierlock --repo /repo --base main --head HEAD

# The test suite inside the image:
docker run --rm verifierlock pytest -q
```

The container is a reproducibility aid, **not** a security sandbox: it still runs
the analysed repository's test code.

## Testing

```bash
pip install -e ".[dev]"
python -m pytest -q                       # full suite: 189 tests
python -m pytest -q -k properties         # the property-based suites
python -m pytest -q tests/test_fixtures.py    # all four fixture scenarios, end to end
python -m pytest -q tests/test_cli_e2e.py     # the CLI demo: VERIFIER_WEAKENED, exit 12
```

The suite is 189 tests and covers the design's **24 properties** with Hypothesis
(minimum 100 examples each). The properties are the interesting part; they encode
the claims the tool makes about itself:

- exit-code interpretation is exact and total, and only 0 means passed;
- exactly one verdict per run, deterministically, with the specified rule
  ordering, and `VERIFIER_WEAKENED` unreachable unless P2 passed;
- worktree paths are pairwise unique and all worktrees are removed on any
  termination;
- every probe command carries every determinism control, and bytecode is purged
  around every probe;
- grafting preserves production source and never copies verifier configuration;
  the environment follows the tests;
- import failure is INCONCLUSIVE while test failure is tests-failed;
- the Evidence Record is complete, and its reproducible core is byte-identical
  for identical inputs;
- the verdict-to-exit-code mapping is injective, and pytest exit code 2 aborts
  with no verdict;
- the verdict and the reproducible core are byte-identical with `--explain` on
  and off — including against a narrator that actively tries to rewrite the
  verdict.

Integration tests that need to build a real per-revision environment skip (rather
than pass) when no environment can be built in the sandbox; a built environment
plus a wrong verdict always fails.

## How it works

```
CLI -> Orchestrator
        Repository_Validator   is this a supported repo?          (short-circuit)
        Revision_Resolver      resolve base / head                (short-circuit)
        File_Classifier        production / test / verifier config (short-circuit)
        Static_Analyzer        advisory weakening findings
        Worktree_Manager       one detached worktree per probe slot
        Environment_Builder    deps-only env per revision
        Probe_Runner           P0 x2, P1, [P2, P3], + instrumented P1
        Coverage_Analyzer      Cobertura XML -> changed head lines
        Verdict_Engine         one pure, total, first-match rule table
        Evidence_Recorder      deterministic, auditable JSON
        Report_Generator       human-readable local report
        Explanation_Model      optional prose, read-only, outside the engine
```

Load-bearing choices, all documented with rationale in
[`DECISIONS.md`](DECISIONS.md):

- **`git worktree` isolation** per probe, with crash-safe cleanup and start-of-run
  pruning.
- **Dependency-only environments.** The project package is never installed;
  `PYTHONPATH` points at the probe's worktree so the on-disk source wins over
  site-packages. Without this, P2 would silently test an installed copy instead
  of the base source — the two source-shadowing guardrail tests exist to prove it
  does not.
- **The environment follows the tests, not the source:** P2 uses the head
  environment, P3 the base environment.
- **`--import-mode=importlib` on every probe**, because grafting breaks under
  pytest's default `prepend` mode.
- **Only exit code 0 is passed.** Exit code 5 (zero tests collected) reads as
  success to any "not 1" check and would corrupt every downstream verdict.
- **A nondeterministic baseline invalidates the run.** P0 runs at least twice in
  fresh worktrees; if the suite is flaky, a flaky P3 failure would fabricate a
  false accusation about someone else's repository.

## Known limitations

Disclosed rather than hidden. Verbatim from [`DECISIONS.md`](DECISIONS.md) §8:

> ### Known limitation: additive changes resolve to INCONCLUSIVE
>
> VerifierLock is strongest for changes that *modify existing behaviour*. Purely
> additive changes — a new function plus a new test for it — cannot be probed by
> P2. The new test imports a symbol that does not exist in base source, so P2
> fails at collection and the run resolves to INCONCLUSIVE (ENV_INCOMPATIBLE /
> IMPORT_LIMITATION) rather than INDEPENDENT_EVIDENCE.
>
> This is inherent to the inversion method: the base revision is the reference
> implementation, and a test for code that does not yet exist in base has nothing
> to run against. It is disclosed rather than hidden. The tool's detection power
> is concentrated where it matters most — on changes to behaviour that already
> had, or should have, test coverage.

Also true of v1:

- **Python and pytest only.** Other languages and runners are out of scope.
- **Repositories with submodules are unsupported** (Git's own submodule support
  across multiple worktrees is incomplete) and return INCONCLUSIVE.
- **Inter-test imports.** Under `--import-mode=importlib`, a test module that
  imports a sibling test module or a test-utility module inside the tests tree
  cannot import; such repositories return INCONCLUSIVE (`IMPORT_LIMITATION`)
  rather than being silently mishandled. `conftest.py` fixtures are unaffected.
- **A flaky suite is not analysable.** That is a deliberate refusal, not a gap.
- **Static findings are advisory.** They inform probe selection and annotate the
  record; they never produce a verdict.
- **No sandboxing claim.** The tool runs untrusted test code in your own
  privilege domain.
- Further deferred questions are listed in `DECISIONS.md` §8.

## Security posture

- Running a repository's tests executes that repository's code with the caller's
  privileges. The CLI warns before every run.
- v1 supports **trusted local repositories** and the bundled fixture only;
  remote-looking `--repo` values are refused.
- **No repository contents and no secrets are transmitted anywhere** (Req 14.3).
  There is no telemetry, no upload, and no model call: the engine, the reporter,
  and the optional explanation path (v1 ships an offline narrator) are entirely
  local. The one piece of outbound traffic in a run is the per-revision
  dependency install, performed by `uv` or `pip` against your configured package
  index — the same traffic `pip install -r requirements.txt` would generate. Use
  `--install-cmd` to point that at a private mirror, or pre-populate a cache, if
  even that is unwanted.
- The bundled fixtures under `fixtures/` contain **deliberate** defects and are
  labelled as test fixtures.

## How Kiro was used

| Kiro feature | Artifact | Role in the build |
|---|---|---|
| **Spec** | `.kiro/specs/verifierlock/requirements.md` | 20 requirement groups in EARS form (16 numbered plus 4b, 7b, 8b, 8c), written first, then analysed for cross-requirement contradictions before any design |
| **Spec** | `.kiro/specs/verifierlock/design.md` | components and interfaces, the verdict rule table, the four design concerns, and the 24 properties each test validates |
| **Spec** | `.kiro/specs/verifierlock/tasks.md` | 20 task groups executed one at a time, each citing the requirements it implements; the whole implementation was driven task by task |
| **Steering** | `.kiro/steering/determinism.md` | always-included invariants: no model in the verdict path, only exit code 0 passes, deps-only environments, shared command composition, guaranteed cleanup, distinct exit codes |
| **Hook** | `.kiro/hooks/verify-after-spec-task.json` | runs `pytest -q` on `PostTaskExec`, so a spec task cannot be closed on a red suite |
| **Skills** | — | not used |

The `.kiro/` directory is committed (it is deliberately **not** gitignored), so
the specs, the steering rule, and the hook are all inspectable. The specs are
what drove the implementation; the steering rule and the hook were added while
packaging the submission to codify invariants the requirements and property tests
already enforced.

The order mattered more than the tooling: requirements first, then a design that
named its own hard problems, then the pure deterministic core (exit-code
interpretation, classification, coverage mapping, verdict engine) with its
property tests **before** any CLI wiring. The verdict table was traced by hand,
which is where several requirement corrections came from — the full list is in
`DECISIONS.md` §9.

## Costs and credit consumption

| | |
|---|---|
| Budget | 2000 Kiro credits |
| **Consumed to date** | **700 credits** |

Model choice was matched to the leverage of the work, which is how the budget
held:

| Work | Model | Rationale |
|---|---|---|
| Requirements, analysis, design | Claude Opus 4.8 | highest-leverage decisions; expensive to reverse |
| Verdict engine, probe orchestration, worktree lifecycle | Claude Opus 4.8 | the genuinely hard reasoning |
| Routine implementation from approved spec | Claude Sonnet 5 | spec-driven work at lower cost |
| Fixtures, CLI plumbing, report templates | Qwen3 Coder Next | 0.05x multiplier; ~20x cheaper than Opus |

Runtime cost of the tool itself is zero: no API key, no model call, no paid
service. A run costs only the CPU time of the probes it executes, plus whatever a
dependency install fetches from your package index.

## Third-party attribution

Full detail, with versions and license expressions, in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

| Component | Role | License |
|---|---|---|
| [coverage.py](https://github.com/coveragepy/coveragepy) | changed-line coverage measurement (the only runtime dependency) | Apache-2.0 |
| [pytest](https://github.com/pytest-dev/pytest) | the probed test runner, and this project's own runner | MIT |
| [Hypothesis](https://github.com/HypothesisWorks/hypothesis) | property-based testing of the 24 properties | MPL-2.0 |
| [uv](https://github.com/astral-sh/uv) | preferred per-revision environment builder (external binary) | Apache-2.0 OR MIT |
| [Git](https://git-scm.com) | worktree isolation, revision resolution, diffs (external binary) | GPL-2.0 |

Mutation testing (mutmut, cosmic-ray, mutatest, MutPy) is acknowledged as
intellectual ancestry; no claim of novelty is made about the underlying
principle, and none of their code is used here (`DECISIONS.md` §2).

## Repository layout

```
verifierlock/          the package: engine, probes, CLI, report, optional explanation
tests/                 189 tests, incl. the 24 property-based suites
fixtures/              four labelled scenarios (deliberately defective, by design)
.kiro/                 specs, steering, hooks — committed on purpose
DECISIONS.md           the decision log: what was built, rejected, deferred, and why
THIRD_PARTY_NOTICES.md attribution
Dockerfile             pinned Python 3.14 + uv + git judging environment
.github/workflows/     the plain-CLI pull-request check (--strict)
```

## License

MIT — see [`LICENSE`](LICENSE).

# Decision Log

This file records the choices behind VerifierLock: what was built, what was
rejected, what remains open, and why. It is maintained by hand as decisions are
made, not generated.

---

## 1. What problem this solves

An AI coding agent asked to "make the failing test pass" has two paths:

| Path | Result | Correct? |
|---|---|---|
| Fix the production code | CI green | Yes |
| Weaken the test until it passes | CI green | No |

Both produce an identical green check. The reviewer sees a passing build. The
weakened test ships, and from that point the repository has a test that proves
nothing.

VerifierLock provides the missing signal: **does this change have independent
test evidence, or did it weaken its own verifier?**

Green CI is not proof. This tool produces proof.

### Evidence that the problem is real

- GitHub engineering (2026): agent-generated PRs can increase redundancy and
  technical debt while appearing clean and passing tests.
- OWASP *Secure Coding with AI* cheat sheet: recommends CI checks for
  unexpected changes to tests, coverage configuration, and files outside the
  requested scope.
- Practitioner reports of a "rubber stamp" problem: as velocity rises with AI
  adoption, reviewer understanding falls.

---

## 2. The core mechanism, and why it is not mutation testing

Mutation testing (mutmut, cosmic-ray, mutatest, MutPy) synthesises *artificial*
mutants of production code and checks whether the suite kills them. cosmic-ray
states the underlying principle: if the suite passes on mutated code, the tests
do not match the functionality.

VerifierLock inverts the source of the mutant. It invents nothing. It treats the
**base revision as the reference implementation** and asks whether the head
tests can distinguish base behaviour from head behaviour.

**The diff is the mutant.**

Mutation testing is acknowledged prior art and intellectual ancestry. No claim
of novelty is made about the underlying principle. The contribution is the
inversion, the four-probe matrix, and the deterministic verdict rules built on
top of it.

### The four probes

| Probe | Composition | Question answered |
|---|---|---|
| P0 | base source + base tests | Is the baseline trustworthy? |
| P1 | head source + head tests | Is the submitted change green? |
| P2 | base source + head tests | Do the new tests distinguish the change? |
| P3 | head source + base tests | How does the old verifier react? |

The load-bearing probe is **P2**. If the new tests pass against the *old* code,
they do not care which version they are running against, so they prove nothing
about the change.

---

## 3. Ideas considered and rejected

| Idea | Why rejected |
|---|---|
| Generic AI code reviewer | Crowded category; no verifiable output |
| Release-readiness / risk-score dashboard | Dashboards do not answer a question; well-populated space |
| Agent permissions / audit gateway | Established tools already occupy this |
| Migration rollback checker | Insufficiently differentiated |
| Incident postmortem generator | Summarisation, not verification |
| Agent-skill supply-chain scanner | Real need, strong evidence, but shipping tools already exist |
| Regulation-as-code | Highest originality, but not trustworthy in the available time |
| Flaky-test trust auditor | Strong idea; adjacent, and partly subsumed by Req 8c here |
| Spec-to-conformance differential tester | Strong idea; demo depends on finding a wild bug on schedule |

**VerifierLock was chosen** because its demo is guaranteed to fire, the
detection is deterministic, the problem is timely and uncrowded, and the output
is inspectable evidence rather than a score.

---

## 4. Architecture decisions

### 4.1 Mechanism is separated from interpretation

Parsing, worktree management, environment construction, probe execution,
exit-code interpretation, coverage mapping, verdict rules and evidence capture
contain **no language model**. Identical inputs produce identical verdicts.

An optional model may explain a finished report in prose. It may not create,
change, suppress or override a verdict. This is enforced by requirement, by
steering file, and by test.

Rationale: a verdict that varies run to run is not a verdict. If it must be
reproducible, it does not belong to the model.

### 4.2 `git worktree` for isolation

Chosen over clone-per-revision because worktrees share the object database and
are therefore cheap.

Consequences accepted:
- Repositories containing submodules are unsupported (Git documents submodule
  support across multiple worktrees as incomplete). These return INCONCLUSIVE.
- Cleanup must be crash-safe. Worktrees are removed on any termination, and
  stale metadata is pruned at run start, because a leaked worktree holding a ref
  breaks the following run.

### 4.3 `--import-mode=importlib` on every probe

Required, not optional. Grafting one revision's tests onto another revision's
source breaks under pytest's default `prepend` mode, which mutates `sys.path`
and demands globally unique test module names.

Cost accepted, per pytest's own documentation: test modules cannot import each
other, and test-utility modules inside the tests tree are not importable. Such
repositories return INCONCLUSIVE rather than being silently mishandled.
`conftest.py` fixtures are unaffected.

Note: pytest has stated `importlib` will not become the default, so this is a
deliberate override.

### 4.4 Exit code 5 is not success

`pytest.ExitCode` has seven values. Treating "not 1" as passing would let a
probe that collected **zero tests** read as success, which would corrupt every
verdict downstream. Only exit code 0 classifies as passed. Codes 3, 4, 5 and 6
are INCONCLUSIVE. Code 2 (user interrupt) aborts the run.

This is the first test written in the project.

### 4.5 Probe environments follow the tests, not the source

- P2 uses the **head** environment (head tests need head test dependencies)
- P3 uses the **base** environment (base tests need base test dependencies)

An import or collection failure under the other revision's environment is
`ENV_INCOMPATIBLE` → INCONCLUSIVE. A *test failure* is classified as tests
failed, per Requirement 8 — never INCONCLUSIVE.

Rationale: a P2 test failure is the primary positive signal that the new tests
genuinely discriminate. Classifying it as INCONCLUSIVE would make
INDEPENDENT_EVIDENCE unreachable.

### 4.6 Coverage comes from P1 only

Changed production lines are numbered by the **head** revision, so only a
head-source probe can be mapped onto them. Coverage from a base-source probe is
unmappable.

### 4.7 A nondeterministic baseline invalidates the run

If the target repository's suite is flaky, every verdict is noise — a flaky P3
failure would fabricate VERIFIER_WEAKENED, a false accusation about someone
else's repository.

P0 runs at least twice, each in a fresh worktree. Distinct reason codes
separate the cases:

- `BASELINE_REF_UNRESOLVED` — base reference could not be resolved
- `BASELINE_NOT_GREEN` — P0 failed reproducibly
- `BASELINE_NONDETERMINISTIC` — P0 repetitions disagreed

If a repetition is itself INCONCLUSIVE (e.g. timeout), the verdict is
INCONCLUSIVE, not BASELINE_INVALID. The baseline could not be *assessed*, which
is different from being assessed and found unstable.

---

## 5. Fixture design constraint — demo-critical

The bundled fixture's weakened test **must be non-discriminating**: it must pass
against both base and head production source.

There are two human-recognisable ways to "weaken a test" and they produce
*different* verdicts:

| Weakening style | P2 | P3 | Verdict |
|---|---|---|---|
| Non-discriminating (assertion gutted or removed) | pass | fail | **VERIFIER_WEAKENED** |
| Retargeted to assert the buggy value | fail | fail | VERIFIER_CHANGED_REVIEW_REQUIRED |

Writing `assert status == 200` to match the bug produces the *wrong* verdict for
the demo. The weakened test must be something like a removed assertion or
`assert response is not None` — something that cannot tell the revisions apart.

This is recorded because it is the single easiest way to silently break the
headline demonstration.

---

## 6. Non-goals

Deliberately out of scope for v1:

- General AI code review
- Risk scores or dashboards
- Languages or frameworks other than Python/pytest
- Automatic fixes or automatic merging
- Remote or untrusted repository execution
- Any claim of formal verification
- Any claim that passing tests prove correctness
- Any language-model-generated blocking verdict

---

## 7. Security posture

Running a repository's tests executes that repository's code with the caller's
privileges. This is the central risk of the tool and is stated plainly rather
than minimised.

v1 supports trusted local repositories and the bundled fixture only. The CLI
warns before execution. Nothing is transmitted off the machine.

Container isolation is a stretch goal. **No sandboxing claim will be made until
it is implemented and tested.**

---

## 8. Open questions, deliberately deferred

These are known ambiguities left unresolved on purpose. They will be answered
during design and implementation, with real code in front of us, rather than
speculated on in the abstract.

| # | Question | Why deferred |
|---|---|---|
| 1 | How does Environment_Builder discover dependencies — `requirements.txt`, `pyproject.toml`, `setup.py`, or a declared command? | Pure design detail; depends on fixture shape |
| 2 | Does the coverage run for P1 happen inside the verdict probe or as a separate instrumented run? | Leaning separate, so `pytest-cov` instrumentation cannot perturb the verdict |
| 3 | Where exactly does classification failure (Req 4b.5) sit in the verdict precedence? | It short-circuits before any probe; needs stating in design |
| 4 | How broad is the `importlib` inter-test-import limitation across real repositories? | Measure it rather than guess; document as a known limitation |
| 5 | Should the static pre-pass ever *skip* probes, or only annotate evidence? | Currently annotates and selects; may simplify to annotate-only |

An ambiguity noticed and deferred is engineering. An ambiguity chased to zero
before any code exists is not.

### Known limitation: additive changes resolve to INCONCLUSIVE

VerifierLock is strongest for changes that *modify existing behaviour*. Purely
additive changes — a new function plus a new test for it — cannot be probed by
P2. The new test imports a symbol that does not exist in base source, so P2
fails at collection and the run resolves to INCONCLUSIVE (ENV_INCOMPATIBLE /
IMPORT_LIMITATION) rather than INDEPENDENT_EVIDENCE.

This is inherent to the inversion method: the base revision is the reference
implementation, and a test for code that does not yet exist in base has nothing
to run against. It is disclosed rather than hidden. The tool's detection power
is concentrated where it matters most — on changes to behaviour that already
had, or should have, test coverage.

---

## 9. Requirements process

Requirements were written first, in EARS form, then analysed for
cross-requirement contradictions before design.

The analysis pass produced one genuinely valuable finding: Requirement 4.5
originally conflated "cannot import" with "test failed," which contradicted
Requirement 8.2 and would have made INDEPENDENT_EVIDENCE unreachable. That was
worth the pass on its own.

Ten further corrections were applied by hand after tracing the verdict table
manually:

1. Verdict precedence reordered so structural checks precede inconclusive
   aggregation, otherwise NO_VERIFIER_CHANGE was unreachable
2. "Required probe" defined, previously an undefined term load-bearing in a rule
3. Coverage restricted to P1 and mapped to head line numbers
4. Import/collection failure separated from test failure, symmetric for P3
5. P2 and P3 composition rules both specified
6. `COVERAGE_UNAVAILABLE` branch added — the last hole in the verdict table
7. Worktree cleanup made crash-safe; prune at run start as the recovery path
8. P0 repetitions isolated in fresh worktrees, so we cannot cause the flakiness
   we report
9. Bytecode purge changed from "between probes" to pre- and post-conditions,
   because a between-probes rule does not run when a probe times out
10. Distinct exit code for an aborted run that produces no verdict

Analysis was then stopped deliberately. It had begun returning
mis-referenced questions and malformed options — diminishing returns on a spec
that was already exhaustive. The verdict table was verified by hand instead:
every input produces exactly one verdict, and VERIFIER_WEAKENED is unreachable
without P2 passing.

### One correction we made to our own reasoning

We initially believed that failing to delete destination test files before
grafting would silently break the wholesale-test-deletion case (Req 16.5).
Tracing it properly showed otherwise: by the time P2 and P3 run, P0 and P1 have
both passed, so any leftover base-only test in P2 runs against base source and
passes, and any leftover head-only test in P3 runs against head source and
passes. Leftovers cannot flip a verdict.

Delete-before-copy remains good hygiene — it keeps the collected counts in the
evidence record honest — but it is not verdict-critical. Recorded because the
reasoning matters more than the conclusion.

---

## 10. Model usage and human responsibility

Kiro was used throughout: requirements-first spec, requirements analysis,
design, and task-driven implementation.

Budget: 2000 Kiro credits.

| Work | Model | Rationale |
|---|---|---|
| Requirements, analysis, design | Claude Opus 4.8 | Highest-leverage decisions; expensive to reverse |
| Verdict engine, probe orchestration, worktree lifecycle | Claude Opus 4.8 | The genuinely hard reasoning |
| Routine implementation from approved spec | Claude Sonnet 5 | Spec-driven work at lower cost |
| Fixtures, CLI plumbing, report templates | Qwen3 Coder Next | 0.05x multiplier; 20x cheaper than Opus |

Actual credit consumption is reported in the README.

The reproducible path pins an **Active** model tier rather than an Experimental
one, so that judges can run the project consistently throughout the judging
period.

Human responsibility, stated plainly: we chose the problem, defined the product,
reviewed and corrected the requirements by hand, traced the verdict table
ourselves, made every scope decision, and own the result. Models drafted and
reviewed; they did not decide.

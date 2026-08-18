# VerifierLock bundled fixtures — DELIBERATELY DEFECTIVE TEST FIXTURES

> **LABEL: These are test fixtures, not real software.**
> Every "defect" in this directory is intentional and exists only to
> demonstrate and test VerifierLock end-to-end. Do not copy this code into a
> real project, and do not treat any file here as a genuine security issue in
> VerifierLock itself.

Each subdirectory is one labelled scenario. A scenario is stored as two
snapshots of a tiny Python/pytest project:

- `base/` — the reference revision (the trustworthy baseline).
- `head/` — the proposed change under scrutiny.

At test time the harness (`tests/fixture_repo.py`) builds a throwaway Git
repository with exactly two commits — `base` then `head` — and runs the full
VerifierLock pipeline over `base..head`. Storing snapshots (rather than a nested
`.git`) keeps the fixtures committable inside this repository.

## Scenarios and their expected verdicts

| Directory | What the head change does | Expected verdict |
|---|---|---|
| `weakened_authz/` | Introduces a real privilege-escalation defect and **guts** the discriminating assertion so the test still passes | `VERIFIER_WEAKENED` |
| `deleted_test/` | Introduces the same defect and **deletes the discriminating test file wholesale** | `VERIFIER_WEAKENED` |
| `independent_evidence/` | A legitimate new capability, pinned by an **independent regression test** | `INDEPENDENT_EVIDENCE` |
| `review_required/` | A legitimate but **breaking behaviour change** that both suites disagree on | `VERIFIER_CHANGED_REVIEW_REQUIRED` |

The `weakened_authz` weakened test is **non-discriminating**: it passes against
BOTH base and head production source, so probe P2 is all-passed and the demo
yields `VERIFIER_WEAKENED` (see `DECISIONS.md` §5).

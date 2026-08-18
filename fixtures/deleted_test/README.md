# Scenario: `deleted_test` — VERIFIER_WEAKENED (wholesale test deletion)

**LABEL: deliberately defective test fixture.**

## The change under scrutiny (`base` → `head`)

- **Production defect (intentional):** the same privilege-escalation bug as
  `weakened_authz` — `is_admin` becomes `return True`.
- **Test weakening (intentional):** instead of gutting an assertion, the head
  deletes the discriminating test file `tests/test_admin_deny.py` **wholesale**.
  Only the non-discriminating `tests/test_admin_grant.py` remains.

## Why the verdict is VERIFIER_WEAKENED

- **P2** (base source + head tests): the surviving grant test passes → all-passed.
- **P3** (head source + base tests): the base still contains the deleted
  discriminating test, which fails against the buggy head source → tests failed.

P2 passes *because the discriminating test is gone*, and P3 fails *because the
base still contains it* — Requirement 16.5.

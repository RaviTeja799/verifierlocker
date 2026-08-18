# Scenario: `weakened_authz` — VERIFIER_WEAKENED (gutted assertion)

**LABEL: deliberately defective test fixture.**

## The change under scrutiny (`base` → `head`)

- **Production defect (intentional):** `is_admin` is changed from
  `user.get("role") == "admin"` to `return True`. This is a real
  privilege-escalation bug — every caller is now treated as an administrator.
- **Test weakening (intentional):** the discriminating assertion
  `assert is_admin({"role": "user"}) is False` is gutted to
  `assert is_admin({"role": "user"}) is not None`, which passes for **any**
  return value and can no longer catch the defect.

## Why the verdict is VERIFIER_WEAKENED

The weakened test is **non-discriminating** — it passes against both the correct
base source and the buggy head source:

- **P2** (base source + head tests): the gutted test passes → all-passed.
- **P3** (head source + base tests): the original discriminating assertion fails
  against the buggy head source → tests failed.

P2 all-passed with P3 failed is the signature of a change that weakened its own
verifier: the head suite can no longer tell correct source from buggy source.

# Scenario: `review_required` — VERIFIER_CHANGED_REVIEW_REQUIRED

**LABEL: test fixture (no defect — a legitimate but breaking change).**

## The change under scrutiny (`base` → `head`)

- **Production change (legitimate, breaking):** the default access level for a
  new user changes from `"read"` to `"write"`.
- **Test change:** the test is updated to assert the new `"write"` default.

## Why the verdict is VERIFIER_CHANGED_REVIEW_REQUIRED

Both revisions' suites disagree about the other revision's behaviour:

- **P2** (base source + head tests): head test asserts `"write"`, base source
  returns `"read"` → tests failed.
- **P3** (head source + base tests): base test asserts `"read"`, head source
  returns `"write"` → tests failed.

When P2 and P3 both fail, the change is a genuine behaviour change that neither
suite can vouch for across revisions — VerifierLock cannot mechanically decide
whether it is intended, so it defers to a human reviewer.

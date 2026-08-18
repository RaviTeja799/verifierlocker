# Scenario: `independent_evidence` — INDEPENDENT_EVIDENCE

**LABEL: test fixture (no defect here — this is the healthy case).**

## The change under scrutiny (`base` → `head`)

- **Production change (legitimate):** `is_admin` gains a new capability —
  members of the `superuser` role are now administrators too.
- **Test change (legitimate):** an **independent regression test**,
  `test_superuser_is_admin`, is added to pin the new behaviour. The existing
  discriminating tests are kept unchanged.

## Why the verdict is INDEPENDENT_EVIDENCE

- **P2** (base source + head tests): the new regression test asserts
  `superuser` is an admin, which the base source does not implement → tests
  failed.
- **P3** (head source + base tests): the base tests still pass against the new
  source (admins are still admins, plain users still are not) → all-passed.

A head test that fails on the old source while the old tests still pass on the
new source is the signature of a change with genuine, independent test
evidence.

"""Property-based tests for environment selection and P2/P3 outcome
classification (Tasks 13.4, 13.5).

- **Property 15: Environment follows the tests, not the source.**
- **Property 16: Import failure is INCONCLUSIVE, test failure is tests-failed.**

Property 15 is pure. Property 16 drives the real `run_probe` P2/P3 path through
an injected `launcher` returning scripted pytest output, so the ENV_INCOMPATIBLE
vs TESTS_FAILED classification is exercised without a real subprocess. Minimum
100 examples each.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from verifierlock import reasons
from verifierlock.environment import BuiltEnv, select_environment
from verifierlock.probe import LaunchResult, detect_env_incompatible, run_probe
from verifierlock.types import ProbeOutcome


def _built_env(revision: str) -> BuiltEnv:
    return BuiltEnv(
        revision=revision,
        tool="uv",
        python_path=Path(f"/tmp/{revision}-env/bin/python"),
        discovery="pyproject.toml",
        pythonpath_entries=(f"/tmp/{revision}-wt",),
        built=True,
    )


# --- Property 15: Environment follows the tests, not the source ------------


# Feature: verifierlock, Property 15: For any P2 composition the selected
# environment is the head-revision environment, and for any P3 composition the
# selected environment is the base-revision environment.
@settings(max_examples=200)
@given(probe_id=st.sampled_from(["P0", "P1", "P2", "P3", "p2", "p3"]))
def test_environment_follows_the_tests(probe_id: str) -> None:
    base_env = _built_env("base")
    head_env = _built_env("head")
    selected = select_environment(probe_id, base_env, head_env)

    # P2 grafts head tests -> head env; P3 grafts base tests -> base env.
    # (P1 head-as-submitted -> head env; P0 baseline -> base env.)
    if probe_id.upper() in ("P2", "P1"):
        assert selected is head_env
    else:
        assert selected is base_env


def test_p2_uses_head_env_p3_uses_base_env_explicitly() -> None:
    base_env = _built_env("base")
    head_env = _built_env("head")
    assert select_environment("P2", base_env, head_env).revision == "head"
    assert select_environment("P3", base_env, head_env).revision == "base"


# --- Property 16: Import failure INCONCLUSIVE, test failure tests-failed ----


def _scripted_launcher(result: LaunchResult):
    def launcher(argv, cwd, env, timeout):
        return result
    return launcher


# A collection-time import failure of the other revision's source: either a
# missing (non-test) module, or the additive-change "cannot import name" case.
_IMPORT_FAILURE_OUTPUTS = [
    (
        "ImportError while importing test module '/wt/tests/test_x.py'.\n"
        "ModuleNotFoundError: No module named 'authpkg.newfeature'\n"
    ),
    (
        "ERROR collecting tests/test_x.py\n"
        "ImportError: cannot import name 'new_symbol' from 'authpkg'\n"
    ),
    (
        "errors during collection\n"
        "ModuleNotFoundError: No module named 'requests'\n"
    ),
]


# Feature: verifierlock, Property 16: For any probe among P2/P3, if the other
# revision's source cannot be imported or collected under the selected
# environment the probe is INCONCLUSIVE with reason code ENV_INCOMPATIBLE,
# whereas a genuine test failure is classified TESTS_FAILED and never
# INCONCLUSIVE. Holds symmetrically for P2 and P3.
@settings(max_examples=200)
@given(
    probe_id=st.sampled_from(["P2", "P3"]),
    output=st.sampled_from(_IMPORT_FAILURE_OUTPUTS),
    returncode=st.sampled_from([1, 2, 3]),  # collection failures surface variously
)
def test_import_failure_is_env_incompatible(
    probe_id: str, output: str, returncode: int
) -> None:
    launch = LaunchResult(
        returncode=returncode, stdout=output, stderr="", timed_out=False, elapsed_seconds=0.2
    )
    result = run_probe(
        Path("/tmp/vl-envincompat-nonexistent"),
        probe_id=probe_id,
        launcher=_scripted_launcher(launch),
        env_incompatible_detection=True,
    )
    assert result.outcome is ProbeOutcome.INCONCLUSIVE
    assert result.reason is not None
    assert result.reason.startswith(reasons.ENV_INCOMPATIBLE)


# Feature: verifierlock, Property 16 (the other half): a genuine test failure
# (assertion failure, no collection error) is TESTS_FAILED, never INCONCLUSIVE,
# even with env-incompatible detection enabled for P2/P3.
@settings(max_examples=200)
@given(
    probe_id=st.sampled_from(["P2", "P3"]),
    passed=st.integers(min_value=0, max_value=20),
    failed=st.integers(min_value=1, max_value=20),
)
def test_test_failure_is_tests_failed_not_inconclusive(
    probe_id: str, passed: int, failed: int
) -> None:
    output = f"collected {passed + failed} items\n\n{failed} failed, {passed} passed in 0.50s"
    launch = LaunchResult(
        returncode=1, stdout=output, stderr="", timed_out=False, elapsed_seconds=0.5
    )
    result = run_probe(
        Path("/tmp/vl-testfail-nonexistent"),
        probe_id=probe_id,
        launcher=_scripted_launcher(launch),
        env_incompatible_detection=True,
    )
    assert result.outcome is ProbeOutcome.TESTS_FAILED
    assert result.failed == failed


def test_env_incompatible_detection_ignores_plain_test_failure() -> None:
    # A failing assertion with no collection marker is not env-incompatible.
    assert detect_env_incompatible("1 failed, 3 passed in 0.2s") is None


def test_env_incompatible_not_triggered_when_flag_off() -> None:
    # Without the P2/P3 flag, a collection import failure of a non-test module
    # is NOT reclassified as ENV_INCOMPATIBLE here (it would abort/interpret
    # normally); this guards the flag's scoping.
    output = "errors during collection\nModuleNotFoundError: No module named 'requests'\n"
    launch = LaunchResult(
        returncode=1, stdout=output, stderr="", timed_out=False, elapsed_seconds=0.1
    )
    result = run_probe(
        Path("/tmp/vl-flagoff-nonexistent"),
        probe_id="P1",
        launcher=_scripted_launcher(launch),
        env_incompatible_detection=False,
    )
    assert result.reason is None or not result.reason.startswith(reasons.ENV_INCOMPATIBLE)

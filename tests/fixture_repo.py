"""Build a throwaway Git repo from a bundled fixture's base/head snapshots.

The implementation lives in `verifierlock.demo` so the integration tests and the
`python -m verifierlock.demo` entry point materialise fixtures through exactly
one code path: a demo that works is evidence the tests run the same thing a judge
runs. This module stays as the tests' import site.
"""

from __future__ import annotations

from verifierlock.demo import build_scenario_repo, fixtures_root

__all__ = ["build_scenario_repo", "FIXTURES_ROOT", "fixtures_root"]

FIXTURES_ROOT = fixtures_root()

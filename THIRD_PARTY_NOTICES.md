# Third-party attribution

VerifierLock is released under the MIT License (see `LICENSE`). It does not
vendor or redistribute any third-party code; the components below are used at
runtime or in development and remain under their own licenses. Versions are the
ones this project was developed and verified against.

## Python packages

| Component | Used for | Version verified | Upstream license | Project |
|---|---|---|---|---|
| coverage.py | measures changed-line coverage in the instrumented P1 run and emits the Cobertura XML the pure `map_coverage` consumes | 7.15.4 | Apache-2.0 | https://github.com/coveragepy/coveragepy |
| pytest | the test runner VerifierLock probes; also this project's own test runner | 9.1.1 | MIT | https://github.com/pytest-dev/pytest |
| Hypothesis | property-based testing of the 24 design properties | 6.165.2 | MPL-2.0 | https://github.com/HypothesisWorks/hypothesis |

`coverage` is the only runtime dependency (`pyproject.toml` `[project]
dependencies`). `pytest` and `Hypothesis` are development dependencies
(`[project.optional-dependencies] dev`).

Note on Hypothesis (MPL-2.0): it is used as an unmodified, separately installed
test dependency. No Hypothesis source is copied into or distributed with this
repository, so no MPL source-disclosure obligation is triggered here.

## External tools invoked as subprocesses

| Component | Used for | Version verified | Upstream license | Project |
|---|---|---|---|---|
| Git | `git worktree` isolation per probe, `rev-parse`, and diff extraction | 2.x | GPL-2.0 | https://git-scm.com |
| uv | preferred builder for the per-revision dependency-only environments (`uv venv`, `uv pip compile`, `uv pip install`); falls back to `venv` + `pip` when absent | 0.10.9 | dual: Apache-2.0 OR MIT | https://github.com/astral-sh/uv |
| CPython | the interpreter; `python -m venv` / `pip` are the fallback environment builder | 3.10 (development), 3.14 (Docker image) | PSF License Agreement | https://www.python.org |

Git and uv are executed as external programs. Their code is neither linked into
nor distributed with VerifierLock, so their licenses do not extend to this
project's own source.

License facts above were taken from the installed package metadata
(`License-Expression` / `License` fields) and, for uv, from its published
[license policy](https://docs.astral.sh/uv/reference/policies/license/).

## Prior art acknowledgement

VerifierLock's mechanism is an inversion of mutation testing, and mutation
testing is acknowledged as intellectual ancestry rather than reinvented: no
claim of novelty is made about the underlying principle (see `DECISIONS.md` §2).
Projects in that lineage include
[mutmut](https://github.com/boxed/mutmut),
[cosmic-ray](https://github.com/sixty-north/cosmic-ray),
[mutatest](https://github.com/EvanKepner/mutatest), and
[MutPy](https://github.com/mutpy/mutpy). None of their code is used here.

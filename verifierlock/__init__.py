"""VerifierLock: determines whether a Git change has independent test
evidence, or weakened its own verifier.

This package is organized so that deterministic, model-free logic
(revision resolution, classification, probe interpretation, verdict
rules, evidence recording) is kept separate from any side-effecting or
optional (LLM-based) components.
"""

__version__ = "0.1.0"

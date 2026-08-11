"""Revision_Resolver: resolve base/head references to commit hashes (Task 9.1).

Implements `resolve` from design.md's Revision_Resolver section: given a repo
and two user-supplied Git references, resolve each to a full commit hash via
`git rev-parse` (Req 1.2) and record the resulting hashes (Req 1.5) or a
per-ref resolution error.

The two references are resolved independently, because their failures lead to
DIFFERENT verdicts:

- base unresolved -> the Verdict_Engine produces BASELINE_INVALID (row 0a,
  reason `BASELINE_REF_UNRESOLVED`) and no probe runs (Req 1.3);
- head unresolved -> INCONCLUSIVE (row 0b, reason `HEAD_REF_UNRESOLVED`) and no
  probe runs (Req 1.4).

This module records only the *facts* (`base_hash`/`head_hash` on success, or a
raw git error detail on failure). It does NOT attach reason codes: the
Verdict_Engine (`decide`) already owns the base-vs-head reason-code mapping, so
keeping the resolver free of verdict semantics avoids two sources of truth.

## How references are resolved

Each ref is resolved with `git rev-parse --verify <ref>^{commit}`:

- `--verify` guarantees the result names exactly one object and fails cleanly
  (non-zero exit) when the reference does not resolve, so branches, tags, and
  short hashes are all handled by git rather than re-implemented here.
- The `^{commit}` peel dereferences annotated tags (and any other committish)
  down to a commit hash, so a tag resolves to the commit it points at rather
  than to the tag object, and non-commit references are rejected.

On success `stdout` is the full 40-character commit hash. On failure the git
`stderr` (or a fallback message) is captured as the per-ref error detail.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResolvedRevisions:
    """The outcome of resolving the base and head references (Req 1.2, 1.5).

    On success a `*_hash` holds the full commit hash and the matching `*_error`
    is `None`; on failure the `*_hash` is `None` and the `*_error` holds the git
    error detail. The `base_resolved` / `head_resolved` convenience properties
    feed the Verdict_Engine's rows 0a/0b.
    """

    base_hash: str | None
    head_hash: str | None
    base_error: str | None
    head_error: str | None

    @property
    def base_resolved(self) -> bool:
        return self.base_hash is not None

    @property
    def head_resolved(self) -> bool:
        return self.head_hash is not None


def _resolve_one(repo: Path, ref: str) -> tuple[str | None, str | None]:
    """Resolve a single ref to a full commit hash, or return an error detail.

    Returns `(hash, None)` on success and `(None, detail)` on failure.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        # Missing git executable, or repo path does not exist / is not a
        # directory: report as an unresolved reference rather than crashing.
        return None, str(exc)

    if proc.returncode == 0:
        return proc.stdout.strip(), None

    detail = proc.stderr.strip() or f"could not resolve reference {ref!r}"
    return None, detail


def resolve(repo: Path, base_ref: str, head_ref: str) -> ResolvedRevisions:
    """Resolve `base_ref` and `head_ref` to commit hashes (Req 1.2, 1.5).

    The two references are resolved independently so that a base-only or
    head-only failure is recorded on its own, letting the Verdict_Engine apply
    the correct (and different) verdict for each.
    """
    repo = Path(repo)
    base_hash, base_error = _resolve_one(repo, base_ref)
    head_hash, head_error = _resolve_one(repo, head_ref)
    return ResolvedRevisions(
        base_hash=base_hash,
        head_hash=head_hash,
        base_error=base_error,
        head_error=head_error,
    )

"""Optional Explanation_Model: prose narration of a finished Evidence Record
(Task 18.3).

This is the only component in VerifierLock that is *allowed* to involve a
language model, and it is deliberately built so that it cannot matter to the
result. Three structural guarantees, not conventions:

1. **It runs after the fact.** `explain` takes a finished Evidence Record. The
   verdict has already been decided by the pure `Verdict_Engine` before this
   module is ever called (Req 13.1).
2. **It cannot write back.** `explain` narrates a `deepcopy` of the record, so
   even a narrator that tried to mutate its input could not reach the record the
   CLI reports or serialises. It returns prose; there is no return path into the
   verdict, the exit code, or the reproducible core (Req 13.2). The CLI attaches
   the prose under the record's `explanation` key, which
   `evidence.reproducible_core` does not read -- so `--explain` cannot change a
   single byte of the reproducible core (Task 18.8).
3. **It is never required.** With no key and no network, `explain` falls back to
   a deterministic, fully local narration derived from the record, so a verdict
   and an Evidence Record are always produced (Req 13.3).

v1 ships only the local narrator: no model SDK is a dependency and no network
call is made anywhere in VerifierLock. `narrator` is the injection point for a
model-backed narration -- pass a callable and it is used *instead of* the local
text, with any failure falling back to the local narration rather than failing
the run.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass

# A narrator turns a (copied) Evidence Record into prose. Model-backed
# implementations live outside this package; nothing here performs I/O.
Narrator = Callable[[dict], str]

SOURCE_LOCAL = "local-deterministic"
SOURCE_MODEL = "model"


@dataclass(frozen=True)
class Explanation:
    """Prose narration of a finished Evidence Record.

    `source` records how the prose was produced (`local-deterministic` or
    `model`) and `fallback_reason` records why a configured narrator was not
    used, so an Evidence Record never hides which path ran.
    """

    text: str
    source: str = SOURCE_LOCAL
    fallback_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "source": self.source,
            "fallback_reason": self.fallback_reason,
            "affects_verdict": False,  # structural guarantee (Req 13.2)
        }


_VERDICT_NARRATION: dict[str, str] = {
    "INDEPENDENT_EVIDENCE": (
        "The head test suite still tells the two revisions apart, so this change "
        "is backed by test evidence that does not depend on the change itself."
    ),
    "NO_INDEPENDENT_EVIDENCE": (
        "Both grafted probes passed and at least one changed production line was "
        "never executed by the tests, so the new behaviour is not actually pinned "
        "down by the suite."
    ),
    "NO_VERIFIER_CHANGE": (
        "The change did not touch test code or verifier configuration, so there "
        "was no verifier change to assess."
    ),
    "VERIFIER_WEAKENED": (
        "The head tests accept the OLD production behaviour, while the base tests "
        "reject the NEW production behaviour. The change altered behaviour and "
        "moved the tests so they would stop objecting: the verifier was weakened."
    ),
    "VERIFIER_CHANGED_REVIEW_REQUIRED": (
        "The tests and the production source moved together in a way this tool "
        "cannot separate from a legitimate behaviour change. A human needs to "
        "read the diff."
    ),
    "INCONCLUSIVE": (
        "The run did not reach a decidable state, so no claim is made either way."
    ),
    "BASELINE_INVALID": (
        "The base revision was not reproducibly green, so no comparison against "
        "it would have meant anything."
    ),
    "ABORTED_NO_VERDICT": (
        "A probe returned pytest exit code 2, which aborts the run outright. No "
        "verdict was produced."
    ),
}

_PROBE_NARRATION: dict[tuple[str, str], str] = {
    ("P0", "verdict"): "base source with base tests",
    ("P1", "verdict"): "head source with head tests, exactly as submitted",
    ("P1", "coverage"): "the instrumented head re-run used only to measure coverage",
    ("P2", "verdict"): "base source with head tests grafted on",
    ("P3", "verdict"): "head source with base tests grafted on",
}


def _probe_sentences(record: dict) -> list[str]:
    sentences: list[str] = []
    for probe in record.get("probes") or []:
        probe_id = str(probe.get("probe_id", "?"))
        kind = str(probe.get("kind", "verdict"))
        described = _PROBE_NARRATION.get((probe_id, kind), "an unrecognised composition")
        outcome = str(probe.get("outcome", "unknown")).replace("_", " ")
        detail = f" ({probe.get('reason')})" if probe.get("reason") else ""
        sentences.append(
            f"- {probe_id} run {probe.get('repetition', 0)} ({described}): "
            f"{outcome}, {probe.get('passed', 0)} passed / "
            f"{probe.get('failed', 0)} failed of {probe.get('collected', 0)} "
            f"collected{detail}."
        )
    return sentences


def _coverage_sentence(record: dict) -> str:
    coverage = record.get("coverage") or {}
    changed_lines = coverage.get("changed_lines") or []
    if not coverage.get("available"):
        reason = coverage.get("reason") or "coverage could not be determined"
        return f"Changed-line coverage was unavailable: {reason}."
    uncovered = [line for line in changed_lines if not line.get("covered")]
    if not changed_lines:
        return "No changed production lines were measured for coverage."
    if not uncovered:
        return (
            f"All {len(changed_lines)} changed production line(s) were executed by "
            "the head test suite."
        )
    listed = ", ".join(
        f"{line.get('file')}:{line.get('line')}" for line in uncovered[:5]
    )
    more = "" if len(uncovered) <= 5 else f" (and {len(uncovered) - 5} more)"
    return (
        f"{len(uncovered)} of {len(changed_lines)} changed production line(s) were "
        f"never executed: {listed}{more}."
    )


def _findings_sentence(record: dict) -> str:
    findings = record.get("static_findings") or []
    if not findings:
        return "The static pre-pass found no weakening patterns in the diff."
    kinds: dict[str, int] = {}
    for finding in findings:
        kind = str(finding.get("kind", "unknown"))
        kinds[kind] = kinds.get(kind, 0) + 1
    listed = ", ".join(f"{kind} x{count}" for kind, count in sorted(kinds.items()))
    return (
        f"The static pre-pass recorded {len(findings)} advisory finding(s) "
        f"({listed}); these informed probe selection only and never the verdict."
    )


def local_narration(record: dict) -> str:
    """Deterministic, fully offline narration of `record` (Req 13.3).

    A pure function of the record: no key, no network, no model. Used whenever
    no narrator is configured, and as the fallback when a configured narrator
    fails.
    """
    verdict = record.get("verdict") or {}
    value = str(verdict.get("value", "UNKNOWN"))
    run = record.get("run") or {}

    paragraphs = [
        f"VerifierLock compared {run.get('base_ref')} ({run.get('base_commit')}) "
        f"against {run.get('head_ref')} ({run.get('head_commit')}) and returned "
        f"{value} (rule {verdict.get('matched_rule')}, "
        f"reason {verdict.get('reason')}).",
        _VERDICT_NARRATION.get(value, "This verdict is not recognised by the narrator."),
    ]

    probe_sentences = _probe_sentences(record)
    if probe_sentences:
        paragraphs.append("What each probe showed:")
        paragraphs.extend(probe_sentences)
    else:
        paragraphs.append(
            "No probe was executed: the run was decided before any repository "
            "code ran."
        )

    paragraphs.append(_findings_sentence(record))
    paragraphs.append(_coverage_sentence(record))
    paragraphs.append(
        "This narration describes an already-decided run. It is derived from the "
        "Evidence Record and has no path back into the verdict."
    )
    return "\n".join(paragraphs)


def explain(record: dict, *, narrator: Narrator | None = None) -> Explanation:
    """Narrate a finished Evidence Record as prose (Req 13.2, 13.3).

    `record` is deep-copied before narration, so neither this function nor a
    supplied `narrator` can mutate the caller's record -- the verdict and the
    reproducible core are structurally out of reach. When `narrator` is `None`
    (the v1 default: no key, no network, no model SDK) the deterministic local
    narration is returned. A narrator that raises, or returns empty text, falls
    back to the local narration with the reason recorded, so enabling
    explanations can never fail a run.
    """
    snapshot = copy.deepcopy(record)
    if narrator is None:
        return Explanation(text=local_narration(snapshot), source=SOURCE_LOCAL)
    try:
        text = narrator(snapshot)
    except Exception as exc:  # noqa: BLE001 - a narrator must never fail a run
        return Explanation(
            text=local_narration(snapshot),
            source=SOURCE_LOCAL,
            fallback_reason=f"narrator raised {type(exc).__name__}: {exc}",
        )
    if not isinstance(text, str) or not text.strip():
        return Explanation(
            text=local_narration(snapshot),
            source=SOURCE_LOCAL,
            fallback_reason="narrator returned no usable text",
        )
    return Explanation(text=text, source=SOURCE_MODEL)

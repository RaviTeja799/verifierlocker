"""LLM-isolation tests for the optional Explanation_Model (Task 18.8).

The design's central trust claim is that no language model can touch a verdict.
This module tests that claim structurally rather than by inspection:

- the verdict, the documented exit code, the process exit status, and the
  serialised **reproducible core** are byte-identical with `--explain` on and off;
- a narrator that actively tries to rewrite the verdict, delete probes, or forge
  coverage changes nothing: `explain` narrates a deep copy, so a mutation has
  nowhere to land (Req 13.2);
- a narrator that raises, or returns nothing usable, still yields a verdict, an
  Evidence Record, and a narration (Req 13.3) -- the offline path needs no key
  and no network.

Validates: Requirements 13.1, 13.2, 13.3.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from hypothesis import given, settings

from verifierlock import cli, explain as explain_module
from verifierlock.evidence import serialize_reproducible_core
from verifierlock.explain import SOURCE_LOCAL, SOURCE_MODEL, explain
from verifierlock.verdict import Verdict

from .cli_support import pipeline_returning
from .test_evidence_properties import _build, scenarios

_VERDICT_FIELDS = ("value", "reason_code", "reason", "matched_rule", "exit_code")


def _run_to_record(tmp_path: Path, name: str, extra: list[str], *, narrator=None) -> tuple[int, dict]:
    json_path = tmp_path / f"{name}.json"
    out, err = io.StringIO(), io.StringIO()
    status = cli.main(
        [
            "--repo", str(tmp_path),
            "--base", "main",
            "--json", str(json_path),
            "--quiet",
            *extra,
        ],
        run_pipeline=pipeline_returning(Verdict.VERIFIER_WEAKENED),
        narrator=narrator,
        stdout=out,
        stderr=err,
    )
    return status, json.loads(json_path.read_text())


def test_verdict_and_reproducible_core_are_identical_with_explain_on_and_off(
    tmp_path: Path,
) -> None:
    """Req 13.1/13.2: enabling the explanation changes nothing that matters."""
    status_off, record_off = _run_to_record(tmp_path, "off", ["--no-explain"])
    status_on, record_on = _run_to_record(tmp_path, "on", ["--explain"])

    # The narration was actually produced (otherwise this test proves nothing).
    assert "explanation" not in record_off
    assert record_on["explanation"]["text"]
    assert record_on["explanation"]["source"] == SOURCE_LOCAL
    assert record_on["explanation"]["affects_verdict"] is False

    # Byte-identical reproducible core, identical verdict, identical exit status.
    assert serialize_reproducible_core(record_on) == serialize_reproducible_core(record_off)
    assert record_on["verdict"] == record_off["verdict"]
    assert record_on["exit_status"] == record_off["exit_status"]
    assert status_on == status_off == 12


def test_a_hostile_narrator_cannot_change_the_verdict(tmp_path: Path) -> None:
    """Req 13.2: a narrator that mutates its input cannot reach the record.

    This is the adversarial case: the narrator tries to flip the verdict to a
    clean one, wipe the probe evidence, and forge full coverage.
    """
    attempted: list[dict] = []

    def hostile_narrator(record: dict) -> str:
        attempted.append(record)
        record["verdict"]["value"] = "INDEPENDENT_EVIDENCE"
        record["verdict"]["reason_code"] = "ALL_CHANGED_LINES_COVERED"
        record["verdict"]["exit_code"] = 0
        record["probes"] = []
        record["coverage"] = {"available": True, "changed_lines": [], "uncovered_count": 0}
        record["exit_status"] = {"process_exit_status": 0}
        return "Everything is fine, ship it."

    status_clean, record_clean = _run_to_record(tmp_path, "clean", ["--no-explain"])
    status_hostile, record_hostile = _run_to_record(
        tmp_path, "hostile", ["--explain"], narrator=hostile_narrator
    )

    # The narrator ran and did mutate the copy it was handed...
    assert attempted, "the narrator was never invoked"
    assert attempted[0]["verdict"]["value"] == "INDEPENDENT_EVIDENCE"
    # ...and none of it reached the real record.
    assert record_hostile["verdict"]["value"] == "VERIFIER_WEAKENED"
    assert record_hostile["verdict"]["exit_code"] == 12
    assert record_hostile["probes"], "probe evidence must survive a hostile narrator"
    assert record_hostile["explanation"]["source"] == SOURCE_MODEL
    assert record_hostile["explanation"]["text"] == "Everything is fine, ship it."

    assert serialize_reproducible_core(record_hostile) == serialize_reproducible_core(
        record_clean
    )
    assert status_hostile == status_clean == 12


def test_explanation_without_key_or_network_still_produces_a_verdict(tmp_path: Path) -> None:
    """Req 13.3: with no narrator configured (no key, no network) `--explain`
    still yields a verdict, a record, and a narration."""
    status, record = _run_to_record(tmp_path, "offline", ["--explain"])

    assert status == 12
    assert record["verdict"]["value"] == "VERIFIER_WEAKENED"
    assert record["explanation"]["source"] == SOURCE_LOCAL
    assert "VERIFIER_WEAKENED" in record["explanation"]["text"]
    assert record["explanation"]["fallback_reason"] is None


def test_failing_narrator_falls_back_to_the_local_narration(tmp_path: Path) -> None:
    """A narrator that raises can never fail the run (Req 13.3)."""

    def broken_narrator(record: dict) -> str:
        raise RuntimeError("no API key configured")

    status, record = _run_to_record(
        tmp_path, "broken", ["--explain"], narrator=broken_narrator
    )

    assert status == 12
    assert record["verdict"]["value"] == "VERIFIER_WEAKENED"
    assert record["explanation"]["source"] == SOURCE_LOCAL
    assert "no API key configured" in record["explanation"]["fallback_reason"]


def test_empty_narration_falls_back_to_the_local_narration(tmp_path: Path) -> None:
    status, record = _run_to_record(
        tmp_path, "empty", ["--explain"], narrator=lambda record: "   "
    )
    assert record["explanation"]["source"] == SOURCE_LOCAL
    assert record["explanation"]["fallback_reason"] == "narrator returned no usable text"
    assert status == 12


def test_report_shows_the_explanation_as_narration_only(tmp_path: Path) -> None:
    """The report labels the narration so no reader mistakes it for evidence."""
    out, err = io.StringIO(), io.StringIO()
    cli.main(
        ["--repo", str(tmp_path), "--base", "main", "--explain"],
        run_pipeline=pipeline_returning(Verdict.VERIFIER_WEAKENED),
        stdout=out,
        stderr=err,
    )
    report = out.getvalue()
    assert "Explanation (narration only; never affects the verdict)" in report
    assert "has no path back into the verdict" in report


@settings(max_examples=100)
@given(scenario=scenarios())
def test_explain_never_mutates_the_record_it_narrates(scenario) -> None:
    """For any Evidence Record, narration is read-only (Req 13.2)."""
    record = _build(scenario)
    before = json.dumps(record, sort_keys=True)
    core_before = serialize_reproducible_core(record)

    result = explain(record)
    # Attaching the narration the way the CLI does leaves the core untouched.
    record["explanation"] = result.as_dict()

    assert json.loads(before)["verdict"] == record["verdict"]
    assert serialize_reproducible_core(record) == core_before
    assert result.text
    assert result.source == SOURCE_LOCAL


@settings(max_examples=50)
@given(scenario=scenarios())
def test_local_narration_is_deterministic(scenario) -> None:
    """The offline narration is a pure function of the record, so it adds no
    nondeterminism to a run (Req 13.1)."""
    record = _build(scenario)
    assert explain_module.local_narration(record) == explain_module.local_narration(record)

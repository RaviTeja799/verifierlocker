"""Property-based test for the Static_Analyzer (Task 11.2).

**Property 21: Reduced test selection equals the node-ID set difference**

Uses Hypothesis (min 100 examples) to prove that, across generated base and
head collected node-ID sets, the `reduced_test_selection` findings produced by
`analyze` correspond exactly to the base node-IDs that are absent from the head
node-IDs (`base_node_ids - head_node_ids`) -- nothing added, nothing dropped
(Req 5.3).
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from verifierlock.static_analyzer import (
    REDUCED_TEST_SELECTION,
    analyze,
)
from verifierlock.types import Diff

# Node-IDs look like "tests/test_mod.py::test_case" or ".../TestClass::test_x".
_node_id = st.builds(
    lambda mod, case: f"tests/test_{mod}.py::test_{case}",
    st.text(alphabet="abcdefghijklmnop", min_size=1, max_size=6),
    st.text(alphabet="abcdefghijklmnop", min_size=1, max_size=6),
)

_node_id_set = st.frozensets(_node_id, max_size=20)


# Feature: verifierlock, Property 21: Reduced test selection equals the node-ID set difference
@settings(max_examples=200)
@given(base=_node_id_set, head=_node_id_set)
def test_reduced_selection_equals_node_id_difference(
    base: frozenset[str], head: frozenset[str]
) -> None:
    """The set of `reduced_test_selection` finding details equals
    `base - head` (Req 5.3, Property 21)."""
    # An empty diff isolates the node-ID comparison from the diff detectors.
    findings = analyze(Diff(changed_paths=()), base_node_ids=base, head_node_ids=head)

    reduced_details = {
        f.detail for f in findings if f.kind == REDUCED_TEST_SELECTION
    }
    assert reduced_details == set(base - head)

    # Only base-minus-head node-IDs are reported: never a head node-ID, never a
    # node-ID absent from both.
    for node_id in reduced_details:
        assert node_id in base
        assert node_id not in head

    # Each reduced-selection finding is located at the node-ID's file path.
    for f in findings:
        if f.kind == REDUCED_TEST_SELECTION:
            assert f.file == f.detail.split("::", 1)[0]


# Feature: verifierlock, Property 21: Reduced test selection equals the node-ID set difference
@settings(max_examples=100)
@given(nodes=_node_id_set)
def test_no_reduced_selection_when_head_superset_of_base(
    nodes: frozenset[str],
) -> None:
    """When head collects every base node-ID (head is a superset), there is no
    reduced selection at all."""
    findings = analyze(
        Diff(changed_paths=()), base_node_ids=nodes, head_node_ids=nodes
    )
    assert not [f for f in findings if f.kind == REDUCED_TEST_SELECTION]

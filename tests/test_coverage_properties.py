"""Property-based tests for `Coverage_Analyzer.map_coverage` (Task 5.2).

**Property 8: Coverage mapping equals the intersection with changed lines**

Uses Hypothesis (min 100 examples each) to prove, across generated sets of
changed head lines and generated sets of covered lines synthesised into valid
Cobertura XML, that `map_coverage`:

- emits exactly one `ChangedLine` per changed (file, line) -- a total mapping
  over the changed lines, nothing added, nothing dropped (Req 9.2), and
- marks each `ChangedLine` covered iff (file, line) is in the intersection of
  the changed lines with the covered lines (Property 8, Req 9.3), and
- reports `available=True` whenever the XML carries coverage data.

Also includes example-based tests for the COVERAGE_UNAVAILABLE cases (empty /
malformed XML) and for the determinable-but-file-absent case (Req 9.4).
"""

from __future__ import annotations

from xml.sax.saxutils import quoteattr

from hypothesis import given, settings
from hypothesis import strategies as st

from verifierlock.coverage import map_coverage

# --- Cobertura XML synthesis helper ---------------------------------------


def build_cobertura_xml(files: dict[str, dict[int, int]]) -> str:
    """Build a syntactically valid Cobertura XML document.

    `files` maps a filename to a `{line_number: hits}` dict. The produced XML
    follows the coverage.py Cobertura structure:
    coverage/packages/package/classes/class[@filename]/lines/line[@number,@hits].
    """
    class_elements = []
    for filename, lines in files.items():
        line_elements = "".join(
            f'<line number="{number}" hits="{hits}"/>'
            for number, hits in sorted(lines.items())
        )
        class_elements.append(
            f'<class filename={quoteattr(filename)} name="mod">'
            f"<methods/><lines>{line_elements}</lines></class>"
        )
    classes = "".join(class_elements)
    return (
        '<?xml version="1.0" ?>'
        '<!DOCTYPE coverage SYSTEM '
        "'http://cobertura.sourceforge.net/xml/coverage-04.dtd'>"
        '<coverage version="7.0" timestamp="0" lines-valid="0" '
        'lines-covered="0" line-rate="0" branches-valid="0" '
        'branches-covered="0" branch-rate="0" complexity="0">'
        "<sources><source>.</source></sources>"
        f'<packages><package name="pkg" line-rate="0" branch-rate="0" '
        f'complexity="0"><classes>{classes}</classes></package></packages>'
        "</coverage>"
    )


# --- Shared generators -----------------------------------------------------

_filename = st.sampled_from(
    ["a.py", "pkg/b.py", "pkg/sub/c.py", "d.py", "src/pkg/e.py"]
)
_line_number = st.integers(min_value=1, max_value=500)


@st.composite
def changed_and_covered(
    draw: st.DrawFn,
) -> tuple[dict[str, frozenset[int]], dict[str, frozenset[int]]]:
    """Generate (changed_head_lines, covered_lines) keyed by the same files.

    Returns two dicts over an overlapping set of files: the changed head lines
    per file and, independently, the covered lines per file. The covered set is
    drawn independently so the intersection with the changed set is non-trivial.
    """
    files = draw(st.lists(_filename, min_size=1, max_size=4, unique=True))
    changed: dict[str, frozenset[int]] = {}
    covered: dict[str, frozenset[int]] = {}
    for f in files:
        changed[f] = frozenset(
            draw(st.sets(_line_number, min_size=0, max_size=8))
        )
        covered[f] = frozenset(
            draw(st.sets(_line_number, min_size=0, max_size=8))
        )
    return changed, covered


# --- Property 8: mapping equals intersection with changed lines ------------


@settings(max_examples=150)
@given(data=changed_and_covered())
def test_mapping_is_total_over_changed_lines_and_equals_intersection(
    data: tuple[dict[str, frozenset[int]], dict[str, frozenset[int]]],
) -> None:
    """The result covers exactly the changed lines, and each line's `covered`
    flag equals membership in the changed-vs-covered intersection (Req 9.2,
    9.3, Property 8)."""
    changed, covered = data

    # Synthesise Cobertura XML reporting each covered line with hits=1 and,
    # to make the "hits==0 is uncovered" path meaningful, also emit some
    # changed-but-not-covered lines with hits=0.
    files_xml: dict[str, dict[int, int]] = {}
    for f in changed:
        entry: dict[int, int] = {}
        for line in covered[f]:
            entry[line] = 1
        for line in changed[f]:
            entry.setdefault(line, 0)
        files_xml[f] = entry

    # Guarantee the document always carries at least one <line> so it is a
    # real (available) coverage report even when the generated changed/covered
    # sets are both empty. The sentinel line (well above the generated 1..500
    # range) never collides with a changed line, so it cannot affect the
    # intersection assertions below.
    _SENTINEL_LINE = 10_000
    first_file = next(iter(files_xml))
    files_xml[first_file].setdefault(_SENTINEL_LINE, 1)

    xml = build_cobertura_xml(files_xml)
    result = map_coverage(xml, changed)

    assert result.available is True
    assert result.reason is None

    # Total mapping: exactly one ChangedLine per changed (file, line).
    expected_pairs = {(f, ln) for f, lns in changed.items() for ln in lns}
    returned_pairs = [(cl.file, cl.line) for cl in result.lines]
    assert sorted(returned_pairs) == sorted(expected_pairs)
    assert len(returned_pairs) == len(set(returned_pairs))  # no duplicates

    # Intersection property: covered iff line is in changed ∩ covered.
    for cl in result.lines:
        in_intersection = cl.line in (changed[cl.file] & covered[cl.file])
        assert cl.covered is in_intersection

    # Deterministic ordering by (file, line).
    assert list(result.lines) == sorted(
        result.lines, key=lambda cl: (cl.file, cl.line)
    )


@settings(max_examples=150)
@given(data=changed_and_covered())
def test_mapping_is_deterministic(
    data: tuple[dict[str, frozenset[int]], dict[str, frozenset[int]]],
) -> None:
    """Identical inputs produce byte-identical results (reproducibility)."""
    changed, covered = data
    files_xml = {f: {ln: 1 for ln in covered[f]} for f in changed}
    xml = build_cobertura_xml(files_xml)

    first = map_coverage(xml, changed)
    second = map_coverage(xml, changed)
    assert first == second


# --- Example-based COVERAGE_UNAVAILABLE cases (Req 9.4, row 10.12) ---------


def test_empty_xml_is_unavailable() -> None:
    result = map_coverage("", {"a.py": frozenset({1, 2})})
    assert result.available is False
    assert result.lines == ()
    assert result.reason is not None


def test_blank_xml_is_unavailable() -> None:
    result = map_coverage("   \n\t  ", {"a.py": frozenset({1})})
    assert result.available is False
    assert result.lines == ()
    assert result.reason is not None


def test_malformed_xml_is_unavailable() -> None:
    result = map_coverage("<coverage><packages>", {"a.py": frozenset({1})})
    assert result.available is False
    assert result.lines == ()
    assert result.reason is not None


def test_non_coverage_xml_is_unavailable() -> None:
    result = map_coverage(
        "<something><else/></something>", {"a.py": frozenset({1})}
    )
    assert result.available is False
    assert result.lines == ()
    assert result.reason is not None


def test_coverage_xml_with_no_line_data_is_unavailable() -> None:
    xml = build_cobertura_xml({"a.py": {}})
    result = map_coverage(xml, {"a.py": frozenset({1, 2})})
    assert result.available is False
    assert result.lines == ()


# --- Determinable-but-file-absent still yields available=True -------------


def test_file_absent_from_report_is_available_with_uncovered_lines() -> None:
    """A changed file that the report never measured is not 'unavailable':
    coverage was determined, so its changed lines are recorded covered=False
    while `available` stays True (Req 9.3)."""
    # Report measures b.py, but the changed file is a.py.
    xml = build_cobertura_xml({"b.py": {10: 1, 11: 1}})
    result = map_coverage(xml, {"a.py": frozenset({1, 2, 3})})

    assert result.available is True
    assert result.reason is None
    assert sorted((cl.line, cl.covered) for cl in result.lines) == [
        (1, False),
        (2, False),
        (3, False),
    ]


def test_path_suffix_matching_maps_covered_lines() -> None:
    """A Cobertura filename that is a path-suffix of the changed key still
    maps its covered lines onto that key."""
    # Report filename is the suffix 'pkg/mod.py'; changed key is the longer
    # 'src/pkg/mod.py'. They must match on segment boundaries.
    xml = build_cobertura_xml({"pkg/mod.py": {5: 1, 6: 0}})
    result = map_coverage("".join(xml), {"src/pkg/mod.py": frozenset({5, 6})})

    assert result.available is True
    covered_map = {cl.line: cl.covered for cl in result.lines}
    assert covered_map == {5: True, 6: False}


def test_suffix_matching_respects_segment_boundaries() -> None:
    """`mod.py` must not match `submod.py`: suffix matching is on whole
    path segments, not raw string suffix."""
    xml = build_cobertura_xml({"submod.py": {5: 1}})
    result = map_coverage(xml, {"mod.py": frozenset({5})})

    assert result.available is True
    covered_map = {cl.line: cl.covered for cl in result.lines}
    assert covered_map == {5: False}

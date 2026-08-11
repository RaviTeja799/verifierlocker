"""Pure mapping of P1 Cobertura coverage onto changed head lines (Task 5.1).

Implements `Coverage_Analyzer.map_coverage` from design.md: given a Cobertura
coverage XML document (as a string, exactly the format produced by
`coverage xml`) and a mapping of changed production files to their changed
head-revision line numbers, produce a `CoverageResult` recording, for every
changed line, whether that line was exercised by the P1 test suite (Req 9.2,
9.3).

This module is pure: it performs NO filesystem I/O, NO subprocess calls, and
has NO external dependencies. Parsing uses the standard library
`xml.etree.ElementTree`. Coverage *collection* (running `coverage run -m
pytest` and emitting Cobertura XML) happens in a side-effecting component
built later (Task 14); this module only maps the already-emitted XML onto the
changed lines, so the verdict path contains no external tool (Req 9.4).

## What "covered" means

Cobertura reports, per source file, a set of `<line number="N" hits="H"/>`
entries. A line is considered *covered* iff its `hits` value is a positive
integer (`hits > 0`). The result for a given changed file is exactly the
intersection of that file's changed head lines with its covered lines
(Property 8): every changed line is emitted exactly once as a `ChangedLine`,
with `covered=True` when the line is in the covered set and `covered=False`
otherwise (an uncovered changed line is still recorded, with its location, so
callers can report it -- Req 9.3).

## Filename matching rule (deterministic, no filesystem I/O)

Cobertura `filename` attributes are relative to some source root chosen by the
coverage tool and need not equal the keys of `changed_head_lines` (which are
whatever paths the caller uses for changed files). To reconcile them without
touching disk, matching is purely lexical on path-segment boundaries:

1. **Exact match** of the normalised POSIX path strings, OR
2. **Path-suffix match**: the segment tuple of one path is a suffix of the
   segment tuple of the other (matched on whole path segments, so `mod.py`
   never matches `submod.py`, and `pkg/mod.py` matches `src/pkg/mod.py` but
   not `otherpkg/mod.py`).

For a changed-file key, the covered-line set is the UNION of the covered lines
of every Cobertura entry that suffix-matches that key. Taking the union keeps
the result deterministic and order-independent when a report happens to list
more than one entry that matches (e.g. the same file under two roots): a line
counts as covered if any matching entry marks it covered.

## Determinism

The `lines` tuple is always sorted by `(file, line)` so identical inputs
produce a byte-identical result -- required because the Evidence Record must be
reproducible.

## Availability (Req 9.4 / verdict row 10.12)

`available=False` (with a populated `reason`, and `lines=()`) means coverage
could not be determined AT ALL, so the Verdict_Engine may emit INCONCLUSIVE
`COVERAGE_UNAVAILABLE`. This happens only when:

- `cobertura_xml` is empty or blank, OR
- it fails to parse as XML, OR
- it parses but is not a Cobertura `<coverage>` document / contains no line
  data whatsoever.

A file that is simply absent from the report is NOT "unavailable": coverage was
determined, that file just was not measured, so its changed lines are recorded
as `covered=False`. That case yields `available=True`, `reason=None`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from . import reasons


@dataclass(frozen=True)
class ChangedLine:
    """One changed head-revision production line and whether it was covered."""

    file: str
    line: int
    covered: bool


@dataclass(frozen=True)
class CoverageResult:
    """The mapping of changed head lines to coverage (Req 9.2, 9.3).

    `lines` is sorted by `(file, line)` and, when `available` is True, contains
    exactly one `ChangedLine` per (file, line) in `changed_head_lines`. When
    `available` is False the coverage could not be determined at all, `lines`
    is empty, and `reason` names why (COVERAGE_UNAVAILABLE, Req 10.12).
    """

    lines: tuple[ChangedLine, ...]
    available: bool          # False -> COVERAGE_UNAVAILABLE
    reason: str | None


def _covered_lines_by_file(root: ET.Element) -> tuple[dict[str, frozenset[int]], bool]:
    """Extract covered line numbers per Cobertura source file.

    Returns `(covered_by_file, saw_any_line)` where `covered_by_file` maps each
    reported filename to the frozenset of its covered (`hits > 0`) line numbers,
    and `saw_any_line` is True iff at least one `<line>` element was seen at all
    (covered or not). `saw_any_line` distinguishes a real, if fully-uncovered,
    coverage document from a structure carrying no line data.
    """
    covered_by_file: dict[str, set[int]] = {}
    saw_any_line = False

    # Cobertura structure: coverage/packages/package/classes/class[@filename]/
    # lines/line[@number,@hits]. Search for every <class> defensively (rather
    # than assuming the exact nesting) so minor structural variation between
    # coverage.py versions still maps.
    for class_el in root.iter("class"):
        filename = class_el.get("filename")
        if not filename:
            continue
        covered = covered_by_file.setdefault(filename, set())
        for line_el in class_el.iter("line"):
            number_attr = line_el.get("number")
            if number_attr is None:
                continue
            try:
                number = int(number_attr)
            except ValueError:
                continue
            saw_any_line = True
            hits_attr = line_el.get("hits", "0")
            try:
                hits = int(hits_attr)
            except ValueError:
                hits = 0
            if hits > 0:
                covered.add(number)

    frozen = {name: frozenset(lines) for name, lines in covered_by_file.items()}
    return frozen, saw_any_line


def _segments(path: str) -> tuple[str, ...]:
    """Normalised POSIX path segments, dropping any leading `./` / `/`."""
    parts = PurePosixPath(path).parts
    # PurePosixPath keeps a leading "/" as its own part; drop it so absolute
    # and relative spellings of the same tail compare equal on suffix.
    return tuple(p for p in parts if p not in ("", "/", "."))


def _is_suffix_match(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """True iff one segment tuple is a whole-segment suffix of the other."""
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return longer[-len(shorter):] == shorter


def _covered_for_key(key: str, covered_by_file: dict[str, frozenset[int]]) -> frozenset[int]:
    """Union of covered lines from every report entry matching `key`.

    Matches exactly first; falls back to a whole-segment path-suffix match in
    either direction. Deterministic: the union is order-independent.
    """
    if key in covered_by_file:
        # Exact key present: prefer it, but still union any suffix matches so a
        # line covered under an alternate spelling is not lost.
        result: set[int] = set(covered_by_file[key])
    else:
        result = set()

    key_segments = _segments(key)
    for filename, lines in covered_by_file.items():
        if filename == key:
            continue
        if _is_suffix_match(key_segments, _segments(filename)):
            result |= lines
    return frozenset(result)


def map_coverage(
    cobertura_xml: str,
    changed_head_lines: dict[str, frozenset[int]],
) -> CoverageResult:
    """Pure mapping of P1 Cobertura coverage onto changed head lines (Req 9).

    For every `(file, line)` in `changed_head_lines`, emit exactly one
    `ChangedLine` whose `covered` flag is True iff that line was exercised
    (intersection with the covered lines parsed from `cobertura_xml`). Lines
    that are changed but not exercised are recorded with `covered=False` so
    their location is preserved (Req 9.3).

    Returns `available=False` with a `COVERAGE_UNAVAILABLE` reason and empty
    `lines` when coverage cannot be determined at all: blank input, an XML
    parse error, or a document that is not a Cobertura coverage report / has no
    line data (Req 9.4, verdict row 10.12). A file that is merely absent from
    the report is still "available": its changed lines are simply uncovered.
    """
    if not cobertura_xml or not cobertura_xml.strip():
        return CoverageResult(
            lines=(),
            available=False,
            reason=f"{reasons.COVERAGE_UNAVAILABLE}:empty",
        )

    try:
        root = ET.fromstring(cobertura_xml)
    except ET.ParseError:
        return CoverageResult(
            lines=(),
            available=False,
            reason=f"{reasons.COVERAGE_UNAVAILABLE}:parse_error",
        )

    # A Cobertura report is rooted at <coverage>. Accept either the root being
    # <coverage> or a <coverage> element somewhere inside (defensive), else the
    # document is not coverage data at all.
    if root.tag != "coverage" and root.find(".//coverage") is None:
        coverage_root = None
    elif root.tag == "coverage":
        coverage_root = root
    else:
        coverage_root = root.find(".//coverage")

    if coverage_root is None:
        return CoverageResult(
            lines=(),
            available=False,
            reason=f"{reasons.COVERAGE_UNAVAILABLE}:not_coverage_xml",
        )

    covered_by_file, saw_any_line = _covered_lines_by_file(coverage_root)

    if not saw_any_line:
        return CoverageResult(
            lines=(),
            available=False,
            reason=f"{reasons.COVERAGE_UNAVAILABLE}:no_line_data",
        )

    mapped: list[ChangedLine] = []
    for file, head_lines in changed_head_lines.items():
        covered_set = _covered_for_key(file, covered_by_file)
        for line in head_lines:
            mapped.append(
                ChangedLine(file=file, line=line, covered=line in covered_set)
            )

    mapped.sort(key=lambda cl: (cl.file, cl.line))
    return CoverageResult(lines=tuple(mapped), available=True, reason=None)

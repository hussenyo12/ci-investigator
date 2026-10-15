import pytest

from ci_investigator.evidence_provenance import (
    EvidenceProvenanceError,
    SanitizedSource,
    physical_lines,
    stable_evidence_id,
    validate_evidence_provenance,
)
from ci_investigator.schemas import Evidence


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ()),
        ("a\n", ("a",)),
        ("a\nb", ("a", "b")),
        ("a\n\n", ("a", "")),
        ("a\r\nb\r\n", ("a", "b")),
        ("a\u2028same\rprogress\nend\r", ("a\u2028same\rprogress", "end\r")),
    ],
)
def test_physical_line_policy(text: str, expected: tuple[str, ...]) -> None:
    assert physical_lines(text) == expected


def evidence_fixture() -> tuple[Evidence, SanitizedSource]:
    source = SanitizedSource(
        source_identifier="source-1",
        source_type="log",
        lines=("start", "PASSWORD=[REDACTED:PASSWORD]", "AssertionError: test"),
    )
    text = source.excerpt(2, 3)
    item = Evidence(
        evidence_id=stable_evidence_id(source, 2, 3, text),
        source_type="log",
        source_identifier=source.source_identifier,
        start_line=2,
        end_line=3,
        text=text,
    )
    return item, source


def test_exact_sanitized_provenance() -> None:
    item, source = evidence_fixture()
    assert validate_evidence_provenance([item], {source.source_identifier: source}) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"text": "PASSWORD=synthetic-raw-secret"},
        {"source_identifier": "unknown"},
        {"start_line": 4, "end_line": 5},
        {"evidence_id": "invented"},
        {"source_type": "config"},
    ],
)
def test_fabricated_excerpt_rejected_without_leaks(changes: dict[str, object]) -> None:
    item, source = evidence_fixture()
    modified = Evidence.model_validate(item.model_dump() | changes)
    with pytest.raises(EvidenceProvenanceError) as caught:
        validate_evidence_provenance([modified], {source.source_identifier: source})
    assert "synthetic-raw-secret" not in str(caught.value)


def test_duplicate_ids_rejected() -> None:
    item, source = evidence_fixture()
    with pytest.raises(EvidenceProvenanceError, match="duplicate"):
        validate_evidence_provenance([item, item], {source.source_identifier: source})

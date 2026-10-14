import pytest
from pydantic import ValidationError

from ci_investigator import Evidence, Hypothesis, InvestigationReport, WorkflowRunReference


@pytest.mark.parametrize("field", ["owner", "repo", "url"])
@pytest.mark.parametrize("value", ["", " \t\n", None])
def test_workflow_requires_nonempty_fields(
    workflow_run: WorkflowRunReference, field: str, value: object
) -> None:
    data = workflow_run.model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        WorkflowRunReference.model_validate(data)


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "42", None])
def test_run_id_is_a_strict_positive_integer(
    workflow_run: WorkflowRunReference, value: object
) -> None:
    data = workflow_run.model_dump()
    data["run_id"] = value
    with pytest.raises(ValidationError):
        WorkflowRunReference.model_validate(data)


@pytest.mark.parametrize(
    "changes",
    [
        {"owner": "other"},
        {"repo": "other"},
        {"run_id": 43},
        {"repo": "..", "url": "https://github.com/example/../actions/runs/42"},
        {"url": "https://github.com/example/project/actions/runs/42/"},
        {"url": "https://github.com/example/project/actions/runs/42?attempt=2"},
        {"url": "https://evil.test/example/project/actions/runs/42"},
    ],
)
def test_workflow_url_must_match_identity(
    workflow_run: WorkflowRunReference, changes: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        WorkflowRunReference.model_validate(workflow_run.model_dump() | changes)


@pytest.mark.parametrize("field", ["evidence_id", "source_identifier", "text"])
@pytest.mark.parametrize("value", ["", " \t\n", None])
def test_evidence_requires_nonempty_fields(evidence: Evidence, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(evidence.model_dump() | {field: value})


@pytest.mark.parametrize(
    ("start", "end"), [(0, 1), (-1, 2), (2, 1), (1, 0), (True, 2), (1, 2.0), ("1", 2)]
)
def test_invalid_evidence_line_ranges(evidence: Evidence, start: object, end: object) -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(evidence.model_dump() | {"start_line": start, "end_line": end})


@pytest.mark.parametrize("source_type", ["log", "config"])
def test_single_original_line_and_exact_text_are_preserved(
    evidence: Evidence, source_type: str
) -> None:
    text = "  indented evidence\n"
    validated = Evidence.model_validate(
        evidence.model_dump()
        | {"source_type": source_type, "start_line": 500, "end_line": 500, "text": text}
    )
    assert validated.text == text
    assert validated.start_line == validated.end_line == 500


@pytest.mark.parametrize(
    "changes",
    [
        {"explanation": " "},
        {"supporting_evidence_ids": [""]},
        {"confidence": "certain"},
        {"confidence": 0.9},
        {"verification_steps": []},
        {"verification_steps": [" "]},
    ],
)
def test_invalid_hypothesis_fields(hypothesis: Hypothesis, changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Hypothesis.model_validate(hypothesis.model_dump() | changes)


@pytest.mark.parametrize("confidence", ["low", "medium", "high"])
def test_qualitative_confidence_labels(hypothesis: Hypothesis, confidence: str) -> None:
    validated = Hypothesis.model_validate(hypothesis.model_dump() | {"confidence": confidence})
    assert validated.confidence == confidence


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_run": None},
        {"observed_failure_summary": " "},
        {"recommended_next_steps": []},
        {"recommended_next_steps": [""]},
        {"limitations": [" "]},
        {"status": "unknown"},
        {"evidence": None},
        {"hypotheses": None},
    ],
)
def test_invalid_report_fields(report: InvestigationReport, changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        InvestigationReport.model_validate(report.model_dump() | changes)


def test_unknown_source_type(evidence: Evidence) -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(evidence.model_dump() | {"source_type": "web"})


def test_json_round_trip(report: InvestigationReport) -> None:
    assert InvestigationReport.model_validate_json(report.model_dump_json()) == report


def test_empty_collections_default_independently(workflow_run: WorkflowRunReference) -> None:
    data = {
        "workflow_run": workflow_run,
        "observed_failure_summary": "Logs are unavailable.",
        "recommended_next_steps": ["Obtain the failed job logs."],
        "status": "insufficient_evidence",
    }
    first = InvestigationReport.model_validate(data)
    second = InvestigationReport.model_validate(data)
    assert first.evidence == first.hypotheses == first.limitations == []
    first.limitations.append("No logs.")
    assert second.limitations == []


def test_extra_fields_are_rejected(report: InvestigationReport) -> None:
    for model in [report, report.workflow_run, report.evidence[0], report.hypotheses[0]]:
        with pytest.raises(ValidationError, match="Extra inputs"):
            type(model).model_validate(model.model_dump() | {"unexpected": "value"})


def test_missing_required_fields_are_rejected(report: InvestigationReport) -> None:
    for model in [report, report.workflow_run, report.evidence[0], report.hypotheses[0]]:
        for name, field in type(model).model_fields.items():
            if field.is_required():
                data = model.model_dump()
                del data[name]
                with pytest.raises(ValidationError, match="Field required"):
                    type(model).model_validate(data)

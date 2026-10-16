import pytest

from ci_investigator import EvidenceValidationError, InvestigationReport, validate_report_evidence


def changed_report(report: InvestigationReport, **changes: object) -> InvestigationReport:
    return InvestigationReport.model_validate(report.model_dump() | changes)


def test_correct_references(report: InvestigationReport) -> None:
    assert validate_report_evidence(report) is None


def test_multiple_sources_and_shared_references(report: InvestigationReport) -> None:
    config = report.evidence[0].model_dump() | {
        "evidence_id": "config-1",
        "source_type": "config",
        "source_identifier": ".github/workflows/ci.yml",
        "text": "run: python -m pytest",
    }
    hypothesis = report.hypotheses[0].model_dump() | {
        "supporting_evidence_ids": ["log-1", "config-1"]
    }
    validated = changed_report(
        report, evidence=[report.evidence[0], config], hypotheses=[hypothesis, hypothesis]
    )
    assert validate_report_evidence(validated) is None


@pytest.mark.parametrize("status", ["diagnosed", "insufficient_evidence"])
def test_hallucinated_evidence_ids_are_rejected(report: InvestigationReport, status: str) -> None:
    hypothesis = report.hypotheses[0].model_dump() | {
        "supporting_evidence_ids": ["log-1", "invented-99"]
    }
    invalid = changed_report(report, status=status, hypotheses=[hypothesis])
    with pytest.raises(EvidenceValidationError, match="hypothesis 1.*unknown.*invented-99"):
        validate_report_evidence(invalid)


@pytest.mark.parametrize("status", ["diagnosed", "insufficient_evidence"])
def test_duplicate_evidence_records_are_rejected(report: InvestigationReport, status: str) -> None:
    duplicate = report.evidence[0].model_dump() | {"text": "Different text with the same ID."}
    invalid = changed_report(report, status=status, evidence=[report.evidence[0], duplicate])
    with pytest.raises(EvidenceValidationError, match="duplicate evidence ID.*log-1"):
        validate_report_evidence(invalid)


def test_duplicate_unreferenced_records_are_rejected(report: InvestigationReport) -> None:
    invalid = changed_report(
        report,
        status="insufficient_evidence",
        evidence=[report.evidence[0], report.evidence[0]],
        hypotheses=[],
    )
    with pytest.raises(EvidenceValidationError, match="duplicate evidence ID"):
        validate_report_evidence(invalid)


@pytest.mark.parametrize("status", ["diagnosed", "insufficient_evidence"])
def test_duplicate_references_are_rejected(report: InvestigationReport, status: str) -> None:
    hypothesis = report.hypotheses[0].model_dump() | {"supporting_evidence_ids": ["log-1", "log-1"]}
    invalid = changed_report(report, status=status, hypotheses=[hypothesis])
    with pytest.raises(EvidenceValidationError, match="hypothesis 1 repeats.*log-1"):
        validate_report_evidence(invalid)


@pytest.mark.parametrize("include_evidence", [False, True])
def test_insufficient_evidence_without_hypotheses(
    report: InvestigationReport, include_evidence: bool
) -> None:
    validated = changed_report(
        report,
        status="insufficient_evidence",
        hypotheses=[],
        evidence=report.evidence if include_evidence else [],
    )
    assert validate_report_evidence(validated) is None


def test_insufficient_evidence_rejects_an_unbacked_tentative_hypothesis(
    report: InvestigationReport,
) -> None:
    hypothesis = report.hypotheses[0].model_dump() | {"supporting_evidence_ids": []}
    validated = changed_report(
        report, status="insufficient_evidence", evidence=[], hypotheses=[hypothesis]
    )
    with pytest.raises(EvidenceValidationError, match="insufficient_evidence.*zero hypotheses"):
        validate_report_evidence(validated)


@pytest.mark.parametrize("include_evidence", [False, True])
def test_diagnosed_reports_require_hypotheses(
    report: InvestigationReport, include_evidence: bool
) -> None:
    invalid = changed_report(
        report, evidence=report.evidence if include_evidence else [], hypotheses=[]
    )
    with pytest.raises(
        EvidenceValidationError, match="diagnosed report requires at least one hypothesis"
    ):
        validate_report_evidence(invalid)


@pytest.mark.parametrize("include_evidence", [False, True])
def test_diagnosed_reports_reject_an_unsupported_hypothesis(
    report: InvestigationReport, include_evidence: bool
) -> None:
    unbacked = report.hypotheses[0].model_dump() | {"supporting_evidence_ids": []}
    invalid = changed_report(
        report,
        evidence=report.evidence if include_evidence else [],
        hypotheses=[unbacked],
    )
    with pytest.raises(
        EvidenceValidationError, match="hypothesis 1 requires.*supporting evidence ID"
    ):
        validate_report_evidence(invalid)


@pytest.mark.parametrize("unsupported_first", [False, True])
@pytest.mark.parametrize("confidence", ["low", "medium", "high"])
def test_diagnosed_report_rejects_mixed_supported_and_unsupported_hypotheses(
    report: InvestigationReport, unsupported_first: bool, confidence: str
) -> None:
    unbacked = report.hypotheses[0].model_dump() | {
        "explanation": "An unsupported possible cause.",
        "supporting_evidence_ids": [],
        "confidence": confidence,
    }
    hypotheses = [report.hypotheses[0], unbacked]
    if unsupported_first:
        hypotheses.reverse()
    invalid = changed_report(report, hypotheses=hypotheses)
    index = 1 if unsupported_first else 2
    with pytest.raises(
        EvidenceValidationError, match=f"hypothesis {index} requires.*supporting evidence ID"
    ):
        validate_report_evidence(invalid)


def test_diagnosed_report_with_two_individually_supported_hypotheses(
    report: InvestigationReport,
) -> None:
    second_evidence = report.evidence[0].model_dump() | {"evidence_id": "log-2"}
    second_hypothesis = report.hypotheses[0].model_dump() | {
        "explanation": "A different environment configuration may cause the import failure.",
        "supporting_evidence_ids": ["log-2"],
    }
    validated = changed_report(
        report,
        evidence=[report.evidence[0], second_evidence],
        hypotheses=[report.hypotheses[0], second_hypothesis],
    )
    assert validate_report_evidence(validated) is None


def test_later_invalid_reference_still_fails(report: InvestigationReport) -> None:
    invalid_hypothesis = report.hypotheses[0].model_dump() | {
        "supporting_evidence_ids": ["missing"]
    }
    invalid = changed_report(report, hypotheses=[report.hypotheses[0], invalid_hypothesis])
    with pytest.raises(EvidenceValidationError, match="hypothesis 2.*missing"):
        validate_report_evidence(invalid)


def test_normalized_ids_do_not_hide_duplicates(report: InvestigationReport) -> None:
    duplicate = report.evidence[0].model_dump() | {"evidence_id": " log-1 "}
    with pytest.raises(EvidenceValidationError, match="duplicate evidence ID"):
        validate_report_evidence(changed_report(report, evidence=[report.evidence[0], duplicate]))


def test_reference_integrity_does_not_claim_factual_correctness(
    report: InvestigationReport,
) -> None:
    unrelated = report.hypotheses[0].model_dump() | {
        "explanation": "An unrelated explanation that the log does not establish."
    }
    assert validate_report_evidence(changed_report(report, hypotheses=[unrelated])) is None

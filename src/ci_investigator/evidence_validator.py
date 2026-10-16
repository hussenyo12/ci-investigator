"""Check status consistency and reference integrity, without judging hypothesis truth."""

from ci_investigator.schemas import InvestigationReport


class EvidenceValidationError(ValueError):
    """An investigation report has inconsistent status, hypotheses, or evidence references."""


def validate_report_evidence(report: InvestigationReport) -> None:
    """Check status consistency, records, references, and support for every diagnosis.

    A successful check means that the references resolve. It does not mean that
    evidence is authentic or that the referenced text proves a hypothesis.
    """
    evidence_ids: set[str] = set()
    for evidence in report.evidence:
        if evidence.evidence_id in evidence_ids:
            raise EvidenceValidationError(f"duplicate evidence ID: {evidence.evidence_id!r}")
        evidence_ids.add(evidence.evidence_id)

    for index, hypothesis in enumerate(report.hypotheses, start=1):
        references: set[str] = set()
        for evidence_id in hypothesis.supporting_evidence_ids:
            if evidence_id in references:
                raise EvidenceValidationError(
                    f"hypothesis {index} repeats evidence reference: {evidence_id!r}"
                )
            if evidence_id not in evidence_ids:
                raise EvidenceValidationError(
                    f"hypothesis {index} references unknown evidence ID: {evidence_id!r}"
                )
            references.add(evidence_id)
        if report.status == "diagnosed" and not references:
            raise EvidenceValidationError(
                f"diagnosed report hypothesis {index} requires at least one supporting evidence ID"
            )

    if report.status == "diagnosed" and not report.hypotheses:
        raise EvidenceValidationError("diagnosed report requires at least one hypothesis")
    if report.status == "insufficient_evidence" and report.hypotheses:
        raise EvidenceValidationError("insufficient_evidence report requires zero hypotheses")

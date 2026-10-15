"""Prepared evidence → untrusted draft → deterministic checks → trusted report."""

import json

from pydantic import ValidationError

from ci_investigator.diagnosis_prompt import (
    DiagnosisInputError,
    build_diagnosis_prompt,
    evidence_is_incomplete,
)
from ci_investigator.diagnosis_schemas import DiagnosisDraft
from ci_investigator.evidence_schemas import PreparedEvidencePackage
from ci_investigator.evidence_validator import EvidenceValidationError, validate_report_evidence
from ci_investigator.gemini_client import (
    MAX_RESPONSE_CHARS,
    DiagnosisClient,
    DiagnosisError,
    GeminiClient,
    InvalidDiagnosisResponse,
    UnusableModelResponse,
)
from ci_investigator.redactor import SanitizationError, SecretRedactor
from ci_investigator.schemas import Hypothesis, InvestigationReport

_REASONING_LIMITATION = (
    "AI hypotheses require human verification. Valid evidence references do not prove "
    "relevance or causal correctness; confidence labels are not calibrated probabilities."
)


def _pipeline_limitations(package: PreparedEvidencePackage) -> list[str]:
    limitations = [*package.limitations, _REASONING_LIMITATION]
    for warning in package.warnings:
        limitations.append(f"Preparation warning ({warning.code}): {warning.message}")
    if evidence_is_incomplete(package):
        limitations.append(
            "Available evidence is incomplete, truncated, or omitted; conclusions are provisional "
            "and high-confidence hypotheses are not accepted."
        )
    return list(dict.fromkeys(limitations))


def _abstain(package: PreparedEvidencePackage, *, has_failed_jobs: bool) -> InvestigationReport:
    report = InvestigationReport(
        workflow_run=package.workflow_run,
        evidence=package.evidence,
        status="insufficient_evidence",
        observed_failure_summary=(
            "No failed jobs to investigate; no AI diagnosis was requested."
            if not has_failed_jobs
            else "Prepared evidence is insufficient to diagnose the observed workflow failure."
        ),
        recommended_next_steps=[
            "Review workflow status and the selected attempt before requesting an investigation."
            if not has_failed_jobs
            else "Obtain informative failed-job output and relevant configuration, then prepare "
            "sanitized evidence again."
        ],
        limitations=_pipeline_limitations(package),
    )
    validate_report_evidence(report)
    return report


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-JSON numeric constant")


def validate_diagnosis_response(text: str, package: PreparedEvidencePackage) -> InvestigationReport:
    """Never trust SDK .parsed or a model's own claim that its citations are valid."""
    if not isinstance(text, str) or not text.strip():
        raise UnusableModelResponse("Gemini returned empty or unusable output.")
    if len(text) > MAX_RESPONSE_CHARS:
        raise UnusableModelResponse("Gemini output exceeded the response size limit.")
    try:
        payload = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        draft = DiagnosisDraft.model_validate(payload)
        # Best-effort output redaction, including secrets a model might generate.
        # Evidence IDs are checked as returned; trusted excerpts are never rewritten here.
        redactor = SecretRedactor()
        report = InvestigationReport(
            workflow_run=package.workflow_run,
            evidence=package.evidence,
            observed_failure_summary=redactor.redact(draft.observed_failure_summary).text,
            status=draft.status,
            hypotheses=[
                Hypothesis(
                    explanation=redactor.redact(item.explanation).text,
                    supporting_evidence_ids=item.supporting_evidence_ids,
                    confidence=item.confidence,
                    verification_steps=[
                        redactor.redact(step).text for step in item.verification_steps
                    ],
                )
                for item in draft.hypotheses
            ],
            recommended_next_steps=[
                redactor.redact(step).text for step in draft.recommended_next_steps
            ],
            limitations=list(
                dict.fromkeys(
                    [
                        *_pipeline_limitations(package),
                        *(redactor.redact(item).text for item in draft.limitations),
                    ]
                )
            ),
        )
        validate_report_evidence(report)
        for hypothesis in report.hypotheses:
            # Tentative hypotheses must also be grounded in this engine.
            if not hypothesis.supporting_evidence_ids:
                raise ValueError("Hypothesis has no support")
            if evidence_is_incomplete(package) and hypothesis.confidence == "high":
                raise ValueError("High confidence with incomplete context")
        return report
    except (ValueError, RecursionError, SanitizationError):
        raise InvalidDiagnosisResponse(
            "Gemini output failed schema, reference, or confidence validation; "
            "no diagnosis accepted."
        ) from None


def diagnose(
    package: PreparedEvidencePackage,
    *,
    has_failed_jobs: bool,
    client: DiagnosisClient | None = None,
    response_retries: int = 1,
) -> InvestigationReport:
    """Call only after external-transmission consent and upstream provenance checks.

    has_failed_jobs is a local collection gate, never additional model context.
    At most one fresh request can follow a complete but invalid draft. No rejected
    response is fed back, persisted or displayed. Service failures are not retried.
    """
    if type(response_retries) is not int or response_retries not in (0, 1):
        raise DiagnosisInputError("Diagnosis response retries must be zero or one.")
    if type(has_failed_jobs) is not bool:
        raise DiagnosisInputError("A trusted failed-job gate is required.")
    try:
        # Snapshot/revalidate mutable models before a client can observe them.
        package = PreparedEvidencePackage.model_validate(package.model_dump())
        if not has_failed_jobs or package.status == "insufficient_evidence" or not package.evidence:
            return _abstain(package, has_failed_jobs=has_failed_jobs)
        prompt = build_diagnosis_prompt(package)
    except (ValidationError, EvidenceValidationError, AttributeError):
        raise DiagnosisInputError("Invalid prepared input; diagnosis refused.") from None
    client = client if client is not None else GeminiClient()
    for attempt in range(response_retries + 1):
        try:
            try:
                text = client.generate(prompt)
            except DiagnosisError:
                raise
            except Exception:
                # Injected clients must not leak their request payload in errors either.
                raise DiagnosisError(
                    "Diagnosis client failed; no diagnosis was accepted."
                ) from None
            return validate_diagnosis_response(text, package)
        except InvalidDiagnosisResponse:
            if attempt == response_retries:
                raise InvalidDiagnosisResponse(
                    "Gemini returned invalid diagnosis output; bounded attempts exhausted."
                ) from None
    raise AssertionError("unreachable")

"""Successful-run completeness and strict diagnosis/abstention consistency."""

import json
from contextlib import nullcontext

import pytest
from diagnosis_fixtures import FakeModel, draft
from evidence_fixtures import collection
from github_fixtures import RUN_URL

from ci_investigator.cli import main
from ci_investigator.collection_schemas import CollectionWarning
from ci_investigator.diagnosis_engine import diagnose, validate_diagnosis_response
from ci_investigator.diagnosis_schemas import ValidationFailureReason
from ci_investigator.evidence_pipeline import prepare_evidence
from ci_investigator.evidence_validator import EvidenceValidationError, validate_report_evidence
from ci_investigator.gemini_client import InvalidDiagnosisResponse


def successful_collection():
    data = collection(
        "",
        files={
            ".github/workflows/ci.yml": "steps:\n  - run: python -m pytest\n",
            "tox.ini": None,
        },
    )
    data.outcome = "successful"
    data.workflow.conclusion = "success"
    data.failed_jobs = []
    data.failed_job_count = 0
    data.logs = []
    data.warnings = [
        CollectionWarning(code="no_failed_jobs", message="No failed jobs in selected attempt."),
        CollectionWarning(
            code="config_missing", message="Optional file absent.", source_identifier="tox.ini"
        ),
    ]
    data.limitations = ["Only the selected attempt was collected."]
    return data


def test_success_is_complete_without_hiding_warnings_or_limitations():
    data = successful_collection()
    package = prepare_evidence(data)
    assert not package.incomplete
    assert package.status == "insufficient_evidence" and package.evidence == []
    assert not package.logs_truncated and not package.evidence_omitted
    assert {
        "no_failed_jobs",
        "config_missing",
        "configuration_unavailable",
        "no_failure_detail",
    } <= {warning.code for warning in package.warnings}
    assert any(data.limitations[0] in limitation for limitation in package.limitations)


@pytest.mark.parametrize(
    "problem",
    [
        "unknown_warning",
        "retrieval_error",
        "truncation",
        "incomplete_attempt",
        "missing_failure_log",
    ],
)
def test_no_failure_observation_does_not_hide_substantive_problems(problem):
    data = successful_collection()
    if problem == "unknown_warning":
        data.warnings.append(CollectionWarning(code="future_warning", message="Unknown gap."))
    elif problem == "retrieval_error":
        data.configurations[0].availability = "unavailable"
        data.configurations[0].content = None
    elif problem == "truncation":
        data.configurations[0].truncated = True
    elif problem == "incomplete_attempt":
        data.outcome = "incomplete"
        data.workflow.status = "in_progress"
        data.workflow.conclusion = None
    else:
        failed = collection()
        data.failed_jobs = failed.failed_jobs
        data.failed_job_count = 1
    package = prepare_evidence(data)
    assert package.incomplete
    assert any(warning.code == "no_failed_jobs" for warning in package.warnings)
    if problem == "missing_failure_log":
        assert any(warning.code == "missing_log" for warning in package.warnings)


def test_no_failed_jobs_warning_alone_cannot_claim_a_completed_success():
    data = successful_collection()
    data.outcome = "failed"
    data.workflow.conclusion = "failure"
    assert prepare_evidence(data).incomplete
    data.outcome = "successful"
    assert prepare_evidence(data).incomplete  # Contradictory workflow metadata is not success.


def test_success_cli_preview_and_abstention_never_initialize_a_model(monkeypatch, capsys):
    data = successful_collection()
    monkeypatch.setattr("ci_investigator.cli.GitHubClient", nullcontext)
    monkeypatch.setattr("ci_investigator.cli.GitHubCollector.collect", lambda *a, **kw: data)

    def forbidden(*args, **kwargs):
        pytest.fail("A successful workflow must not initialize Gemini or ADC")

    monkeypatch.setattr("ci_investigator.diagnosis_engine.GeminiClient", forbidden)
    assert main([RUN_URL, "--preview-evidence"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["incomplete"] is False and preview["warnings"]
    assert main([RUN_URL, "--ai-diagnose", "--allow-external-ai"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "insufficient_evidence" and report["hypotheses"] == []
    assert report["observed_failure_summary"] == (
        "No failed jobs to investigate; no AI diagnosis was requested."
    )
    assert not any("conclusions are provisional" in item for item in report["limitations"])
    assert any("no_failed_jobs" in item for item in report["limitations"])


@pytest.mark.parametrize("status", ["diagnosed", "insufficient_evidence"])
@pytest.mark.parametrize("with_hypothesis", [False, True])
def test_status_hypothesis_combinations_are_consistent(report, status, with_hypothesis):
    report.status = status
    if not with_hypothesis:
        report.hypotheses = []
    if (status == "diagnosed") == with_hypothesis:
        validate_report_evidence(report)
    else:
        with pytest.raises(EvidenceValidationError):
            validate_report_evidence(report)


def test_contradictory_model_abstention_has_safe_status_code_and_bounded_retry():
    package = prepare_evidence(collection())
    payload = draft(package)
    payload["status"] = "insufficient_evidence"
    payload["hypotheses"][0]["explanation"] = "synthetic-private-model-value"
    response = json.dumps(payload)
    with pytest.raises(InvalidDiagnosisResponse) as direct:
        validate_diagnosis_response(response, package)
    assert direct.value.reason_code == ValidationFailureReason.STATUS_INCONSISTENT
    model = FakeModel(response, response, response)
    with pytest.raises(InvalidDiagnosisResponse) as caught:
        diagnose(package, has_failed_jobs=True, client=model)
    assert caught.value.reason_code == ValidationFailureReason.STATUS_INCONSISTENT
    assert caught.value.attempt_count == 2 and len(model.prompts) == 2
    assert "synthetic-private-model-value" not in str(caught.value)
    assert model.prompts[0] == model.prompts[1]
    payload["hypotheses"] = []
    recovered = FakeModel(response, json.dumps(payload))
    report = diagnose(package, has_failed_jobs=True, client=recovered)
    assert report.status == "insufficient_evidence" and report.hypotheses == []
    assert len(recovered.prompts) == 2

"""Offline reproduction of optional configuration causing false incompleteness."""

import json
from contextlib import nullcontext

import pytest
from diagnosis_fixtures import FakeModel, draft
from evidence_fixtures import collection
from github_fixtures import RUN_URL, job

from ci_investigator.cli import main
from ci_investigator.collection_schemas import CollectionResult, CollectionWarning
from ci_investigator.diagnosis_engine import diagnose, validate_diagnosis_response
from ci_investigator.diagnosis_prompt import PROMPT_VERSION
from ci_investigator.diagnosis_schemas import ValidationFailureReason
from ci_investigator.evidence_pipeline import prepare_evidence, validate_prepared_package
from ci_investigator.evidence_schemas import PreparationLimits
from ci_investigator.gemini_client import InvalidDiagnosisResponse
from ci_investigator.redactor import SanitizationError

OPTIONAL_MISSING = ("pyproject.toml", "requirements-dev.txt", "tox.ini", "setup.cfg")


def environment_failure() -> CollectionResult:
    """Synthetic analogue of the reported live case; no remote data is retrieved."""
    data = collection(
        "RuntimeError: DEMO_SERVICE_ENDPOINT environment variable is required\n",
        files={
            ".github/workflows/ci.yml": (
                "name: Demo\non: push\njobs:\n  demo:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: python demo.py\n"
            ),
            **dict.fromkeys(OPTIONAL_MISSING),
        },
    )
    # Match the collector's per-file warnings in addition to availability records.
    data.warnings = [
        CollectionWarning(
            code="config_missing",
            message="Optional configuration absent at run commit.",
            source_identifier=path,
        )
        for path in OPTIONAL_MISSING
    ]
    return data


def environment_draft(package) -> dict:
    payload = draft(package)
    payload["observed_failure_summary"] = "The process reports a required environment variable."
    hypothesis = payload["hypotheses"][0]
    hypothesis.update(
        explanation="DEMO_SERVICE_ENDPOINT appears unset or empty in the failing environment.",
        confidence="high",
        verification_steps=["Check that the failing step receives DEMO_SERVICE_ENDPOINT."],
    )
    return payload


def test_f008_absent_optional_files_keep_warnings_without_false_incompleteness():
    data = environment_failure()
    package = prepare_evidence(data)
    validate_prepared_package(package, data)
    assert package.status == "ready" and len(package.evidence) == 2
    assert not package.logs_truncated and not package.evidence_omitted
    assert not package.incomplete
    assert {
        warning.source_identifier
        for warning in package.warnings
        if warning.code == "config_missing"
    } == set(OPTIONAL_MISSING)
    assert (
        len(
            [warning for warning in package.warnings if warning.code == "configuration_unavailable"]
        )
        == 4
    )
    data.configurations = [file for file in data.configurations if file.content is not None]
    data.warnings = []
    assert package.evidence == prepare_evidence(data).evidence  # IDs/ranges/text are unchanged.


def test_f008_complete_environment_error_accepts_supported_high_confidence_offline():
    package = prepare_evidence(environment_failure())
    response = json.dumps(environment_draft(package))
    model = FakeModel(response, response)
    report = diagnose(package, has_failed_jobs=True, client=model)
    assert report.status == "diagnosed" and report.hypotheses[0].confidence == "high"
    assert len(model.prompts) == 1
    for warning in package.warnings:
        assert any(warning.message in limitation for limitation in report.limitations)
    context = json.loads(model.prompts[0].contents)["untrusted_evidence_data"]
    assert context["incomplete"] is False and context["warnings"]
    assert model.prompts[0].version == PROMPT_VERSION


@pytest.mark.parametrize("canonical_warning_source", [False, True])
def test_f008_missing_records_and_corresponding_warnings_are_both_incidental(
    canonical_warning_source,
):
    data = environment_failure()
    if canonical_warning_source:
        ids = {file.path: file.source_identifier for file in data.configurations}
        for warning in data.warnings:
            warning.source_identifier = ids[warning.source_identifier]
    assert not prepare_evidence(data).incomplete
    data.warnings = []
    assert not prepare_evidence(data).incomplete  # Availability records alone are also safe.


@pytest.mark.parametrize(
    "gap",
    [
        "missing_job_log",
        "truncated_log",
        "omitted_evidence",
        "incomplete_attempt",
        "failed_source_error",
        "unreadable_configuration",
        "missing_workflow",
        "referenced_optional_file",
        "unknown_warning",
        "decoding_loss",
    ],
)
def test_f008_substantive_gaps_still_reject_high_confidence(gap):
    data = environment_failure()
    limits = None
    if gap == "missing_job_log":
        data.failed_jobs.append(type(data.failed_jobs[0]).model_validate(job(8)))
        data.failed_job_count = data.job_count = 2
    elif gap == "truncated_log":
        data.logs[0].truncated = True
    elif gap == "omitted_evidence":
        limits = PreparationLimits(max_excerpts=1)
    elif gap == "incomplete_attempt":
        data.outcome = "incomplete"
        data.workflow.status = "in_progress"
        data.workflow.conclusion = None
    elif gap == "failed_source_error":
        data.warnings.append(
            CollectionWarning(
                code="rate_limited",
                message="Failed-source retrieval error.",
                source_identifier="job:7:log",
            )
        )
    elif gap == "unreadable_configuration":
        data.configurations[1].availability = "unavailable"
    elif gap == "missing_workflow":
        data.configurations[0].content = None
        data.configurations[0].availability = "missing"
    elif gap == "referenced_optional_file":
        data.configurations[0].content += "      - run: pip install -r requirements-dev.txt\n"
    elif gap == "unknown_warning":
        data.warnings.append(
            CollectionWarning(
                code="future_warning",
                message="Unrecognized retrieval condition.",
                source_identifier="pyproject.toml",
            )
        )
    else:
        data.logs[0].decoding_replacements = True
    package = prepare_evidence(data, limits=limits)
    assert package.incomplete and package.status == "ready"
    if gap == "missing_job_log":
        assert any(warning.code == "missing_log" for warning in package.warnings)
    if gap == "omitted_evidence":
        assert package.evidence_omitted
    with pytest.raises(InvalidDiagnosisResponse) as caught:
        validate_diagnosis_response(json.dumps(environment_draft(package)), package)
    assert caught.value.reason_code == ValidationFailureReason.HIGH_CONFIDENCE_INCOMPLETE
    payload = environment_draft(package)
    payload["hypotheses"][0]["confidence"] = "medium"
    report = validate_diagnosis_response(json.dumps(payload), package)
    assert any("provisional" in limitation for limitation in report.limitations)


def test_f008_missing_all_job_logs_abstains_without_model_call():
    data = environment_failure()
    data.logs = []
    package = prepare_evidence(data)
    assert package.incomplete and package.status == "insufficient_evidence"
    model = FakeModel()
    assert diagnose(package, has_failed_jobs=True, client=model).status == "insufficient_evidence"
    assert not model.prompts


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("schema", "SCHEMA_INVALID"),
        ("json", "SCHEMA_INVALID"),
        ("unknown_reference", "UNKNOWN_EVIDENCE_REFERENCE"),
        ("duplicate_reference", "DUPLICATE_EVIDENCE_REFERENCE"),
        ("missing_reference", "MISSING_EVIDENCE_REFERENCE"),
        ("status", "STATUS_INCONSISTENT"),
        ("incomplete", "HIGH_CONFIDENCE_INCOMPLETE"),
        ("sanitization", "OTHER_VALIDATION_FAILURE"),
    ],
)
def test_f008_failure_categories_and_attempts_never_include_model_values(
    failure, reason, capsys, monkeypatch
):
    package = prepare_evidence(environment_failure())
    payload = environment_draft(package)
    poison = "synthetic-private-model-value"
    payload["observed_failure_summary"] = poison
    if failure == "schema":
        payload["hypotheses"][0]["confidence"] = poison
    elif failure == "unknown_reference":
        payload["hypotheses"][0]["supporting_evidence_ids"] = [poison]
    elif failure == "duplicate_reference":
        payload["hypotheses"][0]["supporting_evidence_ids"] *= 2
    elif failure == "missing_reference":
        payload["hypotheses"][0]["supporting_evidence_ids"] = []
    elif failure == "status":
        payload["hypotheses"] = []
    elif failure == "incomplete":
        package.incomplete = True
    elif failure == "sanitization":

        def refuse_redaction(self, text):
            raise SanitizationError(poison)

        monkeypatch.setattr(
            "ci_investigator.diagnosis_engine.SecretRedactor.redact", refuse_redaction
        )
    response = poison + "{bad json" if failure == "json" else json.dumps(payload)
    model = FakeModel(response, response, response)
    with pytest.raises(InvalidDiagnosisResponse) as caught:
        diagnose(package, has_failed_jobs=True, client=model)
    error = caught.value
    assert error.reason_code.value == reason and error.attempt_count == 2
    assert len(model.prompts) == 2 and model.prompts[0] == model.prompts[1]
    assert poison not in str(error) + repr(error) + repr(vars(error))
    assert response not in str(error)
    assert error.__suppress_context__
    assert not capsys.readouterr().out


def test_f008_zero_retry_and_recovery_keep_bounded_behavior():
    package = prepare_evidence(environment_failure())
    valid = json.dumps(environment_draft(package))
    model = FakeModel("malformed", valid)
    with pytest.raises(InvalidDiagnosisResponse) as caught:
        diagnose(package, has_failed_jobs=True, client=model, response_retries=0)
    assert caught.value.attempt_count == 1 and len(model.prompts) == 1
    recovered = FakeModel("malformed", valid)
    assert diagnose(package, has_failed_jobs=True, client=recovered).status == "diagnosed"
    assert len(recovered.prompts) == 2
    payload = environment_draft(package)
    payload["hypotheses"][0]["supporting_evidence_ids"] = ["unknown"]
    changed_failure = FakeModel("malformed", json.dumps(payload))
    with pytest.raises(InvalidDiagnosisResponse) as caught:
        diagnose(package, has_failed_jobs=True, client=changed_failure)
    assert caught.value.reason_code == ValidationFailureReason.UNKNOWN_EVIDENCE_REFERENCE
    assert caught.value.attempt_count == 2


def test_f008_failure_exception_whitelists_reason_and_attempt_values():
    error = InvalidDiagnosisResponse("synthetic-private-value", attempt_count=999)
    assert error.reason_code == ValidationFailureReason.OTHER_VALIDATION_FAILURE
    assert error.attempt_count == 1 and "synthetic-private-value" not in str(error)


@pytest.mark.parametrize("mode", ["metadata", "preview", "diagnosis", "invalid"])
def test_f008_cli_keeps_modes_and_exposes_only_safe_failure_metadata(monkeypatch, capsys, mode):
    data = environment_failure()
    monkeypatch.setattr("ci_investigator.cli.GitHubCollector.collect", lambda *args, **kwargs: data)
    # All collection is injected; the HTTP client is never constructed.
    monkeypatch.setattr("ci_investigator.cli.GitHubClient", nullcontext)
    package = prepare_evidence(data)
    payload = environment_draft(package)
    poison = "synthetic-private-cli-model-value"
    if mode == "invalid":
        payload["hypotheses"][0]["supporting_evidence_ids"] = [poison]
    response = json.dumps(payload)
    model = FakeModel(response, response)
    monkeypatch.setattr("ci_investigator.diagnosis_engine.GeminiClient", lambda: model)
    flags = (
        []
        if mode == "metadata"
        else ["--preview-evidence"]
        if mode == "preview"
        else ["--ai-diagnose", "--allow-external-ai"]
    )
    assert main([RUN_URL, *flags]) == (1 if mode == "invalid" else 0)
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert not captured.err and poison not in captured.out
    if mode == "invalid":
        assert result == {
            "error": "Gemini diagnosis output was rejected.",
            "reason_code": "UNKNOWN_EVIDENCE_REFERENCE",
            "attempt_count": 2,
        }
        assert "RuntimeError" not in captured.out and "untrusted_evidence_data" not in captured.out
        assert len(model.prompts) == 2
    elif mode == "diagnosis":
        assert result["hypotheses"][0]["confidence"] == "high" and len(model.prompts) == 1
    else:
        assert not model.prompts
        if mode == "preview":
            assert result["incomplete"] is False and result["warnings"]

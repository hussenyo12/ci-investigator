"""Existing collection stays local unless both explicit external-AI flags are set."""

import json
import runpy
from pathlib import Path

import httpx
import pytest
from diagnosis_fixtures import FakeModel, draft
from github_fixtures import API_REPO, RUN_URL, GitHubScenario, response

from ci_investigator.cli import main
from ci_investigator.diagnosis_engine import diagnose
from ci_investigator.evidence_provenance import EvidenceProvenanceError
from ci_investigator.gemini_client import DiagnosisError
from ci_investigator.github_client import GitHubClient


@pytest.fixture
def scenario(monkeypatch):
    scenario = GitHubScenario()
    scenario.overrides[f"{API_REPO}/actions/jobs/7/logs"] = response(
        content=b"ModuleNotFoundError: No module named 'yaml'\n"
    )
    monkeypatch.setattr(
        "ci_investigator.cli.GitHubClient",
        lambda: GitHubClient(token="", transport=httpx.MockTransport(scenario)),
    )
    return scenario


@pytest.mark.parametrize(
    "flags",
    [
        ["--ai-diagnose"],
        ["--allow-external-ai"],
        ["--ai-diagnose", "--allow-external-ai", "--preview-evidence"],
        ["--ai-diagnose", "--allow-external-ai", "--metadata-only"],
    ],
)
def test_consent_and_mode_errors_happen_before_any_collection(monkeypatch, capsys, flags):
    def forbidden():
        pytest.fail("No collection is authorized by an invalid invocation")

    monkeypatch.setattr("ci_investigator.cli.GitHubClient", forbidden)
    with pytest.raises(SystemExit) as caught:
        main([RUN_URL, *flags])
    assert caught.value.code == 2 and "error" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flags", [[], ["--preview-evidence"], ["--preview-evidence", "--metadata-only"]]
)
def test_original_modes_do_not_initialize_gemini(scenario, monkeypatch, capsys, flags):
    def forbidden():
        pytest.fail("Original CLI modes must not initialize Gemini")

    monkeypatch.setattr("ci_investigator.diagnosis_engine.GeminiClient", forbidden)
    assert main([RUN_URL, *flags]) == 0
    result = json.loads(capsys.readouterr().out)
    assert "outcome" in result or result["evidence"]


def test_cli_full_fake_diagnosis_outputs_report_and_sanitized_evidence(
    scenario, monkeypatch, capsys
):
    scenario.overrides[f"{API_REPO}/actions/jobs/7/logs"] = response(
        content=b"AssertionError: expected two; Authorization: Bearer synthetic-cli-secret\n"
    )
    models = []

    def investigate(package, *, has_failed_jobs):
        model = FakeModel(json.dumps(draft(package)))
        models.append(model)
        return diagnose(package, has_failed_jobs=has_failed_jobs, client=model)

    monkeypatch.setattr("ci_investigator.cli.diagnose", investigate)
    assert main([RUN_URL, "--ai-diagnose", "--allow-external-ai"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["status"] == "diagnosed"
    assert report["workflow_run"]["url"] == RUN_URL
    assert report["evidence"] and report["hypotheses"] and report["limitations"]
    assert report["recommended_next_steps"]
    assert len(models[0].prompts) == 1
    assert "synthetic-cli-secret" not in output + models[0].prompts[0].contents


@pytest.mark.parametrize("kind", ["successful", "insufficient"])
def test_cli_skips_model_for_success_or_insufficient_evidence(scenario, monkeypatch, capsys, kind):
    if kind == "successful":
        workflow = scenario.run.copy()
        workflow["conclusion"] = "success"
        scenario.overrides[f"{API_REPO}/actions/runs/42"] = response(data=workflow)
        scenario.overrides[f"{API_REPO}/actions/runs/42/attempts/2/jobs"] = response(
            data={"total_count": 0, "jobs": []}
        )
    else:
        scenario.overrides[f"{API_REPO}/actions/jobs/7/logs"] = response(
            content=b"Process completed with exit code 1\n"
        )

    def forbidden():
        pytest.fail("Local abstention must not initialize Gemini")

    monkeypatch.setattr("ci_investigator.diagnosis_engine.GeminiClient", forbidden)
    assert main([RUN_URL, "--ai-diagnose", "--allow-external-ai"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "insufficient_evidence" and not report["hypotheses"]


def test_cli_revalidates_provenance_before_transmission(scenario, monkeypatch, capsys):
    def invalid(*args):
        raise EvidenceProvenanceError("SECRET source content")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid provenance must stop diagnosis")

    monkeypatch.setattr("ci_investigator.cli.validate_prepared_package", invalid)
    monkeypatch.setattr("ci_investigator.cli.diagnose", forbidden)
    assert main([RUN_URL, "--ai-diagnose", "--allow-external-ai"]) == 1
    output = capsys.readouterr().out
    assert "SECRET" not in output and "error" in json.loads(output)


def test_cli_safe_error_without_report_or_traceback(scenario, monkeypatch, capsys):
    def failed(*args, **kwargs):
        raise DiagnosisError("Vertex AI quota or rate limit reached; retry later.")

    monkeypatch.setattr("ci_investigator.cli.diagnose", failed)
    assert main([RUN_URL, "--ai-diagnose", "--allow-external-ai"]) == 1
    captured = capsys.readouterr()
    assert set(json.loads(captured.out)) == {"error"}
    assert "quota" in captured.out and not captured.err


def test_synthetic_live_smoke_requires_opt_in_and_has_exactly_one_generation(monkeypatch, capsys):
    namespace = runpy.run_path(str(Path(__file__).parents[1] / "scripts/gemini_smoke_test.py"))
    package = namespace["prepare_evidence"](namespace["synthetic_collection"]())
    model = FakeModel(json.dumps(draft(package)))
    monkeypatch.setattr("ci_investigator.diagnosis_engine.GeminiClient", lambda: model)
    with pytest.raises(SystemExit) as caught:
        namespace["main"]([])
    assert caught.value.code == 2 and not model.prompts
    capsys.readouterr()
    assert namespace["main"](["--allow-external-ai"]) == 0
    assert len(model.prompts) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "diagnosed"
    broken = FakeModel("malformed", json.dumps(draft(package)))
    monkeypatch.setattr("ci_investigator.diagnosis_engine.GeminiClient", lambda: broken)
    assert namespace["main"](["--allow-external-ai"]) == 1
    assert len(broken.prompts) == 1
    assert "error" in json.loads(capsys.readouterr().out)

"""SDK boundary tests: real SDK schemas and mocked transport, never real ADC/network."""

import json
from types import SimpleNamespace

import httpx
import pytest
from diagnosis_fixtures import draft
from evidence_fixtures import collection
from google import genai
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.genai import errors, types
from google.oauth2.credentials import Credentials

from ci_investigator.diagnosis_engine import diagnose
from ci_investigator.diagnosis_prompt import build_diagnosis_prompt
from ci_investigator.diagnosis_schemas import DiagnosisDraft
from ci_investigator.evidence_pipeline import prepare_evidence
from ci_investigator.gemini_client import (
    MAX_RESPONSE_CHARS,
    DiagnosisError,
    GeminiClient,
    GeminiConfig,
    UnusableModelResponse,
)


@pytest.fixture(autouse=True)
def clean_ai_environment(monkeypatch):
    for name in (
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "VERTEX_AI_MODEL",
        "VERTEX_AI_TIMEOUT_MS",
        "VERTEX_AI_MAX_OUTPUT_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)


def response(text: str = "{}", *, finish: str = "STOP") -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(parts=[types.Part(text=text)]), finish_reason=finish
            )
        ]
    )


class FakeSDK:
    def __init__(self, result):
        self.result = result
        self.models = SimpleNamespace(generate_content=self.generate)
        self.calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_default_and_environment_configuration_is_bounded(monkeypatch) -> None:
    assert GeminiConfig.from_environment() == GeminiConfig()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "another-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("VERTEX_AI_MODEL", "gemini-2.5-flash-lite")
    monkeypatch.setenv("VERTEX_AI_TIMEOUT_MS", "5000")
    monkeypatch.setenv("VERTEX_AI_MAX_OUTPUT_TOKENS", "1024")
    config = GeminiConfig.from_environment()
    assert (config.project, config.location, config.model) == (
        "another-project",
        "us-central1",
        "gemini-2.5-flash-lite",
    )
    assert config.timeout_ms == 5000 and config.max_output_tokens == 1024
    for value in ("0", "120001", "not-a-timeout"):
        monkeypatch.setenv("VERTEX_AI_TIMEOUT_MS", value)
        with pytest.raises(DiagnosisError) as caught:
            GeminiConfig.from_environment()
        assert value not in str(caught.value)


def test_vertex_adc_schema_and_generation_options(monkeypatch) -> None:
    sdk = FakeSDK(response())
    constructions = []

    def factory(**kwargs):
        constructions.append(kwargs)
        return sdk

    monkeypatch.setattr("ci_investigator.gemini_client.genai.Client", factory)
    # Implicit API keys must never be forwarded; explicit project/location chooses ADC.
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-key-not-for-use")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key-not-for-use")
    prompt = build_diagnosis_prompt(prepare_evidence(collection()))
    assert GeminiClient().generate(prompt) == "{}"
    assert sdk.closed and len(sdk.calls) == 1 and len(constructions) == 1
    options = constructions[0]
    assert options["vertexai"] is True
    assert options["project"] == "drivernets" and options["location"] == "global"
    assert "api_key" not in options and "credentials" not in options
    http = options["http_options"]
    assert http.timeout == 60_000 and http.retry_options.attempts == 1
    assert http.api_version == "v1" and http.base_url == "https://aiplatform.googleapis.com"
    assert http.client_args["trust_env"] is False
    call = sdk.calls[0]
    assert call["model"] == "gemini-2.5-flash"
    assert call["contents"] == prompt.contents
    config = call["config"]
    assert config.system_instruction == prompt.system_instruction
    assert config.response_mime_type == "application/json"
    assert config.response_schema is DiagnosisDraft
    assert config.temperature == 0.1 and config.max_output_tokens == 4096
    assert config.candidate_count == 1 and config.tools == []
    assert config.automatic_function_calling.disable is True


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (DefaultCredentialsError("SECRET-ADC"), "ADC authentication failed"),
        (RefreshError("SECRET-REFRESH"), "ADC authentication failed"),
        (errors.ClientError(401, {"error": {"message": "SECRET-401"}}), "authentication"),
        (errors.ClientError(403, {"error": {"message": "SECRET-403"}}), "permission denied"),
        (errors.ClientError(404, {"error": {"message": "SECRET-404"}}), "model unavailable"),
        (errors.ClientError(429, {"error": {"message": "SECRET-429"}}), "quota or rate limit"),
        (errors.ServerError(503, {"error": {"message": "SECRET-503"}}), "unavailable"),
        (httpx.ReadTimeout("SECRET-TIMEOUT"), "timed out"),
        (httpx.ConnectError("SECRET-URL"), "network"),
        (RuntimeError("SECRET-REQUEST"), "Unexpected Gemini SDK failure"),
    ],
)
def test_error_translation_is_non_sensitive_and_service_errors_are_not_retried(
    monkeypatch, failure, message
) -> None:
    sdk = FakeSDK(failure)
    monkeypatch.setattr("ci_investigator.gemini_client.genai.Client", lambda **kwargs: sdk)
    package = prepare_evidence(collection())
    with pytest.raises(DiagnosisError, match=message) as caught:
        diagnose(package, has_failed_jobs=True, client=GeminiClient())
    assert "SECRET" not in str(caught.value)
    assert len(sdk.calls) == 1 and sdk.closed
    assert caught.value.__suppress_context__


def test_adc_constructor_errors_are_also_sanitized(monkeypatch) -> None:
    def missing_adc(**kwargs):
        raise DefaultCredentialsError("SECRET credential path")

    monkeypatch.setattr("ci_investigator.gemini_client.genai.Client", missing_adc)
    with pytest.raises(DiagnosisError, match="ADC authentication failed") as caught:
        GeminiClient().generate(build_diagnosis_prompt(prepare_evidence(collection())))
    assert "SECRET" not in str(caught.value)


def test_credential_file_configuration_is_refused_without_opening_it(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/SECRET/account.json")
    with pytest.raises(DiagnosisError, match="Credential-file authentication") as caught:
        GeminiClient().generate(build_diagnosis_prompt(prepare_evidence(collection())))
    assert "/SECRET" not in str(caught.value)


@pytest.mark.parametrize(
    "result",
    [
        types.GenerateContentResponse(),
        response(""),
        response("  "),
        response("{}", finish="MAX_TOKENS"),
        response("{}", finish="SAFETY"),
        response("{}", finish="RECITATION"),
        response("{}", finish="OTHER"),
        response("x" * (MAX_RESPONSE_CHARS + 1)),
        types.GenerateContentResponse(prompt_feedback={"block_reason": "SAFETY"}),
        types.GenerateContentResponse(candidates=[types.Candidate(finish_reason="STOP")]),
        types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    finish_reason="STOP",
                    content=types.Content(
                        parts=[
                            types.Part(function_call=types.FunctionCall(name="execute", args={}))
                        ]
                    ),
                )
            ]
        ),
    ],
)
def test_empty_blocked_and_incomplete_sdk_responses_are_rejected(monkeypatch, result) -> None:
    sdk = FakeSDK(result)
    monkeypatch.setattr("ci_investigator.gemini_client.genai.Client", lambda **kwargs: sdk)
    with pytest.raises(UnusableModelResponse):
        GeminiClient().generate(build_diagnosis_prompt(prepare_evidence(collection())))
    assert len(sdk.calls) == 1 and sdk.closed


def test_mixed_text_and_executable_content_is_rejected_without_execution(monkeypatch):
    result = response("{}")
    result.candidates[0].content.parts[0].executable_code = types.ExecutableCode(
        language="PYTHON", code="raise RuntimeError('must never execute')"
    )
    sdk = FakeSDK(result)
    monkeypatch.setattr("ci_investigator.gemini_client.genai.Client", lambda **kwargs: sdk)
    with pytest.raises(UnusableModelResponse):
        GeminiClient().generate(build_diagnosis_prompt(prepare_evidence(collection())))
    assert len(sdk.calls) == 1 and sdk.closed


@pytest.mark.parametrize("http_status", [200, 429])
def test_actual_sdk_serialization_adc_and_retry_count_with_mock_http(monkeypatch, http_status):
    """Exercise SDK request construction, not only our constructor arguments."""
    package = prepare_evidence(collection())
    seen = []
    adc_calls = []
    credentials = Credentials(token="synthetic-adc-token")

    def adc(**kwargs):
        adc_calls.append(kwargs)
        return credentials, "ignored-adc-project"

    def handle(request):
        seen.append(request)
        if http_status != 200:
            return httpx.Response(http_status, json={"error": {"message": "SECRET quota data"}})
        return httpx.Response(
            200,
            json=response(json.dumps(draft(package))).model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
        )

    original_client = genai.Client

    def factory(**kwargs):
        kwargs["http_options"].client_args["transport"] = httpx.MockTransport(handle)
        return original_client(**kwargs)

    monkeypatch.setattr("google.auth.default", adc)
    monkeypatch.setattr("ci_investigator.gemini_client.genai.Client", factory)
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-api-key-must-be-ignored")
    monkeypatch.setenv("GOOGLE_VERTEX_BASE_URL", "https://untrusted.invalid")
    if http_status == 200:
        report = diagnose(package, has_failed_jobs=True, client=GeminiClient())
        assert report.status == "diagnosed" and report.evidence == package.evidence
    else:
        with pytest.raises(DiagnosisError, match="quota"):
            diagnose(package, has_failed_jobs=True, client=GeminiClient())
    assert len(seen) == 1 and len(adc_calls) == 1
    request = seen[0]
    assert request.url.host == "aiplatform.googleapis.com"
    assert "/v1/projects/drivernets/locations/global/" in request.url.path
    assert request.headers["authorization"] == "Bearer synthetic-adc-token"
    assert "x-goog-api-key" not in request.headers
    body = json.loads(request.content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    schema = body["generationConfig"]["responseSchema"]
    assert set(schema["properties"]) == set(DiagnosisDraft.model_fields)
    assert "workflow_run" not in schema["properties"]
    assert "untrusted_evidence_data" in body["contents"][0]["parts"][0]["text"]
    assert body["systemInstruction"]["parts"][0]["text"].startswith("You are a cautious")

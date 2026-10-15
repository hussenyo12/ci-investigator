"""Single-request Vertex AI adapter using ADC; no prompts/responses are logged."""

import os
from typing import Annotated, Protocol

import httpx
from google import genai
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.auth.exceptions import TransportError as AuthTransportError
from google.genai import errors, types
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ci_investigator.diagnosis_prompt import DiagnosisPrompt
from ci_investigator.diagnosis_schemas import DiagnosisDraft

MAX_RESPONSE_CHARS = 32_000


class DiagnosisError(RuntimeError):
    """Only application-authored, value-free messages may cross this boundary."""


class InvalidDiagnosisResponse(DiagnosisError):
    """A complete text response failed local validation; one fresh attempt is safe."""


class UnusableModelResponse(DiagnosisError):
    """Empty, blocked, refused or unfinished output; do not retry automatically."""


class GeminiConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    project: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")] = (
        "drivernets"
    )
    location: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")] = "global"
    model: Annotated[str, StringConstraints(pattern=r"^gemini-[a-zA-Z0-9._-]{1,100}$")] = (
        "gemini-2.5-flash"
    )
    timeout_ms: Annotated[int, Field(ge=1000, le=120_000)] = 60_000
    max_output_tokens: Annotated[int, Field(ge=512, le=8192)] = 4096

    @classmethod
    def from_environment(cls) -> "GeminiConfig":
        try:
            return cls(
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "drivernets"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
                model=os.environ.get("VERTEX_AI_MODEL", "gemini-2.5-flash"),
                timeout_ms=int(os.environ.get("VERTEX_AI_TIMEOUT_MS", "60000")),
                max_output_tokens=int(os.environ.get("VERTEX_AI_MAX_OUTPUT_TOKENS", "4096")),
            )
        except (ValueError, ValidationError):
            raise DiagnosisError(
                "Invalid Vertex AI configuration; check environment settings."
            ) from None


class DiagnosisClient(Protocol):
    def generate(self, prompt: DiagnosisPrompt) -> str:
        """Return complete JSON text or raise a non-sensitive DiagnosisError."""
        ...


class GeminiClient:
    def __init__(self, config: GeminiConfig | None = None) -> None:
        self.config = config or GeminiConfig.from_environment()

    def generate(self, prompt: DiagnosisPrompt) -> str:
        # This MVP deliberately uses local/attached ADC, never credential-file configuration.
        if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            raise DiagnosisError(
                "Credential-file authentication is disabled. Unset GOOGLE_APPLICATION_CREDENTIALS "
                "and configure ADC with gcloud auth application-default login, or use attached ADC."
            )
        config = self.config
        endpoint = (
            "https://aiplatform.googleapis.com"
            if config.location == "global"
            else f"https://{config.location}-aiplatform.googleapis.com"
        )
        try:
            with genai.Client(
                vertexai=True,
                project=config.project,
                location=config.location,
                http_options=types.HttpOptions(
                    api_version="v1",
                    base_url=endpoint,
                    timeout=config.timeout_ms,
                    retry_options=types.HttpRetryOptions(attempts=1),
                    client_args={"trust_env": False},
                ),
            ) as client:
                response = client.models.generate_content(
                    model=config.model,
                    contents=prompt.contents,
                    config=types.GenerateContentConfig(
                        system_instruction=prompt.system_instruction,
                        response_mime_type="application/json",
                        response_schema=DiagnosisDraft,
                        temperature=0.1,
                        max_output_tokens=config.max_output_tokens,
                        candidate_count=1,
                        tools=[],
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
                    ),
                )
                return _complete_text(response)
        except DiagnosisError:
            raise
        except (DefaultCredentialsError, RefreshError):
            raise DiagnosisError(
                "ADC authentication failed. Run gcloud auth application-default login "
                "or check the attached identity."
            ) from None
        except (httpx.TimeoutException, TimeoutError):
            raise DiagnosisError(
                "Vertex AI request timed out; no diagnosis was accepted."
            ) from None
        except (httpx.TransportError, AuthTransportError):
            raise DiagnosisError("Vertex AI network or authentication transport failed.") from None
        except errors.APIError as error:
            messages = {
                400: "Vertex AI rejected the request or model configuration.",
                401: "ADC authentication failed; refresh Application Default Credentials.",
                403: "Vertex AI permission denied; check project access and API enablement.",
                404: "Vertex AI model unavailable; check the model and location.",
                408: "Vertex AI request timed out; no diagnosis was accepted.",
                429: "Vertex AI quota or rate limit reached; retry later.",
                503: "Vertex AI model service is unavailable; retry later.",
                504: "Vertex AI request timed out; no diagnosis was accepted.",
            }
            raise DiagnosisError(
                messages.get(error.code, "Vertex AI service request failed; no diagnosis accepted.")
            ) from None
        except Exception:
            # SDK validation/transport errors may embed prompts, signed URLs or response bodies.
            raise DiagnosisError(
                "Unexpected Gemini SDK failure; no diagnosis was accepted."
            ) from None


def _complete_text(response: types.GenerateContentResponse) -> str:
    feedback = response.prompt_feedback
    if feedback and feedback.block_reason not in (None, "BLOCKED_REASON_UNSPECIFIED"):
        raise UnusableModelResponse("Gemini blocked the request; no diagnosis was accepted.")
    if not response.candidates or len(response.candidates) != 1:
        raise UnusableModelResponse("Gemini returned no usable candidate.")
    candidate = response.candidates[0]
    if candidate.finish_reason != "STOP":
        raise UnusableModelResponse(
            "Gemini output was refused or incomplete; no diagnosis accepted."
        )
    if not candidate.content or not candidate.content.parts:
        raise UnusableModelResponse("Gemini returned empty output.")
    parts = candidate.content.parts
    if any(
        part.text is None
        or part.thought
        or set(part.model_dump(exclude_none=True)) - {"text", "thought", "thought_signature"}
        for part in parts
    ):
        raise UnusableModelResponse("Gemini returned unexpected non-diagnosis content.")
    if sum(len(part.text) for part in parts) > MAX_RESPONSE_CHARS:
        raise UnusableModelResponse("Gemini output exceeded the response size limit.")
    text = "".join(part.text for part in parts)
    if not text.strip():
        raise UnusableModelResponse("Gemini returned empty output.")
    return text

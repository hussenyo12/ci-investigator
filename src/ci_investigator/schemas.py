"""Report shape validation; evidence links are checked separately."""

from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveInteger = Annotated[int, Field(strict=True, gt=0)]
GitHubOwner = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=39,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$",
    ),
]
GitHubRepository = Annotated[
    str, StringConstraints(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
]


def _require_evidence_text(value: str) -> str:
    if not value.strip():
        raise ValueError("evidence text must contain non-whitespace characters")
    return value


EvidenceText = Annotated[str, AfterValidator(_require_evidence_text)]


class _Model(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", validate_assignment=True)


class WorkflowRunReference(_Model):
    """A public GitHub run identity with an exact, canonical URL."""

    owner: GitHubOwner
    repo: GitHubRepository
    run_id: PositiveInteger
    url: NonEmptyText

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.repo in {".", ".."}:
            raise ValueError("repository name cannot be '.' or '..'")
        expected = f"https://github.com/{self.owner}/{self.repo}/actions/runs/{self.run_id}"
        if self.url != expected:
            raise ValueError("workflow run URL must be canonical and match owner, repo, and run_id")
        return self


class Evidence(_Model):
    """A source excerpt using inclusive, one-based original line numbers.

    Evidence IDs must be unique within a report, as checked by the reference validator.
    Text preserves indentation and newlines; shape validation does not authenticate it.
    """

    evidence_id: NonEmptyText
    source_type: Literal["log", "config"]
    source_identifier: NonEmptyText
    start_line: PositiveInteger
    end_line: PositiveInteger
    text: EvidenceText

    @model_validator(mode="after")
    def validate_line_range(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class Hypothesis(_Model):
    """A possible explanation; confidence is qualitative, never a probability."""

    explanation: NonEmptyText
    supporting_evidence_ids: list[NonEmptyText]
    confidence: Literal["low", "medium", "high"]
    verification_steps: list[NonEmptyText] = Field(min_length=1)


class InvestigationReport(_Model):
    """A report may explicitly decline to diagnose when evidence is insufficient."""

    workflow_run: WorkflowRunReference
    observed_failure_summary: NonEmptyText
    evidence: list[Evidence] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    recommended_next_steps: list[NonEmptyText] = Field(min_length=1)
    limitations: list[NonEmptyText] = Field(default_factory=list)
    status: Literal["diagnosed", "insufficient_evidence"]

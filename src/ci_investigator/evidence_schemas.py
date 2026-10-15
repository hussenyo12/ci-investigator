"""Preparation metadata; excerpts always reuse the foundation Evidence model."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ci_investigator.collection_schemas import CommitSHA
from ci_investigator.redactor import RedactionCategory
from ci_investigator.schemas import Evidence, NonEmptyText, PositiveInteger, WorkflowRunReference


class PreparationLimits(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    max_excerpts: Annotated[int, Field(gt=0, le=256)] = 24
    max_excerpt_chars: Annotated[int, Field(gt=0, le=32_000)] = 2_000
    max_total_chars: Annotated[int, Field(gt=0, le=1_000_000)] = 30_000
    context_before: Annotated[int, Field(ge=0, le=5)] = 5
    context_after: Annotated[int, Field(ge=0, le=5)] = 5


class _PreparedModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class RedactionSummary(_PreparedModel):
    counts: dict[RedactionCategory, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)


class PreparationWarning(_PreparedModel):
    code: NonEmptyText
    message: NonEmptyText
    source_identifier: NonEmptyText | None = None


class PreparedSourceMetadata(_PreparedModel):
    source_type: Literal["log", "config"]
    source_identifier: NonEmptyText
    line_count: Annotated[int, Field(ge=0)]
    commit_sha: CommitSHA | None = None
    truncated: bool = False


class PreparedEvidencePackage(_PreparedModel):
    workflow_run: WorkflowRunReference
    selected_attempt: PositiveInteger | None
    commit_sha: CommitSHA | None
    evidence: list[Evidence] = Field(default_factory=list)
    status: Literal["ready", "insufficient_evidence"]
    redaction_summary: RedactionSummary
    sources: list[PreparedSourceMetadata] = Field(default_factory=list)
    warnings: list[PreparationWarning] = Field(default_factory=list)
    limitations: list[NonEmptyText] = Field(default_factory=list)
    incomplete: bool = False
    logs_truncated: bool = False
    evidence_omitted: bool = False

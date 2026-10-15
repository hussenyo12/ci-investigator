"""Bounded, non-authoritative model output; never accepts evidence records."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

DraftText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
EvidenceID = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class HypothesisDraft(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    explanation: DraftText
    supporting_evidence_ids: list[EvidenceID] = Field(max_length=64)
    confidence: Literal["low", "medium", "high"]
    verification_steps: list[DraftText] = Field(min_length=1, max_length=8)


class DiagnosisDraft(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    observed_failure_summary: DraftText
    status: Literal["diagnosed", "insufficient_evidence"]
    hypotheses: list[HypothesisDraft] = Field(max_length=5)
    recommended_next_steps: list[DraftText] = Field(min_length=1, max_length=8)
    limitations: list[DraftText] = Field(max_length=12)

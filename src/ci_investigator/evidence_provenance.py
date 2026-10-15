"""Exact sanitized-source mapping; this does not establish truth or causality."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from ci_investigator.schemas import Evidence


class EvidenceProvenanceError(ValueError):
    """Evidence disagrees with the authoritative sanitized registry; never echo text."""


@dataclass(frozen=True)
class SanitizedSource:
    """Internal bounded source registry. Not part of prepared-package serialization."""

    source_identifier: str
    source_type: Literal["log", "config"]
    lines: tuple[str, ...] = field(repr=False)
    path: str | None = None
    commit_sha: str | None = None
    truncated: bool = False
    failed_step_names: tuple[str, ...] = field(default=(), repr=False)

    def excerpt(self, start: int, end: int) -> str:
        return "\n".join(self.lines[start - 1 : end])


def physical_lines(text: str) -> tuple[str, ...]:
    """Split only LF, normalize CRLF, and omit the final synthetic empty line.

    Excerpts join selected lines with LF and have no added trailing delimiter.
    An actual blank final line remains a selected blank line. Bare CR and Unicode
    separators within a physical line are preserved.
    """
    if not text:
        return ()
    parts = text.split("\n")
    newline_count = text.count("\n")
    if text.endswith("\n"):
        parts.pop()
    return tuple(
        part[:-1] if part.endswith("\r") and index < newline_count else part
        for index, part in enumerate(parts)
    )


def stable_evidence_id(source: SanitizedSource, start: int, end: int, text: str) -> str:
    payload = json.dumps(
        [source.source_type, source.source_identifier, start, end, text],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"ev-{hashlib.sha256(payload).hexdigest()[:24]}"


def validate_evidence_provenance(
    evidence: Sequence[Evidence], sources: Mapping[str, SanitizedSource]
) -> None:
    """Verify exact ranges/text and deterministic IDs against sanitized collected sources.

    The registry must be built from collection by the pipeline, never supplied by
    a model. This verifies correspondence to supplied sources, not authenticity
    of GitHub or the validity of any interpretation.
    """
    seen: set[str] = set()
    for item in evidence:
        source = sources.get(item.source_identifier)
        if source is None or source.source_identifier != item.source_identifier:
            raise EvidenceProvenanceError("Evidence refers to an unknown collected source.")
        if item.source_type != source.source_type:
            raise EvidenceProvenanceError(
                "Evidence source type does not match the collected source."
            )
        if not (1 <= item.start_line <= item.end_line <= len(source.lines)):
            raise EvidenceProvenanceError("Evidence line range is outside the available source.")
        if item.text != source.excerpt(item.start_line, item.end_line):
            raise EvidenceProvenanceError(
                "Evidence text differs from the selected sanitized lines."
            )
        expected = stable_evidence_id(source, item.start_line, item.end_line, item.text)
        if item.evidence_id != expected:
            raise EvidenceProvenanceError(
                "Evidence ID does not match its deterministic source identity."
            )
        if item.evidence_id in seen:
            raise EvidenceProvenanceError("Prepared evidence contains a duplicate evidence ID.")
        seen.add(item.evidence_id)

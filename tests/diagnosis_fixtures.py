"""Synthetic drafts and a network-free injected model."""

import json

from ci_investigator.diagnosis_prompt import DiagnosisPrompt
from ci_investigator.evidence_schemas import PreparedEvidencePackage


def draft(package: PreparedEvidencePackage) -> dict:
    return {
        "observed_failure_summary": "Python could not import yaml.",
        "status": "diagnosed",
        "hypotheses": [
            {
                "explanation": "PyYAML may be missing from the job environment.",
                "supporting_evidence_ids": [package.evidence[0].evidence_id],
                "confidence": "medium",
                "verification_steps": [
                    "Check the dependency installation in the same environment."
                ],
            }
        ],
        "recommended_next_steps": ["Reproduce the import using the job's Python environment."],
        "limitations": ["Only selected evidence was available."],
    }


class FakeModel:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = iter(responses)
        self.prompts: list[DiagnosisPrompt] = []

    def generate(self, prompt: DiagnosisPrompt) -> str:
        self.prompts.append(prompt)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def valid_model(package: PreparedEvidencePackage) -> FakeModel:
    return FakeModel(json.dumps(draft(package)))

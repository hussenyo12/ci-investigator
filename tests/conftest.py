"""Shared report fixtures and an explicit offline guard."""

import socket

import pytest

from ci_investigator import (
    Evidence,
    Hypothesis,
    InvestigationReport,
    WorkflowRunReference,
    parse_workflow_run_url,
)


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("tests must not access the network")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket, "getaddrinfo", fail_network)


@pytest.fixture
def workflow_run() -> WorkflowRunReference:
    return parse_workflow_run_url("https://github.com/example/project/actions/runs/42")


@pytest.fixture
def evidence() -> Evidence:
    return Evidence(
        evidence_id="log-1",
        source_type="log",
        source_identifier="job-7/step-3",
        start_line=12,
        end_line=13,
        text="ModuleNotFoundError: No module named 'example'\nProcess exited with code 1",
    )


@pytest.fixture
def hypothesis() -> Hypothesis:
    return Hypothesis(
        explanation="A dependency may be missing from the job environment.",
        supporting_evidence_ids=["log-1"],
        confidence="medium",
        verification_steps=["Check the job's dependency installation step."],
    )


@pytest.fixture
def report(
    workflow_run: WorkflowRunReference, evidence: Evidence, hypothesis: Hypothesis
) -> InvestigationReport:
    return InvestigationReport(
        workflow_run=workflow_run,
        observed_failure_summary="The Python test step failed during import.",
        evidence=[evidence],
        hypotheses=[hypothesis],
        recommended_next_steps=["Reproduce the import in the same environment."],
        limitations=["Only one log excerpt is available."],
        status="diagnosed",
    )

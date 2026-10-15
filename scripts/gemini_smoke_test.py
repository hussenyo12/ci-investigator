"""One explicitly authorized Vertex request using only synthetic, sanitized evidence."""

import argparse
import json

from ci_investigator.collection_schemas import (
    CollectionResult,
    JobLog,
    RepositoryIdentity,
    RepositoryOwner,
    WorkflowJob,
    WorkflowRunData,
)
from ci_investigator.diagnosis_engine import diagnose
from ci_investigator.diagnosis_prompt import DiagnosisInputError
from ci_investigator.evidence_pipeline import prepare_evidence, validate_prepared_package
from ci_investigator.gemini_client import DiagnosisError
from ci_investigator.url_parser import parse_workflow_run_url


def synthetic_collection() -> CollectionResult:
    """No GitHub access; the URL and SHA are explicitly synthetic identities."""
    reference = parse_workflow_run_url("https://github.com/example/project/actions/runs/42")
    sha = "a" * 40
    text = "ModuleNotFoundError: No module named 'synthetic_ci_dependency'\n"
    return CollectionResult(
        workflow_run=reference,
        outcome="failed",
        selected_attempt=1,
        head_sha=sha,
        workflow=WorkflowRunData(
            id=42,
            name="Synthetic smoke fixture",
            status="completed",
            conclusion="failure",
            run_attempt=1,
            head_sha=sha,
            event="workflow_dispatch",
            repository=RepositoryIdentity(owner=RepositoryOwner(login="example"), name="project"),
        ),
        failed_jobs=[
            WorkflowJob(
                id=7,
                run_id=42,
                name="Synthetic import",
                status="completed",
                conclusion="failure",
                steps=[],
                run_attempt=1,
                head_sha=sha,
            )
        ],
        logs=[
            JobLog(
                job_id=7,
                source_identifier="github:example/project:run:42:attempt:1:job:7:log",
                text=text,
                byte_count=len(text.encode()),
                truncated=False,
                decoding_replacements=False,
            )
        ],
        job_count=1,
        failed_job_count=1,
        limitations=[
            "Synthetic fixture only; no real workflow was collected "
            "and no configuration is supplied."
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-external-ai",
        action="store_true",
        help="Authorize one potentially paid Gemini request with synthetic evidence only",
    )
    args = parser.parse_args(argv)
    if not args.allow_external_ai:
        parser.error("--allow-external-ai is required to authorize this live request")
    collection = synthetic_collection()
    package = prepare_evidence(collection)
    validate_prepared_package(package, collection)
    try:
        report = diagnose(package, has_failed_jobs=True, response_retries=0)
    except (DiagnosisError, DiagnosisInputError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True))
        return 1
    print(json.dumps(report.model_dump(), indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

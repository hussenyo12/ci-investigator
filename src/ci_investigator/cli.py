"""Collection, sanitized previews, and explicitly authorized Vertex AI diagnosis."""

import argparse
import json

from ci_investigator.collector import GitHubCollector
from ci_investigator.diagnosis_engine import diagnose
from ci_investigator.diagnosis_prompt import DiagnosisInputError
from ci_investigator.evidence_pipeline import (
    EvidencePreparationError,
    prepare_evidence,
    validate_prepared_package,
)
from ci_investigator.evidence_provenance import EvidenceProvenanceError
from ci_investigator.gemini_client import DiagnosisError
from ci_investigator.github_client import GitHubClient
from ci_investigator.github_errors import GitHubError
from ci_investigator.redactor import SanitizationError, SecretRedactor
from ci_investigator.url_parser import WorkflowRunURLParseError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect CI facts; preview evidence or opt in to Vertex AI diagnosis."
    )
    parser.add_argument("url", help="Public GitHub Actions workflow run URL")
    parser.add_argument("--attempt", type=int, help="Positive attempt number; defaults to latest")
    parser.add_argument(
        "--preview-evidence",
        action="store_true",
        help="Prepare and preview verified sanitized excerpts",
    )
    parser.add_argument(
        "--metadata-only", action="store_true", help="Omit excerpt text from evidence preview"
    )
    parser.add_argument(
        "--ai-diagnose", action="store_true", help="Investigate prepared evidence with Vertex AI"
    )
    parser.add_argument(
        "--allow-external-ai",
        action="store_true",
        help="Consent to send sanitized evidence to Google; redaction may miss secrets",
    )
    args = parser.parse_args(argv)
    if args.ai_diagnose and not args.allow_external_ai:
        parser.error("--ai-diagnose requires --allow-external-ai; redaction is best-effort")
    if args.allow_external_ai and not args.ai_diagnose:
        parser.error("--allow-external-ai requires --ai-diagnose")
    if args.ai_diagnose and (args.preview_evidence or args.metadata_only):
        parser.error("Use --ai-diagnose separately from evidence-preview options")
    try:
        with GitHubClient() as client:
            result = GitHubCollector(client).collect(args.url, attempt=args.attempt)
    except (GitHubError, WorkflowRunURLParseError) as error:
        try:
            message = SecretRedactor().redact(str(error)).text
        except SanitizationError:
            message = "Collection failed; no source content can be displayed."
        print(json.dumps({"error": message}))
        return 1
    if args.preview_evidence or args.ai_diagnose:
        try:
            package = prepare_evidence(result)
            validate_prepared_package(package, result)
        except (EvidencePreparationError, EvidenceProvenanceError):
            print(
                json.dumps(
                    {"error": "Evidence preparation failed; no source content can be displayed."}
                )
            )
            return 1
        if args.ai_diagnose:
            try:
                report = diagnose(
                    package,
                    has_failed_jobs=result.failed_job_count > 0 and result.outcome != "successful",
                )
            except (DiagnosisError, DiagnosisInputError) as error:
                print(json.dumps({"error": str(error)}, ensure_ascii=True))
                return 1
            print(json.dumps(report.model_dump(), indent=2, ensure_ascii=True))
            return 1 if result.outcome == "inaccessible" else 0
        excluded = {"evidence": {"__all__": {"text"}}} if args.metadata_only else None
        print(json.dumps(package.model_dump(exclude=excluded), indent=2, ensure_ascii=True))
        return 1 if result.outcome == "inaccessible" else 0
    metadata = {
        "url": result.workflow_run.url,
        "outcome": result.outcome,
        "workflow_name": result.workflow.name if result.workflow else None,
        "status": result.workflow.status if result.workflow else None,
        "conclusion": result.workflow.conclusion if result.workflow else None,
        "attempt": result.selected_attempt,
        "head_sha": result.head_sha,
        "failed_jobs": [
            {
                "id": job.id,
                "name": job.name,
                "failed_steps": [step.name for step in job.failed_steps],
            }
            for job in result.failed_jobs
        ],
        "logs": [
            {
                "job_id": log.job_id,
                "lines": log.line_count,
                "bytes": log.byte_count,
                "truncated": log.truncated,
            }
            for log in result.logs
        ],
        "configurations": [
            {"path": file.path, "availability": file.availability} for file in result.configurations
        ],
        "warnings": [warning.model_dump() for warning in result.warnings],
        "limitations": result.limitations,
    }

    def sanitize_metadata(value: object) -> object:
        if isinstance(value, str):
            return SecretRedactor().redact(value).text
        if isinstance(value, list):
            return [sanitize_metadata(item) for item in value]
        if isinstance(value, dict):
            return {key: sanitize_metadata(item) for key, item in value.items()}
        return value

    try:
        safe_metadata = sanitize_metadata(metadata)
    except SanitizationError:
        print(
            json.dumps(
                {"error": "Metadata sanitization failed; no source content can be displayed."}
            )
        )
        return 1
    print(json.dumps(safe_metadata, indent=2, ensure_ascii=True))
    return 1 if result.outcome == "inaccessible" else 0


if __name__ == "__main__":
    raise SystemExit(main())

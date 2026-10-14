"""Manual smoke testing that prints metadata and never prints raw contents."""

import argparse
import json

from ci_investigator.collector import GitHubCollector
from ci_investigator.github_client import GitHubClient
from ci_investigator.github_errors import GitHubError
from ci_investigator.url_parser import WorkflowRunURLParseError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect GitHub Actions facts; print metadata only."
    )
    parser.add_argument("url", help="Public GitHub Actions workflow run URL")
    parser.add_argument("--attempt", type=int, help="Positive attempt number; defaults to latest")
    args = parser.parse_args(argv)
    try:
        with GitHubClient() as client:
            result = GitHubCollector(client).collect(args.url, attempt=args.attempt)
    except (GitHubError, WorkflowRunURLParseError) as error:
        print(json.dumps({"error": str(error)}))
        return 1
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
    print(json.dumps(metadata, indent=2, ensure_ascii=True))
    return 1 if result.outcome == "inaccessible" else 0


if __name__ == "__main__":
    raise SystemExit(main())

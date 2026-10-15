"""Synthetic collection inputs with collector-compatible source identities."""

from github_fixtures import HEAD_SHA, RUN_URL, job, workflow

from ci_investigator.collection_schemas import (
    CollectionResult,
    ConfigurationFile,
    JobLog,
    WorkflowJob,
    WorkflowRunData,
)
from ci_investigator.url_parser import parse_workflow_run_url

PREFIX = "github:example/project"


def collection(
    text: str = "ModuleNotFoundError: No module named 'yaml'\n",
    *,
    files: dict[str, str | None] | None = None,
    truncated: bool = False,
) -> CollectionResult:
    configurations = [
        ConfigurationFile(
            path=path,
            commit_sha=HEAD_SHA,
            source_identifier=f"{PREFIX}:{HEAD_SHA}:{path}",
            availability="available" if content is not None else "missing",
            content=content,
            byte_count=len(content.encode()) if content else 0,
            reason="Absent at run commit." if content is None else None,
        )
        for path, content in (files or {}).items()
    ]
    return CollectionResult(
        workflow_run=parse_workflow_run_url(RUN_URL),
        selected_attempt=2,
        head_sha=HEAD_SHA,
        workflow=WorkflowRunData.model_validate(workflow()),
        outcome="failed",
        failed_jobs=[WorkflowJob.model_validate(job())],
        logs=[
            JobLog(
                job_id=7,
                source_identifier=f"{PREFIX}:run:42:attempt:2:job:7:log",
                text=text,
                byte_count=len(text.encode(errors="surrogatepass")),
                truncated=truncated,
                decoding_replacements=False,
            )
        ],
        configurations=configurations,
        job_count=1,
        failed_job_count=1,
    )

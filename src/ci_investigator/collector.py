"""Select a run attempt and gather facts with bounded, explicit partial failures."""

from urllib.parse import parse_qs, urlsplit

from ci_investigator.collection_schemas import (
    FAILURE_CONCLUSIONS,
    CollectionResult,
    CollectionWarning,
    ConfigurationFile,
    GitTree,
    GitTreeEntry,
)
from ci_investigator.github_client import (
    CONFIG_PATHS,
    MAX_CONFIG_BYTES,
    GitHubClient,
    is_workflow_path,
)
from ci_investigator.github_errors import (
    AccessDenied,
    DownloadTooLarge,
    GitHubError,
    InvalidAPIResponse,
    InvalidAttempt,
    RateLimited,
    ResourceNotFound,
    SymlinkNotAllowed,
    WorkflowNotFound,
)
from ci_investigator.schemas import WorkflowRunReference
from ci_investigator.url_parser import parse_workflow_run_url

MAX_FAILED_JOBS = 3
MAX_CONFIG_FILES = 5
_LIMITATIONS = [
    "Only jobs belonging to the selected attempt are collected; partial reruns may rely on "
    "jobs from earlier attempts.",
    "Configuration is fetched at head_sha; individual jobs may check out another commit, "
    "such as a pull request merge commit.",
    "Raw contents are unredacted and internal; no evidence extraction or diagnosis is performed.",
]


def select_run_from_url(url: str) -> tuple[WorkflowRunReference, int | None]:
    """Keep the foundation parser unchanged while recognizing a single attempt query."""
    reference = parse_workflow_run_url(url)
    attempts = parse_qs(urlsplit(url).query, keep_blank_values=True).get("attempt", [])
    if not attempts:
        return reference, None
    if len(attempts) != 1 or not attempts[0].isascii() or not attempts[0].isdigit():
        raise InvalidAttempt("URL must contain a single positive integer attempt parameter.")
    try:
        attempt = int(attempts[0])
    except ValueError:
        raise InvalidAttempt("URL attempt number is invalid.") from None
    GitHubClient._validate_attempt(attempt)
    return reference, attempt


class GitHubCollector:
    """The caller owns the injected client's lifetime. No persistent storage is used."""

    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    def collect(
        self, run: str | WorkflowRunReference, *, attempt: int | None = None
    ) -> CollectionResult:
        if isinstance(run, str):
            reference, query_attempt = select_run_from_url(run)
            if query_attempt is not None and attempt is not None:
                raise InvalidAttempt("Specify the attempt in the URL or argument, not both.")
            attempt = query_attempt if query_attempt is not None else attempt
        else:
            reference = run
        if attempt is not None:
            GitHubClient._validate_attempt(attempt)
        result = CollectionResult(
            workflow_run=reference, outcome="inaccessible", limitations=list(_LIMITATIONS)
        )
        try:
            latest = self.client.get_workflow_run(reference)
            if attempt is not None and attempt > latest.run_attempt:
                raise InvalidAttempt("Requested attempt exceeds the workflow's latest attempt.")
            selected = latest.run_attempt if attempt is None else attempt
            workflow = (
                latest
                if selected == latest.run_attempt
                else self.client.get_workflow_run(reference, attempt=selected)
            )
            if workflow.head_sha.lower() != latest.head_sha.lower():
                raise InvalidAPIResponse("Workflow attempts have inconsistent head commit SHAs.")
            result.workflow = workflow
            result.selected_attempt = selected
            result.head_sha = workflow.head_sha
            jobs = self.client.get_jobs(reference, selected, workflow.head_sha)
        except (WorkflowNotFound, AccessDenied) as error:
            self._warn(result, error.code, str(error))
            return result

        result.job_count = len(jobs)
        failed = sorted(
            [
                job
                for job in jobs
                if job.conclusion in FAILURE_CONCLUSIONS and job.status == "completed"
            ],
            key=lambda job: job.id,
        )
        result.failed_job_count = len(failed)
        result.failed_jobs = failed[:MAX_FAILED_JOBS]
        if workflow.status != "completed" or any(job.status != "completed" for job in jobs):
            result.outcome = "incomplete"
            self._warn(
                result,
                "incomplete_run",
                "The selected attempt is incomplete; results are provisional.",
            )
        elif workflow.conclusion == "success":
            result.outcome = "successful"
        elif failed and workflow.conclusion in FAILURE_CONCLUSIONS:
            result.outcome = "failed"
        else:
            result.outcome = "insufficient_data"
        if not failed:
            self._warn(
                result, "no_failed_jobs", "No failed jobs were found in the selected attempt."
            )
        if len(failed) > MAX_FAILED_JOBS:
            self._warn(
                result,
                "job_limit",
                f"Skipped {len(failed) - MAX_FAILED_JOBS} failed jobs; "
                "selected the three lowest job IDs.",
            )

        rate_limited = False
        for job in result.failed_jobs:
            if rate_limited:
                self._warn(
                    result, "logs_skipped", "Log request skipped after rate limiting.", str(job.id)
                )
                continue
            try:
                log = self.client.get_job_log(reference, selected, job.id)
                result.logs.append(log)
                if log.truncated:
                    self._warn(
                        result,
                        "log_truncated",
                        "Log truncated at the 1 MiB byte limit; the end of the log, including "
                        "the actual failure, may be missing. This log is incomplete evidence.",
                        log.source_identifier,
                    )
                if log.decoding_replacements:
                    self._warn(
                        result,
                        "log_decoding",
                        "Invalid UTF-8 replaced with U+FFFD in this log.",
                        log.source_identifier,
                    )
            except GitHubError as error:
                self._warn(result, error.code, str(error), f"job:{job.id}:log")
                rate_limited = isinstance(error, RateLimited)
        self._collect_configurations(result, rate_limited=rate_limited)
        return result

    @staticmethod
    def _warn(result: CollectionResult, code: str, message: str, source: str | None = None) -> None:
        result.warnings.append(
            CollectionWarning(code=code, message=message, source_identifier=source)
        )

    def _collect_configurations(self, result: CollectionResult, *, rate_limited: bool) -> None:
        workflow = result.workflow
        assert workflow is not None and result.head_sha is not None
        workflow_path = workflow.path
        if workflow_path is None and workflow.workflow_id is not None and not rate_limited:
            try:
                workflow_path = self.client.get_workflow_path(
                    result.workflow_run, workflow.workflow_id
                )
            except GitHubError as error:
                self._warn(result, error.code, "Workflow path could not be resolved.")
                rate_limited = isinstance(error, RateLimited)
        paths = list(CONFIG_PATHS)
        if workflow_path is not None and is_workflow_path(workflow_path):
            paths.insert(0, workflow_path)
        else:
            self._warn(
                result,
                "workflow_path_unresolved",
                "Workflow YAML path is unavailable or not allowlisted.",
            )

        trees: dict[str, GitTree] = {}
        collected = 0
        for path in paths:
            record = ConfigurationFile(
                path=path,
                commit_sha=result.head_sha,
                source_identifier=f"github:{result.workflow_run.owner.lower()}/"
                f"{result.workflow_run.repo.lower()}:{result.head_sha}:{path}",
                availability="unavailable",
            )
            if collected >= MAX_CONFIG_FILES:
                record.availability = "skipped_limit"
                record.reason = "Five configuration files have already been collected."
            elif rate_limited:
                record.reason = "Request skipped after GitHub rate limiting."
            else:
                try:
                    entry = self._configuration_entry(
                        result.workflow_run, result.head_sha, path, trees
                    )
                    if entry.size is not None and entry.size > MAX_CONFIG_BYTES:
                        raise DownloadTooLarge(
                            "Configuration exceeds the 128 KiB limit; content not fetched."
                        )
                    record = self.client.get_configuration(
                        result.workflow_run, path, result.head_sha, entry.sha
                    )
                    collected += 1
                except GitHubError as error:
                    record.reason = str(error)
                    record.availability = (
                        "missing"
                        if isinstance(error, ResourceNotFound)
                        else "symlink"
                        if isinstance(error, SymlinkNotAllowed)
                        else "oversized"
                        if isinstance(error, DownloadTooLarge)
                        else "invalid"
                        if isinstance(error, InvalidAPIResponse)
                        else "unavailable"
                    )
                    rate_limited = isinstance(error, RateLimited)
            result.configurations.append(record)
            if record.availability != "available":
                self._warn(
                    result, f"config_{record.availability}", record.reason or "Not collected.", path
                )

    def _configuration_entry(
        self, reference: WorkflowRunReference, commit_sha: str, path: str, trees: dict[str, GitTree]
    ) -> GitTreeEntry:
        sha = commit_sha
        components = path.split("/")
        for index, component in enumerate(components):
            if sha not in trees:
                trees[sha] = self.client.get_tree(reference, sha)
            entry = next((item for item in trees[sha].tree if item.path == component), None)
            if entry is None:
                raise ResourceNotFound("Optional configuration path is absent at the run's commit.")
            if entry.mode == "120000":
                raise SymlinkNotAllowed(
                    "Configuration path is a symlink; its target was not followed."
                )
            if index < len(components) - 1:
                if entry.mode != "040000" or entry.type != "tree":
                    raise InvalidAPIResponse("Configuration parent is not a regular directory.")
                sha = entry.sha
            elif entry.mode not in {"100644", "100755"} or entry.type != "blob":
                raise InvalidAPIResponse("Configuration path is not a regular file.")
        return entry

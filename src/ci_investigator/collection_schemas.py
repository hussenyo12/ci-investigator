"""Collected GitHub facts, deliberately separate from investigation hypotheses."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ci_investigator.schemas import (
    GitHubOwner,
    GitHubRepository,
    NonEmptyText,
    PositiveInteger,
    WorkflowRunReference,
)

CommitSHA = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{40}$")]
SourcePath = Annotated[str, StringConstraints(min_length=1)]
RunStatus = Literal["queued", "in_progress", "completed", "waiting", "requested", "pending"]
Conclusion = Literal[
    "success",
    "failure",
    "neutral",
    "cancelled",
    "skipped",
    "timed_out",
    "action_required",
    "stale",
    "startup_failure",
]
FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out", "action_required", "startup_failure"})


class APIModel(BaseModel):
    """GitHub may add fields; consumed fields remain strictly validated."""

    model_config = ConfigDict(strict=True, extra="ignore")


class RepositoryOwner(APIModel):
    login: GitHubOwner


class RepositoryIdentity(APIModel):
    owner: RepositoryOwner
    name: GitHubRepository


class WorkflowRunData(APIModel):
    id: PositiveInteger
    name: NonEmptyText
    status: RunStatus
    conclusion: Conclusion | None
    run_attempt: PositiveInteger
    repository: RepositoryIdentity
    head_sha: CommitSHA
    event: NonEmptyText
    workflow_id: PositiveInteger | None = None
    path: SourcePath | None = None

    @model_validator(mode="after")
    def require_completed_conclusion(self) -> "WorkflowRunData":
        if self.status == "completed" and self.conclusion is None:
            raise ValueError("completed workflow must have a conclusion")
        return self


class JobStep(APIModel):
    name: NonEmptyText
    number: PositiveInteger
    status: RunStatus
    conclusion: Conclusion | None


class WorkflowJob(APIModel):
    id: PositiveInteger
    run_id: PositiveInteger
    name: NonEmptyText
    status: RunStatus
    conclusion: Conclusion | None
    steps: list[JobStep]
    run_attempt: PositiveInteger | None = None
    head_sha: CommitSHA | None = None

    @model_validator(mode="after")
    def require_completed_conclusion(self) -> "WorkflowJob":
        if self.status == "completed" and self.conclusion is None:
            raise ValueError("completed job must have a conclusion")
        return self

    @property
    def failed_steps(self) -> list[JobStep]:
        return [step for step in self.steps if step.conclusion in FAILURE_CONCLUSIONS]


class JobsPage(APIModel):
    total_count: Annotated[int, Field(ge=0)]
    jobs: list[WorkflowJob]


class GitTreeEntry(APIModel):
    path: SourcePath
    mode: Literal["100644", "100755", "040000", "120000", "160000"]
    type: Literal["blob", "tree", "commit"]
    sha: CommitSHA
    size: Annotated[int, Field(ge=0)] | None = None


class WorkflowDefinition(APIModel):
    id: PositiveInteger
    path: SourcePath


class GitTree(APIModel):
    sha: CommitSHA
    tree: list[GitTreeEntry]
    truncated: bool


class RepositoryFile(APIModel):
    type: Literal["file"]
    path: SourcePath
    sha: CommitSHA
    size: Annotated[int, Field(ge=0)]
    encoding: Literal["base64"]
    content: str = Field(repr=False, exclude=True)


class CollectedModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class JobLog(CollectedModel):
    job_id: PositiveInteger
    source_identifier: NonEmptyText
    text: str = Field(repr=False, exclude=True)
    byte_count: Annotated[int, Field(ge=0, le=1024 * 1024)]
    truncated: bool
    decoding_replacements: bool

    @property
    def line_count(self) -> int:
        # GitHub log lines use LF (including CRLF), not Unicode string separators.
        return self.text.count("\n") + int(bool(self.text) and not self.text.endswith("\n"))


class ConfigurationFile(CollectedModel):
    path: NonEmptyText
    commit_sha: CommitSHA
    source_identifier: NonEmptyText
    availability: Literal[
        "available", "missing", "unavailable", "invalid", "symlink", "oversized", "skipped_limit"
    ]
    content: str | None = Field(default=None, repr=False, exclude=True)
    byte_count: Annotated[int, Field(ge=0, le=128 * 1024)] = 0
    truncated: bool = False
    reason: NonEmptyText | None = None


class CollectionWarning(CollectedModel):
    code: NonEmptyText
    message: NonEmptyText
    source_identifier: NonEmptyText | None = None


class CollectionResult(CollectedModel):
    workflow_run: WorkflowRunReference
    outcome: Literal["successful", "failed", "incomplete", "insufficient_data", "inaccessible"]
    workflow: WorkflowRunData | None = None
    selected_attempt: PositiveInteger | None = None
    head_sha: CommitSHA | None = None
    failed_jobs: list[WorkflowJob] = Field(default_factory=list)
    logs: list[JobLog] = Field(default_factory=list)
    configurations: list[ConfigurationFile] = Field(default_factory=list)
    warnings: list[CollectionWarning] = Field(default_factory=list)
    limitations: list[NonEmptyText] = Field(default_factory=list)
    job_count: Annotated[int, Field(ge=0)] = 0
    failed_job_count: Annotated[int, Field(ge=0)] = 0

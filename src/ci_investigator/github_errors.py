"""Actionable API errors containing no HTTP bodies, tokens, or signed URLs."""


class GitHubError(Exception):
    code = "github_error"


class WorkflowNotFound(GitHubError):
    code = "workflow_not_found"


class ResourceNotFound(GitHubError):
    code = "resource_not_found"


class AccessDenied(GitHubError):
    code = "access_denied"


class RateLimited(GitHubError):
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RequestTimeout(GitHubError):
    code = "request_timeout"


class ConnectionFailure(GitHubError):
    code = "connection_failure"


class GitHubServerError(GitHubError):
    code = "server_error"


class InvalidAPIResponse(GitHubError):
    code = "invalid_api_response"


class LogsUnavailable(GitHubError):
    code = "logs_unavailable"


class DownloadTooLarge(GitHubError):
    code = "download_too_large"


class InvalidAttempt(GitHubError):
    code = "invalid_attempt"


class SymlinkNotAllowed(GitHubError):
    code = "symlink_not_allowed"

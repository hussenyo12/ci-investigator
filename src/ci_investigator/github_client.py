"""Read-only, bounded GitHub HTTP access with explicitly isolated log downloads."""

import base64
import binascii
import ipaddress
import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from ci_investigator.collection_schemas import (
    CommitSHA,
    ConfigurationFile,
    GitTree,
    JobLog,
    JobsPage,
    RepositoryFile,
    WorkflowDefinition,
    WorkflowJob,
    WorkflowRunData,
)
from ci_investigator.github_errors import (
    AccessDenied,
    ConnectionFailure,
    DownloadTooLarge,
    GitHubServerError,
    InvalidAPIResponse,
    InvalidAttempt,
    LogsUnavailable,
    RateLimited,
    RequestTimeout,
    ResourceNotFound,
    WorkflowNotFound,
)
from ci_investigator.schemas import WorkflowRunReference

MAX_LOG_BYTES = 1024 * 1024
MAX_CONFIG_BYTES = 128 * 1024
MAX_API_BYTES = 2 * 1024 * 1024
MAX_CONFIG_RESPONSE_BYTES = 256 * 1024
MAX_JOB_PAGES = 20
MAX_STREAM_SECONDS = 60.0
PAGE_SIZE = 100
CONFIG_PATHS = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
)
_Model = TypeVar("_Model", bound=BaseModel)
_LOCAL_HOST_SUFFIXES = (
    "localhost",
    "localdomain",
    "local",
    "internal",
    "lan",
    "home",
    "home.arpa",
    "intranet",
    "corp",
    "private",
    "svc",
    "cluster",
)
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_NUMERIC_HOST_LABEL = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)")


def is_workflow_path(path: str) -> bool:
    components = path.split("/")
    return (
        len(components) == 3
        and components[:2] == [".github", "workflows"]
        and components[2] not in {"", ".", ".."}
        and components[2].endswith((".yml", ".yaml"))
        and all(
            character.isascii() and (character.isalnum() or character in "_.-")
            for character in components[2]
        )
    )


class GitHubClient:
    """Own two clients sharing an injectable transport but never auth headers.

    No redirects are followed automatically, no retries are performed, and proxy
    environment variables are ignored. Passing token="" forces anonymous access.
    """

    def __init__(
        self, *, token: str | None = None, transport: httpx.BaseTransport | None = None
    ) -> None:
        configured_token = os.environ.get("GITHUB_TOKEN", "") if token is None else token
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ci-investigator/0.1.0",
            "Accept-Encoding": "identity",
        }
        if configured_token:
            if not configured_token.isascii() or any(
                ord(character) <= 32 or ord(character) == 127 for character in configured_token
            ):
                raise AccessDenied("GITHUB_TOKEN has invalid characters; configure a valid token.")
            headers["Authorization"] = f"Bearer {configured_token}"
        settings = {
            "transport": transport,
            "timeout": httpx.Timeout(20.0, connect=5.0),
            "follow_redirects": False,
            "trust_env": False,
        }
        self._api = httpx.Client(base_url="https://api.github.com", headers=headers, **settings)
        self._downloads = httpx.Client(headers={"Accept-Encoding": "identity"}, **settings)

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._api.close()
        self._downloads.close()

    @contextmanager
    def _stream(
        self,
        path: str,
        *,
        resource: str,
        params: dict[str, str | int] | None = None,
        download: bool = False,
    ) -> Iterator[httpx.Response]:
        client = self._downloads if download else self._api
        try:
            with client.stream("GET", path, params=params) as response:
                self._check_status(response, resource)
                yield response
        except httpx.TimeoutException:
            raise RequestTimeout("GitHub request timed out; retry collection later.") from None
        except (httpx.RemoteProtocolError, httpx.InvalidURL):
            raise InvalidAPIResponse(
                "GitHub returned a malformed HTTP response or redirect."
            ) from None
        except httpx.HTTPError:
            raise ConnectionFailure(
                "GitHub connection failed; check connectivity and retry."
            ) from None

    @staticmethod
    def _check_status(response: httpx.Response, resource: str) -> None:
        status = response.status_code
        limited = status == 429 or (
            status == 403
            and (
                response.headers.get("x-ratelimit-remaining") == "0"
                or "retry-after" in response.headers
            )
        )
        if status == 403 and not limited:
            # Secondary limits sometimes arrive without retry/rate-limit headers.
            try:
                body, _ = GitHubClient._read_bounded(response, 8 * 1024)
                payload = json.loads(body)
                message = payload.get("message", "") if isinstance(payload, dict) else ""
                limited = isinstance(message, str) and "rate limit" in message.lower()
            except (ValueError, RecursionError, DownloadTooLarge, InvalidAPIResponse):
                limited = False
        if limited:
            retry = response.headers.get("retry-after", "")
            raise RateLimited(
                "GitHub rate limit reached; wait before retrying or configure an authorized token.",
                retry_after=int(retry)
                if retry.isascii() and retry.isdigit() and len(retry) <= 10
                else None,
            )
        if status in {401, 403}:
            raise AccessDenied("GitHub denied access; check token and repository read permissions.")
        if status in {404, 410}:
            if resource == "log":
                raise LogsUnavailable("Job logs are missing, expired, or inaccessible.")
            if resource == "workflow":
                raise WorkflowNotFound(
                    "Workflow run not found or hidden by repository permissions."
                )
            if resource == "attempt":
                raise InvalidAttempt("The requested workflow attempt is unavailable.")
            raise ResourceNotFound("The requested GitHub resource is unavailable.")
        if status >= 500:
            raise GitHubServerError("GitHub returned a temporary server error; retry later.")
        if status != 200 and not (resource == "log" and status in {301, 302, 303, 307, 308}):
            raise InvalidAPIResponse("GitHub returned an unexpected HTTP status.")

    @staticmethod
    def _read_bounded(
        response: httpx.Response, limit: int, *, truncate: bool = False
    ) -> tuple[bytes, bool]:
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise InvalidAPIResponse(
                "Unexpected compressed response; bounded decoding is required."
            )
        body = bytearray()
        deadline = time.monotonic() + MAX_STREAM_SECONDS
        # One lookahead byte distinguishes an exact-size response from a truncated one.
        for chunk in response.iter_raw():
            if time.monotonic() > deadline:
                raise RequestTimeout("GitHub response exceeded the 60-second streaming deadline.")
            remaining = limit + 1 - len(body)
            body.extend(chunk[:remaining])
            if len(body) > limit:
                if not truncate:
                    raise DownloadTooLarge("Response exceeds the configured download size limit.")
                return bytes(body[:limit]), True
        return bytes(body), False

    def _json(
        self,
        path: str,
        model: type[_Model],
        *,
        resource: str,
        params: dict[str, str | int] | None = None,
        limit: int = MAX_API_BYTES,
    ) -> _Model:
        with self._stream(path, resource=resource, params=params) as response:
            body, _ = self._read_bounded(response, limit)
        try:
            return model.model_validate(json.loads(body))
        except (ValueError, RecursionError):
            raise InvalidAPIResponse(
                "GitHub response is missing or has invalid required fields."
            ) from None

    @staticmethod
    def _repo_path(reference: WorkflowRunReference) -> str:
        return f"/repos/{reference.owner}/{reference.repo}"

    def get_workflow_run(
        self, reference: WorkflowRunReference, *, attempt: int | None = None
    ) -> WorkflowRunData:
        path = f"{self._repo_path(reference)}/actions/runs/{reference.run_id}"
        if attempt is not None:
            self._validate_attempt(attempt)
            path += f"/attempts/{attempt}"
        run = self._json(path, WorkflowRunData, resource="attempt" if attempt else "workflow")
        if (
            run.id != reference.run_id
            or run.repository.owner.login.lower() != reference.owner.lower()
            or run.repository.name.lower() != reference.repo.lower()
            or (attempt is not None and run.run_attempt != attempt)
        ):
            raise InvalidAPIResponse(
                "Workflow response does not match the requested run or attempt."
            )
        return run

    @staticmethod
    def _validate_attempt(attempt: int) -> None:
        if type(attempt) is not int or attempt <= 0:
            raise InvalidAttempt("Workflow attempt must be a positive integer.")

    def get_jobs(
        self, reference: WorkflowRunReference, attempt: int, head_sha: str
    ) -> list[WorkflowJob]:
        self._validate_attempt(attempt)
        self._validate_sha(head_sha)
        path = (
            f"{self._repo_path(reference)}/actions/runs/{reference.run_id}/attempts/{attempt}/jobs"
        )
        jobs: list[WorkflowJob] = []
        seen_ids: set[int] = set()
        total: int | None = None
        for page_number in range(1, MAX_JOB_PAGES + 1):
            page = self._json(
                path,
                JobsPage,
                resource="attempt",
                params={"per_page": PAGE_SIZE, "page": page_number},
            )
            if total is not None and total != page.total_count:
                raise InvalidAPIResponse("Job list changed during pagination; retry collection.")
            total = page.total_count
            if len(page.jobs) > PAGE_SIZE or total > PAGE_SIZE * MAX_JOB_PAGES:
                raise InvalidAPIResponse("Job list exceeds the bounded pagination limit.")
            for job in page.jobs:
                if (
                    job.id in seen_ids
                    or job.run_id != reference.run_id
                    or (job.run_attempt is not None and job.run_attempt != attempt)
                    or (job.head_sha is not None and job.head_sha.lower() != head_sha.lower())
                ):
                    raise InvalidAPIResponse(
                        "Jobs contain duplicates or inconsistent run provenance."
                    )
                numbers = [step.number for step in job.steps]
                if len(numbers) != len(set(numbers)):
                    raise InvalidAPIResponse("Job contains duplicate step numbers.")
                seen_ids.add(job.id)
                jobs.append(job)
            if len(jobs) == total:
                return jobs
            if len(jobs) > total or not page.jobs:
                raise InvalidAPIResponse("Job pagination returned inconsistent counts.")
        raise InvalidAPIResponse("Job pagination did not finish within the request limit.")

    def get_job_log(self, reference: WorkflowRunReference, attempt: int, job_id: int) -> JobLog:
        self._validate_attempt(attempt)
        if type(job_id) is not int or job_id <= 0:
            raise InvalidAPIResponse("Job ID must be a positive integer.")
        path = f"{self._repo_path(reference)}/actions/jobs/{job_id}/logs"
        with self._stream(path, resource="log") as response:
            if response.status_code == 200:
                data, truncated = self._read_bounded(response, MAX_LOG_BYTES, truncate=True)
            else:
                location = response.headers.get("location", "")
                self._validate_download_url(location)
                with self._stream(location, resource="log", download=True) as downloaded:
                    if downloaded.status_code != 200:
                        raise LogsUnavailable(
                            "Log download redirected again; retry for a fresh link."
                        )
                    data, truncated = self._read_bounded(downloaded, MAX_LOG_BYTES, truncate=True)
        try:
            text = data.decode("utf-8")
            replaced = False
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
            replaced = True
        source = (
            f"github:{reference.owner.lower()}/{reference.repo.lower()}:run:{reference.run_id}"
            f":attempt:{attempt}:job:{job_id}:log"
        )
        return JobLog(
            job_id=job_id,
            source_identifier=source,
            text=text,
            byte_count=len(data),
            truncated=truncated,
            decoding_replacements=replaced,
        )

    @staticmethod
    def _validate_download_url(location: str) -> None:
        """Reject visibly unsafe destinations without DNS or connection-time checks.

        Hostnames can still resolve to private addresses or undergo DNS rebinding.
        These lexical checks reduce risk; they do not constitute complete SSRF protection.
        """
        try:
            parts = urlsplit(location)
            host = parts.hostname
            port = parts.port
        except ValueError:
            raise InvalidAPIResponse("GitHub returned an invalid log redirect.") from None
        if (
            parts.scheme != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or port not in {None, 443}
            or parts.fragment
            or any(
                character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in location
            )
        ):
            raise InvalidAPIResponse(
                "Log redirects must use HTTPS without credentials or fragments."
            )
        GitHubClient._validate_download_host(host)

    @staticmethod
    def _validate_download_host(host: str) -> None:
        host = host.lower().rstrip(".")
        message = "Log redirect destination must be a public IP or a non-local hostname."
        # Reject encoded hostnames and scoped IPv6 before a resolver can interpret them.
        if not host or "%" in host:
            raise InvalidAPIResponse(message)
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if (
                len(labels) < 2
                or len(host) > 253
                or any(_DNS_LABEL.fullmatch(label) is None for label in labels)
                or all(_NUMERIC_HOST_LABEL.fullmatch(label) is not None for label in labels)
                or labels[0] == "localhost"
                or any(
                    host == suffix or host.endswith(f".{suffix}") for suffix in _LOCAL_HOST_SUFFIXES
                )
            ):
                raise InvalidAPIResponse(message) from None
            return

        addresses = [address]
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            addresses.append(address.ipv4_mapped)
        for candidate in addresses:
            # is_global alone includes multicast; also exclude reserved and old site-local IPv6.
            if (
                not candidate.is_global
                or candidate.is_multicast
                or candidate.is_reserved
                or (isinstance(candidate, ipaddress.IPv6Address) and candidate.is_site_local)
            ):
                raise InvalidAPIResponse(message)

    def get_tree(self, reference: WorkflowRunReference, sha: str) -> GitTree:
        self._validate_sha(sha)
        tree = self._json(f"{self._repo_path(reference)}/git/trees/{sha}", GitTree, resource="tree")
        if tree.truncated:
            raise InvalidAPIResponse(
                "Git tree was truncated; file types cannot be verified safely."
            )
        paths = [entry.path for entry in tree.tree]
        if len(paths) != len(set(paths)):
            raise InvalidAPIResponse("Git tree contains duplicate paths.")
        return tree

    @staticmethod
    def _validate_sha(sha: str) -> None:
        try:
            TypeAdapter(CommitSHA).validate_python(sha, strict=True)
        except ValidationError:
            raise InvalidAPIResponse("A valid commit or tree SHA is required.") from None

    def get_workflow_path(self, reference: WorkflowRunReference, workflow_id: int) -> str:
        if type(workflow_id) is not int or workflow_id <= 0:
            raise InvalidAPIResponse("Workflow ID must be a positive integer.")
        workflow = self._json(
            f"{self._repo_path(reference)}/actions/workflows/{workflow_id}",
            WorkflowDefinition,
            resource="workflow_definition",
        )
        if workflow.id != workflow_id:
            raise InvalidAPIResponse("Workflow definition does not match the requested identifier.")
        return workflow.path

    def get_configuration(
        self, reference: WorkflowRunReference, path: str, head_sha: str, blob_sha: str
    ) -> ConfigurationFile:
        if path not in CONFIG_PATHS and not is_workflow_path(path):
            raise InvalidAPIResponse("Configuration path is outside the deterministic allowlist.")
        self._validate_sha(head_sha)
        self._validate_sha(blob_sha)
        file = self._json(
            f"{self._repo_path(reference)}/contents/{path}",
            RepositoryFile,
            resource="config",
            params={"ref": head_sha},
            limit=MAX_CONFIG_RESPONSE_BYTES,
        )
        if file.path != path or file.sha.lower() != blob_sha.lower():
            raise InvalidAPIResponse(
                "Configuration response has inconsistent path or blob identity."
            )
        if file.size > MAX_CONFIG_BYTES:
            raise DownloadTooLarge("Configuration file exceeds the 128 KiB limit.")
        try:
            data = base64.b64decode("".join(file.content.split()), validate=True)
            if len(data) > MAX_CONFIG_BYTES:
                raise DownloadTooLarge("Decoded configuration exceeds the 128 KiB limit.")
            if len(data) != file.size:
                raise InvalidAPIResponse("Configuration size does not match its decoded content.")
            content = data.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            raise InvalidAPIResponse(
                "Configuration must contain valid base64-encoded UTF-8."
            ) from None
        if "\x00" in content:
            raise InvalidAPIResponse("Configuration contains binary NUL characters.")
        return ConfigurationFile(
            path=path,
            commit_sha=head_sha,
            source_identifier=f"github:{reference.owner.lower()}/{reference.repo.lower()}:{head_sha}:{path}",
            availability="available",
            content=content,
            byte_count=len(data),
        )

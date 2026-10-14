import httpx
import pytest
from github_fixtures import API_REPO, BLOB_SHA, HEAD_SHA, config, job, response, tree, workflow

from ci_investigator.collection_schemas import WorkflowRunData
from ci_investigator.github_client import (
    MAX_API_BYTES,
    MAX_CONFIG_BYTES,
    MAX_LOG_BYTES,
    GitHubClient,
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
    WorkflowNotFound,
)
from ci_investigator.schemas import WorkflowRunReference


def client_returning(reply: httpx.Response) -> GitHubClient:
    return GitHubClient(token="", transport=httpx.MockTransport(lambda request: reply))


def test_valid_workflow_headers_and_identity(workflow_run: WorkflowRunReference) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == f"https://api.github.com{API_REPO}/actions/runs/42"
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.headers["accept"] == "application/vnd.github+json"
        assert request.headers["x-github-api-version"] == "2022-11-28"
        assert request.extensions["timeout"]["connect"] == 5.0
        assert request.extensions["timeout"]["read"] == 20.0
        return response(data=workflow(unused_field="GitHub can add fields"))

    with GitHubClient(token="test-token", transport=httpx.MockTransport(handler)) as client:
        run = client.get_workflow_run(workflow_run)
    assert isinstance(run, WorkflowRunData)
    assert (run.id, run.run_attempt, run.head_sha, run.event) == (42, 2, HEAD_SHA, "push")


def test_optional_environment_token(
    monkeypatch: pytest.MonkeyPatch, workflow_run: WorkflowRunReference
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer env-token"
        return response(data=workflow())

    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        client.get_workflow_run(workflow_run)
    monkeypatch.delenv("GITHUB_TOKEN")

    def anonymous(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return response(data=workflow())

    with GitHubClient(transport=httpx.MockTransport(anonymous)) as client:
        client.get_workflow_run(workflow_run)


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "42"},
        {"id": 99},
        {"head_sha": None},
        {"head_sha": "main"},
        {"run_attempt": 0},
        {"run_attempt": True},
        {"status": "mystery"},
        {"conclusion": None},
        {"repository": {"owner": {"login": "attacker"}, "name": "project"}},
    ],
)
def test_invalid_workflow_fields(
    workflow_run: WorkflowRunReference, changes: dict[str, object]
) -> None:
    with client_returning(response(data=workflow(**changes))) as client:
        with pytest.raises(InvalidAPIResponse):
            client.get_workflow_run(workflow_run)


def test_missing_commit_is_not_replaced_with_branch(workflow_run: WorkflowRunReference) -> None:
    data = workflow()
    del data["head_sha"]
    with client_returning(response(data=data)) as client:
        with pytest.raises(InvalidAPIResponse):
            client.get_workflow_run(workflow_run)


@pytest.mark.parametrize(
    ("status", "headers", "error"),
    [
        (404, {}, WorkflowNotFound),
        (401, {}, AccessDenied),
        (403, {}, AccessDenied),
        (403, {"x-ratelimit-remaining": "0"}, RateLimited),
        (429, {"retry-after": "30"}, RateLimited),
        (503, {}, GitHubServerError),
        (302, {"location": "https://attacker.test"}, InvalidAPIResponse),
    ],
)
def test_typed_api_errors_without_secret_leaks(
    workflow_run: WorkflowRunReference,
    status: int,
    headers: dict[str, str],
    error: type[Exception],
) -> None:
    reply = response(status, data={"message": "secret-token"}, headers=headers)
    with GitHubClient(
        token="secret-token", transport=httpx.MockTransport(lambda request: reply)
    ) as client:
        with pytest.raises(error) as caught:
            client.get_workflow_run(workflow_run)
    assert "secret-token" not in str(caught.value)
    assert caught.value.__cause__ is None
    if status == 429:
        assert caught.value.retry_after == 30


@pytest.mark.parametrize("transport_error", [httpx.ReadTimeout, httpx.ConnectError])
def test_network_errors_are_sanitized(
    workflow_run: WorkflowRunReference, transport_error: type[httpx.HTTPError]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise transport_error("secret-token signed-url", request=request)

    expected = RequestTimeout if transport_error is httpx.ReadTimeout else ConnectionFailure
    with GitHubClient(token="secret-token", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(expected) as caught:
            client.get_workflow_run(workflow_run)
    assert "secret-token" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_api_json_is_bounded(workflow_run: WorkflowRunReference) -> None:
    with client_returning(response(content=b" " * (MAX_API_BYTES + 1))) as client:
        with pytest.raises(DownloadTooLarge):
            client.get_workflow_run(workflow_run)


def test_paginated_jobs_use_selected_attempt(workflow_run: WorkflowRunReference) -> None:
    pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/runs/42/attempts/2/jobs")
        assert request.url.params["per_page"] == "100"
        page = int(request.url.params["page"])
        pages.append(page)
        return response(data={"total_count": 2, "jobs": [job(page)]})

    with GitHubClient(token="", transport=httpx.MockTransport(handler)) as client:
        jobs = client.get_jobs(workflow_run, 2, HEAD_SHA)
    assert [entry.id for entry in jobs] == [1, 2]
    assert pages == [1, 2]
    assert [step.number for step in jobs[0].failed_steps] == [2]
    assert [step.conclusion for step in jobs[0].steps] == ["success", "failure", "skipped"]


@pytest.mark.parametrize("changes", [{"run_attempt": 1}, {"run_id": 99}, {"head_sha": "f" * 40}])
def test_jobs_cannot_mix_provenance(
    workflow_run: WorkflowRunReference, changes: dict[str, object]
) -> None:
    with client_returning(response(data={"total_count": 1, "jobs": [job(**changes)]})) as client:
        with pytest.raises(InvalidAPIResponse, match="provenance"):
            client.get_jobs(workflow_run, 2, HEAD_SHA)


@pytest.mark.parametrize(
    "page",
    [
        {"total_count": 2, "jobs": [job(), job()]},
        {"total_count": 1, "jobs": []},
        {"total_count": 0, "jobs": [job()]},
        {"total_count": 2001, "jobs": []},
    ],
)
def test_invalid_pagination_is_bounded(
    workflow_run: WorkflowRunReference, page: dict[str, object]
) -> None:
    with client_returning(response(data=page)) as client:
        with pytest.raises(InvalidAPIResponse):
            client.get_jobs(workflow_run, 2, HEAD_SHA)


def test_log_redirect_never_forwards_token(workflow_run: WorkflowRunReference) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.github.com":
            assert request.headers["authorization"] == "Bearer test-token"
            return response(
                302, headers={"location": "https://downloads.example.test/log?signed=value"}
            )
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert "x-github-api-version" not in request.headers
        return response(content=b"first\r\nsecond\n")

    with GitHubClient(token="test-token", transport=httpx.MockTransport(handler)) as client:
        log = client.get_job_log(workflow_run, 2, 7)
    assert len(requests) == 2
    assert log.text == "first\r\nsecond\n"
    assert log.line_count == 2
    assert log.source_identifier == "github:example/project:run:42:attempt:2:job:7:log"
    assert not log.truncated
    assert "text" not in log.model_dump()
    assert "first" not in repr(log)


@pytest.mark.parametrize(
    "location",
    [
        "http://downloads.example.test/log",
        "//downloads.example.test/log",
        "",
        "https://user:password@downloads.example.test/log",
        "https://downloads.example.test:8080/log",
        "https://downloads.example.test/log#fragment",
        "https://[broken/log",
        "https://downloads.example.test/log\n",
        "https://127.0.0.1/internal",
        "https://localhost/secret",
        "https://169.254.169.254/internal",
        "https://10.0.0.1/log",
        "https://172.16.0.1/log",
        "https://192.168.1.1/log",
        "https://0.0.0.0/log",
        "https://100.64.0.1/log",
        "https://192.0.2.1/log",
        "https://198.18.0.1/log",
        "https://224.0.0.1/log",
        "https://240.0.0.1/log",
        "https://[::1]/log",
        "https://[::]/log",
        "https://[fc00::1]/log",
        "https://[fe80::1]/log",
        "https://[fec0::1]/log",
        "https://[ff02::1]/log",
        "https://[2001:db8::1]/log",
        "https://[::ffff:127.0.0.1]/log",
        "https://[::ffff:10.0.0.1]/log",
        "https://[::ffff:100.64.0.1]/log",
        "https://[::ffff:224.0.0.1]/log",
        "https://[fe80::1%25en0]/log",
        "https://LOCALHOST./secret",
        "https://service.localhost/log",
        "https://localhost.example.com/log",
        "https://localhost.localdomain/log",
        "https://service.local/log",
        "https://service.internal/log",
        "https://service.lan/log",
        "https://service.home.arpa/log",
        "https://service.corp/log",
        "https://service.svc.cluster.local/log",
        "https://printer/log",
        "https://127.0.0.1./log",
        "https://127.1/log",
        "https://2130706433/log",
        "https://0x7f000001/log",
        "https://0x7f.0.0.1/log",
        "https://%31%32%37.0.0.1/log",
    ],
)
def test_unsafe_log_redirect_is_not_requested(
    workflow_run: WorkflowRunReference, location: str
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response(302, headers={"location": location})

    with GitHubClient(token="", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(InvalidAPIResponse):
            client.get_job_log(workflow_run, 2, 7)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "location",
    [
        "https://productionresultssa.blob.core.windows.net/container/log?sig=signed-value",
        "https://logs.actions.githubusercontent.com/path?token=signed-value",
        "https://external-downloads.example.com/log?sig=signed-value",
        "https://8.8.8.8/log",
        "https://[2606:4700:4700::1111]/log",
    ],
)
def test_public_log_redirect_destinations_are_allowed_without_credentials(
    workflow_run: WorkflowRunReference, location: str
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            assert request.headers["authorization"] == "Bearer test-token"
            return response(302, headers={"location": location})
        assert str(request.url) == location
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return response(content=b"job log\n")

    with GitHubClient(token="test-token", transport=httpx.MockTransport(handler)) as client:
        assert client.get_job_log(workflow_run, 2, 7).line_count == 1
    assert len(calls) == 2


@pytest.mark.parametrize("status", [404, 410])
def test_missing_log(workflow_run: WorkflowRunReference, status: int) -> None:
    with client_returning(response(status)) as client:
        with pytest.raises(LogsUnavailable):
            client.get_job_log(workflow_run, 2, 7)


def test_log_download_timeout_is_sanitized(workflow_run: WorkflowRunReference) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return response(
                302, headers={"location": "https://downloads.example.test/?token=secret"}
            )
        raise httpx.ReadTimeout("secret URL", request=request)

    with GitHubClient(token="", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RequestTimeout) as caught:
            client.get_job_log(workflow_run, 2, 7)
    assert "secret" not in str(caught.value)


class CountingStream(httpx.SyncByteStream):
    def __init__(self) -> None:
        self.chunks_read = 0
        self.closed = False

    def __iter__(self):
        for _ in range(1000):
            self.chunks_read += 1
            yield b"a" * (64 * 1024)

    def close(self) -> None:
        self.closed = True


def test_oversized_log_stops_stream_and_records_truncation(
    workflow_run: WorkflowRunReference,
) -> None:
    stream = CountingStream()
    with client_returning(httpx.Response(200, stream=stream)) as client:
        log = client.get_job_log(workflow_run, 2, 7)
    assert log.byte_count == MAX_LOG_BYTES
    assert len(log.text) == MAX_LOG_BYTES
    assert log.truncated
    assert stream.chunks_read == 17
    assert stream.closed


def test_exact_limit_log_is_not_truncated(workflow_run: WorkflowRunReference) -> None:
    with client_returning(response(content=b"a" * MAX_LOG_BYTES)) as client:
        assert not client.get_job_log(workflow_run, 2, 7).truncated


def test_invalid_utf8_log_replaces_bytes_without_reordering(
    workflow_run: WorkflowRunReference,
) -> None:
    with client_returning(response(content=b"before\n\xffafter\n")) as client:
        log = client.get_job_log(workflow_run, 2, 7)
    assert log.text == "before\n\ufffdafter\n"
    assert log.line_count == 2 and log.decoding_replacements


def test_compressed_log_is_rejected(workflow_run: WorkflowRunReference) -> None:
    with client_returning(
        response(content=b"untrusted", headers={"content-encoding": "gzip"})
    ) as client:
        with pytest.raises(InvalidAPIResponse, match="compressed"):
            client.get_job_log(workflow_run, 2, 7)


def test_configuration_uses_exact_commit(workflow_run: WorkflowRunReference) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"{API_REPO}/contents/pyproject.toml"
        assert dict(request.url.params) == {"ref": HEAD_SHA}
        return response(data=config("pyproject.toml", b"[project]\n"))

    with GitHubClient(token="", transport=httpx.MockTransport(handler)) as client:
        file = client.get_configuration(workflow_run, "pyproject.toml", HEAD_SHA, BLOB_SHA)
    assert file.content == "[project]\n" and file.commit_sha == HEAD_SHA
    assert "content" not in file.model_dump()
    assert "[project]" not in repr(file)


@pytest.mark.parametrize(
    "changes",
    [
        {"content": "not-base64!"},
        {"encoding": "none"},
        {"type": "symlink"},
        {"path": "secrets.txt"},
        {"path": " pyproject.toml "},
        {"sha": "f" * 40},
        {"size": 9},
    ],
)
def test_invalid_configuration_content(
    workflow_run: WorkflowRunReference, changes: dict[str, object]
) -> None:
    with client_returning(response(data=config("pyproject.toml", **changes))) as client:
        with pytest.raises(InvalidAPIResponse):
            client.get_configuration(workflow_run, "pyproject.toml", HEAD_SHA, BLOB_SHA)


@pytest.mark.parametrize("content", [b"\xff", b"hello\x00world"])
def test_invalid_utf8_or_binary_configuration(
    workflow_run: WorkflowRunReference, content: bytes
) -> None:
    with client_returning(response(data=config("pyproject.toml", content))) as client:
        with pytest.raises(InvalidAPIResponse):
            client.get_configuration(workflow_run, "pyproject.toml", HEAD_SHA, BLOB_SHA)


def test_configuration_decoded_size_limit(workflow_run: WorkflowRunReference) -> None:
    with client_returning(
        response(data=config("pyproject.toml", b"a" * (MAX_CONFIG_BYTES + 1)))
    ) as client:
        with pytest.raises(DownloadTooLarge):
            client.get_configuration(workflow_run, "pyproject.toml", HEAD_SHA, BLOB_SHA)


@pytest.mark.parametrize("path", ["../secret", "secrets.txt", ".github/workflows/../secret.yml"])
def test_configuration_allowlist_blocks_paths(
    workflow_run: WorkflowRunReference, path: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid path must be rejected before requesting it")

    with GitHubClient(token="", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(InvalidAPIResponse):
            client.get_configuration(workflow_run, path, HEAD_SHA, BLOB_SHA)


def test_tree_truncation_is_rejected(workflow_run: WorkflowRunReference) -> None:
    with client_returning(response(data=tree([]) | {"truncated": True})) as client:
        with pytest.raises(InvalidAPIResponse, match="tree was truncated"):
            client.get_tree(workflow_run, HEAD_SHA)


def test_secondary_rate_limit_without_headers(workflow_run: WorkflowRunReference) -> None:
    with client_returning(
        response(403, data={"message": "You have exceeded a secondary rate limit."})
    ) as client:
        with pytest.raises(RateLimited):
            client.get_workflow_run(workflow_run)


def test_second_log_redirect_is_not_followed(workflow_run: WorkflowRunReference) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response(302, headers={"location": "https://downloads.example.test/log"})

    with GitHubClient(token="", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LogsUnavailable, match="redirected again"):
            client.get_job_log(workflow_run, 2, 7)
    assert len(calls) == 2


def test_config_at_exact_size_limit(workflow_run: WorkflowRunReference) -> None:
    with client_returning(
        response(data=config("pyproject.toml", b"a" * MAX_CONFIG_BYTES))
    ) as client:
        file = client.get_configuration(workflow_run, "pyproject.toml", HEAD_SHA, BLOB_SHA)
    assert file.byte_count == MAX_CONFIG_BYTES and not file.truncated


def test_decoded_configuration_size_cannot_trust_declared_size(
    workflow_run: WorkflowRunReference,
) -> None:
    with client_returning(
        response(data=config("pyproject.toml", b"a" * (MAX_CONFIG_BYTES + 1), size=1))
    ) as client:
        with pytest.raises(DownloadTooLarge):
            client.get_configuration(workflow_run, "pyproject.toml", HEAD_SHA, BLOB_SHA)


def test_missing_config_contents_resource(workflow_run: WorkflowRunReference) -> None:
    from ci_investigator.github_errors import ResourceNotFound

    with client_returning(response(404)) as client:
        with pytest.raises(ResourceNotFound):
            client.get_configuration(workflow_run, "pyproject.toml", HEAD_SHA, BLOB_SHA)


def test_malformed_api_body_is_sanitized(workflow_run: WorkflowRunReference) -> None:
    with client_returning(response(data=workflow(head_sha="secret-token"))) as client:
        with pytest.raises(InvalidAPIResponse) as caught:
            client.get_workflow_run(workflow_run)
    assert "secret-token" not in str(caught.value)


def test_completed_job_requires_conclusion(workflow_run: WorkflowRunReference) -> None:
    with client_returning(
        response(data={"total_count": 1, "jobs": [job(conclusion=None)]})
    ) as client:
        with pytest.raises(InvalidAPIResponse):
            client.get_jobs(workflow_run, 2, HEAD_SHA)


def test_pagination_change_requires_retry(workflow_run: WorkflowRunReference) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        calls.append(page)
        return response(data={"total_count": page + 1, "jobs": [job(page)]})

    with GitHubClient(token="", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(InvalidAPIResponse, match="changed during pagination"):
            client.get_jobs(workflow_run, 2, HEAD_SHA)
    assert calls == [1, 2]


def test_log_line_count_preserves_physical_lf_lines(workflow_run: WorkflowRunReference) -> None:
    text = "first\u2028still first\rprogress\nsecond\n"
    with client_returning(response(content=text.encode())) as client:
        log = client.get_job_log(workflow_run, 2, 7)
    assert log.text == text
    assert log.line_count == 2


@pytest.mark.parametrize("value", [0, -1, True, "2"])
def test_client_rejects_invalid_attempt_before_request(
    workflow_run: WorkflowRunReference, value: object
) -> None:
    with client_returning(response(500)) as client:
        with pytest.raises(InvalidAttempt):
            client.get_jobs(workflow_run, value, HEAD_SHA)  # type: ignore[arg-type]

import httpx
import pytest
from github_fixtures import (
    API_REPO,
    GITHUB_SHA,
    HEAD_SHA,
    RUN_URL,
    GitHubScenario,
    file_entry,
    job,
    response,
    tree,
    workflow,
)

from ci_investigator.cli import main
from ci_investigator.collector import GitHubCollector, select_run_from_url
from ci_investigator.github_client import (
    CONFIG_PATHS,
    MAX_CONFIG_BYTES,
    MAX_LOG_BYTES,
    GitHubClient,
)
from ci_investigator.github_errors import (
    GitHubServerError,
    InvalidAPIResponse,
    InvalidAttempt,
    RateLimited,
    RequestTimeout,
)
from ci_investigator.url_parser import parse_workflow_run_url


def collect(scenario: GitHubScenario, url: str = RUN_URL, **kwargs: object):
    with GitHubClient(token="test-token", transport=httpx.MockTransport(scenario)) as client:
        return GitHubCollector(client).collect(url, **kwargs)


def test_one_failed_job_collects_facts_and_safe_serialization() -> None:
    scenario = GitHubScenario()
    result = collect(scenario)
    assert result.outcome == "failed"
    assert result.selected_attempt == 2 and result.head_sha == HEAD_SHA
    assert result.workflow.status == "completed" and result.workflow.conclusion == "failure"
    assert [step.number for step in result.failed_jobs[0].failed_steps] == [2]
    assert result.logs[0].text == "first line\nsecond line\n"
    assert [file.path for file in result.configurations if file.availability == "available"] == [
        ".github/workflows/ci.yml",
        "pyproject.toml",
    ]
    assert all(request.method == "GET" for request in scenario.requests)
    assert all(
        request.url.params["ref"] == HEAD_SHA
        for request in scenario.requests
        if "/contents/" in request.url.path
    )
    assert all(request.url.params.get("ref") != "main" for request in scenario.requests)
    serialized = result.model_dump_json()
    assert "first line" not in serialized and "x=1" not in serialized
    assert "first line" not in repr(result) and "x=1" not in repr(result)


@pytest.mark.parametrize("url", [RUN_URL, f"{RUN_URL}?attempt=2"])
def test_latest_attempt_uses_attempt_specific_jobs(url: str) -> None:
    scenario = GitHubScenario()
    result = collect(scenario, url)
    assert result.selected_attempt == 2
    paths = [request.url.path for request in scenario.requests]
    assert f"{API_REPO}/actions/runs/42/attempts/2/jobs" in paths
    assert f"{API_REPO}/actions/runs/42/jobs" not in paths


@pytest.mark.parametrize("use_url", [False, True])
def test_explicit_older_attempt_uses_its_status_and_jobs(use_url: bool) -> None:
    scenario = GitHubScenario(
        run=workflow(status="in_progress", conclusion=None), jobs=[job(run_attempt=1)]
    )
    result = collect(scenario, f"{RUN_URL}?attempt=1") if use_url else collect(scenario, attempt=1)
    assert result.selected_attempt == 1 and result.outcome == "failed"
    assert result.workflow.status == "completed"
    paths = [request.url.path for request in scenario.requests]
    assert f"{API_REPO}/actions/runs/42/attempts/1" in paths
    assert f"{API_REPO}/actions/runs/42/attempts/1/jobs" in paths
    assert f"{API_REPO}/actions/runs/42/attempts/2/jobs" not in paths


@pytest.mark.parametrize(
    "query",
    [
        "attempt=0",
        "attempt=-1",
        "attempt=1.5",
        "attempt=abc",
        "attempt=",
        "attempt=1&attempt=2",
        "attempt=2&attempt=2",
        "attempt=%20",
        "attempt=٢",
    ],
)
def test_invalid_attempt_queries_fail_before_network(query: str) -> None:
    scenario = GitHubScenario()
    with pytest.raises(InvalidAttempt):
        collect(scenario, f"{RUN_URL}?{query}")
    assert scenario.requests == []


@pytest.mark.parametrize("attempt", [0, -1, True, "2"])
def test_invalid_attempt_argument_fails_before_network(attempt: object) -> None:
    scenario = GitHubScenario()
    with pytest.raises(InvalidAttempt):
        collect(scenario, attempt=attempt)
    assert scenario.requests == []


def test_out_of_range_attempt_does_not_request_jobs() -> None:
    scenario = GitHubScenario()
    with pytest.raises(InvalidAttempt, match="latest"):
        collect(scenario, attempt=3)
    assert len(scenario.requests) == 1


def test_duplicate_attempt_sources_are_rejected() -> None:
    scenario = GitHubScenario()
    with pytest.raises(InvalidAttempt, match="not both"):
        collect(scenario, f"{RUN_URL}?attempt=2", attempt=2)
    assert scenario.requests == []


def test_foundation_parser_remains_compatible() -> None:
    url = f"{RUN_URL}?attempt=invalid"
    assert parse_workflow_run_url(url).url == RUN_URL
    with pytest.raises(InvalidAttempt):
        select_run_from_url(url)


def test_partial_rerun_never_merges_previous_jobs() -> None:
    scenario = GitHubScenario(jobs=[])
    result = collect(scenario)
    assert result.outcome == "insufficient_data"
    assert result.failed_jobs == result.logs == []
    assert any("earlier attempts" in limitation for limitation in result.limitations)
    assert not any("attempts/1" in request.url.path for request in scenario.requests)
    assert any(warning.code == "no_failed_jobs" for warning in result.warnings)


@pytest.mark.parametrize("conclusion", ["success", "cancelled", "failure"])
def test_no_failed_jobs_has_explicit_outcome(conclusion: str) -> None:
    scenario = GitHubScenario(run=workflow(conclusion=conclusion), jobs=[job(conclusion="success")])
    result = collect(scenario)
    assert result.outcome == ("successful" if conclusion == "success" else "insufficient_data")
    assert not result.failed_jobs and not result.logs
    assert not any(request.url.path.endswith("/logs") for request in scenario.requests)


def test_incomplete_run_is_provisional() -> None:
    scenario = GitHubScenario(
        run=workflow(status="in_progress", conclusion=None),
        jobs=[job(status="in_progress", conclusion=None)],
    )
    result = collect(scenario)
    assert result.outcome == "incomplete"
    assert result.logs == []
    assert any(warning.code == "incomplete_run" for warning in result.warnings)


def test_three_lowest_failed_job_ids_are_selected() -> None:
    scenario = GitHubScenario(jobs=[job(9), job(3), job(7), job(2), job(1, conclusion="success")])
    result = collect(scenario)
    assert [entry.id for entry in result.failed_jobs] == [2, 3, 7]
    assert result.failed_job_count == 4 and result.job_count == 5
    assert len(result.logs) == 3
    assert any(warning.code == "job_limit" for warning in result.warnings)
    assert not any("/jobs/9/logs" in request.url.path for request in scenario.requests)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_inaccessible_run_returns_explicit_result(status: int) -> None:
    scenario = GitHubScenario()
    scenario.overrides[f"{API_REPO}/actions/runs/42"] = response(status)
    result = collect(scenario)
    assert result.outcome == "inaccessible" and result.workflow is None
    assert len(scenario.requests) == 1 and result.warnings


@pytest.mark.parametrize(("status", "error"), [(503, GitHubServerError), (429, RateLimited)])
def test_required_api_failures_propagate(status: int, error: type[Exception]) -> None:
    scenario = GitHubScenario()
    scenario.overrides[f"{API_REPO}/actions/runs/42/attempts/2/jobs"] = response(status)
    with pytest.raises(error):
        collect(scenario)
    assert len(scenario.requests) == 2


def test_unavailable_log_is_partial_and_configurations_continue() -> None:
    scenario = GitHubScenario(jobs=[job(7), job(8)])
    scenario.overrides[f"{API_REPO}/actions/jobs/7/logs"] = response(410)
    result = collect(scenario)
    assert result.outcome == "failed" and len(result.logs) == 1
    assert result.logs[0].job_id == 8
    assert any(warning.code == "logs_unavailable" for warning in result.warnings)
    assert any(file.availability == "available" for file in result.configurations)


def test_truncated_logs_and_decoding_generate_warnings() -> None:
    scenario = GitHubScenario()
    scenario.overrides[f"{API_REPO}/actions/jobs/7/logs"] = response(
        content=b"\xff" * (MAX_LOG_BYTES + 1)
    )
    result = collect(scenario)
    assert result.logs[0].truncated and result.logs[0].decoding_replacements
    assert {warning.code for warning in result.warnings} >= {"log_truncated", "log_decoding"}


def test_truncated_prefix_can_omit_the_actual_failure() -> None:
    scenario = GitHubScenario()
    failure = b"FINAL FAILURE: missing required dependency\n"
    prefix = b"progress output\n" * (MAX_LOG_BYTES // len(b"progress output\n"))
    assert len(prefix) == MAX_LOG_BYTES
    scenario.overrides[f"{API_REPO}/actions/jobs/7/logs"] = response(content=prefix + failure)
    result = collect(scenario)
    log = result.logs[0]
    assert log.truncated and log.byte_count == MAX_LOG_BYTES
    assert log.text == prefix.decode()
    assert "FINAL FAILURE" not in log.text
    warning = next(warning for warning in result.warnings if warning.code == "log_truncated")
    assert warning.source_identifier == log.source_identifier
    assert "end of the log" in warning.message
    assert "actual failure" in warning.message
    assert "may be missing" in warning.message


def test_optional_rate_limit_stops_remaining_requests() -> None:
    scenario = GitHubScenario(jobs=[job(7), job(8)])
    scenario.overrides[f"{API_REPO}/actions/jobs/7/logs"] = response(429)
    result = collect(scenario)
    assert result.logs == []
    assert all(file.availability == "unavailable" for file in result.configurations)
    assert len(scenario.requests) == 3
    assert any(warning.code == "logs_skipped" for warning in result.warnings)


def test_missing_optional_configuration_is_recorded() -> None:
    result = collect(GitHubScenario(files=[]))
    missing = [file for file in result.configurations if file.availability == "missing"]
    assert [file.path for file in missing] == list(CONFIG_PATHS)
    assert all(file.reason for file in missing)
    assert result.configurations[0].availability == "available"


def test_file_selection_is_deterministic_and_limited_to_five() -> None:
    scenario = GitHubScenario(files=list(reversed(CONFIG_PATHS)))
    result = collect(scenario)
    available = [file.path for file in result.configurations if file.availability == "available"]
    assert available == [".github/workflows/ci.yml", *CONFIG_PATHS[:4]]
    skipped = [file for file in result.configurations if file.availability == "skipped_limit"]
    assert [file.path for file in skipped] == ["tox.ini", "setup.cfg"]
    assert len([request for request in scenario.requests if "/contents/" in request.url.path]) == 5


@pytest.mark.parametrize("ancestor_symlink", [False, True])
def test_symlink_or_symlink_parent_never_fetches_target(ancestor_symlink: bool) -> None:
    scenario = GitHubScenario()
    if ancestor_symlink:
        entries = [file_entry(".github", mode="120000"), file_entry("pyproject.toml")]
        rejected = ".github/workflows/ci.yml"
    else:
        entries = [
            {"path": ".github", "mode": "040000", "type": "tree", "sha": GITHUB_SHA},
            file_entry("pyproject.toml", mode="120000"),
        ]
        rejected = "pyproject.toml"
    scenario.overrides[f"{API_REPO}/git/trees/{HEAD_SHA}"] = response(data=tree(entries))
    result = collect(scenario)
    assert (
        next(file for file in result.configurations if file.path == rejected).availability
        == "symlink"
    )
    assert not any(
        request.url.path.endswith(f"/contents/{rejected}") for request in scenario.requests
    )


def test_oversized_file_is_skipped_before_contents_download() -> None:
    scenario = GitHubScenario(run=workflow(path=None, workflow_id=None))
    scenario.overrides[f"{API_REPO}/git/trees/{HEAD_SHA}"] = response(
        data=tree(
            [
                file_entry("pyproject.toml", size=MAX_CONFIG_BYTES + 1),
                file_entry("requirements.txt"),
            ]
        )
    )
    result = collect(scenario)
    assert result.configurations[0].availability == "oversized"
    assert not any(
        request.url.path.endswith("/contents/pyproject.toml") for request in scenario.requests
    )
    assert result.configurations[1].availability == "available"


def test_invalid_configuration_does_not_stop_other_files() -> None:
    scenario = GitHubScenario()
    scenario.overrides[f"{API_REPO}/contents/.github/workflows/ci.yml"] = response(
        content=b"not JSON"
    )
    result = collect(scenario)
    assert result.configurations[0].availability == "invalid"
    assert result.configurations[1].availability == "available"


def test_missing_workflow_path_is_resolved_by_identifier() -> None:
    scenario = GitHubScenario(run=workflow(path=None))
    result = collect(scenario)
    assert result.configurations[0].path == ".github/workflows/ci.yml"
    assert any(request.url.path.endswith("/actions/workflows/10") for request in scenario.requests)


def test_untrusted_workflow_path_is_not_fetched() -> None:
    scenario = GitHubScenario(run=workflow(path="../../credentials.json"))
    result = collect(scenario)
    assert all(file.path in CONFIG_PATHS for file in result.configurations)
    assert any(warning.code == "workflow_path_unresolved" for warning in result.warnings)
    assert not any("credentials" in request.url.path for request in scenario.requests)


def test_run_without_sha_propagates_invalid_response() -> None:
    with pytest.raises(InvalidAPIResponse):
        collect(GitHubScenario(run=workflow(head_sha=None)))


def test_cli_prints_only_safe_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    scenario = GitHubScenario()
    monkeypatch.setattr(
        "ci_investigator.cli.GitHubClient",
        lambda: GitHubClient(token="test-token", transport=httpx.MockTransport(scenario)),
    )
    assert main([RUN_URL]) == 0
    output = capsys.readouterr().out
    assert "first line" not in output and "x=1" not in output and "test-token" not in output
    assert '"attempt": 2' in output and "Run pytest" in output


def test_stream_deadline_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    # Replace only the reader's clock, avoiding sleeps and real network timing.
    ticks = iter([0.0, 61.0])
    monkeypatch.setattr("ci_investigator.github_client.time.monotonic", lambda: next(ticks))
    with pytest.raises(RequestTimeout, match="streaming deadline"):
        GitHubClient._read_bounded(response(content=b"log"), MAX_LOG_BYTES)

"""Minimal realistic GitHub responses and a streaming mock response helper."""

import base64
import json

import httpx

HEAD_SHA = "a" * 40
ROOT_SHA = "b" * 40
GITHUB_SHA = "c" * 40
WORKFLOWS_SHA = "d" * 40
BLOB_SHA = "e" * 40
RUN_URL = "https://github.com/example/project/actions/runs/42"
API_REPO = "/repos/example/project"


def response(
    status: int = 200,
    *,
    data: object | None = None,
    content: bytes = b"",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    if data is not None:
        content = json.dumps(data).encode()
    return httpx.Response(status, stream=httpx.ByteStream(content), headers=headers)


def workflow(**changes: object) -> dict[str, object]:
    return {
        "id": 42,
        "name": "CI",
        "status": "completed",
        "conclusion": "failure",
        "run_attempt": 2,
        "repository": {"owner": {"login": "example"}, "name": "project"},
        "head_sha": HEAD_SHA,
        "event": "push",
        "workflow_id": 10,
        "path": ".github/workflows/ci.yml",
        **changes,
    }


def job(job_id: int = 7, **changes: object) -> dict[str, object]:
    return {
        "id": job_id,
        "run_id": 42,
        "run_attempt": 2,
        "head_sha": HEAD_SHA,
        "name": f"Python tests {job_id}",
        "status": "completed",
        "conclusion": "failure",
        "steps": [
            {"name": "Set up Python", "number": 1, "status": "completed", "conclusion": "success"},
            {"name": "Run pytest", "number": 2, "status": "completed", "conclusion": "failure"},
            {"name": "Publish", "number": 3, "status": "completed", "conclusion": "skipped"},
        ],
        **changes,
    }


def file_entry(path: str, **changes: object) -> dict[str, object]:
    return {"path": path, "mode": "100644", "type": "blob", "sha": BLOB_SHA, "size": 4, **changes}


def tree(entries: list[dict[str, object]], sha: str = ROOT_SHA) -> dict[str, object]:
    return {"sha": sha, "tree": entries, "truncated": False}


def config(raw_path: str, raw_content: bytes = b"x=1\n", **changes: object) -> dict[str, object]:
    return {
        "type": "file",
        "path": raw_path,
        "sha": BLOB_SHA,
        "size": len(raw_content),
        "encoding": "base64",
        "content": base64.b64encode(raw_content).decode(),
        **changes,
    }


class GitHubScenario:
    """Record outgoing requests and provide a small repo with one workflow file."""

    def __init__(
        self,
        *,
        run: dict[str, object] | None = None,
        jobs: list[dict[str, object]] | None = None,
        files: list[str] | None = None,
    ) -> None:
        self.run = workflow() if run is None else run
        self.jobs = [job()] if jobs is None else jobs
        self.files = ["pyproject.toml"] if files is None else files
        self.requests: list[httpx.Request] = []
        self.overrides: dict[str, httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in self.overrides:
            return self.overrides[path]
        if path == f"{API_REPO}/actions/runs/42":
            return response(data=self.run)
        if path == f"{API_REPO}/actions/runs/42/attempts/1":
            return response(data=workflow(run_attempt=1))
        if path.endswith("/jobs"):
            return response(data={"total_count": len(self.jobs), "jobs": self.jobs})
        if path.endswith("/logs"):
            return response(
                302, headers={"location": "https://logs.example.test/download?signed=secret"}
            )
        if request.url.host == "logs.example.test":
            return response(content=b"first line\nsecond line\n")
        if path == f"{API_REPO}/git/trees/{HEAD_SHA}":
            return response(
                data=tree(
                    [
                        {"path": ".github", "mode": "040000", "type": "tree", "sha": GITHUB_SHA},
                        *[file_entry(name) for name in self.files],
                    ]
                )
            )
        if path == f"{API_REPO}/git/trees/{GITHUB_SHA}":
            return response(
                data=tree(
                    [{"path": "workflows", "mode": "040000", "type": "tree", "sha": WORKFLOWS_SHA}],
                    GITHUB_SHA,
                )
            )
        if path == f"{API_REPO}/git/trees/{WORKFLOWS_SHA}":
            return response(data=tree([file_entry("ci.yml")], WORKFLOWS_SHA))
        if "/contents/" in path:
            return response(data=config(path.split("/contents/", 1)[1]))
        if path == f"{API_REPO}/actions/workflows/10":
            return response(data={"id": 10, "path": ".github/workflows/ci.yml"})
        raise AssertionError(f"Unexpected mock request: {request.method} {request.url}")

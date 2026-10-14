# CI Investigator

An AI-powered GitHub Actions failure investigation tool. The foundation and
read-only GitHub data collection layer are implemented. Python 3.11+ is required.

## Install and test

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check .
```

Installation downloads dependencies. All unit tests and foundation validation work
offline without GitHub tokens or Google Cloud credentials. Collection uses GitHub
HTTP requests. Tests use `httpx.MockTransport` and block socket creation and DNS
lookups to detect accidental network use. GitHub Actions runs
the same lint and test commands on pushes and pull requests with Python 3.11.

## Collect a workflow run

```sh
ci-investigator "https://github.com/hussenyo12/ci-investigator/actions/runs/38597354478"
ci-investigator "https://github.com/OWNER/REPO/actions/runs/RUN_ID?attempt=2"
# Alternatively, select an attempt with --attempt 2, without an attempt query.
python -m ci_investigator.cli "https://github.com/OWNER/REPO/actions/runs/RUN_ID" --attempt 2
```

Replace `OWNER`, `REPO`, and `RUN_ID` with the public run to collect. With no
selection, collection uses the latest attempt observed when fetching run metadata.
A selected attempt must be positive, available, and no greater than the latest.
Duplicate query parameters or specifying both a query and an argument are rejected.
Jobs always come from the attempt-specific endpoint. Partial reruns may rely on
jobs from earlier attempts; those jobs are not silently merged into this result.

The CLI prints JSON metadata: workflow status, selected attempt, commit SHA, failed
job/step names, log line counts, configuration availability, warnings, and
limitations. It prints no raw logs or configuration contents, and writes no raw
data to disk. Exit status is 1 for inaccessible runs or collection errors, and 0
for collected results, including failed, incomplete, and insufficient-data runs.

Public collection attempts anonymous access if `GITHUB_TOKEN` is unset or empty.
Export that variable in your shell when authentication is needed. Fine-grained
tokens need repository **Actions: read** and **Contents: read** permissions; classic
tokens need `repo` for private repositories. Some resources can still be hidden,
expired, unavailable, or rate-limited. The package does not automatically load
`.env`, and it does not use Google credentials.

Library callers can inject an HTTP transport and own the client lifetime:

```python
from ci_investigator.collector import GitHubCollector
from ci_investigator.github_client import GitHubClient

with GitHubClient() as client:
    collection = GitHubCollector(client).collect(
        "https://github.com/hussenyo12/ci-investigator/actions/runs/38597354478"
    )
```

`CollectionResult` contains facts, not hypotheses or an `InvestigationReport`.
Its outcomes are `successful`, `failed`, `incomplete`, `insufficient_data`, and
`inaccessible`. A completed non-success run without failed jobs has insufficient
data; no diagnosis is invented. Required network, rate-limit, server, provenance,
or response-validation failures raise typed `GitHubError` subclasses. Missing or
denied run/job access returns `inaccessible`. Optional retrieval errors preserve
other collected facts and generate warnings; rate limiting stops later optional
requests. See [architecture](docs/architecture.md) for the exact error policy.

## Collection limits and security

- Jobs: paginate with 100 per page, capped at 20 pages/2,000 jobs. Select at most
  three completed failed jobs by ascending job ID. Failure outcomes include
  `failure`, `timed_out`, `startup_failure`, and `action_required`.
- Logs: stream at most 1 MiB per selected job, retaining a prefix and recording
  truncation. Preserve ordering and newline characters; LF-delimited line numbers
  start at 1, including CRLF logs.
  Invalid UTF-8 becomes U+FFFD, with a warning. A cutoff inside a UTF-8 character
  can also require replacement. One HTTPS redirect is allowed, using a separate
  unauthenticated client; non-public IP literals, obvious local/internal hosts,
  subsequent redirects, and compressed responses are rejected. A retained prefix
  may omit the end of the log, including the actual failure; the warning says so.
- Configuration: collect at most five valid files of at most 128 KiB each. Try
  the associated workflow YAML, then `pyproject.toml`, `requirements.txt`,
  `requirements-dev.txt`, `pytest.ini`, `tox.ini`, and `setup.cfg`, in that order.
  Missing/invalid candidates do not consume the five-file limit. Every candidate
  gets an availability record and a reason when omitted. Oversized files are
  omitted entirely. Valid base64 and UTF-8 are required; binary NULs are rejected.
- Fetch configuration only at the validated run `head_sha`, with no branch fallback.
  Git tree modes reject symlinks, symlink parents, and non-file entries before
  Contents API retrieval. This SHA may differ from a job's actual checkout.
- Requests: no automatic retries, 5-second connection/20-second operation timeouts,
  and a 60-second deadline checked between streaming chunks. API bodies are capped
  at 2 MiB, Contents bodies at 256 KiB. Environment proxies are disabled.

Raw log text and configuration content remain in memory and are excluded from
model representations and default serialization. Library attributes still expose
raw data for the next processing stage: they are **unredacted**, must remain
internal, and must never be sent directly to an LLM. Exception messages exclude
response bodies, authentication headers, and signed redirect URLs. Future secret
redaction and evidence extraction are not implemented yet.

Redirect hostname checks do not resolve DNS or verify the connected peer's address.
Allowed names can still resolve to private addresses or undergo DNS rebinding;
these checks do not completely prevent SSRF. Future diagnosis must account for
truncation and allow an insufficient-evidence outcome when relevant failure context
is missing. See [review findings and limitations](docs/architecture.md) for details.

## Foundation API

```python
from ci_investigator import (
    InvestigationReport,
    parse_workflow_run_url,
    validate_report_evidence,
)

run = parse_workflow_run_url(
    "https://github.com/hussenyo12/ci-investigator/actions/runs/123?attempt=2"
)
report = InvestigationReport(
    workflow_run=run,
    observed_failure_summary="Failure details are unavailable until logs are collected.",
    recommended_next_steps=["Collect the failed job logs from this workflow run."],
    limitations=["No logs or repository configuration have been collected."],
    status="insufficient_evidence",
)
validate_report_evidence(report)
```

The example only parses an identity; it does not check whether the run exists.
The parser accepts HTTPS URLs on exactly `github.com`, optional query parameters,
and one trailing slash. It rejects ports (including 443), credentials, fragments,
spoofed domains, encoded path components, and malformed paths. Query parameters
are discarded, so this identity does not select a particular rerun attempt.
Invalid inputs raise `WorkflowRunURLParseError`.

## Architecture and boundaries

- `schemas.py`: strict Pydantic v2 models for runs, evidence, hypotheses, and reports.
- `url_parser.py`: deterministic URL parsing and canonicalization without networking.
- `evidence_validator.py`: unique evidence IDs, existing and non-repeated references,
  and at least one hypothesis for a `diagnosed` report, with every hypothesis
  referencing at least one existing evidence ID.

Call `validate_report_evidence` after constructing a report and before consuming
or displaying it; model construction alone does not validate reference integrity.
It raises `EvidenceValidationError` for inconsistent references. Reports marked
`insufficient_evidence` can contain no evidence or hypotheses. Suggested
verification and next steps must be nonempty; limitations may be an empty list.

Evidence uses inclusive, one-based original source line numbers and preserves
text whitespace. Validation checks field shape and reference integrity; it does
**not** authenticate source text, verify that line numbers match a source, or prove
that an explanation is correct. Confidence labels (`low`, `medium`, `high`) are
qualitative judgments, not calibrated probabilities.

The intended pipeline is URL parsing → GitHub collection at the workflow run's
associated commit → evidence extraction and sanitization → Gemini reasoning through
Vertex AI → deterministic reference validation → Streamlit presentation. Gemini,
evidence sanitization, diagnosis, and Streamlit are not implemented yet.
See [architecture](docs/architecture.md) and the [development log](docs/development_log.md).

`.env.example` documents the optional GitHub token and reserves placeholder names
for future Vertex AI integration. Keep real secrets in ignored local files;
never add credential JSON files or tokens to the repository.

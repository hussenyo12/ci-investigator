# CI Investigator

An AI-powered GitHub Actions failure investigation tool. The foundation, read-only
GitHub collection, deterministic evidence preparation, and opt-in Gemini diagnosis
through Vertex AI are implemented. Python 3.11+ is required. No UI or autonomous
code modification is implemented.

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
`.env`. Collection and evidence previews do not use Google credentials.

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
response bodies, authentication headers, and signed redirect URLs. Use the
processing layer below to prepare bounded, sanitized evidence from this raw data.

Redirect hostname checks do not resolve DNS or verify the connected peer's address.
Allowed names can still resolve to private addresses or undergo DNS rebinding;
these checks do not completely prevent SSRF. Future diagnosis must account for
truncation and allow an insufficient-evidence outcome when relevant failure context
is missing. See [review findings and limitations](docs/architecture.md) for details.

## Prepare and preview evidence

```sh
ci-investigator "https://github.com/OWNER/REPO/actions/runs/RUN_ID" --preview-evidence
ci-investigator "https://github.com/OWNER/REPO/actions/runs/RUN_ID" --preview-evidence --metadata-only
```

Preview prints run/attempt/commit identity, source IDs, evidence IDs, original line
ranges, sanitized excerpts, warnings, and limitations. `--metadata-only` omits
excerpt text. Both commands collect from GitHub; the processing itself is offline.
Sanitization failure produces a generic error with no raw-content fallback.
JSON escapes terminal control characters and unusual Unicode.

Given an existing `CollectionResult`, library callers can prepare and independently
check the excerpts without making additional requests:

```python
from ci_investigator.evidence_pipeline import prepare_evidence, validate_prepared_package
from ci_investigator.evidence_schemas import PreparationLimits

package = prepare_evidence(collection, limits=PreparationLimits(max_excerpts=12))
validate_prepared_package(package, collection)
print(package.model_dump_json(indent=2))
```

All available log/configuration text is pattern-redacted before selection. Rules
cover common GitHub tokens, unquoted or quoted Bearer credentials, HTTP Basic
credentials in Authorization/Proxy-Authorization headers, URL credentials,
API/password/secret assignments, AWS access-key IDs, and PEM private keys. Basic
header rules include common JSON fields, `=` assignments, and one layer of escaped
quote delimiters. Category counts contain no
secret values. This is **best-effort redaction**, with possible missed secrets and
false positives; prepared text is not automatically safe to send externally.

Selection prioritizes detailed Python/pytest/install failures over generic exit
messages, merges nearby context, and includes relevant collected configuration.
Defaults are 24 excerpts, 2,000 characters per excerpt, 30,000 combined characters,
and up to five context lines on each side. Whole-line ranges are split or omitted
to fit; omitted context is disclosed. Tracebacks retain continuity when practical.

Provenance checks match every excerpt exactly to its sanitized source and original
inclusive line range. LF defines physical lines; CRLF becomes LF in excerpt text.
Stable IDs depend on sanitized content and source/range identity. No full raw or
sanitized source is included in the prepared package.

`ready` means an informative failure observation was retained, not that a cause
was diagnosed. Generic exit messages, failed-step hints, or configuration alone
yield `insufficient_evidence`. Missing files/logs, truncation, collection warnings,
and processing omissions remain visible. Logs and configuration are untrusted data:
embedded instructions have no authority. No root cause or confidence is generated.

## Diagnose with Gemini through Vertex AI

Configure Application Default Credentials (ADC), using your existing setup or
`gcloud auth application-default login`. The identity needs Vertex AI access in
the selected project, with billing and the Vertex AI API enabled. This integration
uses the official `google-genai` SDK with `vertexai=True`; it does not use Gemini
API keys, Google AI Studio authentication, or service account JSON files.
`GOOGLE_APPLICATION_CREDENTIALS` must be unset for this MVP. No credentials are
read or printed by application code; credential discovery/refresh belongs to ADC.

```sh
export GOOGLE_CLOUD_PROJECT=drivernets
export GOOGLE_CLOUD_LOCATION=global
export VERTEX_AI_MODEL=gemini-2.5-flash
# Optional: VERTEX_AI_TIMEOUT_MS=60000, VERTEX_AI_MAX_OUTPUT_TOKENS=4096

# First inspect sanitized evidence locally:
ci-investigator "https://github.com/OWNER/REPO/actions/runs/RUN_ID" --preview-evidence
# This invocation explicitly authorizes external transmission and possible charges:
ci-investigator "https://github.com/OWNER/REPO/actions/runs/RUN_ID" --ai-diagnose --allow-external-ai
```

**Redaction is best-effort.** Review the preview and your organization's data policy
before opting in: unknown or obfuscated secrets may remain. Both AI flags are
required; missing consent fails before collection. Preview and metadata commands
never call Gemini. AI and preview flags cannot be combined. The application does
not log or persist prompts, raw responses, or full sources by default.

Defaults above can be changed through the process environment or `GeminiConfig`.
Timeouts are bounded to 1–120 seconds per generation request; output to 512–8,192
tokens (default 4,096), followed by a 32,000-character response check. SDK retries
are disabled. One fresh generation can follow a complete draft that fails local
validation; rejected output is never fed back. Authentication, quota, permission,
network, timeout, blocked, empty, and incomplete output errors are not retried.
Timeouts cover SDK HTTP operations, not a single wall-clock deadline for ADC
discovery/refresh and the entire command.

Only `PreparedEvidencePackage` enters the prompt. `ci-diagnosis-v1` keeps fixed
system instructions separate from a JSON envelope named `untrusted_evidence_data`.
The envelope contains sanitized excerpts, evidence/source IDs and ranges,
run/attempt/commit identifiers, warnings, and completeness flags. No raw collection
logs, original configuration, credentials, ground-truth labels, or arbitrary
repository files are sent. The model has no tools or browsing. The engine adds a
30,000-character excerpt budget and a 12,000-character metadata budget; oversized
packages are rejected without silently dropping context or limitations.

Gemini returns only a strict `DiagnosisDraft`: `observed_failure_summary`,
`status` (`diagnosed` or `insufficient_evidence`), `hypotheses`,
`recommended_next_steps`, and `limitations`. Each hypothesis contains an
explanation, provided evidence IDs, `low`/`medium`/`high` confidence, and verification
steps. The application builds the existing `InvestigationReport` from trusted
package identity/evidence, then runs `validate_report_evidence()`. Unknown fields,
malformed/duplicate-key JSON, invented or repeated references, unsupported
hypotheses, and blank required fields are rejected before presentation.

Insufficient packages and workflows without failed jobs return a local
`insufficient_evidence` report without initializing Gemini or ADC. This existing
report status also represents “nothing to investigate”; read the summary for the
distinction. Useful incomplete evidence can be investigated, but all pipeline
warnings/limitations remain visible and high-confidence hypotheses are rejected.
The model can abstain too. Diagnosis errors print safe JSON and exit 1; accepted
reports and local abstentions exit 0 unless collection was inaccessible.

Library use requires the caller to obtain consent and verify the package first:

```python
from ci_investigator.diagnosis_engine import diagnose
from ci_investigator.evidence_pipeline import prepare_evidence, validate_prepared_package

package = prepare_evidence(collection)
validate_prepared_package(package, collection)
# Only after the user has consented to transmitting this prepared evidence:
report = diagnose(
    package,
    has_failed_jobs=collection.failed_job_count > 0 and collection.outcome != "successful",
)
```

Tests inject a `DiagnosisClient` implementing `generate(DiagnosisPrompt) -> str`.
The failed-job boolean is a local collection gate, not additional model context.
The engine cannot reconstruct provenance from a package alone; library callers
must not substitute hand-edited or unsanitized packages for pipeline output.

Valid citations do **not** prove relevance or causal correctness. Prompt injection
can still influence prose or the choice of a valid but irrelevant citation; the
prompt reduces this risk, and deterministic checks enforce schema/reference rules
without claiming to understand causality. Confidence is qualitative, not a
probability. Human review and verification remain necessary.

Requests can incur input/output token charges, including model thinking where
applicable. The prompt, metadata, and response schema consume input tokens;
character bounds are not exact token counts. The optional validation retry can
double generation requests. There is no cost estimator, cache, or paid token-count
preflight. Consult [Vertex AI pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing)
and your project quotas rather than assuming a fixed request price.

### One authorized synthetic live smoke test

No live Gemini diagnosis was run during this implementation. The previously
successful connection test is user-reported, not a test of this diagnosis path.
After configuring ADC and reviewing the script, explicitly authorize **one**
potentially paid request from the repository root:

```sh
python scripts/gemini_smoke_test.py --help
python scripts/gemini_smoke_test.py --allow-external-ai
```

The script prepares and independently validates one synthetic missing-module log
in memory. It does not contact GitHub or read repository logs or configuration.
It uses the environment/default Vertex configuration and disables the optional
response retry, so there is at most one generation request. Without the flag it
exits before preparing or transmitting data. A valid report can diagnose or abstain;
an error exits 1 without showing the raw response. No artifacts are saved.

A successful live run verifies current ADC/project permissions, model availability,
Vertex transport, server acceptance of the structured schema, and end-to-end
validation of one synthetic response. It does not verify real workflow reasoning,
causal correctness, complete secret redaction, injection resistance against a real
model, quota recovery, all models/regions, or production latency/cost. Offline
tests cover those deterministic error paths with fakes, not live service behavior.

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
Vertex AI → deterministic reference validation → Streamlit presentation. Preparation
redacts before selection and verifies source provenance. Gemini diagnosis now
feeds the validated CLI report; Streamlit remains unimplemented.
See [architecture](docs/architecture.md) and the [development log](docs/development_log.md).

`.env.example` documents the optional GitHub token and current Vertex AI defaults.
It is documentation, not an automatically loaded configuration file.
Keep real secrets in ignored files;
never add credential JSON files or tokens to the repository.

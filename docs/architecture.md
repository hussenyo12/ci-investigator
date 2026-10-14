# Architecture

## Implemented foundation

The package exposes four Pydantic models, `parse_workflow_run_url`, and
`validate_report_evidence`. Pydantic validates models; the separate collection
layer uses HTTPX for networking. The standard library URL parser performs no DNS
resolution or HTTP requests.

Models reject unknown fields and type coercion, including boolean, float, or
string run IDs and line numbers. Mandatory text and list items cannot be blank.
Evidence text retains whitespace; other descriptive strings and IDs are trimmed.
Run URLs must agree with their owner, repository, and positive run ID. Original
line ranges are inclusive, one-based, and must have `end_line >= start_line`.
Evidence, hypotheses, and limitations can be empty; action lists cannot.

Evidence IDs are report-local identifiers. The explicit reference validator checks
every hypothesis in a report that passes validation. Separate hypotheses may cite
the same evidence. Each hypothesis must avoid repeated references, and every
reference must resolve to exactly one evidence record. A `diagnosed` report must
contain at least one hypothesis, and every hypothesis must reference at least one
existing evidence ID, regardless of its confidence label. An `insufficient_evidence`
report may omit hypotheses entirely or contain tentative hypotheses with no
references. Any references it includes must still pass the integrity checks.

This separation permits parsing a well-shaped AI candidate before checking its
links. Consumers must run reference validation before accepting a report, and run
it again if its collections change. Successful validation only establishes link
integrity. It does not validate causality, evidence authenticity, source-file
existence, excerpt completeness, or whether text matches its claimed line range.
Confidence remains qualitative.

## F-001: policy tightening after independent review

The independent review identified that the original validator accepted a
`diagnosed` report containing both a supported hypothesis and an unsupported
high-confidence hypothesis. The original policy required support for at least one
hypothesis, so the full report passed despite the unsupported explanation. This
met Task 1's initial acceptance criteria; Task 1.1 intentionally strengthens the
policy based on new adversarial review evidence.

An unsupported explanation inside an accepted diagnosis could mislead readers
into treating it as evidence-backed. The validator now rejects each diagnosed
hypothesis without references, identifies its position in the error, and rejects
diagnosed reports with no hypotheses. Duplicate evidence IDs, repeated references,
and unknown evidence references remain invalid for both statuses. Regression tests
cover mixed supported/unsupported hypotheses in either order at every confidence
label, two individually supported hypotheses, and empty insufficient-evidence
reports. Actual verification results are recorded in the development log.

This policy establishes only that every diagnosed hypothesis cites existing
evidence. A citation can still be irrelevant, misleading, or factually incorrect;
automated factual or causal validation remains outside the validator's scope.

## GitHub collection layer

`collection_schemas.py` models workflow metadata, jobs, all step outcomes, internal
logs, configuration availability, warnings, and collection results. Consumed API
fields use strict Pydantic validation; additional GitHub fields are ignored.
Critical IDs, attempts, repository identity, head SHA, status, and conclusions are
validated. Completed runs and jobs require conclusions. API identity mismatches
are errors, not an excuse to substitute another run, attempt, or branch.

`github_client.py` owns API and unauthenticated download clients. Inject an
`httpx.BaseTransport` (tests use `MockTransport`) without replacing collection
logic. `github_errors.py` defines actionable typed failures. `collector.py`
coordinates retrieval, deterministic selection, and partial results. `cli.py`
prints metadata only. No `Evidence`, `Hypothesis`, or AI report is produced here.

### Endpoints, authentication, and attempt selection

All API requests are GETs to `https://api.github.com`, using the JSON media type,
a project User-Agent, and REST version `2022-11-28`. An optional process environment
`GITHUB_TOKEN` becomes a Bearer header on the API client only. No token means
anonymous access. Fine-grained tokens need Actions read for workflow/jobs/logs and
Contents read for contents/trees. Classic tokens need `repo` for private resources.
`.env` is not loaded. TLS verification stays enabled; environment proxies are
disabled. The log download client receives no API credentials or cookies.

| Purpose | Endpoint suffix after `/repos/{owner}/{repo}` |
| --- | --- |
| Latest workflow metadata | `/actions/runs/{run_id}` |
| Older selected attempt metadata | `/actions/runs/{run_id}/attempts/{attempt}` |
| Jobs for selected attempt | `/actions/runs/{run_id}/attempts/{attempt}/jobs` |
| Job log redirect | `/actions/jobs/{job_id}/logs` |
| Workflow path fallback, only if missing | `/actions/workflows/{workflow_id}` |
| File mode and blob provenance | `/git/trees/{sha}` |
| Allowlisted configuration | `/contents/{path}?ref={head_sha}` |

The existing `parse_workflow_run_url` API still discards query parameters.
`select_run_from_url` additionally recognizes exactly one case-sensitive `attempt`
query key, with a positive ASCII integer value. The collector also accepts an
explicit positive integer argument; using both mechanisms is rejected. Validate
the requested number against the latest metadata, defaulting to that observed
latest attempt. Older selections retrieve attempt-specific metadata so they do not
inherit a later attempt's status/conclusion. Each selected attempt must agree on
the run identity and associated head SHA.

Jobs use `per_page=100` and increasing numeric pages. The client does not follow
untrusted pagination links. Counts must stay consistent, IDs and step numbers must
not duplicate, and optional job attempt/SHA fields must match the selection. Empty
or inconsistent pages and more than 20 pages/2,000 jobs fail explicitly. Changing
counts in an active run require a fresh collection; there is no polling or retry.
Partial reruns may depend on jobs from earlier attempts. Those jobs are deliberately
not fetched or merged. Latest means the snapshot observed at the start of collection,
not a guarantee against a concurrent rerun beginning afterward.

### Selection, outcomes, and optional failures

Completed jobs with `failure`, `timed_out`, `startup_failure`, or `action_required`
conclusions qualify as failed. Sort by ascending job ID and retain at most three,
independent of API page ordering; warn if others are omitted. All steps of selected
jobs are retained, with a separate property for failed steps. Cancelled/skipped
jobs do not qualify as failed jobs.

An unfinished run or any unfinished job yields `incomplete`, with provisional
facts. A completed run with conclusion `success` yields `successful`. A completed
failure with failed jobs yields `failed`. Other completed results, including a
failure without qualifying jobs, yield `insufficient_data`. No failed jobs always
produces an explicit warning. These are collection outcomes, not diagnoses.

Missing or denied required workflow/job access produces an `inaccessible` result,
retaining any known metadata. Invalid attempts, malformed/provenance-mismatched
responses, essential oversized bodies, rate limiting, network failures/timeouts,
and temporary server failures propagate typed exceptions. HTTP 404 cannot reliably
distinguish a missing run from a private run hidden by GitHub.

Optional log or configuration failures preserve remaining useful facts. Logs that
cannot be read produce a warning; every configuration candidate gets a status and
reason. Rate limiting halts subsequent optional requests, recording skips. Other
optional errors continue through the bounded candidate list. No automatic retries
occur. Errors are translated without HTTP bodies or underlying exception messages,
so tokens and signed download URLs do not appear in exposed exceptions.

### Bounds, decoding, and source handling

Logs permit one HTTPS download redirect, with no embedded credentials, fragment,
or nonstandard port; even same-host downloads use the unauthenticated client. A
second redirect fails rather than starting a chain. Destinations must pass the
F-002 checks described below. External signed URLs are supported without a fixed
GitHub-host allowlist; only their hostname, never their signed query, is examined
for destination policy.
Compressed responses are rejected after requesting identity encoding, avoiding
decompression bombs. The reader caps retained data before buffering a whole log:
1 MiB plus a lookahead byte, with normal HTTP/transport chunk buffering in addition.
Excess data is not exhausted; the stream is closed and truncation is recorded.
The retained prefix may omit the end of the log, including the actual failure;
the `log_truncated` warning explicitly states this incompleteness.
An exactly 1 MiB log is not marked truncated. Decode UTF-8 with replacement on
invalid input, including a multibyte character split by the byte limit, and warn.
No line filtering, sorting, newline normalization, or secret redaction occurs.
Log text stays in original order; one-based LF-delimited line positions remain
implicit in the preserved text, including CRLF logs. Embedded Unicode separators
and bare carriage returns do not create additional physical log lines. `line_count`
includes a final partial line if present.

### F-002 and F-003: independent security and reliability review

F-002 showed that the original redirect validator accepted HTTPS destinations such
as `127.0.0.1`, `localhost`, and the link-local metadata address `169.254.169.254`.
Checking scheme and credentials did not establish a safe destination. A supplied
redirect could therefore trigger an unintended request to an internal service.

Before creating the download request, the validator now classifies IPv4/IPv6
literals with Python's [`ipaddress` module](https://docs.python.org/3.11/library/ipaddress.html).
It requires global addresses and excludes
multicast, reserved, and deprecated IPv6 site-local addresses. IPv4-mapped IPv6 is
also checked against its embedded IPv4 destination. This rejects loopback, private,
link-local, unspecified, shared, documentation, and other classified non-public
addresses. IP classification follows the installed Python standard-library tables;
use maintained Python patch releases.

Hostname checks lowercase names and ignore terminal root dots for classification.
Reject single-label names, names beginning with `localhost`, and local/internal
suffixes: `localhost`, `localdomain`, `local`, `internal`, `lan`, `home`, `home.arpa`,
`intranet`, `corp`, `private`, `svc`, and `cluster`. Require ordinary ASCII DNS label
syntax and reject percent-encoded hosts, scoped IPv6, and ambiguous numeric/hex
address forms. Errors do not echo destinations or signed URLs. Existing credential
isolation and single-redirect behavior remain in effect.

These are lexical destination checks, **not complete SSRF prevention**. Validation
does not resolve DNS or bind the connection to a verified public address. An allowed
hostname may resolve to private/link-local addresses, be overridden by local host
configuration, or change through DNS rebinding. There is no verification of the
actual connected peer's address. Network egress restrictions or connection-time
address validation would be needed for a stronger boundary. Offline regression
tests retain the socket/DNS guard and verify rejection before any download request.
Mocked public cloud/signed URLs and public IPv4/IPv6 downloads still succeed without
authentication headers or cookies.

F-003 demonstrated that the first 1 MiB can contain only progress output while the
actual failure occurs afterward. Prefix retention complied with Task 2's original
requirements; the correction strengthens disclosure, not the download algorithm.
A regression places a failure immediately after the byte limit and verifies that
it is absent from collected text, that truncation remains recorded, and that the
warning names the missing log ending and possible missing failure. No tail retrieval,
unbounded download, extraction, or diagnosis is added.

Future diagnosis must account for truncation as an evidence limitation, avoid
confident root-cause assertions from incomplete context, and permit
`insufficient_evidence` when available sources cannot support a cause. An absent
failure message in a prefix is not evidence that a failure did not occur. Evidence
reference integrity still does not prove factual or causal correctness. Actual
verification and the separately attempted live job-log check are recorded in the
development log.

### Configuration and request bounds

Try the workflow YAML first, then `pyproject.toml`, `requirements.txt`,
`requirements-dev.txt`, `pytest.ini`, `tox.ini`, and `setup.cfg`. Stop after five
available files; unavailable candidates do not consume the limit, and remaining
candidates receive `skipped_limit`. At most seven candidates are considered. Each
file is at most 128 KiB. Oversized files are omitted entirely with `oversized`
availability, not retained as partial configuration. Configuration requires valid
base64, matching declared size/blob identity/path, valid UTF-8, and no binary NUL.
Workflow paths must be exactly `.github/workflows/<filename>.yml` or `.yaml`, with
an ASCII filename containing letters, digits, underscores, hyphens, or dots.
Paths from logs are never used.

The Contents API can return a symlink target as an ordinary file. Check modes in
non-recursive Git trees at the run's head SHA first, caching the root and traversed
directory trees within a collection. Reject `120000` symlinks at every path level,
submodules, directories as files, and truncated trees. Then fetch contents with
the explicit `ref=head_sha`, verifying the returned blob SHA against the tree.
Never follow `download_url` or substitute a branch. If a path is resolved from a
workflow identifier, that metadata may reflect a later rename; missing files at
the run's commit are recorded without fallback.

HTTP requests have 5-second connection and 20-second operation timeouts, plus a
60-second streaming deadline checked between chunks (an operation timeout may
extend that deadline while waiting for a chunk). API bodies are capped at 2 MiB,
configuration response bodies at 256 KiB, and forbidden-response classification
bodies at 8 KiB. These bounds apply before JSON/base64 decoding. There is no
single overall deadline for the entire sequence of bounded requests.

Raw log/configuration contents are `repr=False` and excluded from default Pydantic
serialization. Library attributes still contain unredacted in-memory data; this is
an accidental-exposure guard, not a security boundary against a caller explicitly
copying the attributes. No raw content is saved to persistent storage, printed by
the CLI, or sent to an LLM. Sanitization is mandatory in the next development step.

API behavior references: [workflow runs](https://docs.github.com/en/rest/actions/workflow-runs),
[workflow jobs](https://docs.github.com/en/rest/actions/workflow-jobs),
[Contents API symlink behavior](https://docs.github.com/en/rest/repos/contents),
[Git trees](https://docs.github.com/en/rest/git/trees), and
[HTTPX streaming](https://www.python-httpx.org/quickstart/#streaming-responses).

## Pipeline and future work

1. Parse a public GitHub workflow run URL into a canonical identity.
2. Fetch run metadata, failed jobs, and logs. Resolve repository configuration from
   the head commit SHA attached to the run, rather than the current branch head.
   This need not be the exact checkout used by an individual job.
3. Extract relevant evidence with original source IDs and line numbers. Sanitize
   secrets before sending excerpts to an LLM; treat logs/configuration as untrusted
   data rather than instructions. Keep provenance through any transformations.
4. Ask Gemini through Vertex AI for structured hypotheses, verification steps,
   next steps, and limitations. Permit an explicit insufficient-evidence outcome.
5. Apply model validation and deterministic evidence-reference validation.
6. Present the report through Streamlit, distinguishing observations from possible
   causes and exposing evidence and limitations.

Steps 1 and 2 and the data/reference-validation portions of step 5 exist today.
No cloud SDKs, prompts, evidence extraction, orchestration, storage, or UI are included.

## Trade-offs and limitations

- Public `github.com` only; GitHub Enterprise hosts and job URLs are outside scope.
- The foundation parser ignores query parameters; collection separately handles attempts.
- Partial reruns may need earlier jobs, which are not gathered in this MVP.
- `head_sha` does not establish the exact checkout used by every job.
- Three-job and five-file limits can omit relevant context; warnings expose those omissions.
- Only common ASCII workflow filenames and a fixed Python file allowlist are supported.
- Large, compressed, malformed, or unavailable resources can yield partial collection.
- Log prefixes may omit the actual failure; future diagnosis must assess evidence sufficiency.
- Allowed hostnames may still resolve to private IPs or undergo DNS rebinding; lexical
  destination checks do not fully prevent SSRF.
- Live failed-job logs, reruns, and private-repository authentication require separate
  operational verification; mocked tests cover these branches.
- Syntactic owner/repository checks do not establish repository or run existence.
- Evidence IDs are unique within a report, not globally across investigations.
- No assumption that excerpt line count equals its original source range, allowing
  future sanitization to preserve provenance without forcing a false equality.
- Collection fields are ordinary lists; consumers must revalidate after mutation.
- Dependency version ranges permit compatible updates; no lockfile is supplied.

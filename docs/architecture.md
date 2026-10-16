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
coordinates retrieval, deterministic selection, and partial results. The CLI
defaults to metadata and optionally prepares sanitized evidence. Collection itself
produces no `Evidence`, `Hypothesis`, or AI report.

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
the CLI, or sent to an LLM. The processing layer below sanitizes these sources
before selecting evidence; this does not automatically authorize external use.

API behavior references: [workflow runs](https://docs.github.com/en/rest/actions/workflow-runs),
[workflow jobs](https://docs.github.com/en/rest/actions/workflow-jobs),
[Contents API symlink behavior](https://docs.github.com/en/rest/repos/contents),
[Git trees](https://docs.github.com/en/rest/git/trees), and
[HTTPX streaming](https://www.python-httpx.org/quickstart/#streaming-responses).

## Deterministic evidence preparation (Task 3)

`evidence_pipeline.prepare_evidence(CollectionResult)` performs no network requests
and leaves the collection unchanged. It validates source identity, sanitizes all
available sources, selects relevant excerpts, and verifies exact provenance before
returning a `PreparedEvidencePackage`. It reuses the foundation `Evidence` model;
the package is separate from `InvestigationReport` and contains no hypotheses or
confidence labels.

### Redaction and trust boundary

`redactor.SecretRedactor` uses deterministic standard-library patterns and stable
category placeholders. Rules cover classic/fine-grained/variable-length GitHub
tokens, unquoted/quoted Bearer values, Basic Authorization/Proxy-Authorization
credentials, URL user information, common API-key,
password, token, secret and credential assignments, AWS AKIA/ASIA access-key IDs,
and PEM private-key blocks. Quoted assignments and common YAML block scalars are
handled; an unterminated PEM block masks the remaining lines. Recognizable
environment and GitHub expression references are preserved. The token families
are informed by [GitHub authentication documentation](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github)
and [AWS access-key documentation](https://docs.aws.amazon.com/us_en/IAM/latest/UserGuide/security-creds-programmatic-access.html).

Redaction preserves LF separators, including within private-key blocks, and handles
CRLF without shifting physical line numbers. Stable placeholders never contain
original values. Summaries record category counts only, including sanitization of
surfaced metadata and failed-step hints. Warnings/limitations are sanitized too;
sensitive source identities are rejected rather than silently rewritten. Processing
errors do not echo source text or Pydantic validation payloads. The CLI sanitizes
collection metadata and errors, escapes JSON control characters, and fails closed
without exporting original content if preparation fails.

This is best-effort pattern matching, not comprehensive secret discovery. Unknown,
encoded, split, or obfuscated secrets may remain. Conservative rules can remove
useful diagnostics. The implementation deliberately avoids an LLM, entropy-based
scanner, full YAML/TOML/shell parsing, or a new parsing framework. Advanced quoting,
multiline syntax, and arbitrary secret formats are not fully understood. Passing
redaction is not an approval to transmit content to an external service.

Logs, file content, and step names remain untrusted data. A line such as “Ignore
previous instructions” can remain in a relevant excerpt, but is never executed,
followed, or used to retrieve another file. Future Gemini integration must maintain
this boundary in prompts and tool permissions; sanitization does not neutralize
prompt injection by itself.

### F-004: authentication-header redaction review

Independent adversarial review found that the original Bearer pattern skipped
single- and double-quoted credentials and had no HTTP Basic header rule. A quoted
Bearer credential inside an `AssertionError` survived the full preparation
pipeline and appeared in serialized evidence. The original 387 tests passed but
did not cover these forms. This could expose authentication material through
manual previews or a future external Gemini request.

The redactor now handles unquoted, single-quoted, and double-quoted Bearer values,
retaining the `BEARER_TOKEN` category. A separate header-scoped rule handles Basic
credentials under `BASIC_AUTH`. Header names and schemes match without case
sensitivity, including `Proxy-Authorization`. Basic prose outside a matching header
is left alone. Bearer matching retains its prior support outside a header.

Both rules use credential-sized matches and replace only the value, preserving
the scheme, quote delimiters, and surrounding failure message. Quoted values
recognize backslash escapes; an unterminated quote masks through the physical line
ending without crossing LF/CRLF. Already-masked values are unchanged and are not
counted again. Category counts contain no credential material. Existing GitHub,
URL, assignment, AWS, and private-key rules remain in place.

Regressions reproduce the original failure in redaction, full prepared packages,
and CLI previews. They verify LF/CRLF preservation, mixed-case/proxy headers,
multiple credentials on one line, repeatability/idempotence, surrounding errors,
and value-free summaries, warnings, and failure messages. Exact run results are
recorded in the development log. These additions cover recognized authentication
forms, not arbitrary encodings, all header serializations, other authentication
schemes, or unknown credentials. Pattern redaction remains best-effort and does
not by itself make evidence safe for external transmission.

### F-005: serialized authentication credentials

Further independent review reproduced Basic credentials surviving preparation in
`headers={"Authorization": "Basic ..."}` and `Authorization=Basic ...` log text.
F-004 supported ordinary colon-delimited headers, but its Basic prefix did not
recognize quoted field names, quoted header values before the scheme, or equals
separators. This could expose authentication material despite the original 422
tests passing.

The existing header-scoped Basic rule now recognizes Authorization and
Proxy-Authorization keys, optionally quoted, with `:` or `=` separators and
optional quotes around the header value. Key and scheme matching remain case
insensitive. Common single-quoted dictionary representations and one layer of
backslash-escaped quote delimiters are supported. Credential matching handles
these escaped delimiters for both Basic and Bearer, leaving surrounding escaping
and quotes intact. It does not decode or reserialize the source. Bare credential
matches stop before closing object/array punctuation and escaped quote delimiters;
an explicit placeholder case preserves idempotence. Categories remain `BASIC_AUTH`
and `BEARER_TOKEN` with counts only; unrelated keys and Basic prose are untouched.

Regressions compare complete expected strings, verify that JSON and escaped JSON
remain decodable, preserve LF/CRLF counts and useful failure context, and repeat
redaction. Full preparation covers serialized log/configuration text, warnings,
limitations, source ranges, and independently verified provenance. Mocked previews
cover both excerpt and metadata-only output. Failure-path tests verify that
exceptions contain no synthetic credential. Actual reproduction and verification
results appear in the development log.

This remains targeted pattern handling. Arbitrarily nested serialization, Unicode
escape sequences hiding header/scheme names, other encodings, and obfuscated or
unknown credentials are not comprehensively supported. No general JSON parser,
framework, or recursive decoding pass was added. Pattern redaction still does not
establish that evidence is safe to transmit externally or that it contains no
secrets. Malformed quoted input can conservatively mask useful text on that line.

### Relevance, bounds, and incomplete evidence

`evidence_extractor.py` recognizes Python exceptions and traceback sections, pytest
assertions/outcomes/summaries, Actions annotations, dependency-install failures,
and process exits. Failed-step names provide weak hints. Detailed errors outrank
generic exit notices; the priority values are relevance scores, not probabilities
or AI confidence. Dependency/test indicators guide selection from already-collected
workflow commands, dependency declarations, and test configuration. Absence from
an excerpt never proves a package is missing from the environment.

Nearby windows merge, including overlapping/adjacent ranges. Defaults in
`PreparationLimits` are 24 excerpts, 2,000 characters each, 30,000 combined, and up
to five context lines before/after an anchor. Traceback continuity can extend
beyond ordinary context, up to 200 lines, subject to text budgets. Whole-line
segments split when necessary; an oversized physical line is omitted rather than
clipped and assigned a misleading range. Budget selection favors detailed errors,
then output returns to source and line order. Relevant omissions set an explicit
flag/warning. Deterministic source order and IDs avoid duplicate excerpts.

Hard bounds constrain configuration of these defaults (256 excerpts, 32,000
characters per excerpt, one million combined; context remains at most five).
The extractor retains at most 512 strongest anchors per source. Processing accepts
the collector's three logs, five available configuration files, and seven
configuration availability records. An individual redaction input/output is capped
at four MiB of characters, the internal sanitized-source registry at eight MiB of
characters, and surfaced collection metadata input at 64 KiB of characters. Failed
step hints and job step counts are also bounded. Unsupported oversized or malformed
inputs, including unpaired Unicode surrogates, cause value-free preparation errors.
These are engineering limits, not performance guarantees.

`ready` means an informative failure observation survived selection, even if the
package is incomplete. Generic exits, traceback headers, failed-step hints, and
configuration alone do not qualify. Otherwise status is `insufficient_evidence`;
there is no invented cause. Missing logs/configurations, unfinished workflows,
collection/rate-limit warnings, UTF-8 replacements, truncation, and selection
omissions remain visible through warnings, limitations, or incompleteness flags.
The first-1-MiB policy is unchanged: the actual failure can be in the unavailable
ending. No recognizable error in available text proves neither success nor the
absence of a failure. Future reasoning must account for these limitations.

### Exact excerpt provenance

`evidence_provenance.py` holds an internal, bounded sanitized source registry. LF
alone defines physical lines; CR immediately before LF is removed for canonical
excerpt text. Bare CR and Unicode separators remain within their physical line.
A final LF does not invent another line. Excerpts join the original inclusive
one-based range with LF and add no final delimiter. Redaction occurs before this
mapping, and its preservation of LF counts retains original line positions.

Each evidence ID is `ev-` plus 24 hexadecimal SHA-256 characters derived from the
source type, canonical source ID, line range, and sanitized text. No raw secret is
hashed for the ID. Validation checks source membership/type, range bounds, exact
sanitized text, ID derivation, and package-local uniqueness. Configurations must
match the collection commit SHA and canonical source ID; logs match run, attempt,
and collected job. `validate_prepared_package(package, collection)` independently
rebuilds sanitized sources, checks identity/source metadata, and rechecks excerpts.
Call it again after modifying evidence or source metadata.

The package serializes only selected sanitized excerpts and source metadata, never
full raw or sanitized sources. The internal representation is not persistent
storage or an access-control boundary. Validation trusts the supplied collection;
it does not independently authenticate GitHub, establish the job's actual checkout,
or prove that a caller-supplied source is genuine. The truncated hash is an identity
aid, not a signature. The prepared-package validator covers provenance rather
than reclassifying relevance or proving every package flag.

These checks strengthen the earlier reference validator: provenance establishes
that an excerpt matches available sanitized content, while
`validate_report_evidence()` establishes that hypothesis references resolve.
Neither proves factual relevance, completeness, or causal correctness. Actual
tests and corrected implementation bugs are recorded in the development log.

## Pipeline and future work

1. Parse a public GitHub workflow run URL into a canonical identity.
2. Fetch run metadata, failed jobs, and logs. Resolve repository configuration from
   the head commit SHA attached to the run, rather than the current branch head.
   This need not be the exact checkout used by an individual job.
3. Sanitize collected sources, select bounded evidence with original source IDs
   and line numbers, and verify exact provenance. Treat source text as untrusted
   data and preserve incompleteness warnings.
4. Ask Gemini through Vertex AI for structured hypotheses, verification steps,
   next steps, and limitations. Permit an explicit insufficient-evidence outcome.
5. Apply model validation and deterministic evidence-reference validation.
6. Present the report through Streamlit, distinguishing observations from possible
   causes and exposing evidence and limitations.

Steps 1–5 now exist, including opt-in Gemini reasoning and validation. Presentation
is currently CLI JSON. No autonomous code modification, persistent storage, or UI
is included.

## Vertex AI diagnosis boundary (Task 5)

`diagnosis_engine.diagnose(PreparedEvidencePackage, has_failed_jobs=..., client=...)`
is the only diagnosis orchestration entry point. It cannot accept `CollectionResult`,
fetch repository files, or build new evidence from model output. The caller must
prepare and independently validate provenance upstream, obtain external-transmission
consent, and supply the trusted failed-job gate. The CLI does this in that order;
library callers own consent. The gate is not sent as additional context. A package
alone does not carry workflow conclusion or failed-job counts, so those facts must
not be inferred from log prose. A completed success is always gated off by the CLI,
including an anomalous collection containing failed-job records.

The existing report, evidence, collection, and preparation models are unchanged.
The engine snapshots/revalidates the mutable package before calling a client.
It does not mistake structural input validation for independent provenance:
`validate_prepared_package` still reconstructs sanitized sources from collection.
An injected `DiagnosisClient.generate(DiagnosisPrompt) -> str` replaces Gemini in
tests without bypassing local output validation.

### Transport and authentication

`gemini_client.GeminiClient` uses the official SDK, `genai.Client(vertexai=True,
project=..., location=...)`, and ADC. Defaults are project `drivernets`, location
`global`, model `gemini-2.5-flash`, configurable via `GOOGLE_CLOUD_PROJECT`,
`GOOGLE_CLOUD_LOCATION`, and `VERTEX_AI_MODEL`. `.env` is never auto-loaded. Use
`gcloud auth application-default login` locally or attached cloud ADC; no API key
or service account JSON file is supported. The MVP refuses explicit
`GOOGLE_APPLICATION_CREDENTIALS` file configuration. Explicit project/location
select ADC even when unrelated API-key environment variables are present.

The client uses API `v1` and an application-selected Google Vertex HTTPS endpoint;
SDK base-URL environment overrides cannot redirect evidence elsewhere. The HTTPX
client disables environment proxy configuration. ADC has its own credential
discovery/refresh behavior. HTTP timeouts default to 60,000 ms, configurable with
`VERTEX_AI_TIMEOUT_MS` within 1,000–120,000 ms. These are operation bounds, not a
hard command-wide/ADC wall-clock deadline. The SDK client is closed after each
generation request, including errors.

Generation uses temperature 0.1, one candidate, JSON MIME type, and
`response_schema=DiagnosisDraft`. Output defaults to 4,096 tokens, configurable
through `VERTEX_AI_MAX_OUTPUT_TOKENS` within 512–8,192. No tools, grounding, code
execution, external browsing, file uploads, or automatic function calls are enabled.
Model reasoning/thinking defaults are otherwise unchanged; their behavior and
token usage depend on the configured model. Model IDs are restricted to simple
`gemini-*` names, not arbitrary API resource paths.

SDK request attempts are explicitly set to one. There is no automatic service
retry for timeout, quota, authentication, permission, network, or availability
errors. Such errors become application-authored `DiagnosisError` messages without
HTTP bodies, exception payloads, credential values, prompts, or signed URLs. Empty,
blocked, non-text, non-STOP, and token-limited candidates are rejected as unusable.
Even unexpected SDK failures cross the boundary as a generic error. Raw SDK
exception chains are suppressed at the exposed error boundary; the CLI prints
only safe JSON, not a traceback.

### Prompt version and untrusted evidence

`diagnosis_prompt.py` owns `ci-diagnosis-v2`. Application instructions are passed
through the SDK's system-instruction field; the single user message is deterministic
sorted JSON with `prompt_version` and `untrusted_evidence_data`. All strings in
that data, including warnings, filenames, logs, configuration, and apparent role
delimiters, are explicitly untrusted. Quoting/escaping keeps them within JSON data;
no source text is interpolated into system instructions. This is a prompt boundary,
not a mathematical guarantee that the model will ignore adversarial prose.

The prompt distinguishes observations and causes, asks for evidence IDs, considers
workflow/dependency context, rejects generic exits as causal proof, distinguishes
test expectations and implementation defects where possible, requires uncertainty
and verification, and forbids invented observed files/commands/events. A citation
alone must never be described as proving a cause. Suggested checks are proposals,
not claims that a command ran. Incomplete context forbids high confidence.

The builder accepts only prepared evidence: canonical run identity, selected
attempt, commit, selected sanitized excerpts with IDs/source ranges, prepared
source metadata, warnings/limitations, and completeness flags. It does not serialize
raw collection objects, original configuration, full sanitized sources, redaction
counts, credentials, labels, or other repository content. It preserves selection
budgets by adding no context. It also caps excerpt text at 30,000 characters,
metadata (including excerpt identity/range overhead) at 12,000 characters,
excerpts at 256, source records at 10, and serialized user content at 280,000
characters including JSON escaping. Larger custom preparation budgets may produce
packages rejected by this stricter diagnosis boundary. Reprepare with smaller
limits; do not clip excerpts or renumber evidence after preparation.

`DiagnosisPrompt` hides content from its representation. Application code neither
logs nor persists full prompts or responses. This does not control callers that
explicitly print objects, enable SDK/HTTP debug logging, or configure external
instrumentation. Redaction remains best-effort, so the CLI requires both
`--ai-diagnose` and `--allow-external-ai` before even collecting an AI request.
Original metadata/preview modes remain local to collection/preparation. AI and
preview flags are mutually exclusive; metadata-only preview semantics are unchanged.

### Draft, assembly, and deterministic acceptance

`diagnosis_schemas.py` defines a strict, extra-field-forbidden `DiagnosisDraft`:

| Field | Contract |
| --- | --- |
| `observed_failure_summary` | Nonblank text |
| `status` | `diagnosed` or `insufficient_evidence` |
| `hypotheses` | At most five hypothesis drafts |
| `recommended_next_steps` | One to eight nonblank strings |
| `limitations` | Up to twelve nonblank strings |
| Hypothesis `explanation` | Nonblank text |
| Hypothesis `supporting_evidence_ids` | Up to 64 existing IDs, no per-hypothesis repeats |
| Hypothesis `confidence` | `low`, `medium`, or `high` |
| Hypothesis `verification_steps` | One to eight nonblank strings |

Draft text fields cap at 2,000 characters; reference IDs at 128. The model cannot
supply evidence records, source text/ranges, workflow identity, or commit fields.
The engine accepts no model-generated provenance certificates and does not rely
on SDK `.parsed`. It caps complete text at 32,000 characters, parses JSON itself,
rejects duplicate keys/non-JSON constants, and validates strict Pydantic shape.
This character check occurs after SDK decoding, not as a streaming byte cap;
generation token limits provide the server-side output bound.

Application code constructs `InvestigationReport` using package identity and all
prepared `Evidence`, redacts generated descriptive text best-effort, and calls
`validate_report_evidence()`. That validator rejects unknown/repeated references,
duplicate evidence records, diagnosed reports without hypotheses, and every
unsupported diagnosed hypothesis. The engine additionally requires support for
tentative hypotheses too. Shared citations across distinct hypotheses are valid.
Invalid responses are never displayed as accepted reports.

A complete text draft that fails schema, references, or confidence policy permits
at most one fresh request (two generation requests total). `response_retries=0`
disables it. The fresh request uses the exact same prompt: no rejected prose,
model-supplied repair instructions, or growing conversation is fed back. Exhaustion
returns a controlled error. There is no agent/tool loop or automatic confidence
upgrade/downgrade. Empty, oversized, blocked, and incomplete responses do not get
this retry.

Validation failures carry an application-defined `ValidationFailureReason`, never
model-generated strings or offending IDs: `SCHEMA_INVALID`,
`UNKNOWN_EVIDENCE_REFERENCE`, `DUPLICATE_EVIDENCE_REFERENCE`,
`MISSING_EVIDENCE_REFERENCE`, `HIGH_CONFIDENCE_INCOMPLETE`, `STATUS_INCONSISTENT`,
and `OTHER_VALIDATION_FAILURE`. JSON/strict shape errors are schema failures;
`diagnosed` with no hypotheses is a status inconsistency. Draft references are
classified directly before trusted report assembly; the existing report validator
still runs afterward. No validator exception text is parsed to infer a category.
The CLI reports a fixed rejection message, final-attempt category, and bounded
attempt count (one or two). Exceptions whitelist categories/counts and suppress
underlying validation payloads, without printing/persisting requests or responses.

### Abstention, limitations, and scope of guarantees

Insufficient packages, empty evidence, or a false failed-job gate return a local
`insufficient_evidence` report without constructing Gemini or reading ADC. The
existing report contract has no separate success/non-diagnosis status; the summary
states when there is nothing to investigate. The model can also explicitly abstain.

Following F-008, `PreparedEvidencePackage.incomplete` means a substantive context
gap, rather than any warning. Confirmed `missing` files in the optional configuration
allowlist are incidental when no available sanitized source literally mentions
their path. Their availability records and both collection/preparation warnings
remain visible. Both the file record and corresponding `config_missing` warning
must be classified; exempting only one would preserve the false positive.

Missing failed-job logs, logs lost by the job limit, truncated sources, omitted
relevant evidence, unfinished attempts, decoding loss, unreadable/denied/invalid
configuration, missing workflow YAML, referenced missing optional files, and unknown
collection warnings remain substantive gaps. Warning exemptions require a matching
missing optional-file record and source identity; warning prose never overrides
completeness. Reprepare older packages to apply the refined classification; the
engine still honors an explicitly supplied `incomplete=True`.

Literal path mentions are a conservative relevance heuristic, not semantic
analysis. Comments can cause false positives, and indirect/dynamic references may
be missed. A GitHub `missing` result is not independent proof of filesystem absence
(404 can conceal access restrictions). No flag certifies causal sufficiency or
automatically justifies high confidence.

Useful incomplete packages can reach Gemini. Reports always retain preparation
limitations and warning messages, plus a standard reasoning limitation. Any
incomplete/truncated/omitted flag or truncated source adds a provisional-context
limitation and deterministically rejects high-confidence hypotheses. Medium/low
model confidence is retained, not upgraded. Model limitations supplement pipeline
limitations and cannot erase them. The existing report schema stores the canonical
run identity; selected attempt/commit are in the prompt/package and prepared source
identifiers, not model-created report fields.

Reference integrity proves that IDs resolve, and upstream provenance proves
excerpt correspondence to supplied sanitized sources. Neither proves truth,
causality, relevance, the job's actual checkout, or immunity to prompt injection.
An attacker can still influence an explanation citing a valid but irrelevant line.
Human verification is essential. Incomplete data may still yield an incorrect
medium-confidence hypothesis. Missing failure text is never proof of success.

Character limits are not exact token accounting. System instructions, metadata,
JSON escaping and the schema add input tokens; generated and model-thinking tokens
may incur charges. One optional output-validation retry can double requests.
No paid token-count call, cost estimator, cache, or total-cost guarantee is added.
For transport/schema reference, see the official
[Google Gen AI SDK](https://googleapis.github.io/python-genai/) and
[retry option documentation](https://googleapis.github.io/python-genai/genai.html#genai.types.HttpRetryOptions).
Current rates belong to [Vertex AI pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing).

Offline verification includes fake clients and actual SDK serialization through
`httpx.MockTransport` under the socket/DNS guard. It does not verify current live
Vertex permissions, schema acceptance, service latency, charges, or real-model
reasoning/injection resistance. `scripts/gemini_smoke_test.py --allow-external-ai`
provides one explicitly authorized synthetic request with response retries disabled;
the [README](../README.md#one-authorized-synthetic-live-smoke-test) records its exact
scope. No live diagnosis call was made for this task.

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
- The foundation Evidence schema permits arbitrary excerpt text; the preparation
  provenance validator separately enforces exact sanitized source ranges.
- Collection fields are ordinary lists; consumers must revalidate after mutation.
- Dependency version ranges permit compatible updates; no lockfile is supplied.

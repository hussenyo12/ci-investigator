# Development log

## 2026-10-14 — Task 1: project foundation

### Requirements

Implement the Python 3.11+ project foundation with Pydantic v2 models, strict
public GitHub Actions URL parsing, deterministic evidence-reference validation,
offline pytest coverage, Ruff checks, push/PR CI, installation documentation,
architecture boundaries, safe environment placeholders, and credential ignores.
Do not implement GitHub API, Gemini/Vertex AI, or Streamlit integration. Leave
changes uncommitted for review.

### Observed starting state

- Repository inspection found no project files and no existing commits.
- No applicable `AGENTS.md` files were found in the workspace or ancestor paths.
- `python` was absent from the shell; `/usr/bin/python3` reported Python 3.9.6.
- An initial sandboxed pip download of a temporary Python setup tool failed with
  DNS/network access errors. A network escalation was requested to enable checks.

### Implementation decisions

- Use a setuptools `src/` package and a `dev` extra containing pytest and Ruff.
- Use strict types and forbidden extra fields with Pydantic field/model validators;
  preserve evidence text while rejecting whitespace-only mandatory strings.
- Keep canonical URL identity consistent with owner, repository, and run ID.
- Reject URL credentials, every explicit port, fragments, whitespace/controls,
  encoded paths, malformed identities, and non-public or spoofed hostnames.
- Keep evidence-reference validation separate from schema validation and reasoning.
  A diagnosis needs at least one supported hypothesis; insufficient evidence may
  produce no hypotheses. Confidence labels convey qualitative judgments only.
- Add adversarial URL cases, model validation, report reference checks, JSON
  round-tripping, and a socket guard to the offline tests.
- Supply safe environment placeholders; do not load configuration or credentials.

### Verification

- Downloaded a temporary Python interpreter and created the ignored `.venv`;
  `.venv/bin/python --version` reported Python 3.11.17.
- The first sandboxed editable install failed because the build dependency download
  could not resolve PyPI. Retried with approved network access.
- `.venv/bin/python -m pip install -e ".[dev]"` succeeded, building and installing
  the editable package. Installed Pydantic 2.14.1, pytest 8.4.2, and Ruff 0.17.0.
- `.venv/bin/python -m pytest -q`: **149 passed in 0.08s**, with socket creation,
  socket connection helpers, and DNS resolution blocked by an autouse fixture.
- The initial `.venv/bin/ruff check .` found three E501 line-length violations in
  the evidence-validator tests. Wrapped those lines; the rerun exited successfully
  with **All checks passed!**
- After the formatting corrections, reran `.venv/bin/python -m pytest -q`:
  **149 passed in 0.07s**.
- Executed the Python example extracted from `README.md`; it constructed and
  validated an `insufficient_evidence` report successfully.
- `git diff --check` exited successfully. New project files remain untracked;
  no commit or push was performed.
- `git check-ignore` confirmed virtual environments, local environment files,
  credential paths, caches, and package metadata are ignored. `.env.example` was
  correctly not ignored.

### Findings and corrections

- During implementation review, expanded the parser's initial ASCII whitespace
  guard to reject Unicode whitespace as well, and added a nonbreaking-space URL
  case. This prevents a query containing Unicode whitespace from bypassing the
  documented input policy.
- No functional pytest failures occurred in the initial suite run. The three
  initial Ruff findings were formatting defects in tests, not validator failures.

### Unresolved limitations

No service integrations, evidence retrieval/sanitization, source authenticity
checks, LLM reasoning, or UI. Reference validity does not establish factual
correctness. Query parameters do not select a rerun attempt. Dependency ranges are
not locked. CI execution on GitHub requires a subsequent push or pull request.

## 2026-10-14 — Task 1.1: F-001 independent review correction

### Finding and original behavior

The user supplied independent review finding F-001: a `diagnosed` report containing
one supported explanation and an unsupported high-confidence explanation passed
reference validation. Inspection confirmed the validator required evidence support
for only one hypothesis, and an existing test explicitly accepted that behavior.
The original implementation met Task 1's initial acceptance criteria. This change
intentionally tightens the engineering policy based on new review evidence.

The risk is that an unsupported explanation could appear inside an accepted
diagnosis, encouraging readers to treat it as evidence-backed even when its
supporting reference list is empty.

### Correction and implementation decisions

- Require at least one hypothesis in a `diagnosed` report.
- Require at least one existing evidence ID for every diagnosed hypothesis,
  regardless of confidence. An error identifies the unsupported hypothesis's
  one-based position in the report.
- Preserve duplicate evidence record, repeated reference, and unknown reference
  checks for both statuses. Evidence can still be shared across hypotheses.
- Keep empty hypothesis lists and unbacked tentative hypotheses valid for
  `insufficient_evidence` reports. Any included references must still resolve.
- Replace the old acceptance test with mixed supported/unsupported regressions
  covering both hypothesis orders and all three confidence labels. Add an explicit
  passing case with two hypotheses, each citing its own existing evidence record.
  Separate tests reject empty diagnosed reports and unsupported single hypotheses.
- Update the architecture and README to describe the new policy. Keep the initial
  Task 1 log intact as a record of the earlier requirements and implementation.

### Actual verification

- Before changing the validator, ran the targeted mixed-hypothesis regressions,
  two-supported-hypothesis case, and empty insufficient-evidence cases. Result:
  **6 failed, 3 passed, 17 deselected in 0.03s**. All six mixed-report cases failed
  because the original validator did not raise the expected exception, reproducing
  F-001 across both hypothesis orders and every confidence label.
- After the policy correction, ran `python -m pytest -q` using the existing `.venv`
  through `PATH`: **155 passed in 0.07s**. The full suite includes the existing
  unknown-reference, duplicate-ID, duplicate-reference, and insufficient-evidence
  checks, with the offline socket/DNS guard still enabled.
- The first `ruff check .` found one E501 line-length violation in a new test's
  exception assertion. Wrapped the assertion and reran the same command:
  **All checks passed!**
- No commit or push was performed.

### Remaining limitations

Reference integrity establishes that citations exist and are not duplicated; it
does not authenticate evidence or prove that a cited excerpt supports an
explanation. No automated factual or causal validation was added. Confidence
labels remain qualitative. GitHub ingestion and Gemini integration remain
unimplemented. Consumers must still invoke the reference validator explicitly
and revalidate after changing report collections.

## 2026-10-14 — Task 2: GitHub Actions data collection

### Starting state and scope

Read the foundation models, parser, evidence validator, architecture, development
log, project metadata, README, and offline fixtures before implementing collection.
The workspace was on `main` at `f256d87` (`feat: establish CI Investigator foundation
with evidence validation`), tracking `origin/main`. The existing untracked
`ci-investigator-review.zip` was preserved. No applicable agent instructions were
found in the repository or ancestor directories.

Implement read-only GitHub collection with HTTPX, typed models/errors, strict
attempt selection, paginated jobs, bounded logs/configuration, offline mocks, and
a metadata-only manual CLI. Preserve the foundation and F-001 policy. Do not add
Gemini, prompts, diagnosis, evidence extraction/redaction, or Streamlit.

### Implementation decisions

- Add `collection_schemas.py`, `github_client.py`, `github_errors.py`, `collector.py`,
  and `cli.py`. Keep collection facts separate from `InvestigationReport`.
- Add HTTPX as a runtime dependency and the `ci-investigator` console script.
  The injectable transport makes production collection logic testable with
  `httpx.MockTransport`; the existing socket/DNS guard remains enabled.
- Validate consumed API fields strictly and ignore unrelated additional fields.
  Require run identity, positive run/attempt IDs, a full head SHA, status/conclusion,
  and repository identity. Never fall back to a branch when SHA data is invalid.
- Keep the existing URL parser unchanged. Collection recognizes a single positive
  `attempt` query parameter or a positive integer argument, rejecting duplicates,
  conflicting sources, and numbers beyond the latest attempt. Fetch older attempt
  metadata separately and always use the attempt-specific jobs endpoint.
- Paginate by trusted numeric page parameters, 100 jobs per page and at most 20
  pages/2,000 jobs. Reject inconsistent counts, duplicate job/step IDs, and available
  provenance fields that disagree. Choose the three lowest failed job IDs.
- Use an API client with optional environment token and a separate unauthenticated
  download client. Allow one HTTPS log redirect, reject subsequent redirects and
  compressed bodies, and never include response bodies, tokens, or signed URLs in
  public exception messages. No automatic retries or environment proxies.
- Apply 5-second connection/20-second operation timeouts and a 60-second streaming
  deadline checked between received chunks. Bound API bodies at 2 MiB, Contents
  bodies at 256 KiB, and forbidden-response classification bodies at 8 KiB.
- Stream log prefixes of at most 1 MiB, close oversized streams early, and record
  truncation. Preserve original text/newlines and LF-based line positions. Decode
  invalid UTF-8 using U+FFFD and record replacement warnings.
- Prefer workflow YAML, then `pyproject.toml`, `requirements.txt`,
  `requirements-dev.txt`, `pytest.ini`, `tox.ini`, and `setup.cfg`. Keep at most five
  available files, each at most 128 KiB, with availability/reason records for every
  candidate. Oversized configuration is omitted rather than partially retained.
- Use Contents API `ref=head_sha` and validate returned paths/blob identities,
  base64, declared sizes, and UTF-8. Verify non-recursive Git tree modes first,
  rejecting symlinks at every path level and non-regular files. This extra lookup
  is needed because Contents can return a symlink target as a regular file.
- Required network/server/rate-limit/invalid-response failures propagate typed
  exceptions. Missing/denied required access yields `inaccessible`. Optional errors
  preserve other facts and produce warnings; rate limiting stops optional requests.
- Exclude raw contents from repr/default serialization. Keep them in memory only;
  the CLI explicitly prints metadata without raw logs/configuration or persistence.
- Update README, architecture, and environment examples with operational behavior,
  endpoint references, security assumptions, and limitations.

### Actual verification and findings

- The first sandboxed editable install could not fetch build dependencies from
  PyPI. Retried with approved network access. `.venv/bin/python -m pip install -e
  ".[dev]"` succeeded, including HTTPX 0.28.1 and the console script. Python remained
  3.11.17, Pydantic 2.14.1, pytest 8.4.2, and Ruff 0.17.0.
- Initial client/foundation testing: **2 failed, 217 passed in 0.18s**. One mock
  helper had conflicting positional and overridden `path` arguments; renamed its
  positional parameter. The other case showed that HTTPX rejects a malformed
  redirect before yielding the response even with automatic following disabled.
  Added sanitized `InvalidAPIResponse` translation for protocol/URL errors.
- After adding collector coverage and correcting those issues: **265 passed in
  0.21s**. Ruff found four import-order violations and one unused import, all fixed.
- Added secondary rate-limit, repeat-redirect, exact/oversized configuration,
  missing Contents resource, invalid metadata, completed-job conclusion, changing
  pagination, and streaming-deadline coverage. Expanded testing passed **273 tests
  in 0.22s**, and Ruff passed.
- Implementation review identified two further provenance issues: `splitlines()`
  would count embedded Unicode separators/bare carriage returns as physical log
  lines, and trimming API source paths could hide a path mismatch. Switched log
  counting to LF boundaries and preserved exact API path values. Added regression
  coverage for both. Strict SHA validation also applies to direct client calls.
- Final `python -m pytest -q` with `.venv/bin` on PATH: **275 passed in 0.22s**.
  This includes all 155 foundation tests plus 120 collection/client cases; no real
  network requests occurred during these tests.
- Final `ruff check .`: **All checks passed!**
- `git diff --check` passed and `ci-investigator --help` displayed the URL and
  optional attempt interface. No commit or push was performed.

### Separate live smoke test

Sandboxed GitHub requests were blocked by DNS/network restrictions, then retried
with approved network access. Listed the repository's recent public runs and used
the actual console script with `GITHUB_TOKEN` explicitly empty against:

`https://github.com/hussenyo12/ci-investigator/actions/runs/38597354478`

The CLI exited **0** and reported workflow `CI`, status `completed`, conclusion
`success`, selected attempt **1**, and head SHA
`f256d87c3a1dfd6fd33f293d5d2769bf909a93ce`. It retrieved
`.github/workflows/ci.yml` and `pyproject.toml` at that SHA and recorded missing
`requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `tox.ini`, and `setup.cfg`.
No failed jobs or logs existed for this successful run. The test printed metadata
only and did not save or print raw contents.

Live failed-log redirects, reruns, private access, and rate limiting were not
exercised by this run; coverage for those branches is mocked and reported as such.

### Remaining limitations and next iteration

Partial reruns may require jobs from earlier attempts, which are not merged. The
observed latest attempt can change after collection begins. Run `head_sha` can
differ from an individual job's actual checkout, including PR merge commits.
Changing pagination counts requires a new collection. Public GitHub only, common
ASCII workflow filenames, a fixed Python configuration allowlist, three failed
jobs, and five files can omit context. Large or compressed resources can produce
partial results. The streaming deadline is checked between chunks; there is no
single overall deadline covering the full sequence. Download destinations trust
HTTPS redirect URLs from GitHub without a host allowlist. HTTP 404 can hide access
denial. Default serialization guards cannot stop a caller from explicitly copying
raw attributes. Source facts are not a diagnosis and do not establish causality.

Next: build secret redaction and failure-evidence extraction while preserving
original source IDs and line ranges. Raw contents must remain internal until that
boundary is implemented; no LLM or UI integration is included in this task.

## 2026-10-14 — Task 2.1: independent security and reliability review

### Inspection and review findings

Inspected the existing client, collector, offline fixtures/tests, architecture,
development log, project metadata, and repository status before making changes.
Task 2 changes were still uncommitted; the existing review ZIP was preserved.

**F-002:** Independent review showed that HTTPS syntax validation accepted internal
destinations, including `127.0.0.1`, `localhost`, and `169.254.169.254`. The separate
unauthenticated download client protected GitHub credentials, but did not stop an
unintended request to an internal service. Destination validation needed to occur
before executing the download request.

**F-003:** Review placed a failure after the first 1 MiB of a log, beyond the retained
prefix. The error was therefore absent from collected text. This behavior complied
with Task 2's original bounds; the warning needed to explain its consequences for
future diagnosis rather than just state the byte limit.

### Corrections and decisions

- Add deterministic IP/hostname checks without DNS queries. IP literals must be
  global, non-multicast, non-reserved addresses; deprecated IPv6 site-local addresses
  are rejected. IPv4-mapped IPv6 additionally checks the embedded IPv4 address.
- Reject obvious local/internal hostname suffixes, localhost-prefixed names,
  single-label names, malformed DNS labels, scoped IPv6, percent-encoded hosts,
  and ambiguous numeric/hex address forms. Normalize case and terminal root dots
  for classification. Public IP literals and ordinary externally hosted signed
  URLs remain supported; no fixed GitHub-only hostname allowlist is introduced.
- Keep the separate unauthenticated download client, single HTTPS redirect,
  operation timeouts, bounded streaming, and socket/DNS test guard intact.
- Preserve prefix-only 1 MiB retention and strengthen `log_truncated` to state that
  the end of the log, including the actual failure, may be missing. No tail retrieval
  or unbounded download was added.
- Add regressions requiring unsafe redirects to raise before the second HTTP
  request, positive tests for cloud/external signed URLs and public IPv4/IPv6
  without authentication/cookies, and a collector regression placing a failure
  immediately after the byte limit.
- Update architecture and README with both findings, destination policy, DNS risk,
  truncation limitations, and the need for future insufficient-evidence outcomes.

### Actual verification

- Before applying corrections, ran the targeted redirect and truncation regressions:
  **42 failed, 13 passed, 112 deselected in 0.41s**. The 41 new unsafe-destination
  cases proceeded into the mocked download, producing `LogsUnavailable` after a
  second mock redirect instead of rejecting with `InvalidAPIResponse`. The
  truncation regression confirmed the final failure was absent, then failed because
  the old warning omitted the missing log ending/failure. These were expected
  reproductions of the review findings, not new implementation failures.
- Local parsing/classification inspection showed that `is_global` alone accepts
  multicast literals, so the policy explicitly excludes multicast. It also checks
  deprecated IPv6 site-local addresses and IPv4 embedded in mapped IPv6. HTTPX
  accepts several shortened/integer/hex hostname forms syntactically; those forms
  are explicitly rejected rather than delegated to a system resolver.
- After implementing the checks and warning, the full suite passed **322 tests in
  0.25s** and Ruff passed. No additional functional or lint failures occurred.
- After the final warning wording and documentation updates, ran the required
  commands using the existing `.venv/bin` on PATH:
  - `python -m pytest -q`: **322 passed in 0.23s**.
  - `ruff check .`: **All checks passed!**
  - `git diff --check`: passed.
- Offline tests continued to block socket creation, connection helpers, and DNS
  resolution. Unsafe redirects made only the initial mocked API request. No
  Gemini, extraction, or Streamlit code was added, and no commit/push was performed.

### Separate live job-log attempt

Attempted a bounded, metadata-only job-log check for public run:

`https://github.com/hussenyo12/ci-investigator/actions/runs/38597354478`

The initial sandboxed call returned `connection_failure`. Retried with approved
network access, using an anonymous client. Run metadata and jobs were retrieved:
attempt **1**, job **115967394307**, name **checks**. GitHub denied the job-log request
with `access_denied` before any external download request occurred. A subsequent
presence-only environment check confirmed no `GITHUB_TOKEN` was configured; no
credential values were inspected or printed.

Consequently, actual live redirect/download compatibility was **not verified** in
this task. Public signed redirects and credential isolation passed mocked coverage.
No raw logs, configuration contents, or signed URLs were printed or persisted.

### Remaining risks and future diagnosis

Basic destination checks reduce SSRF risk but do not completely prevent it. Allowed
hostnames can resolve to private/link-local IPs, be changed by local hostname
configuration, or undergo DNS rebinding. This implementation neither resolves and
pins a verified public address nor validates the actual connected peer address.
Network egress controls or connection-time validation would be needed for stronger
protection. IP classification depends on the installed standard-library tables;
maintained Python patch releases should be used. ASCII DNS syntax and rejection
of local forms can conservatively omit unusual destinations.

Prefix-only truncation can omit the decisive failure. Future diagnosis must treat
the warning as an evidence limitation, assess available context across sources,
and allow `insufficient_evidence` rather than confidently invent a cause. Absence
of an error in a prefix cannot rule out that error in the missing ending. Raw data
remains unredacted and internal, and valid evidence references still do not prove
factual or causal correctness. Earlier partial-rerun jobs and differences between
`head_sha` and actual checkouts remain existing limitations.

## 2026-10-15 — Task 3: secure log processing and evidence extraction

### Scope and inspection

Inspected the collection/foundation models, collector, HTTP client, reference
validator, CLI, offline fixtures, architecture, and repository state before coding.
The starting implementation was commit `ed81e5e`; its 322 tests passed in 0.26s.
Proposed an offline pipeline of redaction, deterministic selection, and exact
sanitized-source provenance checks, reusing `Evidence`. No GitHub ingestion
behavior, Gemini integration, Streamlit, database, or AI diagnosis was added.

Requirements included line-preserving secret redaction before selection, relevant
Python/pytest/install/configuration excerpts, stable IDs, explicit insufficiency
and collection limitations, configurable evidence budgets, a safe manual preview,
and offline adversarial coverage. Defaults remain 24 excerpts, 2,000 characters
per excerpt, 30,000 combined characters, and five context lines per side.

### Implementation decisions

- Added `redactor.py`: stable category placeholders for common GitHub/Bearer
  tokens, URL credentials, secret assignments, AWS access-key IDs, and PEM private
  keys. Preserve LF counts and CRLF; handle common quoted/YAML block values and
  preserve recognizable environment/expression references. Input/output bounds
  and value-free errors fail closed for unsupported input. Pattern matching is
  explicitly best-effort, not external-transmission approval.
- Added `evidence_schemas.py` for preparation limits, source metadata, category
  counts, warnings, and a `PreparedEvidencePackage` separate from AI reports.
  Existing `Evidence` remains the excerpt schema.
- Added `evidence_extractor.py`: prioritize detailed Python/pytest/install errors
  over generic exits, preserve practical traceback continuity, merge context,
  split whole-line ranges to fit budgets, and select configuration using observed
  failure/step hints. Cap anchors and source counts; report relevant omissions.
  Relevance priority is not confidence, and configuration absence is not a cause.
- Added `evidence_provenance.py`: LF-only physical lines, canonical LF excerpts
  from CRLF, inclusive source ranges, and stable IDs derived from sanitized text
  plus identity/range. Validate exact text, bounds, source/type, ID, and uniqueness.
- Added `evidence_pipeline.py`: canonical collection provenance checks, sanitize
  all available source text and surfaced metadata before extraction, propagate
  missing/truncated/rate-limited/incomplete data, then validate excerpts. An
  independent package validator reconstructs sanitized sources from the supplied
  collection. No full sources are serialized in the prepared package.
- Extended the CLI with `--preview-evidence` and `--metadata-only`. It prints
  sanitized excerpts or metadata, escapes JSON control characters, and never
  falls back to raw text after failure. Existing metadata output is now sanitized
  too. No source contents are written to disk by the implementation.
- Added synthetic offline redaction, extraction/configuration, provenance, budget,
  malformed-input, prompt-injection, and CLI regressions. Updated README and
  architecture with API/CLI examples, security boundaries, and accepted limits.

### Actual tests, findings, and corrections

- An early full run with the new tests passed **378 tests in 0.85s**. Additional
  adversarial tests then exposed two bugs: a bare YAML password containing spaces
  left the suffix visible, and a blank context line could allow an excerpt to
  exceed its character budget by one because the line separator was not counted.
- The targeted regression run recorded **2 failed, 2 passed, 43 deselected in
  0.11s**. Corrected plain YAML scalar masking through the comment/delimiter and
  corrected whole-line splitting to count separators even after an empty line;
  also enforce the per-excerpt cap while fitting the remaining total budget.
  Tests verify both secrecy/useful-context preservation and exact size limits.
- After those corrections, **382 tests passed in 0.83s**. Further regressions
  cover informative-error priority, failed-step hints, generic exits, source
  metadata modification, and truncated configuration. The final expanded suite
  passed **387 tests in 0.84s** before documentation finalization.
- During code review, avoided repeatedly splitting source text and removed
  potentially costly greedy identifier/installer patterns. Long repeated-input
  tests pass; no runtime failure is claimed for these preventive changes.
- Ruff identified line length, import ordering, and an unused test import during
  implementation. These were corrected; subsequent Ruff checks passed.
- `python -m pip install -e ".[dev]"` succeeded using `.venv` after an approved
  network retry. The first sandboxed attempt could not fetch setuptools build
  dependencies. No runtime or development dependency was added by this task.
- Final run after documentation and CLI help updates:
  `python -m pytest -q`: **387 passed in 0.83s**.
- `ruff check .`: **All checks passed!**
- `git diff --check`: passed.
- `ci-investigator --help`: passed; both preview flags are displayed.

Tests use synthetic credentials, mocked collection, and the existing socket/DNS
guard. No live GitHub logs, Google Cloud calls, or external-service processing was
performed for this task. Installation was the only network verification. Changes
remain uncommitted for independent review.

### Remaining limitations and next steps

Unknown, encoded, obfuscated, or unusual multiline secrets may escape the patterns;
false positives can remove useful evidence. No full configuration/shell parser,
entropy scanner, or external secret-detection service was introduced. Redaction
does not neutralize prompt injection or prove text safe to transmit. Logs remain
untrusted data even when their excerpts are accurately mapped and redacted.

Bounded prefixes can omit the actual error. Anchor limits, whole-line omission,
configuration allowlists, and context budgets can omit relevant evidence. `ready`
means useful observations survived, not a diagnosed cause; missing errors never
prove no failure. Original collection limitations, including DNS/SSRF residual
risk, partial reruns, and possible differences from the job checkout, remain.

Provenance validates against the supplied collection, not an independent GitHub
authentication or causal oracle. IDs are deterministic truncated hashes, not
signatures. Callers can access internal data and mutate models; revalidate after
changes. The package validator covers identity/source/excerpt correspondence,
not factual relevance or all derived preparation flags.

Next, independently review this boundary before adding Gemini. Future integration
must explicitly decide what may leave the process, constrain untrusted input,
carry all insufficiency warnings, request structured tentative reasoning, and
apply the existing report-reference validator. No such integration is included.

## 2026-10-15 — Task 3.1: authentication-header redaction review (F-004)

### Inspection and independent finding

Inspected `redactor.py`, preparation/preview tests, category models, documentation,
and working-tree status before changing code. Task 3 changes were already present
and uncommitted; the existing ZIP deletion was left untouched. The baseline
`python -m pytest -q` passed **387 tests in 0.84s**.

Independent review identified that single- and double-quoted Bearer credentials
and HTTP Basic Authorization credentials were not masked. The Bearer pattern
explicitly excluded quote characters, and there was no Basic-header rule. A
synthetic quoted credential inside an `AssertionError` survived extraction and
appeared in serialized `PreparedEvidencePackage` output. Such output could expose
authentication material to previews or a future Gemini request.

### Reproduction and correction

- Added regressions before changing the redactor. The targeted command
  `python -m pytest -q tests/test_redactor.py tests/test_evidence_pipeline.py -k
  "authentication or basic_prose" --tb=no` recorded **30 failed, 5 passed,
  52 deselected in 0.14s**. Failures reproduced missing redaction in direct calls,
  prepared packages, and CLI previews; ordinary unquoted Bearer and existing
  value-free error behavior remained passing controls.
- Extended Bearer credential matching to single- and double-quoted values while
  preserving ordinary Bearer support and its `BEARER_TOKEN` category. Added
  `BASIC_AUTH` to the shared category type and a separate case-insensitive rule
  scoped to Authorization/Proxy-Authorization headers. Basic prose is preserved.
- Shared credential matching handles backslash escapes within quotes, stops at
  LF/CRLF, and masks an unterminated quoted value through the line ending.
  Replacement affects the credential span rather than the whole failure message;
  surrounding quotes and context remain. Existing placeholders are unchanged
  and receive no additional counts on repeated redaction.
- Added 35 regression cases covering ordinary/quoted Bearer, Basic, mixed-case
  and proxy headers, LF/CRLF preservation, multiple sensitive values on one line,
  escaped/unclosed quotes, useful error context, determinism/idempotence, and
  exceptions without credentials. Full preparation tests verify log/config text,
  warnings, limitations, category-only counts, serialized output, and provenance.
  Mocked CLI tests verify both excerpt and metadata-only previews. All credentials
  used in these tests are synthetic.
- Updated architecture and README with the authentication rules, finding, and
  residual limits. No Gemini, Streamlit, ingestion, or provenance-policy changes
  were made, and no dependency was added.

### Actual verification

- After the correction, the same targeted selection with `--tb=short` passed
  **35 tests, 52 deselected in 0.10s**.
- `python -m pytest -q`: **422 passed in 0.91s**, including all 387 existing tests.
- `ruff check .`: **All checks passed!**
- `git diff --check`: passed.

Tests retained the existing socket/DNS guard and used mocked HTTP for previews.
No external service was called. No further implementation or lint failures were
observed after correcting the reproduced finding. Changes remain uncommitted.

### Residual risks

These rules cover recognizable credential forms. Arbitrary encoding, alternate
header serialization, other authentication schemes, unknown secret values, and
obfuscated input may escape redaction. Conservative handling of a malformed quoted
credential can remove useful text on the same line. Redaction remains best-effort;
passing it does not authorize external transmission or neutralize prompt injection.
Existing evidence incompleteness and causal-validation limitations remain. Future
Gemini integration must independently maintain those boundaries; it is not started
in this task.

## 2026-10-15 — Task 3.2: serialized Authorization credentials (F-005)

### Review finding and reproduction

Inspected the redactor, authentication regression tests, package/preview paths,
documentation, and working tree before making changes. Earlier task changes were
already uncommitted; the existing modified review ZIP was not changed by this task.
The baseline suite passed **422 tests in 0.93s**.

Independent review identified that Basic credentials survived in JSON-style
Authorization fields and `Authorization=Basic ...` assignments. The F-004 rule
handled ordinary colon-delimited headers but did not recognize quotes around the
key/value or an equals separator. End-to-end preparation retained the credential
inside an extracted `AssertionError`, creating an exposure risk for previews and
future external use.

Added regressions before changing the rule. The command
`python -m pytest -q tests/test_redactor.py tests/test_evidence_pipeline.py -k
serialized_authorization --tb=no` recorded **33 failed, 4 passed, 87 deselected
in 0.14s**. This reproduced exposure in direct redaction, serialized prepared
packages, and mocked previews, including common escaped representations. Existing
JSON Bearer behavior, unrelated prose, and value-free failure handling provided
passing controls.

### Targeted correction and implementation finding

- Extended the existing Basic header prefix to support optional matching key
  quotes, `:`/`=` separators, and optional quotes before the scheme, preserving
  case-insensitive Authorization/Proxy-Authorization matching.
- Extended shared credential matching to recognize one layer of backslash-escaped
  quotes and preserve those delimiters for both Basic and Bearer. Only credential
  spans are replaced; source text is not decoded or reserialized. No category,
  dependency, framework, ingestion, or provenance-policy change was needed.
- The first correction passed 35 targeted cases but failed **2 cases in 0.11s**:
  an unquoted credential adjacent to `}` consumed the closing brace. The credential
  was masked, but surrounding structure was not preserved. Corrected bare-value
  boundaries to preserve closing object/array punctuation and escaped delimiters;
  explicitly recognize placeholders to retain idempotence.
- Added 37 regression cases for JSON Authorization/Proxy-Authorization, assignments,
  mixed case, literal/escaped quotes, multiple credentials, exact surrounding
  context, LF/CRLF, determinism/idempotence, unrelated keys, and value-free errors.
  JSON round-trip assertions check both ordinary and escaped JSON structure.
  Preparation tests cover logs, collected configuration, warnings, limitations,
  serialization, and provenance. CLI tests cover excerpt and metadata-only modes.
  All credentials are synthetic.
- Updated README and architecture to describe supported serialized forms and
  retain explicit best-effort limitations. No Gemini or Streamlit work was added.

### Actual verification and remaining risks

- After the boundary correction, the targeted command with `--tb=short` passed
  **37 tests, 87 deselected in 0.09s**.
- `python -m pytest -q`: **459 passed in 0.95s**, including all 422 existing tests.
- `ruff check .`: **All checks passed!**
- `git diff --check`: passed.

Tests remained offline under the existing socket/DNS guard, using mocked HTTP
transports for CLI collection. No external service was called. Changes are left
uncommitted for independent review.

Supported escaping is deliberately limited to common single-layer quote forms.
Arbitrarily nested serialization, encoded header/scheme names, unknown credential
formats, and obfuscation can still defeat pattern matching. Malformed quoted
input may cause conservative same-line context removal. There is no guarantee of
detecting arbitrary secrets or of safety for external transmission. Existing
incomplete-evidence and prompt-injection boundaries remain; no diagnosis is added.

## 2026-10-15 — Task 5: Gemini diagnosis via Vertex AI

### Inspection, scope, and trust decisions

Read the current schemas, collection/client, evidence preparation/extraction,
redactor, source/reference validators, CLI, tests/fixtures, project metadata,
README, architecture, and development log before implementation. The existing
modified review ZIP was preserved. Described the plan and trust boundaries before
editing: pipeline-sanitized prepared evidence is the only model context; Gemini
returns a non-authoritative draft; trusted application code supplies evidence and
run identity and deterministically checks references. Existing models and tests
were preserved. No Streamlit, autonomous code modification, commit, or push was
added. The successful Vertex connection mentioned in the task is user-reported;
it was not repeated here and does not verify this new diagnosis path.

### Implementation

- Added `gemini_client.py` using official `google-genai`, Vertex AI and ADC, with
  configurable project/location/model, stable API/Google endpoints, bounded HTTP
  timeouts and output tokens, one SDK request attempt, no model tools, explicit
  resource cleanup, and application-authored non-sensitive errors. Credential-file
  configuration is refused; no API key or service account JSON flow was added.
- Added `diagnosis_schemas.py` for a strict bounded `DiagnosisDraft`, without any
  authoritative evidence, source, workflow, or commit fields.
- Added `diagnosis_prompt.py`, version `ci-diagnosis-v1`: system instructions and
  a separate deterministic JSON envelope of untrusted evidence. Send selected
  sanitized excerpts and necessary identity/source metadata, warnings/limitations,
  and incompleteness flags only. Additional context/metadata limits fail closed
  without rewriting excerpts or silently dropping warnings.
- Added `diagnosis_engine.py`: local abstention before Gemini initialization for
  no failed jobs or insufficient evidence; strict JSON/schema/reference checks;
  report assembly from trusted package records; existing reference validation;
  additive pipeline/model limitations; and no high-confidence hypotheses with
  incomplete context. Generated descriptive text gets best-effort redaction.
  At most one fresh request can follow a complete invalid draft. Service and
  unusable-response errors are not retried; no rejected text is fed back.
- Extended the CLI with both `--ai-diagnose` and `--allow-external-ai`. Invalid or
  missing consent fails before collection. AI mode independently revalidates
  provenance, uses the trusted collection failure gate, prints accepted report
  JSON or safe errors, and saves no source data. Existing collection/preview modes
  remain available. SDK initialization does not occur for local abstention.
- Added `scripts/gemini_smoke_test.py`: explicit consent flag, only synthetic
  collection in memory, preparation/provenance checks, and response retries set
  to zero. No GitHub request or repository-source read occurs. The README gives
  exact live instructions and describes what a successful run leaves unverified.
- Added `google-genai>=1.55,<3`; updated README, architecture and `.env.example`
  with ADC configuration, schema/prompt decisions, consent, request/token costs,
  failure handling, deterministic guarantees and residual limitations.

### Actual verification and corrections

The existing `.venv` has broken Python 3.11 links; its working Python 3.12 entry
also lacked pytest. Used an isolated Python 3.12.10 environment under `/private/tmp`
instead of changing it. The initial sandboxed dependency install failed on DNS
access to build dependencies. An approved network retry of
`python -m pip install -e ".[dev]"` succeeded, installing `google-genai 2.31.0`,
Pydantic 2.14.1, pytest 8.4.2, and Ruff 0.17.0. Package installation/documentation
lookup were network operations; diagnosis verification itself remained offline.

- The first run of the unchanged existing suite after initial integration passed
  **459 tests in 1.51s**. Ruff found nine new long lines; they were corrected.
- The first new targeted run recorded **2 failed, 89 passed in 1.22s**. Both were
  fixture mistakes: an allegedly raw-only marker was repeated into the pipeline's
  legitimate five-line context window, and a success test used a nonexistent
  fixture attribute. Corrected the marker placement and fixture reference. No
  change to evidence selection or collection behavior was needed.
- The resulting full suite passed **550 tests in 1.44s**, with Ruff clean.
- Review tightened candidate handling to reject mixed text/non-text parts, even
  when a part also contains usable text. Added a regression with executable-code
  metadata; nothing executes. This was a preventive implementation correction,
  not an independently reproduced historical incident.
- Added a smoke-script regression proving missing consent makes no call and
  malformed output cannot trigger a second generation request. A subsequent full
  run passed **552 tests in 1.34s**; Ruff and `git diff --check` passed.
- Separately installed `google-genai 1.55.0` without dependencies under a temporary
  path and ran the SDK boundary suite using that version: **28 passed in 0.41s**.
  This exercises real SDK serialization and mocked HTTP, not live Vertex schema
  acceptance. Main verification uses SDK 2.31.0.
- CLI and smoke-script `--help` checks passed without network/ADC initialization.

New tests cover deterministic prompt contents, no raw-source/known-secret leakage,
context bounds, source references, strict draft shape/labels/fields, unknown/missing/
duplicate evidence references, duplicate JSON keys, malformed/empty/oversized output,
bounded fresh retries, ADC/permission/quota/timeout/network/SDK errors, blocked and
unfinished candidates, prompt injection with invalid references, immutable trusted
report records, additive truncation/warning limitations, confidence policy, local
abstention, consent, CLI integration, and synthetic smoke gating. Real SDK calls
are routed through `httpx.MockTransport`, asserting ADC headers, schema, endpoint,
and exactly one generation request on quota failure. The existing socket/DNS guard
remains active. No Google credentials were needed, no Gemini call was made, and no
repository logs were transmitted externally.

### Remaining limitations and unverified behavior

No live diagnosis-path request, real project permission check, server schema
acceptance, billing measurement, or live model-quality/injection evaluation was
performed. Python 3.11 execution and remote GitHub Actions were not run in this
session; the existing workflow still targets 3.11. The README's separately
authorized synthetic smoke checks one request only, not real-repository accuracy.

Redaction may miss secrets. Model prose can still follow malicious suggestions or
cite irrelevant valid evidence; schema/reference checks cannot certify truth or
causality. Confidence labels are qualitative. Incomplete data can support an
incorrect medium-confidence explanation. Pipeline extraction/collection limits,
commit/checkout differences, and earlier SSRF/DNS caveats still apply. Library
callers must obtain consent and independently validate provenance before diagnosis.

Character limits are not exact token or cost estimates; schema/system instructions
and model thinking add token usage. The optional validation retry can double
requests. SDK timeout settings bound HTTP operations, not a hard wall-clock deadline
for ADC discovery/refresh or the full command. Response character checks run after
SDK decoding. No model tools, caching, automatic file changes, or unbounded loop
was introduced. Changes are left uncommitted for independent review.

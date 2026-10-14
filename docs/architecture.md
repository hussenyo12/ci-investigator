# Architecture

## Implemented foundation

The package exposes four Pydantic models, `parse_workflow_run_url`, and
`validate_report_evidence`. Pydantic is the only runtime dependency. The standard
library URL parser performs no DNS resolution or HTTP requests.

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

## Planned pipeline

1. Parse a public GitHub workflow run URL into a canonical identity.
2. Fetch run metadata, failed jobs, and logs. Resolve repository configuration from
   the exact commit SHA attached to the run, rather than the current branch head.
3. Extract relevant evidence with original source IDs and line numbers. Sanitize
   secrets before sending excerpts to an LLM; treat logs/configuration as untrusted
   data rather than instructions. Keep provenance through any transformations.
4. Ask Gemini through Vertex AI for structured hypotheses, verification steps,
   next steps, and limitations. Permit an explicit insufficient-evidence outcome.
5. Apply model validation and deterministic evidence-reference validation.
6. Present the report through Streamlit, distinguishing observations from possible
   causes and exposing evidence and limitations.

Only step 1 and the data/reference-validation portions of step 5 exist today.
No API clients, cloud SDKs, prompts, orchestration, storage, or UI are included.

## Trade-offs and limitations

- Public `github.com` only; GitHub Enterprise hosts and job URLs are outside scope.
- Query parameters are ignored; rerun attempts are not represented separately.
- Syntactic owner/repository checks do not establish repository or run existence.
- Evidence IDs are unique within a report, not globally across investigations.
- No assumption that excerpt line count equals its original source range, allowing
  future sanitization to preserve provenance without forcing a false equality.
- Collection fields are ordinary lists; consumers must revalidate after mutation.
- Dependency version ranges permit compatible updates; no lockfile is supplied.

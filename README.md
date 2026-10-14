# CI Investigator

An AI-powered GitHub Actions failure investigation tool, currently at the offline
project foundation stage. Python 3.11+ is required.

## Install and test

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check .
```

Installation downloads dependencies. Once installed, the package and tests work
offline without GitHub tokens, Google Cloud credentials, or API requests. The test
suite blocks socket creation to detect accidental network use. GitHub Actions runs
the same lint and test commands on pushes and pull requests with Python 3.11.

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
exact commit → evidence extraction and sanitization → Gemini reasoning through
Vertex AI → deterministic reference validation → Streamlit presentation. GitHub,
Gemini, evidence sanitization, and Streamlit integrations are not implemented yet.
See [architecture](docs/architecture.md) and the [development log](docs/development_log.md).

`.env.example` reserves placeholder names for future integrations. The foundation
does not load environment configuration. Keep real secrets in ignored local files;
never add credential JSON files or tokens to the repository.

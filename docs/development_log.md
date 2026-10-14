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

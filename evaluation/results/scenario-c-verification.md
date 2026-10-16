# Scenario C — recorded verification, full report unavailable

This note summarizes available session observations. It is not a reconstructed
`InvestigationReport` or a new evaluation. The supplied temporary directory has no
`scenario-c-report.json` or `scenario-c-execution.json`.

- Run: https://github.com/hussenyo12/ci-investigator-demo/actions/runs/38744993448
- Recorded demo commit: `8afc260aa320a42a0eaa908820e5a18e881c5b52`, attempt 1.
- Preview: `ready`, two excerpts; `incomplete=false`, `logs_truncated=false`,
  `evidence_omitted=false`. Workflow YAML was present; optional-file warnings remained.
- The log contained `RuntimeError: DEMO_SERVICE_ENDPOINT environment variable is required`.
- The authorized CLI invocation exited 0 and returned `diagnosed`, with one
  high-confidence hypothesis identifying the missing required environment variable.
- Both supporting IDs resolved and evidence records matched the preview:
  `ev-73ab7750c2dc9f91e6c0ee3d` (job `116456206813` log, lines 198–214) and
  `ev-59e708d3d888b2cd3a788122` (workflow
  `.github/workflows/03-missing-env-var.yml`, lines 17–30).
- The report recommended checking the script's requirement and configuring the
  variable for the failing step. This paraphrases recorded observations; the exact
  full report is not supplied here.
- Post-response assessment: correct match to the missing-environment-variable
  scenario. User-provided ground truth was not added to the prompt.
- One CLI invocation was recorded. Actual generation/HTTP request counts were not
  instrumented for C; the application allowed at most two generation requests.

These observations verify one real evidence-backed diagnosis, not general causal
accuracy, complete redaction, or all possible environment-variable failure modes.
No live GitHub or Gemini calls were made to create this note.

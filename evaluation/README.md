# Recorded real GitHub evaluations

These are historical evaluation artifacts, not model inputs or production
configuration. Ground-truth assessments are for post-response human review only.
No live request was made while preserving these files for F-009.

| Scenario | Final report / record | Evaluation finding | Observed SDK generation calls / Vertex HTTP sends |
| --- | --- | --- | --- |
| A — missing dependency | [Report](results/scenario-a-report.json) | Correct; configuration contained a ground-truth hint | 1 / 1 |
| B — assertion mismatch | [Report](results/scenario-b-report.json) | Correct conclusion; unjustified high confidence | 1 / 1 |
| C — missing environment variable | [Verification note](results/scenario-c-verification.md) | Correct, evidence-grounded diagnosis; full JSON unavailable | Not measured |
| D — successful control | [Report](results/scenario-d-report.json) | Correct abstention, no hypotheses; historical F-009 limitation retained | 0 / 0 |

[Combined evaluation summary](results/evaluation-summary.json) preserves exact
observed summaries, hypotheses, actions, run URLs, ground-truth assessments,
caveats, and measured request counts for A/B/D. Per-scenario `*-execution.json`
files retain CLI exit codes and counters. A read-only Python call profiler counted
SDK generation entries and Vertex HTTPX send attempts; these are observations,
not independent billing records. Each CLI ran once, with no manual retry.

The evaluated demo commit was `8afc260aa320a42a0eaa908820e5a18e881c5b52`, attempt 1.
The investigator checkout at evaluation had HEAD
`fbc7552fbfaf4d4cca463601b1d0f967051f1627` plus the then-uncommitted F-008 changes,
subsequently committed as `d45bab6`. These reports predate the F-009 correction.

## Preservation and privacy

All seven saved A/B/D JSON files were found. The three reports are byte-for-byte
copies. Only `report_path` values in execution/summary metadata were changed to
relative filenames to remove obsolete personal macOS paths. The
[preservation manifest](results/preservation-manifest.json) records source/copy
SHA-256 hashes, transformations, commit provenance, and unavailable C files.

Before copying, all JSON fields were inspected for token/credential patterns,
private keys, redaction changes, and unnecessary personal paths; report evidence
and narratives were reviewed. No credential patterns or additional redaction
changes were found. Generic public GitHub runner paths remain within evidence to
preserve excerpts, IDs, and original line ranges. This review is best-effort;
unknown or obfuscated secrets cannot be ruled out by pattern matching.

`scenario-c-report.json` and `scenario-c-execution.json` do not exist in the supplied
temporary directory. Neither was reconstructed. The separate C note records only
available verification observations and explicitly leaves unmeasured counts unknown.

## Evaluation caveats

- **A:** `requirements.txt` explicitly says Scenario A deliberately omits PyYAML.
  That comment was part of collected evidence; no user-supplied ground truth was
  injected. The result is correct but cannot establish blind diagnostic ability.
  The same requirements excerpt also appears in B's context.
- **B:** Pytest shows `add(2, 2)` returned 4 while the assertion expected 5. The
  model correctly identified the intended scenario but definitively claimed the
  implementation was correct without its source or an independently established
  behavior contract. Its own limitation acknowledges the missing source.
- **D:** The saved report retains the historical provisional/incomplete limitation
  caused by `no_failed_jobs`. F-009 corrects fresh preparation; it does not rewrite
  this record or claim a new live evaluation. Zero generation calls were observed
  in the original evaluation; new offline regressions preserve the guard.
- Valid evidence IDs establish reference integrity, not causal correctness or
  calibrated confidence. These four cases are not broad quality or security benchmarks.

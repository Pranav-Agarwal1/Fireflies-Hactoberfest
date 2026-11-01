# Current mode: local maintainer logs only

GitHub mutations and automatic issue drafting have been removed. Earlier publication-related results below describe previous builds, not supported behavior. The current regression suite checks read-only transport and local lifecycle logs. Final documentation will follow the human test.

# GitHub PR monitor validation

The final suite passed **156 tests** on Python **3.9.6** and **3.12.14**. Both runs used `tests/run_offline.py`, which blocks Python outbound connections and DNS and disables Git transports during tests. Production GitHub requests use HTTPS, while production AI requests use loopback Ollama.

```bash
python3 tests/run_offline.py
```

## PR lifecycle coverage

The test suite exercises open-PR advisory comments, unchanged PRs, updates to an existing comment, corrected documentation, merged-PR issue creation, closed-without-merge handling, reopened PRs, PR changes during analysis, merges discovered between polls, incomplete analysis, issue-creation timeouts and duplicate recovery. It also checks preview-mode writes, state-mode separation, issue labels, owned comment markers and ignoring PRs targeting branches other than main.

The GitHub snapshot adapter is tested using immutable synthetic Git blobs with real content hashes. It verifies cached content, compares old/current source, detects a prose default mismatch and reuses unchanged documentation blobs. Pagination and truncated-tree rejection are tested.

GitHub requests, comments, issue creation and label creation are mocked. No live GitHub comments or issues were posted during development. A live deployment needs a target repository, an appropriately scoped user token, a running local model server and installed model weights. Continuous process operation, real GitHub rate limits and performance on large PR collections require deployment verification.

## Existing analysis coverage

Local Git integration tests create real temporary repositories and merge histories. Mechanical checks cover Python example/signature drift, removed declarations, potential replacements, explicit prose/table defaults, argparse defaults, literal constants, negative migration/override examples and historical replay before a later documentation correction. Model protocol tests reject fabricated and ambiguous citations, remote model endpoints, malformed output and truncated results.

Issue draft tests cover small grounded fixes, deduplication, current manifests after correction and complex/incomplete findings. Post-merge publishing uses documentation labels for broader alerts and suggests good-first-issue labels for bounded static fixes.

## Local inference evidence

A preceding local CLI build was exercised with already installed `gemma4:latest` weights through loopback Ollama. The model identified a synthetic stale `retries` argument after a change to `timeout`, using valid code and documentation quotations. Current GitHub lifecycle tests use mocked model responses or static analysis. The GitHub monitor itself has not been exercised against a real repository with live local inference.

The default 16 GiB Mac model remains `qwen2.5-coder:7b` Q4_K_M at 8,192 context. Its weights are not bundled or downloaded automatically. Its memory use, speed and task accuracy have not been benchmarked here. Model conclusions remain advisory and require evaluation against labelled project histories.

## Source references

GitHub's REST APIs supply PR metadata, PR conversation comments and issues. These are the primary references used for the integration.

- [Pull requests API](https://docs.github.com/en/rest/pulls/pulls)
- [Issue and PR comments API](https://docs.github.com/en/rest/issues/comments)
- [Issues API](https://docs.github.com/en/rest/issues/issues)

## Portable setup and latest-documentation regressions

The 135-test release includes a runtime prerequisite checker and its CLI entry point. Tests verify local-model availability diagnostics, actionable setup instructions, token requirements for publishing and secret redaction. It also includes actual local Git documentation commits representing a fix after an alert and a fix merged between polling cycles. Both suppress follow-up issues when reanalysis establishes that the discrepancy was resolved. Another regression changes the main revision during issue-label preparation and verifies that creation is deferred without marking the PR complete.

Both final suites passed using Python 3.9.6 and Python 3.12.14 with network access blocked in the test harness. New-laptop OS installation and live GitHub publication remain deployment checks.

## Bootstrap validation

The final 145-test suite passed on Python 3.9.6 and 3.12.14. Bootstrap tests cover reuse of installed weights, explicit download authorization, disk-space checks, preserving previous configuration on failure, missing local server, offline mode, failed inference, local endpoint enforcement, secret exclusion from saved configuration/download environments, and CLI configuration consumption with explicit overrides.

A real setup run with `--offline --model gemma4:latest --ram-gb 16 --unified` completed against the already installed local Ollama model. The inference check passed and a configuration file was saved in the workspace test directory. No model was downloaded. Missing-model downloads are tested with subprocess mocks, and OS software installation remains a manual prerequisite.

## First-run audit and live local test

The final 151-test suites passed on Python 3.9.6 and 3.12.14. New tests cover initial whole-supported-source audit, owner summary alerts, incomplete initial coverage, initial completion retained across process restart, and current default inconsistencies without historical changes. Local-model drift suggestions now require an additional local contradiction-verification verdict.

During a live test on the current machine, hardware detection reported 24 GiB unified RAM and recommended the medium-tier Qwen model. Bootstrap reused the already installed Gemma model with an explicit override. A synthetic stale README produced one static drift finding. The first fixed-documentation run exposed a model false positive describing already-consistent docs as drift. After adding contradiction verification, the corrected repository was reanalysed with real local inference and returned zero findings and zero errors. This validates those examples rather than general model accuracy.

No live GitHub repository was selected for the test and no GitHub token was configured. Initial GitHub summaries, PR comments and issues remain validated with mocked GitHub APIs.

## Local performance fix

Long-running tests were stopped at the user's request. Focused verification of repository reads, initial audit limits, PR lifecycle, model protocol and prose checks passed 75 tests after the performance changes. A no-inference benchmark prepared the Open Food Facts snapshot and retrieved 15 sections in 15.95 seconds. The real local-model 15-section audit completed with zero findings and zero model errors in that subset, with explicit incomplete coverage and exit code 2. Its measured stages were 11.65 seconds static/index preparation, 3.81 seconds retrieval-index construction, 0.155 seconds lookup and 44.89 seconds inference. The checkout remained unchanged.

These measurements separate source preparation from local model runtime. Full-repository detection accuracy and oversized/skipped files remain unverified. Actual reports are supplied separately under `openfoodfacts-optimized-15`.

## Human-test release candidate

Version 0.9.0 passed 156 tests on Python 3.9.6 and Python 3.12.14. Python compilation checks and the CLI local drift smoke test passed. Incomplete initial GitHub audits now fail visibly instead of returning success. Local batch-reading, cached manifests, indexed retrieval, bounded local review and progress output are included. Recommended Qwen weights are to be downloaded and verified by the human bootstrap run. Existing live model results used locally installed Gemma weights. No publishing or repository-content changes were performed for this release verification.

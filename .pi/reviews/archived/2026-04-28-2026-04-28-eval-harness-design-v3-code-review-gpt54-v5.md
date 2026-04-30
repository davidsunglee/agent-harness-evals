# Code Review — Eval Harness v3 (Era 5 / Iteration 1)

**Git range:** `2da6909b4bdbed9bfc9740ccc84ec1dca940b43b`..`a657bf0`
**Reviewer:** delegated (`openai-codex/gpt-5.5`, cli `pi`)
**Date:** 2026-04-29

---

### Reviewer Output

### Strengths
- Clean modular split across discovery, workspace, setup, runner, pipeline, campaign, status, and report.
- Good subprocess hygiene: capped stdout/stderr, pipe draining, watchdog timeouts, and process-group termination.
- Strong test coverage: `cd evals && uv run pytest -q` passes (`164 passed`), and real `just eval-all` produced 32 stub cells with `error_reason == "nonzero_exit"`.
- The major v3 requirements are covered: response.json gating, temp-index diff, venv-on-PATH separation, setup sentinels, resume by `meta.json`, and report generation.

### Issues

#### Critical (Must Fix)
None found.

#### Important (Should Fix)

1. **Single-cell `eval` can traceback when caches are missing**
   - File: `evals/evals/cli.py:421-425`, `evals/evals/cli.py:76-80`
   - Issue: `cmd_eval` directly calls `_run_one_cell`, which assumes `.runs-cache/<case>.git` and `.runs-cache/<case>.venv` already exist. On a fresh campaign after `eval-new` or after `eval-clean-cache`, this will raise from `git clone` instead of preparing or returning a helpful error.
   - Why it matters: `just eval <fw> <case>` is documented as “Run/rerun a single cell”; production CLI users should not get an unhandled traceback for a common sequence.
   - Fix: Mirror the selected-case prepare path from `eval-all` before running the cell, or explicitly detect missing cache artifacts and exit 2 with “run eval-prepare first”.

2. **Lock release can delete another process’s lock**
   - File: `evals/evals/campaign.py:244-248`
   - Issue: `release_lock` unconditionally unlinks `.lock`. If another process force-unlocks/takes over while the original process is still running, the original process can later delete the new owner’s lock.
   - Why it matters: This weakens the cross-host/force-unlock safety story and can allow concurrent writers into the same campaign.
   - Fix: Include a unique owner token in the lock file and only release if the current file still matches this process’s token.

3. **`token_usage` can be emitted for invalid envelopes without `response.json`**
   - File: `evals/evals/pipeline.py:321-333`, `evals/evals/pipeline.py:500-508`
   - Issue: `assemble_scoring` includes token usage from any parsed dict with `trace.tokens`, even if the runner rejected the envelope and did not write `response.json`.
   - Why it matters: The plan requires token usage to be omitted when response is absent; reports could show token metrics for malformed responses.
   - Fix: Pass `runner_result.response_path is not None` into scoring assembly and only include `token_usage` when the envelope-valid response exists.

#### Minor (Nice to Have)

1. **Headline stub integration test is weaker than the acceptance criterion**
   - File: `evals/tests/integration/test_eval_all_stub.py:54`, `evals/tests/integration/test_eval_all_stub.py:76`
   - Issue: The test runs `eval-all --timeout-s 30` instead of no flags and only asserts `cells_seen >= 1`.
   - Why it matters: A regression discovering only one framework would still pass.
   - Fix: Invoke `eval-all` without overrides and assert exactly the expected 8 frameworks × 1 case cells.

### Recommendations
- Add a small CLI regression test for `eval` after `eval-new` with an empty cache.
- Consider holding the campaign lock before auto-prepare in `eval-all`, or adding a separate cache lock, to avoid concurrent prepare races.

### Assessment

**Ready to merge: With fixes**

**Reasoning:** Core architecture and test coverage are strong, and the harness works end-to-end with stub frameworks. The remaining issues are mostly production-hardening around CLI behavior, lock ownership, and one scoring invariant.

---

## Remediation Log

### Era 5 / Iteration 1

**Batch 1: CLI/lock/scoring hardening**
- Fixed single-cell `eval` to prepare the selected case/framework cache before reruns and to fail cleanly if selected-case prepare fails.
- Fixed campaign locks to carry an owner token so `release_lock()` only removes a lock still owned by the current process.
- Fixed scoring assembly so `token_usage` is omitted when the runner rejected the response and no valid `response.json` exists.
- Strengthened `evals/tests/integration/test_eval_all_stub.py` to run `eval-all` without overrides and assert the full expected stub matrix.
- Added regression coverage in `evals/tests/cli_test.py`, `evals/tests/campaign_test.py`, `evals/tests/pipeline_test.py`, and `evals/tests/integration/test_eval_all_stub.py`.
- Verification run:
  - `cd evals && uv run pytest -q tests/cli_test.py tests/campaign_test.py tests/pipeline_test.py tests/integration/test_eval_all_stub.py`
  - `cd evals && uv run pytest -q`

Remaining from reviewer: none from era 5 iteration 1.

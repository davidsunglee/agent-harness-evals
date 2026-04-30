# Code Review — Eval Harness v3 (Era 7 / Iteration 1)

**Git range:** `2da6909b4bdbed9bfc9740ccc84ec1dca940b43b`..`eec904a`
**Reviewer:** delegated (`openai-codex/gpt-5.5`, cli `pi`)
**Date:** 2026-04-29

---

### Reviewer Output

### Strengths
- Clean module separation across discovery, workspace, runner, pipeline, campaign, reporting, and CLI.
- Strong end-to-end coverage: verified `cd evals && uv run pytest -q` → `176 passed`.
- Good subprocess hygiene: capped stdout/stderr, pipe draining, process-group termination, and response-envelope handling are well covered.
- Resume semantics and stub-framework integration are tested.

### Issues

#### Critical (Must Fix)

1. **Case IDs can escape harness artifact directories**
   - File: `evals/evals/schemas.py:72-78`, used at `evals/evals/cli.py:391-395`, `evals/evals/cli.py:472-474`, `evals/evals/workspace.py:78-79`, `evals/evals/workspace.py:189-190`
   - Issue: `case_id` allows `/` and `..`, then is used directly in cache paths and cell paths, including `shutil.rmtree(cell_dir)`.
   - Why it matters: a malformed/malicious case like `../../some-dir` can write outside `.runs-cache/` / `runs/` and potentially delete unintended directories on rerun.
   - Fix: either restrict `case_id` to a safe slug like `[A-Za-z0-9_.-]+` with no path separators, or encode case IDs for filesystem paths and assert resolved paths stay under the intended root before writes/deletes.

#### Important (Should Fix)

1. **Malformed `failure_output_path` can crash discovery instead of returning `DiscoveryError`**
   - File: `evals/evals/schemas.py:90-96`, `evals/evals/discovery.py:145-147`
   - Issue: the validator only checks XOR presence, not that `failure_output` / `failure_output_path` are strings. `failure_output_path: 123` passes validation, then `Path(123)` raises `TypeError`.
   - Why it matters: this violates the structured-discovery-error contract and can crash CLI commands on a bad case manifest.
   - Fix: validate `failure_output` as `str` and `failure_output_path` as non-empty `str`; add regression tests for non-string values.

#### Minor (Nice to Have)

1. **`uv sync` stderr is captured but not surfaced**
   - File: `evals/evals/workspace.py:178-179`, `evals/evals/cli.py:193-196`
   - Issue: `WorkspaceError` stores stderr, but `_do_prepare` prints only `uv sync failed`.
   - Impact: real dependency/setup failures will be hard to diagnose.
   - Fix: include stderr in the summary or write per-case sync logs under `.runs-cache/`.

### Recommendations
- Add path-safety tests for malicious `case_id` values and malformed `failure_output_path` types.
- Consider a small path helper for all artifact paths that enforces `resolve().relative_to(root)` before destructive operations.

### Assessment

**Ready to merge: With fixes**

**Reasoning:** Core architecture and test coverage are strong, and the full suite passes. The path traversal/data-loss risk in case IDs should be fixed before production use.

---

## Remediation Log

### Era 7 / Iteration 1

**Batch 1: case-manifest path safety + discovery robustness**
- Tightened case manifest validation so `case_id` must be a single safe filesystem slug, preventing path traversal through cache/cell artifact paths.
- Fixed case manifest validation so non-string `failure_output` / `failure_output_path` values are rejected as structured discovery errors instead of crashing discovery.
- Fixed prepare summaries to include captured `uv sync` stderr when available for diagnosis.
- Added regression coverage in `evals/tests/schemas_test.py`, `evals/tests/discovery_test.py`, and `evals/tests/cli_test.py`.
- Verification run:
  - `cd evals && uv run pytest -q tests/schemas_test.py tests/discovery_test.py tests/cli_test.py`
  - `cd evals && uv run pytest -q -m 'not integration'`
  - `git diff --check`

Remaining from reviewer: none from era 7 iteration 1.

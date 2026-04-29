# Code Review — Eval Harness v3 (Era 10 / Final Review)

**Git range:** `2da6909b4bdbed9bfc9740ccc84ec1dca940b43b`..`c284998`
**Reviewer:** delegated (`openai-codex/gpt-5.5`, cli `pi`)
**Date:** 2026-04-29

---

### Reviewer Output

### Strengths
- Clear module separation across discovery, workspace, setup, runner, pipeline, campaign, status, and reporting.
- Strong test coverage: unit + integration suites pass locally (`181 passed, 19 deselected`; integration `19 passed`).
- Good subprocess hygiene overall: capped stdout/stderr, process-group cleanup, response envelope validation, and atomic `meta.json` sentinel writes.
- Stub framework end-to-end path works for a filtered real run (`deepagents` / `py-parse-duration-001` produced `error_reason=nonzero_exit` as expected).

### Issues

#### Critical
None found.

#### Important
1. **`eval-all` reruns failed framework setup instead of honoring `.fail` as a terminal sentinel**
   - File: `evals/evals/cli.py:133-137`, `evals/evals/cli.py:388-397`
   - Issue: `_prepare_needed()` returns `True` when `<fw>.fail` exists, so every `eval-all` reruns setup before cells. The runner already treats `.fail` as `framework_misconfigured` (`evals/evals/runner.py:222-223`).
   - Why it matters: A setup that consistently fails or times out can make every resume spend up to the 600s setup timeout before reaching cells, defeating the `.fail` sentinel pipeline described in the plan.
   - Fix: Treat `.fail` as a present setup sentinel unless the manifest/setup fingerprint changed, or only retry setup on explicit `eval-prepare`.

#### Minor
1. **Case ID validator diverges from the planned regex**
   - File: `evals/evals/schemas.py:12`, `evals/evals/schemas.py:81-88`
   - Issue: The implementation disallows `/`, while the plan specified `^[a-zA-Z0-9_.\-/]+$`.
   - Why it matters: Future manifests using slash-containing case IDs per spec will be rejected.
   - Fix: Either align the regex with the spec and safely map IDs to artifact paths, or update the spec/tests to require single-segment IDs.

2. **Edit constraint matcher uses `gitignore` instead of planned `gitwildmatch`**
   - File: `evals/evals/pipeline.py:284-293`
   - Issue: The plan called for `PathSpec.from_lines("gitwildmatch", ...)`; implementation uses `"gitignore"`.
   - Why it matters: Usually equivalent, but edge-case glob semantics may differ from the documented contract.
   - Fix: Switch to `"gitwildmatch"` unless there is a deliberate compatibility reason.

## Remediation Log

### Follow-up after final review
- Fixed `evals/evals/cli.py` so `_prepare_needed()` treats a fresh setup `.fail` sentinel as terminal for `eval-all`, avoiding repeated setup retries on unchanged failing frameworks.
- Fixed `evals/evals/setup.py` so `.fail` sentinels persist the same setup fingerprint metadata as `.ok` sentinels.
- Fixed the remaining `eval-all` case-prep path so fresh unchanged `.fail` sentinels are skipped even when unrelated case preparation is needed, while explicit `eval-prepare` still retries failed setup.
- Added regression coverage in `evals/tests/cli_test.py` and `evals/tests/setup_test.py`.
- Verification run:
  - `cd evals && uv run pytest -q tests/cli_test.py tests/setup_test.py`
  - `cd evals && uv run pytest -q tests/cli_test.py::test_eval_all_skips_fresh_framework_setup_fail_when_case_prepare_needed tests/cli_test.py`

### Recommendations
- Add a regression test for the `.fail` setup sentinel behavior: pre-create `.runs-cache/setup/<fw>.fail`, run `eval-all`, assert setup is not rerun and cells become `framework_misconfigured`.
- Consider documenting the intentional `case_id` single-path-segment hardening if keeping the stricter regex.

### Assessment

**Ready to merge: With fixes**

**Reasoning:** The harness is broadly well-structured and well-tested, but the setup `.fail` retry behavior can make resumability poor or unusable for failing/timeout setup commands and should be corrected before merge.

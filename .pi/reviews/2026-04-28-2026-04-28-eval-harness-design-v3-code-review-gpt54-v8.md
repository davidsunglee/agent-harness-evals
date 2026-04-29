# Code Review — Eval Harness v3 (Era 8 / Iteration 1)

**Git range:** `2da6909b4bdbed9bfc9740ccc84ec1dca940b43b`..`0ed3a7d`
**Reviewer:** delegated (`openai-codex/gpt-5.5`, cli `pi`)
**Date:** 2026-04-29

---

### Reviewer Output

### Strengths
- Clean modular structure matching the plan (`discovery`, `workspace`, `runner`, `pipeline`, `campaign`, `report`, etc.).
- Strong test coverage: fake-framework modes, resume behavior, cache/workspace logic, CLI, and integration paths.
- Good implementation of many production details: per-field config provenance, setup sentinels, response.json envelope gating, temp-index diffs, atomic `meta.json`/`scoring.json`, cross-host lock handling.

Validation run:
- `cd evals && uv run pytest -q` → `184 passed`
- Spot E2E: `just eval-all --framework deepagents --case py-parse-duration-001` produced `meta.status=error`, `error_reason=nonzero_exit`.

### Issues

#### Critical (Must Fix)

1. **Subprocess timeout does not cover background descendants holding pipes open**
   - Files:
     - `evals/evals/pipeline.py:205-213`
     - `evals/evals/runner.py:315-330`
     - `evals/evals/setup.py:335-343`
   - Issue: The code waits for the immediate child process, then joins stdout/stderr pump threads with no deadline. If the child exits but leaves a background descendant that inherited stdout/stderr, the pump threads block until that descendant exits.
   - Why it matters: One bad framework adapter or test command can hang the whole eval despite `timeout_s`, violating the watchdog requirement.
   - Evidence: A `run_test_command(..., timeout_s=1)` command that spawned `sleep 3` returned after ~3s and marked the test as `pass`.
   - Fix: Preserve the parent exit code, then terminate/wait the process group before joining pump threads, or join pumps under the remaining deadline and kill the process group if still open. Add regression tests for background descendants holding stdout/stderr.

#### Important (Should Fix)

1. **Entry validation falsely rejects PATH-resolved commands**
   - File: `evals/evals/runner.py:159-173`
   - Issue: `entry: "python run.py"` or `entry: "node run.js"` is resolved as `<framework_dir>/python`, then classified as `framework_misconfigured`.
   - Why it matters: Framework-agnostic adapters will often invoke interpreters or CLIs from PATH.
   - Fix: Only resolve relative file paths against `framework.dir` when `argv[0]` contains `/`; otherwise use `shutil.which(..., path=agent_env["PATH"])` or let `Popen` decide.

2. **Visible/hidden test commands are silently rewritten**
   - File: `evals/evals/pipeline.py:141-176`
   - Issue: `pytest ...` and `uv run pytest ...` are rewritten to `python -m pytest ...`.
   - Why it matters: The task spec says the harness reruns the declared `failing_test_command`. Silent rewriting can diverge from the command the case author specified and from what the agent sees, especially with uv flags, extras, scripts, or nonstandard env behavior.
   - Fix: Either make this an explicit case-manifest/documented behavior, or execute the original command with a no-sync-safe mechanism. At minimum record both original and effective command in `visible_test.json` / `hidden_test.json`.

#### Minor (Nice to Have)

1. **Report can include stale setup failures outside the campaign**
   - File: `evals/evals/report.py:136-144`
   - Issue: Notes include every `.runs-cache/setup/*.fail`, even for frameworks not in the campaign manifest.
   - Fix: Filter setup failure notes to `fw_name in manifest["frameworks"]`.

### Recommendations
- Add timeout regression tests for subprocesses that spawn background children and exit.
- Add CLI/runner tests for `entry: "python run.py"` and `entry: "node run.js"` style manifests.
- Document the pytest command normalization if it remains intentional.

### Assessment

**Ready to merge: No**

**Reasoning:** The harness is well structured and well tested overall, but the subprocess timeout gap is a production blocker because a single adapter/test can hang an eval indefinitely despite configured timeouts.

---

## Remediation Log

### Era 8 / Iteration 1

**Batch 1: subprocess/entry/report hardening**
- Fixed runner/setup/test subprocess handling so any remaining process-group descendants are terminated before pump-thread joins, preventing background pipe holders from bypassing timeouts.
- Fixed runner entry validation to allow PATH-resolved commands such as `python run.py`.
- Updated test-run artifacts to record both `original_command` and `effective_command` when pytest command normalization is applied.
- Fixed report setup-failure notes to include only frameworks present in the current campaign manifest.
- Added regression coverage in `evals/tests/runner_test.py`, `evals/tests/pipeline_test.py`, `evals/tests/setup_test.py`, and `evals/tests/report_test.py`.
- Verification run:
  - `cd evals && uv run pytest -q tests/runner_test.py tests/pipeline_test.py tests/setup_test.py tests/report_test.py`
  - `cd evals && uv run pytest -q`
  - `git diff --check`

Remaining from reviewer: none from era 8 iteration 1.

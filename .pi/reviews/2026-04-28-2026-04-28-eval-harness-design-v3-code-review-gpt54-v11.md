# Code Review — Eval Harness v3 (Era 11 / Full Review)

**Git range:** `2da6909b4bdbed9bfc9740ccc84ec1dca940b43b`..`c5f9c2b`
**Reviewer:** delegated (`openai-codex/gpt-5.5`, cli `pi`)
**Date:** 2026-04-29

---

### Reviewer Output

### Strengths
- Clear module separation across discovery, workspace, setup, runner, pipeline, campaign, status, and report.
- Good subprocess hygiene: capped stdout/stderr, pipe draining, process-group termination, and timeout handling are covered.
- Strong test coverage: I ran `cd evals && uv run pytest -q` and got `202 passed`.
- End-to-end stub-framework path is covered by integration tests, and `just frameworks` lists all 8 frameworks.

### Issues

#### Critical (Must Fix)
None found.

#### Important (Should Fix)

1. **Harness rewrites shell test commands, violating the `/bin/sh -c` contract**
   - File: `evals/evals/pipeline.py:143-179`
   - Issue: `_normalize_pytest_command()` parses the full case test command with `shlex.split()` and rewrites `uv run pytest ...` / `pytest ...` to `python -m pytest ...`.
   - Why it matters: Case test commands are specified as shell commands. This rewrite changes behavior for valid shell syntax such as `uv run pytest ... || true`, env assignments, redirections, shell variables, or command composition. It can cause the harness to score a different command than the case manifest declared.
   - Fix: Prefer executing the manifest command unchanged via `/bin/sh -c`. If special handling is required for self-hosting pytest fixtures, encode that in the case manifest command itself or add a narrowly-scoped, explicit case-level option rather than rewriting arbitrary shell strings.

#### Minor (Nice to Have)

1. **Unrelated `.pi` review artifacts are included in the diff**
   - Files: `.pi/specs/reviews/*.md`, `.pi/todos/012985d7.md`
   - Issue: The change set includes many generated review documents and todo-status updates that are not part of the eval harness implementation.
   - Why it matters: It adds noise to the production diff and makes future audits harder.
   - Fix: Consider excluding generated review artifacts from this PR unless the repo intentionally tracks them as part of the workflow.

## Remediation Log

### Follow-up after full review
- Removed shell-command rewriting from `evals/evals/pipeline.py`; manifest test commands now execute unchanged via `/bin/sh -c`.
- Added regression coverage for preserving declared shell syntax, including redirection.
- Updated the self-hosting pytest fixture integration test to declare `python -m pytest ...` explicitly instead of relying on harness rewriting.
- Updated docs/tests accordingly.
- Verification run:
  - `cd evals && uv run pytest -q tests/pipeline_test.py tests/readme_test.py tests/integration/test_pytest_fixture.py::test_pytest_fixture_declared_python_module_command_executes_source_without_project_install`
  - `cd evals && uv run pytest -q -m 'not integration'`

### Recommendations
- Add a regression test for shell-preserving test commands, e.g. a case command containing `uv run pytest ... || exit 7` or an env assignment, to ensure the harness executes exactly what the manifest specifies.
- Consider documenting the deliberate `PYTHONPATH`/`UV_NO_SYNC` strategy in `evals/README.md`, since it is central to the no-install-project guarantee.

### Assessment

**Ready to merge: With fixes**

**Reasoning:** The implementation is broad, well-tested, and mostly matches the v3 plan. The main blocker is the shell-command rewrite in test reruns, which violates a stated command-execution rule and can produce incorrect scoring for valid case manifests.

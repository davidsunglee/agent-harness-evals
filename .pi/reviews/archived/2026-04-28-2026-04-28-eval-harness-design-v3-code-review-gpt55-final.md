**Reviewer:** delegated (openai-codex/gpt-5.5, cli pi)

### Strengths
- Clear module boundaries and orchestration flow: CLI discovery/prepare/run paths are separated from runner, pipeline, setup, campaign, and reporting concerns (`evals/evals/cli.py:127`, `evals/evals/runner.py:196`, `evals/evals/pipeline.py:497`).
- Runner implements the important subprocess contract details: capped stdout/stderr, timeout precedence, `response.json` only for envelope-valid stdout, and per-field config provenance (`evals/evals/runner.py:24`, `evals/evals/runner.py:100`, `evals/evals/runner.py:398`).
- Pipeline handles production-safety requirements well: temp-index diff avoids mutating the real index, visible/hidden reruns cap output, and `scoring.json`/`meta.json` are written with temp-and-rename (`evals/evals/pipeline.py:57`, `evals/evals/pipeline.py:81`, `evals/evals/pipeline.py:151`, `evals/evals/pipeline.py:338`).
- Environment separation matches the v3 design: framework-declared keys are only exposed to agent/setup envs, while test envs prepend the venv and avoid framework secrets (`evals/evals/env.py:30`, `evals/evals/env.py:51`, `evals/evals/env.py:75`).
- Locking and setup handling include the tricky lifecycle cases: setup fingerprints/stale sentinels, failed setup fail-fast cells, atomic lock acquisition, cross-host refusal, and owned-lock release (`evals/evals/setup.py:156`, `evals/evals/setup.py:246`, `evals/evals/campaign.py:152`, `evals/evals/campaign.py:202`, `evals/evals/campaign.py:254`).
- Test coverage is broad and behavior-focused, including fake-framework integration coverage for all requested error classes and resume behavior.

### Issues

#### Critical (Must Fix)
- None found.

#### Important (Should Fix)
- None found.

#### Minor (Nice to Have)
- None found.

### Recommendations
- Keep the fake-framework integration matrix as the contract regression suite for future adapter work; it is the highest-value guard against breaking classification and artifact semantics.
- Consider adding a CI job split between `pytest -m "not integration"` and `pytest -m integration` so slow/network-sensitive failures are easy to triage separately.

### Assessment

**Ready to merge: Yes**

**Reasoning:** The implementation matches the v3 plan requirements I reviewed, including config provenance, environment separation, setup sentinels, lock semantics, resumability, capped subprocess/test output, atomic completion artifacts, and reporting. Verification passed with `cd evals && uv run pytest -q -m 'not integration'` (199 passed, 19 deselected), `cd evals && uv run pytest -q -m integration` (19 passed, 199 deselected), `cd evals && uv run pytest -q` (218 passed), and `just frameworks && just cases` (8 frameworks and 4 cases listed).

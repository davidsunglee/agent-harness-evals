# Plan: Eval Harness v3

**Source:** TODO-012985d7
**Spec:** .pi/specs/2026-04-28-eval-harness-design-v3.md

## Goal

Build the framework-agnostic eval harness in `evals/` that runs each framework adapter against each case end-to-end, scores the result against `shared/task-spec.md`, and emits a per-campaign markdown report. V1 is sequential, resumable, and produces a runnable pipeline on a fresh clone (with stub framework scripts every cell still gets a `status: "error", error_reason: "nonzero_exit"` cell-dir). The harness must hit every rule in v3 of the design spec: per-field config provenance, `agent_env`/`test_env` separation with venv-on-PATH, `--no-install-project` venv, response.json-only-when-envelope-valid, atomic temp-and-rename for sentinels, framework-misconfigured artifacts, cross-host lockfile refuse with `--force-unlock`, test-output caps, and the framework-`setup`-failure pipeline (`.ok`/`.fail` sentinels).

## Architecture summary

A single Python package (`evals/evals/`) invoked via `python -m evals` (in turn delegated from `justfile` verbs). Layered modules with one-way dependencies:

```
cli ─┬─> campaign ─> schemas
     ├─> status ──> discovery
     ├─> report ──┬─> schemas
     ├─> setup ───┴─> env
     ├─> runner ─┬─> env
     │           ├─> schemas
     │           └─> workspace ─> (filesystem only)
     └─> pipeline ┬─> schemas
                  └─> env
```

Three artifact spaces, all repo-root and gitignored:
- `.runs-cache/` — derived state (per-case bare git repo, per-case venv, per-framework setup sentinels).
- `runs/<ts>/` — campaign dirs (one cell per `(framework, case)` for v1).
- `runs/CURRENT` — relative symlink → active campaign.

Subprocess transport is `shared/contract.md`'s JSON-over-stdin/stdout. The harness never imports framework code; only invokes scripts declared in `frameworks/<name>/manifest.json`. Stub scripts are shipped under each `frameworks/<name>/` so `just eval-all` runs end-to-end on day one (every cell errors as `nonzero_exit`).

A self-contained test harness lives at `evals/tests/` driven by a `fake-framework/` whose behavior is selected by the `FAKE_BEHAVIOR` env var, and a synthetic `test-case-001` fixture used to drive both the real workspace pipeline and the fake-framework integration suite.

## Tech stack

- Python ≥ 3.11 (already pinned in `evals/pyproject.toml`).
- Runtime deps: `pathspec` (gitignore-style globs for edit-constraint matching), `python-dotenv` (.env loading).
- Dev/test deps: `pytest`.
- Stdlib only for everything else: `argparse`, `json`, `subprocess`, `shlex`, `pathlib`, `hashlib` (BLAKE2 for content / lock / venv-fingerprint hashes), `os`, `signal`, `socket` (for `gethostname` in lockfile), `time`, `uuid`, `tempfile`, `re`.
- External tools invoked by subprocess: `git`, `uv`, `/bin/sh`.
- No `jsonschema` dependency — schemas are inline Python dicts validated by hand-rolled, scope-limited validators in `evals/evals/schemas.py`. The schemas in this repo are simple enough that this avoids one more dep.

---

## File Structure

### Created

#### Package modules (under `evals/evals/`)

- `evals/evals/__init__.py` (Create) — empty marker file.
- `evals/evals/__main__.py` (Create) — entry point: `python -m evals <subcommand>`. Delegates to `cli.main()`.
- `evals/evals/cli.py` (Create) — argparse subcommand dispatch (`frameworks`, `cases`, `eval-prepare`, `eval-new`, `eval-all`, `eval`, `eval-status`, `eval-report`, `eval-clean-cache`, `eval-clean-runs`); option-precedence and rejection rules for `--model` / `--timeout-s` / `--max-steps` / `--setup-timeout-s` / `--force-unlock` / `--framework` / `--case`.
- `evals/evals/discovery.py` (Create) — `discover_frameworks(repo_root)` and `discover_cases(repo_root)`. Each returns a typed list of `FrameworkSpec` / `CaseSpec` dataclasses; malformed manifests propagate as structured `DiscoveryError` records that the runner translates to `framework_misconfigured`.
- `evals/evals/schemas.py` (Create) — schemas as Python dicts + bespoke validators: `validate_framework_manifest(obj) -> list[str]`, `validate_case_manifest(obj) -> list[str]`, `validate_envelope(obj) -> list[str]`, `validate_agent_output(obj) -> list[str]` (the agent-output validator MUST flag any of the forbidden top-level keys `fixed`, `not_fixed`, `status`).
- `evals/evals/env.py` (Create) — `.env` loader (via `python-dotenv`'s `dotenv_values`) and constructors `build_agent_env(...)` / `build_test_env(...)`. Both prepend `<case-venv>/bin` to PATH per v3.
- `evals/evals/workspace.py` (Create) — `compute_fixture_hash(...)`, `compute_lock_hash(...)`, `compute_venv_fingerprint(...)`, `ensure_case_bare_repo(...)`, `ensure_case_venv(...)`, `clone_cell_worktree(...)`, `wipe_cell_dir(...)`. Each layer's "rebuild trigger" semantics from the spec live here.
- `evals/evals/setup.py` (Create) — framework `setup` runner. `run_framework_setup(framework, ...)`, `is_setup_ok(framework_name, cache_dir)`, `is_setup_failed(framework_name, cache_dir)`. Writes `.ok` only on exit 0; writes `.fail` on non-zero/timeout (mutually exclusive). Captures stdout/stderr to capped log files in `.runs-cache/setup/`. Continues past failures; returns aggregate result.
- `evals/evals/runner.py` (Create) — `run_cell(framework, case, effective_config, cell_dir, cache_dir, base_env, dotenv) -> RunnerResult`. Builds the request, writes `request.json`, spawns the entry script via `shlex.split(...)`, streams to capped `stdout.log` / `stderr.log` with pipe-drain after cap, enforces external watchdog timeout (SIGTERM → 5s grace → SIGKILL), classifies per the precedence table, and writes `response.json` iff envelope-valid. Surfaces `framework_misconfigured` cases (bad manifest, missing entry, unresolved setup `.fail`) WITHOUT spawning a subprocess; in that case it produces an empty `stdout.log` and a diagnostic `stderr.log`.
- `evals/evals/pipeline.py` (Create) — post-subprocess pipeline orchestrator. `run_pipeline(cell_dir, runner_result, case, effective_config, base_env, dotenv) -> None`. Implements steps 1–7 from the spec: response validation, **temp-index diff** via `GIT_INDEX_FILE`, visible test rerun (capped, drained), hidden test rerun, edit-constraint check via `pathspec`, `scoring.json` assembly (atomic write), `meta.json` write last as the done-sentinel (atomic temp-and-rename + best-effort parent-dir fsync). `scoring.json` is **always** written.
- `evals/evals/campaign.py` (Create) — `eval_new(...)`, `current_campaign(repo_root)`, `acquire_lock(...)`, `release_lock(...)`, `update_current_symlink(...)`. Lockfile semantics: refuse if PID alive same host, reclaim if PID dead same host, **refuse if hostname differs (overridable by `--force-unlock`, with logged warning)**.
- `evals/evals/status.py` (Create) — matrix renderer (text). Reads `runs/CURRENT/manifest.json` and walks the matrix.
- `evals/evals/report.py` (Create) — markdown report. Header, per-cell table (cells whose `effective_config != campaign config_overrides+defaults` flagged with `*`), per-framework summary, Notes section (typed-failure summaries with stderr links, setup failures, venv mutations).

#### Stub framework adapters (one set per framework dir)

- `frameworks/deepagents/manifest.json` (Create) — minimal: `entry: "./run.sh"`, `env: []`, `model: "claude-sonnet-4-6"`. **No `setup` field** so `eval-prepare` skips them.
- `frameworks/deepagents/run.sh` (Create) — `#!/bin/sh\necho "deepagents: not implemented" 1>&2\nexit 2`.
- Same `manifest.json` + `run.sh` pair for: `pydantic-ai`, `google-adk`, `strands`, `agentcore`, `claude-agent-sdk`, `openai-agents`. (8 frameworks total minus `mastra` — see Modify section: `mastra` is "TBD" per existing `frameworks/README.md` line 21; we still ship a stub there for symmetry and so the `eval-all` matrix is uniform.)
- `frameworks/mastra/manifest.json` (Create) — same shape but `model: "claude-sonnet-4-6"` (placeholder).
- `frameworks/mastra/run.sh` (Create) — same shape ("mastra: not implemented", exit 2).

#### Test fixtures (under `evals/tests/fixtures/`)

- `evals/tests/__init__.py` (Create) — empty marker.
- `evals/tests/conftest.py` (Create) — pytest fixtures: `repo_root`, `tmp_repo_root` (a temp dir wired up like the real repo so tests can run discovery/workspace against an isolated tree), `synthetic_case_dir` (path to `evals/tests/fixtures/cases/test-case-001/`), `fake_framework_dir` (path to `evals/tests/fixtures/fake-framework/`).
- `evals/tests/fixtures/fake-framework/manifest.json` (Create) — `{"entry": "./run.py", "env": ["FAKE_BEHAVIOR"], "model": "fake"}`.
- `evals/tests/fixtures/fake-framework/run.py` (Create) — Python script (executable, shebang `#!/usr/bin/env python3`) that reads request from stdin, branches on `FAKE_BEHAVIOR` env var across all the modes listed in the spec's "Fake framework" table (incl. v3 additions: `crash-with-error-envelope`, `crash-with-bad-json`, `oversize`, `noisy-stderr`, `mutate-venv`, `noisy-test-output`).
- `evals/tests/fixtures/cases/test-case-001/pyproject.toml` (Create) — minimal pyproject (no project install; `[dependency-groups] dev = ["pytest"]`).
- `evals/tests/fixtures/cases/test-case-001/test_case_001/__init__.py` (Create) — re-exports `add` from `arith.py`.
- `evals/tests/fixtures/cases/test-case-001/test_case_001/arith.py` (Create) — buggy `add(a, b) -> a - b`.
- `evals/tests/fixtures/cases/test-case-001/tests/test_arith.py` (Create) — visible test: `assert add(2, 3) == 5`.
- `evals/tests/fixtures/cases/test-case-001/tests/test_arith_extended.py` (Create) — hidden test: `assert add(0, 0) == 0; assert add(-1, 1) == 0`.
- `evals/tests/fixtures/cases/test-case-001/uv.lock` (Create) — generated by running `uv lock` in the fixture; small, captured into git so workspace tests have a deterministic lock-hash.
- `evals/tests/fixtures/cases/test-case-001.json` (Create) — synthetic case manifest pointing at the synthetic fixture.
- `evals/tests/fixtures/cases/test-case-001.failure_output.txt` (Create) — captured failure trace from running the visible test against the buggy fixture.

#### Unit tests (under `evals/tests/`)

- `evals/tests/discovery_test.py` (Create)
- `evals/tests/schemas_test.py` (Create)
- `evals/tests/env_test.py` (Create)
- `evals/tests/workspace_test.py` (Create)
- `evals/tests/setup_test.py` (Create)
- `evals/tests/runner_test.py` (Create)
- `evals/tests/pipeline_test.py` (Create)
- `evals/tests/campaign_test.py` (Create)
- `evals/tests/status_test.py` (Create)
- `evals/tests/report_test.py` (Create)
- `evals/tests/resume_test.py` (Create)

#### Integration tests

- `evals/tests/integration/__init__.py` (Create)
- `evals/tests/integration/test_fake_framework.py` (Create) — one parametrized test per `FAKE_BEHAVIOR` value, asserting `meta.json` shape and `error_reason`.
- `evals/tests/integration/test_eval_all_stub.py` (Create) — `just eval-all` end-to-end with stub framework scripts: every cell ends up `nonzero_exit`.

### Modified

- `evals/pyproject.toml` (Modify) — add `pathspec`, `python-dotenv` to `dependencies`; add `[dependency-groups] dev = ["pytest>=8"]`; add `[tool.pytest.ini_options] markers = ["integration: end-to-end with uv/network"]; testpaths = ["tests"]`.
- `evals/README.md` (Modify) — replace the TODO layout with the v3 module list + a one-paragraph "how to run" section listing each `just` verb.
- `.gitignore` (Modify) — add `.runs-cache/` and `runs/`. Remove `results/` (not used by the new harness; was leftover scaffolding per spec line 518).
- `justfile` (Modify) — replace the existing `eval-all` / `eval framework` / `frameworks` recipes with the full v3 verb set: `frameworks`, `cases`, `eval-prepare`, `eval-new`, `eval-all`, `eval`, `eval-status`, `eval-report`, `eval-clean-cache`, `eval-clean-runs`. Each delegates to `cd evals && uv run python -m evals <verb> <args>`.
- `frameworks/deepagents/README.md` (Modify) — append a one-line note that `manifest.json` and `run.sh` are stubs for v1 (real adapter is follow-on work). Same edit applied to each of the 8 framework READMEs.

---

## Tasks

### Task 1 — Project scaffolding: deps, gitignore, package init

**Files:**
- Modify: `evals/pyproject.toml`
- Modify: `.gitignore`
- Create: `evals/evals/__init__.py`
- Create: `evals/tests/__init__.py`

**Steps:**
- [ ] **Step 1: Update `evals/pyproject.toml`.** Replace the `[project]` table to add runtime deps (`pathspec>=0.12`, `python-dotenv>=1.0`), add `[dependency-groups] dev = ["pytest>=8"]`, and add `[tool.pytest.ini_options]` with `markers = ["integration: requires uv/network; slower"]` and `testpaths = ["tests"]`. Final content:

  ```toml
  [project]
  name = "evals"
  version = "0.0.0"
  description = "Framework-agnostic eval harness for the agent shootout"
  requires-python = ">=3.11"
  dependencies = [
    "pathspec>=0.12",
    "python-dotenv>=1.0",
  ]

  [dependency-groups]
  dev = ["pytest>=8"]

  [build-system]
  requires = ["hatchling"]
  build-backend = "hatchling.build"

  [tool.hatch.build.targets.wheel]
  packages = ["evals"]

  [tool.pytest.ini_options]
  markers = ["integration: end-to-end with uv/network; slower"]
  testpaths = ["tests"]
  ```

- [ ] **Step 2: Resolve dev deps** by running `cd evals && uv sync`. Confirm the command exits 0 and creates `evals/uv.lock` and `evals/.venv/`.
- [ ] **Step 3: Update `.gitignore`.** Insert two new lines `.runs-cache/` and `runs/` under the `# Eval output` comment. Remove the `results/` line (not used by the new harness; per spec line 518).
- [ ] **Step 4: Create the empty package marker** `evals/evals/__init__.py` (zero bytes).
- [ ] **Step 5: Create the empty tests marker** `evals/tests/__init__.py` (zero bytes).
- [ ] **Step 6: Confirm `python -m evals` is invokable** by running `cd evals && uv run python -c "import evals; print('ok')"`. It should print `ok`. (At this point no `__main__.py` exists yet — that lands in Task 13. This step only verifies the import works.)
- [ ] **Step 7: Commit.** `git add evals/pyproject.toml evals/uv.lock evals/evals/__init__.py evals/tests/__init__.py .gitignore && git commit -m "evals: project scaffolding (deps, gitignore, package init)"`.

**Acceptance criteria:**

- `evals/pyproject.toml` lists `pathspec` and `python-dotenv` as runtime deps and `pytest>=8` in `dependency-groups.dev`.
  Verify: `grep -nE "pathspec|python-dotenv|pytest" evals/pyproject.toml` returns at least three lines, and `uv sync` in the `evals/` dir exits 0.
- `.gitignore` contains `.runs-cache/` and `runs/` lines and no longer contains `results/`.
  Verify: `grep -nE "^\.runs-cache/|^runs/" .gitignore` returns exactly two matches, and `grep -n "^results/" .gitignore` returns zero matches.
- The `evals` package is importable.
  Verify: run `cd evals && uv run python -c "import evals; print('ok')"` and confirm stdout is exactly `ok\n` and exit code is 0.

**Model recommendation:** cheap

---

### Task 2 — Synthetic test fixtures: fake-framework + test-case-001

**Files:**
- Create: `evals/tests/conftest.py`
- Create: `evals/tests/fixtures/fake-framework/manifest.json`
- Create: `evals/tests/fixtures/fake-framework/run.py`
- Create: `evals/tests/fixtures/cases/test-case-001/pyproject.toml`
- Create: `evals/tests/fixtures/cases/test-case-001/test_case_001/__init__.py`
- Create: `evals/tests/fixtures/cases/test-case-001/test_case_001/arith.py`
- Create: `evals/tests/fixtures/cases/test-case-001/tests/test_arith.py`
- Create: `evals/tests/fixtures/cases/test-case-001/tests/test_arith_extended.py`
- Create: `evals/tests/fixtures/cases/test-case-001/uv.lock`
- Create: `evals/tests/fixtures/cases/test-case-001.json`
- Create: `evals/tests/fixtures/cases/test-case-001.failure_output.txt`

**Steps:**

- [ ] **Step 1: Write `test_case_001/arith.py`.** Content:

  ```python
  def add(a: int, b: int) -> int:
      return a - b
  ```

  Bug is intentional. The visible test will fail with `2 - 3 == -1, expected 5`.

- [ ] **Step 2: Write `test_case_001/__init__.py`.** Content:

  ```python
  from .arith import add

  __all__ = ["add"]
  ```

- [ ] **Step 3: Write the visible test.** `tests/test_arith.py`:

  ```python
  from test_case_001 import add


  def test_add_positive():
      assert add(2, 3) == 5
  ```

- [ ] **Step 4: Write the hidden test.** `tests/test_arith_extended.py`:

  ```python
  from test_case_001 import add


  def test_add_zero():
      assert add(0, 0) == 0


  def test_add_negative_cancels():
      assert add(-1, 1) == 0
  ```

  Bug pattern: with the buggy `a - b` the hidden test `add(-1, 1) == 0` fails (`-1 - 1 == -2`); a misguided hot-fix that hardcoded `add(2, 3) -> 5` would also fail this hidden test.

- [ ] **Step 5: Write the synthetic fixture's `pyproject.toml`.**

  ```toml
  [project]
  name = "test-case-001"
  version = "0.0.0"
  requires-python = ">=3.11"
  dependencies = []

  [dependency-groups]
  dev = ["pytest>=8"]

  [build-system]
  requires = ["hatchling"]
  build-backend = "hatchling.build"

  [tool.hatch.build.targets.wheel]
  packages = ["test_case_001"]
  ```

- [ ] **Step 6: Generate the lockfile** by running `cd evals/tests/fixtures/cases/test-case-001 && uv lock`. Confirm `uv.lock` is created.

- [ ] **Step 7: Capture the visible failure output.** From the repo root:

  ```bash
  ( cd evals/tests/fixtures/cases/test-case-001 && uv run pytest -q tests/test_arith.py ) > evals/tests/fixtures/cases/test-case-001.failure_output.txt 2>&1 ; true
  ```

  Confirm `grep -c "test_add_positive" evals/tests/fixtures/cases/test-case-001.failure_output.txt` returns at least 1.

- [ ] **Step 8: Write the synthetic case manifest** at `evals/tests/fixtures/cases/test-case-001.json`:

  ```json
  {
    "case_id": "test-case-001",
    "fixture_repo": "evals/tests/fixtures/cases/test-case-001",
    "failing_test_command": "uv run pytest -q tests/test_arith.py",
    "failure_output_path": "evals/tests/fixtures/cases/test-case-001.failure_output.txt",
    "hidden_test_command": "uv run pytest -q tests/test_arith_extended.py",
    "edit_constraints": {},
    "notes": "Synthetic case for harness self-tests; bug in test_case_001/arith.py."
  }
  ```

- [ ] **Step 9: Write the fake-framework manifest** at `evals/tests/fixtures/fake-framework/manifest.json`:

  ```json
  {
    "entry": "./run.py",
    "env": ["FAKE_BEHAVIOR"],
    "model": "fake"
  }
  ```

  No `setup` field.

- [ ] **Step 10: Write the fake-framework `run.py`.** It MUST be executable (`chmod +x`), have shebang `#!/usr/bin/env python3`, read the contract request from stdin, and branch on `os.environ["FAKE_BEHAVIOR"]` (default: `success-noop`). Required modes (one per `FAKE_BEHAVIOR` value):

  - `success-noop`: emit a contract-valid envelope with empty `output` (all fields populated, no edits, schema-valid), exit 0.
  - `success-fix`: open `<input.repo_path>/test_case_001/arith.py`, replace `return a - b` with `return a + b`, then emit a valid envelope listing `test_case_001/arith.py` in `output.changed_files`, exit 0.
  - `hang`: `time.sleep(10**9)` — never returns. Used to test the watchdog.
  - `crash`: emit unstructured stderr "boom" and `sys.exit(1)`.
  - `crash-with-error-envelope`: emit a contract-valid envelope with `error: {"message": "..."}` and `output: null` to stdout, then `sys.exit(1)`.
  - `crash-with-bad-json`: emit `{"task_id": "x"}` (parseable JSON, envelope-INvalid) to stdout, `sys.exit(1)`.
  - `garbage`: write `not-json-at-all` to stdout, exit 0.
  - `empty`: exit 0 without writing anything to stdout.
  - `oversize`: write 9 MiB of `'a'` to stdout (exceeds 8 MiB cap), exit 0.
  - `missing-field`: emit a JSON object missing the required `trace` key.
  - `forbidden-field`: emit a contract-valid envelope, but `output` contains a top-level `fixed: true` (forbidden by `task-spec.md`).
  - `disallowed-edit`: edit `<repo_path>/tests/test_arith.py` (default disallowed), then emit a valid envelope.
  - `over-max-files`: edit 6+ files in `<repo_path>/test_case_001/` (over default `max_changed_files = 5`).
  - `noisy-stderr`: emit > 6 MiB of `'X'` on stderr (over the 5 MiB cap), exit 0 with a valid envelope.
  - `mutate-venv`: write a marker file to `<UV_PROJECT_ENVIRONMENT>/lib/.../site-packages/__fake_marker__.dist-info/METADATA` so the venv fingerprint changes; emit a valid envelope, exit 0.
  - `noisy-test-output`: success-fix, but ALSO write a `tests/conftest.py` that prints > 6 MiB of `'Y'` on stdout per test.

  **Critical:** the script must NOT call `sys.exit()` before writing stdout for the `crash-with-error-envelope` mode, or `oversize` (need to write the full payload before exiting).

- [ ] **Step 11: Make `run.py` executable** with `chmod +x evals/tests/fixtures/fake-framework/run.py`.

- [ ] **Step 12: Sanity-check the synthetic fixture's bug** by running `cd evals/tests/fixtures/cases/test-case-001 && uv run pytest -q tests/`. Confirm exit non-zero and output contains `1 failed, 0 passed` for `test_arith.py` and `1 failed, 1 passed` for `test_arith_extended.py` (overall: 2 failed, 1 passed).

- [ ] **Step 13: Sanity-check the fake-framework `success-noop`.** Run:

  ```bash
  echo '{"task_id":"x","input":{},"config":{"model":"fake","max_steps":1,"timeout_s":1}}' | FAKE_BEHAVIOR=success-noop evals/tests/fixtures/fake-framework/run.py
  ```

  Confirm exit 0 and stdout starts with `{"task_id":"x"`.

- [ ] **Step 14: Write `evals/tests/conftest.py`** with shared pytest fixtures:

  ```python
  from pathlib import Path
  import pytest


  @pytest.fixture
  def repo_root() -> Path:
      return Path(__file__).resolve().parents[2]


  @pytest.fixture
  def fixtures_dir(repo_root: Path) -> Path:
      return repo_root / "evals" / "tests" / "fixtures"


  @pytest.fixture
  def tmp_repo_root(tmp_path: Path) -> Path:
      repo = tmp_path / "repo"
      repo.mkdir()
      return repo


  @pytest.fixture
  def synthetic_case_manifest(fixtures_dir: Path) -> Path:
      return fixtures_dir / "cases" / "test-case-001.json"


  @pytest.fixture
  def synthetic_case_dir(fixtures_dir: Path) -> Path:
      return fixtures_dir / "cases" / "test-case-001"


  @pytest.fixture
  def fake_framework_dir(fixtures_dir: Path) -> Path:
      return fixtures_dir / "fake-framework"
  ```

- [ ] **Step 15: Commit.** `git add evals/tests/conftest.py evals/tests/fixtures/ && git commit -m "evals/tests: synthetic case + fake-framework fixtures"`.

**Acceptance criteria:**

- The synthetic fixture demonstrably has the buggy state: visible test fails, hidden tests one-pass / one-fail.
  Verify: run `cd evals/tests/fixtures/cases/test-case-001 && uv run pytest -q tests/` and confirm exit code is non-zero and stdout contains the substring `2 failed`.
- The synthetic case manifest references the synthetic fixture and a captured `failure_output_path`.
  Verify: `cd evals/tests/fixtures/cases && python -c "import json; m=json.load(open('test-case-001.json')); assert m['case_id']=='test-case-001'; assert m['fixture_repo'].endswith('test-case-001'); assert m['failure_output_path'].endswith('.failure_output.txt')"` exits 0.
- The fake-framework `run.py` is executable, reads stdin, and emits a valid envelope under `success-noop`.
  Verify: run `echo '{"task_id":"x","input":{},"config":{"model":"fake","max_steps":1,"timeout_s":1}}' | FAKE_BEHAVIOR=success-noop evals/tests/fixtures/fake-framework/run.py | python -c "import json,sys; d=json.load(sys.stdin); assert d['task_id']=='x'; print('ok')"` and confirm stdout ends with `ok` and exit 0.
- The fake-framework manifest declares `entry`, `env`, and `model` and has no `setup`.
  Verify: `python -c "import json; m=json.load(open('evals/tests/fixtures/fake-framework/manifest.json')); assert set(m.keys())=={'entry','env','model'}; assert m['entry']=='./run.py'"` exits 0.

**Model recommendation:** standard

---

### Task 3 — `schemas.py`: dict schemas + bespoke validators

**Files:**
- Create: `evals/evals/schemas.py`
- Create: `evals/tests/schemas_test.py`

**Steps:**

- [ ] **Step 1: Write `schemas.py` skeleton.** Define module-level constants:

  - `FORBIDDEN_OUTPUT_KEYS = {"fixed", "not_fixed", "status"}`
  - `FRAMEWORK_MANIFEST_REQUIRED = {"entry", "env", "model"}`
  - `FRAMEWORK_MANIFEST_OPTIONAL = {"setup"}`
  - `CASE_REQUIRED = {"case_id", "fixture_repo", "failing_test_command"}`
  - `CASE_OPTIONAL = {"failure_output", "failure_output_path", "hidden_test_command", "edit_constraints", "notes"}`
  - `ENVELOPE_REQUIRED = {"task_id", "output", "trace", "error"}`
  - `TRACE_REQUIRED = {"steps", "tokens", "latency_ms"}`
  - `OUTPUT_REQUIRED = {"root_cause", "summary", "changed_files", "tests_run", "evidence", "confidence"}`

- [ ] **Step 2: Implement `validate_framework_manifest(obj: object) -> list[str]`.** Returns a list of human-readable error messages (empty list = valid). Checks:
  - `obj` is a dict.
  - All `FRAMEWORK_MANIFEST_REQUIRED` keys present.
  - No keys outside `REQUIRED ∪ OPTIONAL`.
  - `entry` is a non-empty string.
  - `setup` is absent or a non-empty string.
  - `env` is a list of strings.
  - `model` is a non-empty string.

- [ ] **Step 3: Implement `validate_case_manifest(obj: object) -> list[str]`.** Checks:
  - dict shape; required keys present.
  - `case_id` matches `^[a-zA-Z0-9_.\-/]+$`.
  - `fixture_repo` non-empty string.
  - `failing_test_command` non-empty string.
  - **Exactly one** of `failure_output` and `failure_output_path` present (per `task-spec.md` line 18 / spec rule).
  - `edit_constraints`, when present, is a dict; `disallowed_paths` and `allowed_paths` (if present) are lists of strings; `max_changed_files` (if present) is a non-negative int.
  - `hidden_test_command` (if present) is a non-empty string.

- [ ] **Step 4: Implement `validate_envelope(obj: object) -> list[str]`.** Checks:
  - dict; required keys present.
  - `task_id` non-empty string.
  - `trace` is a dict with `TRACE_REQUIRED` keys; `tokens` is a dict with `input` and `output` int fields; `steps` is a list; `latency_ms` is a non-negative int.
  - `error` is `null` or a dict with at least `message: str`.
  - `output` is `null` or a dict (envelope-level validation does not check inner output schema; that's `validate_agent_output`'s job).

- [ ] **Step 5: Implement `validate_agent_output(obj: object) -> list[str]`.** Checks:
  - dict; required keys present.
  - **No top-level `FORBIDDEN_OUTPUT_KEYS` present** — explicit rejection per `task-spec.md` line 111.
  - `root_cause`, `summary`, `evidence` are strings.
  - `changed_files` is a list of strings.
  - `tests_run` is a list of dicts each with `command: str`, `exit_code: int`, `summary: str`.
  - `confidence` is a number in `[0.0, 1.0]`.

- [ ] **Step 6: Write `schemas_test.py`.** Cases (one test function each):
  - `test_valid_framework_manifest_passes` — minimal valid manifest returns `[]`.
  - `test_framework_manifest_missing_entry_fails` — missing `entry` produces an error mentioning `entry`.
  - `test_framework_manifest_extra_key_fails` — unknown key `foo` produces an error mentioning `foo`.
  - `test_valid_case_manifest_passes` — manifest with `failure_output_path` set, no `failure_output`, returns `[]`.
  - `test_case_manifest_both_failure_outputs_fails` — both `failure_output` and `failure_output_path` set produces an error.
  - `test_case_manifest_neither_failure_output_fails` — neither present produces an error.
  - `test_valid_envelope_passes` — minimal valid envelope returns `[]`.
  - `test_envelope_missing_trace_fails` — missing `trace` is reported.
  - `test_agent_output_with_fixed_key_fails` — output dict containing `fixed: true` produces an error mentioning `fixed`.
  - `test_agent_output_with_status_fails` — output containing `status` produces an error mentioning `status`.
  - `test_agent_output_confidence_out_of_range_fails` — `confidence: 1.5` is rejected.

- [ ] **Step 7: Run the tests** with `cd evals && uv run pytest -q tests/schemas_test.py`. All 11 must pass.

- [ ] **Step 8: Commit.** `git add evals/evals/schemas.py evals/tests/schemas_test.py && git commit -m "evals/schemas: framework/case/envelope/output validators"`.

**Acceptance criteria:**

- All schema validators are present and exported from `schemas.py`.
  Verify: `cd evals && uv run python -c "from evals.schemas import validate_framework_manifest, validate_case_manifest, validate_envelope, validate_agent_output; print('ok')"` exits 0 and prints `ok`.
- Forbidden output keys (`fixed`, `not_fixed`, `status`) are rejected with explicit error messages.
  Verify: `cd evals && uv run python -c "from evals.schemas import validate_agent_output; errs=validate_agent_output({'root_cause':'x','summary':'x','changed_files':[],'tests_run':[],'evidence':'x','confidence':0.5,'fixed':True}); assert any('fixed' in e for e in errs); print('ok')"` exits 0 and prints `ok`.
- Case manifest's `failure_output` xor `failure_output_path` rule is enforced.
  Verify: `cd evals && uv run python -c "from evals.schemas import validate_case_manifest as v; assert v({'case_id':'a','fixture_repo':'x','failing_test_command':'y','failure_output':'z','failure_output_path':'w'})!=[]; assert v({'case_id':'a','fixture_repo':'x','failing_test_command':'y'})!=[]; print('ok')"` exits 0 and prints `ok`.
- The schema unit suite passes.
  Verify: run `cd evals && uv run pytest -q tests/schemas_test.py` and confirm exit 0 and the line `11 passed` appears in stdout.

**Model recommendation:** cheap

---

### Task 4 — `discovery.py`: framework + case discovery

**Files:**
- Create: `evals/evals/discovery.py`
- Create: `evals/tests/discovery_test.py`

**Steps:**

- [ ] **Step 1: Write the dataclasses.**

  ```python
  from dataclasses import dataclass
  from pathlib import Path

  @dataclass(frozen=True)
  class FrameworkSpec:
      name: str
      dir: Path
      manifest_path: Path
      entry: str
      setup: str | None
      env_keys: list[str]
      model: str

  @dataclass(frozen=True)
  class CaseSpec:
      case_id: str
      manifest_path: Path
      fixture_repo: Path  # absolute
      failing_test_command: str
      hidden_test_command: str | None
      failure_output: str  # always resolved (file → string)
      edit_constraints: dict
      notes: str | None

  @dataclass(frozen=True)
  class DiscoveryError:
      kind: str  # "framework" | "case"
      name: str
      manifest_path: Path
      messages: list[str]
  ```

- [ ] **Step 2: Implement `discover_frameworks(repo_root: Path) -> tuple[list[FrameworkSpec], list[DiscoveryError]]`.** Iterate `repo_root / "frameworks" / "*"` directories that contain `manifest.json`. Frameworks with no manifest are silently skipped (the framework dir may be README-only). Frameworks with a malformed manifest produce a `DiscoveryError`. Sort results alphabetically by `name`.

- [ ] **Step 3: Implement `discover_cases(repo_root: Path) -> tuple[list[CaseSpec], list[DiscoveryError]]`.** Iterate `repo_root / "cases" / "*.json"`, validate each, resolve `failure_output` (from inline or sidecar). Sort by `case_id`. Errors collected.

- [ ] **Step 4: Write `discovery_test.py`** with:
  - `test_discover_frameworks_returns_fake(tmp_repo_root)` — write a fake `frameworks/x/manifest.json` and confirm it shows up.
  - `test_discover_frameworks_skips_readme_only_dir` — a `frameworks/y/` with only `README.md` is silently skipped.
  - `test_discover_frameworks_reports_malformed` — a `frameworks/z/manifest.json` with invalid JSON or missing `entry` returns a `DiscoveryError`.
  - `test_discover_cases_resolves_inline_failure_output` — case manifest with `failure_output: "foo"` returns `failure_output == "foo"`.
  - `test_discover_cases_resolves_sidecar_failure_output_path` — case manifest with `failure_output_path: "rel/path.txt"` returns the file's contents.
  - `test_discover_cases_rejects_both_failure_output_forms` — both forms set yields `DiscoveryError`.
  - `test_discover_cases_handles_missing_executable_entry` — framework `entry: "./run.sh"` where `run.sh` does not exist or is not executable produces a `DiscoveryError` (or `FrameworkSpec` with the entry preserved — pick: produce a `FrameworkSpec` and let the runner classify as `framework_misconfigured` at run time, since exec-failure is best detected by the actual exec attempt; the test asserts that a non-executable entry produces a spec rather than a discovery error).

  Build an `_isolated_repo_root` helper that creates a tmpdir with the required structure for these tests so they don't depend on the real repo's `frameworks/` and `cases/` dirs.

- [ ] **Step 5: Run the tests.** `cd evals && uv run pytest -q tests/discovery_test.py`. All must pass.

- [ ] **Step 6: Commit.** `git add evals/evals/discovery.py evals/tests/discovery_test.py && git commit -m "evals/discovery: framework + case discovery with structured errors"`.

**Acceptance criteria:**

- `discover_frameworks` and `discover_cases` are exported and return `(specs, errors)` tuples.
  Verify: `cd evals && uv run python -c "from evals.discovery import discover_frameworks, discover_cases, FrameworkSpec, CaseSpec, DiscoveryError; print('ok')"` exits 0 and prints `ok`.
- Discovery surfaces malformed manifests as `DiscoveryError` rather than raising.
  Verify: open `evals/tests/discovery_test.py` and confirm the test `test_discover_frameworks_reports_malformed` exists and asserts `len(errors) > 0` for an invalid manifest, then run `cd evals && uv run pytest -q tests/discovery_test.py::test_discover_frameworks_reports_malformed` and confirm exit 0 with `1 passed`.
- The discovery unit suite passes.
  Verify: run `cd evals && uv run pytest -q tests/discovery_test.py` and confirm exit 0 with at least 7 tests passing.

**Model recommendation:** standard

---

### Task 5 — `env.py`: dotenv loading + agent_env / test_env builders

**Files:**
- Create: `evals/evals/env.py`
- Create: `evals/tests/env_test.py`

**Steps:**

- [ ] **Step 1: Implement the loader.**

  ```python
  from pathlib import Path
  from dotenv import dotenv_values

  def load_dotenv(repo_root: Path) -> dict[str, str]:
      env_file = repo_root / ".env"
      if not env_file.exists():
          return {}
      raw = dotenv_values(env_file)
      return {k: v for k, v in raw.items() if v is not None}
  ```

- [ ] **Step 2: Implement `build_agent_env`.**

  ```python
  BASE_KEYS = ("HOME", "LANG", "TERM")  # PATH handled separately

  def build_agent_env(
      *,
      declared_keys: list[str],
      case_venv_path: Path | None,
      base_env: dict[str, str],
      dotenv: dict[str, str],
  ) -> dict[str, str]:
      out: dict[str, str] = {}
      for k in BASE_KEYS:
          if k in base_env:
              out[k] = base_env[k]
      out["PATH"] = _build_path(case_venv_path, base_env.get("PATH", ""))
      if case_venv_path is not None:
          out["UV_PROJECT_ENVIRONMENT"] = str(case_venv_path.resolve())
      merged_secrets = {**base_env, **dotenv}
      for k in declared_keys:
          if k in merged_secrets:
              out[k] = merged_secrets[k]
      return out
  ```

  `_build_path(venv, inherited_path)`:
  - If `venv is None`: return inherited_path.
  - Else: return `f"{venv.resolve()}/bin:{inherited_path}"`.
  - Note: per spec line 175, "PATH minus user-local additions" is aspirational — v1 just prepends the venv `bin` and inherits the rest verbatim; document that in a one-line comment.

- [ ] **Step 3: Implement `build_test_env`.**

  ```python
  def build_test_env(
      *,
      case_venv_path: Path,
      base_env: dict[str, str],
  ) -> dict[str, str]:
      out: dict[str, str] = {}
      for k in BASE_KEYS:
          if k in base_env:
              out[k] = base_env[k]
      out["PATH"] = _build_path(case_venv_path, base_env.get("PATH", ""))
      out["UV_PROJECT_ENVIRONMENT"] = str(case_venv_path.resolve())
      # No declared framework keys — test reruns are deterministic and never see secrets.
      return out
  ```

- [ ] **Step 4: Implement `build_setup_env`.** Per spec lines 175-176: setup commands run with `agent_env` minus `UV_PROJECT_ENVIRONMENT` (no per-case venv applies during setup) and PATH is base-inherited (no venv prepend).

  ```python
  def build_setup_env(
      *,
      declared_keys: list[str],
      base_env: dict[str, str],
      dotenv: dict[str, str],
  ) -> dict[str, str]:
      out: dict[str, str] = {}
      for k in BASE_KEYS:
          if k in base_env:
              out[k] = base_env[k]
      out["PATH"] = base_env.get("PATH", "")
      merged = {**base_env, **dotenv}
      for k in declared_keys:
          if k in merged:
              out[k] = merged[k]
      return out
  ```

- [ ] **Step 5: Write `env_test.py`.** Tests:
  - `test_load_dotenv_missing_returns_empty(tmp_path)` — no `.env` → `{}`.
  - `test_load_dotenv_parses_pairs(tmp_path)` — write a `.env` with `K=V` and confirm result is `{"K":"V"}`.
  - `test_agent_env_includes_declared_keys` — `declared_keys=["FOO"]`, `base_env={"FOO":"bar","PATH":"/usr/bin","HOME":"/root"}` → `agent_env["FOO"] == "bar"`.
  - `test_agent_env_path_prepends_venv_bin` — venv=`/tmp/v` → `PATH.startswith("/tmp/v/bin:")`.
  - `test_agent_env_excludes_undeclared_keys` — `base_env={"SECRET":"x"}` not declared → `"SECRET" not in agent_env`.
  - `test_agent_env_dotenv_overrides_base_for_declared` — declared `K`, base `K=base`, dotenv `K=dot` → `agent_env["K"] == "dot"`.
  - `test_test_env_excludes_framework_keys` — declared `["FOO"]` provided to `agent_env` returns FOO; `build_test_env` does not see them and never returns `FOO`.
  - `test_test_env_path_prepends_venv_bin` — same PATH-prepending behavior.
  - `test_test_env_includes_uv_project_environment` — `UV_PROJECT_ENVIRONMENT == str(venv.resolve())`.
  - `test_setup_env_does_not_include_uv_project_environment` — `"UV_PROJECT_ENVIRONMENT" not in build_setup_env(...)`.
  - `test_setup_env_path_does_not_prepend_venv` — PATH does not start with any venv bin.

- [ ] **Step 6: Run** `cd evals && uv run pytest -q tests/env_test.py`. All pass.

- [ ] **Step 7: Commit.** `git add evals/evals/env.py evals/tests/env_test.py && git commit -m "evals/env: agent_env / test_env / setup_env builders"`.

**Acceptance criteria:**

- `agent_env` and `test_env` builders prepend `<venv>/bin` to PATH and `test_env` excludes declared framework keys.
  Verify: `cd evals && uv run pytest -q tests/env_test.py::test_test_env_excludes_framework_keys tests/env_test.py::test_agent_env_path_prepends_venv_bin tests/env_test.py::test_test_env_path_prepends_venv_bin` exits 0 with all three passing.
- `build_setup_env` returns no `UV_PROJECT_ENVIRONMENT` and does not prepend the venv to PATH (per spec line 176).
  Verify: run `cd evals && uv run pytest -q tests/env_test.py::test_setup_env_does_not_include_uv_project_environment tests/env_test.py::test_setup_env_path_does_not_prepend_venv` and confirm exit 0 with both passing.
- Dotenv loading is robust to a missing file.
  Verify: run `cd evals && uv run pytest -q tests/env_test.py::test_load_dotenv_missing_returns_empty` and confirm exit 0 with 1 passed.

**Model recommendation:** standard

---

### Task 6 — `workspace.py`: layers 1, 2, 3 + hashes + venv fingerprint

**Files:**
- Create: `evals/evals/workspace.py`
- Create: `evals/tests/workspace_test.py`

**Steps:**

- [ ] **Step 1: Implement BLAKE2 helpers.**

  ```python
  import hashlib
  from pathlib import Path

  def _blake2_hex(data: bytes) -> str:
      return hashlib.blake2b(data, digest_size=16).hexdigest()
  ```

- [ ] **Step 2: Implement `compute_fixture_hash(repo_root: Path, case_id: str) -> str`.**
  - Use `git -C <repo_root> ls-files -z fixtures/<case_id>/` to enumerate tracked files (subprocess call). This is the cleanest filter for ".venv/", "__pycache__/", etc. — by definition, those are not tracked.
  - For each tracked file, append `<rel-path>\0<sha256(file-bytes)>\n` to a sorted byte buffer (sort by rel-path).
  - Return BLAKE2-16 hex of the buffer.
  - If `git ls-files` returns nothing (case with no fixture files? shouldn't happen for valid cases) raise a `WorkspaceError`.

- [ ] **Step 3: Implement `compute_lock_hash(fixture_dir: Path) -> str`.**
  - Hash bytes of `fixture_dir/uv.lock` if present; otherwise hash bytes of `fixture_dir/pyproject.toml`.
  - Return BLAKE2-16 hex.

- [ ] **Step 4: Implement `compute_venv_fingerprint(venv_dir: Path) -> str`.**
  - Glob `<venv>/lib/python*/site-packages/*.dist-info/` directory names.
  - Sort the list.
  - Hash the joined `\n`-separated names with BLAKE2-16.
  - Empty list → fingerprint of empty string. (Used for the `venv_hash_before` / `venv_hash_after` in `meta.json`.)

- [ ] **Step 5: Implement `ensure_case_bare_repo(repo_root: Path, case_id: str, cache_dir: Path) -> Path`.**
  - Compute `fixture_hash`. If `cache_dir/<case_id>.fixture-hash` exists and matches, return `cache_dir/<case_id>.git/` (no rebuild).
  - Otherwise: rmtree any existing `<case_id>.git/` and `<case_id>.fixture-hash`. Build fresh:
    1. Make a tempdir alongside `<case_id>.git/` (in cache_dir).
    2. Inside, create `bare/` and run `git init --bare bare/`.
    3. Create `work/`. For each tracked file from `git -C <repo_root> ls-files fixtures/<case_id>`, copy `<repo_root>/<rel-path>` (preserving file mode) into `work/<path-relative-to-fixture>`.
    4. In `work/`: `git init`, `git add -A`, set author env vars (`GIT_AUTHOR_NAME=harness`, `GIT_AUTHOR_EMAIL=harness@local`, ditto committer) and `git commit -m "fixture: <case_id> @ <fixture_hash>"`, then `git push <bare>/ HEAD:refs/heads/main`.
    5. Atomically `mv tempdir/bare <cache_dir>/<case_id>.git`.
    6. Write `<cache_dir>/<case_id>.fixture-hash` containing the hash.
    7. Discard the tempdir.
  - Return `<cache_dir>/<case_id>.git`.

- [ ] **Step 6: Implement `ensure_case_venv(repo_root: Path, case_id: str, fixture_dir: Path, cache_dir: Path) -> Path`.**
  - Compute `lock_hash`. If `<cache_dir>/<case_id>.lock-hash` exists and matches, return `<cache_dir>/<case_id>.venv/` (no rebuild).
  - Otherwise: rmtree any existing `<case_id>.venv/` and `<case_id>.lock-hash`.
  - Run `uv sync --no-install-project` with cwd=`fixture_dir` and env including `UV_PROJECT_ENVIRONMENT=<absolute path to <cache_dir>/<case_id>.venv/>`. If `uv.lock` exists, also pass `--frozen`.
  - On success, write `<case_id>.lock-hash` with the hash.
  - On failure, raise `WorkspaceError("uv sync failed", stderr=...)`.
  - Return `<cache_dir>/<case_id>.venv`.

- [ ] **Step 7: Implement `clone_cell_worktree(bare_repo: Path, dest: Path) -> Path`.**
  - If `dest` exists, rmtree it first (the cell is being rerun).
  - Run `git clone --local <bare_repo> <dest>`.
  - Return `dest`.

- [ ] **Step 8: Implement `wipe_cell_dir(cell_dir: Path) -> None`.** rmtree if exists. Used by the runner on cell rerun.

- [ ] **Step 9: Write `workspace_test.py`** with:
  - `test_compute_fixture_hash_changes_when_file_changes(tmp_repo_root)` — set up a tmp git repo with a fixture; compute hash; modify a file; recompute; assert different.
  - `test_compute_fixture_hash_excludes_untracked(tmp_repo_root)` — drop a `.venv/foo` file (not tracked) into the fixture; hash unchanged.
  - `test_compute_lock_hash_uses_uv_lock_when_present(tmp_path)` — both lock + pyproject; hash uses lock contents.
  - `test_compute_lock_hash_falls_back_to_pyproject(tmp_path)` — only pyproject; hash uses pyproject contents.
  - `test_compute_venv_fingerprint_stable(tmp_path)` — empty venv dir → fixed value; same call twice → same value.
  - `test_compute_venv_fingerprint_changes_when_distinfo_added(tmp_path)` — create `lib/python3.12/site-packages/foo.dist-info`; fingerprint differs from before.
  - `test_ensure_case_bare_repo_reuses_when_hash_matches(tmp_repo_root)` — call twice; second call must not rebuild (capture mtime of `.git/HEAD`).
  - `test_ensure_case_bare_repo_rebuilds_when_hash_changes(tmp_repo_root)` — modify a tracked file between calls; second call rebuilds.
  - `test_clone_cell_worktree_creates_independent_repo(tmp_path)` — after clone, `git -C dest log` shows the fixture commit.
  - `test_clone_cell_worktree_overwrites_existing(tmp_path)` — `dest` pre-exists with garbage; clone wipes it.
  - **integration-marked** `test_ensure_case_venv_no_install_project(tmp_repo_root)` — build venv from a tiny synthetic fixture (the `test-case-001` fixture from Task 2). Assert `<venv>/lib/python*/site-packages/test_case_001` does NOT exist (project not installed). Assert `<venv>/lib/python*/site-packages/pytest` DOES exist (dependency installed). Marker: `@pytest.mark.integration`.

  For `tmp_repo_root` use a fresh `git init` tmpdir with a tiny fixture committed.

- [ ] **Step 10: Run unit-only tests.** `cd evals && uv run pytest -q -m "not integration" tests/workspace_test.py`. All pass.

- [ ] **Step 11: Run integration test if `uv` available.** `cd evals && uv run pytest -q -m integration tests/workspace_test.py::test_ensure_case_venv_no_install_project`. Pass.

- [ ] **Step 12: Commit.** `git add evals/evals/workspace.py evals/tests/workspace_test.py && git commit -m "evals/workspace: layers 1-3 + content/lock/venv hashes"`.

**Acceptance criteria:**

- Layer 2 venv contains case dependencies but NOT the project under test.
  Verify: run `cd evals && uv run pytest -q -m integration tests/workspace_test.py::test_ensure_case_venv_no_install_project` and confirm exit 0 with `1 passed`.
- Fixture hash excludes untracked files like `.venv/`.
  Verify: run `cd evals && uv run pytest -q tests/workspace_test.py::test_compute_fixture_hash_excludes_untracked` and confirm exit 0 with `1 passed`.
- Layer 1 bare-repo construction is idempotent on hash match.
  Verify: run `cd evals && uv run pytest -q tests/workspace_test.py::test_ensure_case_bare_repo_reuses_when_hash_matches tests/workspace_test.py::test_ensure_case_bare_repo_rebuilds_when_hash_changes` and confirm exit 0 with both passing.
- Venv fingerprint is stable for the same site-packages set and changes when a `.dist-info/` is added.
  Verify: run `cd evals && uv run pytest -q tests/workspace_test.py::test_compute_venv_fingerprint_stable tests/workspace_test.py::test_compute_venv_fingerprint_changes_when_distinfo_added` and confirm exit 0 with both passing.

**Model recommendation:** standard

---

### Task 7 — `setup.py`: framework setup runner with `.ok` / `.fail` sentinels

**Files:**
- Create: `evals/evals/setup.py`
- Create: `evals/tests/setup_test.py`

**Steps:**

- [ ] **Step 1: Define the result type.**

  ```python
  from dataclasses import dataclass

  @dataclass(frozen=True)
  class SetupResult:
      framework: str
      status: str  # "ok" | "skipped" | "failed"
      reason: str | None  # "nonzero_exit" | "timeout" | None
      exit_code: int | None
      stdout_truncated: bool
      stderr_truncated: bool
      duration_s: float
  ```

  - `skipped` means the framework declared no `setup` field — nothing to run, no sentinel written.

- [ ] **Step 2: Implement `is_setup_ok(framework_name: str, cache_dir: Path) -> bool`.** Returns True iff `<cache_dir>/setup/<framework_name>.ok` exists. (Frameworks without a `setup` field are also considered "ok" but that check is a higher layer's responsibility — see runner.)

- [ ] **Step 3: Implement `is_setup_failed(framework_name: str, cache_dir: Path) -> bool`.** Returns True iff `.fail` exists.

- [ ] **Step 4: Implement `run_framework_setup(spec: FrameworkSpec, *, cache_dir: Path, base_env: dict[str,str], dotenv: dict[str,str], timeout_s: int) -> SetupResult`.**

  1. If `spec.setup is None`, return `SetupResult(status="skipped", ...)`.
  2. Ensure `cache_dir / "setup"` exists.
  3. Delete any pre-existing `.ok` and `.fail` for this framework (so retries from scratch — per spec line 156).
  4. Build env via `env.build_setup_env(declared_keys=spec.env_keys, base_env=base_env, dotenv=dotenv)`.
  5. Parse `spec.setup` with `shlex.split`. cwd = `spec.dir`.
  6. Spawn via `subprocess.Popen` with stdout/stderr piped. Stream to `<cache_dir>/setup/<framework>.stdout.log` (5 MiB cap) and `<cache_dir>/setup/<framework>.stderr.log` (5 MiB cap). After cap, drain pipes to a sink to prevent the child from blocking. Use a helper `_pump_capped(reader, dest_path, cap_bytes) -> truncated: bool` running in two threads.
  7. Wait up to `timeout_s` seconds. On timeout: SIGTERM, wait 5 seconds, SIGKILL. Record `reason="timeout"`.
  8. On exit 0: write `.ok` (atomic temp+rename). Contents: `{"hash":"<manifest+lockfile-hash>","started_at":"...","ended_at":"..."}`. Return `status="ok"`.
  9. On non-zero or timeout: write `.fail` (atomic temp+rename). Contents: `{"reason":"...","exit_code":<int|null>,"started_at":"...","ended_at":"..."}`. Return `status="failed"`.

- [ ] **Step 5: Implement `run_all_setups(specs: list[FrameworkSpec], *, cache_dir, base_env, dotenv, timeout_s) -> list[SetupResult]`** that calls `run_framework_setup` for each, **continuing past failures** (per spec line 154). Returns the full list. Caller decides whether to exit non-zero based on aggregate.

- [ ] **Step 6: Write `setup_test.py`.** Tests:
  - `test_run_framework_setup_skipped_when_no_setup_field` — `spec.setup = None`; result is `status="skipped"`, no files in `<cache>/setup/`.
  - `test_run_framework_setup_writes_ok_on_exit_0(tmp_path)` — fake `setup="echo hi && exit 0"` (build a tiny echo script as the setup target). After run: `.ok` exists, `.fail` does not.
  - `test_run_framework_setup_writes_fail_on_exit_nonzero(tmp_path)` — setup script `exit 7`. `.fail` exists with exit_code=7, `.ok` does not.
  - `test_run_framework_setup_timeout(tmp_path)` — setup script sleeps 30s, timeout=2s. Result has `status="failed", reason="timeout"`. `.fail` exists.
  - `test_run_framework_setup_truncates_oversize_stdout(tmp_path)` — script writes 6 MiB to stdout. `<framework>.stdout.log` is exactly 5 MiB; `result.stdout_truncated` is True.
  - `test_run_framework_setup_pipe_drain_does_not_block(tmp_path)` — script writes 8 MiB to stdout then a final marker line; harness completes within timeout (would deadlock without pipe drain).
  - `test_run_framework_setup_retries_clear_prior_fail(tmp_path)` — pre-create `.fail`; run successful setup; `.fail` is removed and `.ok` exists.
  - `test_run_all_setups_continues_past_failures(tmp_path)` — two specs, first fails, second succeeds; both attempted; both results returned.
  - `test_is_setup_ok_returns_true_on_sentinel(tmp_path)` — touch `.ok`; `is_setup_ok` returns True.
  - `test_is_setup_failed_returns_true_on_fail_sentinel(tmp_path)` — touch `.fail`; `is_setup_failed` returns True.

  Build small shell scripts in tmpdirs as the `setup` target — keep them platform-portable (bash one-liners with `sh -c` or python one-liners).

- [ ] **Step 7: Run** `cd evals && uv run pytest -q tests/setup_test.py`. All pass.

- [ ] **Step 8: Commit.** `git add evals/evals/setup.py evals/tests/setup_test.py && git commit -m "evals/setup: framework setup runner with .ok/.fail sentinels"`.

**Acceptance criteria:**

- Successful setup writes `.ok` and removes any prior `.fail`; failed setup writes `.fail` and skips `.ok` (mutually exclusive).
  Verify: run `cd evals && uv run pytest -q tests/setup_test.py::test_run_framework_setup_writes_ok_on_exit_0 tests/setup_test.py::test_run_framework_setup_writes_fail_on_exit_nonzero tests/setup_test.py::test_run_framework_setup_retries_clear_prior_fail` and confirm exit 0 with all three passing.
- Setup timeout enforces SIGTERM/SIGKILL and produces `reason="timeout"`.
  Verify: run `cd evals && uv run pytest -q tests/setup_test.py::test_run_framework_setup_timeout` and confirm exit 0 with `1 passed`.
- Output capture caps at 5 MiB, sets `stdout_truncated=True`, and does not deadlock on overflow.
  Verify: run `cd evals && uv run pytest -q tests/setup_test.py::test_run_framework_setup_truncates_oversize_stdout tests/setup_test.py::test_run_framework_setup_pipe_drain_does_not_block` and confirm exit 0 with both passing.
- `run_all_setups` continues past per-framework failures.
  Verify: run `cd evals && uv run pytest -q tests/setup_test.py::test_run_all_setups_continues_past_failures` and confirm exit 0 with `1 passed`.

**Model recommendation:** standard

---

### Task 8 — `runner.py`: subprocess invocation + classification

**Files:**
- Create: `evals/evals/runner.py`
- Create: `evals/tests/runner_test.py`

**Steps:**

- [ ] **Step 1: Define result types and constants.**

  ```python
  from dataclasses import dataclass
  from pathlib import Path

  STDOUT_CAP_BYTES = 8 * 1024 * 1024  # 8 MiB
  STDERR_CAP_BYTES = 5 * 1024 * 1024  # 5 MiB
  KILL_GRACE_S = 5

  @dataclass(frozen=True)
  class RunnerResult:
      task_id: str
      exit_code: int | None  # None on timeout
      timed_out: bool
      stdout_path: Path
      stderr_path: Path
      stdout_truncated: bool
      stderr_truncated: bool
      response_path: Path | None  # Set iff envelope-valid; see write_response_if_valid
      error_reason: str | None  # per the precedence table
      latency_ms: int
      framework_misconfigured_reason: str | None  # detail for stderr.log
  ```

- [ ] **Step 2: Implement `_pump_capped(reader, dest_path, cap_bytes) -> bool`** identical to setup.py's helper. Move it into a shared internal module if duplication grows; for v1, copy is fine.

- [ ] **Step 3: Implement `_classify_error(*, exit_code, timed_out, stdout_path, envelope_errors, parse_error) -> str | None`** per the precedence table from spec lines 220-229. Highest-precedence wins:
  - `timed_out` → `"timeout"`
  - `exit_code != 0` AND `exit_code is not None` → `"nonzero_exit"`
  - `exit_code == 0` AND `stdout_size == 0` → `"missing_response"`
  - `exit_code == 0` AND (stdout truncated OR parse_error) → `"malformed_response_json"`
  - `exit_code == 0` AND parse OK AND envelope_errors → `"envelope_schema_violation"`
  - else → `None`

  Note: `framework_misconfigured` is not classified here — it's set by the caller if the manifest is invalid or setup is in a `.fail` state.

- [ ] **Step 4: Implement `_write_response_if_valid(stdout_path, response_path) -> response_path | None`.**
  - Read `stdout_path` (capped). If size > cap, return None.
  - Try `json.loads(...)`. On parse error, return None.
  - Run `validate_envelope` from `schemas`. If errors, return None.
  - Re-serialize canonically (sorted keys, indent=2) to `response_path` using atomic temp+rename. Return `response_path`.

- [ ] **Step 5: Implement `run_cell(*, framework, case, effective_config, cell_dir, cache_dir, repo_root, base_env, dotenv) -> RunnerResult`.**

  Sequence:

  1. **Pre-check framework-misconfigured at the manifest/setup level.** If `framework.entry` does not point at an existing executable file, OR `is_setup_failed(framework.name, cache_dir)` is True, build the result *without* spawning. Write `<cell_dir>/stderr.log` containing a diagnostic line like `framework_misconfigured: <reason>`. Write empty `<cell_dir>/stdout.log`. Return a `RunnerResult` with `error_reason="framework_misconfigured"`. Do NOT clone the worktree here — that's pipeline's job; runner's contract is to populate stdout/stderr and classify.
  2. Otherwise: build the request:
     ```python
     request = {
         "task_id": f"{framework.name}:{case.case_id}:{uuid4().hex[:8]}",
         "input": {
             "case_id": case.case_id,
             "repo_path": str((cell_dir / "repo").resolve()),
             "failing_test_command": case.failing_test_command,
             "failure_output": case.failure_output,
             "edit_constraints": _resolve_edit_constraints(case.edit_constraints),
         },
         "config": {
             "model": effective_config.model,
             "max_steps": effective_config.max_steps,
             "timeout_s": effective_config.timeout_s,
         },
     }
     ```
     Write `<cell_dir>/request.json` (pretty-print, sorted keys) before spawning.
  3. Build `agent_env` via `env.build_agent_env(declared_keys=framework.env_keys, case_venv_path=cache_dir/<case>.venv, base_env=base_env, dotenv=dotenv)`.
  4. Parse `framework.entry` with `shlex.split`.
  5. `subprocess.Popen(argv, cwd=framework.dir, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=agent_env)`.
  6. Write request bytes to stdin, then close.
  7. Spawn two pump threads writing to `<cell_dir>/stdout.log` (cap 8 MiB) and `<cell_dir>/stderr.log` (cap 5 MiB).
  8. Wait up to `effective_config.timeout_s`. On timeout: SIGTERM, wait 5s, SIGKILL. Record `timed_out=True`. Join pump threads.
  9. After exit/timeout: parse and validate stdout via `_write_response_if_valid`. Capture `parse_error` and `envelope_errors`.
  10. `error_reason = _classify_error(...)`.
  11. Compute `latency_ms` from start to exit.
  12. Return `RunnerResult`.

  Helper `_resolve_edit_constraints(case_constraints: dict) -> dict` merges `task-spec.md` defaults (per spec lines 28-33). Defaults:
  - `disallowed_paths`: `["tests/**", "**/*test*", "**/*fixture*", "**/*lock*", "**/CHANGELOG*", ".git/**"]`
  - `allowed_paths`: omitted (unrestricted)
  - `max_changed_files`: `5`

- [ ] **Step 6: Implement `EffectiveConfig` dataclass.**

  ```python
  @dataclass(frozen=True)
  class EffectiveConfig:
      model: str
      timeout_s: int
      max_steps: int
      sources: dict[str, str]  # field -> "framework-manifest" | "campaign" | "cell-flag" | "harness-default"
  ```

- [ ] **Step 7: Implement `resolve_effective_config(framework: FrameworkSpec, *, campaign_overrides: dict, cell_overrides: dict, harness_defaults: dict) -> EffectiveConfig`.** Per spec lines 285-289 precedence:
  1. cell flag (highest)
  2. campaign override
  3. framework manifest (for `model`)
  4. harness default (for `timeout_s=120`, `max_steps=50`)

  Each field independently. `sources` records where each came from.

- [ ] **Step 8: Write `runner_test.py`.** Use the fake-framework + a minimal in-memory framework spec. Tests:
  - `test_runner_writes_request_json_before_spawn(fake_framework_dir, tmp_path)` — start with `FAKE_BEHAVIOR=hang` in another thread; immediately assert `<cell_dir>/request.json` exists and parses.
  - `test_runner_classifies_success_noop_as_ok` — `error_reason is None`.
  - `test_runner_classifies_crash_as_nonzero_exit` — `error_reason == "nonzero_exit"`.
  - `test_runner_classifies_crash_with_error_envelope_writes_response_and_keeps_nonzero_exit` — `response.json` exists AND `error_reason == "nonzero_exit"`.
  - `test_runner_classifies_crash_with_bad_json_does_not_write_response` — `response.json` does NOT exist; `error_reason == "nonzero_exit"`.
  - `test_runner_classifies_garbage_as_malformed_response_json` — `error_reason == "malformed_response_json"`.
  - `test_runner_classifies_empty_as_missing_response` — `error_reason == "missing_response"`.
  - `test_runner_classifies_oversize_truncates_and_marks_malformed` — `stdout_truncated=True`, `error_reason="malformed_response_json"`.
  - `test_runner_classifies_missing_field_as_envelope_schema_violation` — `error_reason == "envelope_schema_violation"`.
  - `test_runner_timeout_kills_and_reports_timeout` — `FAKE_BEHAVIOR=hang`, `timeout_s=2`. `timed_out=True`, `error_reason="timeout"`.
  - `test_runner_noisy_stderr_truncates` — `stderr.log` capped at 5 MiB; `stderr_truncated=True`.
  - `test_runner_misconfigured_when_entry_missing` — pass a framework spec whose `entry` points at a non-existent script. `error_reason=="framework_misconfigured"`. `<cell_dir>/stdout.log` exists and is empty. `<cell_dir>/stderr.log` exists and contains `framework_misconfigured`.
  - `test_runner_misconfigured_when_setup_fail_exists` — pre-create `<cache_dir>/setup/<fw>.fail`; runner classifies as `framework_misconfigured` without spawning.
  - `test_resolve_effective_config_per_field_sources` — model from manifest, timeout_s from cell-flag, max_steps from harness-default → sources dict reflects each.

- [ ] **Step 9: Run** `cd evals && uv run pytest -q tests/runner_test.py`. All pass (these are slower; allow ~60s).

- [ ] **Step 10: Commit.** `git add evals/evals/runner.py evals/tests/runner_test.py && git commit -m "evals/runner: subprocess + capped streams + error precedence + per-field config"`.

**Acceptance criteria:**

- Every row of the error precedence table from spec lines 220-229 is reachable via fake-framework behaviors.
  Verify: open `evals/tests/runner_test.py` and confirm one test exists for each of `timeout`, `nonzero_exit` (incl. `crash-with-error-envelope` writes response.json sub-case), `missing_response`, `malformed_response_json` (incl. oversize sub-case), `envelope_schema_violation`, success (`error_reason is None`), and `framework_misconfigured`. Then run `cd evals && uv run pytest -q tests/runner_test.py` and confirm exit 0 with all listed test names appearing in `passed` lines.
- `response.json` is written iff the parsed stdout validates as a contract envelope, regardless of exit status.
  Verify: run `cd evals && uv run pytest -q tests/runner_test.py::test_runner_classifies_crash_with_error_envelope_writes_response_and_keeps_nonzero_exit tests/runner_test.py::test_runner_classifies_crash_with_bad_json_does_not_write_response` and confirm exit 0 with both passing.
- Per-field config provenance is recorded in `EffectiveConfig.sources`.
  Verify: run `cd evals && uv run pytest -q tests/runner_test.py::test_resolve_effective_config_per_field_sources` and confirm exit 0 with `1 passed`.
- `framework_misconfigured` is detected without subprocess spawning when the entry script is missing OR the framework's setup `.fail` sentinel exists.
  Verify: run `cd evals && uv run pytest -q tests/runner_test.py::test_runner_misconfigured_when_entry_missing tests/runner_test.py::test_runner_misconfigured_when_setup_fail_exists` and confirm exit 0 with both passing.

**Model recommendation:** capable

---

### Task 9 — `pipeline.py`: post-subprocess pipeline (steps 1-7)

**Files:**
- Create: `evals/evals/pipeline.py`
- Create: `evals/tests/pipeline_test.py`

**Steps:**

- [ ] **Step 1: Define output dataclasses.**

  ```python
  from dataclasses import dataclass

  TEST_OUTPUT_CAP_BYTES = 5 * 1024 * 1024

  @dataclass(frozen=True)
  class TestRunResult:
      command: str
      exit_code: int | None
      outcome: str  # "pass" | "fail" | "error"
      stdout_truncated: bool
      stderr_truncated: bool
      duration_ms: int
  ```

- [ ] **Step 2: Implement `_atomic_write_json(target: Path, obj) -> None`.** Write bytes to `<target>.tmp`, fsync, close, `os.rename(<target>.tmp, target)`, then best-effort fsync the parent directory (catch and ignore OSError on filesystems that don't support it).

- [ ] **Step 3: Implement Step 2: `derive_canonical_diff(cell_dir: Path) -> dict`** using a temp index per spec lines 332-342:

  ```python
  import tempfile, subprocess, os

  def derive_canonical_diff(cell_dir):
      repo = cell_dir / "repo"
      with tempfile.NamedTemporaryFile(prefix="cell-index.", delete=False) as tf:
          temp_index = tf.name
      try:
          env = os.environ.copy()
          env["GIT_INDEX_FILE"] = temp_index
          subprocess.run(["git", "-C", str(repo), "read-tree", "HEAD"], env=env, check=True)
          subprocess.run(["git", "-C", str(repo), "add", "-A"], env=env, check=True)
          patch = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "HEAD"],
                                 env=env, check=True, capture_output=True).stdout
          names = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "HEAD",
                                  "--name-only"], env=env, check=True, capture_output=True
                                ).stdout.decode().splitlines()
          numstat = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "HEAD",
                                    "--numstat"], env=env, check=True, capture_output=True
                                  ).stdout.decode().splitlines()
      finally:
          try: os.unlink(temp_index)
          except FileNotFoundError: pass
      (cell_dir / "diff.patch").write_bytes(patch)
      added = sum(int(parts[0]) for line in numstat
                  if (parts := line.split("\t")) and parts[0].isdigit())
      removed = sum(int(parts[1]) for line in numstat
                    if (parts := line.split("\t")) and len(parts) > 1 and parts[1].isdigit())
      return {"changed_files": names, "added": added, "removed": removed}
  ```

- [ ] **Step 4: Implement Step 3 + 4: `run_test_command(command: str, *, cwd: Path, env: dict, timeout_s: int) -> TestRunResult`.**
  - Spawn via `subprocess.Popen(["/bin/sh", "-c", command], cwd=cwd, stdout=PIPE, stderr=PIPE, env=env)`.
  - Stream to in-memory buffers up to 5 MiB each, drain after cap.
  - External watchdog (same SIGTERM/grace/SIGKILL pattern).
  - Outcome: `pass` (exit 0) | `fail` (exit non-zero, finite output) | `error` (timeout / signal).
  - Write `visible_test.json` / `hidden_test.json` (caller decides path) with `{command, exit_code, outcome, stdout, stderr, stdout_truncated, stderr_truncated, duration_ms}`.

- [ ] **Step 5: Implement Step 5: `check_edit_constraints(changed_files: list[str], constraints: dict) -> dict`** using `pathspec`:
  - Build a `pathspec.PathSpec.from_lines("gitwildmatch", disallowed_paths)`.
  - `disallowed_violations = [f for f in changed_files if spec.match_file(f)]`.
  - If `allowed_paths` present: `allowed_spec = ...`; `allowed_violations = [f for f in changed_files if not allowed_spec.match_file(f)]`. Else: `allowed_violations = []`.
  - `over_max_changed_files = len(changed_files) > constraints["max_changed_files"]`.
  - Return `{disallowed_violations, allowed_violations, over_max_changed_files}`.

- [ ] **Step 6: Implement Step 6: `assemble_scoring(...)`** that builds the `scoring.json` dict per spec lines 367-378.

- [ ] **Step 7: Implement Step 7: `write_meta_json(...)`** using `_atomic_write_json`. Includes `effective_config` with per-field `sources` and `venv_hash_before` / `venv_hash_after` / `venv_mutated`. Also calls `_atomic_write_json` for `scoring.json`.

- [ ] **Step 8: Implement orchestrator `run_pipeline(cell_dir: Path, runner_result: RunnerResult, *, framework, case, effective_config, cache_dir, base_env, venv_hash_before: str) -> None`.**
  Wire-through:
  1. **Schema validation** — read `<cell_dir>/stdout.log`, parse, run `validate_envelope` and `validate_agent_output`. `schema_validity = (no envelope errors) AND (no agent_output errors) AND (response present)`. Note: per spec line 329, agent-output schema violations are non-fatal — they just set `schema_validity=False` while the rest of the pipeline continues.
  2. **Diff** — derive_canonical_diff (always; even on framework_misconfigured the diff comes out empty).
  3. **Visible test rerun** — build `test_env`, run `failing_test_command` with cwd=`<cell_dir>/repo/`, write `visible_test.json`.
  4. **Hidden test rerun** — same with `hidden_test_command` if present, else outcome="n/a", no file written.
  5. **Edit constraint check** — using changed-file list from step 2.
  6. **Venv mutation check** — recompute venv fingerprint via `workspace.compute_venv_fingerprint`. `venv_mutated = (after != venv_hash_before)`. Warn to stderr if mutated.
  7. **Assemble + write `scoring.json`** atomic.
  8. **Write `meta.json` last (atomic).**

- [ ] **Step 9: Write `pipeline_test.py`.** Tests fed canned `(stdout_log, worktree, case)` tuples — they bypass the runner. Use the synthetic case fixture and the bare-repo / cell-worktree machinery from Task 6. Tests:
  - `test_diff_does_not_modify_real_index(synthetic_case_dir, tmp_path)` — clone a worktree, modify a file, run derive_canonical_diff, then assert `git -C <repo> diff --name-only` (against the actual index) returns nothing (the real index is untouched).
  - `test_visible_test_outcome_pass` — for the canonical-fix worktree (apply `add: a+b`), outcome is `"pass"`.
  - `test_visible_test_outcome_fail` — for the buggy worktree, outcome is `"fail"`.
  - `test_visible_test_outcome_error_on_timeout` — case command `sleep 30`, timeout=1 → outcome `"error"`.
  - `test_visible_test_output_caps_and_drains` — use a tiny script that writes 6 MiB to stdout; outcome captured, `stdout_truncated=True`, no deadlock.
  - `test_edit_constraint_disallowed_paths_default_blocks_tests` — changed files include `tests/foo.py`; default `disallowed_paths` flags it.
  - `test_edit_constraint_max_files_over_threshold` — 6 changed files, `max_changed_files=5` → `over_max_changed_files=True`.
  - `test_assemble_scoring_includes_n_a_for_hidden_when_absent` — case with no hidden test → `hidden_test_outcome == "n/a"`, no `hidden_test.json` written.
  - `test_assemble_scoring_token_usage_omitted_when_response_absent` — runner reported no response → `token_usage` not present in `scoring.json`.
  - `test_meta_json_is_atomic_temp_and_rename(tmp_path)` — monkeypatch `os.rename` to assert it's called with paths ending in `.tmp` → `meta.json`.
  - `test_meta_json_records_per_field_config_sources` — pass a sample `effective_config` with mixed sources; verify the written meta.json `effective_config.sources` matches.
  - `test_pipeline_runs_against_pristine_for_framework_misconfigured` — feed a `RunnerResult` with `error_reason="framework_misconfigured"`; pipeline still produces `diff.patch` (empty), `visible_test.json` (matching captured failure), `scoring.json`, and `meta.json`.

- [ ] **Step 10: Run** `cd evals && uv run pytest -q tests/pipeline_test.py`. All pass.

- [ ] **Step 11: Commit.** `git add evals/evals/pipeline.py evals/tests/pipeline_test.py && git commit -m "evals/pipeline: temp-index diff, capped test reruns, atomic sentinels"`.

**Acceptance criteria:**

- Diff derivation does not modify the cell repo's real `.git/index`.
  Verify: run `cd evals && uv run pytest -q tests/pipeline_test.py::test_diff_does_not_modify_real_index` and confirm exit 0 with `1 passed`.
- Visible/hidden test reruns cap stdout/stderr at 5 MiB, set `stdout_truncated`/`stderr_truncated`, and drain pipes to avoid deadlock.
  Verify: run `cd evals && uv run pytest -q tests/pipeline_test.py::test_visible_test_output_caps_and_drains tests/pipeline_test.py::test_visible_test_outcome_error_on_timeout` and confirm exit 0 with both passing.
- `scoring.json` and `meta.json` are written via temp-and-rename so a partial file cannot be observed as complete.
  Verify: run `cd evals && uv run pytest -q tests/pipeline_test.py::test_meta_json_is_atomic_temp_and_rename` and confirm exit 0 with `1 passed`.
- The pipeline runs to completion against a pristine worktree when the runner reports `framework_misconfigured`, producing the full cell-dir artifact set.
  Verify: run `cd evals && uv run pytest -q tests/pipeline_test.py::test_pipeline_runs_against_pristine_for_framework_misconfigured` and confirm exit 0 with `1 passed`.

**Model recommendation:** capable

---

### Task 10 — `campaign.py`: eval-new, CURRENT, lockfile

**Files:**
- Create: `evals/evals/campaign.py`
- Create: `evals/tests/campaign_test.py`

**Steps:**

- [ ] **Step 1: Implement `_now_iso() -> str`** returning `datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")` (campaign-dir name) and a separate `_iso_zulu() -> str` returning ISO 8601 with `Z` suffix for `started_at`.

- [ ] **Step 2: Implement `_git_state(repo_root) -> dict`** with subprocess calls returning `{git_sha, git_dirty, git_remote_url, git_branch}` per spec lines 477-485. Optional fields are omitted when not available.

- [ ] **Step 3: Implement `eval_new(repo_root: Path, *, frameworks: list[str], cases: list[str], config_overrides: dict) -> Path`.**

  - Build `runs/<ts>/` (rmtree if collision).
  - Write `manifest.json` with `started_at`, git state, frameworks list, cases list, `config_overrides` dict (keys: `model`, `timeout_s`, `max_steps`; missing values stored as `None`). Use atomic temp+rename.
  - Atomically update `runs/CURRENT` symlink: `os.symlink("<ts>", runs/CURRENT.tmp)`, then `os.rename(runs/CURRENT.tmp, runs/CURRENT)`.
  - Return the campaign path.

- [ ] **Step 4: Implement `current_campaign(repo_root) -> Path | None`** — `os.readlink(runs/CURRENT)` if exists.

- [ ] **Step 5: Implement `acquire_lock(campaign_dir: Path, *, argv: list[str], force_unlock: bool = False) -> None`** per spec lines 491-500:

  - Lock path: `<campaign_dir>/.lock`.
  - If lock does not exist: write `{pid, hostname, started_at, argv}` atomically (temp+rename). Return.
  - If lock exists, parse it.
  - If `hostname == socket.gethostname()`:
    - Check PID liveness via `os.kill(pid, 0)` (raises ProcessLookupError if dead, PermissionError if alive owned by another user).
    - If alive: raise `LockBusyError(f"Campaign in use by PID {pid} (since {started_at}). Delete <path> if stale.")`.
    - If dead: log warning to stderr, overwrite the lock atomically. Return.
  - If `hostname != socket.gethostname()`:
    - If `force_unlock`: log warning, overwrite lock atomically. Return.
    - Else: raise `LockBusyError(f"Campaign locked by PID {pid} on host {hostname} (since {started_at}). On a shared filesystem, that lock may still be live. If you are sure it is stale, delete <path> manually or pass --force-unlock.")`.

- [ ] **Step 6: Implement `release_lock(campaign_dir)` -> None`.** Best-effort `os.unlink` (ignore FileNotFoundError).

- [ ] **Step 7: Implement context manager `lock(campaign_dir, *, argv, force_unlock)`** wrapping acquire/release. Used by CLI.

- [ ] **Step 8: Write `campaign_test.py`.** Tests:
  - `test_eval_new_creates_dir_manifest_and_symlink(tmp_repo_root)` — after call, `<repo>/runs/<ts>/manifest.json` exists, `<repo>/runs/CURRENT` resolves to it.
  - `test_eval_new_manifest_records_overrides` — pass `config_overrides={"model":"foo"}`; check `manifest.json#config_overrides.model == "foo"` and other fields are `null`.
  - `test_acquire_lock_writes_pid_hostname` — after acquire, `.lock` JSON has correct `pid` and `hostname`.
  - `test_acquire_lock_refuses_alive_same_host(monkeypatch)` — pre-write a `.lock` with `pid=<self pid>, hostname=<gethostname()>`. Acquire raises `LockBusyError`.
  - `test_acquire_lock_reclaims_dead_same_host` — pre-write `.lock` with a known-dead pid (e.g., very high number) and same host. Acquire succeeds (with warning).
  - `test_acquire_lock_refuses_different_host` — pre-write `.lock` with hostname `"other-host"`. Acquire raises `LockBusyError`.
  - `test_acquire_lock_force_unlock_overrides_different_host` — same setup but `force_unlock=True`. Succeeds with logged warning.
  - `test_release_lock_removes_file` — after release, `.lock` does not exist.
  - `test_lock_context_manager_releases_on_exception` — entering, raising inside the `with` block, asserting lock is released.
  - `test_current_campaign_returns_none_when_no_symlink(tmp_repo_root)` — fresh repo → `current_campaign(...)` is None.

- [ ] **Step 9: Run** `cd evals && uv run pytest -q tests/campaign_test.py`. All pass.

- [ ] **Step 10: Commit.** `git add evals/evals/campaign.py evals/tests/campaign_test.py && git commit -m "evals/campaign: eval-new, CURRENT, cross-host-aware lockfile"`.

**Acceptance criteria:**

- `eval_new` creates the campaign dir, writes `manifest.json` capturing git state and config overrides, and atomically updates `runs/CURRENT`.
  Verify: run `cd evals && uv run pytest -q tests/campaign_test.py::test_eval_new_creates_dir_manifest_and_symlink tests/campaign_test.py::test_eval_new_manifest_records_overrides` and confirm exit 0 with both passing.
- Lockfile refuses on live-PID same-host, reclaims on dead-PID same-host, and refuses on different host (overridable by `--force-unlock`).
  Verify: run `cd evals && uv run pytest -q tests/campaign_test.py::test_acquire_lock_refuses_alive_same_host tests/campaign_test.py::test_acquire_lock_reclaims_dead_same_host tests/campaign_test.py::test_acquire_lock_refuses_different_host tests/campaign_test.py::test_acquire_lock_force_unlock_overrides_different_host` and confirm exit 0 with all four passing.
- The lock context manager releases on exception so a crashed harness leaves no stale `.lock`.
  Verify: run `cd evals && uv run pytest -q tests/campaign_test.py::test_lock_context_manager_releases_on_exception` and confirm exit 0 with `1 passed`.

**Model recommendation:** standard

---

### Task 11 — `status.py`: matrix renderer

**Files:**
- Create: `evals/evals/status.py`
- Create: `evals/tests/status_test.py`

**Steps:**

- [ ] **Step 1: Implement `render_status(campaign_dir: Path) -> str`** that:
  - Reads `<campaign>/manifest.json` for the `frameworks` and `cases` lists.
  - For each `(fw, case)` cell, classifies as:
    - `done-ok`: `<cell>/meta.json` exists AND `meta.status == "ok"`.
    - `done-error`: `<cell>/meta.json` exists AND `meta.status == "error"` (note `error_reason`).
    - `partial`: cell dir exists but no `meta.json`.
    - `missing`: cell dir doesn't exist.
  - Produces a fixed-width matrix with rows = frameworks, columns = cases. Cell glyph: `.` for missing, `…` for partial, `O` for done-ok, `E` for done-error. Plus a per-cell legend below the table for cells with `done-error` listing the `error_reason`.

- [ ] **Step 2: Implement `print_status(campaign_dir, *, file=sys.stdout)`** that calls `render_status` and prints.

- [ ] **Step 3: Write `status_test.py`.** Tests:
  - `test_render_status_missing_cell_appears_as_dot(tmp_path)` — fresh campaign, no cells filled → all `.`.
  - `test_render_status_done_ok_cell_appears_as_O(tmp_path)` — write a `meta.json` with `status=ok`; that cell is `O`.
  - `test_render_status_done_error_cell_appears_as_E(tmp_path)` — `status=error, error_reason="timeout"`; cell is `E`; legend mentions `timeout`.
  - `test_render_status_partial_cell_appears_as_ellipsis(tmp_path)` — cell dir with `request.json` only, no `meta.json`; cell shows `…`.

- [ ] **Step 4: Run** `cd evals && uv run pytest -q tests/status_test.py`. All pass.

- [ ] **Step 5: Commit.** `git add evals/evals/status.py evals/tests/status_test.py && git commit -m "evals/status: matrix renderer"`.

**Acceptance criteria:**

- `render_status` correctly classifies each cell type (missing, partial, done-ok, done-error).
  Verify: run `cd evals && uv run pytest -q tests/status_test.py` and confirm exit 0 with `4 passed`.

**Model recommendation:** cheap

---

### Task 12 — `report.py`: campaign report renderer

**Files:**
- Create: `evals/evals/report.py`
- Create: `evals/tests/report_test.py`

**Steps:**

- [ ] **Step 1: Implement `render_report(campaign_dir: Path) -> str`** that produces the markdown shape from spec lines 600-627:
  - Header: `# Campaign <ts>` + `Campaign config: model=<...>, timeout_s=<...>, max_steps=<...>` + `Cases: N — <ids>`.
  - "## Per-cell results" table with columns: framework, case, visible, hidden, edit_compl., files, +/- lines, latency, tokens (i/o), status. Cells with `effective_config != campaign config_overrides+defaults` get a `*` suffix on the framework column. Status column shows `ok` or `error: <reason>`.
  - "## Per-framework summary" table.
  - "## Notes" section listing typed-failure summaries (with relative links to `<framework>/<case>/stderr.log`), setup failures (with links to `.runs-cache/setup/<fw>.stderr.log`), venv-mutation warnings, and the line `trace_quality: n/a in v1 (capture-only)`.

- [ ] **Step 2: Implement `_cell_differs_from_campaign_config(cell_meta, campaign_overrides) -> bool`.** Compare each field's `sources` value: if any field's `sources[field]` is `"cell-flag"`, the cell differs.

- [ ] **Step 3: Implement `write_report(campaign_dir: Path) -> None`** that calls `render_report` and writes to `<campaign>/report.md` via atomic temp+rename. Idempotent.

- [ ] **Step 4: Write `report_test.py` with a golden-file pattern.** Build a synthetic on-disk campaign via fixtures (no subprocess invocation needed):
  - Create `runs/2026-04-29T00-00-00/manifest.json` with two frameworks, two cases.
  - Create four cell dirs with synthetic `meta.json` and `scoring.json` exercising: ok+pass+pass, ok+fail+n/a, error+timeout, error+nonzero_exit.
  - Make one cell's `effective_config.sources.timeout_s = "cell-flag"` so the report should mark it.
  - Tests:
    - `test_render_report_contains_header_and_tables(synthetic_campaign)` — output contains `# Campaign`, the per-cell table header `framework | case |`, and the per-framework summary header.
    - `test_render_report_marks_cell_level_overrides_with_asterisk` — cell with `cell-flag` source for any field has `*` next to its framework name in that row.
    - `test_render_report_lists_setup_failures` — pre-create `.runs-cache/setup/x.fail`; report's Notes section links to `.runs-cache/setup/x.stderr.log`.
    - `test_render_report_lists_venv_mutations` — cell meta with `venv_mutated=true`; Notes section warns about it.
    - `test_render_report_status_error_includes_reason` — cell with `error_reason="timeout"`; status column reads `error: timeout`.
    - `test_write_report_is_idempotent` — call twice; second call doesn't raise; report bytes are identical.

- [ ] **Step 5: Run** `cd evals && uv run pytest -q tests/report_test.py`. All pass.

- [ ] **Step 6: Commit.** `git add evals/evals/report.py evals/tests/report_test.py && git commit -m "evals/report: per-campaign markdown report"`.

**Acceptance criteria:**

- The report includes a header, per-cell table, per-framework summary, and Notes section.
  Verify: run `cd evals && uv run pytest -q tests/report_test.py::test_render_report_contains_header_and_tables` and confirm exit 0 with `1 passed`.
- Cells whose `effective_config` differs from the campaign config are marked with `*` next to the framework name.
  Verify: run `cd evals && uv run pytest -q tests/report_test.py::test_render_report_marks_cell_level_overrides_with_asterisk` and confirm exit 0 with `1 passed`.
- Setup failures and venv mutations surface in the Notes section.
  Verify: run `cd evals && uv run pytest -q tests/report_test.py::test_render_report_lists_setup_failures tests/report_test.py::test_render_report_lists_venv_mutations` and confirm exit 0 with both passing.
- Report rendering is idempotent.
  Verify: run `cd evals && uv run pytest -q tests/report_test.py::test_write_report_is_idempotent` and confirm exit 0 with `1 passed`.

**Model recommendation:** standard

---

### Task 13 — CLI: `__main__.py` + `cli.py` (argparse subcommands, override rules, auto-behaviors)

**Files:**
- Create: `evals/evals/__main__.py`
- Create: `evals/evals/cli.py`

**Steps:**

- [ ] **Step 1: Write `__main__.py`.** One-liner: `from .cli import main; raise SystemExit(main())`.

- [ ] **Step 2: Write `cli.py`** with `argparse.ArgumentParser` and subparsers for: `frameworks`, `cases`, `eval-prepare`, `eval-new`, `eval-all`, `eval`, `eval-status`, `eval-report`, `eval-clean-cache`, `eval-clean-runs`. The dispatcher returns 0 or non-zero exit codes.

- [ ] **Step 3: Add the override flags** on the relevant subparsers per spec lines 542-554:
  - `eval-new`: `--model`, `--timeout-s`, `--max-steps`.
  - `eval-all`: `--model`, `--timeout-s`, `--max-steps`, `--framework`, `--case`, `--force-unlock`.
  - `eval`: positional `framework` and `case`, plus `--model`, `--timeout-s`, `--max-steps`, `--force-unlock`.
  - `eval-prepare`: `--setup-timeout-s` (default 600).

- [ ] **Step 4: Wire `cmd_frameworks(args)`** to `discovery.discover_frameworks` and print one line per framework.

- [ ] **Step 5: Wire `cmd_cases(args)`** similarly.

- [ ] **Step 6: Wire `cmd_eval_prepare(args)`.**
  1. Discover frameworks and cases.
  2. For each case: `workspace.ensure_case_bare_repo`, `workspace.ensure_case_venv`. (Errors collected; continue past failures.)
  3. For each framework with a `setup`: `setup.run_framework_setup(...)`. Continue past failures.
  4. Print summary table; exit non-zero if any setup or venv build failed.

- [ ] **Step 7: Wire `cmd_eval_new(args)`.**
  - Discover frameworks/cases (only well-formed ones).
  - Build `config_overrides = {"model": args.model, "timeout_s": args.timeout_s, "max_steps": args.max_steps}` (None values for unset flags).
  - Call `campaign.eval_new(...)`.
  - Print the new campaign path.

- [ ] **Step 8: Wire `cmd_eval_all(args)` per the spec's auto-behavior + override rules.**
  - Determine if `runs/CURRENT` already points at a campaign.
  - If override flags are set:
    - Existing campaign → reject with the helpful error (per spec lines 311-313): `"--<flag> passed but campaign already exists; use 'just eval-new --<flag> X' to start a fresh campaign with overrides, or omit the flag to fill missing cells with the campaign's config."`. Exit code 2.
    - No existing campaign → forward the flags to a new `eval_new(...)`.
  - If no existing campaign and no override flags: auto-call `eval_new`.
  - Auto-call `eval-prepare` if any setup sentinel is missing/stale OR any case venv is missing. Continue past per-framework setup failures (cells of failed-setup frameworks get `framework_misconfigured`).
  - Acquire campaign lock (with `force_unlock=args.force_unlock`).
  - Filter the matrix by `--framework` / `--case` (intersection per spec line 546).
  - For each `(fw, case)` cell missing its `meta.json`: rmtree any partial cell-dir, run cell.
  - At end: write the report.
  - Release lock.

  The "run cell" sequence for one cell:
  1. Compute `effective_config` via `runner.resolve_effective_config(...)` with current `config_overrides` from the campaign manifest and any cell flags (none here for `eval-all`).
  2. Clone layer-3 worktree to `<cell>/repo`.
  3. Compute `venv_hash_before`.
  4. Call `runner.run_cell(...)`.
  5. Call `pipeline.run_pipeline(...)`.

- [ ] **Step 9: Wire `cmd_eval(args)` for single-cell.** Same as the cell-run sequence above, with `effective_config` taking the cell flags. Always accepts override flags. The campaign manifest is unchanged.

- [ ] **Step 10: Wire `cmd_eval_status(args)`.** Calls `status.print_status(campaign_dir)`.

- [ ] **Step 11: Wire `cmd_eval_report(args)`.** Calls `report.write_report(campaign_dir)`.

- [ ] **Step 12: Wire `cmd_eval_clean_cache(args)`** — `shutil.rmtree(repo_root / ".runs-cache")`.

- [ ] **Step 13: Wire `cmd_eval_clean_runs(args)`** — `shutil.rmtree(repo_root / "runs")`.

- [ ] **Step 14: Smoke-test the CLI.**
  - `cd evals && uv run python -m evals --help` exits 0 and lists every subcommand.
  - `cd evals && uv run python -m evals frameworks` lists at least the 8 framework names (well-formed manifests will exist after Task 14; for now this may show empty or only fake-framework — that's fine for now).

- [ ] **Step 15: Commit.** `git add evals/evals/__main__.py evals/evals/cli.py && git commit -m "evals/cli: argparse subcommands + override precedence + auto-behaviors"`.

**Acceptance criteria:**

- Every spec verb (`frameworks`, `cases`, `eval-prepare`, `eval-new`, `eval-all`, `eval`, `eval-status`, `eval-report`, `eval-clean-cache`, `eval-clean-runs`) is wired up via argparse subcommands.
  Verify: run `cd evals && uv run python -m evals --help` and confirm stdout contains each subcommand name as a separate line, exit 0.
- `eval-all` rejects override flags when a campaign exists.
  Verify: open `evals/evals/cli.py` and confirm `cmd_eval_all` checks for `current_campaign(repo_root)` and (any of `args.model`, `args.timeout_s`, `args.max_steps` is not None), and exits non-zero with a message containing "use 'just eval-new" if both conditions hold. (Tested end-to-end in Task 16.)
- `eval` always accepts override flags and records cell-level effective config (per-field sources) — the actual recording is in pipeline; CLI only forwards.
  Verify: open `evals/evals/cli.py` and confirm `cmd_eval` builds `cell_overrides = {"model": args.model, "timeout_s": args.timeout_s, "max_steps": args.max_steps}` and passes these to `runner.resolve_effective_config(..., cell_overrides=cell_overrides, ...)`.

**Model recommendation:** capable

---

### Task 14 — Stub framework adapters: manifests + run.sh + framework README notes

**Files:**
- Create: `frameworks/deepagents/manifest.json`
- Create: `frameworks/deepagents/run.sh`
- Create: `frameworks/pydantic-ai/manifest.json`
- Create: `frameworks/pydantic-ai/run.sh`
- Create: `frameworks/google-adk/manifest.json`
- Create: `frameworks/google-adk/run.sh`
- Create: `frameworks/strands/manifest.json`
- Create: `frameworks/strands/run.sh`
- Create: `frameworks/agentcore/manifest.json`
- Create: `frameworks/agentcore/run.sh`
- Create: `frameworks/claude-agent-sdk/manifest.json`
- Create: `frameworks/claude-agent-sdk/run.sh`
- Create: `frameworks/openai-agents/manifest.json`
- Create: `frameworks/openai-agents/run.sh`
- Create: `frameworks/mastra/manifest.json`
- Create: `frameworks/mastra/run.sh`
- Modify: `frameworks/deepagents/README.md`
- Modify: `frameworks/pydantic-ai/README.md`
- Modify: `frameworks/google-adk/README.md`
- Modify: `frameworks/strands/README.md`
- Modify: `frameworks/agentcore/README.md`
- Modify: `frameworks/claude-agent-sdk/README.md`
- Modify: `frameworks/openai-agents/README.md`
- Modify: `frameworks/mastra/README.md`

**Steps:**

- [ ] **Step 1: For each framework dir**, write `manifest.json` with content:

  ```json
  {
    "entry": "./run.sh",
    "env": [],
    "model": "claude-sonnet-4-6"
  }
  ```

  (No `setup` field — stubs need no setup. This is required by spec line 23: "v1 ships stub scripts that exit non-zero so `eval-all` runs end-to-end on day one".)

- [ ] **Step 2: For each framework dir**, write `run.sh`:

  ```sh
  #!/bin/sh
  echo "<framework_name>: not implemented" 1>&2
  exit 2
  ```

  Substitute the framework name. `chmod +x run.sh` on each.

- [ ] **Step 3: For each framework `README.md`**, append the line:

  ```
  > Note: `manifest.json` and `run.sh` are v1 stubs that exit non-zero. The real adapter is follow-on work tracked in the framework-specific TODO list.
  ```

  (Place at the end of the existing README.)

- [ ] **Step 4: Verify each `run.sh` is executable** by running `ls -la frameworks/*/run.sh | grep -E "^-rwx" | wc -l`. Expected: `8`.

- [ ] **Step 5: Smoke-test by running one stub directly.** `echo '{}' | frameworks/deepagents/run.sh; echo "exit=$?"`. Expected: stderr says "deepagents: not implemented", and `exit=2`.

- [ ] **Step 6: Smoke-test discovery.** `cd evals && uv run python -m evals frameworks`. Expected: lists all 8 framework names.

- [ ] **Step 7: Commit.** `git add frameworks/ && git commit -m "frameworks: v1 stubs (manifest.json + run.sh + README note for each)"`.

**Acceptance criteria:**

- All 8 framework dirs have a valid `manifest.json` with `entry`, `env`, and `model` fields.
  Verify: `for d in frameworks/*/; do python -c "import json,sys; m=json.load(open(sys.argv[1])); assert set(m.keys())>={'entry','env','model'}, sys.argv[1]" "$d/manifest.json"; done && echo ok` exits 0 and prints `ok`.
- Each `run.sh` is executable and exits non-zero.
  Verify: `for f in frameworks/*/run.sh; do test -x "$f" || { echo "$f not executable"; exit 1; }; ( "$f" < /dev/null >/dev/null 2>&1; test "$?" != "0" ) || { echo "$f exited 0"; exit 1; }; done && echo ok` exits 0 and prints `ok`.
- `python -m evals frameworks` lists all 8 frameworks.
  Verify: run `cd evals && uv run python -m evals frameworks | wc -l` and confirm the output is `8`.

**Model recommendation:** cheap

---

### Task 15 — Justfile updates: full v3 verb set

**Files:**
- Modify: `justfile`
- Modify: `evals/README.md`

**Steps:**

- [ ] **Step 1: Replace the existing `justfile`** (lines 1-15) with:

  ```just
  _default:
      @just --list

  # List framework dirs.
  frameworks:
      cd evals && uv run python -m evals frameworks

  # List discovered cases.
  cases:
      cd evals && uv run python -m evals cases

  # Build per-case bare repos and venvs; run framework setups. Idempotent.
  eval-prepare *flags:
      cd evals && uv run python -m evals eval-prepare {{flags}}

  # Create a new campaign (runs/<ts>/) and repoint runs/CURRENT.
  eval-new *flags:
      cd evals && uv run python -m evals eval-new {{flags}}

  # Fill missing cells in runs/CURRENT (auto-runs prepare + new if needed).
  eval-all *flags:
      cd evals && uv run python -m evals eval-all {{flags}}

  # Run/rerun a single cell.
  eval framework case *flags:
      cd evals && uv run python -m evals eval {{framework}} {{case}} {{flags}}

  # Print matrix of filled / missing / error per cell in CURRENT.
  eval-status:
      cd evals && uv run python -m evals eval-status

  # Regenerate runs/CURRENT/report.md.
  eval-report:
      cd evals && uv run python -m evals eval-report

  # Wipe .runs-cache/.
  eval-clean-cache:
      cd evals && uv run python -m evals eval-clean-cache

  # Wipe runs/.
  eval-clean-runs:
      cd evals && uv run python -m evals eval-clean-runs
  ```

- [ ] **Step 2: Smoke-test the justfile** by running `just --list`. Confirm all 10 recipes appear (default `_default` plus the 9 verbs).

- [ ] **Step 3: Smoke-test `just frameworks`.** Should list the 8 framework names.

- [ ] **Step 4: Update `evals/README.md`.** Replace the file content with:

  ````markdown
  # evals

  Framework-agnostic eval harness. Discovers framework manifests in `../frameworks/<name>/manifest.json`, invokes them through the contract in `../shared/contract.md`, and scores them against `../shared/task-spec.md`.

  ## Run

  Top-level orchestration is in the repo-root `justfile`:

  ```sh
  just eval-prepare          # build per-case caches and run framework setups
  just eval-new              # create a fresh campaign
  just eval-all              # run every (framework, case) cell in the current campaign
  just eval <fw> <case>      # run/rerun one cell
  just eval-status           # matrix of done/missing/error
  just eval-report           # regenerate runs/CURRENT/report.md
  ```

  Override flags `--model <id>`, `--timeout-s <n>`, `--max-steps <n>` are accepted on `eval-new` (campaign-level) and `eval` (cell-level). `eval-all` rejects them inside an existing campaign — start a new campaign with `eval-new --model X` instead.

  ## Layout

  - `evals/__main__.py` — CLI entry (`python -m evals <verb>`)
  - `evals/cli.py` — subcommand dispatch
  - `evals/discovery.py` — find frameworks and cases
  - `evals/workspace.py` — bare git, venv, per-cell worktree (layers 1, 2, 3)
  - `evals/setup.py` — framework setup runner (`.ok` / `.fail` sentinels)
  - `evals/runner.py` — one cell: build request, spawn, capture, classify
  - `evals/pipeline.py` — temp-index diff, test reruns, edit constraint, scoring, atomic meta sentinel
  - `evals/campaign.py` — campaign creation, CURRENT pointer, lockfile
  - `evals/status.py` — matrix renderer
  - `evals/report.py` — markdown report
  - `evals/env.py` — `.env` loading, agent_env / test_env / setup_env
  - `evals/schemas.py` — bespoke validators for framework / case / envelope / agent output

  Tests live in `tests/`; integration tests are gated by `pytest -m integration`.
  ````

- [ ] **Step 5: Commit.** `git add justfile evals/README.md && git commit -m "justfile: v3 verb set; evals/README: rewrite to match"`.

**Acceptance criteria:**

- `just --list` shows the full v3 verb set.
  Verify: run `just --list` and confirm output contains a line for each of `frameworks`, `cases`, `eval-prepare`, `eval-new`, `eval-all`, `eval`, `eval-status`, `eval-report`, `eval-clean-cache`, `eval-clean-runs`.
- `just frameworks` lists the 8 framework dirs.
  Verify: run `just frameworks 2>/dev/null | wc -l` and confirm output is exactly `8`.
- `evals/README.md` no longer contains the placeholder `## Layout (TODO — implement)` section.
  Verify: `grep -n "TODO — implement" evals/README.md` returns zero matches.

**Model recommendation:** cheap

---

### Task 16 — Resume + integration tests + final E2E acceptance

**Files:**
- Create: `evals/tests/resume_test.py`
- Create: `evals/tests/integration/__init__.py`
- Create: `evals/tests/integration/test_fake_framework.py`
- Create: `evals/tests/integration/test_eval_all_stub.py`

**Steps:**

- [ ] **Step 1: Write `resume_test.py`.** Tests exercise the resume rule from spec line 428: cell is "done" iff `meta.json` exists.
  - `test_resume_blows_away_partial_cell_dir(tmp_path)` — pre-create `<cell>/request.json` and `<cell>/diff.patch` without a `meta.json`. Run `cmd_eval_all` for that single cell. After: cell dir has `meta.json` (it was rebuilt from scratch).
  - `test_resume_skips_cells_with_meta_json(tmp_path)` — pre-create a cell dir with a complete `meta.json` (status `error`, `error_reason: nonzero_exit`). Run `cmd_eval_all`. After: the original `meta.json`'s `started_at` is unchanged (the cell was skipped).
  - `test_resume_treats_meta_tmp_as_partial(tmp_path)` — pre-create `<cell>/meta.json.tmp` (left over from a crash) but no `meta.json`. After resume: the cell is rebuilt; `meta.json.tmp` is gone (cleaned up at the start) and `meta.json` exists.

- [ ] **Step 2: Write `integration/__init__.py`** as empty marker.

- [ ] **Step 3: Write `integration/test_fake_framework.py`** with one parametrized test that runs the fake framework against the synthetic case for every `FAKE_BEHAVIOR` value and asserts the resulting `meta.error_reason`. The test sets up an isolated temp `repo_root` with:
  - A `frameworks/fake/` directory copied from `evals/tests/fixtures/fake-framework/`.
  - A `cases/test-case-001.json` copied from `evals/tests/fixtures/cases/test-case-001.json` (with paths adjusted relative to the temp root) and the matching fixture and `failure_output.txt`.
  - The fake-framework's manifest extended with the relevant `FAKE_BEHAVIOR` baked in via the `env` array (or, simpler, the test sets `FAKE_BEHAVIOR=...` in the harness's base_env and the fake framework declares that key in its manifest's `env` list).

  Parametrized cases (one assertion per row of the spec's fake-framework table):

  | FAKE_BEHAVIOR | expected meta.error_reason | extra assertions |
  | --- | --- | --- |
  | `success-noop` | `None` | scoring.json `schema_validity == True` |
  | `success-fix` | `None` | visible_test_outcome == "pass" |
  | `hang` | `"timeout"` | meta.exit_code is None |
  | `crash` | `"nonzero_exit"` | response.json does NOT exist |
  | `crash-with-error-envelope` | `"nonzero_exit"` | response.json exists |
  | `crash-with-bad-json` | `"nonzero_exit"` | response.json does NOT exist |
  | `garbage` | `"malformed_response_json"` | |
  | `empty` | `"missing_response"` | |
  | `oversize` | `"malformed_response_json"` | meta.stdout_truncated == True |
  | `missing-field` | `"envelope_schema_violation"` | |
  | `forbidden-field` | `None` | scoring.json `schema_validity == False` |
  | `disallowed-edit` | `None` | scoring.edit_constraint_compliance.disallowed_violations is non-empty |
  | `over-max-files` | `None` | scoring.edit_constraint_compliance.over_max_changed_files == True |
  | `noisy-stderr` | `None` | meta.stderr_truncated == True |
  | `mutate-venv` | `None` | meta.venv_mutated == True |
  | `noisy-test-output` | `None` | visible_test.json.stdout_truncated == True |

  The test invokes `cmd_eval` directly (not via subprocess) for speed. Mark with `@pytest.mark.integration` since it requires `uv` (for layer 2 venv).

- [ ] **Step 4: Write `integration/test_eval_all_stub.py`** — the headline acceptance test from spec line 725:
  - Set up a temp repo_root mirroring the real repo: `cases/`, `fixtures/<one tiny case>/`, `frameworks/<each>/manifest.json + run.sh stub` (copy from real ones), `.runs-cache/` empty, `runs/` empty. (We use the synthetic `test-case-001` as the single case to keep the test fast.)
  - Invoke `cmd_eval_all([])` (no flags).
  - Assertions:
    - `runs/CURRENT/manifest.json` exists.
    - For every (framework, case) cell: `<cell>/meta.json` exists, `meta.status == "error"`, `meta.error_reason == "nonzero_exit"`.
    - `runs/CURRENT/report.md` exists and is non-empty.

  Mark `@pytest.mark.integration`.

- [ ] **Step 5: Run unit-only suite to confirm no regressions.** `cd evals && uv run pytest -q -m "not integration"`. All pass.

- [ ] **Step 6: Run integration suite.** `cd evals && uv run pytest -q -m integration`. All pass.

- [ ] **Step 7: Run the full suite.** `cd evals && uv run pytest -q`. All pass.

- [ ] **Step 8: Manually verify the headline acceptance criterion** (spec line 725) by running `just eval-prepare && just eval-all` against the real repo (with the real cases and the stub framework adapters). This will:
  - Build `.runs-cache/<case>.git/` for each of the 4 real cases (this hashes & copies tracked fixture files, then `git init/add/commit/push`).
  - Build `.runs-cache/<case>.venv/` for each (uv sync --no-install-project; needs network for the SWE-bench cases).
  - Run no setups (stubs have no `setup`).
  - Create `runs/<ts>/` campaign + `runs/CURRENT` symlink.
  - For each (framework=8, case=4) cell: clone worktree, spawn run.sh stub (exits 2 with stderr "<fw>: not implemented"), run pipeline (visible test will fail because fixture is buggy and the agent did nothing), write `scoring.json` and `meta.json`.
  - 8 × 4 = 32 cells, each `status: "error", error_reason: "nonzero_exit"`. Plus `report.md` regenerates.

  Confirm:
  - `find runs/CURRENT -name meta.json | wc -l` = 32.
  - `for f in runs/CURRENT/*/*/meta.json; do python -c "import json,sys; m=json.load(open(sys.argv[1])); assert m['error_reason']=='nonzero_exit', sys.argv[1]" "$f"; done; echo $?` prints `0`.
  - `head -20 runs/CURRENT/report.md` shows the campaign header and the per-cell table.

- [ ] **Step 9: Commit.** `git add evals/tests/resume_test.py evals/tests/integration && git commit -m "evals/tests: resume + integration suite + E2E stub-frameworks acceptance"`.

**Acceptance criteria:**

- Every fake-framework `FAKE_BEHAVIOR` produces the expected `meta.error_reason` and scoring shape.
  Verify: run `cd evals && uv run pytest -q -m integration tests/integration/test_fake_framework.py` and confirm exit 0 with `16 passed` (one per FAKE_BEHAVIOR row).
- Resume blows away partial cells without `meta.json` and skips cells with `meta.json`, including error cells.
  Verify: run `cd evals && uv run pytest -q tests/resume_test.py` and confirm exit 0 with `3 passed`.
- Leftover `meta.json.tmp` from a crash is treated as not-done and cleaned up on resume.
  Verify: run `cd evals && uv run pytest -q tests/resume_test.py::test_resume_treats_meta_tmp_as_partial` and confirm exit 0 with `1 passed`.
- `just eval-all` against the real repo with stub framework adapters produces a campaign in which every cell has `status: "error"` and `error_reason: "nonzero_exit"`, plus a `report.md`.
  Verify: with `.runs-cache/` and `runs/` deleted (or after `just eval-clean-cache && just eval-clean-runs`), run `just eval-all`, then `find runs/CURRENT -name meta.json -exec python -c "import json,sys; m=json.load(open(sys.argv[1])); assert m['status']=='error' and m['error_reason']=='nonzero_exit', sys.argv[1]" {} \; && test -s runs/CURRENT/report.md && echo ok`. Confirm stdout ends with `ok` and exit 0.
- The full pytest suite passes.
  Verify: run `cd evals && uv run pytest -q` and confirm exit 0 with the printed summary line containing `passed` and no `failed` count.

**Model recommendation:** capable

---

## Dependencies

```
- Task 1 depends on: (none)
- Task 2 depends on: Task 1
- Task 3 depends on: Task 1
- Task 4 depends on: Task 2, Task 3
- Task 5 depends on: Task 1
- Task 6 depends on: Task 1, Task 2
- Task 7 depends on: Task 4, Task 5
- Task 8 depends on: Task 2, Task 3, Task 4, Task 5, Task 6, Task 7 (uses fake-framework fixtures plus workspace + setup state)
- Task 9 depends on: Task 2, Task 3, Task 5, Task 6, Task 8
- Task 10 depends on: Task 1, Task 2
- Task 11 depends on: Task 4
- Task 12 depends on: Task 4, Task 9, Task 10
- Task 13 depends on: Task 4, Task 6, Task 7, Task 8, Task 9, Task 10, Task 11, Task 12
- Task 14 depends on: Task 13 (smoke-test of `python -m evals frameworks`)
- Task 15 depends on: Task 13, Task 14
- Task 16 depends on: Task 2, Task 13, Task 14, Task 15 (needs everything wired up)
```

## Risk Assessment

1. **`uv sync --no-install-project` flag combination may need tuning.**
   - Spec lines 95-99 explicitly mark this as an open implementation detail. The plan codifies `--no-install-project` (and `--frozen` when `uv.lock` is present), but if a fixture has a transitive dep that requires the project metadata, `uv sync` may fail.
   - **Mitigation:** Task 6 includes an integration-marked test (`test_ensure_case_venv_no_install_project`) that exercises a real synthetic fixture and asserts the project is NOT in site-packages. Test failures here are early signal.
   - **Fallback:** if the canonical "deps-only" mode requires a different flag combination on a specific case (e.g., `uv sync --only-group dev`), add a per-case override to the case manifest in a follow-on plan.

2. **`shutil.copytree` of fixtures with executable bits may not preserve mode.**
   - Layer 1 bare-repo construction copies tracked files. Some fixtures (e.g., `psf__requests-1921`) may have executable test scripts.
   - **Mitigation:** Task 6 step 5 specifies "preserving file mode" via `shutil.copy2` (which preserves mode) per file rather than copytree.

3. **`git ls-files` requires the repo to be at the repo_root with the fixtures in tracked state.**
   - If a developer is working on a fixture with uncommitted-but-untracked new files, those won't make it into the bare repo. The harness is silent about this.
   - **Mitigation:** The fixture content hash includes the working-tree contents of tracked files, so modified-but-tracked changes propagate correctly. Untracked files are an explicit exclusion documented in `workspace_test.py::test_compute_fixture_hash_excludes_untracked`. Add a note to the eval-prepare summary if `git status --porcelain fixtures/` shows untracked files.

4. **Cross-host lock detection assumes `socket.gethostname()` is stable.**
   - On dynamic hostnames (containers, CI), `gethostname()` may change between invocations on the same machine. That would make a same-host PID look like cross-host and incorrectly refuse.
   - **Mitigation:** The `--force-unlock` flag exists for exactly this case. Per spec line 498-499, a documented workaround is acceptable for v1. The plan does not add hostname-stability heuristics.

5. **`pathspec` library's gitwildmatch may not handle every glob the same way as upstream git.**
   - The default `disallowed_paths` use globs like `**/*test*`, `tests/**`. `pathspec` is widely used (in `pre-commit`, `black`, etc.) and matches gitignore semantics, but edge cases exist (e.g., `**/*test*` matching `tests/foo/bar.py` could go either way).
   - **Mitigation:** Task 9's pipeline tests include `test_edit_constraint_disallowed_paths_default_blocks_tests` which covers the typical case. If a glob behaves unexpectedly in production, fix-forward by tightening the case's manifest constraints.

6. **The fake-framework `mutate-venv` mode writes into the shared venv, which may pollute subsequent integration runs.**
   - **Mitigation:** Each integration test uses an isolated temp repo_root with its own `.runs-cache/`, so cross-test pollution doesn't happen. The test asserts `venv_mutated: true` then the temp dir is torn down by pytest.

7. **The "headline" acceptance test (Task 16 step 8) requires network for `uv sync` against SWE-bench cases.**
   - In offline environments, the test may fail at `eval-prepare` for cases other than `py-parse-duration-001` (which has zero deps).
   - **Mitigation:** The test is documented as part of step 8 and CI/dev-machine assumed. The unit suite (Task 16 step 5) does not require network and covers the same paths via the fake framework.

8. **Unicode / encoding in `failure_output_path` files.**
   - Real captured pytest traces are mostly ASCII but may contain Unicode (✓, ⚠, etc.).
   - **Mitigation:** `discovery.py` Step 3 reads `failure_output_path` as UTF-8 with `errors="replace"` and never re-encodes; the captured string flows through verbatim into `request.json` (which is JSON, so any bytes that survive UTF-8 decode are fine).

## Test Command

```bash
cd evals && uv run pytest -q
```

---

## Self-Review

**Spec coverage** (each acceptance criterion from spec lines 723-737 mapped to a task):

- "evals/ contains the modules listed in 'Module layout'" — Tasks 3-12 (one module per task).
- "just eval-all on a fresh clone (with stub framework scripts) runs end-to-end" — Task 16 step 8, tested by `test_eval_all_stub.py`.
- "Workspace lifecycle: layer 1 / 2 / 3 build and rebuild on the documented triggers" — Task 6.
- "Layer 2 venv contains case dependencies but not the fixture project itself" — Task 6 step 11 (`test_ensure_case_venv_no_install_project`).
- "Venv fingerprint is recorded before and after each cell run" — Task 9 step 8 (pipeline computes both, writes to meta).
- "Pipeline: every typed `error_reason` is reachable" — Task 16 step 3 (`test_fake_framework.py` parametrized over all FAKE_BEHAVIOR values).
- "Schema-validity violations of agent output are non-fatal" — Task 9 (forbidden-field test → `schema_validity=false` but `error_reason=null`).
- "Diff derivation does not mutate `<cell>/repo/.git/index`" — Task 9 step 9 (`test_diff_does_not_modify_real_index`).
- "response.json exists iff parsed and envelope-valid" — Task 8 (`test_runner_classifies_crash_with_error_envelope_writes_response_and_keeps_nonzero_exit` and `test_runner_classifies_crash_with_bad_json_does_not_write_response`).
- "Visible/hidden test outputs are capped with truncation flags; harness drains pipes after cap" — Task 9 step 9 (`test_visible_test_output_caps_and_drains`).
- "Storage: `runs/CURRENT` symlink, campaign manifest.json, lockfile semantics" — Task 10.
- "Cross-host refuse with --force-unlock override" — Task 10 (`test_acquire_lock_refuses_different_host`, `test_acquire_lock_force_unlock_overrides_different_host`).
- "stdout.log is always present; response.json is present iff stdout parsed AND envelope validated" — Task 8 (runner contract).
- "meta.json and scoring.json are written via temp-and-rename atomic protocol" — Task 9 (`test_meta_json_is_atomic_temp_and_rename`).
- "CLI: every verb in the table runs and does what its row says" — Task 13 + Task 15.
- "eval-all rejects override flags in an existing campaign with a helpful message; eval accepts them per-cell" — Task 13 step 8.
- "Re-running eval <fw> <case> overwrites that cell" — Task 9 / Task 13 (`wipe_cell_dir` before rerun).
- "Configuration overrides: campaign-level overrides recorded in manifest.json#config_overrides and frozen" — Task 10 step 3 (manifest never mutated after eval-new).
- "per-cell meta.effective_config records model / timeout_s / max_steps with per-field sources" — Task 8 (`resolve_effective_config`) + Task 9 (writes them to meta).
- "Environments: agent_env includes declared keys; test_env excludes them" — Task 5 (`test_test_env_excludes_framework_keys`).
- "Both envs prepend `.runs-cache/<case>.venv/bin` to PATH" — Task 5 (`test_*_path_prepends_venv_bin`).
- "Setup orchestration: eval-prepare runs each framework's setup, captures stdout/stderr, writes .ok only on success and .fail on failure, continues past failures and reports a summary" — Task 7.
- "Cells of failed-setup frameworks fail-fast as framework_misconfigured" — Task 8 (`test_runner_misconfigured_when_setup_fail_exists`).
- "framework_misconfigured artifacts: full cell-dir artifact set" — Task 9 (`test_pipeline_runs_against_pristine_for_framework_misconfigured`).
- "Reporting: report.md regenerates idempotently from cell artifacts" — Task 12 (`test_write_report_is_idempotent`).
- "Cells with cell-level overrides are visibly marked" — Task 12 (`test_render_report_marks_cell_level_overrides_with_asterisk`).
- "Setup failures and venv mutations surface in the Notes section" — Task 12 (`test_render_report_lists_setup_failures`, `test_render_report_lists_venv_mutations`).
- "Tests: unit suite passes without uv; integration suite passes with uv available and exercises every error_reason" — Task 16 step 5 (unit), Task 16 step 6 (integration).
- "Harness never imports framework code; only invokes per-framework entry scripts as subprocesses" — Plan-wide architectural rule. Verifiable via `grep -rn "import frameworks" evals/evals/` returning zero matches.
- "The repo/ worktree is left in place after each cell run" — Task 9 (no rmtree of `<cell>/repo/` after pipeline; only on rerun).

**Placeholder scan:** No `TBD`, `TODO`, `implement later`, "similar to Task N", "add appropriate X", or unresolved type names remain. Each `Verify:` recipe names a specific artifact, command, and success condition.

**Type / name consistency:**
- `FrameworkSpec`, `CaseSpec`, `DiscoveryError`, `RunnerResult`, `EffectiveConfig`, `SetupResult`, `TestRunResult` are defined exactly once each (in their respective modules) and consumed everywhere by name.
- `FORBIDDEN_OUTPUT_KEYS = {"fixed", "not_fixed", "status"}` matches the prohibition in `task-spec.md` line 111.
- The error-reason precedence table values (`timeout`, `framework_misconfigured`, `nonzero_exit`, `missing_response`, `malformed_response_json`, `envelope_schema_violation`) appear consistently across `runner.py`, `pipeline.py`, the fake framework's behaviors, and the integration test parametrization.
- `STDOUT_CAP_BYTES = 8 MiB`, `STDERR_CAP_BYTES = 5 MiB`, `TEST_OUTPUT_CAP_BYTES = 5 MiB` match the spec's caps exactly.
- The `effective_config.sources` field values (`framework-manifest`, `campaign`, `cell-flag`, `harness-default`) match spec lines 297-304 verbatim.

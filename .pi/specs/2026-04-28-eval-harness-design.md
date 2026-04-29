# Eval Harness Design

## Goal

Design the framework-agnostic eval harness that lives in `evals/` and runs every framework in `frameworks/` against every case in `cases/` through the contract defined in `shared/contract.md`, scoring per the categories defined in `shared/task-spec.md`. V1 is sequential, resumable, and produces a per-campaign markdown report. The matrix is small (≤8 frameworks × a handful of cases) so concurrency is not in scope for v1, but the design must extend cleanly to it.

## Context

Three things are already pinned by prior specs and existing artifacts:

- **`shared/contract.md`** defines the framework-agnostic transport: subprocess + JSON over stdin/stdout, one request envelope in (`task_id`, `input`, `config`), one response envelope out (`task_id`, `output`, `trace`, `error`). On failure: non-zero exit, error JSON to stdout, logs to stderr. Each framework provides an entry point declared in `frameworks/<name>/manifest.json` and owns its dependency management.
- **`shared/task-spec.md`** defines the v1 software bugfix benchmark: each case ships a fixture repo, a failing test command, captured failure output, optional hidden test command, and optional edit constraints. The harness derives a fresh worktree per `(framework, run)`, the agent edits in place, and the harness re-runs the test to derive the canonical visible outcome. Spec also enumerates scoring categories with no aggregate score in v1.
- **Cases and fixtures already exist** at `cases/<case_id>.json` and `fixtures/<case_id>/`. The bootstrap case (`py-parse-duration-001`) and three SWE-bench Verified cases (`psf__requests-1921`, `pylint-dev__pylint-7080`, `pytest-dev__pytest-7571`) are the v1 starter set. The repo's authoritative case manifest schema lives in `task-spec.md`.

The harness itself is the missing piece: `evals/` currently contains only a `pyproject.toml` stub and a TODO README. This spec defines what fills it in.

## Out of Scope

- Implementing per-framework adapters (each framework dir owns its own `run.sh` / `setup.sh`; v1 ships stub scripts that exit non-zero so `eval-all` runs end-to-end on day one).
- Concurrent execution. Sequential v1; the design extends cleanly to parallelism (see "Parallelism Notes" near the end).
- LLM-as-judge trace quality scoring. Traces are captured verbatim in v1; rubric grading is a deferred `just judge-traces` command.
- A weighted leaderboard / single aggregate score. Per-category reporting only, per `task-spec.md`.
- Authoring new cases or fixtures — those follow `.pi/specs/2026-04-28-bootstrap-fixture-design.md` and `.pi/specs/2026-04-28-swebench-fixture-conversion.md`.
- Real framework dependency installs. Stub setup hooks make `eval-prepare` succeed without doing real work in v1.

## Architecture

The harness lives in `evals/` and never imports framework code. It interacts with the rest of the repo through three boundaries:

```
┌─────────────────────────────────────────────────────────────────────┐
│  evals/  (this design)                                              │
│                                                                     │
│  CLI ──▶ campaign mgr ──▶ run executor ──▶ subprocess (framework)   │
│                  │              │                                   │
│                  ▼              ▼                                   │
│            workspace mgr   diff/test/scoring                        │
│                  │              │                                   │
│                  ▼              ▼                                   │
│           .runs-cache/      runs/CURRENT/<fw>/<case>/               │
└─────────────────────────────────────────────────────────────────────┘
        │                    │                   │
        ▼                    ▼                   ▼
   fixtures/<case>/     cases/<case>.json    frameworks/<name>/manifest.json + entry
   (read-only source)   (manifest)           (subprocess target)
```

### Immutable inputs

- `cases/<case_id>.json` — case manifest (schema in `task-spec.md`).
- `fixtures/<case_id>/` — pristine fixture files; harness never edits.
- `frameworks/<name>/manifest.json` + entry script — framework adapter.

### Harness-owned artifact spaces

- `.runs-cache/` — gitignored; lazily-built derived state.
- `runs/<timestamp>/` — campaign dirs; per-cell results.
- `runs/CURRENT` — relative symlink to the active campaign.

### Top-level data flow for one cell `(framework=F, case=C)`

1. Workspace manager ensures `.runs-cache/<C>.git/` exists (layer 1) and `.runs-cache/<C>.venv/` exists (layer 2), then `git clone --local` into `runs/CURRENT/<F>/<C>/repo/` (layer 3).
2. Run executor builds the contract request, spawns `frameworks/<F>/<entry>` with the request on stdin, captures stdout/stderr with an external timeout.
3. Post-subprocess pipeline derives the canonical diff (`git diff HEAD` in the cell repo), reruns the visible test command, optionally the hidden test, and validates edit constraints.
4. The cell's `meta.json` is written last as the done-sentinel.

## Workspace Lifecycle

Three layered workspaces, each with a single owner and a single rule for when it's built or rebuilt.

### Layer 1 — Per-case bare git repo: `.runs-cache/<case_id>.git/`

- Built lazily on first reference (or eagerly by `eval-prepare`).
- Construction: `git init --bare` in a tempdir; in a sibling `.work/` dir, `cp -r fixtures/<case_id>/`, `git add -A`, `git commit -m "fixture: <case_id> @ <fixture-content-hash>"`, `git push` into the bare repo. Tempdir discarded.
- The commit message embeds a content hash of the fixture tree. The harness writes the same hash to `.runs-cache/<case_id>.fixture-hash` so it can detect "fixture changed since this `.git/` was built" and rebuild automatically.
- Rebuild trigger: fixture content hash changes. Otherwise reused indefinitely.

### Layer 2 — Per-case shared venv: `.runs-cache/<case_id>.venv/`

- Built lazily on first reference (or eagerly by `eval-prepare`).
- Construction: a `uv sync` invocation rooted at `fixtures/<case_id>/` with `UV_PROJECT_ENVIRONMENT` pointing at the absolute path of `.runs-cache/<case_id>.venv/`. Exact uv flag combination to be confirmed during implementation.
- Rebuild trigger: hash of `fixtures/<case_id>/uv.lock` (or `pyproject.toml` if no lock file exists) changes. The harness writes the last-built hash to `.runs-cache/<case_id>.lock-hash`.
- All test reruns by the harness, and the agent's own `uv run pytest` invocations, use this venv via `UV_PROJECT_ENVIRONMENT`.
- **Read-only at runtime** is documented but not filesystem-enforced in v1. If a framework's agent decides to install into it, that agent's run is at fault; v1 trusts agents not to.

### Layer 3 — Per-`(framework, run)` worktree: `runs/CURRENT/<framework>/<case>/repo/`

- Built fresh per cell run: `git clone --local .runs-cache/<case_id>.git runs/CURRENT/<F>/<C>/repo`.
- This is the agent's mutable sandbox. It has its own working tree and `.git/`; `git diff HEAD` after the agent exits gives the canonical diff.
- Lifetime: from cell run start to cell run end. **Not destroyed on completion** — left in place so the user can inspect what the agent did. `eval-new` and rerunning the cell both wipe-and-rebuild.
- This is a deliberate deviation from `task-spec.md`'s "destroy it after scoring" line. Documented here; rationale is debuggability.

### Cleanup commands

- `just eval-clean-cache` — wipes `.runs-cache/` (forces full rebuild on next run).
- `just eval-clean-runs` — wipes `runs/`.
- No automatic cleanup; both are user-invoked.

## Framework Manifest, Invocation, Env Handling

### Manifest schema (`frameworks/<name>/manifest.json`)

```json
{
  "entry": "./run.sh",
  "setup": "./setup.sh",
  "env": ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"],
  "model": "claude-sonnet-4-6"
}
```

- `entry` (required) — repo-relative-to-framework-dir command. Executed with cwd = `frameworks/<name>/`.
- `setup` (optional) — same shape; run once by `eval-prepare`. Sentinel at `.runs-cache/setup/<framework>.ok` includes a hash of `manifest.json` + the `setup` script + a per-framework lockfile glob (e.g., `uv.lock`, `package-lock.json`) so changes auto-trigger re-setup.
- `env` (required) — list of env var names that survive scrubbing. Empty array is fine.
- `model` (required) — default model id baked into `config.model` of the request envelope.

The harness validates the manifest at startup using a JSON schema. A missing or malformed manifest causes the cell to error out as `framework_misconfigured`.

### Subprocess invocation

For one cell `(F, C)`:

1. Harness builds the request:
   ```json
   {
     "task_id": "<F>:<C>:<8-char uuid>",
     "input": {
       "case_id": "<C>",
       "repo_path": "<absolute path to layer 3 worktree>",
       "failing_test_command": "...",
       "failure_output": "<resolved from failure_output_path if used>",
       "edit_constraints": { /* per task-spec.md, with defaults applied */ }
     },
     "config": {
       "model": "<from manifest, possibly --model overridden>",
       "max_steps": "<flag or default 50>",
       "timeout_s": "<flag or default 120>"
     }
   }
   ```
2. Spawns the entry command with:
   - cwd = `frameworks/<F>/`
   - stdin: the request JSON, then EOF.
   - env: scrubbed environment containing only declared `env` vars from the manifest (sourced from process env + auto-loaded `.env`); plus `UV_PROJECT_ENVIRONMENT=<absolute path to .runs-cache/<C>.venv>`; plus `PATH`, `HOME`, `LANG`, `TERM`.
   - stdout: captured to memory, capped at 8 MiB. Over → `malformed_response_json`.
   - stderr: streamed to `<cell>/stderr.log`, capped at 5 MiB. Over → truncated; truncation noted in `meta.json`.
3. External timeout via `subprocess` with `timeout=config.timeout_s`: SIGTERM at deadline, 5-second grace, SIGKILL.
4. After exit:
   - Exit code → `meta.json`.
   - Stdout parsed; envelope schema validated; one of: success, `nonzero_exit`, `malformed_response_json`, `envelope_schema_violation`, `missing_response`, `timeout`, `framework_misconfigured`.

### Why scrub env

Two reasons:
1. Prevents one framework's API keys leaking into another framework's process.
2. Makes runs reproducible — every framework sees the same minimal env regardless of what the user has exported. The list of declared env vars is auditable in the manifest.

### Secrets sourcing

The harness loads `.env` at the repo root if present (gitignored), merges it with the process environment, and forwards only the declared `env` vars to the subprocess. Implementation uses `python-dotenv` or an equivalent simple parser.

### Stub `run.sh` / `setup.sh` for every framework dir

V1 includes minimal stub scripts in every `frameworks/<name>/` that exit non-zero with a "not implemented" message. This makes `just eval-all` runnable end-to-end on day one with every cell reporting `nonzero_exit`. Real framework adapters fill in these scripts as separate follow-on work.

## Post-Subprocess Pipeline

After the framework subprocess exits, the harness runs a deterministic pipeline. Every step is independent of the framework — pure functions over the worktree, the response, and the case manifest.

### Step 1 — Capture and validate response

- Parse stdout. On parse fail → record `malformed_response_json`. Skip remaining steps that depend on `output`, but still run steps that depend only on the worktree.
- Validate the contract envelope using a JSON schema. Miss → `envelope_schema_violation`, same partial-skip rule.
- Validate the agent `output` against `task-spec.md`'s schema (including the prohibition on top-level `fixed`/`not_fixed`/`status` keys). **Non-fatal**: the cell's `scoring.json` records `schema_validity: false`; pipeline continues.

### Step 2 — Derive canonical diff

- `git -C <cell>/repo add -A` (so untracked files are included).
- `git -C <cell>/repo diff --cached HEAD` → write to `<cell>/diff.patch`.
- `git -C <cell>/repo diff --cached HEAD --name-only` → canonical changed-file list.
- Compute `+/-` line counts.

This works whether or not the framework crashed — the worktree is the source of truth.

### Step 3 — Visible test rerun

- Spawn `failing_test_command` from the case manifest, cwd = `<cell>/repo`, env = same scrubbed env (with `UV_PROJECT_ENVIRONMENT` for the case venv).
- External timeout: same as agent timeout for v1. Captured exit code + stdout + stderr → `<cell>/visible_test.json`.
- Outcome: `pass` (exit 0) | `fail` (nonzero, finite output) | `error` (timeout, signal).

### Step 4 — Hidden test rerun (if case has one)

- Identical to step 3 with `hidden_test_command`. Result → `<cell>/hidden_test.json` and `hidden_test_outcome`.
- If case has none: `hidden_test_outcome: "n/a"`, no file written.

### Step 5 — Edit constraint check

- Resolve effective constraints: merge case `edit_constraints` with `task-spec.md` defaults (defaults fill missing fields).
- Match canonical changed-file list against `disallowed_paths` and `allowed_paths` using the `pathspec` library (gitignore-style globs).
- Check `len(changed_files) <= max_changed_files`.
- Result → `edit_constraint_compliance` object: `{ disallowed_violations: [...], allowed_violations: [...], over_max_changed_files: bool }`.

### Step 6 — Assemble scoring

Build `<cell>/scoring.json` with the categories from `task-spec.md`:

- `schema_validity` — bool (from step 1).
- `visible_test_outcome` — `pass` | `fail` | `error` (from step 3).
- `hidden_test_outcome` — `pass` | `fail` | `error` | `n/a` (from step 4).
- `edit_constraint_compliance` — object (from step 5).
- `minimality` — `{ changed_files, changed_lines_added, changed_lines_removed }` (from step 2).
- `latency_ms` — harness wall-clock from request send to response receive.
- `token_usage` — `{ input, output }` from response `trace.tokens` if present, else omitted.
- `trace_quality` — `"n/a"` in v1.

### Step 7 — Write meta and sentinel

`<cell>/meta.json` is **written last** as the done-sentinel. Contents:

```json
{
  "framework": "...",
  "case_id": "...",
  "task_id": "<F>:<C>:<uuid>",
  "model": "...",
  "started_at": "<iso8601>",
  "ended_at": "<iso8601>",
  "status": "ok" | "error",
  "error_reason": null | "timeout" | "nonzero_exit" | "malformed_response_json"
                       | "envelope_schema_violation" | "missing_response"
                       | "framework_misconfigured",
  "exit_code": "<int>",
  "stderr_truncated": "<bool>",
  "stdout_truncated": "<bool>",
  "harness_latency_ms": "<int>",
  "framework_reported_latency_ms": "<int|null>"
}
```

Resume logic: a cell is "done" iff `meta.json` exists. Anything else (lone `request.json`, partial `diff.patch`) means a crash; resume blows away the dir and reruns.

### Partial-failure visibility

When the agent crashes or times out, steps 2–5 still run. The cell ends up with the diff of whatever the agent edited before crashing, the test outcome on the partially-edited worktree, and the constraint check on whatever files it touched. `meta.status: "error"` plus `error_reason` makes the report attribute the failure correctly; the artifacts are there for inspection.

## Campaign + Storage Layout

### Top-level dirs (all repo-root, gitignored)

```
.runs-cache/                          # harness-derived
├── <case_id>.git/                    # bare git repo per case (layer 1)
├── <case_id>.venv/                   # shared venv per case (layer 2)
├── <case_id>.fixture-hash             # last-built fixture content hash
├── <case_id>.lock-hash                # last-built uv.lock hash
└── setup/<framework>.ok               # per-framework setup sentinel + manifest hash

runs/                                 # campaign artifacts
├── CURRENT -> 2026-04-29T14-32-08/   # relative symlink
├── 2026-04-29T14-32-08/              # one campaign
│   ├── .lock                         # campaign lockfile
│   ├── manifest.json                 # campaign manifest
│   ├── report.md                     # generated by eval-all + eval-report
│   └── <framework>/<case>/           # one cell
│       ├── request.json
│       ├── response.json             # raw stdout
│       ├── stderr.log
│       ├── diff.patch
│       ├── visible_test.json
│       ├── hidden_test.json          # only when case has hidden_test_command
│       ├── scoring.json
│       ├── meta.json                 # written last (sentinel)
│       └── repo/                     # the layer-3 worktree, kept for inspection
└── 2026-04-29T11-08-45/              # earlier campaign, immutable
```

### Campaign manifest: `runs/<ts>/manifest.json`

Captured at `eval-new` and never mutated:

```json
{
  "started_at": "<iso8601>",
  "git_sha": "<HEAD sha at start>",
  "git_dirty": "<bool>",
  "git_remote_url": "<git remote get-url origin, omitted if none>",
  "git_branch": "<git rev-parse --abbrev-ref HEAD, omitted if detached>",
  "frameworks": ["..."],
  "cases": ["..."],
  "config_overrides": { "model": null, "timeout_s": null, "max_steps": null }
}
```

`frameworks` and `cases` are the *discovered set at start*. If a framework dir is added mid-campaign it is not part of *this* campaign — it shows up in the next `eval-new`. `eval-status` and the report only consider the manifest's matrix.

### Campaign lockfile: `runs/CURRENT/.lock`

JSON: `{ "pid": <int>, "hostname": "...", "started_at": "<iso8601>", "argv": [...] }`.

On any harness command that writes to the campaign:
- If the file exists *and* the recorded PID is alive on the same host: refuse with `Campaign in use by PID N (since X). Delete <path> if stale.`
- If the file exists but the PID is dead or the hostname differs: treat as stale and reclaim, after warning.
- Held campaign-wide in v1. When parallelism is added, this degrades to a per-cell claim layer, with the campaign-wide lock still held for `eval-new` and report writes.

### `runs/CURRENT` symlink details

- Relative symlink (so the repo can be moved or cloned).
- Created or updated atomically: write `runs/CURRENT.tmp -> <new>`, then `rename`.
- Unix-only in v1; Windows is undocumented and not supported.
- `eval-status` does `readlink runs/CURRENT` to find the active campaign.

### `.gitignore` delta

The repo's existing `.gitignore` already covers `.env` and `.env.*`. Add:

```
.runs-cache/
runs/
```

Existing `.gitignore` also lists `results/` from earlier scaffolding; the eval harness does not use that path. Cleaning it up is harmless and can be done as part of this work.

### No auto-cleanup

Old campaigns are never auto-deleted. `just eval-clean-runs` wipes all of `runs/`; selective pruning (`rm -rf runs/<ts>`) is left to the user.

## CLI Surface and Module Layout

### Verbs (via `justfile`, all delegating to `evals/__main__.py`)

| verb | description |
| --- | --- |
| `just frameworks` | list framework dirs (already exists) |
| `just cases` | list case ids and which fixtures back them |
| `just eval-prepare` | run all framework `setup`s, materialize `.runs-cache/<case>.git/` and `<case>.venv/`. Idempotent. |
| `just eval-new` | create `runs/<ts>/`, write `manifest.json`, repoint `runs/CURRENT` |
| `just eval-all` | fill missing cells in `runs/CURRENT`. Auto-runs `prepare` and `new` if needed. |
| `just eval <fw> <case>` | run/rerun one cell |
| `just eval-status` | print matrix of filled / missing / error per cell in `CURRENT` |
| `just eval-report` | regenerate `runs/CURRENT/report.md` |
| `just eval-clean-cache` | wipe `.runs-cache/` |
| `just eval-clean-runs` | wipe `runs/` |

### Flags on `eval-all` and `eval`

- `--model <id>` — override per-framework manifest model.
- `--timeout-s <n>` — override `config.timeout_s`.
- `--max-steps <n>` — override `config.max_steps`.
- `--framework <name>` and `--case <id>` — restrict the matrix on `eval-all` (additive — pass either or both).

### Auto-behaviors

- `eval-all` on a cohort with no `runs/CURRENT` auto-runs `eval-new` first.
- `eval-all` auto-runs `eval-prepare` if any setup sentinel is missing or stale.
- `eval` with no args errors with usage.

### Module layout in `evals/`

```
evals/
├── pyproject.toml         # already exists; deps: pathspec, python-dotenv
├── README.md              # already exists; rewrite to match
└── evals/
    ├── __main__.py        # CLI entry (argparse subcommands)
    ├── cli.py             # subcommand dispatch
    ├── discovery.py       # find frameworks/<name>/manifest.json, cases/*.json
    ├── workspace.py       # layers 1, 2, 3 — bare git, venv, per-run worktree; cache hashes
    ├── runner.py          # one cell: build request, spawn, capture, timeout
    ├── pipeline.py        # post-subprocess: diff, test reruns, edit constraint, scoring
    ├── campaign.py        # eval-new, CURRENT pointer, lockfile, campaign manifest
    ├── status.py          # eval-status renderer
    ├── report.py          # eval-report renderer (markdown)
    ├── env.py             # .env loading, env scrubbing for subprocess
    └── schemas.py         # JSON schemas: framework manifest, case manifest, contract envelope, agent output
```

No circular deps: `cli` → `campaign` / `status` / `report` / `runner` / `pipeline` / `workspace` → `discovery` / `env` / `schemas`.

CLI uses stdlib `argparse` rather than `click` or `typer` to keep deps minimal. Only added third-party deps are `pathspec` and `python-dotenv`.

## Reporting

### Generation timing

- Auto-generated at the end of every `eval-all` and after any single-cell `eval` run.
- Also exposed as `just eval-report` for ad hoc regeneration after manual edits.

### Shape

One markdown file per campaign at `runs/<ts>/report.md`, accessible as `runs/CURRENT/report.md`. Single file is the right ergonomic — readable in editor, on GitHub, in terminal.

### Content (v1, expected to be tuned after first real campaign)

```markdown
# Campaign <timestamp>

Model overrides: <model or "default per framework manifest">
Cases: N — <case ids>

## Per-cell results

| framework | case | visible | hidden | edit_compl. | files | +/- lines | latency | tokens (i/o) | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ... | ... | ... | ... | ... | ... | ... | ... | ... | ... |

## Per-framework summary

| framework | cases run | visible pass | hidden pass | mean latency | total tokens (i/o) | errors |
| --- | --- | --- | --- | --- | --- | --- |
| ... | ... | ... | ... | ... | ... | ... |

## Notes

- <typed failure summaries with links to <cell>/stderr.log>
- trace_quality: n/a in v1 (capture-only)
```

Per `task-spec.md`, no aggregate ranking score. The per-framework summary is descriptive only.

Diff snippets are not embedded in the report; the report links to `<cell>/diff.patch` instead. Same for failure stderr. The report is a navigation index plus the comparative table; source-of-truth artifacts live in the cell dirs.

## Testing the Harness

### Test framework

`pytest`, in `evals/tests/`.

### Fake framework — `evals/tests/fixtures/fake-framework/`

A canonical "framework" the test suite controls end-to-end. A Python `run.sh` reads the request from stdin and emits a response according to a `FAKE_BEHAVIOR` env var the harness test sets:

| `FAKE_BEHAVIOR` | what it does |
| --- | --- |
| `success-noop` | valid envelope, no edits, schema-valid output |
| `success-fix` | apply a hard-coded fix to the per-run repo, valid envelope |
| `hang` | sleep forever (test timeout path) |
| `crash` | exit 1 with stderr (test `nonzero_exit`) |
| `garbage` | write non-JSON to stdout (test `malformed_response_json`) |
| `missing-field` | valid JSON, missing `trace` (test `envelope_schema_violation`) |
| `forbidden-field` | output contains top-level `fixed` (test `schema_validity=false`, non-fatal) |
| `disallowed-edit` | edit `tests/foo` (test `edit_constraint_compliance`) |

Plus a manifest declaring `entry`, no `setup`, and `model: "fake"`. Tests dispatch the harness against this fake to drive every code path in `runner` and `pipeline`.

### Synthetic case fixture — `evals/tests/fixtures/cases/test-case-001/`

Tiny: one source file with a known bug, one failing test, one optional hidden test. Used by the fake framework's `success-fix` mode. Lets the suite assert end-to-end on:

- Layer 1 bare-repo construction, content-hash detection, rebuild-on-change.
- Layer 2 venv build (marked integration; skipped if `uv` unavailable).
- Layer 3 worktree clone, mutation, diff derivation.
- Visible/hidden test reruns producing the expected outcomes.

### Module-level unit tests

- `discovery_test.py` — finds frameworks, finds cases, errors on malformed manifest.
- `schemas_test.py` — validates known-good and known-bad envelopes against schemas.
- `env_test.py` — `.env` loading, env scrubbing produces the right set.
- `workspace_test.py` — content-hash rebuild trigger, idempotent prepare, concurrent-safe layer 3 clone.
- `pipeline_test.py` — fed canned `(response, worktree_state, case)` tuples, asserts `scoring.json` shape and contents.
- `report_test.py` — golden-file test of report rendering against a synthetic campaign on disk.
- `campaign_test.py` — `eval-new` creates dir + manifest + symlink atomically; lockfile semantics.

### Integration tests — `evals/tests/integration/`

End-to-end via subprocess against the fake framework + synthetic case. One test per `FAKE_BEHAVIOR` value, asserting:

- `meta.json` reaches the expected `status` and `error_reason`.
- Pipeline steps that should still run on failure actually do.
- The report regenerates without crashing for any of these states.

Slower; runnable with `pytest -m integration`.

### Coverage target

No coverage number, but the integration suite must hit every `error_reason` value and every `scoring.json` field.

### Layer 2 caveat

Tests that exercise real `uv sync` against fixture pyproject files require `uv` and possibly network. They're marked integration; the unit suite stubs `workspace.ensure_case_venv` to a no-op and tests it separately.

## Parallelism Notes (Future)

The design extends cleanly to parallelism without retro-changes:

- Cells are already independent units (worktree per `(framework, run)`, dir per `(framework, case)`).
- `git clone --local` from `.runs-cache/<case>.git/` is concurrent-safe.
- The shared per-case venv is fine for concurrent reads (running pytest); concurrent writes (`uv add` etc) are an out-of-scope agent-side bug.
- `runs/CURRENT` is read during runs and only written by `eval-new`.
- Per-cell directories are independent FS writes.

Three things to add when parallelism ships:

1. Pre-flight: a `prepare` step (already in v1) that materializes `.runs-cache/<case>.git/` and `<case>.venv/` for every case in the matrix sequentially, before parallel work starts.
2. Cell-claim atomicity: enumerate the work list up front and hand cells out from a queue. No per-cell file locks needed if dispatch is done from a single coordinator.
3. Done-sentinel discipline: already baked in. Treat a cell as "done" only when `meta.json` is present.

Optional knobs at that point: `--max-concurrency`, per-provider semaphores for API rate limits.

## Acceptance Criteria

- `evals/` contains the modules listed in "Module layout" with the responsibilities described.
- `just eval-all` on a fresh clone (with stub framework scripts) runs end-to-end: prepares the cache, creates a campaign, fills every cell with `status: "error"` and `error_reason: "nonzero_exit"`, and generates a report.
- Workspace lifecycle: layer 1 / 2 / 3 build and rebuild on the documented triggers. Re-running `eval-prepare` after no changes does no work.
- Pipeline: every typed `error_reason` is reachable and recorded correctly via the fake-framework integration suite. Schema-validity violations of agent `output` are non-fatal.
- Storage: `runs/CURRENT` symlink, campaign `manifest.json`, lockfile semantics (refuse / reclaim) work as specified.
- CLI: every verb in the table runs and does what its row says. Targeted `--framework` / `--case` filtering on `eval-all` works. Re-running `eval <fw> <case>` overwrites that cell.
- Reporting: `report.md` regenerates idempotently from cell artifacts.
- Tests: unit suite passes without `uv`; integration suite passes with `uv` available and exercises every `error_reason` and every scoring field.
- The harness never imports framework code; it only invokes per-framework entry scripts as subprocesses.
- The `repo/` worktree is left in place after each cell run; deviation from `task-spec.md` is documented in this spec.

## Open Implementation Details

To resolve during the implementation plan rather than now:

- Exact `uv sync` flag combination for layer 2 venv build; verify that `UV_PROJECT_ENVIRONMENT` plus a project pointer is the cleanest invocation.
- Hashing algorithm and salt for fixture content hash and lock hash (likely a simple BLAKE2 over a sorted file list).
- JSON schema files: inline as Python dicts vs. shipped as `.schema.json`.
- Whether the `cases` verb reads `cases/*.json` directly or goes through `discovery.py`.

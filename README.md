# Agent Harness Evals

A framework-agnostic evaluation harness for comparing agent frameworks on software bugfix tasks. Each framework receives the same repository worktree, failing test command, captured failure output, and edit constraints; the harness then independently derives the diff, reruns tests, checks constraints, and writes comparable results.

The main design goal is to keep framework implementations isolated while making their outputs comparable. Framework adapters live under `frameworks/`, fixture cases live under `cases/` + `fixtures/`, and the shared JSON-over-stdio contract lives under `shared/`.

> Current status: the Python harness and fixture/case structure are implemented. The framework directories currently contain v1 adapter stubs (`manifest.json` + `run.sh`) and per-framework TODO READMEs.

## How the codebase is organized

```
.
├── justfile       # top-level commands for discovery, setup, campaigns, and cleanup
├── shared/        # benchmark contract and task spec shared by every framework
├── cases/         # case manifests and captured failure-output sidecars
├── fixtures/      # source repositories used as immutable case inputs
├── frameworks/    # one independent adapter directory per agent framework
└── evals/         # framework-agnostic harness package and tests
```

### `shared/`

The source of truth for interoperability:

- `shared/contract.md` defines the transport envelope: one JSON request on stdin, one JSON response on stdout.
- `shared/task-spec.md` defines the software-bugfix task, agent input/output schemas, edit constraints, harness responsibilities, and scoring categories.

Framework adapters should depend on these documents, not on harness internals.

### `cases/` and `fixtures/`

A case is the unit of evaluation. Each case has:

- `cases/<case_id>.json` — manifest with the fixture path, visible failing test, optional hidden test, edit constraints, and private notes.
- `cases/<case_id>.failure_output.txt` — captured failure output shown to the agent.
- `fixtures/<case_id>/` or another fixture directory — the repository materialized into isolated per-run worktrees.

Included cases currently cover a small synthetic parse-duration bug plus SWE-bench-style fixtures for `requests`, `pylint`, and `pytest`.

### `frameworks/`

Each framework gets a fully independent directory with its own runtime, dependencies, lockfile, README, and entry point. The harness only reaches in through `manifest.json`:

- `entry` — command to execute, usually `./run.sh`.
- `env` — required environment variables.
- `model` — default model identifier.
- `setup` — optional setup command.

Frameworks in scope are DeepAgents, Pydantic AI, Google ADK, Amazon Strands, Amazon Bedrock AgentCore, Claude Agent SDK, OpenAI Agents SDK, and Mastra.

### `evals/`

The Python harness package. Important modules:

- `discovery.py` — finds framework manifests and case manifests.
- `workspace.py` — builds cached bare repos, case venvs, and per-cell worktrees.
- `setup.py` — runs optional framework setup commands with success/failure sentinels.
- `campaign.py` — creates timestamped campaigns under `runs/` and maintains `runs/CURRENT`.
- `runner.py` — invokes one framework/case cell through the shared contract.
- `pipeline.py` — derives diffs, reruns visible/hidden tests, checks edit constraints, and writes metadata.
- `report.py` and `status.py` — render campaign reports and matrix status.
- `schemas.py` — validates manifests, envelopes, and agent outputs.

Harness tests live in `evals/tests/`.

## Evaluation flow

1. **Discover** framework adapters from `frameworks/*/manifest.json` and cases from `cases/*.json`.
2. **Prepare** cached case layers in `.runs-cache/`: bare repositories and fixture virtualenvs. Optional framework setup commands run here too.
3. **Create a campaign** in `runs/<timestamp>/` and point `runs/CURRENT` at it.
4. **Run each cell** in the campaign matrix: clone an isolated worktree, send the contract request to the framework adapter, capture stdout/stderr, and classify adapter failures.
5. **Score the result** from harness-observed state: canonical diff, visible test outcome, hidden test outcome, edit-constraint compliance, minimality, trace, latency, and token usage.
6. **Report** results to `runs/CURRENT/report.md`.

The agent's self-report is informational. The harness treats the worktree diff and rerun test results as authoritative.

## Quickstart

Prerequisites: `just`, `uv`, and Python 3.11+. Framework adapters may require additional per-framework credentials or dependencies once implemented.

```sh
just                 # list available commands
just frameworks      # list discovered framework adapters
just cases           # list discovered cases
just eval-prepare    # build case caches and run framework setups
just eval-new        # create a fresh campaign under runs/
just eval-all        # fill missing cells in runs/CURRENT
just eval-status     # print the current campaign matrix
just eval-report     # regenerate runs/CURRENT/report.md
```

Useful variants:

```sh
just eval-new --model <model-id> --timeout-s 120 --max-steps 50
just eval-all --framework <name> --case <case_id>
just eval <framework> <case_id> --timeout-s 300
just eval-clean-cache
just eval-clean-runs
```

## Development

Run harness tests from the `evals/` package:

```sh
cd evals
uv run pytest
```

Integration tests are marked separately:

```sh
cd evals
uv run pytest -m integration
```

## Adding a framework adapter

1. Create `frameworks/<name>/`.
2. Add `manifest.json` with at least `entry`, `env`, and `model`.
3. Implement the entry command so it reads the shared request envelope from stdin and writes exactly one response envelope to stdout.
4. Provide file inspection, search, editing, test execution, and diff inspection capabilities to the framework-native agent.
5. Add a framework README documenting setup, model choice, required env vars, and quirks.

See `shared/contract.md`, `shared/task-spec.md`, and `frameworks/README.md` for details.

## Adding a case

1. Add or import a fixture repository under `fixtures/`.
2. Add `cases/<case_id>.json` with the fixture path, visible failing test, captured failure output path, optional hidden test, and edit constraints.
3. Add `cases/<case_id>.failure_output.txt` with the clean failing-test output shown to agents.
4. Verify the visible test fails on the pristine fixture, the intended patch passes, and hidden tests catch important under-fixes when practical.

See `cases/README.md` for the detailed case-authoring process.

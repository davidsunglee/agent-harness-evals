# deepagents

DeepAgents (Python, on top of LangGraph) — adapter for the agent shootout's software-bugfix benchmark.

## Model

- Default: `claude-sonnet-4-6` (Anthropic).
- Anthropic-only for v1. Cross-provider overrides (OpenAI, Bedrock, etc.) are deferred — see `.pi/specs/2026-04-29-deepagents-adapter.md`.
- The harness may pass another Anthropic model via `--model claude-...`; the adapter forwards it as `init_chat_model("anthropic:<model>")` without further validation.

## Setup

- Manifest setup command: `uv sync --frozen` (run once by the harness during `just eval-prepare`).
- Dependencies: `deepagents`, `langchain`, `langchain-anthropic`. Locked in `uv.lock`.
- Python: `>= 3.11` (this directory pins `3.12` via `.python-version`).

## Environment variables

- `ANTHROPIC_API_KEY` — required. Forwarded by the harness from the calling shell or repo-root `.env`.

## Tool wiring

- **Filesystem and shell**: see the comment at the top of `adapter.py` for which deepagents backend was chosen at implementation time.
  - **Approach A** (default): `LocalShellBackend` constructed with the cwd parameter pinned to `input.repo_path`. The agent gets DeepAgents' canonical tool set: `ls`, `glob`, `grep`, `read_file`, `write_file`, `edit_file`, `execute`. Selected only when the constructor parameter forwards to subprocess `cwd=` AND the built-in filesystem methods resolve paths against the same parameter.
  - **Approach B** (contingency): `FilesystemBackend(root_dir=input.repo_path)` plus a custom `@tool def shell(command)` that calls `subprocess.run(..., cwd=input.repo_path, env=os.environ.copy())` with a per-call timeout derived from the adapter's remaining `config.timeout_s` deadline. Used when `LocalShellBackend` does not expose a usable working-directory knob, does not forward it to subprocess, or its filesystem tools are not rooted by it.
- **Soft deadline**: the adapter derives a soft deadline from `config.timeout_s` (`max(5.0, timeout_s - 5.0)` seconds) and arms a `SIGALRM`-based timer around `agent.invoke`. On timeout the adapter raises an internal exception, catches it, and emits a contract-valid envelope with `error.message` set — *before* the harness's outer hard-kill fires. Approach B's shell tool also caps `subprocess.run` timeout to the remaining deadline.
- **Structured report**: a callable tool `submit_report` whose Pydantic schema mirrors `shared/task-spec.md`'s `output` block (`root_cause`, `summary`, `changed_files`, `tests_run[]`, `evidence`, `confidence`). The agent must call it once at the end of the run; if it calls more than once, last-call-wins. If it never calls it, the adapter emits an envelope with `error.message = "agent did not call submit_report"` and exits non-zero.

## Capabilities (per `shared/task-spec.md`)

- File inspection — `ls`, `read_file`.
- File search — `glob`, `grep`.
- File editing — `write_file`, `edit_file`.
- Test execution — `execute` (Approach A) or `shell` (Approach B).
- Diff inspection — `git diff` via the same shell/execute tool.

## LocalShellBackend cwd & env contingency

`LocalShellBackend` inherits the parent process env by default. The harness builds the agent's env via `build_agent_env` (in `evals/evals/env.py`) and prepends `<case-venv>/bin` to `PATH`, then passes that env to the adapter subprocess. DeepAgents propagates that env through to its subprocess `pytest`/`git` invocations — so the harness-prepared `PATH` reaches them automatically. If a future deepagents change blocks this propagation, the contingency is to pass `env=os.environ.copy()` explicitly at the shell-tool call site (Approach B already does this).

## Constraints honored by the agent

- The agent does not commit, reset, or otherwise mutate `.git/` in `input.repo_path` — diff derivation is the harness's responsibility.
- The agent does not run `pip install`, `uv sync`, `uv add`, or any command that would mutate the harness-owned case venv. Tests use the venv on `PATH` only.
- The agent respects `edit_constraints.disallowed_paths` (gitignore-style globs blocking edits to tests, fixtures, lockfiles, `.git/`, etc.).

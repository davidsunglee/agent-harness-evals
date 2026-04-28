# evals

Framework-agnostic eval harness. Discovers entries in `../frameworks/`, invokes them through the contract in `../shared/contract.md`, and scores them against `../shared/task-spec.md`.

## Run

```sh
uv sync
uv run python -m evals run --all
uv run python -m evals run --framework deepagents
```

## Layout (TODO — implement)

- `evals/__main__.py` — CLI entry
- `evals/runner.py` — invokes a framework via its manifest
- `evals/scoring.py` — grades outputs against the spec
- `evals/report.py` — comparative table across frameworks

_default:
    @just --list

# Run the eval harness against every framework
eval-all:
    cd evals && uv run python -m evals run --all

# Run the eval harness against a single framework (e.g. `just eval deepagents`)
eval framework:
    cd evals && uv run python -m evals run --framework {{framework}}

# Per-framework setup is owned by each framework dir. List them.
frameworks:
    @ls frameworks

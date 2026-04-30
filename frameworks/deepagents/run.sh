#!/bin/sh
set -eu
cd "$(dirname "$0")"

# The harness uses UV_PROJECT_ENVIRONMENT to point test commands at the
# case-owned venv. Do not let this adapter's own `uv run` consume that venv;
# preserve it under an adapter-specific name for shell tools instead.
if [ -n "${UV_PROJECT_ENVIRONMENT:-}" ]; then
    export AGENT_HARNESS_CASE_VENV="$UV_PROJECT_ENVIRONMENT"
    unset UV_PROJECT_ENVIRONMENT
fi

exec uv run --quiet --frozen python adapter.py "$@"

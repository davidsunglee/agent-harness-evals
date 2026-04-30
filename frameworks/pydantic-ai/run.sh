#!/bin/sh
set -eu
cd "$(dirname "$0")"
# The harness sets UV_PROJECT_ENVIRONMENT for case test environments. Do not let
# that override this adapter's own uv environment, or uv may sync pydantic-ai
# dependencies into the harness-owned case venv and trip venv mutation checks.
unset UV_PROJECT_ENVIRONMENT
exec uv run --quiet python adapter.py "$@"

# Bootstrap Fixture: parse_duration

## Goal

Hand-roll the first benchmark case for the urban-winner shootout — a small Python repository with a known failing test that exercises every harness path defined in `shared/task-spec.md`. Purpose is to bootstrap and stress-test the harness end-to-end before SWE-bench cases are vendored in. The case is deliberately designed so naive partial fixes fail the hidden test, giving the scoring layer something to discriminate on from day one.

## Context

`shared/task-spec.md` defines the v1 software bugfix benchmark contract: each case ships a fixture repo, a failing test command, captured failure output, and optional hidden test command and edit constraints. The harness derives a fresh worktree per `(framework, run)` pair, the agent edits in place, and the harness re-runs the test to derive the canonical visible outcome. Edit constraints default to blocking edits to `tests/**`, `**/CHANGELOG*`, lockfiles, and similar gameable paths, with a five-file change cap.

This bootstrap fixture is the first concrete `case_id`. It is not a SWE-bench port; it is a hand-rolled fixture sized for fast iteration on the harness itself. Subsequent cases are expected to come from SWE-bench Verified or Lite.

## Requirements

- The case must exercise file listing, file search, file reading, in-place editing, test execution, and diff inspection — every required agent capability listed in the task spec.
- The visible failure must be a CI-style stack trace, not a natural-language bug report.
- The bug must live in a different file than the file that produces the stack trace, so the agent must navigate at least two source files.
- The hidden test must catch at least two distinct plausible under-fixes a real agent might apply.
- The case must use the spec's default `edit_constraints` — no per-case overrides — so the bootstrap also validates the default path.
- The fixture repo must be self-contained: no external services, no network access, no credentials. Only Python and pytest.
- The fixture repo must be runnable with a single `pytest` invocation against an off-the-shelf Python toolchain (uv or plain venv with pip install).

## Design

### Bug archetype

`parse_duration(s: str) -> int` parses duration strings like `"5s"`, `"10m"`, `"1h"` into seconds. The function looks up the trailing unit character in a `UNITS` dict imported from a sibling module. The dict is incomplete — it contains `{"m": 60}` only — so any input ending in `s` or `h` raises `KeyError`. Logic in `parser.py` is otherwise correct; the bug is **data**, located in `units.py`.

### Repository layout

```
fixtures/parse_duration/
├── README.md                              # one paragraph; documents that s/m/h are the supported units
├── pyproject.toml                         # declares pytest dependency; uv- or pip-installable
├── parse_duration/
│   ├── __init__.py                        # re-exports parse_duration
│   ├── parser.py                          # int(s[:-1]) * UNITS[s[-1]] — correct as written
│   └── units.py                           # UNITS = {"m": 60} — the bug
└── tests/
    ├── test_parse_duration.py             # visible: parse_duration("5s") == 5
    └── test_parse_duration_extended.py    # hidden: covers "1h" and "10m"
```

`parse_duration/parser.py` references `UNITS` from `parse_duration/units.py`. The README and the function's docstring both state that `s`, `m`, and `h` are supported.

### Visible test

`tests/test_parse_duration.py` contains a single assertion: `assert parse_duration("5s") == 5`. The test fails with a `KeyError: 's'` traceback pointing at the `UNITS[s[-1]]` line in `parser.py`. The agent's CI-style failure signal is therefore "stack trace points at parser.py, but the actual bug is one file over in units.py."

### Hidden test

`tests/test_parse_duration_extended.py` contains:

- `assert parse_duration("1h") == 3600`
- `assert parse_duration("10m") == 600`

The first asserts the other missing unit; the second guards against a regression of existing minute behavior.

### Under-fixes the hidden test catches

The hidden test discriminates against at least three plausible-but-wrong fixes:

- Adding only `"s": 1` to `UNITS`: visible passes, hidden's `"1h"` still raises `KeyError`.
- Defensive guard in `parser.py` such as `UNITS.get(s[-1], 1)`: visible passes (`5*1=5`), hidden's `"1h"` returns `1` instead of `3600`.
- Hardcoding the failing input in `parser.py` (e.g. `if s == "5s": return 5`): visible passes, hidden's `"1h"` and `"10m"` both wrong or raising.

A correct fix is a single edit to `units.py`: `UNITS = {"s": 1, "m": 60, "h": 3600}`.

### Case manifest

```json
{
  "case_id": "py-parse-duration-001",
  "fixture_repo": "fixtures/parse_duration",
  "failing_test_command": "pytest -q tests/test_parse_duration.py",
  "failure_output": "<captured KeyError stack trace from one clean run>",
  "hidden_test_command": "pytest -q tests/test_parse_duration_extended.py",
  "edit_constraints": {}
}
```

`edit_constraints` is intentionally empty so the harness applies its defaults: `disallowed_paths` covers `tests/**`, `**/*test*`, `**/*fixture*`, `**/*lock*`, `**/CHANGELOG*`, `.git/**`; `allowed_paths` is unrestricted; `max_changed_files` is `5`. `failure_output` is recorded once at case authoring time, not regenerated per run.

### Notes (case-author commentary, not surfaced to agents)

The bug is data-shaped to make file search non-trivial: the stack trace points at `parser.py`, but every plausible fix that touches `parser.py` is a partial fix that the hidden test catches. The fixture is intentionally not a SWE-bench port; it exists to validate the harness, not to test framework realism.

## Constraints

- The fixture repo must remain in `fixtures/parse_duration/` so the path matches the case manifest's `fixture_repo` field.
- The fixture repo must not depend on packages outside the standard pytest ecosystem in v1.
- The case manifest must use the spec's default edit constraints.
- The hidden test command must reference a separate test file from the visible test command so the harness can run them independently.
- The bug location must remain in `units.py`. Moving it to `parser.py` would collapse the under-fix traps and remove the file-search exercise.

## Acceptance Criteria

- `fixtures/parse_duration/` exists with the layout above.
- `pytest -q tests/test_parse_duration.py` run from inside the fixture repo against a fresh checkout fails with a `KeyError: 's'` traceback.
- `pytest -q tests/test_parse_duration_extended.py` run from inside the fixture repo against a fresh checkout fails on `"1h"`.
- After applying the canonical fix (`UNITS = {"s": 1, "m": 60, "h": 3600}`), both commands pass.
- The README and the `parse_duration` docstring both clearly state that `s`, `m`, and `h` are the supported units.
- A case manifest matching the JSON above is recorded somewhere the harness will be able to load (location TBD when the harness lands; in v1 it is sufficient to commit the manifest alongside the fixture).
- No external network access or credentials are required to install dependencies and run the tests.

## Out of Scope

- Implementing the harness, the eval CLI, or scoring code.
- Vendoring SWE-bench cases.
- Per-framework agent implementations.
- Any cases beyond `py-parse-duration-001`.
- TypeScript or non-Python fixtures.

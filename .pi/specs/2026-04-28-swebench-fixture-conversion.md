# SWE-bench Fixture Conversion: First Three Cases

## Goal

Convert three SWE-bench Verified instances into fixtures that conform to the existing `shared/task-spec.md` contract, no contract changes. The conversion procedure is documented inline so the next batch follows the same recipe. The bootstrap fixture (`py-parse-duration-001`) proved the harness; this spec proves the *real-world-bug* path through the harness using benchmark data.

## Context

`shared/task-spec.md` is contract-frozen. The bootstrap fixture validated end-to-end harness paths against a hand-rolled synthetic case. We need non-synthetic cases drawn from a public benchmark to exercise frameworks against realistic codebases — multi-file repos, real test suites, real upstream pyprojects. SWE-bench Verified is the chosen source: 500 human-vetted instances drawn from real Python OSS bug reports, each with a `base_commit`, gold `patch`, gold `test_patch`, and `FAIL_TO_PASS` / `PASS_TO_PASS` test lists.

The three instances were selected from a filtered candidate set (small repos, ≤3 files / ≤50 lines in the gold patch, sibling test depth, no compiled extensions). They span three different upstream repos and three different bug *patterns*:

- **`psf__requests-1921`** — `Session.merge_setting` mishandles `None` (header should drop, instead persists). Smallest repo (~1 MB). Plays the role of "simple-but-real" baseline.
- **`pylint-dev__pylint-7080`** — `--recursive=y` ignores `ignore-paths`. Failure surfaces in `tests/test_self.py`, fix is one line in `pylint/lint/expand_modules.py`. Exercises the **stack-trace-lies** pattern.
- **`pytest-dev__pytest-7571`** — `caplog` doesn't restore handler log level after a test. Three coordinated edits in `_pytest/logging.py` (init / set / finalize). Strongest **under-fix detection** target — partial fixes (logger level only, handler level only) are highly plausible.

## Requirements

- Three fixtures, one per instance, each conforming to `shared/task-spec.md` without contract modifications.
- Each fixture is browsable on disk (no submodules, no on-demand cloning) so `git diff` works the same as for `parse_duration`.
- Each fixture is reproducible: pinned dependency graph that survives upstream-of-upstream releases.
- Each case provides a `hidden_test_command` that catches at least one plausible under-fix variant for that bug.
- The case manifest schema is unchanged from the bootstrap (no new top-level fields).
- Provenance (SWE-bench instance ID, base commit, source URL) is recorded in `notes` so a future reader can re-derive the fixture from upstream.
- The case `failing_test_command` and `hidden_test_command` are derived deterministically from `FAIL_TO_PASS` per the rule in *Visible / hidden test selection* below.
- Each fixture is installable and runnable in a clean shell with `uv sync && uv run pytest <command>`.
- No external network access required at harness run time (everything vendored at bootstrap time).

## Design

### Conversion pipeline (per case)

1. Clone upstream at `base_commit`. Apply `test_patch` so the failing test physically exists in the tree. Discard the upstream `.git/` directory — the fixture is a snapshot, not a clone.
2. Generate `uv.lock` against the upstream `pyproject.toml` (or wrapped equivalent) plus the test extras the failing test needs. If the upstream pyproject is not uv-friendly out of the box (Poetry-only, setuptools+tox without PEP-621 metadata), add a thin `[tool.uv]` overlay or a wrapper `pyproject.toml` at the fixture root that delegates to upstream's build system. Document the overlay in case `notes`.
3. Read the bodies of all `FAIL_TO_PASS` tests. Pick the **single most representative** test as visible (the one whose name and body most directly describe the obvious symptom). The remaining `FAIL_TO_PASS` tests become hidden.
4. Capture the failure output by running the visible command against the fixture (with `test_patch` applied, gold `patch` not yet applied). Save to `cases/<case_id>.failure_output.txt`.
5. Write the case manifest at `cases/<case_id>.json` per the schema in `shared/task-spec.md`, with provenance in `notes`.
6. Verify three properties end-to-end:
   - The visible command fails as recorded in `failure_output_path`.
   - Applying the upstream gold `patch` makes the visible command pass.
   - The hidden command catches at least one plausible under-fix — typically a fix that handles only one of the F2P scenarios. If the hidden command does not discriminate, the visible/hidden split is wrong and we re-pick.

### Fixture layout

```
fixtures/<instance_id>/
├── pyproject.toml          # upstream's, possibly wrapped with [tool.uv]
├── uv.lock                 # generated at bootstrap, committed
├── <package>/              # upstream source at base_commit
├── tests/                  # upstream tests + applied test_patch
└── ...                     # any other upstream files (README, conftest, etc.)
```

`<instance_id>` is the SWE-bench ID verbatim (e.g. `psf__requests-1921`). Mixed naming with synthetic cases (`py-parse-duration-001`) is intentional: provenance is encoded in the ID. SWE-bench IDs use double-underscore as the org/repo separator, which is filesystem-safe on macOS and Linux.

The upstream `.git/` directory is excluded. The fixture's git history lives in our repo, not the upstream's.

### Case manifest

The schema in `shared/task-spec.md` is used unchanged. Provenance lives in the `notes` field, which the spec defines as case-author commentary not surfaced to the agent.

```json
{
  "case_id": "psf__requests-1921",
  "fixture_repo": "fixtures/psf__requests-1921",
  "failing_test_command": "pytest -q <visible test node id>",
  "failure_output_path": "cases/psf__requests-1921.failure_output.txt",
  "hidden_test_command": "pytest -q <hidden test selector>",
  "edit_constraints": {},
  "notes": "Source: SWE-bench Verified, instance psf__requests-1921, base_commit <sha>, upstream https://github.com/psf/requests. Visible test = <name> (most direct symptom from FAIL_TO_PASS). Hidden tests = <names>. Under-fix patterns the hidden tests catch: <one line per pattern>. Any uv overlay required: <yes/no, with reason>."
}
```

`edit_constraints` defaults to `{}` (spec defaults apply: tests/lockfiles/CHANGELOG/.git blocked, max five changed files). Per-case overrides are added only if a real-world reason emerges; none of the three trio is expected to need one.

### Visible / hidden test selection

For each case, all tests in `FAIL_TO_PASS` exist in the post-`test_patch` fixture and currently fail. Selection rule:

- **Visible**: one test from `FAIL_TO_PASS`, picked by reading the test bodies and choosing the test whose name and assertion most directly describe the bug as a user would report it.
- **Hidden**: every other test in `FAIL_TO_PASS`, expressed as a `pytest -k` selector or an explicit list of test node IDs.

The case-author records, in `notes`, which under-fix patterns each hidden test catches. Concretely, for each hidden test the author writes one line: "test X catches a fix that does Y but not Z."

If a case's `FAIL_TO_PASS` contains only one test, the rule degrades: visible = that test, hidden = a curated subset of `PASS_TO_PASS` chosen to exercise the closest-related code path. The degradation is documented in `notes`. We do not invent synthetic hidden tests — the hidden command must run only tests that exist in the fixture as committed.

Two of the three trio cases trigger the degradation: `pylint-dev__pylint-7080` (1 F2P, 120 P2P) and `pytest-dev__pytest-7571` (1 F2P, 14 P2P) both have a single `FAIL_TO_PASS` test. `psf__requests-1921` (6 F2P, 107 P2P) is the only case where the primary F2P-split rule applies. This means `PASS_TO_PASS` is the hidden source for the majority of the trio in practice; the rule cleanly degrades to it.

When `PASS_TO_PASS` is the hidden source, the case author hand-picks the subset that exercises the same code path the bug lives in (typically by module, fixture, or test-class proximity). Curation matters here — `PASS_TO_PASS` covers regressions in unrelated behavior as a baseline, but for under-fix detection we want tests that exercise scenarios the partial-fix author would plausibly miss. The selected subset and the under-fix patterns it catches are documented in `notes` per the case-manifest convention below.

### Per-case bootstrap order

Cases are bootstrapped sequentially in this order, each producing its own commit so reverting a single fixture is clean:

1. **`psf__requests-1921`** — smallest, fewest unknowns. Establishes the recipe end-to-end. Likely needs a fixture-root `pyproject.toml` wrapper because requests at this commit ships `setup.py` + `requirements.txt` rather than PEP-621 metadata.
2. **`pylint-dev__pylint-7080`** — introduces Poetry-style pyproject. Expected to `uv sync` directly with no overlay, but the case-author verifies; `[testutils]`-style extras may be needed.
3. **`pytest-dev__pytest-7571`** — setuptools + `tox.ini` upstream. Needs `[testing]` extra installed. pytest's own conftest pulls in dev deps (`mock`, `hypothesis`); the uv.lock will be the largest of the three.

Per-case quirks above are *expected, not confirmed* — the bootstrap process flags any divergence in the case's `notes`.

## Constraints

- The fixture directory layout above is fixed: `fixtures/<instance_id>/` with upstream sources, a `pyproject.toml`, and a `uv.lock` at the root.
- The case manifest schema in `shared/task-spec.md` is unchanged. New fields (e.g. structured `provenance`) are explicitly out of scope; provenance lives in `notes`.
- `<instance_id>` matches the upstream SWE-bench ID exactly. Renaming for cosmetic consistency with `py-parse-duration-001` is rejected — provenance is more valuable than naming uniformity.
- The fixture may not depend on network access at harness run time. All dependencies must be resolvable from `uv.lock` alone.
- Hidden tests are drawn from `FAIL_TO_PASS` where the case has multi-F2P (only `psf__requests-1921` in this trio) and from a curated `PASS_TO_PASS` subset where the case has single-F2P (`pylint-dev__pylint-7080` and `pytest-dev__pytest-7571`). Both sources are valid for v1.

## Acceptance Criteria

For each of the three cases:

- `fixtures/<instance_id>/` exists with the layout above, including `pyproject.toml` and committed `uv.lock`.
- `cases/<instance_id>.json` exists, parses against the contract in `shared/task-spec.md`, and references `cases/<instance_id>.failure_output.txt`.
- Running `failing_test_command` from the fixture root in a fresh `uv sync`'d environment fails. The captured `failure_output_path` reproduces the same exception type, the same failing test node ID, and the same primary assertion-failure message as a fresh run; volatile fields (timestamps, absolute paths, durations) are expected to drift and are not part of the match.
- Applying the upstream gold `patch` and re-running `failing_test_command` succeeds.
- `hidden_test_command` fails on at least one hand-constructed under-fix variant of the gold patch, demonstrating that the hidden command discriminates partial fixes.
- The `notes` field records: SWE-bench instance ID, `base_commit` SHA, upstream URL, the under-fix patterns each hidden test catches, and any uv overlay used.
- The default `disallowed_paths` globs (`tests/**`, `**/*test*`, `**/*fixture*`, `**/*lock*`, `**/CHANGELOG*`, `.git/**`) intentionally block several files inside each fixture: upstream tests, the `uv.lock`, any `CHANGELOG`, and any `conftest.py` (matched by `**/*test*`). This is the intended posture — agents must fix the source, not the tests, the lockfile, or the changelog. None of the three trio's gold patches touch a file matched by these globs, so the default constraint set is sufficient and no per-case override is needed.

## Out of Scope

- Updating `shared/task-spec.md` (no contract change is needed).
- Automating the conversion as a CLI tool. The procedure is documented above; a tool is justified at 5+ cases, not 3.
- Additional cases beyond the trio.
- Multi-SWE-bench (TypeScript / Java / Go) instances. Mastra coverage is deferred until the Python flow is end-to-end working.
- Harness changes. The trio is designed to fit the existing contract; any harness adaptation needed for SWE-bench cases is a separate spec.
- Per-framework agent implementations.
- A leaderboard or scoring rubric beyond what the contract already defines.

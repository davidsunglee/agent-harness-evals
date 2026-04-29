# Review: Eval Harness Design v2

Spec reviewed: `.pi/specs/2026-04-28-eval-harness-design-v2.md`

## Overall

V2 is a substantial improvement. It directly addresses the main V1 review points: override semantics, error precedence, env separation, command execution rules, raw stdout vs parsed response artifacts, non-mutating diff capture, and expanded tests. I’d call it close to implementation-ready, with a few remaining ambiguities worth tightening before coding.

## Strengths in V2

- **Excellent compatibility section.** It clearly reconciles deviations from `task-spec.md`.
- **Much better failure model.** The precedence table and “scoring always written” rule remove a lot of implementation guesswork.
- **Good env split.** `agent_env` vs `test_env` is the right security/reproducibility boundary.
- **Artifact semantics are clearer.** `stdout.log` as raw source and `response.json` as parsed artifact is a strong improvement.
- **Temp-index diff design is good.** Preserving the real index avoids surprising users inspecting `repo/`.
- **Override rules are now coherent.** Rejecting bulk overrides in existing campaigns is a good consistency choice.
- **Testing plan is stronger.** The added fake behaviors cover the edge cases that usually rot first.

## Remaining concrete suggestions

### 1. Make `effective_config.source` per-field

Current shape:

```json
"effective_config": {
  "model": "...",
  "timeout_s": 120,
  "max_steps": 50,
  "source": "campaign" | "cell-flag"
}
```

But sources can be mixed: `--timeout-s` from a cell flag, model from framework manifest, max steps from harness default.

Suggest:

```json
"effective_config": {
  "model": "claude-sonnet-4-6",
  "timeout_s": 120,
  "max_steps": 50,
  "sources": {
    "model": "framework-manifest" | "campaign" | "cell-flag",
    "timeout_s": "harness-default" | "campaign" | "cell-flag",
    "max_steps": "harness-default" | "campaign" | "cell-flag"
  }
}
```

### 2. Clarify shared venv/test command correctness

This is the biggest remaining design risk.

The spec should explicitly ensure that tests run against the **mutated cell worktree**, not code installed from `fixtures/<case>` during `uv sync`.

Also, current starter cases are mixed: most use `uv run pytest`, but `py-parse-duration-001` uses plain `pytest`. With only `UV_PROJECT_ENVIRONMENT` set, plain `pytest` may not resolve unless the venv’s `bin/` is on `PATH`.

Concrete additions:

- Decide whether the shared venv is dependency-only, e.g. `uv sync --no-install-project`.
- Ensure `uv run` during test reruns does not mutate the shared venv, e.g. no-sync/frozen mode where possible.
- Either prepend `.runs-cache/<case>.venv/bin` to `PATH`, or require all case commands to use `uv run`.
- State how imports are guaranteed to resolve from `<cell>/repo`.

### 3. Define setup failure behavior

`setup` is described, but failure handling is not.

Specify:

- timeout for setup scripts;
- where setup stdout/stderr is stored;
- whether `eval-prepare` stops on first setup failure;
- whether cells for a failed setup framework are marked `framework_misconfigured` or never run;
- sentinel is written only after setup exits 0.

### 4. Tighten `response.json` semantics for nonzero exits

V2 says nonzero exits may still produce parseable stdout and that it may be written to `response.json`. But the contract says failure writes “error JSON,” which may or may not be a valid response envelope.

Clarify one of these:

- `response.json` exists only for valid contract envelopes; or
- `response.json` contains any parsed stdout JSON, even if it is not a valid envelope.

If the latter, consider naming it `stdout.parsed.json` to avoid implying contract validity.

### 5. Ensure artifacts exist for `framework_misconfigured`

Acceptance says `stdout.log` is always present and `scoring.json` is always written. But `framework_misconfigured` may be detected before exec, possibly before a worktree exists.

Add a rule such as:

> Even for `framework_misconfigured`, the harness creates the cell dir, pristine repo, empty `stdout.log`, diagnostic `stderr.log`, `diff.patch`, test result artifacts, `scoring.json`, and final `meta.json`.

Or explicitly exempt pre-run misconfiguration from some artifacts.

### 6. Add caps for visible/hidden test outputs

Agent stdout/stderr are capped, but visible/hidden test stdout/stderr are not. SWE-bench-style failures can be large.

Suggest adding per-test stdout/stderr caps and recording truncation in `visible_test.json` / `hidden_test.json`.

Also specify that after hitting a cap, the harness continues draining pipes to avoid deadlocks.

### 7. Consider safer different-host lock behavior

Current rule treats a lock from a different hostname as stale. That is risky on shared filesystems.

Safer default:

> If hostname differs, refuse and tell the user to delete the lock manually or pass a force flag.

### 8. Make sentinel writes atomic

Since `meta.json` is the done-sentinel, specify temp-file + rename for it:

```text
write meta.json.tmp, fsync/close, rename to meta.json
```

This prevents a partial `meta.json` from being mistaken as complete after a crash.

## Bottom line

V2 is much stronger than V1 and mostly ready. I would tighten the venv/import semantics, setup failure behavior, and `effective_config` shape before implementation; the rest are edge-case clarifications.

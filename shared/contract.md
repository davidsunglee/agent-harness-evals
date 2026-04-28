# Agent Contract

Every framework implementation must satisfy this contract. The eval harness talks to agents through it — never by importing framework code.

## Transport

Default: **subprocess + JSON over stdin/stdout.** One JSON object in, one JSON object out, then exit. Frameworks that prefer HTTP can expose a server and adapt; the harness stays transport-agnostic via a small adapter per framework.

## Request (stdin)

```json
{
  "task_id": "string",
  "input": { /* shape defined in task-spec.md */ },
  "config": {
    "model": "string",
    "max_steps": 50,
    "timeout_s": 120
  }
}
```

## Response (stdout, single line, then exit 0)

```json
{
  "task_id": "string",
  "output": { /* shape defined in task-spec.md */ },
  "trace": {
    "steps": [
      { "kind": "tool_call" | "model_call" | "thought", "name": "string", "args": {}, "result": {} }
    ],
    "tokens": { "input": 0, "output": 0 },
    "latency_ms": 0
  },
  "error": null
}
```

On failure: exit non-zero, write the error JSON to stdout, write logs to stderr.

## What each framework dir must provide

- An entry point the harness can invoke (`./run.sh` or equivalent — declared in `frameworks/<name>/manifest.json`)
- Its own dependency management (uv, npm, …) — do not share lockfiles across frameworks
- A README documenting model choice, env vars, and any setup quirks

# urban-winner

Agent framework shootout. Each framework in `frameworks/` implements the same task (see `shared/task-spec.md`) against the same I/O contract (`shared/contract.md`). The harness in `evals/` runs them all and produces a comparative report.

Frameworks in scope: DeepAgents, Pydantic AI, Google ADK, Amazon Strands, Amazon Bedrock AgentCore, Claude Agent SDK, OpenAI Agents SDK, and (TBD) Mastra.

## Layout

```
.
├── justfile           # top-level orchestration
├── shared/            # task spec + contract — the only thing tying frameworks together
├── evals/             # framework-agnostic harness; talks to agents through the contract
└── frameworks/        # one dir per framework, each fully independent (own deps, own lockfile)
```

## Quickstart

```sh
just            # list targets
just frameworks # list framework dirs
just eval-all   # run every framework through the harness
```

Per-framework setup lives in each `frameworks/<name>/README.md`.

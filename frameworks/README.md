# frameworks

One subdirectory per framework. Each is **independent**: its own dependency manager, its own lockfile, its own README. The eval harness reaches in only through the contract (`../shared/contract.md`).

Each dir is expected to have:

- `manifest.json` — declares the entry point command, language/runtime, and any required env vars
- `run.sh` (or whatever the manifest points to) — the executable the harness invokes
- `README.md` — model choice, setup, quirks
- Implementation files (Python, TS, …) native to the framework

## Frameworks

- `deepagents/` — DeepAgents (Python, on LangGraph)
- `pydantic-ai/` — Pydantic AI (Python)
- `google-adk/` — Google Agent Development Kit (Python)
- `strands/` — Amazon Strands (Python)
- `agentcore/` — Amazon Bedrock AgentCore (Python)
- `claude-agent-sdk/` — Claude Agent SDK (Python)
- `openai-agents/` — OpenAI Agents SDK (Python)
- `mastra/` — Mastra (TypeScript) — under consideration

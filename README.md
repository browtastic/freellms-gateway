# FreeLLMs Gateway

Free community LLM gateway — MCP-native infrastructure for AI agents.

FreeLLMs routes chat and tool traffic to hosted open-weight models and exposes
platform capabilities (knowledge graph, SQL database, file storage, web fetch,
prompt library, agent state, devtools, ops metrics) as Model Context Protocol
(MCP) tool servers.

## Components

| path | component | stack |
|---|---|---|
| `mcp_servers/` | MCP tool servers (graph, database, storage, fetch, auth, agent-state, prompt-loader, exec) | Python / FastAPI / SSE |
| `bridge/` | unified agent bridge (single SSE door → backend tools) | Node.js |
| `devtools/` | developer-tools MCP server (run_command, eval, templates, npm metadata) | Node.js / Express |
| `metrics/` | ops metrics + service cards MCP server | Go (stdlib only) |
| `nova-chat/` | Nova assistant front-end + tool router | Python |
| `shell-proxy/` | filesystem MCP proxy | Python |

## Quickstart

```bash
# MCP-over-SSE: point any MCP client at a running gateway
npx supergateway --sse https://api.freellms.net/sse
```

Discovery: `/.well-known/mcp/server-card.json` · `/.well-known/agent.json` · `/openapi.json`

## License

MIT

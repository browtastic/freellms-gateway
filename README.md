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

## MCP deployment manifests

The root `mcp.json` lists the 13 source-backed gateway services and their live
legacy-SSE endpoints. Each MCP component also carries a component-scoped
`mcp.json`, so clients and static analysis tools can attribute the deployed
server to its source directory:

| component | manifest | public endpoint |
|---|---|---|
| unified bridge | `bridge/mcp.json` | `https://api.freellms.net/sse` |
| Python tool servers | `mcp_servers/mcp.json` | nine service endpoints |
| developer tools | `devtools/mcp.json` | `https://dev.freellms.net/sse` |
| ops metrics | `metrics/mcp.json` | `https://metrics.freellms.net/sse` |
| filesystem proxy | `shell-proxy/mcp.json` | `https://shell.freellms.net/sse` |

These configurations contain no credentials. SSE is retained for compatibility
with the deployed gateway and legacy MCP clients.

## License

MIT

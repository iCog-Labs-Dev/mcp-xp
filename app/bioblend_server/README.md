# BioBlend Server

The **Galaxy MCP server** that securely exposes a full Galaxy instance to AI assistants via the Model Context Protocol.

### Purpose
Allows LLMs to act as intelligent agents inside Galaxy: discover tools/workflows, analyze past invocations, diagnose failures, and import new suggested workflows, all on behalf of an authenticated user.

### Key Capabilities
- Rich semantic search over tools, workflows, datasets, and histories  
- Deep workflow invocation analysis (step-by-step job tracing, failure root-cause, output explanation, fix suggestions)  
- One-click workflow import from the The Galaxy IWC (Intergalactic Workflow Comission), WorkflowHub, etc., with automatic ToolShed dependency resolution. 
- Background metadata indexer for fast, always-up-to-date search  
- Per-user JWT authentication with encrypted, short-lived Galaxy credentials

### What the Galaxy Assistant Can Do
1. **Search & Explore**: Discover and access comprehensive information about any Galaxy entity (tools, workflows, datasets) on user's galaxy instance, from initial suggestions to detailed insights.
2. **Analyze Workflow Runs**: Deeply inspect any invocation: step-by-step progress, job states, logs, failure diagnosis, fix suggestions, and output explanation.  
3. **Import Workflows**: Pull a workflow from IWC, and WorkflowHub, etc. and automatically install all missing tools into the Galaxy instance.

### Core Components
- `server.py` → FastMCP HTTP/SSE server + MCP server tool exposure.  
- `mcp_middleware.py` → JWT validation, credential decryption, user context injection  
- `informer/` → semantic search engine (indexing, caching, reranking) — see informer/README.md  
- `background_runner.py` → continuous metadata scraper & indexer  
- `utils.py` → response formatting, analysis helpers, schemas

### Run it
```bash
python -m app.bioblend_server --port <MCP-Server-Port>
```
### Connecting from an MCP Client
Add this entry to your `servers_config.json`:

```json
{
  "mcpServers": {
    "GalaxyMcpAssistant": {
      "url": "https://your-bioblend-server.example.com",
      "headers": {
        "Authorization": "Bearer <your-jwt-token-with-the-neccessary-credentials-encoded.>"
      }
    }
  }
}
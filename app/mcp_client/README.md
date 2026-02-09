# MCP Client

**Demo Model Context Protocol (MCP) client** – built primarily **for the Galaxy MCP server development and testing**.

### Purpose
Provides a simple, ready-to-use LLM chat interface that can discover and call tools on any MCP server.  
Use it to quickly validate your server implementation, test tool behavior, and iterate fast.

### What it does
- Connects to MCP servers (HTTP/SSE)
- Lists available tools
- Runs tool calls with retry logic
- Supports Gemini & OpenAI as LLM backends
- Handles multi-turn conversations

Configuration lives in `servers_config.json`.
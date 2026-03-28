# Galaxy Integration

Middleware Service providing RESTful and Model Context Protocol (MCP) interfaces for integrating with the Galaxy bioinformatics workflow platform.

### Purpose

Acts as an intelligent gateway between client applications and Galaxy instances, enabling:

- RESTful API for workflow orchestration, tool execution, and data management
- Model Context Protocol (MCP) server for AI assistant integration with Galaxy
- Real-time progress tracking via WebSocket connections
- JWT-based authentication with encrypted Galaxy API key management

### Architecture

This platform consists of two primary components:

#### 1. MCP Server

**Galaxy MCP Server** - Model Context Protocol server bridging Galaxy capabilities with AI assistants.

Enables LLMs to:
- Search and retrieve Galaxy tool, workflow, and dataset information from the users connected galaxy instance
- Analyze workflow execution results and diagnose failures
- Import workflows from external repositories
- Provide intelligent recommendations using semantic search and reranking

#### 2. Galaxy Integration Layer

**GX Integration** - High-level Python abstraction for Galaxy server interactions.

Provides:
- Workflow management (upload, invoke, track, delete)
- Data operations (file uploads, collection management, dataset adoption)
- History management and invocation tracking
- Output indexing for visualization

### Quick Start with Docker

Copy the example environment file and configure your settings:

```bash
cp .env.example .env
```
#### Running with Docker Compose

**Start all services:**
```bash
docker compose up -d
```

**Start all services (development with hot reload):**
```bash
docker compose -f docker-compose.dev.yml up -d
```

### Makefile Commands

The project includes a Makefile for common operations:

```bash
# Build and start services
make docker-build
make docker-up

# View status and logs
make docker-status
make docker-logs
```

## Documentation

For detailed component documentation, see:
- [Galaxy MCP Server](app/bioblend_server/README.md)
- [Galaxy Integration](app/GX_integration/README.md)
- [API Endpoints](app/api/README.md)
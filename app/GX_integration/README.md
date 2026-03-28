## GX_integration

### Purpose

GX_integration provides a Python abstraction layer for interacting with Galaxy servers. It simplifies workflow execution, tool management, data operations, and history management through high-level, reusable components.

### Functionalities

- **Tool Execution**: Discover, configure, and run Galaxy tools with dynamic HTML form generation
- **Workflow Management**: Upload, invoke, track, and manage Galaxy workflows
- **Data Operations**: Upload files and collections, download outputs, list history contents, manage reference data tables
- **History Management**: Create, retrieve, delete, and list Galaxy histories
- **Form Generation**: Generate dynamic HTML forms for tools and workflows based on Galaxy XML definitions
- **Invocation Tracking**: Monitor workflow invocations with real-time state tracking and WebSocket notifications
- **Output Indexing**: Index workflow output datasets (FASTA, VCF, BAM, GTF) for visualization

### Component Architecture

#### Core Managers (Root Level)

- `data_manager.py`: Handles all Galaxy data I/O operations
- `tool_manager.py`: Manages tool discovery, execution, and monitoring
- `history_manager.py`: Manages Galaxy history lifecycle
- `form_generator.py`: Generates HTML forms from tool/workflow XML definitions

#### Subdirectories

- **invocations/**: Manages workflow invocation lifecycle, tracking, and output processing
- **workflows/**: Handles workflow operations including installation, invocation, and management
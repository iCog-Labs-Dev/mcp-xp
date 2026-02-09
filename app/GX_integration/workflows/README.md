## Workflow Management

**Galaxy workflow engine** – discovery, upload, tool installation, execution & real-time tracking.

#### What it does
- Upload workflows + auto-install missing tools from ToolShed  
- Discover workflows (name/ID, fuzzy matching)  
- Build & validate input forms  
- Execute invocations with step-level progress  
- Collect outputs and send live WebSocket updates  
- Handle cancellation and failure detection

#### Main classes
- `WorkflowManager` – central interface  
- `WorkflowInvocation` – execution & monitoring  
- `WorkflowInstaller` – workflow upload, tool & dependency handling
# Galaxy API Middleware

**FastAPI REST + WebSocket layer** for secure Galaxy integration – workflows, tools, histories, invocations, and datasets with real-time tracking.

### Purpose
Provides a performant, cached API between clients and Galaxy instances: simplified orchestration, file/collection management, execution monitoring, and dataset handling.

### Endpoints
| Prefix        | Responsibility                          |
|---------------|-----------------------------------------|
| `/workflows`  | Lissting, upload, execution, deletion   |
| `/histories`  | History management, uploads, collections|
| `/tools`      | Tool execution + dynamic forms          |
| `/invocation` | Invocation listing, tracking & results  |
| `/dataset`    | Dataset adoption + output indexing      |

### Key Features
- **Auth**: JWT with Fernet-encrypted Galaxy API key credentials 
- **Caching and Persistence**: Redis (workflows, invocations) + MongoDB (dataset metadata)  
- **Real-Time**: WebSocket progress updates for executions & indexing (tracker_id rooms)  
- **Performance**: Request deduplication, background tasks, multi-level rate limiting  
- **Rate Limits**: Per-endpoint, per-user, global via Redis  

### Core Infrastructure
- `api.py` → central router  
- `middleware.py` → JWT auth, rate limiting, CORS  
- `socket_manager.py` → WebSocket management  
- `schemas/` → Pydantic request/response models  
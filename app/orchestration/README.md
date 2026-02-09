### Orchestration Module

**Redis-backed cache + background sync layer** for Galaxy integration layer.

### functionalites
- Distributed caching with locking (Redis)
- Automatic background cache warming for active users
- Deduplicates concurrent identical requests
- Tracks invocation states and workflow mappings
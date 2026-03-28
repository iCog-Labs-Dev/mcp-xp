## Search Engine Module

**Hybrid search** for Galaxy entities, combines fuzzy string matching and semantic vector search.

#### What it does
- Extracts validated n-gram keywords from queries  
- Runs parallel fuzzy (RapidFuzz, priority on names/IDs) and semantic (Qdrant embeddings) searches  
- Merges global + user-specific results with deduplication and normalization  
- Caches indexes in Redis for speed

Feeds combined results directly to the Informer reranker.
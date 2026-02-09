# Galaxy Informer

**RAG-powered semantic search engine** for Galaxy bioinformatics entities (tools, workflows, datasets, histories).

### Purpose
The core intelligence behind the Galaxy MCP Server’s “Search & Explore” capability.  It delivers fast, highly relevant results by combining hybrid search (fuzzy + semantic), multi-layer caching, vector embeddings, and Two-Stage Reranking(RRF and Crossencoder), making natural-language queries over large Galaxy instances feel instant and accurate.

### Key Features
- **Hybrid Search**: Fuzzy string matching + dense vector search for robust recall  
- **Two-Stage Reranking**: Reciprocal Rank Fusion (RRF) → cross-encoder LLM reranking for precision  
- **Smart Caching**: Redis stores frequent queries, entity metadata, and precomputed results (with TTL)  
- **Vector Store**: Qdrant for persistent embeddings; automatic embedding generation on ingest  
- **Data Ingestion**: Live fetching from user Galaxy instances + background scraping of public repositories (GitHub IWC, WorkflowHub, ToolShed)  
- **Always Fresh**: Background processes keep global indexes up-to-date without blocking queries

### Core Components
- **GalaxyInformer**: Main orchestrator coordinating all modules  
- **SearchEngine**: Hybrid search with fuzzy and semantic capabilities  
- **InformerReranker**: Multi-stage result reranking pipeline  
- **InformerManager**: Vector database operations and embedding management  
- **RedisCache**: Redis-based caching layer  
- **GalaxyDataProvider**: Galaxy API client for live user galaxy data retrieval for personal assistance  
- **Scrapers**: External data scraping for tools and workflows from global resources

Used exclusively by the BioBlend MCP server to power intelligent, user-specific Galaxy searches.
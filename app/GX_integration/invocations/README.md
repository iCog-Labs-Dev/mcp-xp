## Invocation Management

**Complete lifecycle manager** for Galaxy workflow invocations – tracking, data enrichment, indexing & real-time updates.

#### What it does
- List, retrieve, delete invocations (with caching)  
- Fetch & structure inputs/outputs/metadata  
- Background monitoring + step-level state + WebSocket progress  
- Cancel invocations and handle failures  
- Index genomic outputs (FASTA, VCF, BAM, GTF)

#### Main classes
- `InvocationService` – high-level orchestration  
- `DataManager` – data fetching & formatting  
- `Tracker` – state tracking & background jobs  
- `OutputIndexer` – dataset indexing for visualization
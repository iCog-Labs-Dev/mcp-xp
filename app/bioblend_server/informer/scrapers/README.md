## Scrapers

**External data ingestion** : scrapes public Galaxy tools and workflows to enrich global search indexes.

#### What it does
- Fetches tool metadata + help docs from public Galaxy instances  
- Scrapes workflows from GitHub IWC repository (with READMEs)  
- Pulls workflows from WorkflowHub API  
- Cleans and structures data for RAG/embedding  
- Concurrent scraping with rate limiting
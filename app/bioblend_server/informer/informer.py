"""
Galaxy Informer Orchestrator.

Coordinates all modules to provide intelligent information retrieval
for Galaxy bioinformatics entities (tools, workflows, datasets).
"""

import os
import re
import logging
import asyncio
from typing import List, Dict
from rapidfuzz import process, fuzz

from sys import path
path.append(".")

from app.log_setup import configure_logging
from app.galaxy import GalaxyClient
from app.bioblend_server.informer.prompts import SUMMARIZER_PROMPT, FINAL_RESPONSE_PROMPT

# Import modular components
from app.bioblend_server.informer.cache import RedisCache
from app.bioblend_server.informer.indexer import RedisIndexer, QdrantIndexer
from app.bioblend_server.informer.search import SearchEngine
from app.bioblend_server.informer.reranker import InformerReranker
from app.bioblend_server.informer.data_provider import GalaxyDataProvider

from app.bioblend_server.informer.utils import InformerTTLs, LLMResponse


class GalaxyInformer:
    """
    A Tool to retrieve and summarize information about Galaxy
    entities (tools, workflows, datasets) using a combination of bioblend based API calls,
    caching, fuzzy search, and Retrieval-Augmented Generation (RAG).
    """
    
    def __init__(self, galaxy_client: GalaxyClient, entity_type: str):
        """Initializes the GalaxyInformer with non-blocking assignments."""
        self.log = logging.getLogger(self.__class__.__name__)
        self.entity_type = entity_type.lower()
        
        self.galaxy_client = galaxy_client
        self.username = self.galaxy_client.whoami
        
        # Data provider
        self.data_provider = GalaxyDataProvider(
            galaxy_client=self.galaxy_client,
            entity_type=self.entity_type
        )
        
        # Modular components (initialized in create())
        self.cache: RedisCache = None
        self.redis_indexer: RedisIndexer = None
        self.qdrant_indexer: QdrantIndexer = None
        self.search_engine: SearchEngine = None
        self.reranker: InformerReranker = None
        self.search_engine: SearchEngine = None
        
        # LLM and embeddings
        self.manager = None
        self.llm_response = LLMResponse()
        
        
        # Entity configuration
        self._entity_config = {
            'dataset': {
                'get_method': self.data_provider.get_datasets,
                'search_fields': ['name', 'dataset_id'],
                'id_field': 'dataset_id',
                'ttl': InformerTTLs.DATASET_TTL.value
            },
            'tool': {
                'get_method': self.data_provider.get_tools,
                'search_fields': ['name', 'tool_id'],
                'id_field': 'tool_id',
                'ttl': InformerTTLs.TOOL_TTL.value
            },
            'workflow': {
                'get_method': self.data_provider.get_workflows,
                'search_fields': ['name', 'workflow_id'],
                'id_field': 'workflow_id',
                'ttl': InformerTTLs.WORKFLOW_TTL.value
            }
        }
        self.log.info(
            f'Initializing the galaxy informer for entity type: {entity_type} '
            f'for user {self.username}'
        )

    @classmethod
    async def create(cls, galaxy_client: GalaxyClient, entity_type: str):
        """Asynchronous factory to create and fully initialize a GalaxyInformer instance."""
        
        from app.bioblend_server.informer.manager import InformerManager
        
        self = cls(galaxy_client, entity_type)

        configure_logging()

        
        # Initialize manager
        self.manager = await InformerManager.create()
        
        # Initialize cache
        self.cache = RedisCache(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6389)),
            db=0
        )
        
        # Initialize indexers
        self.redis_indexer = RedisIndexer(cache=self.cache)
        self.qdrant_indexer = QdrantIndexer(manager=self.manager)
        
        # Initialize search components
        self.search_engine = SearchEngine(
            manager=self.manager,
            cache = self.cache,
            entity_type=self.entity_type,
            username=self.username,
        )
        
        # Initialize reranker
        self.reranker = InformerReranker()
        
        return self


    
    def get_all_entities(self) -> list[dict]:
        """
        Public method to get all entities based on the configured type.
        """
        return self._entity_config[self.entity_type]['get_method']()
    
       
    async def refresh_and_cache_entities(self) -> list[dict]:
        """
        Fetches fresh entity data from Galaxy, then caches it in Redis and
        updates the Qdrant vector store.
        """
        self.log.info(f'No valid cache found for {self.entity_type}, fetching fresh data.')
        entities, name_corpus = self.get_all_entities()

        # If nothing is found in the galaxy return the empty result
        if not entities:
            return None
        
        collection_name, corpus_name = self.search_engine.get_collection_name()
        ttl = self._entity_config[self.entity_type]['ttl']
        
        # Index name corpus in redis ffor later search
        name_indexing = self.redis_indexer.index_entities(
            entities = name_corpus,
            collection_name = corpus_name,
            ttl=ttl
        )
        
        # Index in Redis
        redis_indexing =  self.redis_indexer.index_entities(
            entities=entities,
            collection_name=collection_name,
            ttl=ttl
        )
        
        # Index in Qdrant
        qdrant_indexing =  self.qdrant_indexer.index_entities(
            entities=entities,
            collection_name=collection_name
        )
        
        # Async exection and then gather data.
        await asyncio.gather(name_indexing, redis_indexing, qdrant_indexing)
        return entities

    async def get_cached_or_fresh_entities(self) -> list[dict]:
        """
        Attempts to retrieve entities from Redis cache, falling back to fetching
        fresh data if the cache is empty or invalid.
        """
        collection_name, _ = self.search_engine.get_collection_name()
        
        # Try to get from cache
        entities = self.cache.get_entities(collection_name)
        if entities:
            self.log.info(f'Retrieved cached {self.entity_type} entities from Redis.')
            return entities
        
        # Cache miss - fetch fresh data
        return await self.refresh_and_cache_entities()

    async def search_entities(self, query: str) -> List[Dict]:
        """
        Conducts a hybrid search using both fuzzy and semantic techniques,
        then uses an LLM to select and rank the best results.
        """
        entities = await self.get_cached_or_fresh_entities()

        if not entities:
            return None

        # Extract keywords via reranker
        keywords = self.search_engine.extract_keywords(query)

        # Run fuzzy and semantic searches concurrently
        async def run_fuzzy_searches():
            """Run fuzzy searches concurrently for all keywords."""
            tasks = [ self.search_engine.fuzzy_search(
                keyword = keyword,
                entities = entities,
                search_fields = self._entity_config[self.entity_type]["search_fields"]
                )
                for keyword in keywords
            ]
            
            fuzzy_batches = await asyncio.gather(*tasks)
            # Flatten all fuzzy results into one list
            fuzzy_results = [item for batch in fuzzy_batches for item in batch]
            # Remove duplicates
            
            def _unique_dicts_by_id(dict_results):
                id_field = self._entity_config[self.entity_type]['id_field']
                seen = set()
                unique = []

                for item in dict_results:
                    entity_id = item.get(id_field)
                    if entity_id not in seen:
                        seen.add(entity_id)
                        unique.append(item)
                return unique
            
            # return structured unique fuzzy results.
            return _unique_dicts_by_id(fuzzy_results)

        # Launch both searches concurrently
        fuzzy_task = run_fuzzy_searches()
        semantic_task = self.search_engine.semantic_search(query, entities)

        # Wait for both to complete
        unique_fuzzy_results, semantic_results = await asyncio.gather(fuzzy_task, semantic_task)

        # 2 step reranking Weighted RRF with a cross encoder filteration.
        selected = await self.reranker.rerank_results(
            query = query,
            fuzzy_results = unique_fuzzy_results,
            semantic_results = semantic_results,
            entity_type = self.entity_type
        )
        
        return selected

    async def get_entity_details(self, entity_id: str, action_lookup: dict, content: str = None) -> dict:
        """
        Fetch detailed information for a specific entity. return either metadata and/or summarized cached response
        """
        detail_methods = {
            'dataset': self.data_provider.show_dataset,
            'tool': self.data_provider.show_tool,
            'workflow': self.data_provider.show_workflow
        }

        try:
            self.log.info(f"Fetching details for {self.entity_type} with ID: {entity_id}")
            method = detail_methods[self.entity_type]

            if asyncio.iscoroutinefunction(method):
                result, name, link =  await method(entity_id)
            else:
                result, name, link = method(entity_id)
            if content:
                result["content"] = content
            if name and link:
                action_lookup[name] = {
                    "action" : "Execute",
                    "link" : link
                }
            return result
        
        except Exception as e:
            self.log.error(f"Could not retrieve details for {self.entity_type}:{entity_id}: {e}")
            return {"error": "Failed to retrieve details."}
        

    def clean_galaxy_context(self, text_list):
        """ 
        Cleans a list of Galaxy context strings (tools, workflows, datasets)
        for feeding into an LLM.
        """
        cleaned = []
        for text in text_list:
            # Remove Markdown links but keep text
            text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
            # Remove URLs
            text = re.sub(r'https?://\S+', '', text)
            # Remove quotes
            text = re.sub(r'["\']', '', text)
            # Remove brackets
            text = re.sub(r'[\[\]\{\}]', '', text)
            # Remove parentheses content (annotations)
            text = re.sub(r'\([^\)]*\)', '', text)
            # Collapse multiple spaces and strip
            text = re.sub(r'\s+', ' ', text).strip()
            if text:
                cleaned.append(text)
        return cleaned
 

    async def generate_final_response(self, query: str, retrieved_contents: list = None, cached_summaries: list[str] = None, global_content: list = None , action_lookup: dict[str, dict] = None):
        """Generates a final, user-facing natural language response based on the retrieved and processed information."""
        
        query_responses = ""
        global_responses = ""
        
        async def content_response(content):
            item_id = content.get(f"{self.entity_type}_id", "")
            prompt = SUMMARIZER_PROMPT.format(content=content)            
            response_text = await self.llm_response.get_response(prompt)
            self.log.info(f"context response: {response_text}")
            if item_id:
                self.cache.set_string(
                    key = f"{self.username}_{self.entity_type}_{item_id}",
                    value = response_text,
                    ttl = InformerTTLs.SUMMARY_TTL.value
                    )
            else:
                self.log.warning(f"{self.username} {self.entity_type} id isn't found for context summary caching.")
                
            return response_text
        
        all_results = []
        
        if retrieved_contents:
            tasks = [content_response(content) for content in retrieved_contents]
            task_results = await asyncio.gather(*tasks)
            all_results.extend(task_results)
        if cached_summaries:
            all_results.extend(cached_summaries)
        
        if all_results:        
            all_results = self.clean_galaxy_context(all_results)
            query_responses = "\n\n\n".join(str(r) for r in all_results if r)
        
        if global_content:
            global_content = self.clean_galaxy_context(global_content)
            global_responses = "\n\n\n".join(str(r) for r in global_content if r)
            self.log.info(f'context of the context: {global_responses}')
        
        if not query_responses.strip():
            self.log.info(f"No suitable {self.entity_type}s found in the users galaxy instance for the users needs.")
            query_responses = f"No suitable {self.entity_type}s found in the users galaxy instance for the users needs."
            
        if not global_responses.strip() and not global_content:
            self.log.info(f"No suitable {self.entity_type}s found in external sources for the users needs.")
            global_responses = f"No suitable {self.entity_type}s found in external sources for the users needs."

        self.log.info('Generating final response.')
        prompt = FINAL_RESPONSE_PROMPT.format(entity = self.entity_type,query=query, query_responses = query_responses, global_responses = global_responses)
        final_message = await self.llm_response.get_response(prompt)
        self.log.info(f"Final response: {final_message}")
        # return response_text
        
        actions = {}
        if action_lookup:
            action_names = list(action_lookup.keys())
            best_match = process.extractOne(
                query=final_message,
                choices=action_names,
                scorer=fuzz.token_set_ratio,   # best for LLM-style rephrasing / partial matches
                score_cutoff=70                         # tune if needed (70-85 range is usually perfect)
            )
            if best_match:
                name, score, _ = best_match
                actions[name] = action_lookup[name]
                self.log.info(f"Best fuzzy action match: {name} (score: {score})")
            else:
                self.log.info("No action matched with sufficient confidence (score < 75)")
        
        return final_message, actions

    async def get_entity_info(self, search_query: str, entity_id: str = None) -> dict:
        """The main public method to orchestrate the entire information retrieval process."""
        
        id_field = self._entity_config[self.entity_type]['id_field']
        found_entities = None
        
        if entity_id:
            self.log.info(f'Direct ID provided: {entity_id}. Retrieving details directly.')
            entities = await self.get_cached_or_fresh_entities()
            # Validate the id against the entity id in galaxy instance
            retrived_entity = next((e for e in entities if e[id_field] == entity_id), None)
            if retrived_entity != None:
                found_entities = [
                    {
                        'name': retrived_entity['name'],
                        id_field: retrived_entity[id_field],
                        'source': 'user_instance', 
                        'content': None 
                    }
                ]

        if found_entities is None:
            self.log.info(f"No ID provided or invalid ID. Searching for entities based on query: '{search_query}'")
            found_entities = await self.search_entities(query=search_query)

        if found_entities is None:
            return await self.generate_final_response(search_query, [{"message": "No relevant items found."}], [{"message": "No relevant items found."}])
        
        self.log.info(f"Relevant {self.entity_type}s found are: {[v.get('name') for v in found_entities]}")

        tasks = []
        global_results = []
        cached_summary = None
        cached_summaries = []
        action_lookup = {}
        
        for item_stub in found_entities:
            item_content = item_stub.get("content")
            if item_stub.get("source") == "user_instance":
                item_id = item_stub.get(id_field)
                if item_id:
                    
                    if self.entity_type == "workflow":
                        tasks.append(self.get_entity_details(entity_id = item_id, action_lookup = action_lookup, content= item_content))
                    else:
                        tasks.append(self.get_entity_details(entity_id = item_id, action_lookup = action_lookup))
            else:
                global_results.append(item_content)
                if self.entity_type == "workflow":
                    item_name = item_stub["name"]
                    action_lookup[item_name] = {
                        "action" : "Import",
                        "link": None
                        }
        
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            results = []
        
        response, actions = await self.generate_final_response(
            query = search_query,
            retrieved_contents = results,
            cached_summaries = cached_summaries,
            global_content = global_results,
            action_lookup = action_lookup
            )
        
        return response, actions
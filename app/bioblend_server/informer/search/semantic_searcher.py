import logging
import asyncio

from app.bioblend_server.informer.manager import InformerManager
from app.bioblend_server.informer.utils import LLMResponse


class SemanticSearcher:
    """Executes semantic vector-based searches."""
    
    def __init__(self, vector_manager: InformerManager, 
                 entity_type: str,
                 username: str,
                 score_threshold: float = 0.3,
                 limit: int = 10
                 ):
        """ Initialize semantic searcher. """
            
        self.log = logging.getLogger(__class__.__name__)
        
        self.vector_manager: InformerManager = vector_manager
        self.embedder = LLMResponse()
        self.entity_type = entity_type
        self.username = username
        self.score_threshold = score_threshold
        self.limit = limit
        
    def _determine_collection_names(self, entity_type: str, username: str) -> tuple[str, str]:
        """  Determine global and user collection names based on entity type. """
        
        global_collection = f"generic_galaxy_{entity_type}"
        # User collection naming varies by entity type
        if entity_type == "tool":
            # Tools are shared across users
            user_collection = f"Galaxy_{entity_type}"
        else:
            # Workflows and datasets are user-specific
            user_collection = f"Galaxy_{entity_type}_{username}"
        
        return global_collection, user_collection
    
    async def _search_collection(
        self, 
        collection_name: str, 
        query_vector: list, 
        source_tag: str
    ) -> list[dict]:
        """
        Search a single collection and tag results with source.
        
        Args:
            collection_name: Name of the Qdrant collection
            query_vector: Query embedding vector
            entity_type: Type of entity being searched
            source_tag: Tag to identify source ('global' or 'user_instance')
            
        Returns:
            List of search results with source tags
        """
        try:
            # Check if collection exists
            if not self.vector_manager.client.collection_exists(collection_name):
                self.log.warning(f"Collection '{collection_name}' does not exist. Skipping.")
                return []
            
            # Perform vector search
            results = self.vector_manager.search_by_vector(
                collection=collection_name,
                query_vector=query_vector,
                entity_type=self.entity_type,
                score_threshold= self.score_threshold,
                limit = self.limit
            )
            
            # Tag each result with source
            tagged_results = []
            for result in results:
                result['source'] = source_tag
                tagged_results.append(result)
            
            self.log.info(f"Found {len(tagged_results)} results in {collection_name}")
            return tagged_results
            
        except Exception as e:
            self.log.error(f"Error searching collection '{collection_name}': {e}")
            return []
        
    def _normalize_tool_id(self, tool_id: str) -> str:
        """ Normalize a tool id by stripping the version segment. """
        if not tool_id or not isinstance(tool_id, str):
            return ""
        if "/" in tool_id:
            parts = tool_id.split("/")
            # checking length to avoid stripping short IDs like 'owner/repo' incorrectly
            if len(parts) > 2: 
                return "/".join(parts[:-1])   
        return tool_id
    
    def _normalize_workflow_name(self, workflow_name: str) -> str:
        """ Normalize workflow names by removing "imported: " prefix if it exists"""
        return workflow_name.lower().removeprefix("imported: ").strip()
    
    def _merge_and_deduplicate(
        self, 
        global_results: list[dict], 
        user_results: list[dict], 
        entity_type: str,
        entities: list[dict]
    ) -> list[dict]:
        """
        Merges global and user results with perfect deduplication and fallback.
        
        - Tools: matched on normalized tool_id (version-stripped)
        - Workflows: matched on exact name (stripped)
        - If a user-owned entity was missed in user search but found in global → promote global result to user_instance
        - When both exist → prefer user item (keeps real ID), enrich with global content, average score
        """
        def average_score(u_score, g_score):
            if isinstance(u_score, (int, float)) and isinstance(g_score, (int, float)):
                return (u_score + g_score) / 2
            return u_score or g_score

        # Build lookup of actual user entities using the correct key
        user_entity_map = {}
        for entity in entities:
            if entity_type == "tool":
                tool_id = entity.get("tool_id", "")
                key = self._normalize_tool_id(tool_id) if tool_id else None
            elif entity_type == "workflow":
                workflow_name = entity.get("name", "").strip()
                key = self._normalize_workflow_name(workflow_name)
            else:
                key = entity.get("name", "").strip()

            if key:
                user_entity_map[key] = entity

        # Build global map using same key logic
        global_map = {}
        for item in global_results:
            if entity_type == "tool":
                raw_id = item.get("tool_id") or item.get("name", "")
                key = self._normalize_tool_id(raw_id)
            elif entity_type == "workflow":
                workflow_name = item.get("name", "").strip()
                key = self._normalize_workflow_name(workflow_name)
            else:
                key = item.get("name", "").strip()

            if key:
                global_map[key] = item

        merged_list = []

        # Process all user results first (they take priority)
        for user_item in user_results:
            if entity_type == "tool":
                raw_id = user_item.get("tool_id") or user_item.get("name", "")
                key = self._normalize_tool_id(raw_id)
            elif entity_type == "workflow":
                workflow_name = user_item.get("name", "").strip()
                key = self._normalize_workflow_name(workflow_name)
            else:
                key = user_item.get("name", "").strip()

            if not key:
                merged_list.append(user_item)
                continue

            if key in global_map:
                global_item = global_map.pop(key)  # remove so not added later

                # Start with user item (preserves correct ID, source='user_instance')
                merged_item = user_item.copy()

                # Enrich with better global metadata if available
                if global_item.get("content"):
                    merged_item["content"] = global_item["content"]

                # Average score
                merged_item["score"] = average_score(
                    user_item.get("score"), global_item.get("score")
                )

                merged_list.append(merged_item)

            else:
                # Only in user → keep as is
                merged_list.append(user_item)

        # Now handle remaining global items (those not matched above)
        for key, global_item in global_map.items():
            # DOUBLE-CHECK: Does this global result correspond to a real user-owned entity?
            if key in user_entity_map:
                entity = user_entity_map[key]

                # Promote global → user_instance
                promoted = global_item.copy()
                promoted["source"] = "user_instance"
                promoted[f"{entity_type}_id"] = entity.get(f"{entity_type}_id")

                # Use global content (usually richer), keep global score as fallback
                # (user didn't return it, so no user score to average)
                merged_list.append(promoted)
            else:
                # Truly global-only
                global_item["source"] = "global"  # ensure tag
                merged_list.append(global_item)

        return merged_list

    async def search(
        self, 
        query: str, 
        entity_type: str, 
        entities: list[dict]
    ) -> list[dict]:
        self.log.info(f"Starting dual-source search for {entity_type}")
        
        if isinstance(query, str):
            query = [query]
            
        query_vector = await self.embedder.get_embeddings(query)
        
        global_collection, user_collection = self._determine_collection_names(
            entity_type=entity_type,
            username=self.username
        )
        
        user_task = self._search_collection(user_collection, query_vector, "user_instance")
        
        if entity_type != "dataset":
            global_task = self._search_collection(global_collection, query_vector, "global")
            global_results, user_results = await asyncio.gather(global_task, user_task, return_exceptions=True)
        else:
            user_results = await user_task
            global_results = []

        # Handle exceptions
        if isinstance(global_results, Exception):
            self.log.error(f"Global search failed: {global_results}")
            global_results = []
        if isinstance(user_results, Exception):
            self.log.error(f"User search failed: {user_results}")
            user_results = []

        # NEW: All merging + double-check logic in one robust place
        combined_results = self._merge_and_deduplicate(
            global_results=global_results,
            user_results=user_results,
            entity_type=entity_type,
            entities=entities
        )
        
        self.log.info(f"Dual-source search complete. ")
        self.log.info(f"Raw Global search results: {len(global_results)}: {[names.get('name') for names in global_results]},")
        self.log.info(f"Raw User search results: {len(user_results)}: {[names.get('name') for names in user_results]}")
        self.log.info(f"merged count: {len(combined_results)}")
        
        return combined_results
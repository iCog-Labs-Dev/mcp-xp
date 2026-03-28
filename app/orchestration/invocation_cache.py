import json
from typing import Dict, List, Optional
import redis
import logging
import asyncio

from contextlib import asynccontextmanager

from app.enumerations import TTLiveConfig, InvocationStates

class InvocationCache:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.log = logging.getLogger(__class__.__name__)
    
    @asynccontextmanager
    async def acquire_lock(self, lock_key: str, timeout: int = 15, retry_delay: float = 0.1):
        """Async context manager for distributed lock using Redis SETNX."""
        acquired = False
        try:
            while not acquired:
                acquired = await asyncio.to_thread(
                    self.redis.set,
                    name = lock_key,
                    value = "locked",
                    nx=True, 
                    ex=timeout
                    )
                if not acquired:
                    await asyncio.sleep(retry_delay)
            yield
        finally:
            if acquired:
                await asyncio.to_thread(self.redis.delete, lock_key)
                
    async def get_workflows_cache(self, username: str) -> Optional[List[Dict]]:
        """Get cached processed workflows list"""
        try:
            cache_key = f"workflows:{username}"
            cached_data = await asyncio.to_thread(self.redis.get, cache_key)
            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            self.log.error(f"Error getting workflows cache: {e}")
            return None
    
    async def set_workflows_cache(self, username: str, workflows: List[Dict], ttl: int = TTLiveConfig.WORKFLOW_CACHE.value):
        """Cache processed workflows list with TTL"""
        try:
            cache_key = f"workflows:{username}"
            await asyncio.to_thread(self.redis.setex, cache_key, ttl, json.dumps(workflows))
        except Exception as e:
            self.log.error(f"Error setting workflows cache: {e}")
    
    async def get_invocation_workflow_mapping(self, username: str) -> Dict[str, Dict]:
        """Get cached mapping of invocation_id -> {workflow_name, workflow_id}"""
        try:
            cache_key = f"invocation_workflow_map:{username}"
            cached_data = await asyncio.to_thread(self.redis.hgetall, cache_key)
            if cached_data:
                # Convert bytes keys/values to strings and parse JSON values
                return {
                    k.decode() if isinstance(k, bytes) else k: json.loads(v.decode() if isinstance(v, bytes) else v)
                    for k, v in cached_data.items()
                }
            return {}
        except Exception as e:
            self.log.error(f"Error getting invocation workflow mapping: {e}")
            return {}
    
    async def set_invocation_workflow_mapping(self, username: str, mapping: Dict[str, Dict], ttl: int = TTLiveConfig.INVOCATION_WORKFLOW_MAPPING.value):
        """Cache invocation-workflow mapping with TTL"""
        try:
            cache_key = f"invocation_workflow_map:{username}"
            # Convert to JSON strings for Redis storage
            redis_mapping = {
                inv_id: json.dumps(wf_info)
                for inv_id, wf_info in mapping.items()
            }
            
            # Use pipeline for atomic operations
            pipe = await asyncio.to_thread(self.redis.pipeline)
            await asyncio.to_thread(pipe.delete, cache_key)  # Clear existing data
            if redis_mapping:  # Only set if we have data
                await asyncio.to_thread(pipe.hset, name=cache_key, mapping=redis_mapping)
            await asyncio.to_thread(pipe.expire, cache_key, ttl)
            await asyncio.to_thread(pipe.execute)
        except Exception as e:
            self.log.error(f"Error setting invocation workflow mapping: {e}")
    
    async def get_response_cache(self, username: str, workflow_id: str = None, history_id: str = None) -> Optional[Dict]:
        """Get cached response for specific filter combination"""
        try:
            cache_key = f"invocations_response:{username}:{workflow_id or 'all'}:{history_id or 'all'}"
            cached_data = await asyncio.to_thread(self.redis.get, cache_key)
            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            self.log.error(f"Error getting response cache: {e}")
            return None
    
    async def set_response_cache(self, username: str, response: Dict, workflow_id: str = None, 
                               history_id: str = None, ttl: int = TTLiveConfig.INVOCATION_LIST.value):
        """Cache response for specific filter combination with TTL"""
        try:
            cache_key = f"invocations_response:{username}:{workflow_id or 'all'}:{history_id or 'all'}"
            await asyncio.to_thread(self.redis.setex, cache_key, ttl, json.dumps(response))
        except Exception as e:
            self.log.error(f"Error setting response cache: {e}")
    
    async def get_deleted_invocations(self, username: str) -> set:
        """Get deleted invocations"""
        try:     
            # Refresh from Redis
            redis_key = f"deleted_invocations:{username}"
            deleted_set = {inv_id.decode() if isinstance(inv_id, bytes) else inv_id 
                          for inv_id in await asyncio.to_thread(self.redis.smembers, redis_key)}
                 
            return deleted_set
        except Exception as e:
            self.log.error(f"Error getting deleted invocations: {e}")
            return set()
    
    async def get_invocations_cache(self, username: str, filters: Dict) -> Optional[List[Dict]]:
        """Get cached invocations list"""
        try:
            # Create cache key based on filters
            filter_str = "_".join([f"{k}:{v}" for k, v in sorted(filters.items()) if v is not None])
            cache_key = f"invocations_raw:{username}:{filter_str or 'all'}"
            
            cached_data = await asyncio.to_thread(self.redis.get, cache_key)
            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            self.log.error(f"Error getting invocations cache: {e}")
            return None
    
    async def set_invocations_cache(self, username: str, invocations: List[Dict], 
                                  filters: Dict, ttl: int = TTLiveConfig.RAW_INVOCATION_LIST.value):
        """Cache invocations list with TTL"""
        try:
            filter_str = "_".join([f"{k}:{v}" for k, v in sorted(filters.items()) if v is not None])
            cache_key = f"invocations_raw:{username}:{filter_str or 'all'}"
            await asyncio.to_thread(self.redis.setex, cache_key, ttl, json.dumps(invocations))
        except Exception as e:
            self.log.error(f"Error setting invocations cache: {e}")
    
    async def add_to_deleted_invocations(self, username: str, invocation_ids: List[str]):
        """Add invocation IDs to the deleted set"""
        try:
            redis_key = f"deleted_invocations:{username}"
            if invocation_ids:
                await asyncio.to_thread(self.redis.sadd, redis_key, *invocation_ids)
        except Exception as e:
            self.log.error(f"Error adding to deleted invocations: {e}")
            
    async def is_duplicate_request(self, username: str, request_hash: str, ttl: int = TTLiveConfig.DUPLICATE_CHECK.value) -> bool:
        """Check if this is a duplicate request within TTL seconds"""
        try:
            cache_key = f"request_dedup:{username}:{request_hash}"
            exists = await asyncio.to_thread(self.redis.exists, cache_key)
            if not exists:
                await asyncio.to_thread(self.redis.setex, cache_key, ttl, "1")
                return False
            return True
        except Exception as e:
            self.log.error(f"Error checking duplicate request: {e}")
            return False
    
    async def is_duplicate_workflow_request(self, username: str, request_hash: str, ttl: int = TTLiveConfig.DUPLICATE_CHECK.value) -> bool:
        """Check if this is a duplicate workflow request within TTL seconds"""
        try:
            cache_key = f"workflow_request_dedup:{username}:{request_hash}"
            exists = await asyncio.to_thread(self.redis.exists, cache_key)
            if not exists:
                await asyncio.to_thread(self.redis.setex, cache_key, ttl, "1")
                return False
            return True
        except Exception as e:
            self.log.error(f"Error checking duplicate workflow request: {e}")
            return False
    
    async def get_invocation_result(self, username: str, invocation_id: str) -> Optional[Dict]:
        """Get cached full invocation result"""
        try:
            cache_key = f"invocation_result:{username}:{invocation_id}"
            cached_data = await asyncio.to_thread(self.redis.get, cache_key)
            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            self.log.error(f"Error getting invocation result: {e}")
            return None

    async def set_invocation_result(self, username: str, invocation_id: str, result: Dict, ttl: int = TTLiveConfig.INVOCACTION_RESULT.value):
        """Cache full invocation result with 1-day TTL"""
        try:
            cache_key = f"invocation_result:{username}:{invocation_id}"
            await asyncio.to_thread(self.redis.setex, cache_key, ttl, json.dumps(result))
        except Exception as e:
            self.log.error(f"Error setting invocation result: {e}")
            
    async def set_invocation_state(self, username: str, invocation_id: str, state: str):
        """Set or update an invocation state inside the hash for a given API key"""
        try:
            if state not in {InvocationStates.PENDING.value, InvocationStates.FAILED.value, InvocationStates.COMPLETE.value}:
                raise ValueError(f"Invalid state: {state}")
            cache_key = f"invocation_states:{username}"
            await asyncio.to_thread(self.redis.hset, cache_key, invocation_id, state)
            self.log.info(f" new invocation state has been set for user: {username} invocation id: {invocation_id}")
        except Exception as e:
            self.log.error(f"Error setting invocation state: {e}")

    async def get_invocation_state(self, username: str, invocation_id: str) -> Optional[str]:
        """Get a single invocation state"""
        try:
            cache_key = f"invocation_states:{username}"
            raw = await asyncio.to_thread(self.redis.hget, cache_key, invocation_id)
            return raw.decode() if isinstance(raw, bytes) else raw if raw else None
        except Exception as e:
            self.log.error(f"Error getting invocation state: {e}")
            return None

    async def delete_invocation_state(self, username: str, invocation_id: str):
        """Delete a single invocation state"""
        try:
            cache_key = f"invocation_states:{username}"
            await asyncio.to_thread(self.redis.hdel, cache_key, invocation_id)
        except Exception as e:
            self.log.error(f"Error deleting invocation state: {e}")
    
    async def add_deleted_workflows(self, username: str, workflow_ids: list[str]):
        """ Add multiple deleted workflow IDs to the user's deleted workflows set in Redis. """
        try:
            cache_key = f"deleted_workflows:{username}"
            def _add_to_set():
                pipe = self.redis.pipeline()
                pipe.sadd(cache_key, *workflow_ids)
                pipe.expire(cache_key, TTLiveConfig.WORKFLOW_CACHE.value)
                pipe.execute()
            await asyncio.to_thread(_add_to_set)
        except Exception as e:
            self.log.error(f"Failed to add deleted workflow IDs {workflow_ids} for user: {username}: {e}")

    async def get_deleted_workflows(self, username: str) -> list:
        """ Retrieve the set of deleted workflow IDs for a user from Redis. """
        try:
            cache_key = f"deleted_workflows:{username}"
            def _get_set_members():
                return list(self.redis.smembers(cache_key))
            return await asyncio.to_thread(_get_set_members)
        
        except Exception as e:
            self.log.error(f"Failed to retrieve deleted workflows for user: {username}: {e}")
            return []

    async def clear_deleted_workflows(self, username: str):
        """ Clear the deleted workflows set for a user in Redis. """
        try:
            cache_key = f"deleted_workflows:{username}"
            await asyncio.to_thread(self.redis.delete, cache_key)
            
        except Exception as e:
            self.log.error(f"Failed to clear deleted workflows for user: {username}: {e}")
    
    async def get_dataset_index(self, username: str, unique_id: str) -> Optional[Dict]:
        """Get cached dataset index result"""
        try:
            cache_key = f"dataset_index:{username}:{unique_id}"
            cached_data = await asyncio.to_thread(self.redis.get, cache_key)
            if cached_data:
                return json.loads(cached_data)
            return None
        except Exception as e:
            self.log.error(f"Error getting dataset index: {e}")
            return None

    async def set_dataset_index(self, username: str, unique_id: str, index_data: Dict, ttl: int = TTLiveConfig.SHORT_TTL.value):
        """Cache dataset index result"""
        try:
            cache_key = f"dataset_index:{username}:{unique_id}"
            await asyncio.to_thread(self.redis.setex, cache_key, ttl, json.dumps(index_data))
        except Exception as e:
            self.log.error(f"Error setting dataset index: {e}")
    
    async def delete_dataset_index(self, username: str, unique_id: str) -> None:
        """Remove cached dataset index result (used on failure or forced retry)"""
        try:
            cache_key = f"dataset_index:{username}:{unique_id}"
            await asyncio.to_thread(self.redis.delete, cache_key)
            self.log.info(f"Deleted dataset index cache for {username}:{unique_id}")
        except Exception as e:
            self.log.error(f"Error deleting dataset index: {e}")
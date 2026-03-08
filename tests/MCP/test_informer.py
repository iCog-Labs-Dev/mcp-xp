import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

from sys import path
path.append(".")

from app.galaxy import GalaxyClient
from app.bioblend_server.informer.informer import GalaxyInformer

# --- Fixtures ---

@pytest.fixture
def mock_galaxy_client():
    """Fixture for a mocked GalaxyClient."""
    mock = MagicMock(spec=GalaxyClient)
    mock.whoami = "test_user"
    mock.gi_client = MagicMock()
    mock.gi_admin = MagicMock()
    mock.user_api_key = "test_api_key"
    mock.galaxy_url = "http://test-galaxy.com"
    return mock

@pytest.fixture
def mock_informer_manager():
    """Fixture for a mocked InformerManager."""
    mock = AsyncMock()
    mock.create = AsyncMock(return_value=mock)
    return mock

@pytest.fixture
def mock_redis_cache():
    """Fixture for a mocked RedisCache."""
    return MagicMock()

@pytest.fixture
def mock_redis_indexer():
    """Fixture for a mocked RedisIndexer."""
    return MagicMock()

@pytest.fixture
def mock_qdrant_indexer():
    """Fixture for a mocked QdrantIndexer."""
    return MagicMock()

@pytest.fixture
def mock_search_engine():
    """Fixture for a mocked SearchEngine."""
    return MagicMock()

@pytest.fixture
def mock_reranker():
    """Fixture for a mocked InformerReranker."""
    return MagicMock()

@pytest.fixture
def mock_llm_response():
    """Fixture for a mocked LLMResponse."""
    mock = MagicMock()
    mock.get_response = AsyncMock(return_value="Mocked LLM Response")
    mock.get_embeddings = AsyncMock(return_value=[0.1, 0.2])
    return mock


@pytest.fixture
async def galaxy_informer(
    mock_galaxy_client,
    mock_informer_manager,
    mock_redis_cache,
    mock_redis_indexer,
    mock_qdrant_indexer,
    mock_search_engine,
    mock_reranker,
    mock_llm_response,
):
    """Fixture to create a GalaxyInformer instance with mocked dependencies."""
    with patch("app.bioblend_server.informer.manager.InformerManager", new=mock_informer_manager), \
         patch("app.bioblend_server.informer.informer.RedisCache", return_value=mock_redis_cache), \
         patch("app.bioblend_server.informer.informer.RedisIndexer", return_value=mock_redis_indexer), \
         patch("app.bioblend_server.informer.informer.QdrantIndexer", return_value=mock_qdrant_indexer), \
         patch("app.bioblend_server.informer.informer.SearchEngine", return_value=mock_search_engine), \
         patch("app.bioblend_server.informer.informer.InformerReranker", return_value=mock_reranker), \
         patch("app.bioblend_server.informer.informer.LLMResponse", return_value=mock_llm_response):
        
        # Initialize as 'tool' by default
        informer = await GalaxyInformer.create(galaxy_client=mock_galaxy_client, entity_type="tool")
        return informer


# --- Test Classes ---

class TestGalaxyInformerCreation:
    """Tests for the creation of a GalaxyInformer instance."""

    @pytest.mark.asyncio
    async def test_galaxy_informer_creation(self, galaxy_informer):
        """Test that GalaxyInformer can be created successfully."""
        assert galaxy_informer is not None
        assert galaxy_informer.entity_type == "tool"
        assert galaxy_informer.username == "test_user"
        assert galaxy_informer.manager is not None
        assert galaxy_informer.cache is not None
        assert galaxy_informer.redis_indexer is not None
        assert galaxy_informer.qdrant_indexer is not None
        assert galaxy_informer.search_engine is not None
        assert galaxy_informer.reranker is not None


class TestGetAllEntities:
    """Tests for the get_all_entities method."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("entity_type, mock_method_name", [
        ("tool", "get_tools"),
        ("workflow", "get_workflows"),
        ("dataset", "get_datasets"),
    ])
    async def test_get_all_entities(self, galaxy_informer, entity_type, mock_method_name):
        """Test that get_all_entities calls the correct data_provider method."""
        # Arrange
        galaxy_informer.entity_type = entity_type
        mock_entity_data = ([{"id": "1", "name": f"test_{entity_type}"}], ["test_name"])
        
        # Mock the specific method on the data_provider
        mock_method = MagicMock(return_value=mock_entity_data)
        setattr(galaxy_informer.data_provider, mock_method_name, mock_method)

        # Update the config to point to the new mock method
        galaxy_informer._entity_config[entity_type]['get_method'] = mock_method

        # Act
        result = galaxy_informer.get_all_entities()

        # Assert
        mock_method.assert_called_once()
        assert result == mock_entity_data


class TestGetCachedOrFreshEntities:
    """Tests for the get_cached_or_fresh_entities method."""

    @pytest.mark.asyncio
    async def test_get_cached_entities_hit(self, galaxy_informer):
        """Test cache hit scenario."""
        # Arrange
        cached_entities = [{"id": "1", "name": "cached_tool"}]
        galaxy_informer.cache.get_entities.return_value = cached_entities
        galaxy_informer.refresh_and_cache_entities = AsyncMock()
        galaxy_informer.search_engine.get_collection_name.return_value = ("collection", "corpus")

        # Act
        result = await galaxy_informer.get_cached_or_fresh_entities()

        # Assert
        galaxy_informer.cache.get_entities.assert_called_once()
        galaxy_informer.refresh_and_cache_entities.assert_not_called()
        assert result == cached_entities

    @pytest.mark.asyncio
    async def test_get_fresh_entities_miss(self, galaxy_informer):
        """Test cache miss scenario."""
        # Arrange
        fresh_entities = [{"id": "2", "name": "fresh_tool"}]
        galaxy_informer.cache.get_entities.return_value = None
        galaxy_informer.refresh_and_cache_entities = AsyncMock(return_value=fresh_entities)
        galaxy_informer.search_engine.get_collection_name.return_value = ("collection", "corpus")

        # Act
        result = await galaxy_informer.get_cached_or_fresh_entities()

        # Assert
        galaxy_informer.cache.get_entities.assert_called_once()
        galaxy_informer.refresh_and_cache_entities.assert_called_once()
        assert result == fresh_entities


class TestRefreshAndCacheEntities:
    """Tests for the refresh_and_cache_entities method."""

    @pytest.mark.asyncio
    async def test_refresh_and_cache_entities_calls_indexers(self, galaxy_informer):
        """Test that refresh_and_cache_entities calls the correct indexer methods."""
        # Arrange
        entities = [{"id": "1", "name": "test_entity"}]
        name_corpus = ["test_entity"]
        galaxy_informer.get_all_entities = MagicMock(return_value=(entities, name_corpus))
        
        galaxy_informer.redis_indexer.index_entities = AsyncMock()
        galaxy_informer.qdrant_indexer.index_entities = AsyncMock()
        galaxy_informer.search_engine.get_collection_name.return_value = ("collection", "corpus")

        # Act
        await galaxy_informer.refresh_and_cache_entities()

        # Assert
        galaxy_informer.redis_indexer.index_entities.assert_any_call(
            entities=name_corpus,
            collection_name="corpus",
            ttl=galaxy_informer._entity_config[galaxy_informer.entity_type]['ttl']
        )
    
        galaxy_informer.redis_indexer.index_entities.assert_any_call(
            entities=entities,
            collection_name="collection",
            ttl=galaxy_informer._entity_config[galaxy_informer.entity_type]['ttl']
        )
        galaxy_informer.qdrant_indexer.index_entities.assert_called_once_with(
            entities=entities,
            collection_name="collection"
        )


class TestSearchEntities:
    """Tests for the search_entities method."""

    @pytest.mark.asyncio
    async def test_search_entities_flow(self, galaxy_informer):
        """Test the full flow of the search_entities method."""
        # Arrange
        query = "test query"
        entities = [{"tool_id": "1", "name": "test_entity"}]
        keywords = ["test", "query"]
        fuzzy_results = [{"tool_id": "1", "name": "test_entity"}]
        semantic_results = [{"tool_id": "1", "name": "test_entity"}]
        reranked_results = [{"tool_id": "1", "name": "test_entity"}]

        galaxy_informer.get_cached_or_fresh_entities = AsyncMock(return_value=entities)
        galaxy_informer.search_engine.extract_keywords.return_value = keywords
        galaxy_informer.search_engine.fuzzy_search = AsyncMock(return_value=fuzzy_results)
        galaxy_informer.search_engine.semantic_search = AsyncMock(return_value=semantic_results)
        galaxy_informer.reranker.rerank_results = AsyncMock(return_value=reranked_results)
        
        # Act
        result = await galaxy_informer.search_entities(query)

        # Assert
        galaxy_informer.get_cached_or_fresh_entities.assert_called_once()
        galaxy_informer.search_engine.extract_keywords.assert_called_once_with(query)
        assert galaxy_informer.search_engine.fuzzy_search.call_count == len(keywords)
        galaxy_informer.search_engine.semantic_search.assert_called_once_with(query, entities)
        galaxy_informer.reranker.rerank_results.assert_called_once_with(
            query=query,
            fuzzy_results=fuzzy_results,
            semantic_results=semantic_results,
            entity_type=galaxy_informer.entity_type
        )
        assert result == reranked_results


class TestGetEntityDetails:
    """Tests for the get_entity_details method."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("entity_type, method_name, is_async", [
        ("dataset", "show_dataset", False),
        ("tool", "show_tool", True),
        ("workflow", "show_workflow", False),
    ])
    async def test_get_entity_details_calls_correct_method(self, galaxy_informer, entity_type, method_name, is_async):
        """Test that get_entity_details calls the correct method on the data_provider."""
        # Arrange
        entity_id = "test_id"
        expected_details = {"id": entity_id, "name": "details"}
        action_lookup = {}
        galaxy_informer.entity_type = entity_type

        # Detail methods now return (result_dict, name, link) tuple
        if is_async:
            mock_method = AsyncMock(return_value=(expected_details, "details", "http://link"))
        else:
            mock_method = MagicMock(return_value=(expected_details, "details", "http://link"))
        
        setattr(galaxy_informer.data_provider, method_name, mock_method)

        # Act
        result = await galaxy_informer.get_entity_details(entity_id, action_lookup=action_lookup)

        # Assert
        mock_method.assert_called_once_with(entity_id)
        assert result == expected_details


class TestGenerateFinalResponse:
    """Tests for the generate_final_response method."""

    @pytest.mark.asyncio
    async def test_summarizes_and_caches_content(self, galaxy_informer):
        """Test that content is summarized and the summary is cached."""
        # Arrange
        galaxy_informer.entity_type = "tool"
        galaxy_informer.username = "test_user"
        query = "test query"
        retrieved_content = [{"tool_id": "tool1", "content": "some tool content"}]
        summary = "This is a summary."

        galaxy_informer.llm_response.get_response = AsyncMock(return_value=summary)

        # Act
        await galaxy_informer.generate_final_response(query, retrieved_contents=retrieved_content)

        # Assert
        assert galaxy_informer.llm_response.get_response.call_count == 2 # Once for summary, once for final response
        
        # Check cache call
        galaxy_informer.cache.set_string.assert_called_once()
        cache_args, cache_kwargs = galaxy_informer.cache.set_string.call_args
        assert cache_kwargs['key'] == "test_user_tool_tool1"
        assert cache_kwargs['value'] == summary
        assert 'ttl' in cache_kwargs


class TestGetEntityInfo:
    """
    Tests for the main orchestration method: get_entity_info.
    This tests the integration logic between search, cache, details, and response generation.
    """

    @pytest.mark.asyncio
    async def test_direct_id_lookup_success(self, galaxy_informer):
        """Test that providing a valid entity_id bypasses search and uses that entity directly."""
        # Arrange
        entity_id = "tool_123"
        query = "irrelevant query"
        galaxy_informer.entity_type = "tool"
        
        # Mock entities existing in the system
        galaxy_informer.get_cached_or_fresh_entities = AsyncMock(return_value=[
            {"tool_id": "tool_123", "name": "Target Tool", "content": "raw content"}
        ])
        
        # Mocks
        galaxy_informer.search_entities = AsyncMock()

        galaxy_informer.cache.get_string.return_value = None 
        galaxy_informer.get_entity_details = AsyncMock(return_value={"detailed": "info"})
        galaxy_informer.generate_final_response = AsyncMock(return_value=("Final Answer", {}))

        # Act
        response, actions = await galaxy_informer.get_entity_info(query, entity_id=entity_id)

        # Assert
        galaxy_informer.get_cached_or_fresh_entities.assert_called_once()
        galaxy_informer.search_entities.assert_not_called()
        galaxy_informer.get_entity_details.assert_called_once_with(entity_id=entity_id, action_lookup={}) 
        assert response == "Final Answer"

    @pytest.mark.asyncio
    async def test_direct_id_lookup_invalid_falls_back_to_search(self, galaxy_informer):
        """Test that an invalid ID (not in entities list) triggers a fallback to search."""
        # Arrange
        entity_id = "fake_id"
        query = "actual query"
        galaxy_informer.entity_type = "tool"
        
        # Mock entities list that DOES NOT contain fake_id
        galaxy_informer.get_cached_or_fresh_entities = AsyncMock(return_value=[
            {"tool_id": "other_id", "name": "Other"}
        ])
        
        # Mocks
        galaxy_informer.search_entities = AsyncMock(return_value=[]) # Search is triggered
        galaxy_informer.generate_final_response = AsyncMock(return_value=("No results", {}))

        # Act
        await galaxy_informer.get_entity_info(query, entity_id=entity_id)

        # Assert
        galaxy_informer.search_entities.assert_called_once_with(query=query)

    @pytest.mark.asyncio
    async def test_processing_separates_user_and_global_results(self, galaxy_informer):
        """
        Test that:
        1. 'user_instance' items trigger get_entity_details.
        2. 'global' items are collected separately and do NOT trigger get_entity_details.
        """
        # Arrange
        query = "search"
        galaxy_informer.entity_type = "tool"
        galaxy_informer.username = "test_user"
        
        # Mock Search Results
        search_results = [
            {"tool_id": "local_1", "source": "user_instance", "content": "local content"},
            {"tool_id": "global_1", "source": "global", "content": "global content"}
        ]
        
        galaxy_informer.search_entities = AsyncMock(return_value=search_results)
        galaxy_informer.cache.get_string.return_value = None # No cached summary
        galaxy_informer.get_entity_details = AsyncMock(return_value={"details": "fetched details"})
        galaxy_informer.generate_final_response = AsyncMock(return_value=("Done", {}))

        # Act
        await galaxy_informer.get_entity_info(query)

        # Assert
        # get_entity_details now requires action_lookup kwarg
        galaxy_informer.get_entity_details.assert_called_once()
        call_args = galaxy_informer.get_entity_details.call_args
        assert call_args.kwargs['entity_id'] == 'local_1'
        assert 'action_lookup' in call_args.kwargs
        
        # Verify generate_final_response received the correct mix
        galaxy_informer.generate_final_response.assert_called_once()
        call_kwargs = galaxy_informer.generate_final_response.call_args.kwargs

        assert "global content" in call_kwargs['global_content']
        # Retrieved contents 
        assert call_kwargs['retrieved_contents'][0] == {"details": "fetched details"}

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_uses_cached_summary_if_available(self, galaxy_informer):
        """
        Test that if a summary is in Redis, the entity is still fetched
        but the cached summary is passed to generate_final_response.
        """
        # Arrange
        galaxy_informer.entity_type = "tool"
        galaxy_informer.username = "u"
        
        # Search returns ONE item
        search_results = [{"tool_id": "t1", "source": "user_instance", "content": "..."}]
        galaxy_informer.search_entities = AsyncMock(return_value=search_results)
        
        # Cache Hit
        galaxy_informer.cache.get_string.return_value = "Cached Summary Text"
        galaxy_informer.get_entity_details = AsyncMock(return_value={"tool_id": "t1"})
        galaxy_informer.generate_final_response = AsyncMock(return_value=("Mocked LLM Response", {}))

        # Act
        await galaxy_informer.get_entity_info("query")

        # Assert - get_entity_details IS called (current implementation always calls it)
        galaxy_informer.get_entity_details.assert_called_once()
        
        # Verify generate_final_response was called
        galaxy_informer.generate_final_response.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_results_found(self, galaxy_informer):
        """Test behavior when search returns None or empty list."""
        # Arrange
        galaxy_informer.search_entities = AsyncMock(return_value=None)
        galaxy_informer.generate_final_response = AsyncMock(return_value=("Not Found", {}))

        # Act
        await galaxy_informer.get_entity_info("query")

        # Assert
        galaxy_informer.generate_final_response.assert_called_once()
        call_args = galaxy_informer.generate_final_response.call_args
        
        # retrieved_contents is the 2nd argument
        arg_2 = call_args[0][1] 
        assert arg_2 == [{"message": "No relevant items found."}]



class TestRedisCache:
    """Tests for the RedisCache class."""

    @pytest.fixture
    def redis_cache(self):
        """Fixture for RedisCache with mocked Redis client."""
        with patch('app.bioblend_server.informer.cache.redis.Redis') as mock_redis:
            from app.bioblend_server.informer.cache import RedisCache
            cache = RedisCache(host='localhost', port=6379, db=0)
            cache.client = MagicMock()
            return cache

    def test_get_entities_success(self, redis_cache):
        """Test successful retrieval of cached entities."""
        # Arrange
        entities = [{"id": "1", "name": "test"}]
        redis_cache.client.get.return_value = json.dumps(entities)

        # Act
        result = redis_cache.get_entities("test_collection")

        # Assert
        redis_cache.client.get.assert_called_once_with("test_collection")
        assert result == entities

    def test_get_entities_cache_miss(self, redis_cache):
        """Test cache miss returns None."""
        # Arrange
        redis_cache.client.get.return_value = None

        # Act
        result = redis_cache.get_entities("test_collection")

        # Assert
        assert result is None

    def test_get_entities_redis_error(self, redis_cache):
        """Test Redis error handling."""
        # Arrange
        import redis as redis_module
        redis_cache.client.get.side_effect = redis_module.RedisError("Connection failed")

        # Act
        result = redis_cache.get_entities("test_collection")

        # Assert
        assert result is None

    def test_get_entities_json_decode_error(self, redis_cache):
        """Test JSON decode error handling."""
        # Arrange
        redis_cache.client.get.return_value = "invalid json"

        # Act
        result = redis_cache.get_entities("test_collection")

        # Assert
        assert result is None


    def test_set_entities_success_with_dict(self, redis_cache):
        """Test successful caching of entities as dict."""
        # Arrange
        entities = [{"id": "1", "name": "test"}]
        redis_cache.client.setex.return_value = True

        # Act
        result = redis_cache.set_entities("test_collection", entities, 3600)

        # Assert
        redis_cache.client.setex.assert_called_once_with("test_collection", 3600, json.dumps(entities))
        assert result is True

    def test_set_entities_success_with_string(self, redis_cache):
        """Test successful caching of entities as string."""
        # Arrange
        entities_str = '["test"]'
        redis_cache.client.setex.return_value = True

        # Act
        result = redis_cache.set_entities("test_collection", entities_str, 3600)

        # Assert
        redis_cache.client.setex.assert_called_once_with("test_collection", 3600, entities_str)
        assert result is True

    def test_set_entities_failure(self, redis_cache):
        """Test failure when caching entities."""
        # Arrange
        entities = [{"id": "1"}]
        redis_cache.client.setex.side_effect = Exception("Write failed")

        # Act
        result = redis_cache.set_entities("test_collection", entities, 3600)

        # Assert
        assert result is False

    def test_get_string_success(self, redis_cache):
        """Test successful retrieval of string value."""
        # Arrange
        redis_cache.client.get.return_value = "test_value"

        # Act
        result = redis_cache.get_string("test_key")

        # Assert
        redis_cache.client.get.assert_called_once_with("test_key")
        assert result == "test_value"

    def test_get_string_cache_miss(self, redis_cache):
        """Test string cache miss returns None."""
        # Arrange
        redis_cache.client.get.return_value = None

        # Act
        result = redis_cache.get_string("test_key")

        # Assert
        assert result is None


    def test_get_string_redis_error(self, redis_cache):
        """Test Redis error handling for string retrieval."""
        # Arrange
        import redis as redis_module
        redis_cache.client.get.side_effect = redis_module.RedisError("Connection failed")

        # Act
        result = redis_cache.get_string("test_key")

        # Assert
        assert result is None

    def test_set_string_success(self, redis_cache):
        """Test successful caching of string value."""
        # Arrange
        redis_cache.client.setex.return_value = True

        # Act
        result = redis_cache.set_string("test_key", "test_value", 3600)

        # Assert
        redis_cache.client.setex.assert_called_once_with("test_key", 3600, "test_value")
        assert result is True

    def test_set_string_failure(self, redis_cache):
        """Test failure when caching string."""
        # Arrange
        import redis as redis_module
        redis_cache.client.setex.side_effect = redis_module.RedisError("Write failed")

        # Act
        result = redis_cache.set_string("test_key", "test_value", 3600)

        # Assert
        assert result is False

    def test_delete_entities_success(self, redis_cache):
        """Test successful deletion of cached entities."""
        # Arrange
        redis_cache.client.delete.return_value = 1

        # Act
        result = redis_cache.delete_entities("test_collection")

        # Assert
        redis_cache.client.delete.assert_called_once_with("test_collection")
        assert result is True

    def test_delete_entities_failure(self, redis_cache):
        """Test failure when deleting entities."""
        # Arrange
        import redis as redis_module
        redis_cache.client.delete.side_effect = redis_module.RedisError("Delete failed")

        # Act
        result = redis_cache.delete_entities("test_collection")

        # Assert
        assert result is False



class TestGalaxyDataProvider:
    """Tests for the GalaxyDataProvider class."""

    @pytest.fixture
    def data_provider(self, mock_galaxy_client):
        """Fixture for GalaxyDataProvider with mocked GalaxyClient."""
        from app.bioblend_server.informer.data_provider import GalaxyDataProvider
        return GalaxyDataProvider(galaxy_client=mock_galaxy_client, entity_type="tool")

    def test_init(self, data_provider, mock_galaxy_client):
        """Test GalaxyDataProvider initialization."""
        assert data_provider.galaxy_client == mock_galaxy_client
        assert data_provider.entity_type == "tool"
        assert data_provider.username == "test_user"

    @pytest.mark.parametrize("path, expected", [
        ("/path/to/file.txt", "file.txt"),
        ("file.txt", "file.txt"),
        ("/nested/path/to/data.csv", "data.csv"),
        ("simple", "simple"),
    ])
    def test_extract_filename(self, data_provider, path, expected):
        """Test filename extraction from various path formats."""
        result = data_provider.extract_filename(path)
        assert result == expected

    @pytest.mark.asyncio
    async def test_run_get_request_success(self, data_provider):
        """Test successful GET request."""
        # Arrange
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": "test"}
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            
            # Act
            result = await data_provider.run_get_request("http://test.com", {}, {})
            
            # Assert
            assert result == {"data": "test"}

    @pytest.mark.asyncio
    async def test_run_get_request_http_error(self, data_provider):
        """Test GET request with HTTP error."""
        # Arrange
        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=Exception("HTTP Error")
            )
            
            # Act & Assert
            with pytest.raises(Exception):
                await data_provider.run_get_request("http://test.com", {}, {})


    @pytest.mark.asyncio
    async def test_run_post_request_success(self, data_provider):
        """Test successful POST request."""
        # Arrange
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": "success"}
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            
            # Act
            result = await data_provider.run_post_request("http://test.com", json_data={"key": "value"})
            
            # Assert
            assert result == {"result": "success"}

    @pytest.mark.asyncio
    async def test_run_post_request_http_error(self, data_provider):
        """Test POST request with HTTP error."""
        # Arrange
        with patch('httpx.AsyncClient') as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("HTTP Error")
            )
            
            # Act & Assert
            with pytest.raises(Exception):
                await data_provider.run_post_request("http://test.com")

    def test_get_datasets_from_libraries(self, data_provider):
        """Test dataset retrieval from libraries."""
        # Arrange
        mock_libraries = [{"id": "lib1", "name": "Library 1"}]
        mock_library_details = [
            {"id": "ds1", "name": "/path/to/dataset.txt", "type": "file"}
        ]
        
        data_provider.gi_user.libraries.get_libraries.return_value = mock_libraries
        data_provider.gi_user.libraries.show_library.return_value = mock_library_details

        # Act
        datasets, name_corpus = data_provider.get_datasets()

        # Assert
        assert len(datasets) >= 1
        assert any(ds["dataset_id"] == "ds1" for ds in datasets)
        assert "dataset.txt" in name_corpus

    def test_get_datasets_from_histories(self, data_provider):
        """Test dataset retrieval from histories."""
        # Arrange
        mock_datasets = [
            {"id": "ds2", "name": "history_data.csv", "type": "file", "url": "/api/datasets/ds2"}
        ]
        
        data_provider.gi_user.libraries.get_libraries.return_value = []
        data_provider.gi_user.datasets.get_datasets.return_value = mock_datasets

        # Act
        datasets, name_corpus = data_provider.get_datasets()

        # Assert
        assert len(datasets) >= 1
        assert any(ds["dataset_id"] == "ds2" for ds in datasets)
        assert "history_data.csv" in name_corpus


    def test_get_datasets_error_handling(self, data_provider):
        """Test error handling in dataset retrieval."""
        # Arrange
        data_provider.gi_user.libraries.get_libraries.side_effect = Exception("Library error")
        data_provider.gi_user.datasets.get_datasets.side_effect = Exception("Dataset error")

        # Act
        datasets, name_corpus = data_provider.get_datasets()

        # Assert - should return empty lists on error
        assert datasets == []
        assert name_corpus == []

    def test_get_tools_success(self, data_provider):
        """Test successful tool retrieval."""
        # Arrange
        mock_tools = [
            {"id": "tool1", "name": "Test Tool", "description": "A test tool"}
        ]
        data_provider.gi_user.tools.get_tools.return_value = mock_tools

        # Act
        tools, name_corpus = data_provider.get_tools()

        # Assert
        assert len(tools) == 1
        assert tools[0]["tool_id"] == "tool1"
        assert tools[0]["name"] == "Test Tool"
        assert "Test Tool" in name_corpus

    def test_get_tools_data_manager_detection(self, data_provider):
        """Test data manager tool detection."""
        # Arrange
        mock_tools = [
            {"id": "data_manager_tool", "name": "Data Manager Tool", "description": "Manages data", "config_file": "data_manager.xml"}
        ]
        data_provider.gi_user.tools.get_tools.return_value = mock_tools

        # Act
        tools, _ = data_provider.get_tools()

        # Assert
        assert tools[0]["tool_type"] == "data manager"
        assert "Data Manager tool" in tools[0]["content"]

    def test_get_workflows_success(self, data_provider):
        """Test successful workflow retrieval."""
        # Arrange
        mock_workflows = [
            {
                "id": "wf1",
                "name": "Test Workflow",
                "owner": "test_owner",
                "model_class": "StoredWorkflow",
                "annotations": "Test annotation"
            }
        ]
        data_provider.gi_user.workflows.get_workflows.return_value = mock_workflows

        # Act
        workflows, name_corpus = data_provider.get_workflows()

        # Assert
        assert len(workflows) == 1
        assert workflows[0]["workflow_id"] == "wf1"
        assert workflows[0]["name"] == "Test Workflow"
        assert "Test Workflow" in name_corpus


    def test_show_workflow_success(self, data_provider):
        """Test successful workflow metadata retrieval."""
        # Arrange
        mock_workflow = {
            "id": "wf1",
            "name": "Test Workflow",
            "steps": {},
            "inputs": {}
        }
        data_provider.gi_user.workflows.show_workflow.return_value = mock_workflow

        # Act
        workflow, name, link = data_provider.show_workflow("wf1")

        # Assert
        assert workflow is not None
        assert name == "Test Workflow"
        data_provider.gi_user.workflows.show_workflow.assert_called_once_with("wf1")

    def test_show_workflow_cleaning_metadata(self, data_provider):
        """Test workflow metadata cleaning."""
        # Arrange
        mock_workflow = {
            "id": "wf1",
            "name": "Test Workflow",
            "model_class": "StoredWorkflow",
            "create_time": "2024-01-01",
            "steps": {},
            "inputs": {}
        }
        data_provider.gi_user.workflows.show_workflow.return_value = mock_workflow

        # Act
        workflow, name, link = data_provider.show_workflow("wf1")

        # Assert
        assert "model_class" not in workflow
        assert "create_time" not in workflow
        assert workflow["workflow_id"] == "wf1"

    def test_show_workflow_error_handling(self, data_provider):
        """Test error handling in show_workflow."""
        # Arrange
        data_provider.gi_user.workflows.show_workflow.side_effect = Exception("Workflow not found")

        # Act & Assert
        with pytest.raises(Exception):
            data_provider.show_workflow("invalid_id")

    @pytest.mark.asyncio
    async def test_show_tool_success(self, data_provider):
        """Test successful tool metadata retrieval."""
        # Arrange
        mock_tool = {
            "id": "tool1",
            "name": "Test Tool",
            "inputs": [],
            "help": "<p>Help text</p>"
        }
        mock_histories = [{"id": "hist1"}]
        
        data_provider.gi_user.histories.get_histories.return_value = mock_histories
        
        with patch.object(data_provider, 'run_get_request', new=AsyncMock(return_value=mock_tool)):
            # Act
            tool, name, link = await data_provider.show_tool("tool1")

            # Assert
            assert tool is not None
            assert name == "Test Tool"


    @pytest.mark.asyncio
    async def test_show_tool_no_histories(self, data_provider):
        """Test show_tool when no histories are available."""
        # Arrange
        data_provider.gi_user.histories.get_histories.return_value = []

        # Act & Assert
        with pytest.raises(RuntimeError, match="No histories available"):
            await data_provider.show_tool("tool1")

    @pytest.mark.asyncio
    async def test_show_tool_error_handling(self, data_provider):
        """Test error handling in show_tool."""
        # Arrange
        mock_histories = [{"id": "hist1"}]
        data_provider.gi_user.histories.get_histories.return_value = mock_histories
        
        with patch.object(data_provider, 'run_get_request', new=AsyncMock(side_effect=Exception("API error"))):
            # Act & Assert
            with pytest.raises(Exception):
                await data_provider.show_tool("tool1")

    def test_show_dataset_success(self, data_provider):
        """Test successful dataset metadata retrieval."""
        # Arrange
        mock_dataset = {
            "id": "ds1",
            "name": "Test Dataset",
            "file_size": 1024
        }
        data_provider.gi_user.datasets.show_dataset.return_value = mock_dataset

        # Act
        dataset, name, link = data_provider.show_dataset("ds1")

        # Assert
        assert dataset == mock_dataset
        assert name == "Test Dataset"
        assert "datasets/ds1/details" in link

    def test_show_dataset_error_handling(self, data_provider):
        """Test error handling in show_dataset."""
        # Arrange
        data_provider.gi_user.datasets.show_dataset.side_effect = Exception("Dataset not found")

        # Act & Assert
        with pytest.raises(Exception):
            data_provider.show_dataset("invalid_id")

    def test_prune_empty_nested_dict(self, data_provider):
        """Test pruning empty values from dict."""
        # Arrange
        test_dict = {
            "key1": "value1",
            "key2": None,
            "key3": "",
            "key4": "  ",
            "key5": {"nested": "value"},
            "key6": {}
        }

        # Act
        result = data_provider._prune_empty_nested(test_dict)

        # Assert
        assert "key1" in result
        assert "key2" not in result
        assert "key3" not in result
        assert "key4" not in result
        assert "key5" in result
        assert "key6" not in result


    def test_prune_empty_nested_list(self, data_provider):
        """Test pruning empty values from list."""
        # Arrange
        test_list = ["value1", None, "", "  ", {"key": "value"}, {}]

        # Act
        result = data_provider._prune_empty_nested(test_list)

        # Assert
        assert "value1" in result
        assert None not in result
        assert "" not in result
        assert {} not in result
        assert {"key": "value"} in result

    def test_prune_empty_nested_complex(self, data_provider):
        """Test pruning nested structures."""
        # Arrange
        test_obj = {
            "level1": {
                "level2": {
                    "value": "keep",
                    "empty": None
                },
                "empty_dict": {}
            },
            "list": [1, None, "", {"nested": "value"}]
        }

        # Act
        result = data_provider._prune_empty_nested(test_obj)

        # Assert
        assert result["level1"]["level2"]["value"] == "keep"
        assert "empty" not in result["level1"]["level2"]
        assert "empty_dict" not in result["level1"]
        assert None not in result["list"]
        assert "" not in result["list"]

    @pytest.mark.asyncio
    async def test_clean_tool_metadata_removes_junk(self, data_provider):
        """Test that tool metadata cleaning removes non-semantic fields."""
        # Arrange
        raw_tool = {
            "id": "tool1",
            "name": "Test Tool",
            "model_class": "Tool",
            "icon": "icon.png",
            "hidden": False,
            "inputs": [],
            "help": "<p>Help text</p>"
        }

        # Act
        cleaned, name, link = await data_provider._clean_tool_metadata(raw_tool)

        # Assert
        assert "model_class" not in cleaned
        assert "icon" not in cleaned
        assert "hidden" not in cleaned
        assert cleaned["tool_id"] == "tool1"
        assert name == "Test Tool"


    @pytest.mark.asyncio
    async def test_clean_tool_metadata_enriches_inputs(self, data_provider):
        """Test that tool inputs are enriched properly."""
        # Arrange
        raw_tool = {
            "id": "tool1",
            "name": "Test Tool",
            "inputs": [
                {
                    "name": "input1",
                    "optional": True,
                    "model_class": "InputParameter",
                    "options": [["Label1", "value1", False], ["Label2", "value2", True]]
                }
            ]
        }

        # Act
        cleaned, _, _ = await data_provider._clean_tool_metadata(raw_tool)

        # Assert
        assert cleaned["inputs"][0]["required"] is False
        assert "model_class" not in cleaned["inputs"][0]
        assert cleaned["inputs"][0]["options"][0]["label"] == "Label1"
        assert cleaned["inputs"][0]["options"][1]["selected"] is True

    @pytest.mark.asyncio
    async def test_clean_tool_metadata_help_text(self, data_provider):
        """Test that help text is cleaned and preserved."""
        # Arrange
        raw_tool = {
            "id": "tool1",
            "name": "Test Tool",
            "help": "<p>This is <b>help</b> text</p>"
        }

        # Act
        cleaned, _, _ = await data_provider._clean_tool_metadata(raw_tool)

        # Assert
        assert "help" not in cleaned
        assert "help_text" in cleaned
        assert "<p>" not in cleaned["help_text"]
        assert "<b>" not in cleaned["help_text"]
        assert "This is help text" in cleaned["help_text"]

    def test_clean_workflow_metadata_removes_junk(self, data_provider):
        """Test that workflow metadata cleaning removes non-semantic fields."""
        # Arrange
        raw_workflow = {
            "id": "wf1",
            "name": "Test Workflow",
            "model_class": "StoredWorkflow",
            "create_time": "2024-01-01",
            "update_time": "2024-01-02",
            "deleted": False,
            "steps": {},
            "inputs": {}
        }

        # Act
        cleaned, name, link = data_provider._clean_workflow_metadata(raw_workflow)

        # Assert
        assert "model_class" not in cleaned
        assert "create_time" not in cleaned
        assert "update_time" not in cleaned
        assert "deleted" not in cleaned
        assert cleaned["workflow_id"] == "wf1"
        assert name == "Test Workflow"


    def test_clean_workflow_metadata_enriches_inputs(self, data_provider):
        """Test that workflow inputs are enriched properly."""
        # Arrange
        raw_workflow = {
            "id": "wf1",
            "name": "Test Workflow",
            "inputs": {
                "0": {"label": "Input 1", "optional": False, "uuid": "uuid1"},
                "1": {"label": "Input 2", "optional": True, "uuid": "uuid2"}
            },
            "steps": {}
        }

        # Act
        cleaned, _, _ = data_provider._clean_workflow_metadata(raw_workflow)

        # Assert
        assert isinstance(cleaned["inputs"], list)
        assert len(cleaned["inputs"]) == 2
        assert cleaned["inputs"][0]["required"] is True
        assert cleaned["inputs"][1]["required"] is False
        assert "uuid" not in cleaned["inputs"][0]

    def test_clean_workflow_metadata_enriches_steps(self, data_provider):
        """Test that workflow steps are enriched properly."""
        # Arrange
        raw_workflow = {
            "id": "wf1",
            "name": "Test Workflow",
            "inputs": {},
            "steps": {
                "0": {
                    "type": "tool",
                    "tool_id": "toolshed.g2.bx.psu.edu/repos/owner/repo/tool_name/1.0",
                    "tool_version": "1.0+galaxy1",
                    "tool_inputs": {"param": "value"}
                }
            }
        }

        # Act
        cleaned, _, _ = data_provider._clean_workflow_metadata(raw_workflow)

        # Assert
        assert isinstance(cleaned["steps"], list)
        assert len(cleaned["steps"]) == 1
        assert "tool_info" in cleaned["steps"][0]
        assert cleaned["steps"][0]["tool_info"]["owner"] == "owner"
        assert cleaned["steps"][0]["tool_info"]["repo"] == "repo"
        assert cleaned["steps"][0]["tool_info"]["name"] == "tool_name"
        assert "tool_id" not in cleaned["steps"][0]

    def test_clean_workflow_metadata_annotation_to_description(self, data_provider):
        """Test that annotation is converted to description."""
        # Arrange
        raw_workflow = {
            "id": "wf1",
            "name": "Test Workflow",
            "annotation": "<p>This is an annotation</p>",
            "inputs": {},
            "steps": {}
        }

        # Act
        cleaned, _, _ = data_provider._clean_workflow_metadata(raw_workflow)

        # Assert
        assert "annotation" not in cleaned
        assert "description" in cleaned
        assert "This is an annotation" in cleaned["description"]
        assert "<p>" not in cleaned["description"]



class TestGlobalRecommender:
    """Tests for the GlobalRecommender class."""

    @pytest.fixture
    def mock_manager(self):
        """Fixture for mocked InformerManager."""
        mock = AsyncMock()
        mock.embed_and_store_entities = AsyncMock()
        return mock

    @pytest.fixture
    async def global_recommender(self, mock_manager):
        """Fixture for GlobalRecommender with mocked dependencies."""
        with patch('app.bioblend_server.informer.global_rec.InformerManager') as mock_manager_class:
            mock_manager_class.return_value.create = AsyncMock(return_value=mock_manager)
            
            from app.bioblend_server.informer.global_rec import GlobalRecommender
            recommender = await GlobalRecommender.create()
            recommender.manager = mock_manager
            return recommender

    @pytest.mark.asyncio
    async def test_create(self):
        """Test GlobalRecommender async initialization."""
        # Arrange
        mock_manager = AsyncMock()
        
        with patch('app.bioblend_server.informer.global_rec.InformerManager') as mock_manager_class:
            mock_manager_class.return_value.create = AsyncMock(return_value=mock_manager)
            
            from app.bioblend_server.informer.global_rec import GlobalRecommender
            
            # Act
            recommender = await GlobalRecommender.create()

            # Assert
            assert recommender is not None
            assert recommender.manager == mock_manager

    @pytest.mark.asyncio
    async def test_store_scraped_tools(self, global_recommender):
        """Test storing scraped tools."""
        # Arrange
        mock_tools = [{"tool_id": "tool1", "name": "Test Tool"}]
        global_recommender.tool_scraper.scrape_tool = AsyncMock(return_value=mock_tools)
        global_recommender.tool_scraper.close = AsyncMock()

        # Act
        await global_recommender.store_scraped_tools()

        # Assert
        global_recommender.tool_scraper.scrape_tool.assert_called_once()
        global_recommender.tool_scraper.close.assert_called_once()
        global_recommender.manager.embed_and_store_entities.assert_called_once_with(
            entities=mock_tools,
            collection_name="generic_galaxy_tool"
        )


    @pytest.mark.asyncio
    async def test_store_scraped_tools_error_handling(self, global_recommender):
        """Test error handling when storing scraped tools."""
        # Arrange
        global_recommender.tool_scraper.scrape_tool = AsyncMock(side_effect=Exception("Scraping failed"))

        # Act & Assert
        with pytest.raises(Exception):
            await global_recommender.store_scraped_tools()

    @pytest.mark.asyncio
    async def test_store_scraped_workflows(self, global_recommender):
        """Test storing scraped workflows with deduplication."""
        # Arrange
        github_workflows = [
            {"name": "Workflow A", "description": "Short desc"},
            {"name": "Workflow B", "description": "Another workflow"}
        ]
        hub_workflows = [
            {"name": "Workflow A", "description": "Longer description with more details"},
            {"name": "Workflow C", "description": "Third workflow"}
        ]
        
        global_recommender.workflow_scraper.scrape_workflows = AsyncMock(return_value=github_workflows)
        global_recommender.hub_scraper.scrape_workflows = AsyncMock(return_value=hub_workflows)

        # Act
        await global_recommender.store_scraped_workflows()

        # Assert
        global_recommender.workflow_scraper.scrape_workflows.assert_called_once()
        global_recommender.hub_scraper.scrape_workflows.assert_called_once()
        
        # Check that embed_and_store_entities was called
        call_args = global_recommender.manager.embed_and_store_entities.call_args
        stored_workflows = call_args.kwargs['entities']
        
        # Should have 3 unique workflows (A, B, C)
        assert len(stored_workflows) == 3
        
        # Workflow A should have the longer description from hub_workflows
        workflow_a = next(wf for wf in stored_workflows if wf["name"].lower() == "workflow a")
        assert "Longer description" in workflow_a["description"]

    @pytest.mark.asyncio
    async def test_store_scraped_workflows_deduplication_logic(self, global_recommender):
        """Test that deduplication keeps workflow with longer description."""
        # Arrange
        github_workflows = [{"name": "Test", "description": "abc"}]
        hub_workflows = [{"name": "test", "description": "abcdef"}]  # Same name, case-insensitive
        
        global_recommender.workflow_scraper.scrape_workflows = AsyncMock(return_value=github_workflows)
        global_recommender.hub_scraper.scrape_workflows = AsyncMock(return_value=hub_workflows)

        # Act
        await global_recommender.store_scraped_workflows()

        # Assert
        call_args = global_recommender.manager.embed_and_store_entities.call_args
        stored_workflows = call_args.kwargs['entities']
        
        assert len(stored_workflows) == 1
        assert stored_workflows[0]["description"] == "abcdef"


    @pytest.mark.asyncio
    async def test_store_scraped_workflows_empty_name_handling(self, global_recommender):
        """Test that workflows with empty names are filtered out."""
        # Arrange
        github_workflows = [
            {"name": "", "description": "Empty name"},
            {"name": "Valid", "description": "Valid workflow"}
        ]
        hub_workflows = []
        
        global_recommender.workflow_scraper.scrape_workflows = AsyncMock(return_value=github_workflows)
        global_recommender.hub_scraper.scrape_workflows = AsyncMock(return_value=hub_workflows)

        # Act
        await global_recommender.store_scraped_workflows()

        # Assert
        call_args = global_recommender.manager.embed_and_store_entities.call_args
        stored_workflows = call_args.kwargs['entities']
        
        # Only the valid workflow should be stored
        assert len(stored_workflows) == 1
        assert stored_workflows[0]["name"] == "Valid"

    @pytest.mark.asyncio
    async def test_store_to_collection_tool(self, global_recommender):
        """Test storing tools to collection."""
        # Arrange
        tools = [{"tool_id": "tool1", "name": "Test Tool"}]

        # Act
        await global_recommender.store_to_collection(tools, "tool")

        # Assert
        global_recommender.manager.embed_and_store_entities.assert_called_once_with(
            entities=tools,
            collection_name="generic_galaxy_tool"
        )

    @pytest.mark.asyncio
    async def test_store_to_collection_workflow(self, global_recommender):
        """Test storing workflows to collection."""
        # Arrange
        workflows = [{"workflow_id": "wf1", "name": "Test Workflow"}]

        # Act
        await global_recommender.store_to_collection(workflows, "workflow")

        # Assert
        global_recommender.manager.embed_and_store_entities.assert_called_once_with(
            entities=workflows,
            collection_name="generic_galaxy_workflow"
        )

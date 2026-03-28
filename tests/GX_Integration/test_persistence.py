import pytest
import logging
from unittest.mock import MagicMock, AsyncMock, patch
from pymongo import errors

from sys import path
path.append(".")

from app.persistence import MongoStore
from app.log_setup import configure_logging

configure_logging() 

class MockCursor:
    """Helper to mock async cursors with method chaining support."""
    def __init__(self, seq):
        self.iter = iter(seq)

    def limit(self, n):
        return self  # Supports chaining .limit()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.iter)
        except StopIteration:
            raise StopAsyncIteration


# Fixtures

@pytest.fixture
def mongo_log():
    return logging.getLogger("TestPersistenceLayer")

@pytest.fixture
def mock_mongo_client():
    """Mocks the AsyncMongoClient to avoid real DB connections."""
    client = MagicMock()
    # Mock the database and collection access chain
    db_mock = MagicMock()
    collection_mock = MagicMock()
    
    client.__getitem__.return_value = db_mock
    db_mock.__getitem__.return_value = collection_mock
    
    # Setup AsyncMocks for the actual DB operations
    collection_mock.create_index = AsyncMock()
    collection_mock.find_one = AsyncMock()
    collection_mock.update_one = AsyncMock()
    collection_mock.delete_one = AsyncMock()
    
    # For find(), it usually returns a cursor which is iterable
    collection_mock.find = MagicMock() 
    
    return client

@pytest.fixture
async def store(mock_mongo_client):
    """Initializes the store with the mocked client."""
    # We patch where the class instantiates AsyncMongoClient
    with patch("app.persistence.AsyncMongoClient", return_value=mock_mongo_client):
        mongo_client = MongoStore(
            connection_string="mongodb://test", 
            database_name="TestDB",
            retry_delay_ms=1, # Speed up tests
            max_retries=1
        )
        # We mock _ensure_indexes to avoid testing it in every single CRUD test
        # We will test _ensure_indexes separately.
        mongo_client._ensure_indexes = AsyncMock()
        
        yield mongo_client
        mongo_client.close()
        
# Lifecycle & Internal Tests
class TestPersistenceLayer:
    """ unit tests for the MongoStore class persistent layer. """
        
    @pytest.mark.asyncio
    async def test_initialization_host_port(self, mongo_log):
        """Test initializing with host/port instead of connection string."""
        
        mongo_log.info("TEST: test_initialization_host_port starting")
        
        mongo_log.debug("Initializing MongoStore with host and port")
        with patch("app.persistence.AsyncMongoClient") as mock_client_cls:
            store = MongoStore(host="1.2.3.4", port=9999, connection_string=None)
            mock_client_cls.assert_called_with(
                host="1.2.3.4", port=9999, username=None, password=None
            )
            
            mongo_log.info("TEST: test_initialization_host_port PASSED")

    @pytest.mark.asyncio
    async def test_verify_connection(self, store, mock_mongo_client, mongo_log):
        """Test the verify_connection (ping) method."""
        
        mongo_log.info("TEST: test_verify_connection starting")
        
        mock_mongo_client.admin.command = AsyncMock(return_value={"ok": 1})
        mongo_log.debug("Verifying connection with ping")
        await store.verify_connection()
        mock_mongo_client.admin.command.assert_called_with("ping")
        
        mongo_log.info("TEST: test_verify_connection PASSED")

    @pytest.mark.asyncio
    async def test_ensure_indexes_logic(self, mock_mongo_client, mongo_log):
        """Test the actual _ensure_indexes logic (unmocked for this specific test)."""
        
        mongo_log.info("TEST: test_ensure_indexes_logic starting")
        
        with patch("app.persistence.AsyncMongoClient", return_value=mock_mongo_client):
            real_store = MongoStore()
            # Mock the DB collection access specifically for this test
            coll = mock_mongo_client["Galaxy_Integration"]["test_coll"]
            coll.create_index = AsyncMock()
            
            mongo_log.debug("Ensuring indexes on test_coll")
            await real_store._ensure_indexes("test_coll")
            coll.create_index.assert_called_with("key", unique=True)
            
            mongo_log.info("TEST: test_ensure_indexes_logic PASSED")

    # Key Existence & Retrieval

    @pytest.mark.asyncio
    async def test_exists(self, store, mock_mongo_client, mongo_log):
        
        mongo_log.info("TEST: test_exists starting")
        
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        # Case 1: Exists
        coll.find_one.return_value = {"_id": "some_id"}
        mongo_log.debug("Checking existence of existing key")
        assert await store.exists("test_coll", "my_key") is True
        
        # Case 2: Does not exist
        coll.find_one.return_value = None
        mongo_log.debug("Checking existence of missing key")
        assert await store.exists("test_coll", "missing_key") is False

        mongo_log.info("TEST: test_exists PASSED")

    @pytest.mark.asyncio
    async def test_get(self, store, mock_mongo_client, mongo_log):
        
        mongo_log.info("TEST: test_get starting")
        
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        # Case 1: Found
        coll.find_one.return_value = {"value": "my_data"}
        mongo_log.debug("Getting existing key")
        result = await store.get("test_coll", "my_key")
        assert result == "my_data"
        
        # Case 2: Not Found
        coll.find_one.return_value = None
        mongo_log.debug("Getting missing key")
        result = await store.get("test_coll", "missing_key")
        assert result is None
        
        mongo_log.info("TEST: test_get PASSED")

    @pytest.mark.asyncio
    async def test_list_keys(self, store, mock_mongo_client, mongo_log):
        
        mongo_log.info("TEST: test_list_keys starting")
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        # Mock the cursor returned by find()
        mock_docs = [{"key": "user_1"}, {"key": "user_2"}]
        coll.find.return_value = MockCursor(mock_docs)
        
        mongo_log.debug("Listing keys with prefix user")
        keys = await store.list_keys("test_coll", prefix="user")
        
        assert keys == ["user_1", "user_2"]
        coll.find.assert_called()
        # Check regex construction
        args, _ = coll.find.call_args
        assert args[0] == {"key": {"$regex": "^user"}}
        
        mongo_log.info("TEST: test_list_keys PASSED")

    # Writes & Updates (Set, Delete)

    @pytest.mark.asyncio
    async def test_set(self, store, mock_mongo_client, mongo_log):
        mongo_log.info("TEST: test_set starting")
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        mongo_log.debug("Setting value for key")
        await store.set("test_coll", "my_key", {"some": "json"})
        
        coll.update_one.assert_called_with(
            {"key": "my_key"},
            {"$set": {"value": {"some": "json"}}},
            upsert=True
        )

        mongo_log.info("TEST: test_set PASSED")

    @pytest.mark.asyncio
    async def test_delete(self, store, mock_mongo_client, mongo_log):
        mongo_log.info("TEST: test_delete starting")
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        # Mock result object
        mock_result = MagicMock()
        mock_result.deleted_count = 1
        coll.delete_one.return_value = mock_result
        
        mongo_log.debug("Deleting key")
        assert await store.delete("test_coll", "my_key") is True
        coll.delete_one.assert_called_with({"key": "my_key"})

        mongo_log.info("TEST: test_delete PASSED")

    # List & Set Operations 

    @pytest.mark.asyncio
    async def test_extend(self, store, mock_mongo_client, mongo_log):
        mongo_log.info("TEST: test_extend starting")
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        new_vals = [1, 2, 3]
        mongo_log.debug("Extending list for key")
        await store.extend("test_coll", "list_key", new_vals)
        
        coll.update_one.assert_called_with(
            {"key": "list_key"},
            {"$push": {"value": {"$each": new_vals}}},
            upsert=True
        )

        mongo_log.info("TEST: test_extend PASSED")

    @pytest.mark.asyncio
    async def test_add_to_set(self, store, mock_mongo_client, mongo_log):
        mongo_log.info("TEST: test_add_to_set starting")
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        mongo_log.debug("Adding to set for key")
        await store.add_to_set("test_coll", "set_key", ["a", "b"])
        
        coll.update_one.assert_called_with(
            {"key": "set_key"},
            {"$addToSet": {"value": {"$each": ["a", "b"]}}},
            upsert=True
        )

        mongo_log.info("TEST: test_add_to_set PASSED")

    @pytest.mark.asyncio
    async def test_remove_from_set(self, store, mock_mongo_client, mongo_log):
        mongo_log.info("TEST: test_remove_from_set starting")
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        mock_result = MagicMock()
        mock_result.modified_count = 2
        coll.update_one.return_value = mock_result
        
        mongo_log.debug("Removing from set for key")
        count = await store.remove_from_set("test_coll", "set_key", ["a", "b"])
        
        assert count == 2
        coll.update_one.assert_called_with(
            {"key": "set_key"},
            {"$pull": {"value": {"$in": ["a", "b"]}}}
        )

        mongo_log.info("TEST: test_remove_from_set PASSED")

    # Complex Array Manipulations

    @pytest.mark.asyncio
    async def test_delete_specific(self, store, mock_mongo_client, mongo_log):
        mongo_log.info("TEST: test_delete_specific starting")
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        # Create a mock result object with the required attribute
        mock_result = MagicMock()
        mock_result.modified_count = 1
        coll.update_one.return_value = mock_result

        ids_to_remove = [101, 102]
        mongo_log.debug("Deleting specific elements for key")
        await store.delete_specific("test_coll", "my_key", ids_to_remove)
        
        mongo_log.info("TEST: test_delete_specific PASSED")

    @pytest.mark.asyncio
    async def test_update_value_element(self, store, mock_mongo_client, mongo_log):
        mongo_log.info("TEST: test_update_value_element starting")
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        mongo_log.debug("Updating value element for key")
        await store.update_value_element(
            "test_coll", 
            key="my_key",
            match_field="id",
            match_value=1,
            update_field="status",
            new_value="active"
        )
        
        coll.update_one.assert_called_with(
            {
                "key": "my_key",
                f"value.id": 1,
            },
            {
                "$set": {
                    f"value.$.status": "active"
                }
            },
        )

        mongo_log.info("TEST: test_update_value_element PASSED")

    @pytest.mark.asyncio
    async def test_remove_from_value_array(self, store, mock_mongo_client, mongo_log):
        mongo_log.info("TEST: test_remove_from_value_array starting")
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        mock_result = MagicMock()
        mock_result.modified_count = 1
        coll.update_one.return_value = mock_result
        
        mongo_log.debug("Removing from value array for key")
        result = await store.remove_from_value_array(
            "test_coll", "my_key", "status", "archived"
        )
        
        assert result is True
        coll.update_one.assert_called_with(
            {"key": "my_key"},
            {"$pull": {"value": {"status": "archived"}}}
        )

        mongo_log.info("TEST: test_remove_from_value_array PASSED")

    # Hash/Dictionary Field Operations

    @pytest.mark.asyncio
    async def test_set_element(self, store, mock_mongo_client, mongo_log):
        mongo_log.info("TEST: test_set_element starting")
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        mongo_log.debug("Setting element for key")
        await store.set_element("test_coll", "hash_key", "meta.author", "John")
        
        coll.update_one.assert_called_with(
            {"key": "hash_key"},
            {"$set": {"value.meta.author": "John"}},
            upsert=True
        )

        mongo_log.info("TEST: test_set_element PASSED")

    @pytest.mark.asyncio
    async def test_get_element(self, store, mock_mongo_client, mongo_log):
        mongo_log.info("TEST: test_get_element starting")
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        # Mock returned doc
        coll.find_one.return_value = {"value": {"name": "Alice", "age": 30}}
        
        mongo_log.debug("Getting element for existing key")
        val = await store.get_element("test_coll", "user:1", "name")
        assert val == "Alice"
        
        # Test missing doc
        coll.find_one.return_value = None
        mongo_log.debug("Getting element for missing key")
        val = await store.get_element("test_coll", "user:99", "name")
        assert val is None

        mongo_log.info("TEST: test_get_element PASSED")

    @pytest.mark.asyncio
    async def test_delete_elements(self, store, mock_mongo_client, mongo_log):
        mongo_log.info("TEST: test_delete_elements starting")
        coll = mock_mongo_client["TestDB"]["test_coll"]
        
        fields = ["temp_data", "cache"]
        mongo_log.debug("Deleting elements for key")
        await store.delete_elements("test_coll", "my_key", fields)
        
        coll.update_one.assert_called_with(
            {"key": "my_key"},
            {"$unset": {"value.temp_data": "", "value.cache": ""}}
        )

        mongo_log.info("TEST: test_delete_elements PASSED")

    # Error Handling & Retries
    @pytest.mark.asyncio
    async def test_retry_mechanism(self, store, mock_mongo_client, mongo_log):
        """
        Test that the retry decorator catches PyMongoError.
        We force max_retries to 2 to ensure at least one retry occurs.
        """
        mongo_log.info("TEST: test_retry_mechanism starting")
        # Explicitly set the retry parameters on the store instance for this test
        store._max_retries = 2 
        store._retry_delay_seconds = 0.01 # Keep it fast
        
        coll = mock_mongo_client["TestDB"]["test_coll"]

        # Make update_one always raise an error
        coll.update_one.side_effect = errors.PyMongoError("Connection lost")

        mongo_log.debug("Attempting set with failing update")
        with pytest.raises(RuntimeError) as exc:
            await store.set("test_coll", "key", "val")

        assert "Failed to store key" in str(exc.value)
        
        # call_count should now be 2 (1 original attempt + 1 retry)
        assert coll.update_one.call_count >= 2
        
        mongo_log.info("TEST: test_retry_mechanism PASSED")
import os
import jwt
import json
import pytest
import logging
from sys import path
path.append(".")

from cryptography.fernet import Fernet
from fastmcp.server.middleware import MiddlewareContext
from mcp.shared.exceptions import McpError
from unittest.mock import Mock, patch, MagicMock

from app.bioblend_server.mcp_context import current_api_key_server
from app.bioblend_server.mcp_middleware import JWTGalaxyKeyMiddleware as FastMCPJWTGalaxyKeyMiddleware

@pytest.fixture
def mcp_jwt_logger():
    return logging.getLogger("TestFastMCPJWTGalaxyKeyMiddleware")
        

@pytest.fixture
def fernet_secret():
    """Fixture for Fernet secret key."""
    return Fernet.generate_key()


@pytest.fixture
def fernet(fernet_secret):
    """Fixture for Fernet instance."""
    return Fernet(fernet_secret)

class TestFastMCPJWTGalaxyKeyMiddleware:
    """Unit tests for FastMCP JWTGalaxyKeyMiddleware."""
           
    @pytest.fixture
    def middleware(self, fernet_secret, mcp_jwt_logger):
        """Fixture for JWT middleware instance."""
        mcp_jwt_logger.debug("Setting up middleware")
        os.environ["SECRET_KEY"] = fernet_secret.decode()
        middleware_instance = FastMCPJWTGalaxyKeyMiddleware()
        mcp_jwt_logger.debug("middleware setup complete")
        yield middleware_instance
        del os.environ["SECRET_KEY"]
        mcp_jwt_logger.debug("middleware teardown complete")
    
    @pytest.fixture
    def mock_context(self):
        """Mock MiddlewareContext."""
        return Mock(spec=MiddlewareContext)
    
    @pytest.fixture
    def mock_call_next(self):
        """Mock CallNext that returns a response including the current API key."""
        async def mock_next(context):
            return {"message": "Protected endpoint", "api_key": current_api_key_server.get(None)}
        return MagicMock(wraps=mock_next)
    
    @pytest.mark.asyncio
    async def test_missing_authorization_header(self, middleware, mock_context, mock_call_next, caplog, mcp_jwt_logger):
        """Test unauthorized response for missing Authorization header."""
        mcp_jwt_logger.info("TEST: test_missing_authorization_header starting")
        caplog.set_level(logging.ERROR)
        with patch('app.bioblend_server.mcp_middleware.get_http_headers') as mock_headers:
            mock_headers.return_value = {}
            with pytest.raises(McpError) as exc_info:
                await middleware.on_request(mock_context, mock_call_next)
            assert exc_info.value.error.code == -32001
            assert "Missing or malformed Authorization header" in exc_info.value.error.message
            mock_call_next.assert_not_called()
            assert "Authorization header with Bearer token is required" in caplog.text
        mcp_jwt_logger.info("TEST: test_missing_authorization_header PASSED")

    @pytest.mark.asyncio
    async def test_invalid_jwt_token(self, middleware, mock_context, mock_call_next, caplog, mcp_jwt_logger):
        """Test unauthorized response for invalid JWT token."""
        mcp_jwt_logger.info("TEST: test_invalid_jwt_token starting")
        caplog.set_level(logging.ERROR)
        invalid_token = "invalid.token.here"
        with patch('app.bioblend_server.mcp_middleware.get_http_headers') as mock_headers:
            mock_headers.return_value = {"Authorization": f"Bearer {invalid_token}"}
            with pytest.raises(McpError) as exc_info:
                await middleware.on_request(mock_context, mock_call_next)
            assert exc_info.value.error.code == -32001
            assert "Invalid JWT" in exc_info.value.error.message
            mock_call_next.assert_not_called()
            assert "Invalid JWT" in caplog.text
        mcp_jwt_logger.info("TEST: test_invalid_jwt_token PASSED")

    @pytest.mark.asyncio
    async def test_jwt_missing_galaxy_api_token_claim(self, middleware, mock_context, mock_call_next, caplog, mcp_jwt_logger):
        """Test unauthorized response for JWT missing the required claim."""
        mcp_jwt_logger.info("TEST: test_jwt_missing_galaxy_api_token_claim starting")
        caplog.set_level(logging.ERROR)
        payload = {"other_claim": "value"}
        token = jwt.encode(payload, key=None, algorithm="none")
        with patch('app.bioblend_server.mcp_middleware.get_http_headers') as mock_headers:
            mock_headers.return_value = {"Authorization": f"Bearer {token}"}
            with pytest.raises(McpError) as exc_info:
                await middleware.on_request(mock_context, mock_call_next)
            assert exc_info.value.error.code == -32001
            assert "JWT missing api-key claim" in exc_info.value.error.message
            mock_call_next.assert_not_called()
            assert "JWT missing API key claim 'galaxy_api_token'" in caplog.text
        mcp_jwt_logger.info("TEST: test_jwt_missing_galaxy_api_token_claim PASSED")

    @pytest.mark.asyncio
    async def test_jwt_with_empty_galaxy_api_token_claim(self, middleware, mock_context, mock_call_next, caplog, mcp_jwt_logger):
        """Test unauthorized response for empty galaxy_api_token claim."""
        mcp_jwt_logger.info("TEST: test_jwt_with_empty_galaxy_api_token_claim starting")
        caplog.set_level(logging.ERROR)
        payload = {"galaxy_api_token": ""}
        token = jwt.encode(payload, key=None, algorithm="none")
        with patch('app.bioblend_server.mcp_middleware.get_http_headers') as mock_headers:
            mock_headers.return_value = {"Authorization": f"Bearer {token}"}
            with pytest.raises(McpError) as exc_info:
                await middleware.on_request(mock_context, mock_call_next)
            assert exc_info.value.error.code == -32001
            assert "Empty api-key claim" in exc_info.value.error.message
            mock_call_next.assert_not_called()
            assert "Empty API key claim" in caplog.text
        mcp_jwt_logger.info("TEST: test_jwt_with_empty_galaxy_api_token_claim PASSED")

    @pytest.mark.asyncio
    async def test_jwt_with_encrypted_api_key_claim(self, fernet, middleware, mock_context, mock_call_next, caplog, mcp_jwt_logger):
        """Test successful decryption of encrypted API key in claim."""
        mcp_jwt_logger.info("TEST: test_jwt_with_encrypted_api_key_claim starting")
        caplog.set_level(logging.INFO)
        raw_apikey = "test-encrypted-api-key-456"
        encrypted_payload = json.dumps({"apikey": raw_apikey}).encode()
        encrypted_token = fernet.encrypt(encrypted_payload)
        payload = {"galaxy_api_token": encrypted_token.decode()}
        token = jwt.encode(payload, key=None, algorithm="none")
        with patch('app.bioblend_server.mcp_middleware.get_http_headers') as mock_headers:
            mock_headers.return_value = {"Authorization": f"Bearer {token}"}
            result = await middleware.on_request(mock_context, mock_call_next)
            
            mock_call_next.assert_called_once()
            assert result["message"] == "Protected endpoint"
            assert result["api_key"] == raw_apikey
            assert "Incoming request to MCP server validated." in caplog.text
        mcp_jwt_logger.info("TEST: test_jwt_with_encrypted_api_key_claim PASSED")
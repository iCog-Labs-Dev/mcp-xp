import os
import json
import logging
import asyncio
import jwt

from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.server.dependencies import get_http_headers

from app.log_setup import configure_logging
from app.bioblend_server.mcp_context import current_api_key_server

configure_logging()

GALAXY_API_TOKEN = "galaxy_api_token"

class JWTGalaxyKeyMiddleware(Middleware):
    """
    FastMCP middleware that expects:
      Authorization: Bearer <JWT>
    The JWT must contain a claim ('galaxy_api_token') that is either:
      - a fernet-encrypted JSON payload like {"apikey": "<actual_key>"} (what the register user produces)
    The middleware will set current_api_key to the final plain api key string.
    """
    def __init__(self):
        self.log = logging.getLogger(self.__class__.__name__)

    async def on_request(self, context: MiddlewareContext, call_next: CallNext):
        
        # Get header and validate tokem.        
        headers = get_http_headers(include_all=True)
        auth = headers.get("Authorization", None) or headers.get("authorization", None)
        
        if auth is None:
            self.log.error("unauthorized, Authorization header with Bearer token is required.")
            return {"error": "Unauthorized"}
        
        if not auth.startswith("Bearer "):
            self.log.error("unauthorized, Authorization header with Bearer token is required.")
            return {"error": "Unauthorized"}

        token = auth.split(" ")[1].strip()
        try:
            payload = self._decode_jwt(token)
        except Exception as e:
            self.log.error(f"unauthorized, Invalid JWT: {e}")
            return {"error": "Unauthorized"}

        # Extract the API token claim
        if GALAXY_API_TOKEN not in payload:
            self.log.error("JWT missing API key claim '%s'", GALAXY_API_TOKEN)
            return {"error": "Unauthorized"}

        galaxy_jwt_token = payload[GALAXY_API_TOKEN]
        if not galaxy_jwt_token:
            self.log.error("Empty API key claim")
            return {"error": "Unauthorized"}

        # Try to decrypt claim_value (The fernet token string produced by register-user)
        apikey = await self._decrypt_api_token(galaxy_jwt_token)

        # Set the context for downstream tools/handlers
        current_api_key_server.set(apikey)
        self.log.info("Incoming request to MCP server validated.")
        
        return await call_next(context)

    def _decode_jwt(self, token: str) -> dict:
        """
        Decode/verify JWT synchronously (PyJWT). Raises Exception on invalid token.
        For RS-based tokens, JWT_SECRET should contain the public key (but here signature verification is disabled).
        """
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
            return payload
        except jwt.InvalidTokenError as e:
            self.log.error("Invalid JWT: %s", e)
            raise ValueError(f"Invalid JWT: {e}")

    async def _decrypt_api_token(self, token_str: str) -> Optional[str]:
        """
        If token_str is a fernet-encrypted payload (bytes when encoded),
        decrypt and parse JSON for {"apikey": "<value>"} and return the value.
        Returns None if decryption/parsing fails so caller can fallback to raw token.
        """
        if not isinstance(token_str, str) or not token_str:
            return None
        loop = asyncio.get_running_loop()
        try:
            # Environment / secrets
            FERNET_SECRET = os.getenv("SECRET_KEY")
            if not FERNET_SECRET:
                raise RuntimeError("SECRET_KEY (Fernet secret) is required in env")
            fernet = Fernet(FERNET_SECRET)
            
            decrypted = await loop.run_in_executor(None, fernet.decrypt, token_str.encode("utf-8"))
            parsed: dict = await loop.run_in_executor(None, json.loads, decrypted.decode("utf-8"))
            apikey = parsed.get("apikey")
            if apikey and isinstance(apikey, str):
                return apikey
            self.log.error("Decrypted JWT galaxy api-key payload missing 'apikey' field")
            return None
        except (InvalidToken, Exception) as e:
            # Not a fernet payload or parse failed; return None so fallback can apply
            self.log.debug("Fernet decryption/parsing failed for JWT claim: %s", e)
            return None
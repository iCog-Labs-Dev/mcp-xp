import os
import logging
from typing import Optional

from dotenv import load_dotenv
from bioblend import galaxy
from bioblend.galaxy.objects import GalaxyInstance
from bioblend.galaxy.client import ConnectionError as GalaxyConnectionError
from requests.exceptions import RequestException

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
    )

load_dotenv()

class GalaxyClient:
    """Client wrapper for interacting with a Galaxy instance (production-ready)."""

    def __init__(self, user_api_key: str, galaxy_url: Optional[str] = None):
        # Load configuration
        self.galaxy_url: str = galaxy_url or os.getenv("GALAXY_URL", "")
        self.admin_api_key: Optional[str] = os.getenv("GALAXY_API_KEY")
        self.user_api_key: str = user_api_key

        # Retry and logger config
        self.max_retries: int = 3
        self.logger = logging.getLogger(self.__class__.__name__)

        if not self.galaxy_url:
            raise ValueError("GALAXY_URL is not set in environment or passed explicitly.")
        if not self.user_api_key:
            raise ValueError("User API key must be provided to GalaxyClient.")
        if not self.admin_api_key:
            self.logger.warning("GALAXY_API_KEY (admin) is not set. Admin functionalities will fail.")

        try:
            # Admin client (optional)
            self.gi_admin: Optional[GalaxyInstance] = (
                GalaxyInstance(url=self.galaxy_url, api_key=self.admin_api_key)
                if self.admin_api_key else None
            )

            # User client (required)
            self.gi_object: GalaxyInstance = GalaxyInstance(
                url=self.galaxy_url, api_key=self.user_api_key
            )

            # Set galaxy client
            self.gi_client = self.gi_object.gi

            # Config client
            self.config_client = galaxy.config.ConfigClient(self.gi_client)

            self.logger.info(
                "GalaxyClient initialized for %s (retries=%s)",
                self.galaxy_url, self.max_retries
            )
            
        except GalaxyConnectionError as e:
            self.logger.error(f"Failed to connect to Galaxy at {self.galaxy_url}: {e}")
            raise GalaxyConnectionError(f"Failed to connect to Galaxy at {self.galaxy_url}: {e}") from e
        except Exception as e:
            self.logger.error(f"Unexpected error initializing GalaxyClient: {e}")
            raise Exception(f"Unexpected error initializing GalaxyClient: {e}") from e
    
    @property
    def whoami(self) -> str:
        """
        Return current user info with retries, error handling, and safe fallback.
        Retries up to 3 times with exponential backoff on network errors.
        """
        @retry(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type((GalaxyConnectionError, RequestException)),
            reraise=True,
        )
        def _whoami():
            try:
                whoami = self.config_client.whoami()
                if not whoami or "id" not in whoami:
                    self.logger.error("Received invalid response from Galaxy whoami: %s", whoami)
                    return {"error": "Invalid whoami response"}
                return whoami.get("username")
            except (GalaxyConnectionError, RequestException) as e:
                error_msg = str(e) if not isinstance(e, str) else e
                if "API key has expired" in error_msg:
                    self.logger.error("Network error fetching user identity: API key expired. Re-authentication required to refresh the token.")
                else:
                    self.logger.error("Network error fetching user identity: %s", e)

                raise
            except Exception as e:
                self.logger.exception("Unexpected error in whoami: %s", e)
                raise 

        return _whoami()


    def validate_connection(self) -> bool:
        """
        Check if connection to Galaxy is alive.
        Returns True if valid, False otherwise.
        """
        try:
            whoami = self.whoami
            if isinstance(whoami, dict) and "id" in whoami:
                self.logger.debug("Connection valid: %s", whoami)
                return True
            self.logger.warning("Connection validation failed: response missing id.")
            return False
        except Exception as e:
            self.logger.warning("Validation failed: %s", e)
            return False
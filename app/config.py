
import yaml
import json
import os
from typing import Any
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

# Example: Access an environment variable
GALAXY_URL = os.getenv("GALAXY_URL")
GALAXY_API_KEY = os.getenv("GALAXY_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
AZURE_API_KEY = os.getenv("AZURE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") 
OPENAI_API_KEY= os.getenv("OPENAI_API_KEY")

class Configuration:
    """Manages configuration and environment variables for the MCP client."""

    def __init__(self) -> None:
        """Initialize configuration with environment variables."""
        self.load_env()
        self.api_key = os.getenv("LLM_API_KEY")

    @staticmethod
    def load_env() -> None:
        """Load environment variables from .env file."""
        load_dotenv()

    @staticmethod
    def load_server_config(file_path: str) -> dict[str, Any]:
        """Load server configuration from JSON file.

        Args:
            file_path: Path to the JSON configuration file.

        Returns:
            Dict containing server configuration.

        Raises:
            FileNotFoundError: If configuration file doesn't exist.
            JSONDecodeError: If configuration file is invalid JSON.
        """
        with open(file_path, "r") as f:
            return json.load(f)

    @staticmethod
    def load_llm_config(file_path: str) -> dict[str, Any]:
        """Load LLM configuration from JSON file.

        Args:
            file_path: Path to the JSON configuration file.

        Returns:
            Dict containing LLM configuration.

        Raises:
            FileNotFoundError: If configuration file doesn't exist.
            JSONDecodeError: If configuration file is invalid JSON.
        """
        with open(file_path, "r") as f:
            return yaml.safe_load(f)


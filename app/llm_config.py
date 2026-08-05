import yaml
from typing import Any, Dict
from pathlib import Path

from app.config import GEMINI_API_KEY, OPENAI_API_KEY

LLM_CONFIGURATIONS = "app/llm_config.yaml"

# Configuration Classes
class LLMModelConfig:
    def __init__(self, config_data: Dict[str, Any]) -> None:
        self.config_data = config_data
        self.model_name: str = config_data["model"]
        self.base_url: str = config_data["base_url"]
        self.embedding_model: str | None = config_data.get("embedding_model")

class GEMINIConfig(LLMModelConfig):
    @property
    def api_key(self) -> str:
        return GEMINI_API_KEY

class OPENAIConfig(LLMModelConfig):
    @property
    def api_key(self) -> str:
        return OPENAI_API_KEY

class LLMConfiguration:
    def __init__(self):
        self._config_path = Path(LLM_CONFIGURATIONS)
        self._config = self._load()

    def _load(self) -> dict:
        if not self._config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self._config_path}")

        with self._config_path.open("r") as f:
            return yaml.safe_load(f)

    @property
    def data(self) -> dict:
        return self._config
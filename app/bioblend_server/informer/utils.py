import os
import yaml
import numpy as np
from enum import IntEnum, Enum
from dotenv import load_dotenv

from app.llm_config import LLMModelConfig
from app.llm_provider import (
    LLMProvider, 
    GeminiProvider, 
    OpenAIProvider,
    HuggingFaceModel
    )

load_dotenv()

# A Protocol identifies which client SDK/API shape a provider block speaks,
# NOT its identity in the config. A single protocol can back many named
# provider blocks (e.g. "openai" the OpenAI cloud, "local_llm" pointing at a
# self-hosted vLLM, "local_embed" pointing at Ollama — all speak the OpenAI
# protocol at different URLs).
#
# Config lookups now happen by provider NAME (arbitrary yaml key). The
# `protocol:` field inside each block tells us which Provider class to
# instantiate. This decouples "what SDK" from "what endpoint / role", which
# is what let us swap only-embeddings to a local model without also having
# to route chat through the same host.
class Protocol(Enum):
    GEMINI = "gemini"
    OPENAI = "openai"
    HUGGINGFACE = "huggingface"

# Fallback: when a provider block doesn't declare `embedding_size:` we look
# up the dim by embedding_model name. Keeps existing configs working without
# a migration step; new configs should just set `embedding_size:` explicitly.
_EMBEDDING_MODEL_DIMS: dict[str, int] = {
    "embedding-001": 768,
    "embedding-002": 1408,
    "text-embedding-004": 768,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "intfloat/e5-large-v2": 1024,
    "mxbai-embed-large:latest": 1024,
    "mxbai-embed-large": 1024,
}


class ReRankerModel(Enum):
    MS_MARCO_MINILM = "cross-encoder/ms-marco-MiniLM-L-6-v2"

class InformerTTLs(IntEnum):
    TOOL_TTL = 43200
    WORKFLOW_TTL = 3600
    DATASET_TTL = 300
    SHORT_SLEEP = 10
    LIFESPAN = 604800
    SUMMARY_TTL = 604800

class SearchThresholds(Enum):
    SEMANTIC_THRESHOLD = 0.3
    FUZZY_THRESHOLD = 50
    SEARCH_LIMIT = 50
    TOOL_SCRAPE_PERCENTAGE = 1

class WorkflowGitubScraperUrl(Enum):
    GITHUB_SCRAPE_URL = "https://api.github.com/repos/galaxyproject/iwc/contents/workflows"
    RAW_BASE_URL = "https://raw.githubusercontent.com/galaxyproject/iwc/main/workflows"

class WorkflowHubScraperUrl(Enum):
    BASE_URL = "https://workflowhub.eu"
    TRS_BASE_URL = "https://workflowhub.eu/ga4gh/trs/v2"
    
class LLMResponse:
    """
    Loads llm_config.yaml and instantiates LLM + embedding providers
    based on env vars CURRENT_LLM and CURRENT_EMBEDDER. Both env vars
    name a yaml key under `providers:`; the block's `protocol:` field
    determines which Provider class handles it.

    This lets a single deployment mix and match freely — e.g. chat via
    a local vLLM Gemma, embeddings via a local Ollama mxbai — by adding
    two provider blocks and setting the two env vars, with no code
    change.
    """

    _PROTOCOL_TO_CLASS: dict[Protocol, type[LLMProvider]] = {
        Protocol.GEMINI: GeminiProvider,
        Protocol.OPENAI: OpenAIProvider,
        Protocol.HUGGINGFACE: HuggingFaceModel,
    }

    def __init__(self):
        self.config = None
        with open("app/llm_config.yaml", 'r') as f:
            self.config = yaml.safe_load(f)

    def _get_provider_block(self, provider_name: str) -> dict:
        """Look up a provider block by yaml key; raise with a helpful list on miss."""
        providers = self.config.get('providers') or {}
        if provider_name not in providers:
            raise ValueError(
                f"Provider '{provider_name}' not found in llm_config.yaml. "
                f"Known providers: {sorted(providers.keys())}. "
                "Add a block under `providers:` or fix the CURRENT_LLM / "
                "CURRENT_EMBEDDER env var."
            )
        return providers[provider_name]

    def _instantiate(self, provider_name: str) -> LLMProvider:
        """Build an LLMProvider from a config block, dispatching by `protocol:`."""
        block = self._get_provider_block(provider_name)
        protocol_str = block.get('protocol')
        if not protocol_str:
            raise ValueError(
                f"Provider '{provider_name}' has no 'protocol' set; "
                "can't decide which SDK to use."
            )
        try:
            protocol = Protocol(protocol_str)
        except ValueError:
            raise ValueError(
                f"Provider '{provider_name}' declares unknown protocol "
                f"'{protocol_str}'. Known: {[p.value for p in Protocol]}."
            )
        return self._PROTOCOL_TO_CLASS[protocol](model_config=LLMModelConfig(block))

    @property
    def embedding_size(self) -> int:
        """Vector dim expected from the current embedder.

        Preferred source: `embedding_size:` on the provider block. Falls
        back to a well-known-model lookup so pre-existing configs don't
        need to be migrated all at once. Raises if neither resolves.
        """
        name = os.getenv("CURRENT_EMBEDDER", "gemini")
        block = self._get_provider_block(name)
        if 'embedding_size' in block and block['embedding_size'] is not None:
            return int(block['embedding_size'])
        embedding_model = block.get('embedding_model') or ''
        if embedding_model in _EMBEDDING_MODEL_DIMS:
            return _EMBEDDING_MODEL_DIMS[embedding_model]
        raise ValueError(
            f"Provider '{name}' has neither 'embedding_size' nor a "
            f"recognized 'embedding_model' (got: {embedding_model!r}). "
            "Add `embedding_size: <int>` to the block."
        )

    @property
    def embedder(self) -> LLMProvider:
        return self._instantiate(os.getenv("CURRENT_EMBEDDER", "gemini"))

    @property
    def llm(self) -> LLMProvider:
        return self._instantiate(os.getenv("CURRENT_LLM", "gemini"))
        
    async def get_embeddings(self, input):
        """ Get embeddings for input text. """
        
        raw = await self.embedder.embedding_model(input)
        embed= np.array(raw)
        if embed.shape[-1] != self.embedding_size:
            raise ValueError(f"Expected embedding dimension {self.embedding_size}, got {embed.shape[-1]}")
        embeddings = embed.reshape(-1, self.embedding_size)
        if len(input) == 1:
            return embeddings.tolist()[0]
        else:
            return embeddings.tolist()
    
    async def get_response(self, message):
        """Get response from LLM."""
        
        if isinstance(message, str):
            message = [{"role": "user", "content": message}]
        return await self.llm.get_response(message)
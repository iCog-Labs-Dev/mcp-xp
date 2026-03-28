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

class TextProvider(Enum):
    GEMINI = "gemini"
    OPENAI = "openai"

class EmbeddingProvider(Enum):
    GEMINI = "gemini"
    OPENAI = "openai"
    HUGGINGFACE = "huggingface"
    
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
    
class EmbeddingModel(Enum):
    """Defines supported embedding models and their vector sizes."""
    
    GEMINI_EMBEDDING_001 = ("embedding-001", 768)
    GEMINI_EMBEDDING_002 = ("embedding-002", 1408)
    GEMINI_TEXT_EMBEDDING_004 = ("text-embedding-004", 2048)
    OPENAI_TEXT_EMBEDDING_3_SMALL = ("text-embedding-3-small", 1536)
    OPENAI_TEXT_EMBEDDING_3_LARGE = ("text-embedding-3-large", 3072)
    E5_MODEL = ("intfloat/e5-large-v2", 1024)

    @property
    def model_name(self) -> str:
        return self.value[0]

    @property
    def embedding_size(self) -> int:
        return self.value[1]
    
class LLMResponse:
    def __init__(self):
        self.config = None
        with open("app/llm_config.yaml", 'r') as f:
            self.config = yaml.safe_load(f)
            
    @property
    def embedding_size(self) -> int:
        """Return the embedding size based on the current embedding provider."""
        provider = EmbeddingProvider(os.getenv("CURRENT_EMBEDDER", EmbeddingProvider.GEMINI.value)).value
        if provider == EmbeddingProvider.GEMINI.value:
            return EmbeddingModel.GEMINI_EMBEDDING_001.embedding_size
        elif provider == EmbeddingProvider.OPENAI.value:
            return EmbeddingModel.OPENAI_TEXT_EMBEDDING_3_SMALL.embedding_size
        elif provider == EmbeddingProvider.HUGGINGFACE.value:
            return EmbeddingModel.E5_MODEL.embedding_size
        else:
            raise ValueError(f"Unknown embedding provider: {provider}")
        
    @property
    def embedder(self) -> LLMProvider:
        provider = EmbeddingProvider(os.getenv("CURRENT_EMBEDDER", EmbeddingProvider.GEMINI.value)).value
        selected_config = LLMModelConfig(self.config['providers']["openai"])
        if provider == EmbeddingProvider.GEMINI.value:
            return GeminiProvider(model_config=selected_config)
        elif provider == EmbeddingProvider.OPENAI.value:
            return OpenAIProvider(model_config=selected_config)
        elif provider == EmbeddingProvider.HUGGINGFACE.value:
            return HuggingFaceModel(model_config=selected_config)
    
    @property
    def llm(self) -> LLMProvider:
        provider = TextProvider(os.getenv("CURRENT_LLM", TextProvider.GEMINI.value)).value
        selected_config = LLMModelConfig(self.config['providers'][provider])
        if provider ==  TextProvider.GEMINI.value:
            return GeminiProvider(model_config=selected_config)
        elif provider ==  TextProvider.OPENAI.value:
            return OpenAIProvider(model_config=selected_config)
        
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
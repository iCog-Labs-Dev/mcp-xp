import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from typing import List, Dict

import sys
sys.path.append(".")

from app.llm_provider import (
    LLMProvider,
    GeminiProvider,
    OpenAIProvider,
    HuggingFaceModel
)
from app.llm_config import LLMModelConfig


@pytest.fixture
def gemini_config():
    """Fixture for Gemini model configuration."""
    return LLMModelConfig({
        "model": "gemini-1.5-flash",
        "provider": "gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "embedding_model": "text-embedding-004",
        "temperature": 0.7,
        "max_tokens": 10000,
        "top_p": 1.0,
        "stop": []
    })


@pytest.fixture
def openai_config():
    """Fixture for OpenAI model configuration."""
    return LLMModelConfig({
        "model": "gpt-4",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "embedding_model": "text-embedding-3-small",
        "temperature": 0.7,
        "max_tokens": 10000,
        "top_p": 1.0,
        "stop": []
    })


@pytest.fixture
def huggingface_config():
    """Fixture for HuggingFace model configuration."""
    return LLMModelConfig({
        "model": "intfloat/e5-base-v2",
        "provider": "huggingface",
        "base_url": "",
        "embedding_model": None,
        "temperature": 0.7,
        "max_tokens": 10000,
        "top_p": 1.0,
        "stop": []
    })


@pytest.fixture
def sample_messages():
    """Sample messages for testing."""
    return [
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "The capital of France is Paris."}
    ]


class TestGeminiProvider:
    """Tests for GeminiProvider class."""

    @pytest.mark.asyncio
    async def test_gemini_provider_initialization(self, gemini_config):
        """Test GeminiProvider initialization."""
        with patch("app.llm_provider.genai.configure") as mock_configure:
            provider = GeminiProvider(gemini_config)
            
            assert provider.config == gemini_config
            mock_configure.assert_called_once()

    @pytest.mark.asyncio
    async def test_gemini_get_response_success_with_json(self, gemini_config, sample_messages):
        """Test successful response from Gemini API with JSON content."""
        with patch("app.llm_provider.genai.configure"):
            provider = GeminiProvider(gemini_config)
            
            # Mock the GenerativeModel and its response
            mock_model = MagicMock()
            mock_response = MagicMock()
            mock_response.text = '```json\n{"answer": "Paris"}\n```'
            mock_model.generate_content_async = AsyncMock(return_value=mock_response)
            
            with patch("app.llm_provider.genai.GenerativeModel", return_value=mock_model):
                result = await provider.get_response(sample_messages)
                
                assert isinstance(result, dict)
                assert result == {"answer": "Paris"}
                mock_model.generate_content_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_gemini_get_response_success_plain_text(self, gemini_config, sample_messages):
        """Test successful response from Gemini API with plain text."""
        with patch("app.llm_provider.genai.configure"):
            provider = GeminiProvider(gemini_config)
            
            mock_model = MagicMock()
            mock_response = MagicMock()
            mock_response.text = "The capital of France is Paris."
            mock_model.generate_content_async = AsyncMock(return_value=mock_response)
            
            with patch("app.llm_provider.genai.GenerativeModel", return_value=mock_model):
                result = await provider.get_response(sample_messages)
                
                assert isinstance(result, str)
                assert result == "The capital of France is Paris."

    @pytest.mark.asyncio
    async def test_gemini_get_response_api_error(self, gemini_config, sample_messages):
        """Test Gemini API error handling."""
        with patch("app.llm_provider.genai.configure"):
            provider = GeminiProvider(gemini_config)
            
            mock_model = MagicMock()
            mock_model.generate_content_async = AsyncMock(
                side_effect=Exception("API rate limit exceeded")
            )
            
            with patch("app.llm_provider.genai.GenerativeModel", return_value=mock_model):
                with pytest.raises(RuntimeError, match="Gemini API error"):
                    await provider.get_response(sample_messages)

    @pytest.mark.asyncio
    async def test_gemini_embedding_model_success(self, gemini_config):
        """Test successful embedding generation with Gemini."""
        with patch("app.llm_provider.genai.configure"):
            provider = GeminiProvider(gemini_config)
            
            batch = ["text1", "text2", "text3"]
            mock_embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]]
            
            def mock_embed_content(model, content):
                return {"embedding": mock_embeddings[:len(content)]}
            
            with patch("app.llm_provider.genai.embed_content", side_effect=mock_embed_content):
                with patch("asyncio.to_thread", side_effect=lambda f, **kwargs: f(**kwargs)):
                    result = await provider.embedding_model(batch)
                    
                    assert len(result) == 3
                    assert result == mock_embeddings

    @pytest.mark.asyncio
    async def test_gemini_embedding_model_with_batching(self, gemini_config):
        """Test embedding generation with large batch requiring batching."""
        with patch("app.llm_provider.genai.configure"):
            provider = GeminiProvider(gemini_config)
            
            # Create a batch larger than batch_size (100)
            batch = [f"text{i}" for i in range(150)]
            
            call_count = 0
            def mock_embed_content(model, content):
                nonlocal call_count
                call_count += 1
                return {"embedding": [[0.1, 0.2] for _ in content]}
            
            with patch("app.llm_provider.genai.embed_content", side_effect=mock_embed_content):
                with patch("asyncio.to_thread", side_effect=lambda f, **kwargs: f(**kwargs)):
                    result = await provider.embedding_model(batch)
                    
                    assert len(result) == 150
                    # Should be called twice: once for first 100, once for remaining 50
                    assert call_count == 2

    @pytest.mark.asyncio
    async def test_gemini_embedding_model_error_handling(self, gemini_config):
        """Test embedding generation with error handling."""
        with patch("app.llm_provider.genai.configure"):
            provider = GeminiProvider(gemini_config)
            
            batch = ["text1", "text2"]
            
            def mock_embed_content(model, content):
                raise Exception("Temporary error")
            
            with patch("app.llm_provider.genai.embed_content", side_effect=mock_embed_content):
                with patch("asyncio.to_thread", side_effect=lambda f, **kwargs: f(**kwargs)):
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        result = await provider.embedding_model(batch)
                        
                        # When error occurs, it logs and continues, returning empty list
                        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_gemini_embedding_model_missing_config(self, gemini_config):
        """Test embedding generation with missing embedding_model config."""
        gemini_config.config_data.pop("embedding_model")
        
        with patch("app.llm_provider.genai.configure"):
            provider = GeminiProvider(gemini_config)
            
            batch = ["text1"]
            
            with pytest.raises(ValueError, match="Missing 'embedding_model'"):
                await provider.embedding_model(batch)


class TestOpenAIProvider:
    """Tests for OpenAIProvider class."""

    @pytest.mark.asyncio
    async def test_openai_provider_initialization(self, openai_config):
        """Test OpenAIProvider initialization."""
        with patch("app.llm_provider.AsyncOpenAI") as mock_client:
            provider = OpenAIProvider(openai_config)
            
            assert provider.config == openai_config
            mock_client.assert_called_once()

    @pytest.mark.asyncio
    async def test_openai_get_response_success_with_json(self, openai_config, sample_messages):
        """Test successful response from OpenAI API with JSON content."""
        with patch("app.llm_provider.AsyncOpenAI") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            
            # Mock the response
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = '```json\n{"answer": "Paris"}\n```'
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            
            provider = OpenAIProvider(openai_config)
            result = await provider.get_response(sample_messages)
            
            assert isinstance(result, dict)
            assert result == {"answer": "Paris"}

    @pytest.mark.asyncio
    async def test_openai_get_response_success_plain_text(self, openai_config, sample_messages):
        """Test successful response from OpenAI API with plain text."""
        with patch("app.llm_provider.AsyncOpenAI") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "The capital of France is Paris."
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            
            provider = OpenAIProvider(openai_config)
            result = await provider.get_response(sample_messages)
            
            assert isinstance(result, str)
            assert result == "The capital of France is Paris."

    @pytest.mark.asyncio
    async def test_openai_get_response_api_error(self, openai_config, sample_messages):
        """Test OpenAI API error handling."""
        with patch("app.llm_provider.AsyncOpenAI") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            
            mock_client.chat.completions.create = AsyncMock(
                side_effect=Exception("API rate limit exceeded")
            )
            
            provider = OpenAIProvider(openai_config)
            
            with pytest.raises(RuntimeError, match="OpenAI API error"):
                await provider.get_response(sample_messages)

    @pytest.mark.asyncio
    async def test_openai_embedding_model_success(self, openai_config):
        """Test successful embedding generation with OpenAI."""
        with patch("app.llm_provider.AsyncOpenAI") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            
            batch = ["text1", "text2", "text3"]
            
            # Mock embedding response
            mock_response = MagicMock()
            mock_response.data = [
                MagicMock(embedding=[0.1, 0.2, 0.3]),
                MagicMock(embedding=[0.4, 0.5, 0.6]),
                MagicMock(embedding=[0.7, 0.8, 0.9])
            ]
            mock_client.embeddings.create = AsyncMock(return_value=mock_response)
            
            provider = OpenAIProvider(openai_config)
            result = await provider.embedding_model(batch)
            
            assert len(result) == 3
            assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]]

    @pytest.mark.asyncio
    async def test_openai_embedding_model_with_batching(self, openai_config):
        """Test embedding generation with large batch requiring batching."""
        with patch("app.llm_provider.AsyncOpenAI") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            
            # Create a batch larger than batch_size (100)
            batch = [f"text{i}" for i in range(150)]
            
            # Mock embedding response
            def create_mock_response(count):
                mock_response = MagicMock()
                mock_response.data = [MagicMock(embedding=[0.1, 0.2]) for _ in range(count)]
                return mock_response
            
            call_count = 0
            async def mock_create(**kwargs):
                nonlocal call_count
                call_count += 1
                input_list = kwargs.get("input", [])
                return create_mock_response(len(input_list))
            
            mock_client.embeddings.create = mock_create
            
            provider = OpenAIProvider(openai_config)
            result = await provider.embedding_model(batch)
            
            assert len(result) == 150
            # Should be called twice: once for first 100, once for remaining 50
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_openai_embedding_model_error_retry(self, openai_config):
        """Test embedding generation with error and retry."""
        with patch("app.llm_provider.AsyncOpenAI") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            
            batch = ["text1", "text2"]
            
            call_count = 0
            async def mock_create(**kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("Temporary error")
                mock_response = MagicMock()
                mock_response.data = [
                    MagicMock(embedding=[0.1, 0.2]),
                    MagicMock(embedding=[0.3, 0.4])
                ]
                return mock_response
            
            mock_client.embeddings.create = mock_create
            
            provider = OpenAIProvider(openai_config)
            
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await provider.embedding_model(batch)
                
                # First call fails and returns empty list for that batch
                # The error is caught and logged, but doesn't retry automatically
                assert len(result) == 0

    @pytest.mark.asyncio
    async def test_openai_embedding_model_missing_config(self, openai_config):
        """Test embedding generation with missing embedding_model config."""
        openai_config.config_data.pop("embedding_model")
        
        with patch("app.llm_provider.AsyncOpenAI"):
            provider = OpenAIProvider(openai_config)
            
            batch = ["text1"]
            
            with pytest.raises(ValueError, match="Missing 'embedding_model'"):
                await provider.embedding_model(batch)


class TestHuggingFaceModel:
    """Tests for HuggingFaceModel class."""

    @pytest.mark.asyncio
    async def test_huggingface_provider_initialization(self, huggingface_config):
        """Test HuggingFaceModel initialization."""
        with patch("app.llm_provider.SentenceTransformer") as mock_st:
            with patch("app.llm_provider.torch.cuda.is_available", return_value=False):
                provider = HuggingFaceModel(huggingface_config)
                
                assert provider.config == huggingface_config
                mock_st.assert_called_once()

    @pytest.mark.asyncio
    async def test_huggingface_embedding_model_success(self, huggingface_config):
        """Test successful embedding generation with HuggingFace."""
        with patch("app.llm_provider.SentenceTransformer") as mock_st_class:
            with patch("app.llm_provider.torch.cuda.is_available", return_value=False):
                mock_model = MagicMock()
                mock_st_class.return_value = mock_model
                
                batch = ["text1", "text2", "text3"]
                mock_embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]]
                
                # Mock the encode method to return numpy-like array
                mock_model.encode.return_value.tolist.return_value = mock_embeddings
                
                provider = HuggingFaceModel(huggingface_config)
                
                # Mock run_in_executor to directly call the lambda
                async def mock_run_in_executor(executor, func):
                    return func()
                
                with patch.object(asyncio.get_event_loop(), "run_in_executor", side_effect=mock_run_in_executor):
                    result = await provider.embedding_model(batch)
                    
                    assert len(result) == 3
                    assert result == mock_embeddings

    @pytest.mark.asyncio
    async def test_huggingface_embedding_model_with_batching(self, huggingface_config):
        """Test embedding generation with large batch requiring batching."""
        with patch("app.llm_provider.SentenceTransformer") as mock_st_class:
            with patch("app.llm_provider.torch.cuda.is_available", return_value=False):
                mock_model = MagicMock()
                mock_st_class.return_value = mock_model
                
                # Create a batch larger than batch_size (100)
                batch = [f"text{i}" for i in range(150)]
                
                call_count = 0
                def mock_encode(*args, **kwargs):
                    nonlocal call_count
                    call_count += 1
                    batch_segment = args[0]
                    mock_result = MagicMock()
                    mock_result.tolist.return_value = [[0.1, 0.2] for _ in batch_segment]
                    return mock_result
                
                mock_model.encode = mock_encode
                
                provider = HuggingFaceModel(huggingface_config)
                
                async def mock_run_in_executor(executor, func):
                    return func()
                
                with patch.object(asyncio.get_event_loop(), "run_in_executor", side_effect=mock_run_in_executor):
                    result = await provider.embedding_model(batch)
                    
                    assert len(result) == 150
                    # Should be called twice: once for first 100, once for remaining 50
                    assert call_count == 2

    @pytest.mark.asyncio
    async def test_huggingface_embedding_model_error_retry(self, huggingface_config):
        """Test embedding generation with error and retry."""
        with patch("app.llm_provider.SentenceTransformer") as mock_st_class:
            with patch("app.llm_provider.torch.cuda.is_available", return_value=False):
                mock_model = MagicMock()
                mock_st_class.return_value = mock_model
                
                batch = ["text1", "text2"]
                
                call_count = 0
                def mock_encode(*args, **kwargs):
                    nonlocal call_count
                    call_count += 1
                    if call_count < 3:
                        raise Exception("Temporary error")
                    mock_result = MagicMock()
                    mock_result.tolist.return_value = [[0.1, 0.2], [0.3, 0.4]]
                    return mock_result
                
                mock_model.encode = mock_encode
                
                provider = HuggingFaceModel(huggingface_config)
                
                async def mock_run_in_executor(executor, func):
                    return func()
                
                with patch.object(asyncio.get_event_loop(), "run_in_executor", side_effect=mock_run_in_executor):
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        result = await provider.embedding_model(batch)
                        
                        # Should succeed on third attempt
                        assert len(result) == 2
                        assert call_count == 3

    @pytest.mark.asyncio
    async def test_huggingface_embedding_model_all_retries_fail(self, huggingface_config):
        """Test embedding generation when all retries fail."""
        with patch("app.llm_provider.SentenceTransformer") as mock_st_class:
            with patch("app.llm_provider.torch.cuda.is_available", return_value=False):
                mock_model = MagicMock()
                mock_st_class.return_value = mock_model
                
                batch = ["text1", "text2"]
                
                def mock_encode(*args, **kwargs):
                    raise Exception("Persistent error")
                
                mock_model.encode = mock_encode
                
                provider = HuggingFaceModel(huggingface_config)
                
                async def mock_run_in_executor(executor, func):
                    return func()
                
                with patch.object(asyncio.get_event_loop(), "run_in_executor", side_effect=mock_run_in_executor):
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        result = await provider.embedding_model(batch)
                        
                        # Should return empty list after all retries fail
                        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_huggingface_cuda_available(self, huggingface_config):
        """Test HuggingFaceModel initialization with CUDA available."""
        with patch("app.llm_provider.SentenceTransformer") as mock_st:
            with patch("app.llm_provider.torch.cuda.is_available", return_value=True):
                provider = HuggingFaceModel(huggingface_config)
                
                # Verify SentenceTransformer was called with cuda device
                call_args = mock_st.call_args
                assert call_args[1]["device"] == "cuda"

    @pytest.mark.asyncio
    async def test_huggingface_get_response_calls_parent(self, huggingface_config):
        """Test that get_response calls the parent abstract method."""
        with patch("app.llm_provider.SentenceTransformer"):
            with patch("app.llm_provider.torch.cuda.is_available", return_value=False):
                provider = HuggingFaceModel(huggingface_config)
                
                # get_response calls the parent's abstract method which returns a coroutine
                # Since it's not implemented, it just returns the coroutine object
                result = provider.get_response([{"role": "user", "content": "test"}])
                
                # Verify it returns a coroutine (not implemented properly)
                import inspect
                assert inspect.iscoroutine(result)
                
                # Clean up the coroutine
                result.close()


class TestLLMProviderAbstract:
    """Tests for abstract LLMProvider base class."""

    def test_llm_provider_cannot_be_instantiated(self, gemini_config):
        """Test that abstract LLMProvider cannot be instantiated directly."""
        with pytest.raises(TypeError):
            LLMProvider(gemini_config)

    def test_llm_provider_requires_get_response_implementation(self, gemini_config):
        """Test that subclasses must implement get_response."""
        class IncompleteProvider(LLMProvider):
            pass
        
        with pytest.raises(TypeError):
            IncompleteProvider(gemini_config)

import asyncio
import json
import logging

import os
import yaml
import torch

from typing import Dict, List
from abc import ABC, abstractmethod

from google import genai
from google.genai import types
from openai import AsyncOpenAI
from sentence_transformers import SentenceTransformer

from sys import path
path.append(".")

from app.llm_config import LLMModelConfig
from app.config import GEMINI_API_KEY, OPENAI_API_KEY
from app.utils import _extract_json_from_llm_response

# Abstract Provider Base Class
class LLMProvider(ABC):
    def __init__(self, model_config: LLMModelConfig) -> None:
        self.config = model_config

    @abstractmethod
    async def get_response(self, messages: List[Dict[str, str]]) -> str:
        """
        Retrieve a response given a list of messages.
        """
        pass
    

class GeminiProvider(LLMProvider):
    def __init__(self, model_config):
        super().__init__(model_config)
        self.client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=10000),
        )
        self.log = logging.getLogger(self.__class__.__name__)

    @staticmethod
    def _build_contents(messages: List[Dict[str, str]]) -> List[types.Content]:
        contents: List[types.Content] = []

        for message in messages:
            role = message.get("role", "user")
            sdk_role = "model" if role in {"assistant", "model"} else "user"
            text = message.get("content", "")
            contents.append(
                types.Content(
                    role=sdk_role,
                    parts=[types.Part.from_text(text=text)],
                )
            )

        return contents
        
    async def get_response(self, messages: List[Dict[str, str]]) -> str:
        """
        Sends a request to the Gemini API via google-genai and returns the generated response.
        Identical behavior to the HTTP version: builds the same 'contents' structure, applies the same
        generation config, extracts a JSON codeblock if present, and returns parsed JSON when possible.
        """

        generation_config = types.GenerateContentConfig(
            temperature=self.config.config_data.get("temperature", 0.7),
            max_output_tokens=self.config.config_data.get("max_tokens", 10000),
            top_p=self.config.config_data.get("top_p", 1),
            stop_sequences=self.config.config_data.get("stop", []),
        )

        model_name = self.config.config_data.get("model")

        try:
            response = await self.client.aio.models.generate_content(
                model=model_name,
                contents=self._build_contents(messages),
                config=generation_config,
            )

            content = response.text or ""
            json_content = _extract_json_from_llm_response(content)
            try:
                return json.loads(json_content)
            except json.JSONDecodeError:
                return json_content

        except Exception as e:
            self.log.error(f"Gemini API error: {str(e)}")
            raise RuntimeError(f"Gemini API error: {str(e)}") from e

    async def embedding_model(self, batch: List[str]) -> List[List[float]]:
        """
        Generates embeddings for a batch of texts using the google-genai SDK.
        Logic preserved:
        - Uses batching (size 100)
        - Retries after an error with a fixed sleep time
        - Returns a flat list of embeddings
        - Uses the async SDK client directly
        """
        

        embeddings: List[List[float]] = []
        batch_size = 100
        sleep_time = 2  # Time to wait before retrying after an error

        embedding_model = self.config.config_data.get("embedding_model")
        if not embedding_model:
            raise ValueError("Missing 'embedding_model' in config_data")
        embedding_model = embedding_model.removeprefix("models/")

        for i in range(0, len(batch), batch_size):
            batch_segment = batch[i:i + batch_size]
            
            try:
                result = await self.client.aio.models.embed_content(
                    model=embedding_model,
                    contents=batch_segment,
                )

                batch_embeddings = [embedding.values for embedding in result.embeddings or []]
                embeddings.extend(batch_embeddings)

            except Exception as e:
                self.log.error(f"Gemini Embedding error: {e}")
                await asyncio.sleep(sleep_time)

        self.log.info("Gemini embeddings generated.")
        return embeddings



class OpenAIProvider(LLMProvider):
    def __init__(self, model_config):
        super().__init__(model_config)
        self.client = AsyncOpenAI(
                        api_key=OPENAI_API_KEY,
                        base_url = model_config.base_url
                        )

        self.log = logging.getLogger(self.__class__.__name__)
        
    async def get_response(self, messages: List[Dict[str, str]]) -> str:
        """
        Sends a request to the OpenAI API via openai.AsyncOpenAI and returns the generated response.
        Identical behavior to the HTTP version: builds the same 'contents' structure, applies the same
        generation config, extracts a JSON codeblock if present, and returns parsed JSON when possible.
        """
        
        # Messages are already in the format expected by OpenAI (list of dicts with 'role' and 'content')

        # Generation config: same fields as before, mapped to SDK names
        generation_config = {
            "temperature": self.config.config_data.get("temperature", 0.7),
            "top_p": self.config.config_data.get("top_p", 1),
            "stop": self.config.config_data.get("stop", []),
        }

        model_name = self.config.config_data.get("model")
        sem = asyncio.Semaphore(1)

        async with sem:
            try:
                # Mirror the previous 10s timeout
                response = await self.client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    timeout=60.0,
                    **generation_config
                )

                content = response.choices[0].message.content
                json_content = _extract_json_from_llm_response(content)
                try:
                    return json.loads(json_content)
                except json.JSONDecodeError:
                    return json_content

            except Exception as e:
                self.log.error(f"OpenAI API error: {str(e)}")
                raise RuntimeError(f"OpenAI API error: {str(e)}") from e

    async def embedding_model(self, batch: List[str]) -> List[List[float]]:
        """
        Generates embeddings for a batch of texts using the openai.AsyncOpenAI SDK.
        Logic preserved:
        - Uses batching (size 100)
        - Retries after an error with a fixed sleep time
        - Returns a flat list of embeddings
        - Async native calls
        """
        

        embeddings: List[List[float]] = []
        batch_size = 100
        sleep_time = 2  # Time to wait before retrying after an error


        embedding_model = self.config.config_data.get("embedding_model")
        if not embedding_model:
            raise ValueError("Missing 'embedding_model' in config_data")

        sem = asyncio.Semaphore(5)  # limit concurrency to avoid rate limit spikes

        async def fetch_batch(batch_segment):
            async with sem:
                try:
                    result = await self.client.embeddings.create(
                        model=embedding_model,
                        input=batch_segment,
                    )
                    return [e.embedding for e in result.data]
                except Exception as e:
                    self.log.error(f"OpenAI Embedding error: {e}")
                    await asyncio.sleep(sleep_time)  # backoff before retry
                    return []

        # Create all batch tasks
        tasks = [
            fetch_batch(batch[i:i + batch_size])
            for i in range(0, len(batch), batch_size)
        ]

        # Run concurrently
        results = await asyncio.gather(*tasks)

        # Flatten results into embeddings
        for r in results:
            embeddings.extend(r)

        self.log.info("OpenAI embeddings generated.")
        return embeddings

# TODO: Fix abstraction here, since we are abstracting a configuration that the hugging face model wont be using.
class HuggingFaceModel(LLMProvider):

    def __init__(self, model_config):
        super().__init__(model_config)
        self.log = logging.getLogger(__class__.__name__)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model_name = os.getenv("HUGGINGFACE_MODEL", "intfloat/e5-base-v2")
        self.log.info(f"Loading SentenceTransformer on device: {device}")
        # Load the SentenceTransformer model
        self.model = SentenceTransformer(self.model_name, device=device)
    
    def get_response(self, messages):
        return super().get_response(messages)
    

    async def embedding_model(self, batch: List[str]) -> List[List[float]]:
        """ Generates embeddings for a batch of texts using a local SentenceTransformer model. Returns a flat list of embeddings """

        embeddings: List[List[float]] = []
        batch_size = 100
        sleep_time = 2  # seconds to wait before retry

        for i in range(0, len(batch), batch_size):
            batch_segment = batch[i:i + batch_size]

            for attempt in range(3):  # up to 3 retries
                try:
                    loop = asyncio.get_event_loop()
                    # Run encode in a background thread so we don't block the event loop
                    result = await loop.run_in_executor(
                        None,
                        lambda: self.model.encode(
                            batch_segment,
                            convert_to_numpy=True,
                            show_progress_bar=False
                        ).tolist()
                    )
                    embeddings.extend(result)
                    break  # success, move to next batch

                except Exception as e:
                    self.log.error(
                        f"SentenceTransformer encode error (attempt {attempt + 1}/3): {e}"
                    )
                    await asyncio.sleep(sleep_time)

            else:
                # If all retries fail, log and continue
                self.log.warning(f"Failed to encode batch segment starting at index {i} after 3 attempts.")

        self.log.info("SentenceTransformer embeddings generated.")
        return embeddings

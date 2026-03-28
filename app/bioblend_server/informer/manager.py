import os
import traceback
import pandas as pd
import numpy as np
import logging
import random
import asyncio
from typing import Any
from dotenv import load_dotenv

from qdrant_client import QdrantClient, models
from qdrant_client.models import Filter, FieldCondition, MatchText, PointStruct

from app.log_setup import configure_logging
from app.bioblend_server.informer.utils import LLMResponse

class InformerManager:
    """
    Manages all vector database operations for the GalaxyInformer, including
    data preparation, embedding generation, storage, and retrieval from Qdrant.
    """
    def __init__(self):
        self.embedder = LLMResponse()
        self.client: QdrantClient = None       
        self.logger = logging.getLogger(self.__class__.__name__)

    @classmethod
    async def create(cls):
        """Asynchronous factory to create and initialize an InformerManager instance."""
        self = cls()
        load_dotenv()
        configure_logging()
        try:
            # Initialize Qdrant client asynchronously if possible, or run in executor
            QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
            QDRANT_PORT: str = os.getenv("QDRANT_HTTP_PORT", "6555")
            self.client = QdrantClient(f"http://{QDRANT_HOST}:{QDRANT_PORT}", timeout=120)
            self.logger.info("Qdrant Client initialized")
            self.logger.info("InformerManager connected to Qdrant successfully.")
        except Exception as e:
            self.logger.exception(f"Qdrant connection failed: {e}")
            raise
        return self
    
    def _ensure_collection_exists(self, collection_name: str):
        """
        A private helper to ensure a collection exists in Qdrant, creating it if necessary.
        """
        try:
            if not self.client.collection_exists(collection_name):
                self.client.create_collection(
                    collection_name,
                    vectors_config=models.VectorParams(
                        size=self.embedder.embedding_size,
                        distance=models.Distance.COSINE
                    )
                )
                self.client.create_payload_index(
                    collection_name=collection_name,
                    field_name="name",
                    field_schema=models.TextIndexParams(
                        type=models.TextIndexType.TEXT,
                        tokenizer=models.TokenizerType.WORD,
                        min_token_len=1,
                        max_token_len=30,
                    ),
                )
                self.logger.info(f"Collection '{collection_name}' created successfully.")
        except Exception as e:
            self.logger.error(f"Failed to ensure collection '{collection_name}' exists: {e}")
            traceback.print_exc()

    def _prepare_dataframe(self, entities: list[dict]) -> pd.DataFrame:
        """
        A private helper to convert the list of entity dictionaries into a pandas DataFrame.
        """
        if isinstance(entities, list) and all(isinstance(d, dict) for d in entities):
            return pd.DataFrame(entities)
        else:
            self.logger.error("Invalid data format. Expected a list of dictionaries.")
            raise ValueError("Input data must be a list of dictionaries.")

    async def _generate_embeddings(self, df: pd.DataFrame) -> pd.DataFrame:
        try:
            self.logger.info("Generating vector embeddings for entity content.")
            df['dense'] = await self.embedder.get_embeddings(df['content'].tolist())
            self.logger.info(f"Embeddings generated successfully with size {self.embedder.embedding_size}.")
            return df
        except Exception as e:
            self.logger.error(f"Error generating dense embeddings: {e}")
            traceback.print_exc()
            raise


    def _upsert_points(self, collection_name: str, df: pd.DataFrame, batch_size = 500):
        """
        A private helper to perform the final upsert operation into the Qdrant collection.
        """
        try:
            # Define which columns from the DataFrame become the payload
            excluded_columns = {"dense"}
            payload_columns = [col for col in df.columns if col not in excluded_columns]
            payloads_list = [
                {col: getattr(item, col) for col in payload_columns}
                for item in df.itertuples(index=False)
            ]

            if 'id' not in df.columns:
                df['id'] = [random.randint(100000, 999999) for _ in range(len(df))]

            self._ensure_collection_exists(collection_name)
            
            total_points = len(df)
            for start in range(0, total_points, batch_size):
                end = start + batch_size
                batch_ids = df['id'].iloc[start:end].tolist()
                batch_vectors = df['dense'].iloc[start:end].tolist()
                batch_payloads = payloads_list[start:end]

                self.client.upsert(
                    collection_name=collection_name,
                    points=models.Batch(
                        ids=batch_ids,
                        vectors=batch_vectors,
                        payloads=batch_payloads
                    ),
                )
                self.logger.info(f"Upserted points {start}-{end} to '{collection_name}'.")
                
            return "Data Successfully Uploaded"
        
        except Exception as e:
            self.logger.error(f"Error upserting data to Qdrant: {e}")
            traceback.print_exc()

    async def embed_and_store_entities(self, entities: list[dict], collection_name: str):
        """
        Processes and saves Galaxy entities to a specified Qdrant collection.
        This is the main public method for saving data.

        Called in: GalaxyInformer.retrive_informer_data
        """
        try:
            df = self._prepare_dataframe(entities)
            
            if df.empty or 'content' not in df.columns:
                raise ValueError("No data available for embedding")
            
            df_embedded = await self._generate_embeddings(df)
            if df_embedded is not None:
                if self.client.collection_exists(collection_name):
                    self.delete_collection(collection_name)
                    
                return self._upsert_points(collection_name, df_embedded)
        except Exception as e:
            self.logger.error(f"Failed to embed and store entities in '{collection_name}': {e}")
            traceback.print_exc()

    def search_by_vector(self,
                         collection: str,
                         query_vector: list,
                         entity_type: str,
                         score_threshold: int = 0.3,
                         limit = 50
                         ) -> dict:
        """
        Performs a semantic search in a Qdrant collection based on a query vector.

        Called in: GalaxyInformer.semantic_search
        """

        try:
            result = self.client.query_points(
                collection_name=collection,
                query=query_vector,
                with_payload=True,
                score_threshold=score_threshold,
                limit=limit
            ).points
            
            response = []
            if entity_type == "tool":

                for point in result:
                    response.append({
                        "score": point.score,
                        "description": point.payload.get('description', 'description not available'),
                        "tool_id": point.payload.get('tool_id'),
                        "name": point.payload.get('name'),
                        "content": point.payload.get("content")

                    })
            elif entity_type == "workflow":
                for point in result:
                    response.append({
                        "score": point.score,
                        'description': point.payload.get('description', 'unkown'),
                        "owner": point.payload.get('owner', 'unknown'),
                        "workflow_id": point.payload.get('workflow_id'),
                        "name": point.payload.get('name'),
                        "content": point.payload.get("content")
                    })
            elif entity_type == 'dataset':
                for point in result:
                    response.append({
                        'score': point.score,
                        "dataset_id": point.payload.get('dataset_id'),
                        "name": point.payload.get('name'),
                        "full_path": point.payload.get('full_path', 'unknown'),
                        "type": point.payload.get('type', 'unknown'),
                        "source": point.payload.get('source'),
                        "content": point.payload.get("content")
                    })
                
            return response
        except Exception as e:
            self.logger.error(f"Error searching collection '{collection}': {e}")
            traceback.print_exc()
            return {"error": "Search failed"}

    def delete_collection(self, collection_name: str):
        """ Deletes a Qdrant collection if it exists."""
        
        try:
            self.client.delete_collection(collection_name)
            self.logger.info(f"Successfully deleted collection: {collection_name}")
        except Exception as e:
            # Qdrant client might raise an exception if the collection doesn't exist.
            # We can log this as info or a warning instead of an error.
            self.logger.warning(f"Could not delete collection '{collection_name}'. It might not exist. Details: {e}")
            
    
    async def match_name_from_collection(self, workflow_collection_name: str, workflow_name: str) -> tuple[Any, list[PointStruct]]:
        """ Retrieve documents from a collection matching the specified workflow name. """
        
        loop = asyncio.get_running_loop()
        hits = await loop.run_in_executor(
            None,
            lambda: self.client.scroll(
                collection_name=workflow_collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="name",
                            match=MatchText(text=workflow_name)
                        )
                    ]
                ),
                limit=1
            )
        )
        return hits
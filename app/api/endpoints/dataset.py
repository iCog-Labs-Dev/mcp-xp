import logging
import httpx
from typing import Optional, Literal
from starlette.status import HTTP_202_ACCEPTED
from fastapi import (
    APIRouter,
    Query,
    HTTPException
    )

from app.log_setup import configure_logging
from app.context import current_api_key
from app.config import GALAXY_URL
from app.galaxy import GalaxyClient
from app.enumerations import CollectionNames
from app.api.schemas.dataset import LocalDatasetImportResponse, IndexingResponse
from app.api.endpoints.invocation import ws_manager, mongo_client, invocation_cache
from app.GX_integration.invocations.output_indexer import OutputIndexer 

configure_logging()
log = logging.getLogger("Dataset")

router = APIRouter()

@router.post(
"/local_import",
response_model = LocalDatasetImportResponse,
summary="Import/Adopt a local dataset into galaxy, return the existing history_id and the dataset_id of the adopted file.",
tags = ["Histories & Data"]

)
async def proxy_adopt_local_file(
    file_path: str = Query(..., description="Absolute path to the local file"),
    file_name: Optional[str] = Query(None, description="Display name in history"),
    extension: Optional[str] = Query(None, description="Galaxy extension (fastq.gz, vcf, etc.)"),
    file_origin: Optional[Literal["annotation", "hypothesis"]] = Query(None),
    unique_id: Optional[str] = Query(None, description="unique identifier for the file to be adopted. Preferably annotation/Hypothesis ID."),
    hist_id: Optional[str] = Query(None),
    ):
    """
    Adopts a local file into Galaxy via proxy, optionally caching the response for annotation or hypothesis files.
    This endpoint proxies a request to Galaxy's `/api/datasets/adopt_file` to import a file located in local storage.
    """

    if file_origin and unique_id:
        storage_key = f"{file_origin}_{unique_id}"
        try:
            stored_response = await mongo_client.get(collection_name=CollectionNames.DATA_ADOPTATION.value, key = storage_key)
            if stored_response:
                return LocalDatasetImportResponse(**stored_response)
        except Exception as e:
            log.warning(f"Failed to fetch from storage: {e}")
            
    params = {
    "file_path": file_path,
    "file_name": file_name,
    "extension": extension,
    "file_origin": file_origin,
    "hist_id": hist_id,
    }


    # remove None values to avoid polluting query string
    params = {k: v for k, v in params.items() if v is not None}
    api_key = current_api_key.get()
    headers = {"x-api-key": api_key}
    

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            
            resp = await client.post(
                url = f"{GALAXY_URL}/api/datasets/adopt_file",
                params=params,
                headers=headers,
            )
            
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Upstream service unreachable: {exc}",
                )

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=resp.status_code,
            detail=resp.text,
            )

    response = resp.json()
    
    if file_origin and unique_id:
        storage_key = f"{file_origin}_{unique_id}"
        try:
            await mongo_client.set(collection_name= CollectionNames.DATA_ADOPTATION.value, key = storage_key, value = response)
        except Exception as e:
            log.warning(f"Failed to store response: {e}")
            
    return LocalDatasetImportResponse(**response)

@router.post(
    "/object_import",
    response_model=LocalDatasetImportResponse,
    summary=(
        "Import/Adopt a MinIO/S3 object into Galaxy, return the existing history_id and the dataset_id of the adopted object."
        "Note: For this to work properly, the .dat object naming should follow the format 'dataset_{uuid}.dat' as Galaxy's internal code expects this format."
        ),
    tags=["Histories & Data"]
)
async def proxy_adopt_minio_object(
    bucket: str = Query(..., description="Bucket name (must match the bucket and the object store id in the config.)"),
    object_key: str = Query(..., description="Full object key (path for object store .../file_path/object_name.dat)."),
    file_name: Optional[str] = Query(None, description="Display name in history"),
    extension: Optional[str] = Query(None, description="Galaxy extension (fastq.gz, vcf, etc.)"),
    file_origin: Optional[Literal["annotation", "hypothesis"]] = Query(None),
    unique_id: Optional[str] = Query(None, description="unique identifier for the file to be adopted. Preferably annotation/Hypothesis ID."),
    hist_id: Optional[str] = Query(None, description="Encoded history_id"),
):
    """
    Adopts an object from a MinIO bucket into Galaxy via proxy, optionally caching the response.
    This endpoint proxies a request to Galaxy's `/api/datasets/adopt_object` to import a file from MinIO object storage.
    """
    if file_origin and unique_id:
        storage_key = f"{file_origin}_{unique_id}"
        try:
            stored_response = await mongo_client.get(collection_name=CollectionNames.DATA_ADOPTATION.value, key = storage_key)
            if stored_response:
                return LocalDatasetImportResponse(**stored_response)
        except Exception as e:
            log.warning(f"Failed to fetch from storage: {e}")
            
    params = {
        "bucket": bucket,
        "object_key": object_key,
        "file_name": file_name,
        "extension": extension,
        "file_origin": file_origin,
        "hist_id": hist_id,
    }

    # Remove None values to avoid polluting query string
    params = {k: v for k, v in params.items() if v is not None}
    api_key = current_api_key.get()
    headers = {"x-api-key": api_key}
    

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                url=f"{GALAXY_URL}/api/datasets/adopt_object",
                params=params,
                headers=headers,
            )
            
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Upstream service unreachable: {exc}",
            )

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=resp.status_code,
            detail=resp.text,
        )

    response = resp.json()
    
    if file_origin and unique_id:
        storage_key = f"{file_origin}_{unique_id}"
        try:
            await mongo_client.set(collection_name= CollectionNames.DATA_ADOPTATION.value, key = storage_key, value = response)
        except Exception as e:
            log.warning(f"Failed to store response: {e}")
    
    return LocalDatasetImportResponse(**response)

@router.post(
    "/index_file",
    response_model=IndexingResponse,
    status_code= HTTP_202_ACCEPTED,
    summary="Generate index file for endpoint"
)
async def generate_index_file(
    file_path: str = Query(..., description="Absolute path to the local file"),
    file_name: Optional[str] = Query(None),
    extension: Optional[str] = Query(None),
    file_origin: Optional[Literal["annotation", "hypothesis"]] = Query(None),
    unique_id: Optional[str] = Query(None, description="unique identifier for the file to be indexed. Preferably annotation/Hypothesis ID.")
):
    api_key = current_api_key.get()
    galaxy_client = GalaxyClient(user_api_key=api_key)
    username = galaxy_client.whoami

    # Adopt local file or fetch dataset id if data has already beed adopted into galaxy.
    try:
        galaxy_info = await proxy_adopt_local_file(
            file_path=file_path,
            file_name=file_name,
            extension=extension,
            file_origin=file_origin,
            unique_id = unique_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to adopt file: {str(e)}")

    output_indexer = OutputIndexer(
        username=username,
        galaxy_client=galaxy_client,
        cache = invocation_cache,
        mongo_client = mongo_client,
        ws_manager = ws_manager 
    )

    # Index and return index data information
    return await output_indexer.get_dataset_index(
        dataset_id = galaxy_info.dataset_id,
        unique_id = unique_id,
        username = username
    )
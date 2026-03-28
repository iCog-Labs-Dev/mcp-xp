from pydantic import BaseModel
from typing import List, Dict, Any, Optional

class CollectionCreate(BaseModel):
    """Request body for creating a new dataset collection from existing datasets."""
    name: str
    collection_type: str  # e.g., 'list', 'paired'
    element_identifiers: List[Dict[str, Any]]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "My Paired End Reads",
                    "collection_type": "paired",
                    "element_identifiers": [
                        {"name": "forward", "src": "hda", "id": "f2db41e1fa331b3e"},
                        {"name": "reverse", "src": "hda", "id": "d8d1822bdd153a8c"}
                    ]
                }
            ]
        }
    }

class CollectionResponse(BaseModel):
    """Response schema for a successfully created collection."""
    id: str
    name: str

class DatasetInfo(BaseModel):
    """Schema for a single dataset or collection within a history."""
    id: str
    name: str
    type: str # 'dataset' or 'dataset_collection'

class FileUploadResponse(BaseModel):
    """Response schema for a successful file upload."""
    id: str
    name: str
    message: str = "File uploaded successfully."

class HistoryContentsResponse(BaseModel):
    """Response schema for listing the contents of a history."""
    datasets: List[DatasetInfo]
    collections: List[DatasetInfo]
    
class LocalDatasetImportResponse(BaseModel):
    """Response schema for local file adoption."""
    dataset_id: str
    history_id: str

class IndexingResponse(BaseModel):
    status: str
    message: str
    dataset: Optional[DatasetInfo] = None
from fastapi import APIRouter
from app.api.endpoints import (
    dataset,
    histories,
    tools,
    workflows,
    invocation
    )

api_router = APIRouter()

# Include the histories router with a prefix and tags
api_router.include_router(
    histories.router,
    prefix="/histories",
    tags=["Histories & Data"]
)

# Include the workflows router with a prefix and tags
api_router.include_router(
    workflows.router,
    prefix="/workflows",
    tags=["Workflows"]
)

# Include the tools router with a prefix and tags
api_router.include_router(
    tools.router,
    prefix="/tools",
    tags=["Tools"]
)
# Include the invocation router with a prefix and tags
api_router.include_router(
    invocation.router,
    prefix="/invocation",
    tags=["Invocation"]
)

# Include the dataset router with a prefix and tags
api_router.include_router(
    dataset.router,
    prefix="/dataset",
    tags=["Date Adoption"]
)
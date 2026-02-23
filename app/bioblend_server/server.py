import os
from fastmcp import FastMCP
import logging
from typing import Optional
import asyncio
import httpx
from contextlib import asynccontextmanager

from bioblend.galaxy.client import ConnectionError as GalaxyConnectionError
from qdrant_client.models import PointStruct
from qdrant_client.http.exceptions import ApiException

from app.log_setup import configure_logging
from app.galaxy import GalaxyClient

from app.bioblend_server.mcp_middleware import JWTGalaxyKeyMiddleware
from app.bioblend_server.mcp_context import current_api_key_server

from app.bioblend_server.utils import (
    InformerResponse,
    DefaultTextResponses,
    get_llm_response,
    analyze_invocation,
    fetch_workflow_json_async
    )

from app.bioblend_server.background_runner import BackgroundIndexer
from app.bioblend_server.informer.informer import GalaxyInformer
from app.GX_integration.workflows.workflow_manager import WorkflowManager

configure_logging()
logger = logging.getLogger("fastmcp_bioblend_server")

if not os.environ.get("GALAXY_API_KEY") or not os.environ.get("QDRANT_HTTP_PORT") or not os.environ.get("CURRENT_LLM"):
    logger.warning("MCP server environment variables are not set.")

# Context Manager for the MCP server.

@asynccontextmanager
async def mcp_galaxy_lifespan(server: FastMCP):
    """ 
    Manages the lifecycle of the background indexer.
    Ensures it starts with the server and shuts down gracefully.
    """
    # 1. Initialize the worker and start loop
    indexer = BackgroundIndexer()
    loop_task = asyncio.create_task(indexer.run_loop())
    
    yield 
    
    # 3. Graceful Shutdown
    logger.info("Server shutting down, stopping background tasks...")
    loop_task.cancel()
    try:
        # Wait for the task to acknowledge cancellation
        await loop_task
    except asyncio.CancelledError:
        pass
    logger.info("Background tasks stopped cleanly.")
    
    
# ==================================== #
     ## MCP Server ##
# ==================================== #

bioblend_app = FastMCP(
                    name="GalaxyMcpAssistant",
                    instructions="""
                            Galaxy MCP assistant.
                            Provide information on Galaxy tools, datasets, workflows, and invocations.
                            Explain failures and recommend fixes.
                            Import recommended workflows when requested.
                            
                            """,
                    middleware=[JWTGalaxyKeyMiddleware()],
                    lifespan=mcp_galaxy_lifespan
                    )


# =============================================================================================================================================================== #
    ## Tool 2: Galaxy assitant recommendation tool, gives details on galaxy datasets, tools, and workflows both in and outside of the connected galaxy instance ##
# =============================================================================================================================================================== #

@bioblend_app.tool()
async def get_galaxy_information_tool(
    query: str,
    query_type: str,
    entity_id: str = None
) -> DefaultTextResponses:
    """
    Fetch detailed information on Galaxy tools, workflows, datasets, and invocations.

    This tool handles all information requests about Galaxy entities, based on
    the `query_type` (tool, workflow, dataset) and the user's `query`.
    Use `entity_id` only when the user's query explicitly includes an ID.

    Args:
        query: The user's query message that needs a response, accompanied by full and detailed contextual information.
        query_type: The type of Galaxy entity the query needs a response for, with one of three values: "tool", "dataset", or "workflow".
                    Select "workflow" for general workflow details and specific workflow invocation details.
        entity_id: Optional parameter. Provide this only when the user's query explicitly includes an ID,
                   allowing retrieval of information by that specific entity ID.

    Returns:
       DefaultTextResponses: A string containing the detailed Galaxy information and the response to the user's query. and a dict with action links of the information fetched.
        
    """
    logger.info(f"Calling get_galaxy_information with query='{query}', query_type='{query_type}', entity_id='{entity_id}'")
    try:
        # Get current user
        user_api_key = current_api_key_server.get()
        if user_api_key is None:
            raise ValueError("current user api-key is missing")        

        # Create galaxy instances
        galaxy_client = GalaxyClient(user_api_key)
        logger.info( f"current Galaxy MCP server user: {galaxy_client.whoami}")
        # Create GalaxyInformer object and execute informer
        informer = await GalaxyInformer.create(galaxy_client=galaxy_client, entity_type=query_type)
        response, actions  = await informer.get_entity_info(search_query = query, entity_id = entity_id)
        
        return InformerResponse(response = response, actions = actions)
    
    except GalaxyConnectionError as e:
        logger.error(f"Failed to connect to Galaxy: {e}")
        return f"Failed to connect to Galaxy: {e}"
    except Exception as e:
        logger.error(f"Error in get_galaxy_information_tool: {e}", exc_info=True)
        return f"An error occurred while fetching Galaxy information: {str(e)}"
    

# ========================================================================================================== #
    ## Tool 2: Invocaiton Analyzing tool, analyzes, summarizes and recommends fixes for failed invocaiton. ##
# ========================================================================================================== #

@bioblend_app.tool()
async def explain_galaxy_workflow_invocation(
    invocation_id: str,
    failure: bool
) -> DefaultTextResponses:
    """
    Generates a detailed explanation of a Galaxy workflow invocation.

    This function retrieves and analyzes metadata for a given Galaxy workflow invocation.
    It either summarizes successful outputs or provides diagnostic details for failed jobs,
    and suggest fixes for workflow invocation.

    Args:
        invocation_id (str): 
            The unique identifier of the Galaxy workflow invocation to analyze.
        failure (bool): 
            Indicates whether to focus on failed job diagnostics (`True`) or 
            output dataset summaries (`False`), if empty it defaults to false.

    Returns:
        DefaultTextResponses: A clear report of the workflow invocation results or a report explaining failure causes with actionable suggestions.
    """
    
    # Get current user
    user_api_key = current_api_key_server.get()
    if user_api_key is None:
        raise ValueError("current user api-key is missing")
    
    try:
        
        invocation_analysis: str = await analyze_invocation(invocation_id = invocation_id, user_api_key = user_api_key, failure=failure)
        
        if failure:
            logger.info("Loading failure explanation and suggestions for invocation.")
            invocation_prompt = f"""
                You are a Galaxy workflow expert.

                Analyze the following workflow invocation report.
                Identify why the workflow failed and suggest clear, actionable fixes.

                Report:
                {invocation_analysis}

                Respond with:
                - Root cause of failure(s)
                - Recommended fix or next step
                """
        else:
            logger.info("Loading summarized report for successful invocation.")
            invocation_prompt = f"""
                You are a Galaxy workflow expert.

                Summarize this successful workflow invocation report.

                Report:
                {invocation_analysis}

                Respond with:
                - What the workflow accomplished
                - Key output datasets or collections
                - Next logical steps for the user
                """
        try:
            response = await get_llm_response(message = invocation_prompt)
        except Exception as e:
            logger.error(f"Error preparing structured suggestions, returning full report. {e}")
            response = invocation_analysis
            
        return DefaultTextResponses(response = response)
    
    except GalaxyConnectionError as e:
        logger.error(f"Failed to connect to Galaxy: {e}")
        return DefaultTextResponses(response = "Failed to connect to Galaxy please try again.")
    except Exception as e:
        logger.error(f"Error caused whn trying to fetch invocation details: {e}")
        return DefaultTextResponses(response = "Error caused whn trying to fetch invocation details.")


# ============================================================ #
    ## Tool 3: Workflow Importing tool after recommendation. ##
# ============================================================ #

@bioblend_app.tool()
async def import_workflow_to_galaxy_instance(
    workflow_name: str
) -> DefaultTextResponses:
    # TODO: No Galaxy duplicate check add that.
    
    """
    Imports a Galaxy workflow from the IWC workflow repository, fetching the workflow JSON,
    and uploading it to the Galaxy instance. Handles tool installation and ensures the workflow is added to the user's list.

    Args:
        workflow_name (str): The Full and exact name of the workflow to import.

    Returns:
        DefaultTextResponses: A message indicating the import status or an error description.
    """
    try:
        
        from app.bioblend_server.informer.manager import InformerManager
        
        # Validate API key and initialize clients
        user_api_key: str = current_api_key_server.get()
        if not user_api_key:
            raise ValueError("User API key is not provided.")
        
        galaxy_client: GalaxyClient = GalaxyClient(user_api_key)
        username = galaxy_client.whoami
        workflow_manager: WorkflowManager = WorkflowManager(galaxy_client)
        qdrant_client: InformerManager = await InformerManager().create()

        # TODO: Fill the workflow collection name (has to be user-specific, so ...)
        workflow_collection_name: str = "generic_galaxy_workflow"

        # Step 1: Search for the workflow by name in metadata (synchronous call in thread pool)
        logger.info(f"Searching for workflow '{workflow_name}' in collection '{workflow_collection_name}'.")
        logger.info(f"current Galaxy MCP server user: {username}")
        hits = await qdrant_client.match_name_from_collection(
            workflow_collection_name=workflow_collection_name,
            workflow_name = workflow_name
            )

        if not hits or not hits[0]:
            logger.warning(f"Workflow '{workflow_name}' not found in collection '{workflow_collection_name}'")
            return f"Workflow '{workflow_name}' not found in available workflow collection for import."

        # Extract workflow download URL from point payload
        point: PointStruct = hits[0][0]
        workflow_url: Optional[str] = point.payload.get("raw_download_url")
        
        if not workflow_url:
            logger.error(f"No download link found for workflow '{workflow_name}'")
            return f"Couldn't import workflow '{workflow_name}'."

        # Fetch the workflow JSON
        logger.info(f"Fetching workflow JSON from IWC repository using URL: {workflow_url}")
        workflow_json: dict = await fetch_workflow_json_async(workflow_url)

        # TODO: Ensure that getting the workflow name is correct.
        ga_workflow_name: str = workflow_json.get("name", workflow_json.get("workflow_name", ""))
        if not ga_workflow_name:
            logger.error(f"Workflow JSON does not contain a 'name' field for '{workflow_name}'")
            raise ValueError(f"Workflow JSON does not contain a 'name' field for '{workflow_name}'.")

        # Background upload task
        logger.info(f"Initiating upload of workflow '{ga_workflow_name}'")
        
        asyncio.create_task(
            workflow_manager.upload_workflow(
                workflow_json=workflow_json
                )
            )

        response = (
            f"{ga_workflow_name} workflow is being imported,"
            "mssing tools are being checked and installed,"
            "and the workflow will be added to your workflow list shortly."
        )
        
        return DefaultTextResponses(response = response)
    
    except GalaxyConnectionError as e:
        logger.error(f"Failed to connect to Galaxy: {e}")
        return DefaultTextResponses(response = "Failed to connect to Galaxy.")
    
    except httpx.HTTPStatusError as http_err:
        logger.error(f"HTTP error fetching workflow from IWC repository: {str(http_err)}")
        return DefaultTextResponses(response = "HTTP error occurred while fetching workflow from IWC repository.")
    
    except httpx.RequestError as req_err:
        logger.error(f"Network error fetching workflow from IWC repository: {str(req_err)}")
        return DefaultTextResponses(response = "Request HTTP error occurred while fetching workflow from IWC repository.")
    
    except ApiException as qdrant_err:
        logger.error(f"Qdrant error occurred during workflow search: {str(qdrant_err)}")
        return DefaultTextResponses(response ="Error occured during workflow search.")
    
    except ValueError as val_err:
        logger.error(f"Validation error: {str(val_err)}")
        return DefaultTextResponses("An unexpected error occurred during workflow import.")
    
    except Exception as exc:
        logger.exception(f"Unexpected error during workflow import: {str(exc)}")
        return DefaultTextResponses("An unexpected error occurred during workflow import.")
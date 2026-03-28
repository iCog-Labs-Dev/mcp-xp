import asyncio
import logging

from app.galaxy import GalaxyClient
from app.api.socket_manager import SocketManager
from app.GX_integration.tool_manager import ToolManager
from app.orchestration.invocation_cache import InvocationCache
from app.persistence import MongoStore
from app.enumerations import CollectionNames
from app.GX_integration.invocations.utils import (
    FASTAIndexerTools,
    VCFIndexerTools,
    BAMIndexerTools,
    GTFIndexerTools
)

from app.api.schemas import dataset
from app.enumerations import IndexingResponses, NumericLimits, SocketMessageEvent, SocketMessageType
from app.GX_integration.invocations.utils import log_task_error
from app.exceptions import InternalServerErrorException

class OutputIndexer:
    """support dataset indexing for galaxy workflow invocation output datasets for visualization purposes."""
    
    def __init__(self, username: str, galaxy_client: GalaxyClient, cache: InvocationCache, mongo_client: MongoStore, ws_manager: SocketManager):

        self.gi = galaxy_client.gi_client
        self.username = username
        self.cache = cache
        self.ws_manager = ws_manager
        self.tool_manager = ToolManager(galaxy_client = galaxy_client)
        self.mongo_client = mongo_client
        
        self.log = logging.getLogger(__class__.__name__)
        
        # Datasets indexing counter variables.
        self.index_count = 0
        self.total_index = 0      
    
    async def _structure_indexed_data(self, history_id: str, dataset_name: str, index_outputs: list[dict]):
        """structure indexed result"""
        
        self.log.debug(f"Structuring index data for history_id={history_id}, "
                   f"target_name={dataset_name}, outputs={len(index_outputs)}")
        structured_index = []
        
        for index in index_outputs:
            # Update to approapriate name for index file.
            try:
                self.log.debug(f"Renaming index dataset id={index.get('id')} -> {dataset_name}")
                await asyncio.to_thread(
                    self.gi.histories.update_dataset,
                    history_id=history_id,
                    dataset_id=index.get("id"),
                    name=dataset_name
                )
            except Exception as e:
                self.log.error(f"Failed to rename index dataset id={index.get('id')}: {e}")
                raise
            
            # even though it is most likely a single file, structure it into a list so as to not break code.
            structured_index.append(
            {
                "type": "dataset",
                "id": index.get("id"),
                "name": dataset_name,
                "visible": index.get("visible"),
                "file_path": index.get('file_name'),
                "peek": index.get('peek'),
                "data_type": index.get('extension', index.get('file_ext', 'unknown')),
                "is_intermediate": not index.get("visible")
                }
            )
            
        self.log.debug(f"Index structuring complete for {dataset_name} ({len(structured_index)} files).")
        return structured_index
       
       
    async def index_fasta(self, history_id: str, dataset: tuple[str, str]):
        """ Index fasta files to generate a fai index.

        Args:
            dataset (tuple): fasta dataset name and id.
        """
        name, dataset_id = dataset
        tool_id = FASTAIndexerTools.INDEXER_TOOL.value
        tool_input = {"input" : {"src": "hda", "id": dataset_id}}
        
        self.log.debug(f"Starting FASTA indexing: history={history_id}, dataset={dataset_id}, tool={tool_id}")
        try:
            result = await self.tool_manager.run(
                tool_id= tool_id,
                history_id = history_id,
                inputs = tool_input
                )
        except Exception as e:
            self.log.error(f"FASTA indexing failed for dataset id={dataset_id}: {e}")
            return[]
        
        self.log.debug(f"FASTA indexing tool completed: produced={len(result['dataset'])} output(s)")
        
        self.index_count += 1
        self.log.debug(f"fasta indexing complete for dataset id: {dataset_id} - {self.index_count}/{self.total_index}")
        
        return await self._structure_indexed_data(
            history_id = history_id,
            dataset_name = f"{name}_fai_index", 
            index_outputs = result["dataset"]
            )
    
    async def index_vcf(self, history_id: str, dataset: tuple[str, str]):
        """ Index vcf files to generate a tabix index.

        Args:
            dataset (tuple): vcf dataset name and id.
        """
        name, dataset_id = dataset

        # Execute 2 tools consecutively as kind of like workflows.
        tool_id1 = VCFIndexerTools.COMPRESSER_TOOL.value
        tool_id2 = VCFIndexerTools.INDEXER_TOOL.value
        
        self.log.debug(f"Starting VCF compression: history={history_id}, dataset={dataset_id}, tool={tool_id1}")
        tool1_input = {"input1": {"src": "hda", "id": dataset_id}}
        
        try:   
            result_1 = await self.tool_manager.run(
                tool_id = tool_id1, 
                history_id = history_id, 
                inputs = tool1_input
                )
        except Exception as e:
            self.log.error(f"VCF indexing failed for dataset id={dataset_id}: {e}")
            return []
        
        # extract dataset id of the compressed dataset.
        result_1_id = result_1.get("dataset")[0].get("id")
        self.log.debug(f"VCF compressed: new_dataset_id={result_1_id}")
        
        tool2_input = {"input1": {"src": "hda", "id" : result_1_id }}
        
        self.log.debug(f"Starting tabix indexing: tool={tool_id2}, input={result_1_id}")
        try:
            result = await self.tool_manager.run(
                tool_id = tool_id2,
                history_id = history_id,
                inputs = tool2_input
            )
        except Exception as e:
            self.log.error(f"VCF indexing failed for dataset id={dataset_id}: {e}")
            return []
        
        self.index_count += 1
        self.log.debug(f"vcf indexing complete for dataset id: {dataset_id} - {self.index_count}/{self.total_index}")
        return await self._structure_indexed_data(
            history_id = history_id,
            dataset_name = f"{name}_tabix_index", 
            index_outputs = result["dataset"]
            )
    
    
    async def index_bam(self, history_id: str, dataset: tuple[str, str]):
        """ Index vcf files to return a bai index.

        Args:
            dataset (tuple): bam dataset name and id.
        """
        name, dataset_id = dataset

        tool_id = BAMIndexerTools.INDEXER_TOOL.value
        tool_input = {"input1" : {"src": "hda", "id": dataset_id}}
        self.log.debug(f"Starting BAM indexing: history={history_id}, dataset={dataset_id}, tool={tool_id}")
        try:
            result = await self.tool_manager.run(
                tool_id= tool_id,
                history_id = history_id,
                inputs = tool_input
                )
        except Exception as e:
            self.log.error(f"BAM indexing failed for dataset id={dataset_id}: {e}")
            return []       
         
        self.index_count += 1
        self.log.debug(f"bam indexing complete for dataset id: {dataset_id} - {self.index_count}/{self.total_index}")
        return await self._structure_indexed_data(
            history_id = history_id,
            dataset_name = f"{name}_bai_index", 
            index_outputs = result["dataset"]
            )

    async def index_gtf(self, history_id: str, dataset: tuple[str, str]):
        """ Index gtf files by compressing and then indexing them.

        Args:
            dataset (tuple): gtf dataset name and id.
        """
        name, dataset_id = dataset

        # Execute 3 tools consecutively as kind of like workflows.
        tool_id1 = GTFIndexerTools.COMPRESSER_TOOL.value
        tool_id2 = GTFIndexerTools.INDEXER_TOOL.value
        
        self.log.debug(f"Starting GTF compression: history={history_id}, dataset={dataset_id}, tool={tool_id1}")
        tool1_input = {"input1": {"src": "hda", "id": dataset_id}}
        
        try:   
            result_1 = await self.tool_manager.run(
                tool_id = tool_id1, 
                history_id = history_id, 
                inputs = tool1_input
                )
        except Exception as e:
            self.log.error(f"GTF indexing failed for dataset id={dataset_id}: {e}")
            return []
        
        # extract dataset id of the compressed dataset.
        result_1_id = result_1.get("dataset")[0].get("id")
        self.log.debug(f"GTF compressed: new_dataset_id={result_1_id}")
        
        tool2_input = {
            "input1": {"src": "hda", "id": dataset_id},
            "bgzip": {"src": "hda", "id" : result_1_id }
            }
        
        self.log.debug(f"Starting tabix indexing: tool={tool_id2}, input={result_1_id}, {dataset_id}")
        try:
            result = await self.tool_manager.run(
                tool_id = tool_id2,
                history_id = history_id,
                inputs = tool2_input
            )
        except Exception as e:
            self.log.error(f"GTF indexing failed for dataset id={dataset_id}: {e}")
            return []
        
        self.index_count += 1
        self.log.debug(f"GTF indexing complete for dataset id: {dataset_id} - {self.index_count}/{self.total_index}")
        return await self._structure_indexed_data(
            history_id = history_id,
            dataset_name = f"{name}_tabix_index", 
            index_outputs = result["dataset"]
            )
    
    #TODO: Add more dataset indexing functions that JBrowse supports.
    
    async def index_datasets_and_register(self, invocation_result: dict):
        """
        This function cleans indexes outputs of an invocaiton if they are a [fasta, vcf, bam, gtf] file,
        and then registers them to the output list
        """
        
        # create dict to collect datasets to be indexed. NOTE: Add as needed, for now we support fasta, vcf and bam files. 
        index_datasets = {
                "fasta" : [],
                "vcf": [],
                "bam": [],
                "gtf": []
                }
        
        # extract invocation and history id of the invocation.
        invocation_id = invocation_result.get("invocation_id", None)
        history_id = invocation_result.get("history_id")
        
        if invocation_id:
           
            invocation_outputs: list[dict]= invocation_result.get("result", [])
            
            # collect each dataset within the invocation output to sort out which indexing the datasets need.
            for output in invocation_outputs:
                output_type = output.get("type")
                
                if output_type == "collection":
                    collection_elements: list[dict] = output.get("elements", [])
                    for element in collection_elements:
                        element_type = element.get("data_type", "unknown")
                        element_id = element.get("id")
                        element_name = element.get("name")
                        if element_type in index_datasets.keys() and element_id and element_name:
                            index_datasets[element_type].append((element_name, element_id))
                            
                elif output_type == "dataset":
                    element_type = output.get("data_type")
                    element_id = output.get("id")
                    element_name = output.get("name")
                    if element_type in index_datasets.keys() and element_id and element_name:
                            index_datasets[element_type].append((element_name, element_id))
            
            self.log.debug(f"Collected datasets for indexing: {index_datasets}")
            
            # start of with fasta indexing: NOTE: Add indexer function and task as necessary here.
            indexing_task = [self.index_fasta(history_id, dataset) for dataset in index_datasets.get("fasta", [])]
            indexing_task.extend([self.index_vcf(history_id, dataset) for dataset in index_datasets.get("vcf", [])])
            indexing_task.extend([self.index_bam(history_id, dataset) for dataset in index_datasets.get("bam", [])])
            indexing_task.extend([self.index_gtf(history_id, dataset) for dataset in index_datasets.get("gtf", [])])
            
            # count total indxes that are to be used.
            self.total_index = len(indexing_task)

            if self.total_index > 0:
                
                self.log.info(f"Total datasets to index: {self.total_index}")
                self.log.debug(f"Beginning dataset indexing for invocation_id = {invocation_id} in history_id = {history_id}")
                
                completed = 0

                # Notify start
                if self.ws_manager:
                    
                    await self.ws_manager.broadcast(
                        event=SocketMessageEvent.output_index.value,
                        data={
                            "type": SocketMessageType.INDEX_START.value,
                            "payload":{ "message" : f"Starting indexing of {self.total_index} datasets for invocation {invocation_id}"},
                        },
                        tracker_id=invocation_id
                    )
                    
                # gather index results; Run tasks concurrently, process results as they finish
                for coro in asyncio.as_completed(indexing_task):

                    try:
                        indexed_result = await coro  # list of the structured index data

                        # append to invocation output
                        invocation_outputs.extend(indexed_result)

                        # update final object
                        invocation_result["result"] = invocation_outputs

                        # store partial progress to cache
                        await self.cache.set_invocation_result(
                            username=self.username,
                            invocation_id=invocation_id,
                            result=invocation_result
                        )
                        
                        if self.ws_manager:
                            
                            completed += 1
                            await self.ws_manager.broadcast(
                                event=SocketMessageEvent.output_index.value,
                                data={
                                    "type": SocketMessageType.INDEX_UPDATE.value,
                                    "payload":{
                                        "message": f"Dataset indexing progress: {completed}/{self.total_index}",
                                        "progress": f"{completed}/{self.total_index}"
                                        }
                                },
                                tracker_id=invocation_id
                            )
                        self.log.debug("Incremental index result saved to cache.")

                    except Exception as e:
                        self.log.error(f"Indexing subtask failed: {e}")
                
                self.log.debug(F'Invocaion with id {invocation_id} as completed execution and indexing, persisting final results.')
                
                if self.ws_manager:
                    
                    await self.ws_manager.broadcast(
                        event=SocketMessageEvent.output_index.value,
                        data={
                            "type": SocketMessageType.INDEX_FINISH.value,
                            "payload": {
                                "message": f"Completed indexing datasets for invocation {invocation_id}."
                                },
                        },
                        tracker_id=invocation_id
                    )
                    
                await self.mongo_client.set(
                    collection_name= CollectionNames.INVOCATION_RESULTS.value, 
                    key = f"{self.username}:{invocation_id}", 
                    value = invocation_result
                )
                
                extracted_outputs = []
                
                for output in invocation_outputs:
                    dataset_type = output.get("type")
                    if  dataset_type == "dataset":
                        extracted_outputs.append({
                            "id": output.get("id"),
                            "name": output.get("name"),
                            "type": output.get("data_type")
                        })
                        
                    elif dataset_type == "collection":
                        collection_elements: list[dict] = output.get("elements", [])
                        for element in collection_elements:
                            extracted_outputs.append({
                                "id":element.get("id"),
                                "name":  element.get("name"),
                                "type": element.get("data_type", "unknown")
                            })
                        
                self.log.debug("output information extracted and stored.")

                await self.mongo_client.update_value_element(
                    collection_name = CollectionNames.INVOCATION_LISTS.value,
                    key = self.username,
                    match_field = "id",
                    match_value = invocation_id,
                    update_field = "outputs",
                    new_value = extracted_outputs
                )
                
                self.log.info(f"Worklfow invocaiton with id {invocation_id} has completed full execution including output indexing. results have been stored.")           

    async def index_single_dataset(self, dataset_id: str):
            """
            Index a single Galaxy dataset and return the indexed output.
            """
            try:
                # Fetch metadata
                dataset = await asyncio.to_thread(self.gi.datasets.show_dataset, dataset_id=dataset_id)
            except Exception as e:
                self.log.error(f"Failed to fetch dataset metadata: {e}")
                return None

            dataset_name = dataset.get("name")
            data_type = dataset.get("data_type", "unknown")
            history_id = dataset.get("history_id")

            if not history_id or not dataset_name:
                self.log.error(f"Invalid dataset metadata for dataset_id={dataset_id}")
                return None

            dataset_tuple = (dataset_name, dataset_id)
            result = []
            
            # Use elif to ensure mutual exclusivity
            if data_type == "fasta":
                result = await self.index_fasta(history_id, dataset_tuple)
            elif data_type == "vcf":
                result = await self.index_vcf(history_id, dataset_tuple)
            elif data_type == "bam":
                result = await self.index_bam(history_id, dataset_tuple)
            elif data_type == "gtf":
                result = await self.index_gtf(history_id, dataset_tuple)
            else:
                self.log.warning(
                    f"No indexer available for dataset_id={dataset_id}, type={data_type}"
                )
                return None # Explicit None for unsupported types

            # Result is likely a list of dicts based on your other methods
            if result and len(result) > 0:
                return result[0]
            
            return None
        
    async def _run_dataset_index_background(
        self,
        dataset_id: str,
        username: str,
        unique_id: str
    ):
        """Background worker – moved here so the service owns the whole flow"""
        self.log.info(f"Background indexing started for dataset {dataset_id}")
        try:
            indexed_result = await self.index_single_dataset(dataset_id)

            if indexed_result:
                
                response = {
                        "status": IndexingResponses.COMPLETE_STATUS.value,
                        "message": IndexingResponses.COMPLETE_STATUS.value,
                        "dataset": {
                            "id": indexed_result["id"],
                            "name": indexed_result["name"],
                            "type": indexed_result["data_type"],
                        }
                    }
                # Persist with the same pattern as invocation results (per-item key)
                await self.mongo_client.set_element(
                    collection_name=CollectionNames.DATA_INDEXES.value,
                    key=username,
                    field=unique_id,
                    value=response
                )

                # Cache it
                await self.cache.set_dataset_index(username = username, unique_id = unique_id, index_data = response)

                # Notify user
                if self.ws_manager:
                    
                    await self.ws_manager.broadcast(
                        event= SocketMessageEvent.output_index.value,
                        data={
                            "type": SocketMessageType.INDEX_FINISH.value,
                            "payload": {"message": "Dataset indexing complete"}
                        },
                        tracker_id = unique_id
                    )
                self.log.info(f"Indexing complete and persisted for {dataset_id}")
                
            else:
                raise RuntimeError("Indexing produced no result")

        except Exception as e:
            self.log.error(f"Background indexing failed: {e}")
            await self.cache.delete_dataset_index(username, unique_id)
            
            if self.ws_manager:
                
                await self.ws_manager.broadcast(
                    event=SocketMessageEvent.output_index.value,
                    data={
                        "type": SocketMessageType.INDEX_FAIL.value, 
                        "payload": {"message": "Dataset indexing Failed."}
                        },
                    tracker_id=unique_id
                )
                
    async def get_dataset_index(
        self,
        dataset_id: str,
        unique_id: str,
        username: str,
    ) -> dataset.IndexingResponse:
        """Exactly the same pattern as get_invocation_result: check cache → mongo → start background if missing"""
        try:
            # 1. Cache hit?
            cached_index = await self.cache.get_dataset_index(username, unique_id)
            if cached_index:
                self.log.info("Dataset index served from cache")
                return dataset.IndexingResponse(**cached_index)

            # 2. Mongo hit?
            stored_index = await self.mongo_client.get_element(
                collection_name=CollectionNames.DATA_INDEXES.value,
                key= username,
                field = unique_id
            )
            if stored_index:
                await self.cache.set_dataset_index(username, unique_id, stored_index)
                self.log.info("Dataset index served from database")
                return dataset.IndexingResponse(**stored_index)
            
            if self.ws_manager:
                
                await self.ws_manager.broadcast(
                    event = SocketMessageEvent.output_index.value,
                    data = {
                        "event": SocketMessageType.INDEX_START.value, # TODO
                        "payload": {"message" :"Starting Dataset indexing."}
                    },
                    tracker_id = unique_id
                )

            # Optional short-lived "processing" marker
            result = {
                "status" : IndexingResponses.PENDING_STATUS.value,
                "message" : IndexingResponses.PENDING_MESSAGE.value,
                "dataset" : None
            }
            await self.cache.set_dataset_index(
                username = username, unique_id = unique_id, index_data = result
            )

            tracking_key = f"{username}_{dataset_id}_{unique_id}"
            overflowcheck = await asyncio.to_thread(
                    self.cache.redis.set, tracking_key, "1", ex=NumericLimits.BACKGROUND_INDEX_TRACK.value, nx=True
                )
            if overflowcheck:
                background_task = asyncio.create_task(
                    self._run_dataset_index_background(
                        dataset_id=dataset_id,
                        username=username,
                        unique_id=unique_id
                    )
                )
                
                background_task.add_done_callback(lambda t: log_task_error(t, task_name="dataset_indexing"))
                self.log.info(f"Background indexing started for dataset {dataset_id}")
                
            else:
                self.log.info(f"Indexing already in progress for dataset {dataset_id}, skipping background task")
            
            return dataset.IndexingResponse(**result)

        except Exception as e:
            self.log.error(f"Error in get_dataset_index: {e}")
            raise InternalServerErrorException("Failed to process dataset index")
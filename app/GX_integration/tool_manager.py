from __future__ import annotations
import logging
import time
from typing import Dict, List, Any
import os
from dotenv import load_dotenv
from rapidfuzz import process, fuzz
import httpx
import re
import asyncio

load_dotenv()

from sys import path
path.append('.')

from starlette.datastructures import FormData
from app.galaxy import GalaxyClient
from bioblend.galaxy.objects.wrappers import Job, History, Tool

from app.GX_integration.form_generator import ToolFormGenerator
from app.GX_integration.data_manager import DataManager
from app.api.socket_manager import SocketManager
from app.enumerations import (
    SocketMessageEvent,
    SocketMessageType,
    NumericLimits
    )

class ToolManager:
    """
    Minimal, reusable tool-runner for Galaxy.
    Responsibilities
    ----------------
    - Discover tools
    - Build correct input payloads and generate html form for execution UI
    - Execute
    - Wait until done
    - Return outputs & diagnostics
    """

    def __init__(self, galaxy_client: GalaxyClient):
        self.galaxy_client = galaxy_client
        self.gi_object=self.galaxy_client.gi_object
        self.log = logging.getLogger(self.__class__.__name__)
        self.data_manager = DataManager(galaxy_client= self.galaxy_client)
        self.poll_interval = 2

    def get_tool_by_name(self, name: str, score_cutoff: int = 70) -> Tool| None:
        """Get workflow by its name (fuzzy match)."""
        tool_list = self.gi_object.gi.tools.get_tools()
        name_to_tool = {tool["name"]: tool for tool in tool_list}

        match = process.extractOne(
            name,
            name_to_tool.keys(),          # list of names
            scorer=fuzz.partial_ratio,
            score_cutoff=score_cutoff
        )

        if match is None:               # nothing above the cutoff
            return None

        tool_dict = name_to_tool[match[0]]
        return self.gi_object.tools.get(tool_dict["id"])
    
    def get_tool_by_id(self, id: str) -> Tool | None:
        """
        Return the Tool instance for the given Galaxy tool ID, or None if it does not exist.
        """
        try:
            return self.gi_object.tools.get(id)
        except Exception as e:
            self.log.info(f"Error occured: {e}")    
            return None
        
    def get_tool_io (self, tool_id):
        """Get tool i/o details"""
        tool= self.gi_object.gi.tools.show_tool(tool_id=tool_id, io_details=True)
        return tool['inputs']
    
    async def get_tool_xml(self, tool_id: str) -> str:
            """Retrieve tool XML to build dynamic HTML form for tool execution by making a direct api call."""
            try:
                api_key = self.galaxy_client.user_api_key
                base_url = self.galaxy_client.galaxy_url
                
                if not base_url or not api_key:
                    raise ValueError("Galaxy environment variables are not set")

                _url = f"{base_url.rstrip('/')}/api/tools/{tool_id}/raw_tool_source"
                _headers = {"x-api-key":api_key}

                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(url=_url, headers=_headers)
                    response.raise_for_status()
                    return response.text  # Return raw XML, not JSON
            except Exception as e:
                self.log.error(f"Error getting tool XML: {e}")
                return None
            
    def remove_dash(self, param_dict: Dict):
        """Remove the "--" sign from galaxy tool arguments to get the correct input parameter key"""
        return {re.sub(r'^--', '', k): v for k, v in param_dict.items()}
   
    def transform_form_data(self, form_data: FormData, tool_id: str):
        """
        Transform HTML form data into bioblend-compatible input structure.
        
        Args:
            form_data: MultiDict from FastAPI form submission
            tool_id: Galaxy tool ID
            tool_manager: ToolManager instance
            
        Returns:
            dict: Bioblend-compatible input structure
        """       
        # Get tool details to understand input structure
        tool_details = self.gi_object.gi.tools.show_tool(tool_id, io_details=True)
        inputs_structure = tool_details['inputs']
        # from pprint import pprint
        # pprint(f"input strucutre: {inputs_structure}")
        # Convert form data to regular dict, handling multiple values
        form_dict = {}

        for key in form_data:
            value = form_data[key]
            if isinstance(value, list):
                form_dict[key] = value[-1]
            else:
                form_dict[key] = value

        
        # Process boolean fields (checkboxes)
        for key, value in form_dict.items():
            if value == 'true':
                form_dict[key] = True
            elif value == 'false':
                form_dict[key] = False
        
        # Build bioblend inputs structure
        bioblend_inputs = {}
        
        # Process each input parameter
        for param in inputs_structure:
            param_name = param.get("argument") or param.get('name')
            param_type = param.get('type')
            model_class = param.get('model_class')
            
            # Skip if not in form data (optional parameter)
            if param_name not in form_dict:
                continue
                
            # Handle different parameter types
            if model_class == 'DataToolParameter':
                # Dataset reference
                bioblend_inputs[param_name] = {
                    'src': 'hda',
                    'id': form_dict[param_name]
                }
            elif model_class == 'DataCollectionToolParameter':
                # Dataset collection reference
                bioblend_inputs[param_name] = {
                    'src': 'hdca',
                    'id': form_dict[param_name]
                }
            elif model_class == 'Conditional':
                # Conditional parameter
                bioblend_inputs[param_name] = self.process_conditional_param(
                    param, form_dict, param_name
                )
            elif model_class == 'Repeat':
                # Repeat parameter
                bioblend_inputs[param_name] = self.process_repeat_param(
                    param, form_dict, param_name
                )
            else:
                # Simple parameter types
                if param_type in ('integer', 'float'):
                    try:
                        bioblend_inputs[param_name] = float(form_dict[param_name]) if param_type == 'float' else int(form_dict[param_name])
                    except (ValueError, TypeError):
                        # Use default if conversion fails
                        bioblend_inputs[param_name] = param.get('value', 0)
                else:
                    bioblend_inputs[param_name] = form_dict[param_name]
        
        # Remove the dash("--") from the the input dictionary keys 
        bioblend_inputs = self.remove_dash(bioblend_inputs)

        return bioblend_inputs

    def process_conditional_param(self, param, form_dict, param_name):
        """
        Process conditional parameters from form data.
        
        Args:
            param: Conditional parameter definition
            form_dict: Form data dictionary
            param_name: Name of the conditional parameter
            
        Returns:
            dict: Conditional parameter structure
        """
        conditional = {}
        
        # Get test parameter
        test_param = param.get('test_param')
        test_name = test_param.get('argument') or test_param.get('name')
        test_value = form_dict.get(f"{param_name}|{test_name}", test_param.get('value'))
        
        # Add test parameter to conditional
        conditional[test_name] = test_value
        
        # Find the active case
        active_case = None
        for case in param.get('cases', []):
            if case.get('value') == test_value:
                active_case = case
                break
        
        # Default to first case if no match found
        if active_case is None:
            active_case = param.get('cases', [{}])[0]
        
        # Process inputs from the active case
        for case_param in active_case.get('inputs', []):
            case_param_name = case_param.get('name')
            full_name = f"{param_name}|{test_value}|{case_param_name}"
            
            if full_name in form_dict:
                # Handle different case parameter types
                if case_param.get('model_class') == 'DataToolParameter':
                    conditional[case_param_name] = {
                        'src': 'hda',
                        'id': form_dict[full_name]
                    }
                elif case_param.get('model_class') == 'DataCollectionToolParameter':
                    conditional[case_param_name] = {
                        'src': 'hdca',
                        'id': form_dict[full_name]
                    }
                else:
                    conditional[case_param_name] = form_dict[full_name]

        # Remove the dash("--") from the the input dictionary keys 
        conditional = self.remove_dash(conditional)
        return conditional

    def process_repeat_param(self, param, form_dict, param_name):
        """
        Process repeat parameters from form data.
        
        Args:
            param: Repeat parameter definition
            form_dict: Form data dictionary
            param_name: Name of the repeat parameter
            
        Returns:
            list: List of repeat instances
        """
        repeats = []
        
        # Find all repeat instances in form data
        repeat_instances = {}
        for key, value in form_dict.items():
            if key.startswith(f"{param_name}|"):
                parts = key.split('|')
                if len(parts) >= 3:
                    instance_id = parts[1]
                    param_key = parts[2]
                    
                    if instance_id not in repeat_instances:
                        repeat_instances[instance_id] = {}
                    repeat_instances[instance_id][param_key] = value
        
        # Convert instances to bioblend format
        for instance_id, instance_data in repeat_instances.items():
            instance = {}
            
            for param_def in param.get('inputs', []):
                param_name = param_def.get('argument') or param_def.get('name')
                if param_name in instance_data:
                    # Handle different parameter types
                    if param_def.get('model_class') == 'DataToolParameter':
                        instance[param_name] = {
                            'src': 'hda',
                            'id': instance_data[param_name]
                        }
                    elif param_def.get('model_class') == 'DataCollectionToolParameter':
                        instance[param_name] = {
                            'src': 'hdca',
                            'id': instance_data[param_name]
                        }
                    else:
                        instance[param_name] = instance_data[param_name]
            
            repeats.append(instance)

        # Remove the dash("--") from the the input dictionary keys 
        repeats = [self.remove_dash(inst) for inst in repeats]
        return repeats

        # Build tool html form from the tools xml.
    async def build_html_form(self, tool: Tool, history: History) -> str | None:
        """
        Builds a dynamic HTML form from the tool's XML definition.
        """
        xml_str = await self.get_tool_xml(tool_id= tool.id) # Get tool xml 
        if not xml_str:
            self.log.error(f"Could not retrieve XML for tool {tool.id}")
            raise

        # We only want active, visible datasets
        generator = ToolFormGenerator(xml_str, self.data_manager, tool, history)
        html =  generator.build_html()
        return html
    
    async def wait(self, 
            job_id: str,
            ws_manager: SocketManager,
            tracker_id: str,
            initial_wait: int,
            base_extension: int
            )-> Job:
        """Waits for a Galaxy job to finish, with dynamic timeout and progress tracking."""

        previous_state = None
        start_time = time.time()
        deadline = start_time + initial_wait
        max_extension = initial_wait // 2
        polling_interval = NumericLimits.TOOL_EXECUTION_POLL.value

        while True:
            job: Job = await asyncio.to_thread(
                self.gi_object.jobs.get, id_ = job_id
                )
            current_state = job.state

            if current_state != previous_state:
                self.log.debug(f"Job {job_id} transitioned to {current_state}")
                
                if ws_manager:
                    await ws_manager.broadcast(
                        event = SocketMessageEvent.tool_execute.value,
                        data = {"type": SocketMessageType.JOB_UPDATE.value,
                            "payload" : {
                                "job_id": job_id,
                                "status" : current_state
                            }
                        },
                        tracker_id = tracker_id
                        )
                    
                previous_state = current_state
                # Extend deadline slightly when progress is detected
                extension = min(base_extension, max_extension)
                deadline = max(deadline + extension, start_time + initial_wait)

            if current_state == "ok":
                self.log.debug("Job execution complete.")
                if ws_manager:
                    await ws_manager.broadcast(
                        event = SocketMessageEvent.tool_execute.value,
                        data = {
                            "type": SocketMessageType.JOB_COMPLETE.value,
                            "payload" : {"message": "Job execution complete." }
                        },
                        tracker_id=tracker_id
                        )
                     
                break

            if current_state in {'error', 'cancelled'}:
                
                if ws_manager:
                    await ws_manager.broadcast(
                        event = SocketMessageEvent.tool_execute.value,
                        data = {
                            "type": SocketMessageType.JOB_FAILURE.value,
                            "payload" : {"message": "Job execution cancelled or failed." }
                        },
                        tracker_id=tracker_id
                        )
                    
                break

            if time.time() > deadline:
                self.log.error(f"Job {job_id} timed out after {int(time.time() - start_time)} seconds.")
                try:
                    await asyncio.to_thread(
                        self.gi_object.gi.jobs.cancel_job, job_id = job_id
                        )
                    
                    self.log.warning(f"Job {job_id} cancelled due to timeout.")
                    
                    if ws_manager:
                        await ws_manager.broadcast(
                            event = SocketMessageEvent.tool_execute.value,
                            data = {
                            "type": SocketMessageType.JOB_FAILURE.value,
                            "payload" : {"message": "Job cancelled due to timeout." }
                            },
                            tracker_id=tracker_id
                        )

                except Exception as e:
                    self.log.warning(f"Failed to cancel job {job_id}: {e}")

                    if ws_manager:
                        await ws_manager.broadcast(
                            event = SocketMessageEvent.tool_execute.value,
                            data = {
                            "type": SocketMessageType.JOB_FAILURE.value,
                            "payload" : {"message": f"Job execution failed: {e}"}
                            },
                            tracker_id=tracker_id
                        )

                break
            
            await asyncio.sleep(polling_interval)  # polling interval

        return job

    # Internal helpers
    def _build_payload(
        self,
        tool_id: str,
        history_id: str,
        inputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Use Galaxy's `build` endpoint so we do not have to reverse-engineer
        the parameter tree.

        """
        # The low-level client is needed for the build endpoint
        payload = self.gi_object.gi.tools.build(
            tool_id=tool_id,
            inputs=inputs,
            history_id=history_id
        )
        return payload['state_inputs']

    async def _make_result(self, job: Job, datasets: list[dict]) -> Dict[str, Any]:

        job_details= await asyncio.to_thread(
            self.gi_object.gi.jobs.show_job, job_id= job.id, full_details=True
            )
        message = {
            "stdout": job_details.get("stdout", None),
            "stderr": job_details.get("stderr", None),
            "error": job_details.get("error_message", None) if job.state == "error" else None,
        }

        return {
            "dataset": datasets,
            "state": job.state,
            "message": {k: v for k, v in message.items() if v},  # Ignore empty ones
        }
    
    # Core executor
    async def run( 
        self,
        tool_id: str,
        history_id: str,
        inputs: Dict[str, Any],
        tracker_id:str = None,
        ws_manager: SocketManager = None
        ):
        """
        Run a tool and return a structured result.
        """
        inputs = inputs or {}

        # Build the tool payload 
        # (validation step to convert name: value pairs from the io details into state input)
        tool_payload = await asyncio.to_thread(
            self._build_payload, tool_id=tool_id, history_id = history_id, inputs = inputs
            )
        
        #  Run
        tool_execution= await asyncio.to_thread(
            self.gi_object.gi.tools.run_tool, tool_id= tool_id, history_id = history_id, tool_inputs = tool_payload
            )

        # The job id
        job_id = tool_execution['jobs'][0]['id']
        outputs = tool_execution['outputs']

        self.log.debug(f"Started job {job_id} for tool {tool_id!r}")
        
        if ws_manager:
            await ws_manager.broadcast(
                event = SocketMessageEvent.tool_execute.value,
                data = {
                    "type": SocketMessageType.TOOL_EXECUTE.value,
                    "payload": {"message": "Execution started."}
                },
                tracker_id = tracker_id
            )

        # Track job until it complete before making result
        job= await self.wait(
            job_id = job_id,
            ws_manager = ws_manager,
            tracker_id = tracker_id,
            initial_wait = NumericLimits.TOOL_EXECUTION_INITIAL_WAIT.value,
            base_extension = NumericLimits.TOOL_EXECUTION_EXTENSION.value
            )

        output_datasets: List[dict] = []

        for output in outputs:
            dataset =  await asyncio.to_thread(
                self.gi_object.gi.datasets.show_dataset, dataset_id = output['id']
                )
            output_datasets.append(dataset)

        final_result = await self._make_result(job, output_datasets)

        return final_result
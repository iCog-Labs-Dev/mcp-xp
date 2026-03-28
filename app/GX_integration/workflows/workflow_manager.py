from __future__ import annotations

import logging
from typing import List, Tuple, Optional, Literal, Dict
import asyncio

from rapidfuzz import process, fuzz
from dotenv import load_dotenv
import xml.etree.ElementTree as ET

load_dotenv()

from sys import path
path.append('.')

from bioblend.galaxy.objects.wrappers import Workflow, Invocation, History
from bioblend.galaxy.toolshed import ToolShedClient

from app.galaxy import GalaxyClient
from app.GX_integration.tool_manager import ToolManager
from app.GX_integration.form_generator import WorkflowFormGenerator
from app.api.socket_manager import SocketManager

from app.enumerations import InvocationStates

from app.GX_integration.workflows.worklfow_installer import WorkflowInstaller
from app.GX_integration.workflows.workflow_invocation import WorkflowInvocationHandler


class WorkflowManager:
    """Workflow manager class for managing Galaxy workflows"""

    def __init__(self, galaxy_client: GalaxyClient):
        self.galaxy_client = galaxy_client

        self.gi_object=self.galaxy_client.gi_object 
        self.gi_admin = self.galaxy_client.gi_admin # For administrative functionalitites like toolshed instantiation and tool installing
        self.toolshed=ToolShedClient(self.gi_admin.gi)    # Toolshed instance

        self.tool_manager = ToolManager(galaxy_client = self.galaxy_client)
        self.data_manager = self.tool_manager.data_manager
        self.log = logging.getLogger(__class__.__name__)
        
        self.workflow_installer = WorkflowInstaller(self.galaxy_client)
        self.invocation_handler = WorkflowInvocationHandler(self.galaxy_client)

    async def upload_workflow(self, workflow_json: dict, ws_manager: SocketManager = None, tracker_id: str = None, retry_count: int = 1, installer_count = 1):
        """Upload workflow from a ga file json."""
        
        await self.workflow_installer.upload_workflow(
            workflow_json = workflow_json,
            ws_manager = ws_manager,
            tracker_id = tracker_id,
            retry_count = retry_count,
            installer_count = installer_count
            )
                

    def get_worlflow_by_name(self, name: str, score_cutoff: int = 70) -> Workflow | None:
        """Get workflow by its name (fuzzy match)."""
        workflow_list = self.gi_object.gi.workflows.get_workflows()
        # Build a dict that maps each workflow name to the full dict
        name_to_wf = {wf["name"]: wf for wf in workflow_list}

        match = process.extractOne(
            name,
            name_to_wf.keys(),          # list of names
            scorer=fuzz.partial_ratio,
            score_cutoff=score_cutoff
        )

        if match is None:               # nothing above the cutoff
            return None

        wf_dict = name_to_wf[match[0]]
        return self.gi_object.workflows.get(wf_dict["id"])
    
    def get_workflow_by_id(self, id: str)-> Workflow | None:
        """Get tool by its id"""
        
        try:
            workflow= self.gi_object.workflows.get(id)
            return workflow 
        except:
            return None
        
    def get_workflow_io(self, workflow: Workflow)-> dict:
        """get input structure of a workflow to be filled"""
        
        return dict.fromkeys(workflow.inputs.keys())

    def _map_inputs_to_steps(self, name: str, inputs: dict, steps: dict) -> dict:
        """ Map workflow inputs to their corresponding input steps. """
        
        input_map = {
            "name": name
        }

        for input_id, input_info in inputs.items():
            step = steps.get(str(input_id))
            input_map[input_id] = {
                "Label": input_info.get("label"),
                "step_id": step.get('id'),
                "input_type": step.get('type'),
                "annotation": step.get('annotation', ''),
                "tool_inputs": step.get('tool_inputs', {}),
                "uuid": input_info.get("uuid", "")
            }

        return input_map
    
    ## TODO : VALIDATION STEP needs improvement.
    def validate_input_to_tool(self, mapped_input: dict, workflow: dict) -> dict:
        """Validate the input HTML form against the first tool it is connected to."""
        
        steps = workflow.get("steps", {})
        input_tools = set()
        tool_xml_cache = {}

        # Map source step ID to connected tool params and tool IDs
        input_connections_map = {}  # {source_step_id: [(tool_id, param_name)]}

        for step in steps.values():
            tool_id = step.get("tool_id")
            if not tool_id:
                continue

            input_steps = step.get("input_steps", {})
            for param_name, conn_info in input_steps.items():
                source_step = str(conn_info.get("source_step"))
                input_connections_map.setdefault(source_step, []).append((tool_id, param_name))
                input_tools.add(tool_id)

        self.log.info(f"Tools that directly take input from workflow inputs are: {input_tools}")
        self.log.info("Collecting tool XML for workflow mapped input")

        # Pre-fetch XML definitions for all involved tools
        for tool_id in input_tools:
            try:
                xml = asyncio.run(self.tool_manager.get_tool_xml(tool_id))
                if xml:
                    tool_xml_cache[tool_id] = ET.fromstring(xml)
                else:
                    self.log.warning(f"Could not retrieve XML for tool_id: {tool_id}")
            except ET.ParseError as e:
                self.log.error(f"Failed to parse XML for tool {tool_id}: {e}")

        # Perform validation for each mapped input
        for input_id, input_details in mapped_input.items():
            if input_id == "name":
                continue

            connections = input_connections_map.get(str(input_id), [])
            for tool_id, param_name in connections:
                root = tool_xml_cache.get(tool_id)
                if root is None:
                    continue

                param_node = root.find(f".//inputs/param[@name='{param_name}']")
                if param_node is not None:
                    validation_info = {
                        "tool_id": tool_id,
                        "tool_param_name": param_name,
                        "type": param_node.get("type"),
                        "format": param_node.get("format", "data"),
                        "label": param_node.get("label"),
                        "help": (param_node.findtext("help") or "").strip(),
                    }

                    input_details.setdefault("validation", []).append(validation_info)
                    self.log.info(f"Validated input '{input_id}' against tool '{tool_id}' param '{param_name}'")
                else:
                    self.log.warning(f"Could not find param '{param_name}' in tool XML for tool_id '{tool_id}'")

        return mapped_input

    def build_input(self, workflow: Workflow, history: History)-> str:
        """builds workflow input html form from the workflow inputs and input steps. """
        
        # map inputs to input steps of the workflow to get information from both
        workflow= self.gi_object.gi.workflows.show_workflow(workflow_id=workflow.id)
        
        mapped_input = self._map_inputs_to_steps(name=workflow['name'], inputs=workflow['inputs'], steps=workflow['steps'])

        # builds a html form from the mapped input payload and validate it
        mapped_input = self.validate_input_to_tool(mapped_input, workflow)
        form_generator = WorkflowFormGenerator(mapped_workflow= mapped_input, data_manager= self.data_manager, workflow= workflow, history= history)
        html_form = form_generator._build_html()
        return html_form
    
    # TODO(less priority): invocation tracker must also consider workflows that have PAUSE steps. But this can wait since pause steps are almost never used.
    async def track_invocation(self, 
                            invocation: Invocation,
                            tracker_id: str = None,
                            ws_manager: SocketManager = None,
                            invocation_check: bool = False
                        ) -> Tuple[
                            Dict[str,List],
                            Literal['Pending', 'Failed', 'Complete'],
                            Optional[str]
                        ]:
                            
        """Tracks invocation steps and waits for the invocation reaches a terminal state and returns with the invocation results""" 
        
        if invocation_check:
            try:
                invocation_outputs, invocation_state_result, invocation_update_time = await self.invocation_handler.track_invocation(
                    invocation = invocation,
                    tracker_id = tracker_id,
                    ws_manager = ws_manager,
                    invocation_check = invocation_check
                    )
                
                return invocation_outputs, invocation_state_result, invocation_update_time
            except Exception as e:
                # Handle failure safely
                self.log.error(f"Error tracking invocations: {e}")
                return {}, InvocationStates.FAILED.value, None
        
        else:
            try:
                return await self.invocation_handler.track_invocation(
                    invocation = invocation,
                    tracker_id = tracker_id,
                    ws_manager = ws_manager,
                    invocation_check = invocation_check
                    )
            except Exception as e:
                # Handle failure safely
                self.log.error(f"Error tracking invocations: {e}")
                return {}
            
        
    async def run_track_workflow(self, inputs: dict, workflow: Workflow , history: History, ws_manager: SocketManager, tracker_id: str):
        """ Start a workflow invocation, track that invocation, get intermediate and final outputs, and prepare a result. """
        
        # NOTE: Deprecated code, workflow execution is done on the galaxy client. Integratoin layer for now only handles invocation tracking and response handling.
        
        # start invocation
        invocation = await asyncio.to_thread(
            self.invocation_handler.invoke_workflow, inputs = inputs, workflow = workflow, history = history
            )
        # track invocation steps and collect outputs
        outputs= await self.track_invocation(invocation, tracker_id, ws_manager) # can set base time extenstion and max_wait here
        # prepare workflow invocation results
        invocation_id, invocation_report, intermediate_outputs, final_outputs = self.invocation_handler.make_result(invocation, outputs)
        
        # Return result of workflow invocation
        return invocation_id, invocation_report, intermediate_outputs, final_outputs
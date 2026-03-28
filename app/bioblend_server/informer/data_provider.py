import httpx
import json
import copy
from rapidfuzz import fuzz
import re
import logging
from typing import Dict, Any, Union, Tuple, Optional
from urllib.parse import quote

from app.galaxy import GalaxyClient

class GalaxyDataProvider:
    """Dynamic Data fetcher/provider of for a given user. Data fecthing ffor galaxy tools, worklows,and datasets."""
    
    def __init__(self, galaxy_client: GalaxyClient, entity_type: str):
        
        self.galaxy_client = galaxy_client
        self.entity_type = entity_type
        
        self.log = logging.getLogger(__class__.__name__)
        self.gi_user = self.galaxy_client.gi_client
        self.gi_admin = self.galaxy_client.gi_admin
        self.username = self.galaxy_client.whoami
   
    def extract_filename(self, path: str) -> str:
        """
        Extracts a filename from a full path string.
        """
        self.log.info(f'Extracting file name from path.')
        match = re.search(r'([^/]+)$', path)
        return match.group(1) if match else path   
    
    async def run_get_request(self, url, headers, params):
        """
        Sends an asynchronous HTTP GET request to the specified URL with the given headers and query parameters.
        """
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url=url, headers=headers, params=params)
            response.raise_for_status()
        return response.json()
    
    async def run_post_request(self, url, headers=None, data=None, json_data=None, params=None):
        """
        Makes an asynchronous HTTP POST request using httpx.
        """
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                url=url,
                headers=headers,
                data=data,
                json=json_data,
                params=params
            )
            response.raise_for_status()
            return response.json()  
    
    def get_datasets(self) -> list[dict]:
        """
        Retrieves all datasets from both Galaxy libraries and histories.
        """
        dataset_list = []
        name_corpus = [] # To build dataset name corpus.
        
        self.log.info('Gathering datasets from libraries...')
        try:
            libraries = self.gi_user.libraries.get_libraries()
            for library in libraries:
                library_details = self.gi_user.libraries.show_library(library['id'], contents=True)
                for lib in library_details:
                    if lib['type'] == 'file':
                        name = self.extract_filename(lib['name'])
                        dataset_list.append({
                            "dataset_id": lib['id'],
                            "name": name,
                            "full_path": lib['name'],
                            "type": lib["type"],
                            "source": "library",
                            "content": (
                                        f"Library dataset titled '{name}' (Galaxy ID: {lib['id']}) "
                                        f"stored under path '{lib['name']}' in the data galaxy instance library. "
                                        f"Dataset type is '{lib['type']}', indicating a Galaxy data file (e.g., FASTQ, BAM, VCF, TXT, etc.). "
                                        f"This file originates from a curated library, suggesting reuse in standardized workflows or reference analyses. "
                                     )
                            })
                        
                        # Build name corpus for key word extraction later on search.
                        name_corpus.append(name)
                        
        except Exception as e:
            self.log.error(f"Failed to retrieve library datasets: {e}")

        self.log.info('Gathering datasets from histories...')
        try:
            # Note: get_datasets() can be slow. Consider filtering if possible.
            for data in self.gi_user.datasets.get_datasets(deleted=False, state='ok'):
                if data['type'] == 'file':
                    dataset_list.append({
                        "dataset_id": data['id'],
                        "name": data['name'],
                        "full_path": data['url'],
                        "type": data['type'],
                        "source": "history",
                        "content": (
                                    f"History dataset titled '{data['name']}' (Galaxy ID: {data['id']}) retrieved from user history. "
                                    f"Located at URL '{data['url']}'. Type: '{data['type']}', representing a file in a Galaxy history."
                                    f"This dataset likely resulted from a prior step in a bioinformatics workflow, tool execution output, or an uploaded to the history by the user."
                                )
                    })
                    
                    # Build name corpus for key word extraction later on search.
                    name_corpus.append(data["name"])
        except Exception as e:
            self.log.error(f"Failed to retrieve history datasets: {e}")
            
        return dataset_list, name_corpus


    def get_tools(self) -> list[dict]:
        """  Retrieves all tools available in the Galaxy instance.  """

        def is_data_manager_tool(tool_dict, threshold=90) -> bool:
            """ Detects if a Galaxy tool is likely a data manager tool. """
            fields_to_check = [
                tool_dict.get('id', ''),
                tool_dict.get('name', ''),
                tool_dict.get('config_file', ''),
            ]

            # Combine all text into one lowercase string
            combined_text = ' '.join(fields_to_check).lower()

            # First, use regex to quickly check for obvious matches
            if re.search(r'data.*manager|manager.*data', combined_text):
                return True

            # Fuzzy check separately for "data" and "manager"
            has_data = fuzz.partial_ratio("data", combined_text) >= threshold
            has_manager = fuzz.partial_ratio("manager", combined_text) >= threshold

            if has_data and has_manager:
                return True
            else:
                return False
   
        tools=[]
        name_corpus = [] # To build tool name corpus for later search
        
        self.log.info('Gathering all available tools...')
        for tool in self.gi_user.tools.get_tools():
            tool_type="data manager" if is_data_manager_tool(tool) else "Regular"
            tool_info={
                    'description': tool.get('description', None),
                    'tool_id': tool['id'],
                    'name': tool['name'],
                    'tool_type': tool_type,
                    'content': (
                                f"Galaxy tool named '{tool['name']}' (ID: {tool['id']}) designed to perform a specific function within a bioinformatics workflow. "
                                f"Tool description: '{tool['name']} {tool.get('description', 'No description provided')}'. "
                            )
                             }
            
            name_corpus.append(tool['name'])
            
            if tool_type == "data manager":
                tool_info['content'] += (
                           f'This Galaxy tool named {tool_info["name"]} is a Galaxy Data Manager tool, meaning it is designed to either create, modify, or manage reference datasets that are used by other tools within the Galaxy platform.'
                    )
            elif tool_type == "Regular" :
                tool_info['content'] += (f'This Galaxy tool named  {tool_info["name"]} is a regular galaxy tool')

            tools.append(tool_info)

        return tools, name_corpus

    def get_workflows(self) -> list[dict]:
        """
        Retrieves all published workflows in the Galaxy instance.
        """
        self.log.info('Gathering all published workflows...')
        workflows = []
        name_corpus = []
        
        for wf in self.gi_user.workflows.get_workflows(published=True):
            desc = str(wf.get('annotations') or wf.get('description') or 'No description available.')
            workflows.append({
                'description': desc,
                'model_class': wf['model_class'],
                'owner': wf['owner'],
                'workflow_id': wf['id'],
                'name': wf['name'],
                'content': (
                    f"Galaxy workflow titled '{wf['name']}' (ID: {wf['id']}), authored by user '{wf['owner']}'. "
                    f"This is a workflow composed of multiple bioinformatics tools connected in a pipeline. "
                    f"Workflow class: '{wf['model_class']}'. Purpose: {desc}. "
                    f"Workflows typically automate multi-step processes."
                )
            })
             
            name_corpus.append(wf["name"])
             
             
        return workflows, name_corpus
    
    def show_workflow(self, workflow_id) -> Tuple[Dict[str, Any], str, Optional[str]]:
        """Retrieve and clean workflow metadata."""

        try:
            workflow = self.gi_user.workflows.show_workflow(workflow_id)
        except Exception as e:
            self.log.error(f"Failed to fetch workflow {workflow_id}: {e}", exc_info=True)
            raise

        self.log.info("Workflow metadata fetched.")
        self.log.debug(json.dumps(workflow, ensure_ascii=False))

        try:
            cleaned_workflow_data, workflow_name, workflow_link = self._clean_workflow_metadata(workflow)
        except Exception as e:
            self.log.warning(f"Workflow metadata cleaning failed: {e}", exc_info=True)
            return workflow, workflow["name"], None

        return cleaned_workflow_data, workflow_name, workflow_link

    
    async def show_tool(self, tool_id) -> Tuple[Dict[str, Any], str, Optional[str]]:
        """Retrieve tool metadata with safe error handling."""
        
        headers = {'x-api-key': self.galaxy_client.user_api_key}
        histories = self.gi_user.histories.get_histories()

        if not histories:
            raise RuntimeError("No histories available to attach to tool build request.")

        url = f'{self.galaxy_client.galaxy_url}/api/tools/{tool_id}/build'
        params = {'history_id': histories[-1]['id']}

        try:
            tool = await self.run_get_request(url=url, headers=headers, params=params)
        except Exception as e:
            self.log.error(f"Failed GET /tools/{tool_id}/build: {e}", exc_info=True)
            raise

        try:
            cleaned_tool_data, tool_name, tool_link = await self._clean_tool_metadata(tool)
        except Exception as e:
            self.log.warning(f"Tool metadata cleaning failed: {e}", exc_info=True)
            return tool, tool["name"], None

        return  cleaned_tool_data, tool_name, tool_link
    
    def show_dataset(self, dataset_id: str) ->  Tuple[Dict[str, Any], str, str]:
        """Retrieve dataset metadata with safe error handling."""

        try:
            dataset = self.gi_user.datasets.show_dataset(dataset_id)
            dataset_link = f"{self.galaxy_client.galaxy_url}/datasets/{dataset_id}/details"
            dataset_name = dataset["name"]
        except Exception as e:
            self.log.error(f"Failed to fetch dataset {dataset_id}: {e}", exc_info=True)
            raise

        return dataset, dataset_name, dataset_link
    
    def _prune_empty_nested(self, obj: Union[Dict[str, Any], list]) -> Union[Dict[str, Any], list]:
        """Recursively prune empty key-value pairs, lists, and dicts from nested structures."""
        
        if isinstance(obj, dict):
            to_remove = []
            # Use list(obj.items()) for safe iteration while modifying 'obj'
            for k, v in list(obj.items()):
                if isinstance(v, (dict, list)):
                    self._prune_empty_nested(v)  # Recurse first
                    if isinstance(v, (dict, list)) and len(v) == 0:
                        to_remove.append(k)
                # Prune None values and strings consisting only of whitespace
                elif v is None or (isinstance(v, str) and not v.strip()):
                    to_remove.append(k)
            for k in to_remove:
                obj.pop(k, None)
        elif isinstance(obj, list):
            # Recursively prune items and rebuild the list, filtering out empty primitives/structures
            obj[:] = [item for item in (self._prune_empty_nested(item) for item in obj) if item not in [None, '', [], {}]]
        return obj
    
    async def _clean_tool_metadata(self, raw_tool: Dict[str, Any]) ->  Tuple[Dict[str, Any], str, str]:
        """Clean tool metadata for RAG compatibility."""

        tool = copy.deepcopy(raw_tool)
        tool_link = None
        tool_name = None
        # Step 1: Drop only junk (UI/runtime/internal keeps all semantics)
        universal_drops = [
            'model_class', 'icon', 'hidden', 'config_file', 'panel_section_id', 'form_style',
            'sharable_url', 'message', 'tool_errors', 'job_id', 'job_remap', 'history_id',
            'display', 'action', 'method', 'enctype', 'help_format'
        ]
        for field in universal_drops:
            tool.pop(field, None)
        
        # Step 1.5: Recursively prune empty nested structures
        self._prune_empty_nested(tool)
        
        # Step 2: Enrich inputs
        if 'inputs' in tool:
            enriched_inputs = []
            for inp in tool['inputs']:
                # Keep all core fields; drop only per-input UI junk
                input_junk = ['model_class', 'argument', 'help_format', 'refresh_on_change', 'hidden', 
                            'is_dynamic', 'tag', 'text_value']  # These are form-specific
                for junk in input_junk:
                    inp.pop(junk, None)
                
                if 'optional' in inp:
                    inp['required'] = not inp['optional']
                
                # flatten options lightly
                if 'options' in inp and isinstance(inp['options'], list):

                    flattened_opts = []
                    for opt in inp['options']:
                        if isinstance(opt, list) and len(opt) >= 2:
                            flattened_opts.append({'label': opt[0], 'value': opt[1], 'selected': opt[2] if len(opt) > 2 else False})
                        else:
                            flattened_opts.append(opt)
                    inp['options'] = flattened_opts
                
                if 'edam' in inp and inp['edam']:
                    pass
                
                enriched_inputs.append(inp)
            tool['inputs'] = enriched_inputs
        
        # Step 3: Full help preservation (strip HTML/tags for clean text, no truncation)
        if 'help' in tool:
            help_text = tool['help']

            clean_help = re.sub(r'<[^>]+>', '', help_text)  # Basic tag removal
            clean_help = re.sub(r'\n\s*\n', '\n\n', clean_help.strip())  # Normalize whitespace
            tool['help_text'] = clean_help
            del tool['help']
               
        # Step 5: Add universal derived fields
        tool['tool_id'] = tool.get('id', 'unknown')
        tool['tool name'] = tool.get('name', 'unknown')
        tool_name = tool['tool name']
        if 'name' in tool:
            del tool['name']
            
        if 'id' in tool:
            del tool['id']
        tool['category'] = tool.get('panel_section_name', tool.get('labels', ['General'])[0])
        
        tool_id = tool['tool_id']
        
        # Add link to for execution.
        tool_url = quote(tool_id, safe = "")
        tool_link = f"{self.galaxy_client.galaxy_url}/?tool_id={tool_url}&version=latest"
        
        if 'panel_section_name' in tool:
            del tool['panel_section_name']
              
        # Flatten arrays for brevity if huge
        for k in ['versions', 'requirements', 'labels', 'xrefs', 'edam_operations', 'edam_topics']:
            if k in tool and isinstance(tool[k], list) and len(tool[k]) > 10:
                tool[k + '_truncated'] = tool[k][:10] + ['...']
        
        return tool, tool_name, tool_link
    
    def _clean_workflow_metadata(self, raw_workflow: Dict[str, Any]) ->  Tuple[Dict[str, Any], str, Optional[str]]:
        """Clean and enrich workflow metadata for RAG compatibility."""
        
        # Initial Safety Guard: Ensure input is a dictionary
        if not isinstance(raw_workflow, dict):
            self.log.error("Input to _clean_workflow_metadata was not a dictionary.")
            return {}, None, None

        workflow = copy.deepcopy(raw_workflow)
        workflow_link = None
        workflow_name = None
        
        try:
            # Step 1: Remove universal non-semantic fields
            universal_drops = [
                'model_class', 'id', 'create_time', 'update_time', 'url', 'published', 'deleted', 'hidden',
                'owner', 'latest_workflow_uuid', 'number_of_steps', 'show_in_tool_panel', 'creator_deleted',
                'doi', 'email_hash', 'readme', 'help', 'slug', 'source_metadata', 'name'
            ]
            for field in universal_drops:
                workflow.pop(field, None)
            
            # Step 1.5: Recursively prune empty nested structures across the entire workflow
            self._prune_empty_nested(workflow)
            
            # Step 2: Enrich inputs by converting dictionary to sorted list
            if 'inputs' in workflow and isinstance(workflow['inputs'], dict):
                enriched_inputs = []
                
                # CORRECTION 1: Robust sorting logic for keys that might be non-integers (like UUIDs)
                sorted_items = sorted(
                    workflow['inputs'].items(), 
                    key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0 
                )
                
                for key, inp in sorted_items:
                    # CORRECTION 2: Type Guard for safety
                    if not isinstance(inp, dict):
                        continue
                    
                    # Remove input-specific non-semantic fields
                    input_junk = ['uuid', 'value']
                    for junk in input_junk:
                        inp.pop(junk, None)
                    
                    # Derive 'required' field
                    inp['required'] = not inp.get('optional', False)
                    
                    self._prune_empty_nested(inp)
                    enriched_inputs.append(inp)
                
                workflow['inputs'] = enriched_inputs
            
            # Step 3: Clean Annotation/Description (logic is fine, kept for context)
            if 'annotation' in workflow:
                annot_text = workflow['annotation']
                if isinstance(annot_text, str): # Type Guard
                    clean_annot = re.sub(r'<[^>]+>', '', annot_text)
                    clean_annot = re.sub(r'\n\s*\n', '\n\n', clean_annot.strip())
                    
                    if 'description' not in workflow:
                        workflow['description'] = clean_annot
                    else:
                        workflow['description'] += f" {clean_annot}"
                        
                workflow.pop('annotation', None)
            
            # Step 4: Enrich steps by converting dictionary to sorted list
            if 'steps' in workflow and isinstance(workflow['steps'], dict):
                enriched_steps = []
                
                # CORRECTION 1: Robust sorting logic for steps keys
                sorted_steps = sorted(
                    workflow['steps'].items(), 
                    key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0
                )
                
                for _, step in sorted_steps:
                    # CORRECTION 2: Type Guard for safety
                    if not isinstance(step, dict):
                        continue
                    
                    # Remove step-specific non-semantic fields
                    step_junk = ['when', '__page__', '__rerun_remap_job_id__']
                    for junk in step_junk:
                        step.pop(junk, None)
                                            
                    # For subworkflows, recursively clean the metadata
                    if step.get('type') == 'subworkflow':
                        step, _, _ = self._clean_workflow_metadata(step)
                    
                    # Structure tool info better
                    if step.get('type') == 'tool':
                        if 'tool_id' in step and isinstance(step['tool_id'], str): # Type Guard
                            parts = step['tool_id'].split('/')
                            if len(parts) >= 5:
                                tool_info = {
                                    'full_path': step['tool_id'],
                                    'owner': parts[-4],
                                    'repo': parts[-3],
                                    'name': parts[-2],
                                }
                                if 'tool_version' in step and isinstance(step['tool_version'], str): # Type Guard
                                    tool_info['version'] = re.sub(r'\+galaxy\d+$', '', step['tool_version'])
                                
                                t_inputs = step.get('tool_inputs')
                                if t_inputs is not None and isinstance(t_inputs, dict): # Type Guard
                                    tool_info['tool_inputs'] = t_inputs
                                    
                                step['tool_info'] = tool_info
                            
                        step.pop('tool_id', None)
                        step.pop('tool_version', None)
                        step.pop('tool_inputs', None)
                    
                    self._prune_empty_nested(step)
                    enriched_steps.append(step)
                
                workflow['steps'] = enriched_steps
                workflow['number_of_steps'] = len(enriched_steps)
            
            # Step 5: Clean creator list
            if 'creator' in workflow and isinstance(workflow['creator'], list):
                for person in workflow['creator']:
                    if isinstance(person, dict): # Type Guard
                        person_junk = [
                            'class', 'address', 'alternateName', 'email', 'faxNumber', 'image', 'telephone',
                            'url', 'familyName', 'givenName', 'honorificPrefix', 'honorificSuffix', 'jobTitle'
                        ]
                        for junk in person_junk:
                            person.pop(junk, None)
                self._prune_empty_nested(workflow['creator'])
            
            # Step 6: Add derived fields
            workflow['workflow_id'] = raw_workflow.get('id', 'unknown')
            workflow['workflow name'] = raw_workflow.get('name', 'unknown')
            workflow_name = workflow['workflow name']
            
            tags = workflow.get('tags')
            if isinstance(tags, list) and tags:
                workflow['category'] = tags[0]
            else:
                workflow['category'] = 'General'
            
            workflow_id = workflow['workflow_id']
            if workflow_id != 'unknown' and hasattr(self, 'galaxy_client') and hasattr(self.galaxy_client, 'galaxy_url'):
                workflow_link = f"{self.galaxy_client.galaxy_url}/workflows/run?id={workflow_id}"
                                        
            # Final prune
            self._prune_empty_nested(workflow)
            
        except Exception as e:
            self.log.error(f"Error cleaning metadata for Galaxy workflow: {e}")
            return raw_workflow, workflow_name, None
        if workflow_link:
            return workflow, workflow_name, workflow_link
        return workflow, workflow_name, None
import os
from sys import path
path.append(".")

import httpx
import random
import logging
from dotenv import load_dotenv
from app.log_setup import configure_logging
import asyncio

import xml.etree.ElementTree as ET
from bioblend.galaxy import GalaxyInstance

from app.bioblend_server.informer.utils import SearchThresholds
import tiktoken

MODEL_NAME = "text-embedding-3-small"
MAX_TOKENS = 8000  

#TODO: Fix here
def truncate_to_token_limit(text, model=MODEL_NAME, max_tokens=MAX_TOKENS):
    enc = tiktoken.encoding_for_model(model)
    tokens = enc.encode(text)
    
    if len(tokens) > max_tokens:
        tokens = tokens[:max_tokens]
    
    return enc.decode(tokens)



class GalaxyToolScraper:

    def __init__(self):

        load_dotenv()
        configure_logging()
        
        self.log = logging.getLogger(__class__.__name__)
            
        # Get Galaxy scraping credentials from environment variables
        self.galaxy_url = os.getenv("GALAXY_SCRAPING_URL")
        self.galaxy_url_api_key = os.getenv("GALAXY_SCRAPING_API_KEY")
        
        # Validate credentials
        if not self.galaxy_url or not self.galaxy_url_api_key:
            self.log.error("Galaxy scraping URL or API key not found in environment variables.")
            raise ValueError("Galaxy scraping URL or API key not found in environment variables.")
        
        # Initialize Galaxy Instance
        self.gi = GalaxyInstance(url=self.galaxy_url, key=self.galaxy_url_api_key)
        self.semaphore = asyncio.Semaphore(10)
        self.client = httpx.AsyncClient()

    async def close(self):
        await self.client.aclose()
        
    async def scrape_tool(self):
        # setting variables
        self.log.info(f"Scraping galaxy tools data...")
        self.log.info(f"Fetching tool list from Galaxy instance at {self.galaxy_url}...")
        
        tools = await asyncio.to_thread(self.gi.tools.get_tools)
        
        # Group tools By Category
        tools_by_category = {}
        for tool in tools:
            category = tool.get("panel_section_name") or "Uncategorized"
            tools_by_category.setdefault(category, []).append(tool)
            
        # Deduplicate tools
        tool_map = {}
        total_tools = 0
        for category, cat_tools in tools_by_category.items():
            # TODO: Fix implementation of percentage and tool scraping, the percentage is uneeded here.
            
            percentage = SearchThresholds.TOOL_SCRAPE_PERCENTAGE.value
            num_tools = int(len(cat_tools) * percentage)
            if num_tools == 0 and percentage > 0:
                num_tools = 1
            num_tools = len(cat_tools)
            self.log.debug(f"Selecting {num_tools} tools from category '{category}' ({num_tools} total tools, {percentage*100}% specified).")
            total_tools +=num_tools
            selected_tools = random.sample(cat_tools, num_tools) if num_tools < len(cat_tools) else cat_tools
            
            for tool in selected_tools:
                tool_id = tool.get("id")
                
                if tool_id not in tool_map:
                    tool_map[tool_id] = {"base": tool, "categories": set()}
                tool_map[tool_id]["categories"].add(category)
                
        # Final Filtered tools 
        self.log.info(f"Fetching tool data for {total_tools} tools.")
        filtered_tools = []
        for entry in tool_map.values():
            tool = entry["base"]
            tool["categories"] = list(entry["categories"])
            filtered_tools.append(tool)
            
        # Scrape tool details concurrently
        tools_json = []
        async def fetch_with_semaphore(tool):
            async with self.semaphore:
                try:
                    tool_data = await self.fetch_tools_detail(tool)
                    if tool_data:
                        tools_json.append(tool_data)
                except Exception as e:
                    self.log.warning(f"Error processing tool {tool.get('id', 'unknown')}: {e}")

        # Create tasks for all tools
        tasks = [fetch_with_semaphore(tool) for tool in filtered_tools]
        # Run tasks concurrently
        await asyncio.gather(*tasks)       
        return tools_json
           

    # Scrape helper function
    async def fetch_tools_detail(self, tool: dict) -> dict | None:
        tool_id = tool.get("id", "")
        try:
            help_text = ""
            raw_tool_url = f"{self.galaxy_url}/api/tools/{tool_id}/raw_tool_source?key={self.galaxy_url_api_key}"
            response = await self.client.get(raw_tool_url)
            response.raise_for_status()
            tool_xml = response.text

            try:
                root = ET.fromstring(tool_xml)
            except ET.ParseError:
                self.log.warning(f"Invalid XML for tool {tool_id}, skipping XML parsing.")
                root = None

            help_elem = root.find("help") if root is not None else None
            if help_elem is not None and help_elem.text:
                help_text = help_elem.text.strip()
                self.log.debug(f"Content Extracted from help section for tool {tool_id}.")
            else:
                self.log.debug(f"No help section found for tool {tool_id}.")

            name = (tool.get("name") or "Unknown").strip()
            description = (tool.get("description") or "").strip()
            version = (tool.get("version") or "").strip()
            
            categories = tool.get("categories") or []  # categories is expected to be a list            
            clean_categories = {c.strip() for c in categories if isinstance(c, str) and c.strip()}
            if not clean_categories:
                clean_categories = {"Uncategorized"}

            # You can make this more natural-language friendly for embeddings:
            content = (
                f"This is a Galaxy tool named '{name}'. "
                f"It is described as follows: {description if description else 'No description available.'} "
                f"The tool belongs to the following categories: {', '.join(sorted(clean_categories))}. "
                f"The current version of the tool is {version if version else 'unspecified'}. "
                f"Here is the help or usage information: {help_text if help_text else 'No help content provided.'}"
            )
            # Usage
            cropped_context = truncate_to_token_limit(content)

            return {
                "tool_id": tool_id,
                "name": name,
                "description": description,
                "help": help_text,
                "content": cropped_context
            }

        except Exception as e:
            self.log.warning(f"Error fetching details for tool {tool_id}: {e}")
            return None
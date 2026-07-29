from sys import path

path.append(".")

import logging
from typing import Literal

from app.bioblend_server.informer.manager import InformerManager
from app.bioblend_server.informer.indexer_state import IndexerState
from app.bioblend_server.informer.scrapers.tool_scraper import GalaxyToolScraper
from app.bioblend_server.informer.scrapers.workflow_scraper import (
    GalaxyWorkflowScraper,
    WorkflowHubScraper,
)
from app.bioblend_server.informer.utils import InformerTTLs


class GlobalRecommender:

    def __init__(self):
        self.manager: InformerManager = None
        self.log = logging.getLogger(__class__.__name__)
        self.tool_scraper = GalaxyToolScraper()
        self.workflow_scraper = GalaxyWorkflowScraper()
        self.hub_scraper = WorkflowHubScraper()
        self.state = IndexerState()

    @classmethod
    async def create(cls):
        self = cls()
        self.manager = await InformerManager().create()
        return self

    @staticmethod
    def _collection_name(entity_type: Literal["tool", "workflow"]) -> str:
        return f"generic_galaxy_{entity_type}"

    def _skip_if_fresh(self, entity_type: Literal["tool", "workflow"]) -> bool:
        name = self._collection_name(entity_type)
        if self.state.is_fresh(name, InformerTTLs.LIFESPAN.value):
            last = self.state.get_last_indexed(name)
            self.log.info(
                f"Skipping scrape of {name}; last indexed at {last.isoformat()}."
            )
            return True
        return False

    async def store_scraped_tools(self):
        if self._skip_if_fresh("tool"):
            return
        scraped_tools = await self.tool_scraper.scrape_tool()
        await self.tool_scraper.close()
        await self.store_to_collection(scraped_tools, "tool")

    async def store_scraped_workflows(self):
        if self._skip_if_fresh("workflow"):
            return
        github_workflows = await self.workflow_scraper.scrape_workflows()
        hub_workflows = await self.hub_scraper.scrape_workflows()

        unique_workflows: dict[str, dict] = {}

        def add_or_update_workflow(wf):
            name = wf.get("name").lower().strip()
            if not name:
                return

            if name not in unique_workflows:
                unique_workflows[name] = wf
            else:
                existing_wf = unique_workflows[name]
                existing_desc = existing_wf.get("description") or ""
                new_desc = wf.get("description")

                if len(new_desc) > len(existing_desc):
                    unique_workflows[name] = wf

        for wf in github_workflows:
            add_or_update_workflow(wf)

        for wf in hub_workflows:
            add_or_update_workflow(wf)

        all_workflows = list(unique_workflows.values())
        await self.store_to_collection(all_workflows, "workflow")

    async def store_to_collection(
        self, scraped_data: list[dict], entity_type: Literal["tool", "workflow"]
    ):
        collection_name = self._collection_name(entity_type)
        self.log.info(f"Storing scraped galaxy data to Qdrant.")
        result = await self.manager.embed_and_store_entities(
            entities=scraped_data, collection_name=collection_name
        )
        if result is None:
            self.log.error(
                f"Aborting freshness update for {collection_name}: embed_and_store_entities failed."
            )
            return
        self.state.mark_indexed(collection_name)
        self.log.info(f"content embedded and stored in {collection_name} succefully.")

from sys import path

path.append(".")

import os
import re
import json
import httpx
import asyncio
import logging
from dotenv import load_dotenv

from app.log_setup import configure_logging
from app.bioblend_server.informer.utils import (
    WorkflowGitubScraperUrl,
    WorkflowHubScraperUrl,
)


class GalaxyWorkflowScraper:

    def __init__(self):

        load_dotenv()
        configure_logging()

        self.log = logging.getLogger(__class__.__name__)

        # github token for future use
        self.github_token = os.getenv("GITHUB_TOKEN", None)
        self.github_API_URL = WorkflowGitubScraperUrl.GITHUB_SCRAPE_URL.value
        self.raw_base_url = WorkflowGitubScraperUrl.RAW_BASE_URL.value

        if self.github_token:
            self.headers = {"Authorization": f"token {self.github_token}"}
        else:
            self.log.warning(
                "No GITHUB_TOKEN found in environment variables. Using unauthenticated requests may hit rate limits."
            )
            self.headers = {}

        self.semaphore = asyncio.Semaphore(10)

    async def github_api_get(self, url: str) -> dict | list | str:
        async with self.semaphore:
            async with httpx.AsyncClient(headers=self.headers, timeout=30.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                text = resp.text
                if not text.strip():
                    return ""
                try:
                    return resp.json()
                except Exception:
                    return text

    async def parse_ga_content(self, ga_text: str) -> dict:
        """Return dict with workflow_name, number_of_steps, tools_used (list of dicts)."""
        try:
            if isinstance(ga_text, str):
                data = json.loads(ga_text)
            else:
                data = ga_text

            workflow_name = data.get("name", "unknown")
            steps = data.get("steps", {}) or {}
            tools_used: list = []

            for step in steps.values():
                if step.get("type") != "tool":
                    continue
                repo = step.get("tool_shed_repository", {}) or {}
                tool_info = {
                    "id": step.get("tool_id", "") or "",
                    "name": step.get("name", "") or "",
                    "version": step.get("tool_version", "") or "",
                    "owner": repo.get("owner", "") or "",
                    "category": repo.get("name", "") or "",
                    "tool_shed_url": repo.get("tool_shed", "") or "",
                }
                if tool_info not in tools_used:
                    tools_used.append(tool_info)

            return {
                "workflow_name": workflow_name,
                "owner": data.get("owner", "Unkown"),
                "description": str(
                    data.get("annotations")
                    or data.get("description")
                    or "No description available."
                ),
                "number_of_steps": len(steps),
                "tools_used": tools_used,
            }
        except Exception as e:
            self.log.error(f"Failed to parse .ga JSON: {e}")
            return {"workflow_name": "unknown", "number_of_steps": 0, "tools_used": []}

    async def scan_repo(self, category: str, repo_name: str) -> list[dict] | None:
        """Scans the iwc workflow repository for workflows and there description."""
        # base_path now represents the full path to the current directory being scanned
        base_path = f"{category}/{repo_name}".strip("/")
        if base_path:
            api_url = f"{self.github_API_URL}/{base_path}"
        else:
            api_url = self.github_API_URL  # for root or initial call

        try:
            repo_contents = await self.github_api_get(api_url)
        except Exception as e:
            self.log.error(f"Failed to list repo contents for {base_path}: {e}")
            return None

        workflow_files = []
        files_present = set()
        directories_present = set()
        readme_content: str | None = None

        # Identify and process files/directories
        ga_fetch_tasks = []
        readme_task = None

        for item in repo_contents:
            name = item.get("name", "")
            itype = item.get("type", "")

            if itype == "file":
                files_present.add(name)
                if name.endswith(".ga"):
                    # prepare for concurrent fetch/parse
                    ga_path = f"{base_path}/{name}" if base_path else name
                    url_path = f"{self.raw_base_url}/{ga_path}"

                    async def _fetch_and_parse_ga(
                        name=name, ga_path=ga_path, url_path=url_path
                    ):
                        try:
                            ga_text = await self.github_api_get(url_path)
                            ga_info = await self.parse_ga_content(ga_text)
                            ga_info.update(
                                {
                                    "file_name": name,
                                    "raw_download_url": f"{self.raw_base_url}/{ga_path}",
                                }
                            )
                            return ga_info
                        except Exception as e:
                            # preserve the original per-file error logging behaviour
                            self.log.error(f"Error fetching/parsing {ga_path}: {e}")
                            return None

                    ga_fetch_tasks.append(_fetch_and_parse_ga())

                if name == "README.md":
                    # prepare concurrent fetch for README
                    readme_path = f"{base_path}/README.md" if base_path else "README.md"
                    url_path = f"{self.raw_base_url}/{readme_path}"

                    async def _fetch_readme(url_path=url_path, base_path=base_path):
                        try:
                            return await self.github_api_get(url_path)
                        except Exception as e:
                            self.log.error(
                                f"Failed to fetch README for {base_path}: {e}"
                            )
                            return None

                    readme_task = _fetch_readme()

            elif itype == "dir":
                directories_present.add(name)

        # Run all .ga fetch/parse tasks and README fetch concurrently
        all_fetch_tasks = []
        if ga_fetch_tasks:
            all_fetch_tasks.extend(ga_fetch_tasks)
        if readme_task:
            all_fetch_tasks.append(readme_task)

        if all_fetch_tasks:
            # gather returns results in same order as tasks; each ga task returns either dict or None
            fetch_results = await asyncio.gather(
                *all_fetch_tasks, return_exceptions=False
            )

            # split results back: ga results first (len = len(ga_fetch_tasks)), then optional readme
            idx = 0
            for _ in range(len(ga_fetch_tasks)):
                ga_info = fetch_results[idx]
                idx += 1
                if ga_info:
                    if ga_info not in workflow_files:
                        workflow_files.append(ga_info)

            # if README was fetched, it's the last result
            if readme_task:
                readme_content = fetch_results[-1]

        results = []

        # Handle the current folder's workflows (if found)
        if workflow_files:
            # We use base_path to intelligently determine the context names
            current_category = (
                base_path.split("/")[0].lower() if base_path.split("/") else ""
            )
            current_repo_name = (
                base_path.split("/")[-1].lower() if base_path.split("/") else ""
            )

            repo_dict = {
                "category": current_category,
                "workflow_repository": current_repo_name,
                "workflow_files": workflow_files,
                "planemo_tests": [],
                "has_test_data": "test-data" in directories_present,
                "has_dockstore_yml": ".dockstore.yml" in files_present,
                "has_readme": "README.md" in files_present,
                "readme_content": readme_content,
                "has_changelog": "CHANGELOG.md" in files_present,
            }
            results.append(repo_dict)

        # Recurse into subdirectories concurrently (preserving original skip rules)
        sub_tasks = []
        for dir_name in directories_present:
            if dir_name == "test-data" or dir_name.startswith(
                "."
            ):  # skip special or hidden dirs
                continue

            # The *entire* base_path becomes the new 'category' context
            new_category = base_path

            # The directory name becomes the new 'repo_name' for the next level down
            sub_tasks.append(self.scan_repo(new_category, dir_name))

        if sub_tasks:
            # run subdirectory scans concurrently
            sub_results_list = await asyncio.gather(*sub_tasks, return_exceptions=False)
            for sub_results in sub_results_list:
                if sub_results:
                    results.extend(sub_results)

        return results if results else None

    def clean_readme(self, text: str) -> str:
        """Normalize and clean README/help text for embedding."""
        if not isinstance(text, str):
            return ""
        # Remove HTML/XML tags
        text = re.sub(r"<[^>]+>", " ", text)
        # Replace multiple underline/equal sequences
        text = re.sub(r"[=_]{2,}", " ", text)
        # Remove box drawing / ASCII table characters
        text = re.sub(r"[│║╔╗╚╝╠╣═╦╩╬┼─┐└┘┌┴┬├┤]+", " ", text)
        # Remove markdown table lines (+ | -)
        text = re.sub(r"[\+\|\-]{2,}", " ", text)
        # Remove stray markdown special chars
        text = re.sub(r"[*_`#\[\]]", "", text)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def preprocess_scraped(self, raw_data: list) -> list:
        """
        Convert raw scraped data (list of repo dicts) into a preprocessed, compact format:
        - category
        - workflow_repository
        - workflow_name (first non-empty workflow_files.workflow_name)
        - tool_names (unique flattened list from all workflow_files)
        - readme_cleaned
        - raw_download_url (first non-empty found)
        - content (structured text for embeddings)
        """
        preprocessed = []
        for repo in raw_data:
            tool_names = []
            owner = ""
            description = ""
            chosen_workflow_name = None
            chosen_raw_url = None

            # Iterate all workflow_files and aggregate tool names; choose first non-empty name & raw_url
            for wf in repo.get("workflow_files", []):
                # Capture first non-empty workflow name
                if not chosen_workflow_name:
                    name = wf.get("workflow_name") or ""
                    owner = wf.get("owner") or ""
                    description = wf.get("description")
                    if name:
                        chosen_workflow_name = name
                # Capture first non-empty raw URL
                if not chosen_raw_url:
                    url = wf.get("raw_download_url") or ""
                    if url:
                        chosen_raw_url = url
                # Collect tool names
                for tool in wf.get("tools_used", []):
                    tname = tool.get("name")
                    if tname:
                        tool_names.append(tname)

            if not chosen_workflow_name:
                self.log.debug(
                    f"Fetching detailed data for galaxy workflow named: {chosen_workflow_name}"
                )

            # Make unique while preserving order
            seen = set()
            unique_tool_names = []
            for t in tool_names:
                if t not in seen:
                    seen.add(t)
                    unique_tool_names.append(t)

            readme = repo.get("readme_content", "") or ""
            cleaned_readme = self.clean_readme(readme)

            # Well-structured semantic context rich content field
            content = (
                f"This is a Galaxy workflow.\n"
                f"Workflow Name: {chosen_workflow_name or repo.get('workflow_repository', 'Unknown')}.\n"
                f"Category: {repo.get('category', 'Uncategorized')}.\n"
                f"Owner: {owner if owner else 'unknown'}"
                f"Workflow Description: {description if description else 'refer to readme.'}"
                f"Description (from README): {cleaned_readme.strip()}.\n"
                f"This workflow uses the following tools: {', '.join(unique_tool_names) if unique_tool_names else 'No tools listed'}.\n"
            )

            preprocessed.append(
                {
                    "name": chosen_workflow_name or repo.get("workflow_repository", ""),
                    "description": description,
                    "source": "iwc",
                    "owner": owner,
                    "raw_download_url": chosen_raw_url or "",
                    "content": content,
                }
            )

        return preprocessed

    async def scrape_workflows(self):
        """
        Scrape Galaxy workflows from the IWC GitHub repository, preprocess them in-memory,
        and save a single preprocessed JSON file suitable for downstream LLM or embedding use.
        """
        self.log.info(" Starting Galaxy IWC workflow scraping...")

        all_data = []
        self.log.info("Initiating deep scan from repository root.")

        # Scan root/ and recurse everything, including files at the root.
        repo_data_list = await self.scan_repo("", "")

        if repo_data_list:
            all_data.extend(repo_data_list)
        self.log.info(
            f"Completed scraping workflows across {len(all_data)} repositories."
        )

        try:
            self.log.info("Preprocessing scraped workflows...")
            preprocessed = self.preprocess_scraped(all_data)
            if not preprocessed:
                raise ValueError("No workflows scraped")
            return preprocessed
        except Exception as e:
            self.log.error(f"Error during preprocessing or saving: {e}")
            raise


class WorkflowHubScraper:
    def __init__(self):
        configure_logging()
        self.log = logging.getLogger(__class__.__name__)
        self.base_url = WorkflowHubScraperUrl.BASE_URL.value
        self.trs_base_url = WorkflowHubScraperUrl.TRS_BASE_URL.value
        self.semaphore = asyncio.Semaphore(5)

    async def _get(self, client, url, params=None):
        try:
            resp = await client.get(url, params=params, timeout=30.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.log.error(f"Request failed for {url}: {e}")
            return None

    async def fetch_workflow_details(self, client, wf_id):
        """Fetch detailed Metadata"""
        url = f"{self.api_url}/{wf_id}"
        data = await self._get(client, url)
        if not data:
            return None

        attr = data.get("data", {}).get("attributes", {})
        latest_version = attr.get("latest_version", 1)

        return {
            "id": data["data"]["id"],
            "title": attr.get("title", ""),
            "description": attr.get("description", "") or "",
            "tags": attr.get("tags", []),
            "versions": attr.get("versions", []),
            "latest_version": latest_version,
            "web_url": f"{self.base_url}/workflows/{wf_id}",
            "download_url": f"{self.base_url}/workflows/{wf_id}/download?version={latest_version}",
        }

    def preprocess_workflowhub_data(self, raw_items: list) -> list:

        preprocessed = []

        for tool in raw_items:
            name = tool.get("name") or tool.get("id", "Unknown")
            description = tool.get("description", "") or ""

            # Construct a direct download link for the latest Galaxy version if available
            versions = tool.get("versions", [])
            if not versions:
                continue

            latest_version = versions[-1]
            version_id = latest_version.get("id")

            # Create content string
            img_string = (
                f"This is a Galaxy workflow from WorkflowHub (TRS Source).\n"
                f"ID: {tool['id']}\n"
                f"Name: {name}\n"
                f"Description: {description}\n"
                f"URL: {tool.get('url', '')}\n"
            )

            preprocessed.append(
                {
                    "name": name,
                    "description": description,
                    "source": "workflow_hub",
                    "owner": tool.get("organization", "WorkflowHub"),
                    "raw_download_url": f"{self.trs_base_url}/tools/{tool['id']}/versions/{version_id}/GALAXY/descriptor",
                    "content": img_string,
                }
            )
        return preprocessed

    async def scrape_workflows(self):
        self.log.info("starting WorkflowHub scraping...")

        headers = {"Accept": "application/json"}

        async with httpx.AsyncClient(headers=headers) as client:
            # 1. Fetch all tools (workflows) from TRS
            url = f"{self.trs_base_url}/tools"
            params = {"limit": 1000}

            raw_tools = await self._get(client, url, params=params)

            if not raw_tools:
                self.log.warning("No tools returned from TRS API.")
                return []

            self.log.info(
                f"Fetched {len(raw_tools)} items from TRS. Filtering for Galaxy..."
            )

            galaxy_workflows = []
            for t in raw_tools:
                # Check for GALAXY in versions
                is_galaxy = False
                versions = t.get("versions", [])
                for v in versions:
                    # 'descriptor_type' is usually a list of strings like ["GALAXY"]
                    dtypes = v.get("descriptor_type", [])
                    if "GALAXY" in dtypes or "GALAXY" == dtypes:
                        is_galaxy = True
                        break

                if is_galaxy:
                    galaxy_workflows.append(t)

            self.log.info(f"Found {len(galaxy_workflows)} Galaxy workflows.")

            return self.preprocess_workflowhub_data(galaxy_workflows)
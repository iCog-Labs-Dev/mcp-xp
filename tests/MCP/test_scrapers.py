import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from typing import List, Dict
import xml.etree.ElementTree as ET

import sys
sys.path.append(".")

from app.bioblend_server.informer.scrapers.tool_scraper import GalaxyToolScraper
from app.bioblend_server.informer.scrapers.workflow_scraper import (
    GalaxyWorkflowScraper,
    WorkflowHubScraper
)


@pytest.fixture
def mock_galaxy_instance():
    """Mock GalaxyInstance for tool scraper."""
    with patch("app.bioblend_server.informer.scrapers.tool_scraper.GalaxyInstance") as mock_gi:
        mock_instance = MagicMock()
        mock_gi.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_env_vars():
    """Mock environment variables for scrapers."""
    with patch.dict("os.environ", {
        "GALAXY_SCRAPING_URL": "http://test-galaxy.org",
        "GALAXY_SCRAPING_API_KEY": "test-api-key",
        "GITHUB_TOKEN": "test-github-token"
    }):
        yield


@pytest.fixture
def sample_tools():
    """Sample tool data for testing."""
    return [
        {
            "id": "tool1",
            "name": "Tool One",
            "description": "First test tool",
            "version": "1.0",
            "panel_section_name": "Category A"
        },
        {
            "id": "tool2",
            "name": "Tool Two",
            "description": "Second test tool",
            "version": "2.0",
            "panel_section_name": "Category A"
        },
        {
            "id": "tool3",
            "name": "Tool Three",
            "description": "Third test tool",
            "version": "1.5",
            "panel_section_name": "Category B"
        }
    ]


@pytest.fixture
def sample_tool_xml():
    """Sample tool XML for testing."""
    return """<?xml version="1.0"?>
<tool id="tool1" name="Tool One" version="1.0">
    <description>First test tool</description>
    <help>
        This is the help text for Tool One.
        It provides detailed usage information.
    </help>
</tool>"""


@pytest.fixture
def sample_workflow_ga():
    """Sample Galaxy workflow .ga file content."""
    return {
        "name": "Test Workflow",
        "owner": "test_user",
        "description": "A test workflow",
        "steps": {
            "0": {
                "type": "tool",
                "tool_id": "tool1",
                "name": "Tool One",
                "tool_version": "1.0",
                "tool_shed_repository": {
                    "owner": "test_owner",
                    "name": "test_repo",
                    "tool_shed": "https://toolshed.g2.bx.psu.edu"
                }
            },
            "1": {
                "type": "tool",
                "tool_id": "tool2",
                "name": "Tool Two",
                "tool_version": "2.0",
                "tool_shed_repository": {
                    "owner": "test_owner",
                    "name": "test_repo2",
                    "tool_shed": "https://toolshed.g2.bx.psu.edu"
                }
            }
        }
    }


class TestGalaxyToolScraper:
    """Tests for GalaxyToolScraper class."""

    @pytest.mark.asyncio
    async def test_tool_scraper_initialization_success(self, mock_env_vars, mock_galaxy_instance):
        """Test successful initialization of GalaxyToolScraper."""
        scraper = GalaxyToolScraper()
        
        assert scraper.galaxy_url == "http://test-galaxy.org"
        assert scraper.galaxy_url_api_key == "test-api-key"
        assert scraper.gi is not None
        
        await scraper.close()

    @pytest.mark.asyncio
    async def test_tool_scraper_initialization_missing_url(self):
        """Test initialization fails with missing URL."""
        with patch.dict("os.environ", {"GALAXY_SCRAPING_API_KEY": "test-key", "GALAXY_SCRAPING_URL": ""}, clear=False):
            with patch("app.bioblend_server.informer.scrapers.tool_scraper.load_dotenv"):
                with pytest.raises(ValueError, match="Galaxy scraping URL or API key not found"):
                    GalaxyToolScraper()

    @pytest.mark.asyncio
    async def test_tool_scraper_initialization_missing_api_key(self):
        """Test initialization fails with missing API key."""
        with patch.dict("os.environ", {"GALAXY_SCRAPING_URL": "http://test.org", "GALAXY_SCRAPING_API_KEY": ""}, clear=False):
            with patch("app.bioblend_server.informer.scrapers.tool_scraper.load_dotenv"):
                with pytest.raises(ValueError, match="Galaxy scraping URL or API key not found"):
                    GalaxyToolScraper()


    @pytest.mark.asyncio
    async def test_scrape_tool_success(self, mock_env_vars, mock_galaxy_instance, sample_tools, sample_tool_xml):
        """Test successful tool scraping."""
        scraper = GalaxyToolScraper()
        
        # Mock get_tools
        mock_galaxy_instance.tools.get_tools = MagicMock(return_value=sample_tools)
        
        # Mock HTTP client responses
        mock_response = MagicMock()
        mock_response.text = sample_tool_xml
        mock_response.raise_for_status = MagicMock()
        scraper.client.get = AsyncMock(return_value=mock_response)
        
        # Mock asyncio.to_thread
        with patch("asyncio.to_thread", side_effect=lambda f, *args, **kwargs: f(*args, **kwargs)):
            result = await scraper.scrape_tool()
        
        assert isinstance(result, list)
        assert len(result) > 0
        assert all("tool_id" in tool for tool in result)
        assert all("name" in tool for tool in result)
        assert all("content" in tool for tool in result)
        
        await scraper.close()

    @pytest.mark.asyncio
    async def test_fetch_tools_detail_success(self, mock_env_vars, mock_galaxy_instance, sample_tool_xml):
        """Test fetching tool details successfully."""
        scraper = GalaxyToolScraper()
        
        tool = {
            "id": "tool1",
            "name": "Tool One",
            "description": "Test tool",
            "version": "1.0",
            "categories": ["Category A"]
        }
        
        mock_response = MagicMock()
        mock_response.text = sample_tool_xml
        mock_response.raise_for_status = MagicMock()
        scraper.client.get = AsyncMock(return_value=mock_response)
        
        result = await scraper.fetch_tools_detail(tool)
        
        assert result is not None
        assert result["tool_id"] == "tool1"
        assert result["name"] == "Tool One"
        assert "help" in result
        assert "content" in result
        assert "This is a Galaxy tool named" in result["content"]
        
        await scraper.close()

    @pytest.mark.asyncio
    async def test_fetch_tools_detail_invalid_xml(self, mock_env_vars, mock_galaxy_instance):
        """Test handling of invalid XML."""
        scraper = GalaxyToolScraper()
        
        tool = {
            "id": "tool1",
            "name": "Tool One",
            "description": "Test tool",
            "version": "1.0",
            "categories": ["Category A"]
        }
        
        mock_response = MagicMock()
        mock_response.text = "Invalid XML <unclosed"
        mock_response.raise_for_status = MagicMock()
        scraper.client.get = AsyncMock(return_value=mock_response)
        
        result = await scraper.fetch_tools_detail(tool)
        
        # Should still return a result even with invalid XML
        assert result is not None
        assert result["tool_id"] == "tool1"
        assert result["help"] == ""  # No help text extracted
        
        await scraper.close()


    @pytest.mark.asyncio
    async def test_fetch_tools_detail_http_error(self, mock_env_vars, mock_galaxy_instance):
        """Test handling of HTTP errors when fetching tool details."""
        scraper = GalaxyToolScraper()
        
        tool = {
            "id": "tool1",
            "name": "Tool One",
            "description": "Test tool",
            "version": "1.0",
            "categories": ["Category A"]
        }
        
        scraper.client.get = AsyncMock(side_effect=Exception("HTTP Error"))
        
        result = await scraper.fetch_tools_detail(tool)
        
        assert result is None
        
        await scraper.close()

    @pytest.mark.asyncio
    async def test_scrape_tool_with_uncategorized(self, mock_env_vars, mock_galaxy_instance, sample_tool_xml):
        """Test scraping tools without categories."""
        scraper = GalaxyToolScraper()
        
        tools = [
            {
                "id": "tool1",
                "name": "Tool One",
                "description": "Test tool",
                "version": "1.0",
                "panel_section_name": None  # No category
            }
        ]
        
        mock_galaxy_instance.tools.get_tools = MagicMock(return_value=tools)
        
        mock_response = MagicMock()
        mock_response.text = sample_tool_xml
        mock_response.raise_for_status = MagicMock()
        scraper.client.get = AsyncMock(return_value=mock_response)
        
        with patch("asyncio.to_thread", side_effect=lambda f, *args, **kwargs: f(*args, **kwargs)):
            result = await scraper.scrape_tool()
        
        assert isinstance(result, list)
        assert len(result) > 0
        
        await scraper.close()

    @pytest.mark.asyncio
    async def test_scrape_tool_deduplication(self, mock_env_vars, mock_galaxy_instance, sample_tool_xml):
        """Test that duplicate tools are deduplicated."""
        scraper = GalaxyToolScraper()
        
        # Same tool in different categories
        tools = [
            {
                "id": "tool1",
                "name": "Tool One",
                "description": "Test tool",
                "version": "1.0",
                "panel_section_name": "Category A"
            },
            {
                "id": "tool1",  # Duplicate
                "name": "Tool One",
                "description": "Test tool",
                "version": "1.0",
                "panel_section_name": "Category B"
            }
        ]
        
        mock_galaxy_instance.tools.get_tools = MagicMock(return_value=tools)
        
        mock_response = MagicMock()
        mock_response.text = sample_tool_xml
        mock_response.raise_for_status = MagicMock()
        scraper.client.get = AsyncMock(return_value=mock_response)
        
        with patch("asyncio.to_thread", side_effect=lambda f, *args, **kwargs: f(*args, **kwargs)):
            result = await scraper.scrape_tool()
        
        # Should only have one tool despite two entries
        assert len(result) == 1
        
        await scraper.close()



class TestGalaxyWorkflowScraper:
    """Tests for GalaxyWorkflowScraper class."""

    @pytest.mark.asyncio
    async def test_workflow_scraper_initialization(self, mock_env_vars):
        """Test GalaxyWorkflowScraper initialization."""
        scraper = GalaxyWorkflowScraper()
        
        assert scraper.github_token == "test-github-token"
        assert "Authorization" in scraper.headers
        assert scraper.headers["Authorization"] == "token test-github-token"

    @pytest.mark.asyncio
    async def test_workflow_scraper_initialization_no_token(self):
        """Test initialization without GitHub token."""
        with patch.dict("os.environ", {"GITHUB_TOKEN": ""}, clear=False):
            with patch("app.bioblend_server.informer.scrapers.workflow_scraper.load_dotenv"):
                scraper = GalaxyWorkflowScraper()
                
                assert scraper.github_token == "" or scraper.github_token is None
                assert scraper.headers == {}

    @pytest.mark.asyncio
    async def test_parse_ga_content_success(self, mock_env_vars, sample_workflow_ga):
        """Test parsing .ga workflow content."""
        scraper = GalaxyWorkflowScraper()
        
        result = await scraper.parse_ga_content(json.dumps(sample_workflow_ga))
        
        assert result["workflow_name"] == "Test Workflow"
        assert result["owner"] == "test_user"
        assert result["number_of_steps"] == 2
        assert len(result["tools_used"]) == 2
        assert result["tools_used"][0]["name"] == "Tool One"

    @pytest.mark.asyncio
    async def test_parse_ga_content_dict_input(self, mock_env_vars, sample_workflow_ga):
        """Test parsing .ga content when input is already a dict."""
        scraper = GalaxyWorkflowScraper()
        
        result = await scraper.parse_ga_content(sample_workflow_ga)
        
        assert result["workflow_name"] == "Test Workflow"
        assert result["number_of_steps"] == 2

    @pytest.mark.asyncio
    async def test_parse_ga_content_invalid_json(self, mock_env_vars):
        """Test parsing invalid JSON."""
        scraper = GalaxyWorkflowScraper()
        
        result = await scraper.parse_ga_content("invalid json {")
        
        assert result["workflow_name"] == "unknown"
        assert result["number_of_steps"] == 0
        assert result["tools_used"] == []

    @pytest.mark.asyncio
    async def test_parse_ga_content_no_tools(self, mock_env_vars):
        """Test parsing workflow with no tool steps."""
        scraper = GalaxyWorkflowScraper()
        
        ga_content = {
            "name": "Empty Workflow",
            "steps": {
                "0": {"type": "data_input"},
                "1": {"type": "data_collection_input"}
            }
        }
        
        result = await scraper.parse_ga_content(ga_content)
        
        assert result["workflow_name"] == "Empty Workflow"
        assert result["number_of_steps"] == 2
        assert len(result["tools_used"]) == 0

    @pytest.mark.asyncio
    async def test_github_api_get_success(self, mock_env_vars):
        """Test successful GitHub API request."""
        scraper = GalaxyWorkflowScraper()
        
        mock_response = MagicMock()
        mock_response.text = '{"key": "value"}'
        mock_response.json.return_value = {"key": "value"}
        mock_response.raise_for_status = MagicMock()
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client
            
            result = await scraper.github_api_get("http://test.com")
            
            assert result == {"key": "value"}


    @pytest.mark.asyncio
    async def test_github_api_get_empty_response(self, mock_env_vars):
        """Test GitHub API request with empty response."""
        scraper = GalaxyWorkflowScraper()
        
        mock_response = MagicMock()
        mock_response.text = ""
        mock_response.raise_for_status = MagicMock()
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client
            
            result = await scraper.github_api_get("http://test.com")
            
            assert result == ""

    @pytest.mark.asyncio
    async def test_github_api_get_http_error(self, mock_env_vars):
        """Test GitHub API request with HTTP error."""
        scraper = GalaxyWorkflowScraper()
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("HTTP Error"))
            mock_client_class.return_value = mock_client
            
            result = await scraper.github_api_get("http://test.com")
            
            assert result is None

    @pytest.mark.asyncio
    async def test_clean_readme(self, mock_env_vars):
        """Test README cleaning."""
        scraper = GalaxyWorkflowScraper()
        
        dirty_text = """
        <html>Some HTML</html>
        ===== Header =====
        │ Table │ Content │
        **Bold** and _italic_ text
        [Link](url)
        Multiple    spaces
        """
        
        clean = scraper.clean_readme(dirty_text)
        
        assert "<html>" not in clean
        assert "│" not in clean
        assert "**" not in clean
        assert "[" not in clean
        assert "  " not in clean  # Multiple spaces should be collapsed

    @pytest.mark.asyncio
    async def test_clean_readme_non_string(self, mock_env_vars):
        """Test README cleaning with non-string input."""
        scraper = GalaxyWorkflowScraper()
        
        result = scraper.clean_readme(None)
        assert result == ""
        
        result = scraper.clean_readme(123)
        assert result == ""

    @pytest.mark.asyncio
    async def test_preprocess_scraped(self, mock_env_vars):
        """Test preprocessing of scraped workflow data."""
        scraper = GalaxyWorkflowScraper()
        
        raw_data = [
            {
                "category": "genomics",
                "workflow_repository": "test-repo",
                "workflow_files": [
                    {
                        "workflow_name": "Test Workflow",
                        "owner": "test_owner",
                        "description": "Test description",
                        "raw_download_url": "http://test.com/workflow.ga",
                        "tools_used": [
                            {"name": "Tool1"},
                            {"name": "Tool2"},
                            {"name": "Tool1"}  # Duplicate
                        ]
                    }
                ],
                "readme_content": "# Test README\nSome content"
            }
        ]
        
        result = scraper.preprocess_scraped(raw_data)
        
        assert len(result) == 1
        assert result[0]["name"] == "Test Workflow"
        assert result[0]["source"] == "iwc"
        assert "Tool1" in result[0]["content"]
        assert "Tool2" in result[0]["content"]
        assert "content" in result[0]


    @pytest.mark.asyncio
    async def test_preprocess_scraped_empty_workflow_name(self, mock_env_vars):
        """Test preprocessing with empty workflow name."""
        scraper = GalaxyWorkflowScraper()
        
        raw_data = [
            {
                "category": "genomics",
                "workflow_repository": "test-repo",
                "workflow_files": [
                    {
                        "workflow_name": "",
                        "owner": "",
                        "description": "",
                        "raw_download_url": "",
                        "tools_used": []
                    }
                ],
                "readme_content": ""
            }
        ]
        
        result = scraper.preprocess_scraped(raw_data)
        
        assert len(result) == 1
        assert result[0]["name"] == "test-repo"  # Falls back to repo name

    @pytest.mark.asyncio
    async def test_scan_repo_with_ga_files(self, mock_env_vars, sample_workflow_ga):
        """Test scanning repository with .ga files."""
        scraper = GalaxyWorkflowScraper()
        
        # Mock repo contents
        repo_contents = [
            {"name": "workflow1.ga", "type": "file"},
            {"name": "README.md", "type": "file"},
            {"name": "test-data", "type": "dir"}
        ]
        
        # Mock GitHub API responses
        async def mock_github_api_get(url):
            if "workflow1.ga" in url:
                return json.dumps(sample_workflow_ga)
            elif "README.md" in url:
                return "# Test README"
            else:
                return repo_contents
        
        scraper.github_api_get = mock_github_api_get
        
        result = await scraper.scan_repo("", "test-repo")
        
        assert result is not None
        assert len(result) > 0
        assert result[0]["workflow_repository"] == "test-repo"
        assert len(result[0]["workflow_files"]) > 0

    @pytest.mark.asyncio
    async def test_scan_repo_error_handling(self, mock_env_vars):
        """Test error handling in scan_repo."""
        scraper = GalaxyWorkflowScraper()
        
        # Mock GitHub API to raise error
        scraper.github_api_get = AsyncMock(side_effect=Exception("API Error"))
        
        result = await scraper.scan_repo("", "test-repo")
        
        assert result is None

    @pytest.mark.asyncio
    async def test_scrape_workflows_success(self, mock_env_vars, sample_workflow_ga):
        """Test successful workflow scraping."""
        scraper = GalaxyWorkflowScraper()
        
        # Mock scan_repo to return sample data
        mock_repo_data = [
            {
                "category": "genomics",
                "workflow_repository": "test-repo",
                "workflow_files": [
                    {
                        "workflow_name": "Test Workflow",
                        "owner": "test_owner",
                        "description": "Test description",
                        "raw_download_url": "http://test.com/workflow.ga",
                        "tools_used": [{"name": "Tool1"}]
                    }
                ],
                "readme_content": "Test README"
            }
        ]
        
        scraper.scan_repo = AsyncMock(return_value=mock_repo_data)
        
        result = await scraper.scrape_workflows()
        
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0]["source"] == "iwc"

    @pytest.mark.asyncio
    async def test_scrape_workflows_no_data(self, mock_env_vars):
        """Test workflow scraping with no data."""
        scraper = GalaxyWorkflowScraper()
        
        scraper.scan_repo = AsyncMock(return_value=None)
        
        with pytest.raises(ValueError, match="No workflows scraped"):
            await scraper.scrape_workflows()



class TestWorkflowHubScraper:
    """Tests for WorkflowHubScraper class."""

    @pytest.mark.asyncio
    async def test_workflowhub_scraper_initialization(self):
        """Test WorkflowHubScraper initialization."""
        scraper = WorkflowHubScraper()
        
        assert scraper.base_url is not None
        assert scraper.trs_base_url is not None

    @pytest.mark.asyncio
    async def test_get_success(self):
        """Test successful _get request."""
        scraper = WorkflowHubScraper()
        
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"key": "value"}
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        
        result = await scraper._get(mock_client, "http://test.com")
        
        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_get_http_error(self):
        """Test _get with HTTP error."""
        scraper = WorkflowHubScraper()
        
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("HTTP Error"))
        
        result = await scraper._get(mock_client, "http://test.com")
        
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_workflow_details_success(self):
        """Test fetching workflow details."""
        scraper = WorkflowHubScraper()
        scraper.api_url = "http://test.com/api"
        
        mock_data = {
            "data": {
                "id": "123",
                "attributes": {
                    "title": "Test Workflow",
                    "description": "Test description",
                    "tags": ["genomics", "rna-seq"],
                    "versions": [1, 2, 3],
                    "latest_version": 3
                }
            }
        }
        
        mock_client = AsyncMock()
        scraper._get = AsyncMock(return_value=mock_data)
        
        result = await scraper.fetch_workflow_details(mock_client, "123")
        
        assert result is not None
        assert result["id"] == "123"
        assert result["title"] == "Test Workflow"
        assert result["latest_version"] == 3
        assert "web_url" in result
        assert "download_url" in result

    @pytest.mark.asyncio
    async def test_fetch_workflow_details_no_data(self):
        """Test fetching workflow details with no data."""
        scraper = WorkflowHubScraper()
        scraper.api_url = "http://test.com/api"
        
        mock_client = AsyncMock()
        scraper._get = AsyncMock(return_value=None)
        
        result = await scraper.fetch_workflow_details(mock_client, "123")
        
        assert result is None

    @pytest.mark.asyncio
    async def test_preprocess_workflowhub_data(self):
        """Test preprocessing WorkflowHub data."""
        scraper = WorkflowHubScraper()
        
        raw_items = [
            {
                "id": "wf1",
                "name": "Test Workflow",
                "description": "Test description",
                "organization": "Test Org",
                "url": "http://test.com/wf1",
                "versions": [
                    {"id": "v1"},
                    {"id": "v2"}
                ]
            }
        ]
        
        result = scraper.preprocess_workflowhub_data(raw_items)
        
        assert len(result) == 1
        assert result[0]["name"] == "Test Workflow"
        assert result[0]["source"] == "workflow_hub"
        assert result[0]["owner"] == "Test Org"
        assert "raw_download_url" in result[0]
        assert "content" in result[0]


    @pytest.mark.asyncio
    async def test_preprocess_workflowhub_data_no_versions(self):
        """Test preprocessing with no versions."""
        scraper = WorkflowHubScraper()
        
        raw_items = [
            {
                "id": "wf1",
                "name": "Test Workflow",
                "description": "Test description",
                "versions": []  # No versions
            }
        ]
        
        result = scraper.preprocess_workflowhub_data(raw_items)
        
        # Should skip workflows with no versions
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_preprocess_workflowhub_data_missing_name(self):
        """Test preprocessing with missing name."""
        scraper = WorkflowHubScraper()
        
        raw_items = [
            {
                "id": "wf1",
                "description": "Test description",
                "versions": [{"id": "v1"}]
            }
        ]
        
        result = scraper.preprocess_workflowhub_data(raw_items)
        
        assert len(result) == 1
        assert result[0]["name"] == "wf1"  # Falls back to ID

    @pytest.mark.asyncio
    async def test_scrape_workflows_success(self):
        """Test successful WorkflowHub scraping."""
        scraper = WorkflowHubScraper()
        
        mock_tools = [
            {
                "id": "wf1",
                "name": "Test Workflow",
                "description": "Test description",
                "organization": "Test Org",
                "url": "http://test.com/wf1",
                "versions": [
                    {
                        "id": "v1",
                        "descriptor_type": ["GALAXY"]
                    }
                ]
            },
            {
                "id": "wf2",
                "name": "Non-Galaxy Workflow",
                "versions": [
                    {
                        "id": "v1",
                        "descriptor_type": ["CWL"]  # Not Galaxy
                    }
                ]
            }
        ]
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client
            
            scraper._get = AsyncMock(return_value=mock_tools)
            
            result = await scraper.scrape_workflows()
            
            # Should only return Galaxy workflows
            assert len(result) == 1
            assert result[0]["name"] == "Test Workflow"

    @pytest.mark.asyncio
    async def test_scrape_workflows_no_tools(self):
        """Test scraping with no tools returned."""
        scraper = WorkflowHubScraper()
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client
            
            scraper._get = AsyncMock(return_value=None)
            
            result = await scraper.scrape_workflows()
            
            assert result == []

    @pytest.mark.asyncio
    async def test_scrape_workflows_galaxy_descriptor_string(self):
        """Test scraping with GALAXY as string instead of list."""
        scraper = WorkflowHubScraper()
        
        mock_tools = [
            {
                "id": "wf1",
                "name": "Test Workflow",
                "versions": [
                    {
                        "id": "v1",
                        "descriptor_type": "GALAXY"  # String instead of list
                    }
                ]
            }
        ]
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client
            
            scraper._get = AsyncMock(return_value=mock_tools)
            
            result = await scraper.scrape_workflows()
            
            assert len(result) == 1

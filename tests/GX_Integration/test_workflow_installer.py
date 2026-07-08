import pytest

from unittest.mock import AsyncMock, MagicMock, patch

from app.GX_integration.workflows.worklfow_installer import WorkflowInstaller


@pytest.fixture
def mock_galaxy_client():
    mock_client = MagicMock()
    mock_client.gi_object = MagicMock()
    mock_client.gi_admin = MagicMock()
    mock_client.admin_api_key = "admin-key"
    mock_client.galaxy_url = "https://galaxy.example"
    return mock_client


@pytest.fixture
def workflow_installer(mock_galaxy_client):
    return WorkflowInstaller(mock_galaxy_client)


def _step():
    return {
        "id": "1",
        "tool_id": "toolshed.g2.bx.psu.edu/repos/dev/tool/tool/1.0",
        "tool_shed_repository": {
            "tool_shed": "toolshed.g2.bx.psu.edu",
            "name": "tool",
            "owner": "dev",
            "changeset_revision": "rev123",
        },
    }


@pytest.mark.asyncio
async def test_ensure_repository_installable_repairs_deleted_repo(workflow_installer):
    step = _step()
    repo = {
        "id": "repo-1",
        "name": "tool",
        "owner": "dev",
        "changeset_revision": "rev123",
        "status": "Installed",
        "deleted": True,
        "uninstalled": False,
    }
    workflow_installer.gi_admin.gi.toolShed.get_repositories.return_value = [repo]

    with patch("app.GX_integration.workflows.worklfow_installer.httpx.AsyncClient") as mock_client_cls, \
         patch.object(workflow_installer, "_tool_exists", new=AsyncMock(side_effect=[False, True])), \
         patch.object(workflow_installer, "_reload_toolbox", new=AsyncMock()) as mock_reload, \
         patch("app.GX_integration.workflows.worklfow_installer.asyncio.sleep", new=AsyncMock()):
        mock_client = MagicMock()
        mock_client.post = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        await workflow_installer._ensure_repository_installable(step, step["tool_shed_repository"])

    mock_client.post.assert_awaited_once()
    mock_reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_repository_installable_skips_repair_when_repo_healthy(workflow_installer):
    step = _step()
    repo = {
        "id": "repo-1",
        "name": "tool",
        "owner": "dev",
        "changeset_revision": "rev123",
        "status": "Installed",
        "deleted": False,
        "uninstalled": False,
    }
    workflow_installer.gi_admin.gi.toolShed.get_repositories.return_value = [repo]

    with patch("app.GX_integration.workflows.worklfow_installer.httpx.AsyncClient") as mock_client_cls, \
         patch.object(workflow_installer, "_tool_exists", new=AsyncMock(return_value=True)):
        await workflow_installer._ensure_repository_installable(step, step["tool_shed_repository"])

    mock_client_cls.assert_not_called()

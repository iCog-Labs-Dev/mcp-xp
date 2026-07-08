import logging
import asyncio
import traceback

import httpx
from dotenv import load_dotenv

load_dotenv()

from sys import path
path.append('.')

from bioblend.galaxy.toolshed import ToolShedClient

from app.galaxy import GalaxyClient
from app.api.socket_manager import SocketManager
from app.enumerations import (
    NumericLimits,
    SocketMessageEvent,
    SocketMessageType
    )

class WorkflowInstaller:
    """Handles tool installation and workflow upload operations"""

    UNHEALTHY_REPOSITORY_STATUSES = {"Error", "Uninstalled", "New"}
    
    def __init__(self, galaxy_client: GalaxyClient):

        self.galaxy_client = galaxy_client

        self.gi_object=self.galaxy_client.gi_object 
        self.gi_admin = self.galaxy_client.gi_admin # For administrative functionalitites like toolshed instantiation and tool installing
        self.toolshed=ToolShedClient(self.gi_admin.gi)    # Toolshed instance

        self.log = logging.getLogger(__class__.__name__)
    
    
    async def _install_galaxy_tool(self, toolshed_info, checker = False):
        """install a tool to galaxy using a toolshed information of a galaxy worklfow ga file."""
        
        try:
            
            install_result = await asyncio.to_thread( self.toolshed.install_repository_revision,
                                tool_shed_url=f'https://{toolshed_info["tool_shed"]}',
                                name=toolshed_info["name"],
                                owner=toolshed_info["owner"],
                                changeset_revision=toolshed_info["changeset_revision"],
                                install_tool_dependencies=True,
                                install_repository_dependencies=True,
                                install_resolver_dependencies=True,
                                tool_panel_section_id=None,
                                new_tool_panel_section_label=None
                            )
            
        except Exception as e:
            
            if not checker:
                self.log.warning(f"tool installation failed, trying again. {e}")
                install_result = await self._install_galaxy_tool(toolshed_info, checker = True)
                return install_result
            else:
                self.log.error(f" tool installation failing: {e}")
                raise
            
        return install_result

    async def _reload_toolbox(self):
        """ Reload the galaxy toolbox. """
        
        # Reload the tool box after tools are installed
        await asyncio.sleep(NumericLimits.SHORT_SLEEP)
        await asyncio.to_thread(self.gi_admin.gi.config.reload_toolbox)
        
    async def _tool_exists(self, step: dict) -> bool:
        """Checks if a specific version of a tool is installed within the galaxy instance"""

        tool_id = step.get('tool_id')
        if not tool_id:
            return True

        try:
            tool = await asyncio.to_thread(self.gi_admin.gi.tools.show_tool, tool_id)
        except Exception as e:
            self.log.debug(f"Could not find tool in search: {e}")
            return False
        if not tool:
            return False

        # Grab repository info (None if local tool)
        step_repo = step.get('tool_shed_repository')
        tool_repo = tool.get('tool_shed_repository')
        
        # If the step was defined to come from a Tool Shed, enforce that
        if step_repo:
            # tool must also be from a Tool Shed
            if not tool_repo:
                return False
            # revisions must match exactly
            if tool_repo.get('changeset_revision') != step_repo.get('changeset_revision'):
                return False

        return True

    async def _ensure_repository_installable(self, step: dict, toolshed_info: dict) -> None:
        """Detect 'ghost installs' before attempting install.

        Galaxy's `tool_shed_repositories` table can mark a repository revision as
        `Installed` even when files were never downloaded, conda env never resolved,
        or the install was interrupted. In those cases, `install_repository_revision`
        silently no-ops because the DB row already exists.

        We cross-check the DB status against `_tool_exists` (which queries the
        in-memory toolbox). If the row claims Installed but the tool isn't actually
        loaded, OR the row is in an explicitly bad state (Error/Uninstalled/New),
        we call `repair_repository_revision` to force Galaxy to re-fetch files and
        re-resolve dependencies before our own install call runs."""
        try:
            # gi.toolShed (camelCase) is bioblend's local installed-repos client.
            # gi.toolshed (lowercase) is for the remote Tool Shed and lacks
            # installed-repo introspection. (Attribute name varies by bioblend
            # version; toolShed is what this codebase ships with.)
            repos = await asyncio.to_thread(
                self.gi_admin.gi.toolShed.get_repositories
            )
            for r in repos:
                if not (r.get('name') == toolshed_info['name']
                        and r.get('owner') == toolshed_info['owner']
                        and r.get('changeset_revision') == toolshed_info['changeset_revision']):
                    continue

                status = r.get('status', '')
                tool_actually_present = await self._tool_exists(step)

                repo_deleted = bool(r.get('deleted'))
                repo_uninstalled = bool(r.get('uninstalled'))
                needs_repair = (
                    repo_deleted
                    or repo_uninstalled
                    or status in self.UNHEALTHY_REPOSITORY_STATUSES
                    or (status == 'Installed' and not tool_actually_present)
                )

                if needs_repair:
                    self.log.warning(
                        f"Repo {toolshed_info['name']}@{toolshed_info['changeset_revision']} "
                        f"status='{status}' deleted={repo_deleted} uninstalled={repo_uninstalled} "
                        f"tool_present={tool_actually_present} — repairing"
                    )
                    # bioblend in this version lacks `repair_repository_revision`,
                    # so call Galaxy's HTTP API directly. The endpoint forces a
                    # re-fetch of files and re-resolution of conda envs for the
                    # specified changeset, regardless of the stale DB row.
                    try:
                        admin_key = self.galaxy_client.admin_api_key
                        galaxy_url = self.galaxy_client.galaxy_url
                        # Galaxy's repair route requires the repo id in the path:
                        #   POST /api/tool_shed_repositories/{id}/repair_repository_revision
                        # (per https://galaxyproject.org/toolshed/api/)
                        repo_id = r.get('id')
                        if not repo_id:
                            raise RuntimeError(
                                f"Cannot repair {toolshed_info['name']}@{toolshed_info['changeset_revision']}: "
                                "matching repo row has no 'id' field."
                            )
                        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                            resp = await client.post(
                                f"{galaxy_url}/api/tool_shed_repositories/{repo_id}/repair_repository_revision",
                                headers={"x-api-key": admin_key},
                                json={
                                    "tool_shed_url": f'https://{toolshed_info["tool_shed"]}',
                                    "name": toolshed_info['name'],
                                    "owner": toolshed_info['owner'],
                                    "changeset_revision": toolshed_info['changeset_revision'],
                                },
                            )
                            resp.raise_for_status()
                            self.log.info(
                                f"Repair API call accepted for "
                                f"{toolshed_info['name']}@{toolshed_info['changeset_revision']}"
                            )
                        for _ in range(6):
                            await self._reload_toolbox()
                            if await self._tool_exists(step):
                                self.log.info(
                                    f"Repair restored tool visibility for "
                                    f"{toolshed_info['name']}@{toolshed_info['changeset_revision']}"
                                )
                                return
                            await asyncio.sleep(10)
                    except Exception as repair_err:
                        self.log.warning(
                            f"Repair HTTP call failed for "
                            f"{toolshed_info['name']}@{toolshed_info['changeset_revision']}: {repair_err}. "
                            f"Falling through to install attempt."
                        )
                return
        except Exception as e:
            # Best-effort: if we can't enumerate repos, don't block the install path.
            self.log.debug(f"Repo state check skipped: {e}")

    # Function that installs tools missing in the galaxy instance for the workflow invocation
    # Need  administrator api
    async def _tool_check_install(self, step: dict, ws_manager: SocketManager, tracker_id: str):
        """Check and install if a tool in a workflow is missing.

        After install, re-verifies the exact revision is present by reloading the toolbox
        and re-checking up to 6 times (~60s). The Tool Shed API can return "already
        installed" while a different revision is the one actually installed, so the
        response message alone is not trustworthy."""

        # Recurse into subworkflow steps
        if step.get('type') == 'subworkflow':
            for sub_step in step['subworkflow']['steps'].values():
                await self._tool_check_install(sub_step, ws_manager, tracker_id)
            return  # Skip install for subworkflow container itself

        # Skip steps without a tool_id
        if not step.get('tool_id'):
            return

        # Already present at the right revision — nothing to do
        if await self._tool_exists(step):
            return

        toolshed_info = step['tool_shed_repository']
        self.log.info(f"Installing tool for step {step['id']}: {step['tool_id']}")

        # Repair stale/ghost DB rows so the install call below actually runs
        # instead of being silently shortcut by the Tool Shed API.
        await self._ensure_repository_installable(step, toolshed_info)

        try:
            await self._install_galaxy_tool(toolshed_info)
        except Exception as e:
            self.log.error(f"Failed to install tool '{toolshed_info['name']}': {str(e)}  traceback:{traceback.format_exc()}")
            if ws_manager:
                await ws_manager.broadcast(
                    event=SocketMessageEvent.workflow_upload.value,
                    data={
                        "type": SocketMessageType.TOOL_INSTALL.value,
                        "payload": {"message": f"Failed to install {toolshed_info['name']}: {e}"}
                    },
                    tracker_id=tracker_id,
                )
            raise

        # Post-install verification: reload toolbox and confirm the exact
        # revision is present. Galaxy may need a moment for conda resolution.
        for attempt in range(6):  # up to ~60s total
            await self._reload_toolbox()
            if await self._tool_exists(step):
                self.log.info(f"Verified install of {step['tool_id']}")
                if ws_manager:
                    await ws_manager.broadcast(
                        event=SocketMessageEvent.workflow_upload.value,
                        data={
                            "type": SocketMessageType.TOOL_INSTALL.value,
                            "payload": {"message": f"Tool Name: {toolshed_info.get('name', 'N/A')}, installed successfully."}
                        },
                        tracker_id=tracker_id,
                    )
                return
            await asyncio.sleep(10)

        msg = (
            f"Tool {step['tool_id']} not present after install + 60s wait. "
            f"A different revision of '{toolshed_info['name']}' may be installed; "
            f"uninstall the existing revision and retry, or install manually."
        )
        self.log.error(msg)
        if ws_manager:
            await ws_manager.broadcast(
                event=SocketMessageEvent.workflow_upload.value,
                data={
                    "type": SocketMessageType.TOOL_INSTALL.value,
                    "payload": {"message": msg}
                },
                tracker_id=tracker_id,
            )
        raise RuntimeError(msg)
    
    async def upload_workflow(self, workflow_json: dict, ws_manager: SocketManager = None, tracker_id: str = None, retry_count: int = 1, installer_count = 1):
        """Upload workflow from a ga file json."""
        
        semaphore = asyncio.Semaphore(installer_count) # Limit semaphores for tool installation.
        async def limited_install(step):
            async with semaphore:
                return await self._tool_check_install(step, ws_manager, tracker_id)

        if ws_manager:
            await ws_manager.broadcast(
                event = SocketMessageEvent.workflow_upload.value,
                data = {
                    "type": SocketMessageType.UPLOAD_WORKFLOW.value,
                    "payload": {"message": "Workflow upload started, checking and installing missing tools."}
                    },
                tracker_id=tracker_id
            )
        self.log.info("Workflow upload started, checking and installing missing tools.")
        # Check if the tools are installed and install all missing tools
        try:
            workflow_steps=workflow_json.get('steps', None)

            if workflow_steps:
                    await asyncio.gather(*[limited_install(step) for step in workflow_steps.values()])
                    
        except Exception as e:
            if ws_manager:
                await ws_manager.broadcast(
                    event = SocketMessageEvent.workflow_upload.value,
                    data = {
                        "type": SocketMessageType.UPLOAD_FAILURE.value,
                        "payload": {"message": f"Error installing missing tools in the uploaded workflow: {e}"}
                        },
                    tracker_id=tracker_id
                )
            self.log.error(f"Error installing missing tools in the uploaded workflow: {e} traceback:{traceback.format_exc()}")
            
            # return {"error": f"Error installing missing tools in the uploaded workflow: {e}"}
            
        await self._reload_toolbox()

        workflow = await asyncio.to_thread(
            self.gi_object.workflows.import_new, 
            src=workflow_json, 
            publish=False
            )
        
        # Extract workflow id for uploaded worklflow checking.
        workflow_id = workflow.id
        
        retry_count += 1
        # Check if the workflow is considered runnable by the instance
        if workflow.is_runnable:
            if ws_manager:
                self.log.info("workflow Uploaded successfully")
                await ws_manager.broadcast(
                    event = SocketMessageEvent.workflow_upload.value,
                    data = {
                        "type": SocketMessageType.UPLOAD_COMPLETE.value,
                        "payload": {"message": "Workflow successfully uploaded."}
                        },
                    tracker_id=tracker_id
                    )
        else:
            self.log.debug("Workflow is not runnable, deleting failed workflow.")
            await asyncio.to_thread(self.gi_object.gi.workflows.delete_workflow, workflow_id=workflow_id)
            if retry_count > 3:
                self.log.error("Workflow is not runnable, failed to upload correctly.")
                if ws_manager:
                    await ws_manager.broadcast(
                        event = SocketMessageEvent.workflow_upload.value,
                        data = {
                            "type": SocketMessageType.UPLOAD_FAILURE.value,
                            "payload": {"message": "Workflow upload failed."}
                            },
                        tracker_id=tracker_id
                        )
            else:
                self.log.error(f"Workflow is not runnable, failed to upload correctly. Retrying... (attempt {retry_count})")
                await asyncio.sleep(NumericLimits.SHORT_SLEEP.value)
                await self.upload_workflow(workflow_json=workflow_json, ws_manager=ws_manager, tracker_id=tracker_id, retry_count=retry_count)

import pytest
import logging
import itertools

from unittest.mock import Mock, AsyncMock, MagicMock, patch, ANY
from sys import path

path.append(".")

from app.log_setup import configure_logging
from app.GX_integration.workflows.workflow_manager import WorkflowManager
from app.enumerations import JobState

configure_logging()


@pytest.fixture
def upload_test_log():
    return logging.getLogger("TestWorkflowManagerUploadWorkflow")


@pytest.fixture
def tracker_test_log():
    return logging.getLogger("TestWorkflowManagerTrackInvocation")


@pytest.fixture
def mock_galaxy_client():
    with patch(
        "app.GX_integration.workflows.workflow_manager.GalaxyClient"
    ) as mock_class:
        # Create a mock instance of GalaxyClient
        mock_instance = MagicMock()

        mock_instance.gi_object = MagicMock()
        mock_instance.gi_admin = MagicMock()

        # Set the patched class to return the mock instance when called
        mock_class.return_value = mock_instance

        yield mock_instance


@pytest.fixture
def mock_tool_manager():
    return Mock()


@pytest.fixture
def mock_toolshed_client():
    return Mock()


@pytest.fixture
def mock_socket_manager():
    mock_ws = AsyncMock()
    mock_ws.broadcast = AsyncMock()
    return mock_ws


@pytest.fixture
def workflow_manager(mock_galaxy_client, mock_tool_manager, mock_toolshed_client):
    manager = WorkflowManager(galaxy_client=mock_galaxy_client)
    manager.tool_manager = mock_tool_manager
    manager.toolshed = mock_toolshed_client
    return manager


class TestWorkflowManagerUploadWorkflow:
    """Unit tests for upload_workflow method"""

    @pytest.mark.asyncio
    async def test_upload_workflow_success_no_missing_tools(
        self, workflow_manager, mock_socket_manager, upload_test_log
    ):
        """Test successful workflow upload with no missing tools"""

        upload_test_log.info(
            "TEST: test_upload_workflow_success_no_missing_tools starting."
        )

        workflow_json = {
            "steps": {"1": {"tool_id": "existing_tool", "tool_shed_repository": None}}
        }
        mock_galaxy_client = workflow_manager.galaxy_client
        mock_gi_object = mock_galaxy_client.gi_object
        mock_gi_admin = mock_galaxy_client.gi_admin

        # Mock toolbox reload
        mock_gi_admin.gi.config.reload_toolbox.return_value = None

        # Mock workflow import
        mock_workflow = Mock()
        mock_workflow.is_runnable = True
        mock_gi_object.workflows.import_new.return_value = mock_workflow

        with patch.object(
            workflow_manager.workflow_installer,
            "_tool_check_install",
            new_callable=AsyncMock,
        ) as mock_tool_check_install:
            # Execute
            result = await workflow_manager.upload_workflow(
                workflow_json=workflow_json,
                ws_manager=mock_socket_manager,
                tracker_id="test_tracker",
            )

            # Assert
            mock_tool_check_install.assert_called_once_with(
                {"tool_id": "existing_tool", "tool_shed_repository": None},
                mock_socket_manager,
                "test_tracker",
            )
            mock_gi_admin.gi.config.reload_toolbox.assert_called_once()
            mock_gi_object.workflows.import_new.assert_called_once_with(
                src=workflow_json, publish=False
            )
            mock_socket_manager.broadcast.assert_any_call(
                event=ANY,
                data={
                    "type": "UPLOAD_WORKFLOW",
                    "payload": {
                        "message": "Workflow upload started, checking and installing missing tools."
                    },
                },
                tracker_id="test_tracker",
            )
            mock_socket_manager.broadcast.assert_any_call(
                event=ANY,
                data={
                    "type": "UPLOAD_COMPLETE",
                    "payload": {"message": "Workflow successfully uploaded."},
                },
                tracker_id="test_tracker",
            )

            upload_test_log.info(
                "TEST: test_upload_workflow_success_no_missing_tools PASSED."
            )

    @pytest.mark.asyncio
    async def test_upload_workflow_with_missing_tool_install_no_ws(
        self, workflow_manager, mock_toolshed_client, upload_test_log
    ):
        """Test workflow upload installs missing tool (ignore WebSocket side-effects)"""

        upload_test_log.info(
            "TEST: test_upload_workflow_with_missing_tool_install_no_ws starting."
        )
        # Arrange
        workflow_manager.workflow_installer.toolshed = mock_toolshed_client
        mock_toolshed_client.gi.url = (
            "http://test-toolshed"  # Fix for BioBlend URL join
        )
        mock_toolshed_client.module = "toolshed"  # Fix for BioBlend URL join
        mock_toolshed_client.install_repository_revision = Mock(
            return_value={
                "status": "installed",
                "message": "Tool installed successfully",
            }
        )

        workflow_manager.gi_admin.gi.config.reload_toolbox = Mock(return_value=None)
        mock_workflow = Mock(is_runnable=True)
        workflow_manager.gi_object.workflows.import_new = Mock(
            return_value=mock_workflow
        )

        workflow_json = {
            "steps": {
                "1": {
                    "id": "1",
                    "tool_id": "missing_tool",
                    "tool_shed_repository": {
                        "tool_shed": "test_shed",
                        "name": "test_repo",
                        "owner": "test_owner",
                        "changeset_revision": "rev123",
                    },
                }
            }
        }

        # Act
        with patch.object(
            workflow_manager.workflow_installer,
            "_tool_exists",
            new_callable=AsyncMock,
            return_value=False,
        ):
            await workflow_manager.upload_workflow(
                workflow_json=workflow_json,
                ws_manager=None,  # <— no WebSocket
                tracker_id=None,
            )

        # Assert
        mock_toolshed_client.install_repository_revision.assert_called_once_with(
            tool_shed_url="https://test_shed",
            name="test_repo",
            owner="test_owner",
            changeset_revision="rev123",
            install_tool_dependencies=True,
            install_repository_dependencies=True,
            install_resolver_dependencies=True,
            tool_panel_section_id=None,
            new_tool_panel_section_label=None,
        )

        assert workflow_manager.gi_admin.gi.config.reload_toolbox.call_count >= 1
        workflow_manager.gi_object.workflows.import_new.assert_called_once()
        upload_test_log.info(
            "TEST: test_upload_workflow_with_missing_tool_install_no_ws PASSED."
        )

    @pytest.mark.asyncio
    async def test_upload_workflow_not_runnable(
        self, workflow_manager, mock_socket_manager, upload_test_log
    ):
        """Test workflow upload where workflow is not runnable"""

        upload_test_log.info("TEST: test_upload_workflow_not_runnable starting.")

        workflow_json = {"steps": {}}
        mock_galaxy_client = workflow_manager.galaxy_client
        mock_gi_object = mock_galaxy_client.gi_object
        mock_gi_admin = mock_galaxy_client.gi_admin

        # Mock toolbox reload
        mock_gi_admin.gi.config.reload_toolbox.return_value = None

        # Mock workflow import - not runnable
        mock_workflow = Mock()
        mock_workflow.is_runnable = False
        mock_gi_object.workflows.import_new.return_value = mock_workflow

        with patch.object(
            workflow_manager.workflow_installer,
            "_tool_check_install",
            new_callable=AsyncMock,
        ) as mock_tool_check_install:
            # Execute
            result = await workflow_manager.upload_workflow(
                workflow_json=workflow_json,
                ws_manager=mock_socket_manager,
                tracker_id="test_tracker",
            )

            mock_tool_check_install.assert_not_called()
            mock_socket_manager.broadcast.assert_any_call(
                event=ANY,
                data={
                    "type": "UPLOAD_WORKFLOW",
                    "payload": {
                        "message": "Workflow upload started, checking and installing missing tools."
                    },
                },
                tracker_id="test_tracker",
            )
            # Verify no UPLOAD_COMPLETE (since not runnable)
            assert not any(
                "UPLOAD_COMPLETE" in call.kwargs["data"]["type"]
                for call in mock_socket_manager.broadcast.call_args_list
            )

        upload_test_log.info("TEST: test_upload_workflow_not_runnable PASSED.")


class TestWorkflowManagerTrackInvocation:
    """Unit tests for track_invocation method"""

    @pytest.mark.asyncio
    async def test_track_invocation_already_complete(
        self, workflow_manager, mock_socket_manager, tracker_test_log, caplog
    ):
        """Test tracking invocation that is already complete"""
        caplog.set_level(logging.INFO, logger="WorkflowManager")
        tracker_test_log.info("TEST: test_track_invocation_already_complete starting.")
        mock_invocation = Mock()
        mock_invocation.id = "inv_123"
        mock_invocation.cancel = Mock()

        mock_galaxy_client = workflow_manager.galaxy_client
        mock_gi_object = mock_galaxy_client.gi_object

        # Mock show_invocation - already succeeded
        mock_inv_data = {
            "state": "ok",
            "update_time": "2023-01-01T00:00:00",
            "steps": [],
            "inputs": [],
            "input_step_parameters": [],
        }
        mock_gi_object.gi.invocations.show_invocation.return_value = mock_inv_data

        # Mock step jobs summary - all ok
        mock_step_jobs = [
            {"id": "step_1", "states": {JobState.OK: 1}, "populated_state": JobState.OK}
        ]
        mock_gi_object.gi.invocations.get_invocation_step_jobs_summary.return_value = (
            mock_step_jobs
        )

        # Mock final outputs
        final_inv_data = {
            "outputs": {"out1": {"id": "ds_1"}},
            "output_collections": {"coll1": {"id": "coll_1"}},
            "update_time": "2023-01-01T00:00:00",
        }
        mock_gi_object.gi.invocations.show_invocation.side_effect = [
            mock_inv_data,
            final_inv_data,
        ]

        # Execute with invocation_check=True
        outputs, state, update_time = await workflow_manager.track_invocation(
            invocation=mock_invocation,
            tracker_id="test_tracker",
            ws_manager=mock_socket_manager,
            invocation_check=True,
        )

        # Assert
        assert state == "Complete"
        assert update_time == "2023-01-01T00:00:00"
        assert outputs == {
            "output_datasets": ["ds_1"],
            "collection_datasets": ["coll_1"],
        }
        tracker_test_log.info(caplog.text)
        assert (
            f"Outputted 1 dataset outputs and 1 collection from invocation"
            in caplog.text
        )

        tracker_test_log.info("TEST: test_track_invocation_already_complete PASSED.")

    @pytest.mark.asyncio
    async def test_track_invocation_already_failed(
        self, workflow_manager, mock_socket_manager, tracker_test_log
    ):
        """Test tracking invocation that is already failed"""

        tracker_test_log.info("TEST: test_track_invocation_already_failed starting.")

        mock_invocation = Mock()
        mock_invocation.id = "inv_456"
        mock_invocation.cancel = Mock()

        mock_galaxy_client = workflow_manager.galaxy_client
        mock_gi_object = mock_galaxy_client.gi_object

        # Mock show_invocation - failed
        mock_inv_data = {
            "state": "error",
            "update_time": "2023-01-01T00:00:00",
            "steps": [],
            "inputs": [],
            "input_step_parameters": [],
        }
        mock_gi_object.gi.invocations.show_invocation.return_value = mock_inv_data

        # Execute with invocation_check=True
        outputs, state, update_time = await workflow_manager.track_invocation(
            invocation=mock_invocation,
            tracker_id="test_tracker",
            ws_manager=mock_socket_manager,
            invocation_check=True,
        )

        # Assert
        assert state == "Failed"
        assert update_time == "2023-01-01T00:00:00"
        assert outputs == {"output_datasets": [], "collection_datasets": []}
        mock_socket_manager.broadcast.assert_called_once_with(
            event=ANY,
            data={
                "type": "INVOCATION_FAILURE",
                "payload": {"message": "Invocation failed or has error"},
            },
            tracker_id="test_tracker",
        )

        tracker_test_log.info("TEST: test_track_invocation_already_failed PASSED.")

    @pytest.mark.asyncio
    async def test_track_invocation_error_during_polling(
        self, workflow_manager, mock_socket_manager, tracker_test_log, caplog
    ):
        """Test error in a step during polling loop"""

        tracker_test_log.info(
            "TEST: test_track_invocation_error_during_polling starting."
        )

        mock_invocation = Mock()
        mock_invocation.id = "inv_789"
        mock_invocation.cancel = Mock()

        mock_galaxy_client = workflow_manager.galaxy_client
        mock_gi_object = mock_galaxy_client.gi_object

        # Initial show_invocation - pending
        initial_inv_data = {
            "state": "running",
            "update_time": "2023-01-01T00:00:00",
            "steps": [],
            "inputs": [],
            "input_step_parameters": [],
        }
        mock_gi_object.gi.invocations.show_invocation.side_effect = [initial_inv_data]

        # Step jobs with error
        error_step_jobs = [
            {
                "id": "step_1",
                "states": {JobState.ERROR: 1},
                "populated_state": JobState.ERROR,
            }
        ]
        mock_gi_object.gi.invocations.get_invocation_step_jobs_summary.return_value = (
            error_step_jobs
        )

        # Mock cancel task creation
        with patch("asyncio.create_task") as mock_create_task:
            # Execute
            outputs, state, _ = await workflow_manager.track_invocation(
                invocation=mock_invocation,
                tracker_id="test_tracker",
                ws_manager=mock_socket_manager,
                invocation_check=True,
            )

            # Assert
            assert state == "Failed"
            assert outputs == {"output_datasets": [], "collection_datasets": []}
            mock_create_task.assert_called_once()
            assert (
                "Step (ID: step_1) COMPLETELY FAILED - ALL 1 jobs failed. Cancelling invocation."
                in caplog.text
            )

        tracker_test_log.info(
            "TEST: test_track_invocation_error_during_polling PASSED."
        )

    @pytest.mark.asyncio
    async def test_track_invocation_timeout(
        self, workflow_manager, mock_socket_manager, tracker_test_log, caplog
    ):
        """Test invocation timeout during polling"""

        tracker_test_log.info("TEST: test_track_invocation_timeout starting.")
        caplog.set_level(logging.ERROR)

        def cancel_func():
            pass

        mock_invocation = Mock()
        mock_invocation.id = "inv_timeout"
        mock_invocation.cancel = cancel_func

        mock_galaxy_client = workflow_manager.galaxy_client
        mock_gi_object = mock_galaxy_client.gi_object

        # Mock persistent pending state
        pending_inv_data = {
            "state": "running",
            "update_time": "2023-01-01T00:00:00",
            "steps": [],
            "inputs": [],
            "input_step_parameters": [],
        }
        mock_gi_object.gi.invocations.show_invocation.return_value = pending_inv_data

        # Mock step jobs - no progress
        no_progress_jobs = [
            {"id": "step_1", "states": {}, "populated_state": JobState.WAITING}
        ]
        mock_gi_object.gi.invocations.get_invocation_step_jobs_summary.return_value = (
            no_progress_jobs
        )

        start_time = 1000.0
        hard_cap = 7 * 24 * 3600  # InvocationTracking.HARD_CAP

        # Use an iterator that repeats the last value to handle extra calls from logging
        time_side_effect = itertools.chain(
            [start_time], itertools.repeat(start_time + hard_cap + 100)
        )

        with (
            patch(
                "time.time",
                side_effect=time_side_effect,
            ),
            patch("asyncio.sleep", return_value=None),
            patch.object(
                workflow_manager.invocation_handler,
                "_cancel_invocation_background",
                new_callable=AsyncMock,
            ) as mock_cancel_bg,
        ):

            # Ensure the mock returns an awaitable that create_task can consume
            mock_cancel_bg.return_value = None

            # Execute
            _, state, _ = await workflow_manager.track_invocation(
                invocation=mock_invocation,
                tracker_id="test_tracker",
                ws_manager=mock_socket_manager,
                invocation_check=True,
            )

            # Assert timeout occurred
            assert state == "Failed"

            # Verify logs
            assert (
                f"Invocation stalled for {int(hard_cap + 100)} seconds; exceeding hard cap"
                in caplog.text
            )

            # Verify socket broadcast
            mock_socket_manager.broadcast.assert_called_with(
                event=ANY,
                data={
                    "type": "INVOCATION_FAILURE",
                    "payload": {"message": "Invocation timed out"},
                },
                tracker_id="test_tracker",
            )

            # Verify cancellation was called
            mock_cancel_bg.assert_called_once_with(mock_invocation)

        tracker_test_log.info("TEST: test_track_invocation_timeout PASSED.")

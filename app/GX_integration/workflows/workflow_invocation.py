from __future__ import annotations

import logging
import time
from typing import List, Union, Tuple, Optional, Literal, Dict
import json
import asyncio

from dotenv import load_dotenv

load_dotenv()

from sys import path
path.append('.')

from bioblend.galaxy.objects.wrappers import (
    Workflow,
    Invocation,
    History,
    Dataset,
    DatasetCollection
    )

from app.enumerations import (
    NumericLimits,
    InvocationTracking,
    JobState,
    InvocationStates,
    SocketMessageEvent,
    SocketMessageType
    )

from app.galaxy import GalaxyClient
from app.api.socket_manager import SocketManager

class WorkflowInvocationHandler:
    """Handles workflow invocation execution, monitoring, and result collection"""
    
    def __init__(self, galaxy_client: GalaxyClient):
        
        self.galaxy_client = galaxy_client
        self.gi_object=self.galaxy_client.gi_object 
        self.log = logging.getLogger(__class__.__name__)        
        self.semaphore = asyncio.Semaphore(NumericLimits.SEMAPHORE_LIMIT.value)
    

    def _analyze_step_jobs(self, step: dict) -> dict:
        """Analyze job states within a single step."""
        
        states = step.get('states', {})
        
        # Count jobs by state
        total_jobs = sum(states.values())
        
        if total_jobs == 0:
            return {
                'total_jobs': 0,
                'completed_jobs': 0,
                'failed_jobs': 0,
                'running_jobs': 0,
                'all_jobs_failed': False,
                'step_state': 'pending'
            }
        
        ok_count = states.get(JobState.OK, 0)
        skipped_count = states.get(JobState.SKIPPED, 0)
        failed_count = states.get(JobState.FAILED, 0)
        error_count = states.get(JobState.ERROR, 0)
        running_count = states.get(JobState.RUNNING, 0)
        
        completed_jobs = ok_count + skipped_count
        failed_jobs = failed_count + error_count
        
        # CRITICAL: All jobs failed check
        all_jobs_failed = (failed_jobs == total_jobs and total_jobs > 0)
        
        # Determine step state
        if all_jobs_failed:
            step_state = JobState.ERROR
        elif completed_jobs == total_jobs:
            step_state = JobState.OK
        elif running_count > 0:
            step_state = JobState.RUNNING
        elif failed_jobs > 0 and failed_jobs < total_jobs:
            # Partial failure - some jobs failed but not all
            step_state = JobState.RUNNING  # Still in progress
        else:
            step_state = 'pending'
        
        return {
            'total_jobs': total_jobs,
            'completed_jobs': completed_jobs,
            'failed_jobs': failed_jobs,
            'running_jobs': running_count,
            'all_jobs_failed': all_jobs_failed,
            'step_state': step_state
        }
        
    
    async def _fetch_with_semaphore(self, coro):
        """ Helper: Fetch with semaphore """
        
        async with self.semaphore:
            try:
                return await coro
            except Exception as e:  # Catch BioBlend errors like GalaxyRequestError
                self.log.error(f"API fetch error: {e}")
                return None
    
    async def _cancel_invocation_background(self, invocation: Invocation):
        """ Cancel invocations in the background. """
        
        await asyncio.to_thread(invocation.cancel)
        self.log.info(f"Invocation {invocation.id} has been cancelled.")
            
    # TODO(less priority): invocation tracker must also consider workflows that have PAUSE steps. But this can wait since pause steps are almost never used.
    async def track_invocation(self, 
                            invocation: Invocation,
                            tracker_id: str = None,
                            ws_manager: SocketManager = None,
                            invocation_check: bool = False
                        ) -> Tuple[
                            Dict[str,List],
                            Optional[Literal['Pending', 'Failed', 'Complete']],
                            Optional[str]
                        ]:
                            
        """ Tracks invocation steps and waits for the invocation reaches a terminal state and returns with the invocation results. """ 
        
        # Initialize tracking variables
        previous_states = {}
        
        output_ids=[]
        collection_output_ids=[]
        
        invocation_outputs = {
            "output_datasets": [],
            "collection_datasets": []
        }
        
        # Job-level tracking
        total_scheduled_jobs_in_invocation = 0
        completed_jobs_in_invocation = 0
        failed_jobs_in_invocation = 0
        step_job_tracking = {}

        inv = await self._fetch_with_semaphore(asyncio.to_thread(
            self.gi_object.gi.invocations.show_invocation, invocation_id=invocation.id
        ))
   
        # Variables for tracking the number of steps and completed steps
        num_input_steps = len(inv.get("inputs")) + len(inv.get("input_step_parameters"))
        num_steps = len(inv.get("steps")) - num_input_steps
        completed_step_count = 0
        
        # Polling and progress tracking.
        last_progress_at = time.time()
        poll_interval = InvocationTracking.POLL_FAST.value

        invocation_state_result = InvocationStates.FAILED.value

        # Explicit initial check before loop for already-completed/failed workflows
        if inv is None:
            self.log.error("Failed to fetch initial invocation state.")
            if invocation_check:
                return invocation_outputs, invocation_state_result, None
            else:
                return invocation_outputs
        
        invocation_update_time = inv.get("update_time", None)
        invocation_state = inv.get("state")
        
        self.log.debug(f"invocation details: {json.dumps(inv, indent=4)}")

        if invocation_state in ("failed", "error"):
            invocation_state_result = InvocationStates.FAILED.value
            
            self.log.error("workflow invocation has failed.")
            
            if ws_manager:
                ws_data = {
                    "type": SocketMessageType.INVOCATION_FAILURE.value,
                    "payload": {"message": "Invocation failed or has error"}
                }
                await ws_manager.broadcast(
                    event=SocketMessageEvent.workflow_execute.value, 
                    data=ws_data, 
                    tracker_id=tracker_id
                )
            if invocation_check:
                return invocation_outputs, invocation_state_result, invocation_update_time
            else:
                return invocation_outputs

        # Assume 'succeeded' or similar for terminal success; adjust based on your API
        if invocation_state in ("succeeded", "completed", "ok"):  # Added 'ok' as common Galaxy state for success
            self.log.info("Invocation already completed.")
            step_jobs_coro = asyncio.to_thread(
                self.gi_object.gi.invocations.get_invocation_step_jobs_summary, invocation_id=invocation.id
            )
            step_jobs = await self._fetch_with_semaphore(step_jobs_coro)
            if step_jobs is None:
                step_jobs = []  # Safety fallback
            self.log.debug(json.dumps(step_jobs, indent=4))
            # Proceed to process steps (will set all_ok and collect below)
        else:
            # Not terminal; fetch steps for initial poll
            step_jobs_coro = asyncio.to_thread(
                self.gi_object.gi.invocations.get_invocation_step_jobs_summary, invocation_id=invocation.id
            )
            step_jobs = await self._fetch_with_semaphore(step_jobs_coro)
            if step_jobs is None:
                step_jobs = []  # Safety fallback
            self.log.debug(json.dumps(step_jobs, indent=4))

        # Calculate total jobs on first fetch
        if total_scheduled_jobs_in_invocation == 0 and step_jobs:
            for step in step_jobs:
                states = step.get('states', {})
                step_total = sum(states.values())
                total_scheduled_jobs_in_invocation += step_total
                step_id = step.get('id')
                step_job_tracking[step_id] = {
                    'total': step_total,
                    'completed': 0,
                    'failed': 0,
                    'running': 0
                }
            self.log.info(f"Invocation currently has {total_scheduled_jobs_in_invocation} scheduled jobs across {num_steps} steps")

        # Initial processing of steps (handles already-completed case)
        all_ok = True
        progress_made = False
        has_error = False

        for step in step_jobs:
            step_id: str = step.get('id')
            
            # Analyze this step's jobs
            step_analysis = self._analyze_step_jobs(step)
            
            current_state = step_analysis['step_state']
            step_total_jobs = step_analysis['total_jobs']
            step_completed = step_analysis['completed_jobs']
            step_failed = step_analysis['failed_jobs']
            step_running = step_analysis['running_jobs']
            all_jobs_failed_in_step = step_analysis['all_jobs_failed']
            
            # Initialize tracking for this step if not exists
            if step_id not in step_job_tracking:
                step_job_tracking[step_id] = {
                    'total': step_total_jobs,
                    'completed': 0,
                    'failed': 0,
                    'running': 0
                }
            
            # Track changes from previous state
            prev = previous_states.get(step_id)
            prev_tracking = step_job_tracking[step_id]
            
            # Calculate newly completed/failed jobs for this step
            newly_completed = step_completed - prev_tracking['completed']
            newly_failed = step_failed - prev_tracking['failed']
            
            # Update invocation-wide counters
            if newly_completed > 0:
                completed_jobs_in_invocation += newly_completed
                prev_tracking['completed'] = step_completed
            
            if newly_failed > 0:
                failed_jobs_in_invocation += newly_failed
                prev_tracking['failed'] = step_failed
            
            prev_tracking['running'] = step_running
            
            # Log state transitions
            if current_state != prev:
                if current_state == JobState.RUNNING:
                    self.log.debug(
                        f"Step (ID: {step_id}) started running. "
                        f"Jobs: {step_running}/{step_total_jobs} running"
                    )
                    previous_states[step_id] = current_state
                    progress_made = True
                    last_progress_at = time.time()

                
                if current_state == JobState.OK:
                    previous_states[step_id] = current_state
                    progress_made = True
                    last_progress_at = time.time()

                    completed_step_count += 1
            
            # CANCELLATION LOGIC: Cancel if ALL jobs in THIS step failed
            if all_jobs_failed_in_step:
                self.log.error(
                    f"Step (ID: {step_id}) COMPLETELY FAILED - "
                    f"ALL {step_total_jobs} jobs failed. Cancelling invocation."
                )
                all_ok = False
                
                asyncio.create_task(self._cancel_invocation_background(invocation))
                has_error = True
                break
            
            if current_state != JobState.OK:
                all_ok = False
            
        if has_error:
            # Early exit if error (collected prior steps)
            invocation_state_result = InvocationStates.FAILED.value
            if invocation_check:
                return invocation_outputs, invocation_state_result, invocation_update_time
            else:
                return invocation_outputs

        if all_ok:
            self.log.info(
                f"All steps completed successfully. "
                f"Jobs: {completed_jobs_in_invocation}/{total_scheduled_jobs_in_invocation} completed"
            )
            invocation_state_result = InvocationStates.COMPLETE.value
            
            final_inv_coro = asyncio.to_thread(
                self.gi_object.gi.invocations.show_invocation, invocation_id=invocation.id
            )
            final_inv = await self._fetch_with_semaphore(final_inv_coro)
            
            if final_inv and 'outputs' in final_inv:
                output_ids = []
                for label, output_info in final_inv.get('outputs', {}).items():
                    if 'id' in output_info:
                        output_ids.append(output_info['id'])
                    self.log.debug(f"Found output '{label}': {output_info}")

            if final_inv and 'output_collections' in final_inv:
                collection_output_ids = []
                for label, output_info in final_inv.get('output_collections', {}).items():
                    if 'id' in output_info:
                        collection_output_ids.append(output_info['id'])
                    self.log.debug(f"Found output '{label}': {output_info}")
                
                # Collect final output ids.
                invocation_outputs = {
                        "output_datasets": output_ids,
                        "collection_datasets": collection_output_ids
                    }
                self.log.info(f"Outputted {len(output_ids)} dataset outputs and {len(collection_output_ids)} collection from invocation")
                    
                #Update invocation update time
                invocation_update_time = final_inv.get("update_time", None)
            
            else:
                self.log.warning("No 'outputs' in final invocation; falling back to partial.")
            
            # For already-completed, we return here (no loop entered)
            if invocation_check:
                return invocation_outputs, invocation_state_result, invocation_update_time
            else:
                return invocation_outputs
        
        # Boolean value to convfirm the pooling check and that all jobs are indeed done and not just unscheduled.
        confirmation_check = False
        
        # If not completed/failed initially, enter polling loop
        while True:
            
            # Reset for this poll cycle
            progress_made = False
            has_error = False
            all_ok = True

            inv_coro = asyncio.to_thread(
                self.gi_object.gi.invocations.show_invocation, invocation_id=invocation.id
            )
            inv = await self._fetch_with_semaphore(inv_coro)
            if inv is None:
                self.log.error("Failed to fetch invocation state during polling.")
                break
            invocation_state = inv.get("state")
            self.log.debug(json.dumps(inv, indent=4))
            if invocation_state in ("failed", "error"):
                self.log.error("workflow invocation has failed.")
                invocation_state_result = InvocationStates.FAILED.value
                if ws_manager:
                    ws_data = {
                        "type": SocketMessageType.INVOCATION_FAILURE.value,
                        "payload": {"message": "Invocation failed or has error"}
                    }
                    await ws_manager.broadcast(
                        event=SocketMessageEvent.workflow_execute.value, 
                        data=ws_data, 
                        tracker_id=tracker_id
                    )
                break
            
            invocation_state_result = InvocationStates.PENDING.value
            
            step_jobs_coro = asyncio.to_thread(
                self.gi_object.gi.invocations.get_invocation_step_jobs_summary, invocation_id=invocation.id
            )
            step_jobs = await self._fetch_with_semaphore(step_jobs_coro)
            if step_jobs is None:
                step_jobs = []  # Safety fallback
            self.log.debug(json.dumps(step_jobs, indent=4))
            
            total_scheduled_jobs_in_invocation = 0
            for step in step_jobs:
                states = step.get('states', {})
                total_scheduled_jobs_in_invocation += sum(states.values())
                
            for step in step_jobs:
                step_id: str = step.get('id')
                
                # Analyze this step's jobs
                step_analysis = self._analyze_step_jobs(step)
                
                current_state = step_analysis['step_state']
                step_total_jobs = step_analysis['total_jobs']
                step_completed = step_analysis['completed_jobs']
                step_failed = step_analysis['failed_jobs']
                step_running = step_analysis['running_jobs']
                all_jobs_failed_in_step = step_analysis['all_jobs_failed']
                
                # Initialize tracking for this step if not exists
                if step_id not in step_job_tracking:
                    step_job_tracking[step_id] = {
                        'total': step_total_jobs,
                        'completed': 0,
                        'failed': 0,
                        'running': 0
                    }
                
                # Track changes from previous state
                prev = previous_states.get(step_id)
                prev_tracking = step_job_tracking[step_id]
                
                # Calculate newly completed/failed jobs for this step
                newly_completed = step_completed - prev_tracking['completed']
                newly_failed = step_failed - prev_tracking['failed']
                
                # Update invocation-wide counters
                if newly_completed > 0:
                    completed_jobs_in_invocation += newly_completed
                    prev_tracking['completed'] = step_completed
                    self.log.info(
                        f"Job: {step_completed}/{step_total_jobs} in current step completed. "
                        f"Invocation Job progress: {completed_jobs_in_invocation}/{total_scheduled_jobs_in_invocation}"
                        )
                
                if newly_failed > 0:
                    failed_jobs_in_invocation += newly_failed
                    prev_tracking['failed'] = step_failed
                
                prev_tracking['running'] = step_running
                
                # Log state transitions
                if current_state != prev:
                    if current_state == JobState.RUNNING:
                        self.log.debug(
                            f"Step (ID: {step_id}) started running. "
                            f"Jobs: {step_running}/{step_total_jobs} running"
                        )
                        previous_states[step_id] = current_state
                        progress_made = True
                        last_progress_at = time.time()

                    if current_state == JobState.OK:
                        self.log.info(f"Step (ID: {step_id}): {completed_step_count}/{num_steps} total steps completed successfully. "
                        )
                        previous_states[step_id] = current_state
                        progress_made = True
                        last_progress_at = time.time()

                        completed_step_count += 1
                        
                        # Broadcast with job-level information
                        if ws_manager:
                            ws_data = {
                                "type": SocketMessageType.INVOCATION_STEP_UPDATE.value,
                                "payload": {
                                    "workflow_steps": num_steps,
                                    "completed_steps": completed_step_count,
                                    "total_jobs": total_scheduled_jobs_in_invocation,
                                    "completed_jobs": completed_jobs_in_invocation,
                                    "failed_jobs": failed_jobs_in_invocation
                                }
                            }
                            await ws_manager.broadcast(
                                event=SocketMessageEvent.workflow_execute.value,
                                data=ws_data,
                                tracker_id=tracker_id
                            )
                
                # CANCELLATION LOGIC: Cancel if ALL jobs in THIS step failed
                if all_jobs_failed_in_step:
                    self.log.error(
                        f"Step (ID: {step_id}) COMPLETELY FAILED - "
                        f"ALL {step_total_jobs} jobs failed. Cancelling invocation."
                    )
                    all_ok = False
                    
                    if ws_manager:
                        ws_data = {
                            "type": SocketMessageType.INVOCATION_FAILURE.value,
                            "payload": {
                                "message": f"Step completely failed - all {step_total_jobs} jobs in step failed",
                                "failed_step_id": step_id,
                                "total_failed_jobs_in_invocation": failed_jobs_in_invocation
                            }
                        }
                        await ws_manager.broadcast(
                            event=SocketMessageEvent.workflow_execute.value,
                            data=ws_data,
                            tracker_id=tracker_id
                        )
                    
                    asyncio.create_task(self._cancel_invocation_background(invocation))
                    has_error = True
                    break
                
                if current_state != JobState.OK:
                    all_ok = False
                
            if has_error:
                invocation_state_result = InvocationStates.FAILED.value
                break
            
            if not all_ok or not confirmation_check:
                confirmation_check = False
                               
            if all_ok and confirmation_check:
                self.log.info(
                    f"All Jobs completed successfully. "
                    f"Jobs: {completed_jobs_in_invocation}/{total_scheduled_jobs_in_invocation} completed"
                )
                invocation_state_result = InvocationStates.COMPLETE.value
                
                if ws_manager:
                    ws_data = {
                        "type": SocketMessageType.INVOCATION_STEP_UPDATE.value,
                        "payload": {
                            "message": "All Jobs completed successfully",
                            "total_jobs": total_scheduled_jobs_in_invocation,
                            "completed_jobs": completed_jobs_in_invocation
                        }
                    }
                    await ws_manager.broadcast(
                        event=SocketMessageEvent.workflow_execute.value,
                        data=ws_data,
                        tracker_id=tracker_id
                    )
                
                # Final full collection (as above)
                final_inv_coro = asyncio.to_thread(
                    self.gi_object.gi.invocations.show_invocation, invocation_id=invocation.id
                )
                final_inv = await self._fetch_with_semaphore(final_inv_coro)
                if final_inv and 'outputs' in final_inv:
                    output_ids = []
                    for label, output_info in final_inv['outputs'].items():
                        if 'id' in output_info:
                            output_ids.append(output_info['id'])
                        self.log.debug(f"Found output '{label}': {output_info}")

                if final_inv and 'output_collections' in final_inv:
                    collection_output_ids = []
                    for label, output_info in final_inv['output_collections'].items():
                        if 'id' in output_info:
                            collection_output_ids.append(output_info['id'])
                        self.log.debug(f"Found output '{label}': {output_info}")

                    # Collect output ids.
                    invocation_outputs = {
                        "output_datasets": output_ids,
                        "collection_datasets": collection_output_ids
                    }
                    self.log.info(f"Collected {len(output_ids)} dataset outputs and {len(collection_output_ids)} collection outputs")
                    
                    # Update invocation update time.
                    invocation_update_time = final_inv.get("update_time", None)
                else:
                    self.log.warning("No 'outputs' in final invocation; falling back to partial.")
                
                break
            
            if all_ok:
                #TODO: Make this an Enum for setting the limit easier. 
                if completed_step_count > (num_steps - 5) :
                    self.log.info("Most of the Scheduled jobs are complete, confirming there are no more jobs to be sheduled")
                    confirmation_check = True
                else:
                    self.log.info("some of the scheduled jobs have not been complete.")
                    
                await asyncio.sleep(2)
                
            
            no_progress = time.time() - last_progress_at
            
            # Polling should slowly take longer and longer, but for the first 10 min do quick checks.
            if no_progress < 1 * 600: 
                if not confirmation_check:
                    poll_interval = InvocationTracking.POLL_QUICK.value
                else:
                    poll_interval = InvocationTracking.POLL_QUICK.value * 6 # 1 Min for confirmation if workflow invocation finishes fast

            elif no_progress < 1 * 3600:
                poll_interval = InvocationTracking.POLL_FAST.value
                
            elif no_progress < InvocationTracking.BASE_NO_PROGRESS.value:
                poll_interval = InvocationTracking.POLL_MEDIUM.value
                
            elif no_progress < InvocationTracking.STALLED_THRESHOLD.value:
                poll_interval = InvocationTracking.POLL_SLOW.value
            else:
                poll_interval = InvocationTracking.POLL_MAX.value

            
            await asyncio.sleep(poll_interval)
            
            no_progress = time.time() - last_progress_at

            if no_progress > InvocationTracking.HARD_CAP.value:
                self.log.error(
                    f"Invocation stalled for {int(no_progress)} seconds; exceeding hard cap"
                )
                
                if ws_manager:
                    ws_data = {
                        "type": SocketMessageType.INVOCATION_FAILURE.value,
                        "payload": {"message": "Invocation timed out"}
                    }
                    await ws_manager.broadcast(
                        event=SocketMessageEvent.workflow_execute.value, 
                        data=ws_data, 
                        tracker_id=tracker_id
                    )
                    
                asyncio.create_task(self._cancel_invocation_background(invocation))
                invocation_state_result = InvocationStates.FAILED.value
                break

            
        
        if invocation_check:
            return invocation_outputs, invocation_state_result, invocation_update_time
        else:
            return invocation_outputs
    
    def make_result(self, invocation: Invocation, outputs: list[Union[Dataset, DatasetCollection]]):
        """Result report of the workflow invocation"""
        
        invocation_id = invocation.id
        invocation_report = self.gi_object.gi.invocations.get_invocation_report(invocation.id)
        final_outputs=[]
        intermediate_outputs=[]

        # Classify the workflow invocation outputs as visible and hidden(i.e final outputs and intermediate step outputs)
        for output in outputs:
            if output.visible:
                final_outputs.append(output)
            else:
                intermediate_outputs.append(output)
        return invocation_id, invocation_report, intermediate_outputs, final_outputs
    
    def invoke_workflow(self, inputs: dict, workflow: Workflow, history: History ) -> Invocation:
        """
        Invoke a Galaxy workflow with specified inputs.
        
        :param workflow: workflow object
        :param inputs: Workflow inputs parameters mapping
        :param history: history object

        :return: The workflow invocation  
        """

        invoke = self.gi_object.gi.workflows.invoke_workflow(workflow_id=workflow.id, 
                                                                  inputs=inputs,
                                                                  history_id=history.id,
                                                                  parameters_normalized=True,
                                                                  require_exact_tool_versions=False)
        time.sleep(NumericLimits.SHORT_SLEEP.value)
        invocation=self.gi_object.invocations.get(invoke['id'])
        return invocation
    
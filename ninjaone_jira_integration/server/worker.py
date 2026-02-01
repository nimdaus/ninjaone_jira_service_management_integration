"""
Background job worker.

Processes jobs from the queue asynchronously:
- Device sync jobs
- Alert processing jobs

Uses exponential backoff for retries and moves failed
jobs to dead-letter queue after max attempts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import SecretStr

from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
from ninjaone_jira_integration.config.models import AppConfig
from ninjaone_jira_integration.store.db import DatabaseManager
from ninjaone_jira_integration.store.jobs import Job, JobStore, JobType

logger = logging.getLogger(__name__)


class JobWorker:
    """Background worker for processing jobs.
    
    Runs in a separate asyncio task and continuously polls
    for new jobs to process.
    """
    
    def __init__(
        self,
        config: AppConfig,
        db: DatabaseManager,
    ):
        """Initialize job worker.
        
        Args:
            config: Application configuration.
            db: Database manager.
        """
        self.config = config
        self.db = db
        self.job_store = JobStore(db)
        
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._is_running = False
        
        # Clients will be initialized on start
        self._ninja_client: NinjaOneClient | None = None
        self._jira_client: JiraAssetsClient | None = None
    
    @property
    def is_running(self) -> bool:
        """Check if worker is running."""
        return self._is_running
    
    async def start(self) -> None:
        """Start the background worker."""
        if self._is_running:
            logger.warning("Worker already running")
            return
        
        logger.info("Starting job worker")
        
        # Initialize clients
        await self._init_clients()
        
        # Reset any stale processing jobs
        await self.job_store.reset_stale_processing()
        
        # Start processing loop
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())
        self._is_running = True
        
        logger.info("Job worker started")
    
    async def stop(self) -> None:
        """Stop the background worker."""
        if not self._is_running:
            return
        
        logger.info("Stopping job worker")
        
        self._stop_event.set()
        
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Worker did not stop gracefully, cancelling")
                self._task.cancel()
        
        # Close clients
        if self._ninja_client:
            await self._ninja_client.close()
        if self._jira_client:
            await self._jira_client.close()
        
        self._is_running = False
        
        logger.info("Job worker stopped")
    
    async def _init_clients(self) -> None:
        """Initialize API clients."""
        ninja_config = self.config.ninjaone
        jira_config = self.config.jira
        
        # Initialize NinjaOne client
        self._ninja_client = NinjaOneClient(
            base_url=ninja_config.base_url,
            client_id=ninja_config.client_id,
            client_secret=ninja_config.client_secret,
            scopes=ninja_config.scopes,
        )
        
        # Test NinjaOne connection
        try:
            await self._ninja_client.authenticate()
            logger.info("NinjaOne client authenticated")
        except Exception as e:
            logger.error("Failed to authenticate NinjaOne client: %s", str(e))
            # Continue anyway, will retry on job processing
        
        # Initialize Jira client
        self._jira_client = JiraAssetsClient(
            subdomain=jira_config.subdomain,
            email=jira_config.email,
            api_token=jira_config.api_token,
            workspace_id=jira_config.workspace_id,
        )
        
        # Discover workspace if not configured
        if not jira_config.workspace_id:
            try:
                await self._jira_client.discover_workspace()
                logger.info("Jira Assets workspace discovered")
            except Exception as e:
                logger.error("Failed to discover Jira workspace: %s", str(e))
    
    async def _run(self) -> None:
        """Main processing loop."""
        poll_interval = 1.0  # Start with 1 second
        max_poll_interval = 30.0
        
        while not self._stop_event.is_set():
            try:
                # Claim next job
                job = await self.job_store.claim_next()
                
                if job:
                    # Reset poll interval on work
                    poll_interval = 1.0
                    
                    # Process the job
                    await self._process_job(job)
                else:
                    # No work, backoff
                    poll_interval = min(poll_interval * 1.5, max_poll_interval)
                    
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=poll_interval,
                        )
                    except asyncio.TimeoutError:
                        pass  # Normal timeout, continue loop
                        
            except Exception as e:
                logger.exception("Error in worker loop: %s", str(e))
                await asyncio.sleep(5.0)  # Backoff on error
    
    async def _process_job(self, job: Job) -> None:
        """Process a single job.
        
        Args:
            job: Job to process.
        """
        logger.info(
            "Processing job %d: %s/%s (attempt %d/%d, correlation_id=%s)",
            job.id,
            job.job_type.value,
            job.job_key,
            job.attempts,
            job.max_attempts,
            job.correlation_id,
        )
        
        try:
            if job.job_type == JobType.DEVICE_SYNC:
                await self._process_device_sync(job)
            elif job.job_type == JobType.ALERT_PROCESS:
                await self._process_alert(job)
            else:
                logger.warning("Unknown job type: %s", job.job_type)
                await self.job_store.fail(job.id, f"Unknown job type: {job.job_type}")
                return
            
            await self.job_store.complete(job.id)
            logger.info("Job %d completed successfully", job.id)
            
        except Exception as e:
            logger.error("Job %d failed: %s", job.id, str(e))
            await self.job_store.fail(job.id, str(e))
    
    async def _process_device_sync(self, job: Job) -> None:
        """Process a device sync job.
        
        Args:
            job: Device sync job.
        """
        device_id = int(job.job_key)
        
        # Import here to avoid circular imports
        from ninjaone_jira_integration.sync.engine import SyncEngine
        
        engine = SyncEngine(
            config=self.config,
            ninja_client=self._ninja_client,
            jira_client=self._jira_client,
            db=self.db,
        )
        
        # Get device data from payload or fetch fresh
        device = job.payload.get("data")
        
        result = await engine.sync_device(device_id, device)
        
        if result.error:
            raise Exception(result.error)
        
        logger.info(
            "Device sync complete: %d -> %s (%s)",
            device_id,
            result.jira_asset_key,
            result.action.value,
        )
    
    async def _process_alert(self, job: Job) -> None:
        """Process an alert job.
        
        Args:
            job: Alert processing job.
        """
        alert_id = int(job.job_key)
        
        # Import here to avoid circular imports
        from ninjaone_jira_integration.alerts.processor import AlertProcessor
        
        processor = AlertProcessor(
            config=self.config,
            ninja_client=self._ninja_client,
            jira_client=self._jira_client,
            db=self.db,
        )
        
        # Get alert data from payload or fetch fresh
        alert = job.payload.get("data")
        
        result = await processor.process_alert(alert_id, alert)
        
        if result.error:
            raise Exception(result.error)
        
        logger.info(
            "Alert processing complete: %d -> %s (%s)",
            alert_id,
            result.jira_issue_key,
            result.action.value,
        )

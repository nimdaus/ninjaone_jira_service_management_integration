"""
Scheduled sync runner.

Periodically calls sync_all() according to the configured interval,
allowing fully automatic device sync without requiring a public-facing
HTTP server for webhooks.
"""

from __future__ import annotations

import asyncio
import logging

from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
from ninjaone_jira_integration.config.models import AppConfig
from ninjaone_jira_integration.store.db import DatabaseManager
from ninjaone_jira_integration.sync.engine import SyncEngine

logger = logging.getLogger(__name__)


class SyncScheduler:
    """Runs full device syncs on a configurable interval.

    Can coexist with the webhook-based JobWorker (run-server mode) or
    operate standalone (run mode) for environments without a public IP.
    """

    def __init__(self, config: AppConfig, db: DatabaseManager):
        self.config = config
        self.db = db
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._is_running = False
        self._ninja_client: NinjaOneClient | None = None
        self._jira_client: JiraAssetsClient | None = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def start(self) -> None:
        if self._is_running:
            return
        if not self.config.schedule.enabled:
            logger.info("Scheduled sync is disabled via config")
            return

        logger.info(
            "Starting sync scheduler (interval: %.1f hours)",
            self.config.schedule.interval_hours,
        )
        await self._init_clients()
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())
        self._is_running = True

    async def stop(self) -> None:
        if not self._is_running:
            return
        logger.info("Stopping sync scheduler")
        self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except asyncio.TimeoutError:
                self._task.cancel()
        if self._ninja_client:
            await self._ninja_client.close()
        if self._jira_client:
            await self._jira_client.close()
        self._is_running = False
        logger.info("Sync scheduler stopped")

    async def run_once(self, dry_run: bool = False) -> None:
        """Authenticate and run a single full sync, then close clients."""
        await self._init_clients()
        try:
            await self._run_sync(dry_run=dry_run)
        finally:
            if self._ninja_client:
                await self._ninja_client.close()
            if self._jira_client:
                await self._jira_client.close()

    async def _init_clients(self) -> None:
        ninja_cfg = self.config.ninjaone
        jira_cfg = self.config.jira

        self._ninja_client = NinjaOneClient(
            base_url=ninja_cfg.base_url,
            client_id=ninja_cfg.client_id,
            client_secret=ninja_cfg.client_secret,
            scopes=ninja_cfg.scopes,
        )
        try:
            await self._ninja_client.authenticate()
            logger.info("Scheduler: NinjaOne authenticated")
        except Exception as e:
            logger.error("Scheduler: NinjaOne auth failed: %s", e)

        self._jira_client = JiraAssetsClient(
            subdomain=jira_cfg.subdomain,
            email=jira_cfg.email,
            api_token=jira_cfg.api_token,
            workspace_id=jira_cfg.workspace_id,
        )
        if not jira_cfg.workspace_id:
            try:
                await self._jira_client.discover_workspace()
                logger.info("Scheduler: Jira workspace discovered")
            except Exception as e:
                logger.error("Scheduler: Jira workspace discovery failed: %s", e)

    async def _run_sync(self, dry_run: bool = False) -> None:
        engine = SyncEngine(
            config=self.config,
            ninja_client=self._ninja_client,
            jira_client=self._jira_client,
            db=self.db,
        )
        try:
            summary = await engine.sync_all(dry_run=dry_run)
            logger.info(
                "Scheduled sync complete: %d total, %d created, %d updated, %d skipped, %d failed",
                summary.total_devices,
                summary.created,
                summary.updated,
                summary.skipped,
                summary.failed,
            )
            if not dry_run:
                from ninjaone_jira_integration.notifications import OutboundNotifier
                await OutboundNotifier(self.config.heartbeat).send_sync_complete(summary)
        except Exception as e:
            logger.exception("Scheduled sync failed: %s", e)

    async def _loop(self) -> None:
        interval_secs = self.config.schedule.interval_hours * 3600
        while not self._stop_event.is_set():
            logger.info("Scheduler: running scheduled sync")
            await self._run_sync()

            logger.info(
                "Scheduler: next sync in %.1f hours",
                self.config.schedule.interval_hours,
            )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_secs)
            except asyncio.TimeoutError:
                pass  # Normal interval expiry

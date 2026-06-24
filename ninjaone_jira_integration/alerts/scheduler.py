"""
Scheduled alert poller.

Periodically fetches active alerts from NinjaOne /v2/alerts and creates
Jira issues for any that don't already have one. Runs on its own interval
(default 5 minutes), separate from the device sync scheduler.

If an alert references a device that has no Jira asset mapping yet, a
targeted device sync is triggered first so the issue can be linked to the
correct asset on creation.
"""

from __future__ import annotations

import asyncio
import logging

from ninjaone_jira_integration.alerts.processor import AlertAction, AlertProcessor
from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
from ninjaone_jira_integration.config.models import AppConfig
from ninjaone_jira_integration.store.db import DatabaseManager
from ninjaone_jira_integration.store.mappings import MappingStore
from ninjaone_jira_integration.sync.engine import SyncEngine

logger = logging.getLogger(__name__)


class AlertScheduler:
    """Polls NinjaOne for active alerts and creates Jira issues on a schedule.

    Can coexist with the device SyncScheduler and the webhook-based JobWorker.
    All three can run simultaneously — alert deduplication in AlertProcessor
    (via alert_mappings table) ensures no duplicate issues are created.
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
        if not self.config.alert_schedule.enabled:
            logger.info("Alert polling is disabled via config")
            return

        logger.info(
            "Starting alert scheduler (interval: %.1f minutes)",
            self.config.alert_schedule.interval_minutes,
        )
        await self._init_clients()
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())
        self._is_running = True

    async def stop(self) -> None:
        if not self._is_running:
            return
        logger.info("Stopping alert scheduler")
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
        logger.info("Alert scheduler stopped")

    async def run_once(self, dry_run: bool = False) -> None:
        """Authenticate and run a single alert poll, then close clients."""
        await self._init_clients()
        try:
            await self._run_poll(dry_run=dry_run)
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
            logger.info("Alert scheduler: NinjaOne authenticated")
        except Exception as e:
            logger.error("Alert scheduler: NinjaOne auth failed: %s", e)

        self._jira_client = JiraAssetsClient(
            subdomain=jira_cfg.subdomain,
            email=jira_cfg.email,
            api_token=jira_cfg.api_token,
            workspace_id=jira_cfg.workspace_id,
        )
        if not jira_cfg.workspace_id:
            try:
                await self._jira_client.discover_workspace()
                logger.info("Alert scheduler: Jira workspace discovered")
            except Exception as e:
                logger.error("Alert scheduler: Jira workspace discovery failed: %s", e)

    async def _run_poll(self, dry_run: bool = False) -> None:
        processor = AlertProcessor(
            config=self.config,
            ninja_client=self._ninja_client,
            jira_client=self._jira_client,
            db=self.db,
        )
        mapping_store = MappingStore(self.db)

        created = skipped = failed = exists = 0

        try:
            async for alert in self._ninja_client.get_alerts():
                alert_id = alert.get("uid")
                if alert_id is None:
                    continue

                device_id = alert.get("deviceId")

                # If the device has no Jira asset yet, sync it first so the
                # issue can be linked to the asset on creation.
                if device_id is not None:
                    existing_mapping = await mapping_store.get_device_mapping(device_id)
                    if not existing_mapping:
                        logger.info(
                            "Alert %s: device %d has no asset mapping, syncing device first",
                            alert_id,
                            device_id,
                        )
                        await self._sync_device_for_alert(device_id, dry_run=dry_run)

                result = await processor.process_alert(
                    alert_id=alert_id,
                    alert=alert,
                    dry_run=dry_run,
                )

                if result.action == AlertAction.CREATED:
                    created += 1
                elif result.action == AlertAction.EXISTS:
                    exists += 1
                elif result.action == AlertAction.SKIPPED:
                    skipped += 1
                elif result.action == AlertAction.FAILED:
                    failed += 1

        except Exception as e:
            logger.exception("Alert poll failed: %s", e)
            return

        logger.info(
            "Alert poll complete: %d created, %d already exist, %d skipped, %d failed",
            created,
            exists,
            skipped,
            failed,
        )

    async def _sync_device_for_alert(self, device_id: int, dry_run: bool = False) -> None:
        """Run a targeted device sync to create the asset mapping before alert processing."""
        try:
            engine = SyncEngine(
                config=self.config,
                ninja_client=self._ninja_client,
                jira_client=self._jira_client,
                db=self.db,
            )
            result = await engine.sync_device(device_id, dry_run=dry_run)
            logger.info(
                "Alert-triggered device sync for device %d: %s",
                device_id,
                result.action,
            )
        except Exception as e:
            logger.warning(
                "Alert-triggered device sync for device %d failed: %s — will create issue without asset link",
                device_id,
                e,
            )

    async def _loop(self) -> None:
        interval_secs = self.config.alert_schedule.interval_minutes * 60
        while not self._stop_event.is_set():
            logger.info("Alert scheduler: running alert poll")
            await self._run_poll()

            logger.info(
                "Alert scheduler: next poll in %.1f minutes",
                self.config.alert_schedule.interval_minutes,
            )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_secs)
            except asyncio.TimeoutError:
                pass  # Normal interval expiry

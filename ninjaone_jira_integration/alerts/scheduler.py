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
from datetime import datetime, timezone

from ninjaone_jira_integration.alerts.processor import AlertProcessor
from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
from ninjaone_jira_integration.config.models import AppConfig
from ninjaone_jira_integration.store.db import DatabaseManager
from ninjaone_jira_integration.store.mappings import AlertMapping, MappingStore
from ninjaone_jira_integration.sync.engine import SyncAction, SyncEngine, SyncResult

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
        issues_cfg = self.config.issues

        created = skipped = failed = exists = no_asset_link = resolved = retriggered = 0
        devices_without_asset: set[int] = set()
        active_uids: set[str] = set()

        # Per-pass collections
        to_create: list[tuple[str, dict, int | None]] = []          # brand-new alerts
        to_retrigger_new: list[tuple[str, dict, int | None]] = []   # retriggered, need new issue
        to_retrigger_reopen: list[tuple[str, AlertMapping]] = []    # retriggered, try to reopen
        to_update_comment: list[tuple[str, AlertMapping, float]] = []  # active alerts whose condition updated

        # ── Pass 1: classify every active alert ──────────────────────────────────
        try:
            async for alert in self._ninja_client.get_alerts():
                alert_id = alert.get("uid")
                if alert_id is None:
                    continue

                active_uids.add(alert_id)
                device_id = alert.get("deviceId")

                # Sync device if it has no Jira asset yet
                if device_id is not None:
                    existing_dm = await mapping_store.get_device_mapping(device_id)
                    if not existing_dm:
                        logger.info(
                            "Alert %s: device %d has no asset mapping, syncing device first",
                            alert_id, device_id,
                        )
                        sync_result = await self._sync_device_for_alert(device_id, dry_run=dry_run)
                        if sync_result is None or sync_result.action == SyncAction.FAILED:
                            devices_without_asset.add(device_id)

                existing_mapping = await mapping_store.get_alert_mapping(alert_id)

                if existing_mapping is not None:
                    if existing_mapping.resolved_at is not None:
                        # RETRIGGERED: was marked resolved but has reappeared
                        if (
                            issues_cfg.retrigger_behavior == "reopen"
                            and issues_cfg.reopen_transition_id
                        ):
                            to_retrigger_reopen.append((alert_id, existing_mapping))
                        else:
                            to_retrigger_new.append((alert_id, alert, device_id))
                    else:
                        # EXISTS: active, unresolved — check for condition update
                        update_time = float(alert.get("updateTime") or 0)
                        create_time = float(alert.get("createTime") or 0)
                        if update_time > create_time:
                            last_seen = existing_mapping.ninja_update_time or 0.0
                            if update_time > last_seen:
                                to_update_comment.append(
                                    (existing_mapping.jira_issue_key, existing_mapping, update_time)
                                )
                        exists += 1
                else:
                    # NEW: apply filters
                    skip_reason = processor._skip_reason(alert)
                    if skip_reason:
                        logger.info("Alert %s skipped: %s", alert_id, skip_reason)
                        skipped += 1
                        continue
                    to_create.append((alert_id, alert, device_id))

        except Exception as e:
            logger.exception("Alert poll failed during fetch: %s", e)
            return

        # ── Bulk create: new alerts ───────────────────────────────────────────────
        if to_create:
            if dry_run:
                logger.info("[DRY RUN] Would create %d new issue(s) in bulk", len(to_create))
                created += len(to_create)
            else:
                payloads_meta: list[tuple[str, dict, int | None]] = []
                fields_list: list[dict] = []
                for alert_id, alert, device_id in to_create:
                    device_mapping = await mapping_store.get_device_mapping(device_id) if device_id else None
                    payload = processor.build_create_payload(alert_id, alert, device_mapping)
                    payloads_meta.append((alert_id, alert, device_id))
                    fields_list.append(payload)

                try:
                    logger.info("Creating %d issue(s) in bulk", len(fields_list))
                    results = await self._jira_client.create_issues_bulk(fields_list)
                    for i, issue in enumerate(results):
                        alert_id, alert, device_id = payloads_meta[i]
                        if issue is None:
                            logger.error("Bulk creation failed for alert %s", alert_id)
                            failed += 1
                            continue
                        issue_key = issue.get("key", "")
                        issue_id = str(issue.get("id", ""))
                        new_mapping = AlertMapping(
                            ninja_alert_id=alert_id,
                            jira_issue_key=issue_key,
                            jira_issue_id=issue_id,
                            ninja_device_id=device_id,
                        )
                        await mapping_store.upsert_alert_mapping(new_mapping)
                        created += 1
                        if device_id in devices_without_asset:
                            no_asset_link += 1
                            logger.warning(
                                "Alert %s: issue %s created WITHOUT asset reference — "
                                "device %d could not be synced to a Jira asset",
                                alert_id, issue_key, device_id,
                            )
                        else:
                            logger.info("Created issue %s for alert %s", issue_key, alert_id)
                except Exception as e:
                    logger.error("Bulk issue creation failed: %s", e)
                    failed += len(to_create)

        # ── Bulk create: retriggered alerts that need a new issue ─────────────────
        if to_retrigger_new:
            if dry_run:
                logger.info("[DRY RUN] Would create %d new issue(s) for retriggered alerts", len(to_retrigger_new))
            else:
                rt_payloads_meta: list[tuple[str, dict, int | None]] = []
                rt_fields_list: list[dict] = []
                for alert_id, alert, device_id in to_retrigger_new:
                    device_mapping = await mapping_store.get_device_mapping(device_id) if device_id else None
                    payload = processor.build_create_payload(alert_id, alert, device_mapping)
                    rt_payloads_meta.append((alert_id, alert, device_id))
                    rt_fields_list.append(payload)

                try:
                    logger.info("Creating %d issue(s) for retriggered alerts", len(rt_fields_list))
                    rt_results = await self._jira_client.create_issues_bulk(rt_fields_list)
                    for i, issue in enumerate(rt_results):
                        alert_id, alert, device_id = rt_payloads_meta[i]
                        if issue is None:
                            logger.error("Bulk creation failed for retriggered alert %s", alert_id)
                            failed += 1
                            continue
                        issue_key = issue.get("key", "")
                        issue_id = str(issue.get("id", ""))
                        old_mapping = await mapping_store.get_alert_mapping(alert_id)
                        old_key = old_mapping.jira_issue_key if old_mapping else "?"
                        updated_mapping = AlertMapping(
                            ninja_alert_id=alert_id,
                            jira_issue_key=issue_key,
                            jira_issue_id=issue_id,
                            ninja_device_id=device_id,
                            ninja_update_time=None,
                            resolved_at=None,
                        )
                        await mapping_store.upsert_alert_mapping(updated_mapping)
                        logger.info(
                            "Alert %s retriggered — new issue %s (previous: %s)", alert_id, issue_key, old_key
                        )
                        retriggered += 1
                except Exception as e:
                    logger.error("Bulk creation for retriggered alerts failed: %s", e)
                    failed += len(to_retrigger_new)

        # ── Retrigger reopen ──────────────────────────────────────────────────────
        for alert_id, mapping in to_retrigger_reopen:
            try:
                # Comment before transitioning so business rules on the target state don't block it
                if issues_cfg.reopen_comment:
                    await self._jira_client.add_comment(mapping.jira_issue_key, issues_cfg.reopen_comment)
                if issues_cfg.reopen_target_status:
                    await self._jira_client.transition_to_status(
                        mapping.jira_issue_key, issues_cfg.reopen_target_status
                    )
                elif issues_cfg.reopen_transition_id:
                    await self._jira_client.do_transition(mapping.jira_issue_key, issues_cfg.reopen_transition_id)
                mapping.resolved_at = None
                mapping.ninja_update_time = None
                await mapping_store.upsert_alert_mapping(mapping)
                logger.info("Alert %s retriggered — reopened issue %s", alert_id, mapping.jira_issue_key)
                retriggered += 1
                exists += 1
            except Exception as e:
                logger.warning(
                    "Failed to reopen %s for retriggered alert %s (%s) — will retry next poll",
                    mapping.jira_issue_key, alert_id, e,
                )
                # Leave resolved_at set so next poll retries (or falls back if config changes)
                failed += 1

        # ── Update comments ───────────────────────────────────────────────────────
        for issue_key, mapping, update_time in to_update_comment:
            try:
                await self._jira_client.add_comment(issue_key, "NinjaOne condition updated.")
                mapping.ninja_update_time = update_time
                await mapping_store.upsert_alert_mapping(mapping)
                logger.debug("Alert %s condition updated — commented on %s", mapping.ninja_alert_id, issue_key)
            except Exception as e:
                logger.warning("Failed to add update comment to %s: %s", issue_key, e)

        # ── Resolution detection ──────────────────────────────────────────────────
        all_active_mappings = await mapping_store.list_active_alert_mappings()
        for mapping in all_active_mappings:
            if mapping.ninja_alert_id not in active_uids:
                try:
                    if not dry_run:
                        # Comment first so business rules on the resolved state don't block it
                        if issues_cfg.resolve_comment:
                            await self._jira_client.add_comment(
                                mapping.jira_issue_key, issues_cfg.resolve_comment
                            )
                        if issues_cfg.resolve_target_status:
                            await self._jira_client.transition_to_status(
                                mapping.jira_issue_key, issues_cfg.resolve_target_status
                            )
                        elif issues_cfg.resolve_transition_id:
                            await self._jira_client.do_transition(
                                mapping.jira_issue_key, issues_cfg.resolve_transition_id
                            )
                    mapping.resolved_at = datetime.now(timezone.utc)
                    await mapping_store.upsert_alert_mapping(mapping)
                    logger.info(
                        "Alert %s resolved — transitioned issue %s",
                        mapping.ninja_alert_id, mapping.jira_issue_key,
                    )
                    resolved += 1
                except Exception as e:
                    logger.warning(
                        "Failed to process resolved alert %s (issue %s): %s",
                        mapping.ninja_alert_id, mapping.jira_issue_key, e,
                    )

        # ── Summary ───────────────────────────────────────────────────────────────
        logger.info(
            "Alert poll complete: %d created, %d exist, %d skipped, %d failed, "
            "%d resolved, %d retriggered",
            created, exists, skipped, failed, resolved, retriggered,
        )
        if no_asset_link:
            logger.warning(
                "%d alert issue(s) created without an asset reference — "
                "check object_type_mappings for unmapped device roles",
                no_asset_link,
            )
        if not dry_run:
            from ninjaone_jira_integration.notifications import OutboundNotifier
            await OutboundNotifier(self.config.heartbeat).send_alert_poll_complete(
                created, exists, skipped, failed, no_asset_link=no_asset_link
            )

    async def _sync_device_for_alert(
        self, device_id: int, dry_run: bool = False
    ) -> SyncResult | None:
        """Run a targeted device sync to create the asset mapping before alert processing."""
        try:
            engine = SyncEngine(
                config=self.config,
                ninja_client=self._ninja_client,
                jira_client=self._jira_client,
                db=self.db,
            )
            result = await engine.sync_device(device_id, dry_run=dry_run)
            if result.action == SyncAction.FAILED:
                logger.warning(
                    "Alert-triggered device sync for device %d failed: %s — "
                    "Jira issue will be created without an asset reference",
                    device_id,
                    result.error or "unknown reason",
                )
            else:
                logger.info(
                    "Alert-triggered device sync for device %d: %s",
                    device_id,
                    result.action,
                )
            return result
        except Exception as e:
            logger.warning(
                "Alert-triggered device sync for device %d failed: %s — "
                "Jira issue will be created without an asset reference",
                device_id,
                e,
            )
            return None

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

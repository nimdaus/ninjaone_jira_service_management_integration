"""
Outbound notification system.

Sends optional webhooks for two event types:
- heartbeat: periodic ping confirming the process is alive
- change summary: posted after each successful write (sync or alert)

If no URL is configured, all methods are silent no-ops.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ninjaone_jira_integration.config.models import HeartbeatConfig
    from ninjaone_jira_integration.sync.engine import SyncSummary

logger = logging.getLogger(__name__)


def detect_runtime() -> str:
    """Detect the current runtime environment."""
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return "kubernetes"
    if os.path.exists("/.dockerenv"):
        return "docker"
    return "bare-python"


class OutboundNotifier:
    """Fire-and-forget outbound webhook sender.

    All public methods silently no-op if heartbeat.url is unset.
    Failures are logged as WARNING and never re-raised.
    """

    def __init__(self, config: HeartbeatConfig) -> None:
        self._cfg = config
        self._start_time = datetime.now(timezone.utc)

    def _base(self) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "runtime": detect_runtime(),
        }

    async def send_heartbeat(self) -> None:
        if not self._cfg.url:
            return
        from ninjaone_jira_integration import __version__

        uptime = int((datetime.now(timezone.utc) - self._start_time).total_seconds())
        await self._post({
            **self._base(),
            "event": "heartbeat",
            "status": "running",
            "version": __version__,
            "uptime_seconds": uptime,
        })

    async def send_sync_complete(self, summary: SyncSummary) -> None:
        if not self._cfg.url or not self._cfg.notify_on_changes:
            return

        changes: list[str] = []
        for r in summary.results:
            if r.changes:
                label = r.device_name or str(r.device_id)
                for c in r.changes:
                    changes.append(f"{label}: {c}")

        await self._post({
            **self._base(),
            "event": "sync_complete",
            "summary": {
                "type": "device_sync",
                "total": summary.total_devices,
                "created": summary.created,
                "updated": summary.updated,
                "skipped": summary.skipped,
                "failed": summary.failed,
                "success_rate": round(summary.success_rate, 1),
                "changes": changes[:20],
            },
        })

    async def send_alert_poll_complete(
        self,
        created: int,
        exists: int,
        skipped: int,
        failed: int,
        no_asset_link: int = 0,
    ) -> None:
        if not self._cfg.url or not self._cfg.notify_on_changes:
            return

        summary: dict[str, Any] = {
            "type": "alert_poll",
            "created": created,
            "already_exists": exists,
            "skipped": skipped,
            "failed": failed,
        }
        if no_asset_link:
            summary["no_asset_link"] = no_asset_link
            summary["warning"] = (
                f"{no_asset_link} issue(s) created without an asset reference — "
                "device role not configured in object_type_mappings"
            )

        await self._post({
            **self._base(),
            "event": "alert_poll_complete",
            "summary": summary,
        })

    async def _post(self, payload: dict[str, Any]) -> None:
        import httpx

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._cfg.token:
            headers["Authorization"] = f"Bearer {self._cfg.token.get_secret_value()}"

        try:
            async with httpx.AsyncClient(timeout=self._cfg.timeout_seconds) as client:
                response = await client.post(self._cfg.url, json=payload, headers=headers)
                response.raise_for_status()
            logger.debug("Outbound notification sent: %s", payload.get("event"))
        except Exception as e:
            logger.warning("Outbound notification failed (%s): %s", payload.get("event"), e)

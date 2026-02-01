"""
Push-based heartbeat service for monitoring.

Sends periodic heartbeats to a configured endpoint
(e.g., Uptime Kuma, Healthchecks.io, etc.)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ninjaone_jira_integration.config.models import HeartbeatConfig
from ninjaone_jira_integration.store.db import DatabaseManager

logger = logging.getLogger(__name__)


class HeartbeatService:
    """Push-based heartbeat service.
    
    Sends periodic HTTP requests to a monitoring endpoint
    to signal that the service is alive and healthy.
    """
    
    def __init__(
        self,
        config: HeartbeatConfig,
        db: DatabaseManager | None = None,
    ):
        """Initialize heartbeat service.
        
        Args:
            config: Heartbeat configuration.
            db: Optional database manager for health checks.
        """
        self.config = config
        self.db = db
        
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._client: httpx.AsyncClient | None = None
        
        # State tracking
        self._last_success: float = 0.0
        self._consecutive_failures: int = 0
    
    @property
    def is_running(self) -> bool:
        """Check if heartbeat service is running."""
        return self._task is not None and not self._task.done()
    
    async def start(self) -> None:
        """Start the heartbeat service."""
        if not self.config.enabled:
            logger.info("Heartbeat service disabled")
            return
        
        if not self.config.url:
            logger.warning("Heartbeat URL not configured")
            return
        
        logger.info(
            "Starting heartbeat service (interval=%ds, url=%s)",
            self.config.interval_seconds,
            self.config.url,
        )
        
        self._client = httpx.AsyncClient(timeout=30.0)
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())
    
    async def stop(self) -> None:
        """Stop the heartbeat service."""
        if not self._task:
            return
        
        logger.info("Stopping heartbeat service")
        
        self._stop_event.set()
        
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            self._task.cancel()
        
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def _run(self) -> None:
        """Main heartbeat loop."""
        while not self._stop_event.is_set():
            try:
                await self._send_heartbeat()
            except Exception as e:
                logger.error("Heartbeat error: %s", str(e))
                self._consecutive_failures += 1
            
            # Wait for next interval
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.interval_seconds,
                )
                break  # Stop event set
            except asyncio.TimeoutError:
                pass  # Normal timeout, continue loop
    
    async def _send_heartbeat(self) -> None:
        """Send a single heartbeat."""
        if not self._client or not self.config.url:
            return
        
        # Build health status
        status = await self._get_health_status()
        
        # Prepare request
        headers = {}
        if self.config.token and self.config.token.get_secret_value():
            headers["Authorization"] = f"Bearer {self.config.token.get_secret_value()}"
        
        # Send heartbeat
        method = self.config.method.upper()
        
        if method == "GET":
            response = await self._client.get(
                self.config.url,
                headers=headers,
                params=status if self.config.include_status else None,
            )
        else:
            response = await self._client.post(
                self.config.url,
                headers=headers,
                json=status if self.config.include_status else None,
            )
        
        if response.status_code < 400:
            self._last_success = asyncio.get_event_loop().time()
            self._consecutive_failures = 0
            logger.debug("Heartbeat sent successfully")
        else:
            self._consecutive_failures += 1
            logger.warning(
                "Heartbeat failed: %d %s",
                response.status_code,
                response.text[:100],
            )
    
    async def _get_health_status(self) -> dict[str, Any]:
        """Get current health status for heartbeat.
        
        Returns:
            Status dictionary with health info.
        """
        status: dict[str, Any] = {
            "status": "ok",
            "service": "ninjaone-jira-integration",
        }
        
        if self.db:
            try:
                stats = await self.db.get_stats()
                status["db_stats"] = stats
            except Exception as e:
                status["status"] = "degraded"
                status["db_error"] = str(e)
        
        if self._consecutive_failures > 0:
            status["consecutive_failures"] = self._consecutive_failures
        
        return status

"""
Webhook endpoints for NinjaOne callbacks.

Handles:
- Device webhooks: /webhook/device
- Alert webhooks: /webhook/alert

Both endpoints verify the shared secret and enqueue jobs
for asynchronous processing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request, HTTPException, Header, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ninjaone_jira_integration.config.models import AppConfig
from ninjaone_jira_integration.store.db import DatabaseManager
from ninjaone_jira_integration.store.jobs import JobStore, JobType

logger = logging.getLogger(__name__)

router = APIRouter()


class WebhookPayload(BaseModel):
    """Base webhook payload model."""
    
    event_type: str | None = None
    timestamp: str | None = None
    data: dict[str, Any] = {}


class DeviceWebhookPayload(BaseModel):
    """Device webhook payload."""
    
    id: int
    deviceId: int | None = None  # Some webhooks use this format
    event: str | None = None
    data: dict[str, Any] = {}


class AlertWebhookPayload(BaseModel):
    """Alert webhook payload."""
    
    id: int
    alertId: int | None = None
    deviceId: int | None = None
    event: str | None = None
    data: dict[str, Any] = {}


def verify_webhook_signature(
    payload: bytes,
    signature: str | None,
    secret: str,
) -> bool:
    """Verify webhook signature.
    
    NinjaOne webhooks can be configured with a shared secret.
    This verifies the HMAC-SHA256 signature.
    
    Args:
        payload: Raw request body.
        signature: X-Webhook-Signature header value.
        secret: Configured shared secret.
        
    Returns:
        True if signature is valid or secret is not configured.
    """
    if not secret:
        # No secret configured, skip verification
        return True
    
    if not signature:
        return False
    
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    
    # Handle various header formats
    # Some systems prefix with 'sha256='
    if signature.startswith("sha256="):
        signature = signature[7:]
    
    return hmac.compare_digest(expected.lower(), signature.lower())


async def get_config_and_db(request: Request) -> tuple[AppConfig, DatabaseManager]:
    """Get config and database from request app state.
    
    Args:
        request: FastAPI request.
        
    Returns:
        Tuple of (config, db).
    """
    return request.app.state.config, request.app.state.db


@router.post("/device")
async def device_webhook(
    request: Request,
    x_webhook_signature: str | None = Header(None),
    x_correlation_id: str | None = Header(None),
) -> JSONResponse:
    """Handle device webhook from NinjaOne.
    
    Enqueues a device sync job for asynchronous processing.
    
    Expected payload format:
    {
        "id": 12345,
        "event": "device.updated",
        "data": { ... device data ... }
    }
    """
    config, db = await get_config_and_db(request)
    
    # Read raw body for signature verification
    body = await request.body()
    
    # Verify signature
    secret = config.server.webhook.secret.get_secret_value() if config.server.webhook.secret else ""
    if not verify_webhook_signature(body, x_webhook_signature, secret):
        logger.warning("Invalid webhook signature for device webhook")
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Parse payload
    try:
        payload_dict = json.loads(body)
        
        # NinjaOne activity webhooks have:
        #   - "id": Activity ID (not the device ID)
        #   - "deviceId": Actual NinjaOne device ID to use for lookup
        # Prioritize deviceId for device lookup
        device_id = payload_dict.get("deviceId")
        if device_id is None:
            # Fallback to id only if deviceId not present (legacy format)
            device_id = payload_dict.get("id")
        
        if not device_id:
            raise HTTPException(status_code=400, detail="Missing device ID")
        
        device_id = int(device_id)
        
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("Invalid webhook payload: %s", str(e))
        raise HTTPException(status_code=400, detail="Invalid payload")
    
    # Generate correlation ID if not provided
    correlation_id = x_correlation_id or str(uuid.uuid4())
    
    # Enqueue job
    job_store = JobStore(db)
    job_id = await job_store.enqueue(
        job_type=JobType.DEVICE_SYNC,
        job_key=str(device_id),
        payload=payload_dict,
        correlation_id=correlation_id,
    )
    
    logger.info(
        "Enqueued device sync job %d for device %d (correlation_id=%s)",
        job_id,
        device_id,
        correlation_id,
    )
    
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "job_id": job_id,
            "correlation_id": correlation_id,
        },
    )


@router.post("/alert")
async def alert_webhook(
    request: Request,
    x_webhook_signature: str | None = Header(None),
    x_correlation_id: str | None = Header(None),
) -> JSONResponse:
    """Handle alert webhook from NinjaOne.
    
    Enqueues an alert processing job for asynchronous processing.
    
    Expected payload format:
    {
        "id": 12345,
        "alertId": 12345,
        "deviceId": 67890,
        "event": "alert.created",
        "data": { ... alert data ... }
    }
    """
    config, db = await get_config_and_db(request)
    
    # Read raw body for signature verification
    body = await request.body()
    
    # Verify signature
    secret = config.server.webhook.secret.get_secret_value() if config.server.webhook.secret else ""
    if not verify_webhook_signature(body, x_webhook_signature, secret):
        logger.warning("Invalid webhook signature for alert webhook")
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Parse payload
    try:
        payload_dict = json.loads(body)
        alert_id = payload_dict.get("id") or payload_dict.get("alertId")
        
        if not alert_id:
            raise HTTPException(status_code=400, detail="Missing alert ID")
        
        alert_id = int(alert_id)
        
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("Invalid webhook payload: %s", str(e))
        raise HTTPException(status_code=400, detail="Invalid payload")
    
    # Generate correlation ID if not provided
    correlation_id = x_correlation_id or str(uuid.uuid4())
    
    # Enqueue job
    job_store = JobStore(db)
    job_id = await job_store.enqueue(
        job_type=JobType.ALERT_PROCESS,
        job_key=str(alert_id),
        payload=payload_dict,
        correlation_id=correlation_id,
    )
    
    logger.info(
        "Enqueued alert processing job %d for alert %d (correlation_id=%s)",
        job_id,
        alert_id,
        correlation_id,
    )
    
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "job_id": job_id,
            "correlation_id": correlation_id,
        },
    )

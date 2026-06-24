"""
Alert processor for creating Jira issues from NinjaOne alerts.

Handles:
- Alert to issue field mapping
- Deduplication (one issue per alert)
- Asset linking (link issue to device's asset)
- State tracking
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
from ninjaone_jira_integration.config.models import AppConfig
from ninjaone_jira_integration.store.db import DatabaseManager
from ninjaone_jira_integration.store.mappings import AlertMapping, DeviceMapping, MappingStore

logger = logging.getLogger(__name__)


class AlertAction(str, Enum):
    """Action taken for an alert."""
    
    CREATED = "created"
    EXISTS = "exists"  # Issue already exists for this alert
    SKIPPED = "skipped"  # Alert type not configured for issues
    FAILED = "failed"


@dataclass
class AlertResult:
    """Result of processing a single alert."""
    
    alert_id: str
    device_id: int | None
    action: AlertAction
    jira_issue_key: str | None = None
    jira_issue_id: str | None = None
    jira_asset_id: str | None = None
    error: str | None = None


class AlertProcessor:
    """Processes NinjaOne alerts to create Jira issues.
    
    Responsibilities:
    - Map alert fields to Jira issue fields
    - Create issues with appropriate fields
    - Link issues to the corresponding asset
    - Track alert-to-issue mappings for deduplication
    """
    
    def __init__(
        self,
        config: AppConfig,
        ninja_client: NinjaOneClient,
        jira_client: JiraAssetsClient,
        db: DatabaseManager,
    ):
        """Initialize alert processor.
        
        Args:
            config: Application configuration.
            ninja_client: NinjaOne API client.
            jira_client: Jira Assets API client.
            db: Database manager.
        """
        self.config = config
        self.ninja_client = ninja_client
        self.jira_client = jira_client
        self.db = db
        self.mapping_store = MappingStore(db)
    
    async def process_alert(
        self,
        alert_id: str,
        alert: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> AlertResult:
        """Process a single alert.

        Args:
            alert_id: NinjaOne alert UID (UUID string).
            alert: Optional alert data (will be fetched if not provided).
            dry_run: If True, don't actually create issue.
            
        Returns:
            AlertResult with action taken.
        """
        try:
            # Check if we already have an issue for this alert
            existing = await self.mapping_store.get_alert_mapping(alert_id)
            if existing:
                logger.debug(
                    "Alert %s already has issue %s",
                    alert_id,
                    existing.jira_issue_key,
                )
                return AlertResult(
                    alert_id=alert_id,
                    device_id=existing.ninja_device_id,
                    action=AlertAction.EXISTS,
                    jira_issue_key=existing.jira_issue_key,
                    jira_issue_id=existing.jira_issue_id,
                )
            
            # Fetch alert if not provided
            if alert is None:
                alert = await self.ninja_client.get_alert(alert_id)
            
            device_id = alert.get("deviceId")
            
            # Check if this alert type should create issues
            if not self._should_create_issue(alert):
                logger.debug("Alert %s type not configured for issue creation", alert_id)
                return AlertResult(
                    alert_id=alert_id,
                    device_id=device_id,
                    action=AlertAction.SKIPPED,
                )
            
            # Create the issue
            return await self._create_issue(alert_id, alert, device_id, dry_run)
            
        except Exception as e:
            logger.error("Failed to process alert %s: %s", alert_id, str(e))
            return AlertResult(
                alert_id=alert_id,
                device_id=None,
                action=AlertAction.FAILED,
                error=str(e),
            )
    
    def _should_create_issue(self, alert: dict[str, Any]) -> bool:
        """Check if an alert should create an issue.
        
        Can be extended to filter by:
        - Alert severity
        - Alert source type
        - Condition type
        
        Args:
            alert: Alert data.
            
        Returns:
            True if an issue should be created.
        """
        # If severity filter is configured, check it
        severity = (alert.get("severity") or "").upper()
        min_severity = self.config.issues.min_severity
        
        if min_severity:
            severity_order = ["NONE", "MINOR", "MODERATE", "MAJOR", "CRITICAL"]
            try:
                alert_level = severity_order.index(severity) if severity in severity_order else 0
                min_level = severity_order.index(min_severity.upper()) if min_severity.upper() in severity_order else 0
                if alert_level < min_level:
                    return False
            except ValueError:
                pass  # Unknown severity, allow it
        
        # Check source type if configured
        source_types = self.config.issues.source_types
        if source_types:
            source_type = alert.get("sourceType", "")
            if source_type not in source_types:
                return False
        
        return True
    
    async def _create_issue(
        self,
        alert_id: str,
        alert: dict[str, Any],
        device_id: int | None,
        dry_run: bool,
    ) -> AlertResult:
        """Create a Jira issue for an alert.

        Args:
            alert_id: NinjaOne alert UID (UUID string).
            alert: Alert data.
            device_id: Associated device ID.
            dry_run: If True, don't actually create.
            
        Returns:
            AlertResult.
        """
        # Build issue fields
        summary = self._build_summary(alert)
        description = self._build_description(alert)
        additional_fields = self._map_alert_fields(alert)
        
        # Get linked asset if device is known
        jira_asset_id: str | None = None
        if device_id:
            device_mapping = await self.mapping_store.get_device_mapping(device_id)
            if device_mapping:
                jira_asset_id = device_mapping.jira_asset_id
        
        if dry_run:
            logger.info(
                "[DRY RUN] Would create issue for alert %s: %s",
                alert_id,
                summary,
            )
            return AlertResult(
                alert_id=alert_id,
                device_id=device_id,
                action=AlertAction.CREATED,
                jira_asset_id=jira_asset_id,
            )
        
        try:
            # Create the issue
            issue = await self.jira_client.create_issue(
                project_key=self.config.issues.project_key,
                issue_type_id=self.config.issues.issue_type_id,
                summary=summary,
                description=description,
                fields=additional_fields,
            )
            
            issue_key = issue.get("key", "")
            issue_id = str(issue.get("id", ""))
            
            logger.info(
                "Created issue %s for alert %s",
                issue_key,
                alert_id,
            )
            
            # Link to asset if available
            if jira_asset_id and self.config.issues.asset_field_id:
                try:
                    await self.jira_client.link_asset_to_issue(
                        issue_key=issue_key,
                        asset_id=jira_asset_id,
                        custom_field_id=self.config.issues.asset_field_id,
                    )
                    logger.debug("Linked asset %s to issue %s", jira_asset_id, issue_key)
                except Exception as e:
                    logger.warning(
                        "Failed to link asset to issue %s: %s",
                        issue_key,
                        str(e),
                    )
            
            # Persist the mapping
            mapping = AlertMapping(
                ninja_alert_id=alert_id,
                jira_issue_key=issue_key,
                jira_issue_id=issue_id,
                ninja_device_id=device_id,
            )
            await self.mapping_store.upsert_alert_mapping(mapping)
            
            return AlertResult(
                alert_id=alert_id,
                device_id=device_id,
                action=AlertAction.CREATED,
                jira_issue_key=issue_key,
                jira_issue_id=issue_id,
                jira_asset_id=jira_asset_id,
            )
            
        except Exception as e:
            logger.error("Failed to create issue for alert %s: %s", alert_id, str(e))
            return AlertResult(
                alert_id=alert_id,
                device_id=device_id,
                action=AlertAction.FAILED,
                error=str(e),
            )
    
    def _build_summary(self, alert: dict[str, Any]) -> str:
        """Build issue summary from alert data.
        
        Args:
            alert: Alert data.
            
        Returns:
            Issue summary string.
        """
        template = self.config.issues.summary_template or "[NinjaOne] {message}"
        
        # Available template variables
        variables = {
            "message": alert.get("message", "Unknown Alert"),
            "device_name": alert.get("deviceName") or (alert.get("device") or {}).get("systemName", "Unknown"),
            "severity": alert.get("severity", "Unknown"),
            "source_type": alert.get("sourceType", "Unknown"),
            "condition": alert.get("conditionName", alert.get("message", "")),
        }
        
        try:
            return template.format(**variables)
        except KeyError:
            return template.format_map(variables)
    
    def _build_description(self, alert: dict[str, Any]) -> str:
        """Build issue description from alert data.
        
        Args:
            alert: Alert data.
            
        Returns:
            Issue description string.
        """
        parts = []
        
        # Header
        parts.append("h3. Alert Details")
        parts.append("")
        
        # Alert info
        parts.append(f"*Message:* {alert.get('message', 'N/A')}")
        parts.append(f"*Severity:* {alert.get('severity', 'N/A')}")
        parts.append(f"*Source Type:* {alert.get('sourceType', 'N/A')}")
        parts.append(f"*Condition:* {alert.get('conditionName', 'N/A')}")
        parts.append("")
        
        # Device info
        device_name = alert.get("deviceName") or (alert.get("device") or {}).get("systemName")
        if device_name:
            parts.append(f"*Device:* {device_name}")

        org_name = alert.get("organizationName") or (alert.get("device") or {}).get("organizationName")
        if org_name:
            parts.append(f"*Organization:* {org_name}")
        
        parts.append("")
        
        # Timestamps
        created_time = alert.get("createTime", alert.get("timestamp"))
        if created_time:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(float(created_time), tz=timezone.utc)
                parts.append(f"*Alert Created:* {dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            except (ValueError, TypeError, OSError):
                parts.append(f"*Alert Created:* {created_time}")

        # NinjaOne Reference
        parts.append("")
        parts.append(f"*NinjaOne Alert UID:* {alert.get('uid', 'N/A')}")
        parts.append(f"*NinjaOne Device ID:* {alert.get('deviceId', 'N/A')}")
        
        return "\n".join(parts)
    
    def _map_alert_fields(self, alert: dict[str, Any]) -> dict[str, Any]:
        """Map alert data to additional Jira issue fields.
        
        Args:
            alert: Alert data.
            
        Returns:
            Dictionary of additional fields.
        """
        fields: dict[str, Any] = {}
        
        # Apply configured field mappings
        for mapping in self.config.issues.field_mappings:
            source_value = self._get_alert_value(alert, mapping.source)
            
            if source_value is None and mapping.default_value is not None:
                source_value = mapping.default_value
            
            if source_value is not None:
                fields[mapping.jira_field_id] = self._format_field_value(
                    source_value,
                    mapping.jira_field_type,
                )
        
        # Set priority based on severity if configured
        if self.config.issues.severity_to_priority_mapping:
            severity = (alert.get("severity") or "").upper()
            priority_id = self.config.issues.severity_to_priority_mapping.get(severity)
            if priority_id:
                fields["priority"] = {"id": priority_id}
        
        # Add labels if configured
        if self.config.issues.default_labels:
            fields["labels"] = self.config.issues.default_labels
        
        return fields
    
    def _get_alert_value(self, alert: dict[str, Any], path: str) -> Any:
        """Get a value from alert data using dot notation.
        
        Args:
            alert: Alert data.
            path: Dot-separated path.
            
        Returns:
            Value at path, or None.
        """
        parts = path.split(".")
        current = alert
        
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        
        return current
    
    def _format_field_value(
        self,
        value: Any,
        field_type: str | None,
    ) -> Any:
        """Format a value for a Jira field.
        
        Args:
            value: Value to format.
            field_type: Jira field type.
            
        Returns:
            Formatted value.
        """
        if field_type == "select":
            return {"value": str(value)}
        elif field_type == "user":
            return {"accountId": str(value)}
        elif field_type == "date":
            return str(value)[:10] if value else None
        else:
            return value

"""
Sync engine orchestration.

Coordinates the full sync workflow:
1. Fetch devices from NinjaOne
2. Resolve identity (find/create asset)
3. Compute diff (what needs updating)
4. Apply changes (create/update assets)
5. Track results and statistics
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
from ninjaone_jira_integration.config.models import AppConfig
from ninjaone_jira_integration.store.db import DatabaseManager
from ninjaone_jira_integration.store.mappings import DeviceMapping, MappingStore
from ninjaone_jira_integration.sync.mapper import DeviceMapper
from ninjaone_jira_integration.sync.matching import IdentityResolver, MatchMethod

logger = logging.getLogger(__name__)


class SyncAction(str, Enum):
    """Action taken during sync."""
    
    CREATED = "created"
    UPDATED = "updated"
    SKIPPED = "skipped"  # No changes needed
    FAILED = "failed"


@dataclass
class SyncResult:
    """Result of syncing a single device."""
    
    device_id: int
    device_name: str | None
    action: SyncAction
    jira_asset_id: str | None = None
    jira_asset_key: str | None = None
    match_method: MatchMethod | None = None
    changes: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class SyncSummary:
    """Summary of a sync operation."""
    
    total_devices: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    
    results: list[SyncResult] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.total_devices == 0:
            return 100.0
        return ((self.total_devices - self.failed) / self.total_devices) * 100
    
    def add_result(self, result: SyncResult) -> None:
        """Add a sync result to the summary."""
        self.total_devices += 1
        
        if result.action == SyncAction.CREATED:
            self.created += 1
        elif result.action == SyncAction.UPDATED:
            self.updated += 1
        elif result.action == SyncAction.SKIPPED:
            self.skipped += 1
        elif result.action == SyncAction.FAILED:
            self.failed += 1
            if result.error:
                self.errors.append((result.device_id, result.error))
        
        self.results.append(result)


def compute_attribute_diff(
    current_attrs: list[dict[str, Any]],
    new_attrs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compute the difference between current and new attributes.
    
    Only returns attributes that have actually changed.
    
    Args:
        current_attrs: Current Jira object attributes.
        new_attrs: New mapped attributes.
        
    Returns:
        Tuple of (changed_attributes, change_descriptions).
    """
    changed = []
    changes = []
    
    # Build lookup of current values by attribute ID
    current_lookup: dict[str, Any] = {}
    for attr in current_attrs:
        attr_id = str(attr.get("objectTypeAttributeId", attr.get("objectTypeAttribute", {}).get("id")))
        values = attr.get("objectAttributeValues", [])
        if values:
            current_lookup[attr_id] = values[0].get("value") if values else None
    
    # Compare new values
    for new_attr in new_attrs:
        attr_id = str(new_attr["objectTypeAttributeId"])
        new_values = new_attr.get("objectAttributeValues", [])
        new_value = new_values[0].get("value") if new_values else None
        
        current_value = current_lookup.get(attr_id)
        
        # Normalize for comparison
        if current_value is not None:
            current_value = str(current_value)
        if new_value is not None:
            new_value = str(new_value)
        
        if current_value != new_value:
            changed.append(new_attr)
            changes.append(f"{attr_id}: {current_value!r} -> {new_value!r}")
    
    return changed, changes


class SyncEngine:
    """Orchestrates sync between NinjaOne and Jira Assets.
    
    Handles:
    - Full sync of all devices
    - Single device sync
    - Dry-run mode for testing
    - Diff computation to avoid unnecessary updates
    """
    
    def __init__(
        self,
        config: AppConfig,
        ninja_client: NinjaOneClient,
        jira_client: JiraAssetsClient,
        db: DatabaseManager,
    ):
        """Initialize sync engine.
        
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
        self.mapper = DeviceMapper(config.assets)
        self.resolver = IdentityResolver(jira_client, self.mapping_store, config.assets)
    
    async def sync_device(
        self,
        device_id: int,
        device: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> SyncResult:
        """Sync a single device to Jira Assets.
        
        Args:
            device_id: NinjaOne device ID.
            device: Optional device data (will be fetched if not provided).
            dry_run: If True, don't make any changes.
            
        Returns:
            SyncResult with action taken.
        """
        try:
            # Fetch device if not provided
            if device is None:
                device = await self.ninja_client.get_device(device_id)
            
            device_name = self.mapper.extract_device_name(device)
            
            logger.debug("Syncing device %d: %s", device_id, device_name)
            
            # Resolve to existing asset or determine need to create
            match = await self.resolver.resolve(device_id, device)
            
            if match.needs_create:
                # Create new asset
                return await self._create_asset(
                    device_id, device, device_name, dry_run
                )
            else:
                # Update existing asset
                return await self._update_asset(
                    device_id, device, device_name, match, dry_run
                )
                
        except Exception as e:
            logger.error("Failed to sync device %d: %s", device_id, str(e))
            return SyncResult(
                device_id=device_id,
                device_name=None,
                action=SyncAction.FAILED,
                error=str(e),
            )
    
    async def _create_asset(
        self,
        device_id: int,
        device: dict[str, Any],
        device_name: str | None,
        dry_run: bool,
    ) -> SyncResult:
        """Create a new Jira asset for a device.
        
        Args:
            device_id: NinjaOne device ID.
            device: NinjaOne device data.
            device_name: Device name for logging.
            dry_run: If True, don't actually create.
            
        Returns:
            SyncResult.
        """
        if dry_run:
            logger.info("[DRY RUN] Would create asset for device %d (%s)", device_id, device_name)
            return SyncResult(
                device_id=device_id,
                device_name=device_name,
                action=SyncAction.CREATED,
                match_method=MatchMethod.NOT_FOUND,
            )
        
        asset_id, asset = await self.resolver.create_and_map(device_id, device)
        
        if asset_id:
            return SyncResult(
                device_id=device_id,
                device_name=device_name,
                action=SyncAction.CREATED,
                jira_asset_id=asset_id,
                jira_asset_key=asset.get("objectKey") if asset else None,
                match_method=MatchMethod.CREATED,
            )
        else:
            return SyncResult(
                device_id=device_id,
                device_name=device_name,
                action=SyncAction.FAILED,
                error="Failed to create asset",
            )
    
    async def _update_asset(
        self,
        device_id: int,
        device: dict[str, Any],
        device_name: str | None,
        match: "MatchResult",
        dry_run: bool,
    ) -> SyncResult:
        """Update an existing Jira asset.
        
        Only updates if there are actual changes.
        
        Args:
            device_id: NinjaOne device ID.
            device: NinjaOne device data.
            device_name: Device name for logging.
            match: Match result with existing asset.
            dry_run: If True, don't actually update.
            
        Returns:
            SyncResult.
        """
        assert match.jira_asset_id is not None
        
        # Map device to new attributes
        new_attrs = self.mapper.map_device(device)
        
        # Get current attributes
        current_attrs = match.existing_asset.get("attributes", []) if match.existing_asset else []
        
        # Compute diff
        changed_attrs, changes = compute_attribute_diff(current_attrs, new_attrs)
        
        if not changed_attrs:
            logger.debug(
                "Device %d (%s) - no changes needed",
                device_id,
                device_name,
            )
            
            # Persist mapping if matched by serial (for future lookups)
            if match.method == MatchMethod.SERIAL_NUMBER and not dry_run:
                await self.resolver.persist_mapping(
                    device_id, device, match.jira_asset_id, match.jira_asset_key
                )
            
            return SyncResult(
                device_id=device_id,
                device_name=device_name,
                action=SyncAction.SKIPPED,
                jira_asset_id=match.jira_asset_id,
                jira_asset_key=match.jira_asset_key,
                match_method=match.method,
            )
        
        logger.info(
            "Device %d (%s) - %d attribute(s) changed",
            device_id,
            device_name,
            len(changed_attrs),
        )
        
        if dry_run:
            logger.info("[DRY RUN] Would update asset %s: %s", match.jira_asset_key, changes)
            return SyncResult(
                device_id=device_id,
                device_name=device_name,
                action=SyncAction.UPDATED,
                jira_asset_id=match.jira_asset_id,
                jira_asset_key=match.jira_asset_key,
                match_method=match.method,
                changes=changes,
            )
        
        try:
            await self.jira_client.update_object(match.jira_asset_id, changed_attrs)
            
            # Persist mapping
            await self.resolver.persist_mapping(
                device_id, device, match.jira_asset_id, match.jira_asset_key
            )
            
            return SyncResult(
                device_id=device_id,
                device_name=device_name,
                action=SyncAction.UPDATED,
                jira_asset_id=match.jira_asset_id,
                jira_asset_key=match.jira_asset_key,
                match_method=match.method,
                changes=changes,
            )
            
        except Exception as e:
            logger.error(
                "Failed to update asset %s: %s",
                match.jira_asset_id,
                str(e),
            )
            return SyncResult(
                device_id=device_id,
                device_name=device_name,
                action=SyncAction.FAILED,
                jira_asset_id=match.jira_asset_id,
                jira_asset_key=match.jira_asset_key,
                error=str(e),
            )
    
    async def sync_all(
        self,
        dry_run: bool = False,
        progress_callback: Any = None,
    ) -> SyncSummary:
        """Sync all devices from NinjaOne to Jira Assets.
        
        Args:
            dry_run: If True, don't make any changes.
            progress_callback: Optional callback(current, total) for progress.
            
        Returns:
            SyncSummary with all results.
        """
        summary = SyncSummary()
        
        logger.info("Starting full sync (dry_run=%s)", dry_run)
        
        # Fetch all devices with pagination
        device_count = 0
        async for device in self.ninja_client.get_devices_detailed():
            device_id = device.get("id")
            if not device_id:
                continue
            
            device_count += 1
            
            # Sync this device
            result = await self.sync_device(device_id, device, dry_run)
            summary.add_result(result)
            
            # Progress callback
            if progress_callback:
                progress_callback(device_count, None)  # Total unknown during streaming
            
            # Periodic logging
            if device_count % 100 == 0:
                logger.info(
                    "Progress: %d devices processed (%d created, %d updated, %d skipped, %d failed)",
                    device_count,
                    summary.created,
                    summary.updated,
                    summary.skipped,
                    summary.failed,
                )
        
        logger.info(
            "Sync complete: %d total, %d created, %d updated, %d skipped, %d failed (%.1f%% success)",
            summary.total_devices,
            summary.created,
            summary.updated,
            summary.skipped,
            summary.failed,
            summary.success_rate,
        )
        
        return summary
    
    async def sync_devices_batch(
        self,
        device_ids: list[int],
        dry_run: bool = False,
    ) -> SyncSummary:
        """Sync a batch of specific devices.
        
        Args:
            device_ids: List of NinjaOne device IDs.
            dry_run: If True, don't make any changes.
            
        Returns:
            SyncSummary with all results.
        """
        summary = SyncSummary()
        
        for device_id in device_ids:
            result = await self.sync_device(device_id, dry_run=dry_run)
            summary.add_result(result)
        
        return summary

"""
Mapping store for device and alert ID mappings.

Provides persistent storage for:
- NinjaOne device ID ↔ Jira asset ID
- NinjaOne alert ID ↔ Jira issue key

Used for idempotency and identity resolution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiosqlite

from ninjaone_jira_integration.store.db import DatabaseManager

logger = logging.getLogger(__name__)


@dataclass
class DeviceMapping:
    """Represents a device-to-asset mapping."""
    
    ninja_device_id: int
    jira_asset_id: str
    jira_asset_key: str | None = None
    serial_number: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    
    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "DeviceMapping":
        """Create from database row.
        
        Args:
            row: Database row.
            
        Returns:
            DeviceMapping instance.
        """
        return cls(
            ninja_device_id=row["ninja_device_id"],
            jira_asset_id=row["jira_asset_id"],
            jira_asset_key=row["jira_asset_key"],
            serial_number=row["serial_number"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
        )


@dataclass
class AlertMapping:
    """Represents an alert-to-issue mapping."""
    
    ninja_alert_id: int
    jira_issue_key: str
    jira_issue_id: str | None = None
    ninja_device_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    
    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "AlertMapping":
        """Create from database row.
        
        Args:
            row: Database row.
            
        Returns:
            AlertMapping instance.
        """
        return cls(
            ninja_alert_id=row["ninja_alert_id"],
            jira_issue_key=row["jira_issue_key"],
            jira_issue_id=row["jira_issue_id"],
            ninja_device_id=row["ninja_device_id"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
        )


class MappingStore:
    """Store for device and alert mappings.
    
    Provides CRUD operations for mappings with proper
    transaction handling and conflict resolution.
    """
    
    def __init__(self, db: DatabaseManager):
        """Initialize mapping store.
        
        Args:
            db: Database manager instance.
        """
        self.db = db
    
    # =========================================================================
    # Device Mappings
    # =========================================================================
    
    async def get_device_mapping(
        self,
        ninja_device_id: int,
    ) -> DeviceMapping | None:
        """Get device mapping by NinjaOne device ID.
        
        Args:
            ninja_device_id: NinjaOne device ID.
            
        Returns:
            DeviceMapping or None if not found.
        """
        row = await self.db.fetch_one(
            """
            SELECT * FROM device_mappings 
            WHERE ninja_device_id = ?
            """,
            (ninja_device_id,),
        )
        
        return DeviceMapping.from_row(row) if row else None
    
    async def get_device_mapping_by_asset(
        self,
        jira_asset_id: str,
    ) -> DeviceMapping | None:
        """Get device mapping by Jira asset ID.
        
        Args:
            jira_asset_id: Jira asset ID.
            
        Returns:
            DeviceMapping or None if not found.
        """
        row = await self.db.fetch_one(
            """
            SELECT * FROM device_mappings 
            WHERE jira_asset_id = ?
            """,
            (jira_asset_id,),
        )
        
        return DeviceMapping.from_row(row) if row else None
    
    async def get_device_mapping_by_serial(
        self,
        serial_number: str,
    ) -> DeviceMapping | None:
        """Get device mapping by serial number.
        
        Args:
            serial_number: Device serial number.
            
        Returns:
            DeviceMapping or None if not found.
        """
        row = await self.db.fetch_one(
            """
            SELECT * FROM device_mappings 
            WHERE serial_number = ?
            """,
            (serial_number,),
        )
        
        return DeviceMapping.from_row(row) if row else None
    
    async def upsert_device_mapping(
        self,
        mapping: DeviceMapping,
    ) -> DeviceMapping:
        """Insert or update a device mapping.
        
        Uses UPSERT to handle conflicts atomically.
        
        Args:
            mapping: DeviceMapping to save.
            
        Returns:
            Saved mapping.
        """
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO device_mappings 
                    (ninja_device_id, jira_asset_id, jira_asset_key, serial_number, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(ninja_device_id) DO UPDATE SET
                    jira_asset_id = excluded.jira_asset_id,
                    jira_asset_key = excluded.jira_asset_key,
                    serial_number = excluded.serial_number,
                    updated_at = datetime('now')
                """,
                (
                    mapping.ninja_device_id,
                    mapping.jira_asset_id,
                    mapping.jira_asset_key,
                    mapping.serial_number,
                ),
            )
        
        logger.debug(
            "Upserted device mapping: %d -> %s",
            mapping.ninja_device_id,
            mapping.jira_asset_id,
        )
        
        return mapping
    
    async def delete_device_mapping(
        self,
        ninja_device_id: int,
    ) -> bool:
        """Delete a device mapping.
        
        Args:
            ninja_device_id: NinjaOne device ID.
            
        Returns:
            True if deleted, False if not found.
        """
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM device_mappings WHERE ninja_device_id = ?",
                (ninja_device_id,),
            )
            return cursor.rowcount > 0
    
    async def get_all_device_mappings(
        self,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[DeviceMapping]:
        """Get all device mappings with pagination.
        
        Args:
            limit: Maximum number of mappings.
            offset: Offset for pagination.
            
        Returns:
            List of device mappings.
        """
        rows = await self.db.fetch_all(
            """
            SELECT * FROM device_mappings 
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        
        return [DeviceMapping.from_row(row) for row in rows]
    
    async def count_device_mappings(self) -> int:
        """Count total device mappings.
        
        Returns:
            Number of device mappings.
        """
        row = await self.db.fetch_one(
            "SELECT COUNT(*) as count FROM device_mappings"
        )
        return row["count"] if row else 0
    
    # =========================================================================
    # Alert Mappings
    # =========================================================================
    
    async def get_alert_mapping(
        self,
        ninja_alert_id: int,
    ) -> AlertMapping | None:
        """Get alert mapping by NinjaOne alert ID.
        
        Args:
            ninja_alert_id: NinjaOne alert ID.
            
        Returns:
            AlertMapping or None if not found.
        """
        row = await self.db.fetch_one(
            """
            SELECT * FROM alert_mappings 
            WHERE ninja_alert_id = ?
            """,
            (ninja_alert_id,),
        )
        
        return AlertMapping.from_row(row) if row else None
    
    async def get_alert_mapping_by_issue(
        self,
        jira_issue_key: str,
    ) -> AlertMapping | None:
        """Get alert mapping by Jira issue key.
        
        Args:
            jira_issue_key: Jira issue key.
            
        Returns:
            AlertMapping or None if not found.
        """
        row = await self.db.fetch_one(
            """
            SELECT * FROM alert_mappings 
            WHERE jira_issue_key = ?
            """,
            (jira_issue_key,),
        )
        
        return AlertMapping.from_row(row) if row else None
    
    async def upsert_alert_mapping(
        self,
        mapping: AlertMapping,
    ) -> AlertMapping:
        """Insert or update an alert mapping.
        
        Args:
            mapping: AlertMapping to save.
            
        Returns:
            Saved mapping.
        """
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO alert_mappings 
                    (ninja_alert_id, jira_issue_key, jira_issue_id, ninja_device_id, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(ninja_alert_id) DO UPDATE SET
                    jira_issue_key = excluded.jira_issue_key,
                    jira_issue_id = excluded.jira_issue_id,
                    ninja_device_id = excluded.ninja_device_id,
                    updated_at = datetime('now')
                """,
                (
                    mapping.ninja_alert_id,
                    mapping.jira_issue_key,
                    mapping.jira_issue_id,
                    mapping.ninja_device_id,
                ),
            )
        
        logger.debug(
            "Upserted alert mapping: %d -> %s",
            mapping.ninja_alert_id,
            mapping.jira_issue_key,
        )
        
        return mapping
    
    async def delete_alert_mapping(
        self,
        ninja_alert_id: int,
    ) -> bool:
        """Delete an alert mapping.
        
        Args:
            ninja_alert_id: NinjaOne alert ID.
            
        Returns:
            True if deleted, False if not found.
        """
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM alert_mappings WHERE ninja_alert_id = ?",
                (ninja_alert_id,),
            )
            return cursor.rowcount > 0
    
    async def get_alerts_for_device(
        self,
        ninja_device_id: int,
    ) -> list[AlertMapping]:
        """Get all alert mappings for a device.
        
        Args:
            ninja_device_id: NinjaOne device ID.
            
        Returns:
            List of alert mappings.
        """
        rows = await self.db.fetch_all(
            """
            SELECT * FROM alert_mappings 
            WHERE ninja_device_id = ?
            ORDER BY created_at DESC
            """,
            (ninja_device_id,),
        )
        
        return [AlertMapping.from_row(row) for row in rows]
    
    async def count_alert_mappings(self) -> int:
        """Count total alert mappings.
        
        Returns:
            Number of alert mappings.
        """
        row = await self.db.fetch_one(
            "SELECT COUNT(*) as count FROM alert_mappings"
        )
        return row["count"] if row else 0

"""
SQLite database initialization and management.

Uses WAL mode for better concurrency and ensures proper
connection handling for async operations.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

logger = logging.getLogger(__name__)

# SQL for creating tables
SCHEMA_SQL = """
-- Device mappings: NinjaOne device ID to Jira asset ID
CREATE TABLE IF NOT EXISTS device_mappings (
    ninja_device_id INTEGER PRIMARY KEY,
    jira_asset_id TEXT NOT NULL,
    jira_asset_key TEXT,
    serial_number TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_device_mappings_jira_asset 
    ON device_mappings(jira_asset_id);
CREATE INDEX IF NOT EXISTS idx_device_mappings_serial 
    ON device_mappings(serial_number);

-- Alert mappings: NinjaOne alert ID to Jira issue key
CREATE TABLE IF NOT EXISTS alert_mappings (
    ninja_alert_id INTEGER PRIMARY KEY,
    jira_issue_key TEXT NOT NULL,
    jira_issue_id TEXT,
    ninja_device_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_alert_mappings_jira_issue 
    ON alert_mappings(jira_issue_key);
CREATE INDEX IF NOT EXISTS idx_alert_mappings_device 
    ON alert_mappings(ninja_device_id);

-- Job queue for async processing
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,  -- 'device_sync' or 'alert_process'
    job_key TEXT NOT NULL,   -- ninja_device_id or ninja_alert_id
    payload TEXT NOT NULL,   -- JSON payload
    status TEXT NOT NULL DEFAULT 'queued',  -- queued, processing, completed, failed, dead_letter
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 8,
    last_error TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(job_type, job_key)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_type_key ON jobs(job_type, job_key);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);

-- Sync history for tracking sync operations
CREATE TABLE IF NOT EXISTS sync_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_type TEXT NOT NULL,  -- 'full', 'single', 'webhook'
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',  -- running, completed, failed
    devices_processed INTEGER DEFAULT 0,
    devices_created INTEGER DEFAULT 0,
    devices_updated INTEGER DEFAULT 0,
    devices_skipped INTEGER DEFAULT 0,
    devices_failed INTEGER DEFAULT 0,
    error_message TEXT
);

-- Heartbeat state for observability
CREATE TABLE IF NOT EXISTS heartbeat_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_database_path(config_path: str | None = None) -> Path:
    """Get the database file path.
    
    Args:
        config_path: Optional path from configuration.
        
    Returns:
        Path to database file.
    """
    if config_path:
        return Path(config_path)
    
    # Default location
    return Path("data") / "integration.db"


async def init_database(db_path: Path, wal_mode: bool = True) -> aiosqlite.Connection:
    """Initialize the database with schema.
    
    Args:
        db_path: Path to database file.
        wal_mode: Whether to enable WAL mode.
        
    Returns:
        Database connection.
    """
    # Ensure directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    logger.info("Initializing database at %s", db_path)
    
    # Connect to database
    conn = await aiosqlite.connect(db_path)
    
    # Enable WAL mode for better concurrency
    if wal_mode:
        await conn.execute("PRAGMA journal_mode=WAL")
        logger.debug("Enabled WAL mode")
    
    # Set other pragmas for performance
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
    
    # Create tables
    await conn.executescript(SCHEMA_SQL)
    await conn.commit()
    
    logger.info("Database initialized successfully")
    return conn


class DatabaseManager:
    """Manages database connections and lifecycle.
    
    Usage:
        async with DatabaseManager("data/integration.db") as db:
            async with db.connection() as conn:
                await conn.execute(...)
    """
    
    def __init__(
        self,
        db_path: str | Path,
        wal_mode: bool = True,
    ):
        """Initialize database manager.
        
        Args:
            db_path: Path to database file.
            wal_mode: Whether to enable WAL mode.
        """
        self.db_path = Path(db_path)
        self.wal_mode = wal_mode
        self._conn: aiosqlite.Connection | None = None
    
    async def initialize(self) -> None:
        """Initialize the database connection and schema."""
        if self._conn is None:
            self._conn = await init_database(self.db_path, self.wal_mode)
    
    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
    
    async def __aenter__(self) -> "DatabaseManager":
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
    
    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Get a database connection.
        
        Yields:
            Database connection.
        """
        if self._conn is None:
            await self.initialize()
        
        assert self._conn is not None
        yield self._conn
    
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Execute operations within a transaction.
        
        The transaction is committed on success or rolled back on error.
        
        Yields:
            Database connection.
        """
        async with self.connection() as conn:
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
    
    async def execute(
        self,
        sql: str,
        params: tuple = (),
    ) -> aiosqlite.Cursor:
        """Execute a SQL statement.
        
        Args:
            sql: SQL statement.
            params: Parameters for the statement.
            
        Returns:
            Cursor.
        """
        async with self.connection() as conn:
            cursor = await conn.execute(sql, params)
            await conn.commit()
            return cursor
    
    async def fetch_one(
        self,
        sql: str,
        params: tuple = (),
    ) -> aiosqlite.Row | None:
        """Fetch a single row.
        
        Args:
            sql: SQL query.
            params: Query parameters.
            
        Returns:
            Row or None.
        """
        async with self.connection() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(sql, params)
            return await cursor.fetchone()
    
    async def fetch_all(
        self,
        sql: str,
        params: tuple = (),
    ) -> list[aiosqlite.Row]:
        """Fetch all rows.
        
        Args:
            sql: SQL query.
            params: Query parameters.
            
        Returns:
            List of rows.
        """
        async with self.connection() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(sql, params)
            return await cursor.fetchall()
    
    async def get_stats(self) -> dict[str, int]:
        """Get database statistics.
        
        Returns:
            Dictionary with table counts.
        """
        stats = {}
        
        async with self.connection() as conn:
            for table in ["device_mappings", "alert_mappings", "jobs"]:
                cursor = await conn.execute(f"SELECT COUNT(*) FROM {table}")
                row = await cursor.fetchone()
                stats[table] = row[0] if row else 0
        
        return stats

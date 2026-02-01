"""
Job queue store for async processing.

Provides:
- Job enqueueing with deduplication
- Atomic job claiming (prevents double-processing)
- Status tracking and retry management
- Dead-letter handling
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import aiosqlite

from ninjaone_jira_integration.store.db import DatabaseManager

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Job status values."""
    
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class JobType(str, Enum):
    """Job type values."""
    
    DEVICE_SYNC = "device_sync"
    ALERT_PROCESS = "alert_process"


@dataclass
class Job:
    """Represents a queued job."""
    
    id: int
    job_type: JobType
    job_key: str
    payload: dict[str, Any]
    status: JobStatus
    attempts: int = 0
    max_attempts: int = 8
    last_error: str | None = None
    correlation_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    
    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "Job":
        """Create from database row.
        
        Args:
            row: Database row.
            
        Returns:
            Job instance.
        """
        return cls(
            id=row["id"],
            job_type=JobType(row["job_type"]),
            job_key=row["job_key"],
            payload=json.loads(row["payload"]),
            status=JobStatus(row["status"]),
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            last_error=row["last_error"],
            correlation_id=row["correlation_id"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        )


@dataclass
class JobStats:
    """Statistics about job queue."""
    
    queued: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    dead_letter: int = 0
    
    @property
    def total(self) -> int:
        """Total jobs across all statuses."""
        return self.queued + self.processing + self.completed + self.failed + self.dead_letter


class JobStore:
    """Store for job queue operations.
    
    All operations are designed to be safe under concurrent access:
    - Enqueue uses ON CONFLICT to deduplicate
    - Claim uses atomic UPDATE with status check
    - Complete/fail use transactions
    """
    
    def __init__(self, db: DatabaseManager):
        """Initialize job store.
        
        Args:
            db: Database manager instance.
        """
        self.db = db
    
    async def enqueue(
        self,
        job_type: JobType,
        job_key: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        max_attempts: int = 8,
    ) -> int:
        """Enqueue a job for processing.
        
        If a job with the same type and key already exists:
        - If queued/processing: updates payload, no new job created
        - If completed/failed: requeues the job
        
        Args:
            job_type: Type of job.
            job_key: Unique key within type (e.g., device ID).
            payload: Job payload data.
            correlation_id: Optional correlation ID for tracing.
            max_attempts: Maximum retry attempts.
            
        Returns:
            Job ID.
        """
        payload_json = json.dumps(payload)
        
        async with self.db.transaction() as conn:
            # Use UPSERT to handle existing jobs
            cursor = await conn.execute(
                """
                INSERT INTO jobs 
                    (job_type, job_key, payload, status, correlation_id, max_attempts, updated_at)
                VALUES (?, ?, ?, 'queued', ?, ?, datetime('now'))
                ON CONFLICT(job_type, job_key) DO UPDATE SET
                    payload = excluded.payload,
                    correlation_id = COALESCE(excluded.correlation_id, jobs.correlation_id),
                    status = CASE 
                        WHEN jobs.status IN ('completed', 'failed', 'dead_letter') 
                        THEN 'queued' 
                        ELSE jobs.status 
                    END,
                    attempts = CASE 
                        WHEN jobs.status IN ('completed', 'failed', 'dead_letter') 
                        THEN 0 
                        ELSE jobs.attempts 
                    END,
                    last_error = CASE 
                        WHEN jobs.status IN ('completed', 'failed', 'dead_letter') 
                        THEN NULL 
                        ELSE jobs.last_error 
                    END,
                    updated_at = datetime('now')
                RETURNING id
                """,
                (job_type.value, job_key, payload_json, correlation_id, max_attempts),
            )
            
            row = await cursor.fetchone()
            job_id = row[0] if row else 0
        
        logger.debug("Enqueued job %d: %s/%s", job_id, job_type.value, job_key)
        return job_id
    
    async def claim_next(self) -> Job | None:
        """Claim the next available job for processing.
        
        Uses atomic UPDATE to prevent race conditions:
        - Only jobs with status='queued' are claimed
        - Status is atomically changed to 'processing'
        - Returns None if no jobs available
        
        Returns:
            Claimed Job or None.
        """
        async with self.db.transaction() as conn:
            # Atomically claim the next queued job
            cursor = await conn.execute(
                """
                UPDATE jobs 
                SET status = 'processing',
                    attempts = attempts + 1,
                    started_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id = (
                    SELECT id FROM jobs 
                    WHERE status = 'queued'
                    ORDER BY created_at ASC
                    LIMIT 1
                )
                RETURNING *
                """,
            )
            
            row = await cursor.fetchone()
            
            if not row:
                return None
            
            # Need to get column names
            conn.row_factory = aiosqlite.Row
            return Job.from_row(row)
    
    async def complete(self, job_id: int) -> None:
        """Mark a job as completed.
        
        Args:
            job_id: Job ID.
        """
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                UPDATE jobs 
                SET status = 'completed',
                    completed_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (job_id,),
            )
        
        logger.debug("Completed job %d", job_id)
    
    async def fail(
        self,
        job_id: int,
        error: str,
    ) -> bool:
        """Mark a job as failed.
        
        If the job has exceeded max_attempts, it is moved to dead_letter.
        Otherwise, it is requeued for retry.
        
        Args:
            job_id: Job ID.
            error: Error message.
            
        Returns:
            True if job was dead-lettered, False if requeued for retry.
        """
        async with self.db.transaction() as conn:
            # Get current job state
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT attempts, max_attempts FROM jobs WHERE id = ?",
                (job_id,),
            )
            row = await cursor.fetchone()
            
            if not row:
                logger.warning("Job %d not found for failure", job_id)
                return False
            
            attempts = row["attempts"]
            max_attempts = row["max_attempts"]
            
            if attempts >= max_attempts:
                # Move to dead letter
                await conn.execute(
                    """
                    UPDATE jobs 
                    SET status = 'dead_letter',
                        last_error = ?,
                        completed_at = datetime('now'),
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (error, job_id),
                )
                logger.warning(
                    "Job %d moved to dead letter after %d attempts: %s",
                    job_id,
                    attempts,
                    error[:100],
                )
                return True
            else:
                # Requeue for retry
                await conn.execute(
                    """
                    UPDATE jobs 
                    SET status = 'queued',
                        last_error = ?,
                        started_at = NULL,
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (error, job_id),
                )
                logger.debug(
                    "Job %d requeued for retry (attempt %d/%d): %s",
                    job_id,
                    attempts,
                    max_attempts,
                    error[:100],
                )
                return False
    
    async def get_job(self, job_id: int) -> Job | None:
        """Get a job by ID.
        
        Args:
            job_id: Job ID.
            
        Returns:
            Job or None.
        """
        row = await self.db.fetch_one(
            "SELECT * FROM jobs WHERE id = ?",
            (job_id,),
        )
        return Job.from_row(row) if row else None
    
    async def get_job_by_key(
        self,
        job_type: JobType,
        job_key: str,
    ) -> Job | None:
        """Get a job by type and key.
        
        Args:
            job_type: Job type.
            job_key: Job key.
            
        Returns:
            Job or None.
        """
        row = await self.db.fetch_one(
            "SELECT * FROM jobs WHERE job_type = ? AND job_key = ?",
            (job_type.value, job_key),
        )
        return Job.from_row(row) if row else None
    
    async def get_stats(self) -> JobStats:
        """Get job queue statistics.
        
        Returns:
            JobStats with counts by status.
        """
        rows = await self.db.fetch_all(
            """
            SELECT status, COUNT(*) as count 
            FROM jobs 
            GROUP BY status
            """,
        )
        
        stats = JobStats()
        for row in rows:
            status = row["status"]
            count = row["count"]
            
            if status == JobStatus.QUEUED.value:
                stats.queued = count
            elif status == JobStatus.PROCESSING.value:
                stats.processing = count
            elif status == JobStatus.COMPLETED.value:
                stats.completed = count
            elif status == JobStatus.FAILED.value:
                stats.failed = count
            elif status == JobStatus.DEAD_LETTER.value:
                stats.dead_letter = count
        
        return stats
    
    async def get_dead_letter_jobs(
        self,
        limit: int = 100,
    ) -> list[Job]:
        """Get jobs in dead letter queue.
        
        Args:
            limit: Maximum jobs to return.
            
        Returns:
            List of dead letter jobs.
        """
        rows = await self.db.fetch_all(
            """
            SELECT * FROM jobs 
            WHERE status = 'dead_letter'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [Job.from_row(row) for row in rows]
    
    async def replay_dead_letter(
        self,
        job_id: int | None = None,
        limit: int | None = None,
    ) -> int:
        """Requeue dead letter jobs for retry.
        
        Args:
            job_id: Specific job ID to replay, or None for all.
            limit: Maximum jobs to replay.
            
        Returns:
            Number of jobs replayed.
        """
        async with self.db.transaction() as conn:
            if job_id:
                cursor = await conn.execute(
                    """
                    UPDATE jobs 
                    SET status = 'queued',
                        attempts = 0,
                        last_error = NULL,
                        started_at = NULL,
                        completed_at = NULL,
                        updated_at = datetime('now')
                    WHERE id = ? AND status = 'dead_letter'
                    """,
                    (job_id,),
                )
            else:
                sql = """
                    UPDATE jobs 
                    SET status = 'queued',
                        attempts = 0,
                        last_error = NULL,
                        started_at = NULL,
                        completed_at = NULL,
                        updated_at = datetime('now')
                    WHERE status = 'dead_letter'
                """
                if limit:
                    sql += f" AND id IN (SELECT id FROM jobs WHERE status = 'dead_letter' LIMIT {limit})"
                
                cursor = await conn.execute(sql)
            
            count = cursor.rowcount
        
        logger.info("Replayed %d dead letter jobs", count)
        return count
    
    async def cleanup_completed(
        self,
        older_than_days: int = 7,
    ) -> int:
        """Delete old completed jobs.
        
        Args:
            older_than_days: Age threshold in days.
            
        Returns:
            Number of jobs deleted.
        """
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """
                DELETE FROM jobs 
                WHERE status = 'completed'
                AND completed_at < datetime('now', ?)
                """,
                (f"-{older_than_days} days",),
            )
            count = cursor.rowcount
        
        logger.info("Cleaned up %d completed jobs older than %d days", count, older_than_days)
        return count
    
    async def reset_stale_processing(
        self,
        older_than_minutes: int = 30,
    ) -> int:
        """Reset jobs stuck in processing state.
        
        Jobs that have been processing for too long are assumed
        to have failed without proper cleanup.
        
        Args:
            older_than_minutes: Age threshold in minutes.
            
        Returns:
            Number of jobs reset.
        """
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """
                UPDATE jobs 
                SET status = 'queued',
                    last_error = 'Reset due to stale processing state',
                    started_at = NULL,
                    updated_at = datetime('now')
                WHERE status = 'processing'
                AND started_at < datetime('now', ?)
                """,
                (f"-{older_than_minutes} minutes",),
            )
            count = cursor.rowcount
        
        if count > 0:
            logger.warning("Reset %d stale processing jobs", count)
        
        return count

"""Tests for store/database operations."""

import pytest
from datetime import datetime

from ninjaone_jira_integration.store.db import DatabaseManager
from ninjaone_jira_integration.store.mappings import MappingStore
from ninjaone_jira_integration.store.jobs import JobStore, JobType, JobStatus


class TestDatabaseManager:
    """Tests for DatabaseManager."""
    
    @pytest.mark.asyncio
    async def test_init_database(self, temp_dir):
        """Test database initialization."""
        db_path = temp_dir / "test.db"
        
        async with DatabaseManager(str(db_path)) as db:
            # Verify database file exists
            assert db_path.exists()
            
            # Verify tables exist
            async with db.connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
                tables = {row[0] for row in await cursor.fetchall()}
            
            assert "device_mappings" in tables
            assert "alert_mappings" in tables
            assert "job_queue" in tables


class TestMappingStore:
    """Tests for MappingStore operations."""
    
    @pytest.mark.asyncio
    async def test_save_device_mapping(self, temp_database):
        """Test saving a device mapping."""
        store = MappingStore(temp_database)
        
        await store.save_device_mapping(
            ninja_device_id=12345,
            jira_asset_id="asset-100",
            jira_asset_key="ASSET-100",
            last_sync=datetime.now().isoformat(),
        )
        
        # Verify it was saved
        result = await store.get_device_mapping(12345)
        
        assert result is not None
        assert result["jira_asset_id"] == "asset-100"
        assert result["jira_asset_key"] == "ASSET-100"
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_mapping(self, temp_database):
        """Test getting a mapping that doesn't exist."""
        store = MappingStore(temp_database)
        
        result = await store.get_device_mapping(99999)
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_update_device_mapping(self, temp_database):
        """Test updating an existing device mapping."""
        store = MappingStore(temp_database)
        
        # Create initial mapping
        await store.save_device_mapping(
            ninja_device_id=12345,
            jira_asset_id="asset-100",
            jira_asset_key="ASSET-100",
        )
        
        # Update it
        await store.save_device_mapping(
            ninja_device_id=12345,
            jira_asset_id="asset-100",
            jira_asset_key="ASSET-100-UPDATED",
        )
        
        result = await store.get_device_mapping(12345)
        
        assert result["jira_asset_key"] == "ASSET-100-UPDATED"
    
    @pytest.mark.asyncio
    async def test_save_alert_mapping(self, temp_database):
        """Test saving an alert mapping."""
        store = MappingStore(temp_database)
        
        await store.save_alert_mapping(
            ninja_alert_id=67890,
            jira_issue_id="10001",
            jira_issue_key="HELP-123",
        )
        
        result = await store.get_alert_mapping(67890)
        
        assert result is not None
        assert result["jira_issue_key"] == "HELP-123"
    
    @pytest.mark.asyncio
    async def test_count_mappings(self, temp_database):
        """Test counting mappings."""
        store = MappingStore(temp_database)
        
        # Initially empty
        assert await store.count_device_mappings() == 0
        assert await store.count_alert_mappings() == 0
        
        # Add some mappings
        await store.save_device_mapping(1, "a-1", "A-1")
        await store.save_device_mapping(2, "a-2", "A-2")
        await store.save_alert_mapping(1, "i-1", "I-1")
        
        assert await store.count_device_mappings() == 2
        assert await store.count_alert_mappings() == 1


class TestJobStore:
    """Tests for JobStore operations."""
    
    @pytest.mark.asyncio
    async def test_enqueue_job(self, temp_database):
        """Test enqueueing a new job."""
        store = JobStore(temp_database)
        
        job = await store.enqueue(
            job_type=JobType.DEVICE_SYNC,
            job_key="device-12345",
            payload={"device_id": 12345},
        )
        
        assert job.id is not None
        assert job.status == JobStatus.QUEUED
        assert job.job_type == JobType.DEVICE_SYNC
    
    @pytest.mark.asyncio
    async def test_job_deduplication(self, temp_database):
        """Test that duplicate jobs are deduplicated."""
        store = JobStore(temp_database)
        
        # Enqueue first job
        job1 = await store.enqueue(
            job_type=JobType.DEVICE_SYNC,
            job_key="device-12345",
            payload={"v": 1},
        )
        
        # Enqueue duplicate
        job2 = await store.enqueue(
            job_type=JobType.DEVICE_SYNC,
            job_key="device-12345",
            payload={"v": 2},
        )
        
        # Should return same job ID
        assert job1.id == job2.id
    
    @pytest.mark.asyncio
    async def test_claim_job(self, temp_database):
        """Test claiming a job for processing."""
        store = JobStore(temp_database)
        
        await store.enqueue(
            job_type=JobType.DEVICE_SYNC,
            job_key="device-12345",
            payload={},
        )
        
        jobs = await store.claim_jobs(limit=1)
        
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.PROCESSING
    
    @pytest.mark.asyncio
    async def test_claim_empty_queue(self, temp_database):
        """Test claiming from empty queue."""
        store = JobStore(temp_database)
        
        jobs = await store.claim_jobs(limit=5)
        
        assert len(jobs) == 0
    
    @pytest.mark.asyncio
    async def test_complete_job(self, temp_database):
        """Test marking a job as completed."""
        store = JobStore(temp_database)
        
        job = await store.enqueue(
            job_type=JobType.DEVICE_SYNC,
            job_key="device-12345",
            payload={},
        )
        
        # Claim it
        await store.claim_jobs(limit=1)
        
        # Complete it
        await store.complete(job.id)
        
        # Verify status
        status = await store.get_job_status(job.id)
        assert status == JobStatus.COMPLETED
    
    @pytest.mark.asyncio
    async def test_fail_job(self, temp_database):
        """Test marking a job as failed."""
        store = JobStore(temp_database)
        
        job = await store.enqueue(
            job_type=JobType.ALERT_PROCESS,
            job_key="alert-67890",
            payload={},
        )
        
        await store.claim_jobs(limit=1)
        
        # Fail it
        await store.fail(job.id, error="Test error message")
        
        status = await store.get_job_status(job.id)
        assert status == JobStatus.QUEUED  # Should be requeued for retry
    
    @pytest.mark.asyncio
    async def test_dead_letter_after_max_retries(self, temp_database):
        """Test that jobs move to dead-letter after max retries."""
        store = JobStore(temp_database)
        
        job = await store.enqueue(
            job_type=JobType.DEVICE_SYNC,
            job_key="device-12345",
            payload={},
        )
        
        # Simulate max retries exceeded
        for i in range(5):
            jobs = await store.claim_jobs(limit=1)
            if jobs:
                await store.fail(jobs[0].id, error=f"Retry {i+1}")
        
        status = await store.get_job_status(job.id)
        assert status == JobStatus.DEAD_LETTER
    
    @pytest.mark.asyncio
    async def test_get_stats(self, temp_database):
        """Test getting job statistics."""
        store = JobStore(temp_database)
        
        # Create various jobs
        await store.enqueue(JobType.DEVICE_SYNC, "d-1", {})
        await store.enqueue(JobType.DEVICE_SYNC, "d-2", {})
        job = await store.enqueue(JobType.ALERT_PROCESS, "a-1", {})
        
        # Claim and complete one
        await store.claim_jobs(limit=1)
        
        stats = await store.get_stats()
        
        assert stats.queued >= 1
        assert stats.processing >= 1

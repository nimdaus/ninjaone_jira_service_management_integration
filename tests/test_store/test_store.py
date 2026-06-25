"""Tests for store/database operations."""

from __future__ import annotations

import pytest

from ninjaone_jira_integration.store.db import DatabaseManager
from ninjaone_jira_integration.store.mappings import AlertMapping, DeviceMapping, MappingStore
from ninjaone_jira_integration.store.jobs import JobStore, JobType, JobStatus


class TestDatabaseManager:
    """Tests for DatabaseManager."""

    @pytest.mark.asyncio
    async def test_init_database(self, temp_dir):
        """Test database initialization creates expected tables."""
        db_path = temp_dir / "test.db"

        async with DatabaseManager(str(db_path)) as db:
            assert db_path.exists()

            rows = await db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in rows}

        assert "device_mappings" in tables
        assert "alert_mappings" in tables
        assert "jobs" in tables


class TestMappingStore:
    """Tests for MappingStore operations."""

    @pytest.mark.asyncio
    async def test_save_device_mapping(self, temp_database) -> None:
        store = MappingStore(temp_database)

        mapping = DeviceMapping(
            ninja_device_id=12345,
            jira_asset_id="asset-100",
            jira_asset_key="ASSET-100",
        )
        await store.upsert_device_mapping(mapping)

        result = await store.get_device_mapping(12345)
        assert result is not None
        assert result.jira_asset_id == "asset-100"
        assert result.jira_asset_key == "ASSET-100"

    @pytest.mark.asyncio
    async def test_get_nonexistent_mapping(self, temp_database) -> None:
        store = MappingStore(temp_database)
        result = await store.get_device_mapping(99999)
        assert result is None

    @pytest.mark.asyncio
    async def test_update_device_mapping(self, temp_database) -> None:
        store = MappingStore(temp_database)

        await store.upsert_device_mapping(DeviceMapping(
            ninja_device_id=12345, jira_asset_id="asset-100", jira_asset_key="ASSET-100",
        ))
        await store.upsert_device_mapping(DeviceMapping(
            ninja_device_id=12345, jira_asset_id="asset-100", jira_asset_key="ASSET-100-UPDATED",
        ))

        result = await store.get_device_mapping(12345)
        assert result is not None
        assert result.jira_asset_key == "ASSET-100-UPDATED"

    @pytest.mark.asyncio
    async def test_save_alert_mapping(self, temp_database) -> None:
        store = MappingStore(temp_database)

        mapping = AlertMapping(
            ninja_alert_id="uid-abc-123",
            jira_issue_id="10001",
            jira_issue_key="HELP-123",
        )
        await store.upsert_alert_mapping(mapping)

        result = await store.get_alert_mapping("uid-abc-123")
        assert result is not None
        assert result.jira_issue_key == "HELP-123"

    @pytest.mark.asyncio
    async def test_count_mappings(self, temp_database) -> None:
        store = MappingStore(temp_database)

        assert await store.count_device_mappings() == 0
        assert await store.count_alert_mappings() == 0

        await store.upsert_device_mapping(DeviceMapping(ninja_device_id=1, jira_asset_id="a-1", jira_asset_key="A-1"))
        await store.upsert_device_mapping(DeviceMapping(ninja_device_id=2, jira_asset_id="a-2", jira_asset_key="A-2"))
        await store.upsert_alert_mapping(AlertMapping(ninja_alert_id="uid-1", jira_issue_key="I-1"))

        assert await store.count_device_mappings() == 2
        assert await store.count_alert_mappings() == 1


class TestJobStore:
    """Tests for JobStore operations."""

    @pytest.mark.asyncio
    async def test_enqueue_job(self, temp_database) -> None:
        store = JobStore(temp_database)

        job_id = await store.enqueue(
            job_type=JobType.DEVICE_SYNC,
            job_key="device-12345",
            payload={"device_id": 12345},
        )

        assert isinstance(job_id, int)
        assert job_id > 0

    @pytest.mark.asyncio
    async def test_job_deduplication(self, temp_database) -> None:
        store = JobStore(temp_database)

        id1 = await store.enqueue(JobType.DEVICE_SYNC, "device-12345", {"v": 1})
        id2 = await store.enqueue(JobType.DEVICE_SYNC, "device-12345", {"v": 2})

        assert id1 == id2

    @pytest.mark.asyncio
    async def test_claim_job(self, temp_database) -> None:
        store = JobStore(temp_database)

        await store.enqueue(JobType.DEVICE_SYNC, "device-12345", {})

        job = await store.claim_next()

        assert job is not None
        assert job.status == JobStatus.PROCESSING

    @pytest.mark.asyncio
    async def test_claim_empty_queue(self, temp_database) -> None:
        store = JobStore(temp_database)
        result = await store.claim_next()
        assert result is None

    @pytest.mark.asyncio
    async def test_complete_job(self, temp_database) -> None:
        store = JobStore(temp_database)

        job_id = await store.enqueue(JobType.DEVICE_SYNC, "device-12345", {})
        await store.claim_next()
        await store.complete(job_id)

        row = await temp_database.fetch_one(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        )
        assert row is not None
        assert row[0] == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_fail_job_requeues(self, temp_database) -> None:
        store = JobStore(temp_database)

        job_id = await store.enqueue(JobType.ALERT_PROCESS, "alert-67890", {}, max_attempts=3)
        await store.claim_next()
        await store.fail(job_id, error="Test error")

        row = await temp_database.fetch_one(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        )
        assert row is not None
        assert row[0] == JobStatus.QUEUED.value

    @pytest.mark.asyncio
    async def test_dead_letter_after_max_retries(self, temp_database) -> None:
        store = JobStore(temp_database)

        job_id = await store.enqueue(JobType.DEVICE_SYNC, "device-12345", {}, max_attempts=3)

        for i in range(3):
            job = await store.claim_next()
            if job:
                await store.fail(job.id, error=f"Retry {i + 1}")

        row = await temp_database.fetch_one(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        )
        assert row is not None
        assert row[0] == JobStatus.DEAD_LETTER.value

    @pytest.mark.asyncio
    async def test_get_stats(self, temp_database) -> None:
        store = JobStore(temp_database)

        await store.enqueue(JobType.DEVICE_SYNC, "d-1", {})
        await store.enqueue(JobType.DEVICE_SYNC, "d-2", {})
        await store.enqueue(JobType.ALERT_PROCESS, "a-1", {})

        await store.claim_next()

        stats = await store.get_stats()

        assert stats.queued >= 1
        assert stats.processing >= 1

"""
Shared test fixtures for the integration tests.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from ninjaone_jira_integration.config.models import (
    AppConfig,
    AttributeMapping,
    JiraAssetsConfig,
    JiraConfig,
    NinjaOneConfig,
)


# Configure pytest-asyncio
@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir() -> Iterator[Path]:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_config(temp_dir: Path) -> AppConfig:
    """Create a sample AppConfig for testing."""
    return AppConfig(
        ninjaone=NinjaOneConfig(
            base_url="https://app.ninjarmm.com",
            client_id="test-client-id",
            client_secret=SecretStr("test-client-secret"),
        ),
        jira=JiraConfig(
            subdomain="testcompany",
            email="test@example.com",
            api_token=SecretStr("test-api-token"),
            workspace_id="workspace-123",
        ),
        assets=JiraAssetsConfig(
            object_schema_id="1",
            object_type_id="10",
            serial_number_attribute_id="123",
            attribute_mappings=[
                AttributeMapping(
                    jira_attribute_id="100",
                    jira_attribute_name="Name",
                    source="systemName",
                    required=True,
                ),
                AttributeMapping(
                    jira_attribute_id="101",
                    jira_attribute_name="Serial Number",
                    source="system.serialNumber",
                    transform="normalize_serial",
                ),
            ],
        ),
    )


@pytest.fixture
def sample_ninjaone_device() -> dict[str, Any]:
    """Create a sample NinjaOne device for testing."""
    return {
        "id": 12345,
        "systemName": "LAPTOP-ABC",
        "displayName": "John's Laptop",
        "organizationId": 1,
        "organizationName": "Test Org",
        "locationId": 1,
        "locationName": "Main Office",
        "nodeClass": "WINDOWS_WORKSTATION",
        "nodeRoleId": 1,
        "system": {
            "name": "LAPTOP-ABC",
            "serialNumber": "SN12345678",
            "manufacturer": "Dell Inc.",
            "model": "XPS 15 9520",
            "biosSerialNumber": "BIOS12345",
            "assetSerialNumber": "ASSET12345",
            "totalPhysicalMemory": 34359738368,  # 32 GB
            "numberOfProcessors": 1,
        },
        "memory": {
            "capacity": 34359738368,  # 32 GB — top-level, mirrors system.totalPhysicalMemory
        },
        "processors": [
            {"name": "Intel Core i7-12700H", "numberOfCores": 14}
        ],
        "os": {
            "name": "Microsoft Windows 11 Pro",
            "version": "22H2",
            "architecture": "64-bit",
        },
        "ipAddresses": ["192.168.1.100", "10.0.0.50"],
        "macAddresses": ["AA:BB:CC:DD:EE:FF"],
        "lastContact": 1705315800.0,
    }


@pytest.fixture
def sample_ninjaone_alert() -> dict[str, Any]:
    """Create a sample NinjaOne alert matching the real API format."""
    return {
        "uid": "2c38a77c-50f9-47dd-9963-e89f830d2210",
        "deviceId": 12345,
        "message": "High CPU usage detected",
        "severity": "MAJOR",
        "sourceType": "CONDITION",
        "conditionName": "CPU Threshold Exceeded",
        "sourceName": "CPU Monitor",
        "subject": "CPU",
        "priority": "MEDIUM",
        "createTime": 1781023410.099173,
        "updateTime": 1781023410.099173,
        "conditionHealthStatus": "NEEDS_ATTENTION",
        "useGlobalHealthStatus": False,
    }


@pytest.fixture
def sample_jira_asset() -> dict[str, Any]:
    """Create a sample Jira Asset response for testing."""
    return {
        "id": "12345",
        "objectKey": "ASSET-123",
        "label": "LAPTOP-ABC",
        "objectTypeId": "10",
        "attributes": [
            {
                "objectAttributeId": "1001",
                "objectTypeAttributeId": "100",
                "objectAttributeValues": [{"value": "LAPTOP-ABC"}],
            },
            {
                "objectAttributeId": "1002",
                "objectTypeAttributeId": "101",
                "objectAttributeValues": [{"value": "SN12345678"}],
            },
        ],
    }


@pytest.fixture
def mock_ninja_client() -> AsyncMock:
    """Create a mock NinjaOne client."""
    client = AsyncMock()
    client.authenticate = AsyncMock()
    client.close = AsyncMock()
    client.get_device = AsyncMock()
    client.get_alert = AsyncMock()
    
    async def mock_devices_generator(*args, **kwargs):
        yield {
            "id": 12345,
            "systemName": "TEST-DEVICE",
            "system": {"serialNumber": "SN123"},
        }
    
    client.get_devices_detailed = MagicMock(return_value=mock_devices_generator())
    return client


@pytest.fixture
def mock_jira_client() -> AsyncMock:
    """Create a mock Jira Assets client."""
    client = AsyncMock()
    client.close = AsyncMock()
    client.test_connection = AsyncMock(return_value=True)
    client.discover_workspace = AsyncMock(return_value="workspace-123")
    client.search_objects = AsyncMock(return_value=[])
    client.create_object = AsyncMock(return_value={"id": "12345", "objectKey": "ASSET-100"})
    client.update_object = AsyncMock(return_value={"id": "12345", "objectKey": "ASSET-100"})
    client.create_issue = AsyncMock(return_value={"id": "10001", "key": "HELP-123"})
    return client


@pytest.fixture
async def temp_database(temp_dir: Path):
    """Create a temporary test database."""
    from ninjaone_jira_integration.store.db import DatabaseManager
    
    db_path = temp_dir / "test.db"
    async with DatabaseManager(str(db_path)) as db:
        yield db

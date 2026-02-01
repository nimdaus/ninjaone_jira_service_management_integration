"""Tests for sync engine components."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

from ninjaone_jira_integration.config.models import (
    AttributeMapping,
    JiraAssetsConfig,
    JiraAttributeType,
)
from ninjaone_jira_integration.sync.mapper import DeviceMapper


class TestDeviceMapper:
    """Tests for DeviceMapper."""
    
    @pytest.fixture
    def mapper_config(self) -> JiraAssetsConfig:
        """Create mapper configuration."""
        return JiraAssetsConfig(
            object_schema_id="1",
            object_type_id="10",
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
                AttributeMapping(
                    jira_attribute_id="102",
                    jira_attribute_name="Manufacturer",
                    source="system.manufacturer",
                ),
                AttributeMapping(
                    jira_attribute_id="103",
                    jira_attribute_name="OS",
                    source="os.name",
                ),
            ],
        )
    
    def test_extract_simple_value(self, mapper_config):
        """Test extracting a simple top-level field."""
        mapper = DeviceMapper(mapper_config)
        device = {"systemName": "LAPTOP-ABC"}
        
        value = mapper._get_nested_value(device, "systemName")
        
        assert value == "LAPTOP-ABC"
    
    def test_extract_nested_value(self, mapper_config):
        """Test extracting a nested field."""
        mapper = DeviceMapper(mapper_config)
        device = {
            "system": {
                "serialNumber": "SN12345",
                "manufacturer": "Dell",
            }
        }
        
        value = mapper._get_nested_value(device, "system.serialNumber")
        
        assert value == "SN12345"
    
    def test_extract_array_index(self, mapper_config):
        """Test extracting from array by index."""
        mapper = DeviceMapper(mapper_config)
        device = {
            "ipAddresses": ["192.168.1.1", "10.0.0.1"],
        }
        
        value = mapper._get_nested_value(device, "ipAddresses[0]")
        
        assert value == "192.168.1.1"
    
    def test_extract_missing_value_returns_none(self, mapper_config):
        """Test that missing fields return None."""
        mapper = DeviceMapper(mapper_config)
        device = {"systemName": "TEST"}
        
        value = mapper._get_nested_value(device, "nonexistent.field")
        
        assert value is None
    
    def test_transform_normalize_serial(self, mapper_config):
        """Test normalize_serial transformation."""
        mapper = DeviceMapper(mapper_config)
        
        # Test various serial number formats
        assert mapper._apply_transform("  ABC-123  ", "normalize_serial") == "ABC123"
        assert mapper._apply_transform("SN: 12345", "normalize_serial") == "12345"
        assert mapper._apply_transform("abc-def", "normalize_serial") == "ABCDEF"
    
    def test_transform_upper(self, mapper_config):
        """Test uppercase transformation."""
        mapper = DeviceMapper(mapper_config)
        
        assert mapper._apply_transform("hello", "upper") == "HELLO"
    
    def test_transform_lower(self, mapper_config):
        """Test lowercase transformation."""
        mapper = DeviceMapper(mapper_config)
        
        assert mapper._apply_transform("HELLO", "lower") == "hello"
    
    def test_transform_strip(self, mapper_config):
        """Test strip whitespace transformation."""
        mapper = DeviceMapper(mapper_config)
        
        assert mapper._apply_transform("  hello  ", "strip") == "hello"
    
    def test_map_device_full(self, mapper_config, sample_ninjaone_device):
        """Test full device mapping."""
        mapper = DeviceMapper(mapper_config)
        
        result = mapper.map_device(sample_ninjaone_device)
        
        assert result is not None
        assert len(result.attributes) > 0
        
        # Find mapped attributes
        name_attr = next((a for a in result.attributes if a["objectTypeAttributeId"] == "100"), None)
        assert name_attr is not None
        assert name_attr["objectAttributeValues"][0]["value"] == "LAPTOP-ABC"
    
    def test_map_device_with_missing_optional_field(self, mapper_config):
        """Test mapping when optional fields are missing."""
        mapper = DeviceMapper(mapper_config)
        device = {
            "systemName": "MINIMAL-DEVICE",
            # No system.serialNumber or other fields
        }
        
        result = mapper.map_device(device)
        
        # Should still succeed with just the required Name field
        assert result is not None
        name_attr = next((a for a in result.attributes if a["objectTypeAttributeId"] == "100"), None)
        assert name_attr is not None
    
    def test_get_mapped_preview(self, mapper_config, sample_ninjaone_device):
        """Test getting a mapping preview."""
        mapper = DeviceMapper(mapper_config)
        
        preview = mapper.get_mapped_preview(sample_ninjaone_device)
        
        assert len(preview) == 4  # All 4 mappings
        
        # Check preview items
        name_item = next((p for p in preview if p.attribute_name == "Name"), None)
        assert name_item is not None
        assert name_item.value == "LAPTOP-ABC"
        assert name_item.source_field == "systemName"


class TestIdentityResolution:
    """Tests for device identity resolution."""
    
    @pytest.mark.asyncio
    async def test_match_by_persisted_mapping(self, temp_database, sample_config):
        """Test matching device via persisted mapping."""
        from ninjaone_jira_integration.sync.matching import IdentityResolver
        from ninjaone_jira_integration.store.mappings import MappingStore
        
        # Set up a persisted mapping
        store = MappingStore(temp_database)
        await store.save_device_mapping(
            ninja_device_id=12345,
            jira_asset_id="asset-100",
            jira_asset_key="ASSET-100",
        )
        
        resolver = IdentityResolver(sample_config.assets, store, None)
        
        device = {"id": 12345, "systemName": "TEST"}
        result = await resolver.find_matching_asset(device)
        
        assert result is not None
        assert result["jira_asset_id"] == "asset-100"
    
    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, temp_database, sample_config):
        """Test that no match returns None for new device."""
        from ninjaone_jira_integration.sync.matching import IdentityResolver
        from ninjaone_jira_integration.store.mappings import MappingStore
        
        store = MappingStore(temp_database)
        
        resolver = IdentityResolver(sample_config.assets, store, None)
        
        device = {"id": 99999, "systemName": "NEW-DEVICE"}
        result = await resolver.find_matching_asset(device)
        
        assert result is None


class TestDiffComputation:
    """Tests for attribute diff computation."""
    
    def test_detect_no_changes(self):
        """Test detecting when there are no changes."""
        from ninjaone_jira_integration.sync.engine import compute_attribute_diff
        
        current = [
            {"objectTypeAttributeId": "100", "objectAttributeValues": [{"value": "VALUE1"}]},
        ]
        new = [
            {"objectTypeAttributeId": "100", "objectAttributeValues": [{"value": "VALUE1"}]},
        ]
        
        diff = compute_attribute_diff(current, new)
        
        assert len(diff) == 0
    
    def test_detect_value_change(self):
        """Test detecting when a value has changed."""
        from ninjaone_jira_integration.sync.engine import compute_attribute_diff
        
        current = [
            {"objectTypeAttributeId": "100", "objectAttributeValues": [{"value": "OLD"}]},
        ]
        new = [
            {"objectTypeAttributeId": "100", "objectAttributeValues": [{"value": "NEW"}]},
        ]
        
        diff = compute_attribute_diff(current, new)
        
        assert len(diff) == 1
        assert diff[0]["objectTypeAttributeId"] == "100"


# Add a stub for compute_attribute_diff if it doesn't exist in engine.py
def compute_attribute_diff(current: list, new: list) -> list:
    """Compute which attributes have changed.
    
    This is a test helper that matches the expected engine behavior.
    """
    current_map = {
        a["objectTypeAttributeId"]: a.get("objectAttributeValues", [])
        for a in current
    }
    
    changed = []
    for attr in new:
        attr_id = attr["objectTypeAttributeId"]
        new_values = attr.get("objectAttributeValues", [])
        current_values = current_map.get(attr_id, [])
        
        if new_values != current_values:
            changed.append(attr)
    
    return changed

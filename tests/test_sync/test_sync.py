"""Tests for sync engine components."""

from __future__ import annotations

from unittest.mock import AsyncMock
from typing import Any

import pytest

from ninjaone_jira_integration.config.models import (
    AttributeMapping,
    JiraAssetsConfig,
)
from ninjaone_jira_integration.sync.mapper import (
    DeviceMapper,
    MappedAttribute,
    apply_transform,
    get_nested_value,
)


class TestGetNestedValue:
    """Tests for get_nested_value helper."""

    def test_extract_simple_value(self) -> None:
        assert get_nested_value({"systemName": "LAPTOP-ABC"}, "systemName") == "LAPTOP-ABC"

    def test_extract_nested_value(self) -> None:
        device = {"system": {"serialNumber": "SN12345", "manufacturer": "Dell"}}
        assert get_nested_value(device, "system.serialNumber") == "SN12345"

    def test_extract_array_index(self) -> None:
        device = {"ipAddresses": ["192.168.1.1", "10.0.0.1"]}
        assert get_nested_value(device, "ipAddresses[0]") == "192.168.1.1"

    def test_missing_value_returns_none(self) -> None:
        assert get_nested_value({"systemName": "TEST"}, "nonexistent.field") is None


class TestApplyTransform:
    """Tests for apply_transform helper."""

    def test_upper(self) -> None:
        assert apply_transform("hello", "upper") == "HELLO"

    def test_lower(self) -> None:
        assert apply_transform("HELLO", "lower") == "hello"

    def test_strip(self) -> None:
        assert apply_transform("  hello  ", "strip") == "hello"

    def test_normalize_serial_strips_and_uppercases(self) -> None:
        assert apply_transform("  abc-def  ", "normalize_serial") == "ABC-DEF"

    def test_normalize_serial_filters_none_literal(self) -> None:
        assert apply_transform("NONE", "normalize_serial") is None

    def test_normalize_serial_filters_na(self) -> None:
        assert apply_transform("N/A", "normalize_serial") is None

    def test_normalize_serial_filters_unknown(self) -> None:
        assert apply_transform("Unknown", "normalize_serial") is None

    def test_none_value_passthrough(self) -> None:
        assert apply_transform(None, "upper") is None

    def test_no_transform_passthrough(self) -> None:
        assert apply_transform("value", None) == "value"


class TestDeviceMapper:
    """Tests for DeviceMapper class."""

    @pytest.fixture
    def mapper_config(self) -> JiraAssetsConfig:
        return JiraAssetsConfig(
            schema_id="1",
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
                    transforms=["normalize_serial"],
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

    def test_map_device_full(self, mapper_config: JiraAssetsConfig, sample_ninjaone_device: dict[str, Any]) -> None:
        mapper = DeviceMapper(mapper_config)
        result = mapper.map_device(sample_ninjaone_device)

        assert isinstance(result, list)
        assert len(result) > 0

        name_attr = next((a for a in result if a["objectTypeAttributeId"] == "100"), None)
        assert name_attr is not None
        assert name_attr["objectAttributeValues"][0]["value"] == "LAPTOP-ABC"

    def test_map_device_with_missing_optional_field(self, mapper_config: JiraAssetsConfig) -> None:
        mapper = DeviceMapper(mapper_config)
        device = {"systemName": "MINIMAL-DEVICE"}

        result = mapper.map_device(device)
        assert isinstance(result, list)
        name_attr = next((a for a in result if a["objectTypeAttributeId"] == "100"), None)
        assert name_attr is not None

    def test_get_mapped_preview(self, mapper_config: JiraAssetsConfig, sample_ninjaone_device: dict[str, Any]) -> None:
        mapper = DeviceMapper(mapper_config)
        preview = mapper.get_mapped_preview(sample_ninjaone_device)

        assert len(preview) == 4

        name_item = next((p for p in preview if p.attribute_name == "Name"), None)
        assert name_item is not None
        assert name_item.value == "LAPTOP-ABC"
        assert name_item.source_field == "systemName"


class TestIdentityResolution:
    """Tests for device identity resolution."""

    @pytest.mark.asyncio
    async def test_match_by_persisted_mapping(self, temp_database, sample_config) -> None:
        from ninjaone_jira_integration.sync.matching import IdentityResolver
        from ninjaone_jira_integration.store.mappings import DeviceMapping, MappingStore

        store = MappingStore(temp_database)
        mapping = DeviceMapping(
            ninja_device_id=12345,
            jira_asset_id="asset-100",
            jira_asset_key="ASSET-100",
        )
        await store.upsert_device_mapping(mapping)

        jira_client = AsyncMock()
        jira_client.get_object.return_value = {"id": "asset-100", "objectKey": "ASSET-100"}
        resolver = IdentityResolver(
            jira_client=jira_client,
            mapping_store=store,
            config=sample_config.assets,
        )

        device = {"id": 12345, "systemName": "TEST"}
        result = await resolver.resolve(device_id=12345, device=device)

        assert result is not None
        assert result.found is True
        assert result.jira_asset_id == "asset-100"

    @pytest.mark.asyncio
    async def test_no_match_returns_not_found(self, temp_database, sample_config) -> None:
        from ninjaone_jira_integration.sync.matching import IdentityResolver
        from ninjaone_jira_integration.store.mappings import MappingStore

        store = MappingStore(temp_database)

        resolver = IdentityResolver(
            jira_client=AsyncMock(),
            mapping_store=store,
            config=sample_config.assets,
        )

        device = {"id": 99999, "systemName": "NEW-DEVICE"}
        result = await resolver.resolve(device_id=99999, device=device)

        assert result is not None
        assert result.found is False


class TestDiffComputation:
    """Tests for attribute diff computation."""

    def test_detect_no_changes(self) -> None:
        from ninjaone_jira_integration.sync.engine import compute_attribute_diff

        current = [
            {"objectTypeAttributeId": "100", "objectAttributeValues": [{"value": "VALUE1"}]},
        ]
        new = [
            {"objectTypeAttributeId": "100", "objectAttributeValues": [{"value": "VALUE1"}]},
        ]

        changed, descriptions = compute_attribute_diff(current, new)
        assert len(changed) == 0
        assert len(descriptions) == 0

    def test_detect_value_change(self) -> None:
        from ninjaone_jira_integration.sync.engine import compute_attribute_diff

        current = [
            {"objectTypeAttributeId": "100", "objectAttributeValues": [{"value": "OLD"}]},
        ]
        new = [
            {"objectTypeAttributeId": "100", "objectAttributeValues": [{"value": "NEW"}]},
        ]

        changed, descriptions = compute_attribute_diff(current, new)
        assert len(changed) == 1
        assert changed[0]["objectTypeAttributeId"] == "100"

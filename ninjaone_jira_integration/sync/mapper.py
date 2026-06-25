"""
Device to asset mapping engine.

Transforms NinjaOne device data into Jira Assets attribute format
according to configured mappings.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ninjaone_jira_integration.config.models import (
    AttributeMapping,
    JiraAssetsConfig,
    JiraAttributeType,
)
from ninjaone_jira_integration.utils import get_nested_value

logger = logging.getLogger(__name__)


@dataclass
class MappedAttribute:
    """Result of mapping a single attribute."""
    
    attribute_id: str
    attribute_name: str
    value: Any
    source_field: str
    original_value: Any | None = None
    transformed: bool = False


def apply_transform(value: Any, transform: str | None) -> Any:
    """Apply a single transformation to a value.

    Supported transforms:
    - upper: Convert to uppercase
    - lower: Convert to lowercase
    - strip: Strip whitespace
    - normalize_serial: Normalize serial number (uppercase, strip, remove fillers)
    - to_string: Convert value to string
    - to_integer: Convert value to integer (truncates floats)
    - to_float: Convert value to float
    - to_boolean: Parse truthy/falsy strings to boolean
    - first_ip: Extract first IP address from a list or comma-separated string
    - first_mac: Extract first MAC address from a list or comma-separated string
    - bytes_to_gb: Convert bytes (int) to gigabytes (rounded to 2 decimal places)

    Args:
        value: Value to transform.
        transform: Transform name.

    Returns:
        Transformed value, or None if the transform filters out the value.
    """
    if value is None or not transform:
        return value

    transform = transform.lower().strip()

    # Type-conversion transforms (work on any type)
    if transform == "to_string":
        return str(value)
    elif transform == "to_integer":
        try:
            return int(float(str(value)))
        except (ValueError, TypeError):
            logger.warning("to_integer failed for value: %r", value)
            return value
    elif transform == "to_float":
        try:
            return float(str(value))
        except (ValueError, TypeError):
            logger.warning("to_float failed for value: %r", value)
            return value
    elif transform == "to_boolean":
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "yes", "1", "on")

    # String transforms (coerce to str first)
    if not isinstance(value, str):
        value = str(value)

    if transform == "upper":
        return value.upper()
    elif transform == "lower":
        return value.lower()
    elif transform == "strip":
        return value.strip()
    elif transform == "normalize_serial":
        normalized = value.strip().upper()
        if normalized in ("NONE", "N/A", "NA", "UNKNOWN", "NOT SPECIFIED", "TBD", ""):
            return None
        return normalized
    elif transform == "first_ip":
        # Extract first IP from list representation or comma-separated string
        import re
        ips = re.findall(r'\d{1,3}(?:\.\d{1,3}){3}', value)
        return ips[0] if ips else value
    elif transform == "first_mac":
        import re
        macs = re.findall(r'(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}', value)
        return macs[0] if macs else value
    elif transform == "bytes_to_gb":
        try:
            return round(float(value) / (1024 ** 3), 2)
        except (ValueError, TypeError):
            logger.warning("bytes_to_gb failed for value: %r", value)
            return value
    else:
        logger.warning("Unknown transform: %s", transform)
        return value


def convert_type(
    value: Any,
    target_type: JiraAttributeType,
) -> Any:
    """Convert a value to the target Jira attribute type.
    
    Args:
        value: Value to convert.
        target_type: Target Jira type.
        
    Returns:
        Converted value.
    """
    if value is None:
        return None
    
    try:
        if target_type == JiraAttributeType.INTEGER:
            if isinstance(value, int):
                return value
            return int(float(str(value)))
        
        elif target_type == JiraAttributeType.FLOAT:
            if isinstance(value, float):
                return value
            return float(str(value))
        
        elif target_type == JiraAttributeType.BOOLEAN:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "yes", "1", "on")
            return bool(value)
        
        elif target_type in (JiraAttributeType.DATE, JiraAttributeType.DATE_TIME):
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, str):
                # Try to parse and reformat
                try:
                    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    return dt.isoformat()
                except ValueError:
                    return value
            return str(value)
        
        else:
            # All other types: convert to string
            return str(value)
    
    except (ValueError, TypeError) as e:
        logger.warning("Type conversion failed for %s to %s: %s", value, target_type, e)
        return str(value) if value is not None else None


def map_device_to_attributes(
    device: dict[str, Any],
    mappings: list[AttributeMapping],
) -> list[dict[str, Any]]:
    """Map a NinjaOne device to Jira asset attributes.
    
    Args:
        device: NinjaOne device data.
        mappings: List of attribute mappings.
        
    Returns:
        List of Jira attribute value objects in format:
        [{"objectTypeAttributeId": "123", "objectAttributeValues": [{"value": "..."}]}]
    """
    attributes = []
    
    for mapping in mappings:
        # Extract value from device
        value = get_nested_value(device, mapping.source)
        
        # Apply default if value is None/empty
        if value is None or value == "":
            if mapping.default_value is not None:
                value = mapping.default_value
            elif mapping.required:
                logger.warning(
                    "Required field %s is missing and has no default",
                    mapping.jira_attribute_name,
                )
                continue
            else:
                continue  # Skip optional empty fields
        
        if mapping.transform:
            value = apply_transform(value, mapping.transform)
        if value is None:
            continue
        
        # Convert to target type
        value = convert_type(value, mapping.jira_attribute_type)
        
        if value is None:
            continue
        
        # Build attribute value structure
        attr_value = {
            "objectTypeAttributeId": mapping.jira_attribute_id,
            "objectAttributeValues": [{"value": value}],
        }
        
        attributes.append(attr_value)
    
    return attributes


class DeviceMapper:
    """High-level mapper for device to asset transformations.
    
    Provides caching and validation on top of basic mapping.
    """
    
    def __init__(self, config: JiraAssetsConfig):
        """Initialize device mapper.

        Args:
            config: Jira Assets configuration with mappings.
        """
        self.config = config

    def _get_mappings_for_device(self, device: dict[str, Any]):
        """Return the attribute mappings to use for a given device.

        Prefers the role-specific ObjectTypeMapping; falls back to the legacy
        flat attribute_mappings list when no role-based config is found.
        """
        role_id = device.get("nodeRoleId")
        if role_id is not None:
            role_mapping = self.config.get_mapping_for_role(role_id)
            if role_mapping:
                return role_mapping.attribute_mappings
        return self.config.attribute_mappings

    def map_device(
        self,
        device: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Map a device to Jira asset attributes.

        Args:
            device: NinjaOne device data.

        Returns:
            List of Jira attribute value objects.
        """
        return map_device_to_attributes(device, self._get_mappings_for_device(device))
    
    def get_mapped_preview(
        self,
        device: dict[str, Any],
    ) -> list[MappedAttribute]:
        """Get a preview of mapped values for display.

        Args:
            device: NinjaOne device data.

        Returns:
            List of MappedAttribute with source and value info.
        """
        results = []

        for mapping in self._get_mappings_for_device(device):
            original_value = get_nested_value(device, mapping.source)
            value = original_value
            transformed = False

            # Apply default
            if value is None or value == "":
                if mapping.default_value is not None:
                    value = mapping.default_value
                    transformed = True

            if mapping.transform and value is not None:
                new_value = apply_transform(value, mapping.transform)
                if new_value != value:
                    transformed = True
                value = new_value

            # Convert type
            if value is not None:
                new_value = convert_type(value, mapping.jira_attribute_type)
                if new_value != value:
                    transformed = True
                value = new_value

            results.append(MappedAttribute(
                attribute_id=mapping.jira_attribute_id,
                attribute_name=mapping.jira_attribute_name,
                value=value,
                source_field=mapping.source,
                original_value=original_value,
                transformed=transformed,
            ))

        return results
    
    def extract_serial_number(
        self,
        device: dict[str, Any],
    ) -> str | None:
        """Extract and normalize the serial number from a device.
        
        Args:
            device: NinjaOne device data.
            
        Returns:
            Normalized serial number, or None.
        """
        # Standard paths for serial number
        serial_paths = [
            "system.serialNumber",
            "serialNumber",
            "system.biosSerialNumber",
            "biosSerialNumber",
            "system.assetSerialNumber",
        ]
        
        for path in serial_paths:
            value = get_nested_value(device, path)
            if value:
                normalized = apply_transform(str(value), "normalize_serial")
                if normalized:
                    return normalized
        
        return None
    
    def extract_device_name(
        self,
        device: dict[str, Any],
    ) -> str | None:
        """Extract the device name/hostname.
        
        Args:
            device: NinjaOne device data.
            
        Returns:
            Device name, or None.
        """
        name_paths = [
            "systemName",
            "system.name",
            "dnsName",
            "displayName",
        ]
        
        for path in name_paths:
            value = get_nested_value(device, path)
            if value:
                return str(value)
        
        return None

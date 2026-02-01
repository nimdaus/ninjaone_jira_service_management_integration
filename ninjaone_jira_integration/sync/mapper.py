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


def get_nested_value(data: dict[str, Any], path: str) -> Any | None:
    """Extract a nested value from a dictionary using dot notation.
    
    Supports:
    - Dot notation: 'system.serialNumber'
    - Array indexing: 'disks[0].size'
    - Nested objects: 'os.name'
    
    Args:
        data: Dictionary to search.
        path: Dot-separated path.
        
    Returns:
        Value at path, or None if not found.
    """
    if not path or not data:
        return None
    
    current = data
    
    # Split by dots, but preserve array indices
    segments = re.split(r'\.(?![^\[]*\])', path)
    
    for segment in segments:
        if current is None:
            return None
        
        # Check for array indexing: field[0]
        match = re.match(r'^(\w+)\[(\d+)\]$', segment)
        if match:
            key, index = match.groups()
            if isinstance(current, dict) and key in current:
                arr = current[key]
                if isinstance(arr, list) and int(index) < len(arr):
                    current = arr[int(index)]
                else:
                    return None
            else:
                return None
        else:
            # Regular key access
            if isinstance(current, dict) and segment in current:
                current = current[segment]
            else:
                # Try case-insensitive match
                found = False
                for key in current.keys() if isinstance(current, dict) else []:
                    if key.lower() == segment.lower():
                        current = current[key]
                        found = True
                        break
                if not found:
                    return None
    
    return current


def apply_transform(value: Any, transform: str | None) -> Any:
    """Apply a transformation to a value.
    
    Supported transforms:
    - upper: Convert to uppercase
    - lower: Convert to lowercase
    - strip: Strip whitespace
    - normalize_serial: Normalize serial number
    
    Args:
        value: Value to transform.
        transform: Transform name.
        
    Returns:
        Transformed value.
    """
    if value is None or not transform:
        return value
    
    if not isinstance(value, str):
        value = str(value)
    
    transform = transform.lower()
    
    if transform == "upper":
        return value.upper()
    elif transform == "lower":
        return value.lower()
    elif transform == "strip":
        return value.strip()
    elif transform == "normalize_serial":
        # Normalize serial number: uppercase, strip, remove common fillers
        normalized = value.strip().upper()
        if normalized in ("NONE", "N/A", "NA", "UNKNOWN", "NOT SPECIFIED", "TBD", ""):
            return None
        return normalized
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
        
        # Apply transform if specified
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
        self.mappings = config.attribute_mappings
    
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
        return map_device_to_attributes(device, self.mappings)
    
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
        
        for mapping in self.mappings:
            original_value = get_nested_value(device, mapping.source)
            value = original_value
            transformed = False
            
            # Apply default
            if value is None or value == "":
                if mapping.default_value is not None:
                    value = mapping.default_value
                    transformed = True
            
            # Apply transform
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

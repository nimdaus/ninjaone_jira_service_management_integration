"""
Validation logic for configuration mappings.

Validates that:
- Required Jira fields are mapped or have defaults
- Type compatibility between NinjaOne and Jira
- Enum values are valid
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ninjaone_jira_integration.config.models import (
    AttributeMapping,
    FieldMapping,
    JiraAttributeType,
)


class MappingErrorSeverity(str, Enum):
    """Severity level for mapping errors."""
    
    ERROR = "error"  # Will prevent sync
    WARNING = "warning"  # May cause issues
    INFO = "info"  # For informational messages


@dataclass
class MappingError:
    """Represents a validation error in a mapping."""
    
    path: str
    message: str
    severity: MappingErrorSeverity
    details: dict[str, Any] | None = None
    
    def __str__(self) -> str:
        prefix = {
            MappingErrorSeverity.ERROR: "❌",
            MappingErrorSeverity.WARNING: "⚠️",
            MappingErrorSeverity.INFO: "ℹ️",
        }[self.severity]
        return f"{prefix} {self.path}: {self.message}"


# Type compatibility matrix: (jira_type, python_type) -> compatible
TYPE_COMPATIBILITY = {
    JiraAttributeType.DEFAULT: (str, int, float, bool),
    JiraAttributeType.BOOLEAN: (bool, int, str),
    JiraAttributeType.INTEGER: (int, str),
    JiraAttributeType.FLOAT: (float, int, str),
    JiraAttributeType.DATE: (str,),
    JiraAttributeType.DATE_TIME: (str,),
    JiraAttributeType.URL: (str,),
    JiraAttributeType.EMAIL: (str,),
    JiraAttributeType.TEXTAREA: (str,),
    JiraAttributeType.SELECT: (str,),
    JiraAttributeType.REFERENCE: (str, int),
    JiraAttributeType.USER: (str,),
    JiraAttributeType.GROUP: (str,),
    JiraAttributeType.STATUS: (str,),
}


def validate_attribute_mapping(
    mapping: AttributeMapping,
    value: Any | None,
    jira_attribute_info: dict[str, Any] | None = None,
) -> list[MappingError]:
    """Validate a single attribute mapping against a value.
    
    Args:
        mapping: The attribute mapping configuration.
        value: The actual value from NinjaOne device.
        jira_attribute_info: Optional Jira attribute metadata for validation.
        
    Returns:
        List of validation errors (empty if valid).
    """
    errors: list[MappingError] = []
    path = f"attributes.{mapping.jira_attribute_name}"
    
    # Check required fields
    if mapping.required and value is None and mapping.default_value is None:
        errors.append(MappingError(
            path=path,
            message="Required field is missing and has no default value",
            severity=MappingErrorSeverity.ERROR,
            details={"source": mapping.source, "required": True},
        ))
        return errors  # No point checking further if missing
    
    # Use default if value is None
    effective_value = value if value is not None else mapping.default_value
    
    if effective_value is None:
        return errors  # Optional field with no value is fine
    
    # Check type compatibility
    compatible_types = TYPE_COMPATIBILITY.get(mapping.jira_attribute_type, (str,))
    if not isinstance(effective_value, compatible_types):
        errors.append(MappingError(
            path=path,
            message=f"Type mismatch: got {type(effective_value).__name__}, expected one of {[t.__name__ for t in compatible_types]}",
            severity=MappingErrorSeverity.WARNING,
            details={
                "jira_type": mapping.jira_attribute_type.value,
                "value_type": type(effective_value).__name__,
                "value": str(effective_value)[:100],
            },
        ))
    
    # Check enum/select values
    if mapping.jira_attribute_type == JiraAttributeType.SELECT:
        allowed = mapping.allowed_values or []
        if jira_attribute_info:
            # Get allowed values from Jira attribute info
            type_attribute = jira_attribute_info.get("typeAttribute", {})
            options = type_attribute.get("options", [])
            if options:
                allowed = [opt.get("value", opt.get("name", "")) for opt in options]
        
        if allowed and str(effective_value) not in allowed:
            errors.append(MappingError(
                path=path,
                message=f"Invalid enum value: '{effective_value}' not in allowed values",
                severity=MappingErrorSeverity.ERROR,
                details={
                    "value": str(effective_value),
                    "allowed_values": allowed[:10],  # Limit for display
                },
            ))
    
    # Validate specific formats
    if mapping.jira_attribute_type == JiraAttributeType.EMAIL:
        if isinstance(effective_value, str) and "@" not in effective_value:
            errors.append(MappingError(
                path=path,
                message=f"Invalid email format: '{effective_value}'",
                severity=MappingErrorSeverity.WARNING,
            ))
    
    if mapping.jira_attribute_type == JiraAttributeType.URL:
        if isinstance(effective_value, str) and not (
            effective_value.startswith("http://") or 
            effective_value.startswith("https://")
        ):
            errors.append(MappingError(
                path=path,
                message=f"Invalid URL format: '{effective_value}'",
                severity=MappingErrorSeverity.WARNING,
            ))
    
    return errors


def validate_field_mapping(
    mapping: FieldMapping,
    value: Any | None,
    jira_field_info: dict[str, Any] | None = None,
) -> list[MappingError]:
    """Validate a single issue field mapping.
    
    Args:
        mapping: The field mapping configuration.
        value: The resolved value.
        jira_field_info: Optional Jira field metadata.
        
    Returns:
        List of validation errors.
    """
    errors: list[MappingError] = []
    path = f"fields.{mapping.jira_field_name}"
    
    # Use static value or template if no direct value
    effective_value = value
    if effective_value is None:
        effective_value = mapping.static_value
    
    # Check required fields
    if mapping.required and effective_value is None:
        errors.append(MappingError(
            path=path,
            message="Required field is missing",
            severity=MappingErrorSeverity.ERROR,
            details={"source": mapping.source, "static_value": mapping.static_value},
        ))
    
    return errors


def validate_all_mappings(
    attribute_mappings: list[AttributeMapping],
    device_data: dict[str, Any],
    jira_attributes: list[dict[str, Any]] | None = None,
) -> list[MappingError]:
    """Validate all attribute mappings against device data.
    
    Args:
        attribute_mappings: List of attribute mappings.
        device_data: NinjaOne device data.
        jira_attributes: Optional list of Jira attribute definitions.
        
    Returns:
        List of all validation errors.
    """
    errors: list[MappingError] = []
    
    # Build lookup for Jira attribute info
    jira_attr_lookup = {}
    if jira_attributes:
        for attr in jira_attributes:
            jira_attr_lookup[str(attr.get("id"))] = attr
    
    for mapping in attribute_mappings:
        # Extract value from device data using source path
        value = get_nested_value(device_data, mapping.source)
        
        # Get Jira attribute info if available
        jira_info = jira_attr_lookup.get(mapping.jira_attribute_id)
        
        # Validate this mapping
        mapping_errors = validate_attribute_mapping(mapping, value, jira_info)
        errors.extend(mapping_errors)
    
    return errors


def check_required_coverage(
    attribute_mappings: list[AttributeMapping],
    jira_required_attributes: list[dict[str, Any]],
) -> list[MappingError]:
    """Check that all required Jira attributes are mapped.
    
    Args:
        attribute_mappings: Configured attribute mappings.
        jira_required_attributes: Jira attributes marked as required.
        
    Returns:
        List of errors for unmapped required attributes.
    """
    errors: list[MappingError] = []
    
    mapped_ids = {m.jira_attribute_id for m in attribute_mappings}
    
    for attr in jira_required_attributes:
        attr_id = str(attr.get("id"))
        attr_name = attr.get("name", attr_id)
        
        if attr_id not in mapped_ids:
            errors.append(MappingError(
                path=f"attributes.{attr_name}",
                message=f"Required Jira attribute '{attr_name}' is not mapped",
                severity=MappingErrorSeverity.ERROR,
                details={"jira_attribute_id": attr_id},
            ))
    
    return errors


def get_nested_value(data: dict[str, Any], path: str) -> Any | None:
    """Extract a nested value from a dictionary using dot notation.
    
    Supports array indexing with [n] notation.
    
    Args:
        data: Dictionary to search.
        path: Dot-separated path (e.g., 'system.serialNumber', 'disks[0].size').
        
    Returns:
        Value at path, or None if not found.
    """
    if not path:
        return None
    
    current = data
    
    # Parse path segments
    import re
    segments = re.split(r'\.(?![^\[]*\])', path)
    
    for segment in segments:
        if current is None:
            return None
        
        # Check for array indexing
        match = re.match(r'(\w+)\[(\d+)\]', segment)
        if match:
            key, index = match.groups()
            if isinstance(current, dict) and key in current:
                current = current[key]
                if isinstance(current, list) and int(index) < len(current):
                    current = current[int(index)]
                else:
                    return None
            else:
                return None
        else:
            if isinstance(current, dict) and segment in current:
                current = current[segment]
            else:
                return None
    
    return current

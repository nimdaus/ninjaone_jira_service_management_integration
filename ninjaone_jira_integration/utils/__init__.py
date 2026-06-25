"""Utility modules for the integration service."""

import re as _re
from typing import Any

from ninjaone_jira_integration.utils.secrets import redact_secrets
from ninjaone_jira_integration.utils.concurrency import RateLimiter, TokenBucket


def get_nested_value(data: dict[Any, Any], path: str) -> Any | None:
    """Extract a nested value using dot notation and optional [n] array indexing.

    Supports:
    - Dot notation: 'system.serialNumber'
    - Array indexing: 'disks[0].size'
    - Case-insensitive key fallback for device data
    """
    if not path or not data:
        return None
    current: Any = data
    for segment in _re.split(r'\.(?![^\[]*\])', path):
        if current is None:
            return None
        m = _re.match(r'^(\w+)\[(\d+)\]$', segment)
        if m:
            key, idx = m.groups()
            if isinstance(current, dict) and key in current:
                arr = current[key]
                current = arr[int(idx)] if isinstance(arr, list) and int(idx) < len(arr) else None
            else:
                return None
        elif isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            current = next(
                (current[k] for k in current if isinstance(current, dict) and k.lower() == segment.lower()),
                None,
            )
    return current


__all__ = [
    "redact_secrets",
    "RateLimiter",
    "TokenBucket",
    "get_nested_value",
]

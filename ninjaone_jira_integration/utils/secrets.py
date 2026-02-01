"""
Secret redaction utilities.

Ensures secrets are never logged or exposed in error messages.
"""

from __future__ import annotations

import re
from typing import Any

# Patterns that indicate a secret value
SECRET_KEY_PATTERNS = [
    r".*secret.*",
    r".*token.*",
    r".*password.*",
    r".*api[_-]?key.*",
    r".*auth.*",
    r".*credential.*",
    r".*private.*",
]

# Compiled patterns for efficiency
_SECRET_KEY_REGEX = re.compile(
    "|".join(SECRET_KEY_PATTERNS),
    re.IGNORECASE,
)

# Redaction placeholder
REDACTED = "[REDACTED]"


def is_secret_key(key: str) -> bool:
    """Check if a key name indicates a secret value.
    
    Args:
        key: Key name to check.
        
    Returns:
        True if the key likely contains a secret.
    """
    return bool(_SECRET_KEY_REGEX.match(key))


def redact_secrets(
    data: dict[str, Any],
    additional_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Recursively redact secret values from a dictionary.
    
    Args:
        data: Dictionary potentially containing secrets.
        additional_keys: Additional key names to redact.
        
    Returns:
        New dictionary with secrets replaced by '[REDACTED]'.
    """
    result = {}
    extra_keys = additional_keys or set()
    
    for key, value in data.items():
        if is_secret_key(key) or key in extra_keys:
            result[key] = REDACTED
        elif isinstance(value, dict):
            result[key] = redact_secrets(value, extra_keys)
        elif isinstance(value, list):
            result[key] = [
                redact_secrets(item, extra_keys) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    
    return result


def redact_string(
    text: str,
    secrets: list[str],
) -> str:
    """Redact known secret values from a string.
    
    Args:
        text: Text that may contain secrets.
        secrets: List of secret values to redact.
        
    Returns:
        Text with secrets replaced by '[REDACTED]'.
    """
    result = text
    for secret in secrets:
        if secret and len(secret) > 3:  # Don't redact very short strings
            result = result.replace(secret, REDACTED)
    return result


def mask_secret(value: str, visible_chars: int = 4) -> str:
    """Partially mask a secret value for display.
    
    Args:
        value: Secret value to mask.
        visible_chars: Number of characters to leave visible at end.
        
    Returns:
        Masked value like '****abcd'.
    """
    if not value or len(value) <= visible_chars:
        return "*" * len(value) if value else ""
    
    return "*" * (len(value) - visible_chars) + value[-visible_chars:]


class SecretFilter:
    """Log filter that redacts secrets from log records."""
    
    def __init__(self, secrets: list[str] | None = None):
        """Initialize with list of secret values to redact.
        
        Args:
            secrets: List of secret string values.
        """
        self.secrets = secrets or []
    
    def add_secret(self, secret: str) -> None:
        """Add a secret to the filter.
        
        Args:
            secret: Secret value to add.
        """
        if secret and secret not in self.secrets:
            self.secrets.append(secret)
    
    def filter(self, record: Any) -> bool:
        """Filter log record to redact secrets.
        
        Args:
            record: Log record to filter.
            
        Returns:
            True (always allow record, but modify it).
        """
        if hasattr(record, "msg") and isinstance(record.msg, str):
            record.msg = redact_string(record.msg, self.secrets)
        
        if hasattr(record, "args") and record.args:
            args = list(record.args)
            for i, arg in enumerate(args):
                if isinstance(arg, str):
                    args[i] = redact_string(arg, self.secrets)
            record.args = tuple(args)
        
        return True

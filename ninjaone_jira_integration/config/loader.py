"""
Configuration loader with precedence-based value resolution.

Precedence order (highest to lowest):
1. CLI flags (non-secrets only, unless --allow-cli-secrets)
2. Environment variables
3. .env file (auto-loaded from CWD, then config file directory)
4. Config file values (YAML or JSON)

Secrets are never written to config files by default.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import SecretStr

from ninjaone_jira_integration.config.models import AppConfig
from ninjaone_jira_integration.utils import get_nested_value

# Environment variable prefix
ENV_PREFIX = "NINJA_JIRA_"

# Mapping of config paths to environment variables
ENV_VAR_MAPPING = {
    # NinjaOne
    "ninjaone.base_url": "NINJA_BASE_URL",
    "ninjaone.client_id": "NINJA_CLIENT_ID",
    "ninjaone.client_secret": "NINJA_CLIENT_SECRET",
    # Jira
    "jira.email": "JIRA_EMAIL",
    "jira.api_token": "JIRA_API_TOKEN",
    "jira.subdomain": "JIRA_SUBDOMAIN",
    "jira.workspace_id": "JIRA_WORKSPACE_ID",
    # Assets
    "assets.schema_id": "JIRA_ASSETS_SCHEMA_ID",
    "assets.object_type_id": "JIRA_ASSETS_OBJECT_TYPE_ID",
    # Issues
    "issues.project_key": "JIRA_PROJECT_KEY",
    "issues.issue_type_id": "JIRA_ISSUE_TYPE_ID",
    # Server
    "server.host": "SERVER_HOST",
    "server.port": "SERVER_PORT",
    "server.webhook.secret": "WEBHOOK_SECRET",
    # Database
    "database.path": "DATABASE_PATH",
    # Heartbeat / outbound notifications
    "heartbeat.enabled": "HEARTBEAT_ENABLED",
    "heartbeat.url": "HEARTBEAT_URL",
    "heartbeat.interval_seconds": "HEARTBEAT_INTERVAL_SECONDS",
    "heartbeat.token": "HEARTBEAT_TOKEN",
    "heartbeat.notify_on_changes": "HEARTBEAT_NOTIFY_ON_CHANGES",
    # Concurrency
    "concurrency.max_workers": "MAX_WORKERS",
    "concurrency.max_in_flight_jira_requests": "MAX_IN_FLIGHT_JIRA_REQUESTS",
    "concurrency.jira_requests_per_minute": "JIRA_REQUESTS_PER_MINUTE",
    # Logging
    "logging.level": "LOG_LEVEL",
    "logging.format": "LOG_FORMAT",
    "logging.file": "LOG_FILE",
    # Schedule (device sync)
    "schedule.enabled": "SCHEDULE_ENABLED",
    "schedule.interval_hours": "SCHEDULE_INTERVAL_HOURS",
    # Alert polling schedule
    "alert_schedule.enabled": "ALERT_SCHEDULE_ENABLED",
    "alert_schedule.interval_minutes": "ALERT_SCHEDULE_INTERVAL_MINUTES",
}

# Known secret paths (never write to config file)
SECRET_PATHS = {
    "ninjaone.client_secret",
    "jira.api_token",
    "server.webhook.secret",
    "heartbeat.token",
}

# Default config file locations
DEFAULT_CONFIG_PATHS = [
    Path("config.yaml"),
    Path("config.yml"),
    Path("config.json"),
    Path.home() / ".config" / "ninja-jira" / "config.yaml",
]


def find_config_file(config_path: str | Path | None = None) -> Path | None:
    """Find configuration file.

    Search order:
    1. Explicit path (if provided)
    2. NINJA_JIRA_CONFIG environment variable
    3. CWD and parent directories (walking up to a .git/pyproject.toml root)
    4. ~/.config/ninja-jira/config.yaml

    Args:
        config_path: Explicit config file path, or None to search defaults.

    Returns:
        Path to config file if found, None otherwise.
    """
    if config_path:
        path = Path(config_path)
        if path.exists():
            return path
        return None

    # Check environment variable override
    env_path = os.environ.get("NINJA_JIRA_CONFIG")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path

    # Walk up from CWD looking for config.yaml / config.yml / config.json
    cwd = Path.cwd()
    candidate = cwd
    while True:
        for name in ("config.yaml", "config.yml", "config.json"):
            p = candidate / name
            if p.exists():
                return p
        # Stop at a project root marker or filesystem root
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            break
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    # Home directory fallback
    home_config = Path.home() / ".config" / "ninja-jira" / "config.yaml"
    if home_config.exists():
        return home_config

    return None


def load_dotenv_files(config_file_dir: Path | None = None) -> None:
    """Load .env files from CWD and config file directory.
    
    Files are loaded in order, with later files overriding earlier ones:
    1. Config file directory .env (if different from CWD)
    2. CWD .env
    """
    loaded_paths: set[Path] = set()
    
    # Load from config file directory first (lower priority)
    if config_file_dir and config_file_dir != Path.cwd():
        env_path = config_file_dir / ".env"
        if env_path.exists() and env_path not in loaded_paths:
            load_dotenv(env_path, override=False)
            loaded_paths.add(env_path)
    
    # Load from CWD (higher priority, will override)
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists() and cwd_env not in loaded_paths:
        load_dotenv(cwd_env, override=True)
        loaded_paths.add(cwd_env)


def set_nested_value(data: dict[str, Any], path: str, value: Any) -> None:
    """Set a nested value in a dictionary using dot notation.
    
    Args:
        data: Dictionary to modify.
        path: Dot-separated path (e.g., 'ninjaone.client_id').
        value: Value to set.
    """
    keys = path.split(".")
    current = data
    
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    
    current[keys[-1]] = value


def load_config_file(path: Path) -> dict[str, Any]:
    """Load configuration from a YAML or JSON file.
    
    Args:
        path: Path to config file.
        
    Returns:
        Configuration dictionary.
    """
    content = path.read_text(encoding="utf-8")
    
    if path.suffix in (".yaml", ".yml"):
        return yaml.safe_load(content) or {}
    elif path.suffix == ".json":
        return json.loads(content)
    else:
        # Try YAML first, then JSON
        try:
            return yaml.safe_load(content) or {}
        except yaml.YAMLError:
            return json.loads(content)


def apply_env_overrides(config_data: dict[str, Any]) -> dict[str, Any]:
    """Apply environment variable overrides to config data.
    
    Args:
        config_data: Base configuration dictionary.
        
    Returns:
        Configuration with environment variable overrides applied.
    """
    result = config_data.copy()
    
    for config_path, env_var in ENV_VAR_MAPPING.items():
        # Check for prefixed version first, then unprefixed
        value = os.environ.get(f"{ENV_PREFIX}{env_var}") or os.environ.get(env_var)
        
        if value is not None:
            # Handle type conversion for known types
            if config_path.endswith("_seconds") or config_path.endswith("_minutes"):
                try:
                    value = int(value)
                except ValueError:
                    pass
            elif config_path in ("server.port",):
                try:
                    value = int(value)
                except ValueError:
                    pass
            elif config_path.endswith("_hours") or config_path.endswith("_minutes"):
                try:
                    value = float(value)
                except ValueError:
                    pass
            elif value.lower() in ("true", "false"):
                value = value.lower() == "true"
            
            set_nested_value(result, config_path, value)
    
    return result


def load_config(
    config_path: str | Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> AppConfig:
    """Load application configuration with full precedence resolution.
    
    Precedence order (highest to lowest):
    1. CLI overrides
    2. Environment variables
    3. .env file
    4. Config file
    5. Defaults
    
    Args:
        config_path: Optional explicit path to config file.
        cli_overrides: Optional CLI flag overrides.
        
    Returns:
        Fully resolved AppConfig instance.
    """
    config_data: dict[str, Any] = {}
    
    # Find and load config file
    config_file = find_config_file(config_path)
    config_file_dir = config_file.parent if config_file else None
    
    if config_file:
        config_data = load_config_file(config_file)
    
    # Load .env files
    load_dotenv_files(config_file_dir)
    
    # Apply environment variable overrides
    config_data = apply_env_overrides(config_data)
    
    # Apply CLI overrides (highest priority)
    if cli_overrides:
        for path, value in cli_overrides.items():
            if value is not None:
                set_nested_value(config_data, path, value)
    
    # Create and return AppConfig
    return AppConfig.model_validate(config_data)


def save_config(
    config: AppConfig,
    path: Path,
    write_secrets: bool = False,
) -> None:
    """Save configuration to a file.
    
    By default, secrets are NOT written to the file. Instead, comments
    indicate which environment variables should be used.
    
    Args:
        config: Configuration to save.
        path: Path to save to.
        write_secrets: If True, write secrets to file (use with caution).
    """
    # Convert to dict, excluding secrets by default
    data = config.model_dump(mode="json")
    
    if not write_secrets:
        # Remove secret values and add comments about env vars
        for secret_path in SECRET_PATHS:
            current = data
            keys = secret_path.split(".")
            
            for key in keys[:-1]:
                if key in current:
                    current = current[key]
                else:
                    break
            else:
                if keys[-1] in current:
                    # Replace with placeholder
                    env_var = ENV_VAR_MAPPING.get(secret_path, secret_path.upper().replace(".", "_"))
                    current[keys[-1]] = f"${{{env_var}}}"
    
    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write based on file extension
    if path.suffix in (".yaml", ".yml"):
        header = """# NinjaOne → Jira Service Management Integration Configuration
# 
# Secrets should be set via environment variables:
#   - NINJA_CLIENT_SECRET
#   - JIRA_API_TOKEN
#   - WEBHOOK_SECRET
#   - HEARTBEAT_TOKEN
#
# See README.md for full configuration reference.

"""
        content = header + yaml.dump(data, default_flow_style=False, sort_keys=False)
    else:
        content = json.dumps(data, indent=2)
    
    path.write_text(content, encoding="utf-8")



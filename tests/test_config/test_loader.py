"""Tests for configuration loader."""

import os
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr

from ninjaone_jira_integration.config import load_config, save_config
from ninjaone_jira_integration.config.loader import ENV_VAR_MAPPING


class TestConfigLoader:
    """Tests for configuration loading."""
    
    def test_load_default_config(self):
        """Test loading with all defaults when no file exists."""
        config = load_config(None)
        
        assert config is not None
        assert config.ninjaone.base_url == "https://app.ninjarmm.com"
    
    def test_load_from_file(self, temp_dir: Path):
        """Test loading configuration from YAML file."""
        config_path = temp_dir / "config.yaml"
        config_content = {
            "ninjaone": {
                "base_url": "https://eu.ninjarmm.com",
                "client_id": "file-client-id",
            },
            "jira": {
                "subdomain": "filecompany",
            },
        }
        
        with open(config_path, "w") as f:
            yaml.dump(config_content, f)
        
        config = load_config(config_path)
        
        assert config.ninjaone.base_url == "https://eu.ninjarmm.com"
        assert config.ninjaone.client_id == "file-client-id"
        assert config.jira.subdomain == "filecompany"
    
    def test_env_var_override(self, temp_dir: Path, monkeypatch):
        """Test that environment variables override config file values."""
        # Set up a config file
        config_path = temp_dir / "config.yaml"
        config_content = {
            "ninjaone": {
                "client_id": "file-client-id",
            },
        }
        
        with open(config_path, "w") as f:
            yaml.dump(config_content, f)
        
        # Set environment variable
        monkeypatch.setenv("NINJA_CLIENT_ID", "env-client-id")
        
        config = load_config(config_path)
        
        # Environment variable should win
        assert config.ninjaone.client_id == "env-client-id"
    
    def test_secret_env_var_override(self, monkeypatch):
        """Test that secret environment variables work correctly."""
        monkeypatch.setenv("NINJA_CLIENT_SECRET", "secret-from-env")
        monkeypatch.setenv("JIRA_API_TOKEN", "token-from-env")
        
        config = load_config(None)
        
        assert config.ninjaone.client_secret.get_secret_value() == "secret-from-env"
        assert config.jira.api_token.get_secret_value() == "token-from-env"
    
    def test_dotenv_file(self, temp_dir: Path, monkeypatch):
        """Test loading from .env file."""
        # Change to temp directory for .env file
        monkeypatch.chdir(temp_dir)
        
        # Create .env file
        env_content = """
NINJA_CLIENT_ID=dotenv-client-id
NINJA_CLIENT_SECRET=dotenv-secret
"""
        (temp_dir / ".env").write_text(env_content)
        
        config = load_config()
        
        assert config.ninjaone.client_id == "dotenv-client-id"


class TestConfigSaver:
    """Tests for configuration saving."""
    
    def test_save_config(self, temp_dir: Path):
        """Test saving configuration to file."""
        config_path = temp_dir / "output.yaml"
        config_data = {
            "ninjaone": {
                "base_url": "https://app.ninjarmm.com",
                "client_id": "my-client-id",
            },
            "jira": {
                "subdomain": "mycompany",
            },
        }
        
        save_config(config_data, config_path)
        
        assert config_path.exists()
        
        with open(config_path) as f:
            loaded = yaml.safe_load(f)
        
        assert loaded["ninjaone"]["client_id"] == "my-client-id"
    
    def test_save_without_secrets(self, temp_dir: Path):
        """Test that secrets are excluded by default."""
        config_path = temp_dir / "output.yaml"
        config_data = {
            "ninjaone": {
                "client_id": "my-id",
                "client_secret": "super-secret",
            },
            "jira": {
                "api_token": "my-token",
            },
        }
        
        save_config(config_data, config_path, include_secrets=False)
        
        with open(config_path) as f:
            content = f.read()
        
        # Secrets should not be in the file
        assert "super-secret" not in content
        assert "my-token" not in content


class TestEnvVarMapping:
    """Tests for environment variable mapping."""
    
    def test_mapping_exists(self):
        """Test that expected mappings exist."""
        assert "ninjaone.client_id" in ENV_VAR_MAPPING
        assert "ninjaone.client_secret" in ENV_VAR_MAPPING
        assert "jira.api_token" in ENV_VAR_MAPPING
        assert "jira.subdomain" in ENV_VAR_MAPPING
    
    def test_mapping_values(self):
        """Test that mappings have correct values."""
        assert ENV_VAR_MAPPING["ninjaone.client_id"] == "NINJA_CLIENT_ID"
        assert ENV_VAR_MAPPING["ninjaone.client_secret"] == "NINJA_CLIENT_SECRET"
        assert ENV_VAR_MAPPING["jira.api_token"] == "JIRA_API_TOKEN"

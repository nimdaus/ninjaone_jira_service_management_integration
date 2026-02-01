"""Tests for configuration models."""

import pytest
from pydantic import SecretStr, ValidationError

from ninjaone_jira_integration.config.models import (
    AppConfig,
    AttributeMapping,
    JiraAssetsConfig,
    JiraAttributeType,
    JiraConfig,
    NinjaOneConfig,
)


class TestNinjaOneConfig:
    """Tests for NinjaOneConfig model."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = NinjaOneConfig()
        
        assert config.base_url == "https://app.ninjarmm.com"
        assert config.client_id == ""
        assert config.scopes == ["monitoring", "management"]
    
    def test_base_url_normalization(self):
        """Test that trailing slashes are removed from base URL."""
        config = NinjaOneConfig(base_url="https://eu.ninjarmm.com/")
        
        assert config.base_url == "https://eu.ninjarmm.com"
    
    def test_is_configured(self):
        """Test is_configured method."""
        # Not configured
        config = NinjaOneConfig()
        assert config.is_configured() is False
        
        # Configured
        config = NinjaOneConfig(
            client_id="test-id",
            client_secret=SecretStr("test-secret"),
        )
        assert config.is_configured() is True
    
    def test_secret_not_exposed(self):
        """Test that client_secret is not exposed when serialized."""
        config = NinjaOneConfig(
            client_id="test-id",
            client_secret=SecretStr("super-secret-value"),
        )
        
        # Check that the secret is masked
        assert "super-secret-value" not in repr(config)
        assert "super-secret-value" not in str(config)


class TestJiraConfig:
    """Tests for JiraConfig model."""
    
    def test_base_url_property(self):
        """Test base_url property construction."""
        config = JiraConfig(subdomain="mycompany")
        
        assert config.base_url == "https://mycompany.atlassian.net"
    
    def test_assets_api_url_property(self):
        """Test assets_api_url property construction."""
        config = JiraConfig(subdomain="mycompany", workspace_id="ws-123")
        
        assert config.assets_api_url == "https://api.atlassian.com/jsm/assets/workspace/ws-123/v1"


class TestAttributeMapping:
    """Tests for AttributeMapping model."""
    
    def test_basic_mapping(self):
        """Test creating a basic attribute mapping."""
        mapping = AttributeMapping(
            jira_attribute_id="100",
            jira_attribute_name="Name",
            source="systemName",
        )
        
        assert mapping.jira_attribute_id == "100"
        assert mapping.jira_attribute_name == "Name"
        assert mapping.source == "systemName"
        assert mapping.required is False
        assert mapping.transform is None
    
    def test_mapping_with_transform(self):
        """Test mapping with transformation."""
        mapping = AttributeMapping(
            jira_attribute_id="101",
            jira_attribute_name="Serial",
            source="system.serialNumber",
            transform="normalize_serial",
        )
        
        assert mapping.transform == "normalize_serial"
    
    def test_mapping_with_default_value(self):
        """Test mapping with default value fallback."""
        mapping = AttributeMapping(
            jira_attribute_id="102",
            jira_attribute_name="Status",
            source="status",
            default_value="Unknown",
        )
        
        assert mapping.default_value == "Unknown"
    
    def test_attribute_type_enum(self):
        """Test JiraAttributeType enum values."""
        mapping = AttributeMapping(
            jira_attribute_id="103",
            jira_attribute_name="Select Field",
            source="category",
            jira_attribute_type=JiraAttributeType.SELECT,
        )
        
        assert mapping.jira_attribute_type == JiraAttributeType.SELECT


class TestAppConfig:
    """Tests for AppConfig root model."""
    
    def test_default_config(self):
        """Test creating config with all defaults."""
        config = AppConfig()
        
        assert config.ninjaone is not None
        assert config.jira is not None
        assert config.assets is not None
        assert config.database is not None
        assert config.retry is not None
    
    def test_config_with_values(self):
        """Test creating config with values."""
        config = AppConfig(
            ninjaone=NinjaOneConfig(client_id="test-id"),
            jira=JiraConfig(subdomain="testcompany"),
        )
        
        assert config.ninjaone.client_id == "test-id"
        assert config.jira.subdomain == "testcompany"


class TestJiraAssetsConfig:
    """Tests for JiraAssetsConfig model."""
    
    def test_with_mappings(self):
        """Test assets config with attribute mappings."""
        config = JiraAssetsConfig(
            object_schema_id="1",
            object_type_id="10",
            attribute_mappings=[
                AttributeMapping(
                    jira_attribute_id="100",
                    jira_attribute_name="Name",
                    source="systemName",
                ),
            ],
        )
        
        assert len(config.attribute_mappings) == 1
        assert config.attribute_mappings[0].jira_attribute_name == "Name"

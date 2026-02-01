"""Tests for alert processor."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from ninjaone_jira_integration.config.models import AppConfig, JiraIssueConfig
from ninjaone_jira_integration.alerts.processor import (
    AlertProcessor,
    AlertAction,
    AlertResult,
)


class TestAlertProcessor:
    """Tests for AlertProcessor."""
    
    @pytest.fixture
    def alert_config(self) -> JiraIssueConfig:
        """Create alert processing configuration."""
        return JiraIssueConfig(
            project_key="HELP",
            issue_type_id="10001",
            priority_mapping={
                "CRITICAL": "1",
                "MAJOR": "2",
                "MODERATE": "3",
                "MINOR": "4",
            },
            severity_filter=["CRITICAL", "MAJOR"],
            source_types=["CONDITION"],
        )
    
    @pytest.mark.asyncio
    async def test_should_process_high_severity(
        self,
        alert_config,
        sample_ninjaone_alert,
    ):
        """Test that high severity alerts are processed."""
        processor = AlertProcessor(
            config=alert_config,
            jira_client=AsyncMock(),
            mapping_store=AsyncMock(),
        )
        
        should_process = processor.should_process(sample_ninjaone_alert)
        
        assert should_process is True
    
    @pytest.mark.asyncio
    async def test_should_not_process_low_severity(
        self,
        alert_config,
    ):
        """Test that low severity alerts are skipped."""
        processor = AlertProcessor(
            config=alert_config,
            jira_client=AsyncMock(),
            mapping_store=AsyncMock(),
        )
        
        alert = {
            "id": 12345,
            "severity": "MINOR",
            "sourceType": "CONDITION",
        }
        
        should_process = processor.should_process(alert)
        
        assert should_process is False
    
    @pytest.mark.asyncio
    async def test_should_not_process_wrong_source_type(
        self,
        alert_config,
    ):
        """Test that wrong source types are skipped."""
        processor = AlertProcessor(
            config=alert_config,
            jira_client=AsyncMock(),
            mapping_store=AsyncMock(),
        )
        
        alert = {
            "id": 12345,
            "severity": "CRITICAL",
            "sourceType": "OTHER_TYPE",
        }
        
        should_process = processor.should_process(alert)
        
        assert should_process is False
    
    @pytest.mark.asyncio
    async def test_build_issue_summary(
        self,
        alert_config,
        sample_ninjaone_alert,
    ):
        """Test building issue summary from alert."""
        processor = AlertProcessor(
            config=alert_config,
            jira_client=AsyncMock(),
            mapping_store=AsyncMock(),
        )
        
        summary = processor._build_summary(sample_ninjaone_alert)
        
        assert "High CPU usage detected" in summary
        assert "LAPTOP-ABC" in summary
    
    @pytest.mark.asyncio
    async def test_skip_duplicate_alert(
        self,
        alert_config,
        sample_ninjaone_alert,
    ):
        """Test that duplicate alerts are skipped."""
        # Mock mapping store to return existing mapping
        mapping_store = AsyncMock()
        mapping_store.get_alert_mapping = AsyncMock(return_value={
            "jira_issue_id": "10001",
            "jira_issue_key": "HELP-100",
        })
        
        processor = AlertProcessor(
            config=alert_config,
            jira_client=AsyncMock(),
            mapping_store=mapping_store,
        )
        
        result = await processor.process(sample_ninjaone_alert)
        
        assert result.action == AlertAction.ALREADY_EXISTS
        assert result.jira_issue_key == "HELP-100"
    
    @pytest.mark.asyncio
    async def test_create_new_issue(
        self,
        alert_config,
        sample_ninjaone_alert,
    ):
        """Test creating a new issue from alert."""
        # Mock mapping store to return no existing mapping
        mapping_store = AsyncMock()
        mapping_store.get_alert_mapping = AsyncMock(return_value=None)
        mapping_store.save_alert_mapping = AsyncMock()
        
        # Mock Jira client
        jira_client = AsyncMock()
        jira_client.create_issue = AsyncMock(return_value={
            "id": "10001",
            "key": "HELP-200",
        })
        
        processor = AlertProcessor(
            config=alert_config,
            jira_client=jira_client,
            mapping_store=mapping_store,
        )
        
        result = await processor.process(sample_ninjaone_alert)
        
        assert result.action == AlertAction.CREATED
        assert result.jira_issue_key == "HELP-200"
        
        # Verify mapping was saved
        mapping_store.save_alert_mapping.assert_called_once()


class TestAlertResult:
    """Tests for AlertResult dataclass."""
    
    def test_created_result(self):
        """Test creating a result for created issue."""
        result = AlertResult(
            alert_id=12345,
            action=AlertAction.CREATED,
            jira_issue_key="HELP-100",
            jira_issue_id="10001",
        )
        
        assert result.is_success() is True
        assert result.action == AlertAction.CREATED
    
    def test_error_result(self):
        """Test creating a result for failed processing."""
        result = AlertResult(
            alert_id=12345,
            action=AlertAction.ERROR,
            error="Failed to create issue",
        )
        
        assert result.is_success() is False
        assert result.error is not None
    
    def test_skipped_result(self):
        """Test creating a result for skipped alert."""
        result = AlertResult(
            alert_id=12345,
            action=AlertAction.SKIPPED,
        )
        
        assert result.is_success() is True
        assert result.action == AlertAction.SKIPPED

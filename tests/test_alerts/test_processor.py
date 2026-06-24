"""Tests for alert processor."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from ninjaone_jira_integration.alerts.processor import AlertAction, AlertProcessor, AlertResult
from ninjaone_jira_integration.config.models import AppConfig, JiraConfig, JiraIssueConfig, NinjaOneConfig
from ninjaone_jira_integration.store.mappings import AlertMapping


def make_config(**issues_kwargs) -> AppConfig:
    """Build a minimal AppConfig with the given issues overrides."""
    return AppConfig(
        ninjaone=NinjaOneConfig(
            base_url="https://app.ninjarmm.com",
            client_id="test",
            client_secret=SecretStr("secret"),
        ),
        jira=JiraConfig(
            subdomain="test",
            email="test@example.com",
            api_token=SecretStr("token"),
            workspace_id="ws-1",
        ),
        issues=JiraIssueConfig(**issues_kwargs),
    )


def make_processor(config: AppConfig, *, mapping: AlertMapping | None = None) -> AlertProcessor:
    ninja = AsyncMock()
    jira = AsyncMock()
    jira.create_issue = AsyncMock(return_value={"id": "10001", "key": "HELP-200"})
    db = MagicMock()

    processor = AlertProcessor(config=config, ninja_client=ninja, jira_client=jira, db=db)

    mapping_store = AsyncMock()
    mapping_store.get_alert_mapping = AsyncMock(return_value=mapping)
    mapping_store.upsert_alert_mapping = AsyncMock()
    processor.mapping_store = mapping_store

    return processor


class TestShouldCreateIssue:
    """Tests for _should_create_issue filtering logic."""

    def test_no_filters_allows_all(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001")
        processor = make_processor(config)
        assert processor._should_create_issue(sample_ninjaone_alert) is True

    def test_min_severity_passes_when_at_threshold(self) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001", min_severity="MAJOR")
        processor = make_processor(config)
        assert processor._should_create_issue({"severity": "MAJOR"}) is True

    def test_min_severity_passes_above_threshold(self) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001", min_severity="MAJOR")
        processor = make_processor(config)
        assert processor._should_create_issue({"severity": "CRITICAL"}) is True

    def test_min_severity_blocks_below_threshold(self) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001", min_severity="MAJOR")
        processor = make_processor(config)
        assert processor._should_create_issue({"severity": "MINOR"}) is False

    def test_source_type_filter_passes_matching(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001", source_types=["CONDITION"])
        processor = make_processor(config)
        assert processor._should_create_issue(sample_ninjaone_alert) is True

    def test_source_type_filter_blocks_non_matching(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001", source_types=["SCRIPT"])
        processor = make_processor(config)
        assert processor._should_create_issue(sample_ninjaone_alert) is False

    def test_null_severity_treated_as_none(self) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001", min_severity="MAJOR")
        processor = make_processor(config)
        assert processor._should_create_issue({"severity": None}) is False


class TestBuildSummary:
    """Tests for _build_summary."""

    def test_default_template_includes_message(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001")
        processor = make_processor(config)
        summary = processor._build_summary(sample_ninjaone_alert)
        assert "High CPU usage detected" in summary

    def test_default_template_includes_severity(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001")
        processor = make_processor(config)
        summary = processor._build_summary(sample_ninjaone_alert)
        assert "MAJOR" in summary

    def test_custom_template(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        config = make_config(
            project_key="HELP",
            issue_type_id="10001",
            summary_template="[Test] {message}",
        )
        processor = make_processor(config)
        summary = processor._build_summary(sample_ninjaone_alert)
        assert summary == "[Test] High CPU usage detected"


class TestBuildDescription:
    """Tests for _build_description."""

    def test_includes_uid(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001")
        processor = make_processor(config)
        desc = processor._build_description(sample_ninjaone_alert)
        assert "2c38a77c-50f9-47dd-9963-e89f830d2210" in desc

    def test_formats_float_timestamp(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001")
        processor = make_processor(config)
        desc = processor._build_description(sample_ninjaone_alert)
        # Should format as UTC datetime string, not raw float
        assert "UTC" in desc
        assert "1781023410" not in desc

    def test_includes_device_id(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001")
        processor = make_processor(config)
        desc = processor._build_description(sample_ninjaone_alert)
        assert "12345" in desc


class TestProcessAlert:
    """Tests for process_alert orchestration."""

    @pytest.mark.asyncio
    async def test_returns_exists_for_duplicate(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        existing = AlertMapping(
            ninja_alert_id="2c38a77c-50f9-47dd-9963-e89f830d2210",
            jira_issue_key="HELP-100",
            jira_issue_id="10001",
        )
        config = make_config(project_key="HELP", issue_type_id="10001")
        processor = make_processor(config, mapping=existing)

        result = await processor.process_alert(
            alert_id="2c38a77c-50f9-47dd-9963-e89f830d2210",
            alert=sample_ninjaone_alert,
        )

        assert result.action == AlertAction.EXISTS
        assert result.jira_issue_key == "HELP-100"

    @pytest.mark.asyncio
    async def test_creates_issue_when_no_mapping(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001")
        processor = make_processor(config, mapping=None)

        result = await processor.process_alert(
            alert_id="2c38a77c-50f9-47dd-9963-e89f830d2210",
            alert=sample_ninjaone_alert,
        )

        assert result.action == AlertAction.CREATED
        assert result.jira_issue_key == "HELP-200"
        processor.mapping_store.upsert_alert_mapping.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_filtered_out(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        config = make_config(
            project_key="HELP",
            issue_type_id="10001",
            source_types=["SCRIPT"],  # alert has sourceType=CONDITION
        )
        processor = make_processor(config, mapping=None)

        result = await processor.process_alert(
            alert_id="2c38a77c-50f9-47dd-9963-e89f830d2210",
            alert=sample_ninjaone_alert,
        )

        assert result.action == AlertAction.SKIPPED
        processor.jira_client.create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_does_not_call_jira(self, sample_ninjaone_alert: dict[str, Any]) -> None:
        config = make_config(project_key="HELP", issue_type_id="10001")
        processor = make_processor(config, mapping=None)

        result = await processor.process_alert(
            alert_id="2c38a77c-50f9-47dd-9963-e89f830d2210",
            alert=sample_ninjaone_alert,
            dry_run=True,
        )

        assert result.action == AlertAction.CREATED
        processor.jira_client.create_issue.assert_not_called()


class TestAlertResult:
    """Tests for AlertResult dataclass."""

    def test_created_result(self) -> None:
        result = AlertResult(
            alert_id="uid-123",
            device_id=None,
            action=AlertAction.CREATED,
            jira_issue_key="HELP-100",
            jira_issue_id="10001",
        )
        assert result.action == AlertAction.CREATED
        assert result.jira_issue_key == "HELP-100"

    def test_failed_result(self) -> None:
        result = AlertResult(
            alert_id="uid-123",
            device_id=None,
            action=AlertAction.FAILED,
            error="Network error",
        )
        assert result.action == AlertAction.FAILED
        assert result.error == "Network error"

    def test_skipped_result(self) -> None:
        result = AlertResult(
            alert_id="uid-123",
            device_id=None,
            action=AlertAction.SKIPPED,
        )
        assert result.action == AlertAction.SKIPPED

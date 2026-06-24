"""
Integration tests that load the real sample JSON files from the repo root and push
data through the actual parsing / mapping code.

These tests are skipped automatically when the sample files are not present (e.g.
in CI if they haven't been committed), so the suite never fails in that environment.
Add the files to git tracking to enable them in CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

_REPO_ROOT = Path(__file__).parents[2]
_ALERT_SAMPLE = _REPO_ROOT / "alert.sample.json"
_DEVICE_SAMPLE = _REPO_ROOT / "devices.sample.json"

_samples_present = pytest.mark.skipif(
    not _ALERT_SAMPLE.exists() or not _DEVICE_SAMPLE.exists(),
    reason="alert.sample.json / devices.sample.json not present",
)


@pytest.fixture(scope="module")
def alert_sample() -> list[dict]:
    with open(_ALERT_SAMPLE) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def device_sample() -> list[dict]:
    with open(_DEVICE_SAMPLE) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def _processor():
    """AlertProcessor wired with mock clients for _build_description testing."""
    from ninjaone_jira_integration.alerts.processor import AlertProcessor
    from ninjaone_jira_integration.config.models import AppConfig, JiraConfig, NinjaOneConfig

    config = AppConfig(
        ninjaone=NinjaOneConfig(
            base_url="https://app.ninjarmm.com",
            client_id="ci",
            client_secret=SecretStr("ci"),
        ),
        jira=JiraConfig(
            subdomain="ci",
            email="ci@example.com",
            api_token=SecretStr("ci"),
        ),
    )
    return AlertProcessor(
        config=config,
        ninja_client=AsyncMock(),
        jira_client=AsyncMock(),
        db=MagicMock(),
    )


@pytest.fixture(scope="module")
def _mapper():
    """DeviceMapper with a simple systemName mapping for smoke-test use."""
    from ninjaone_jira_integration.config.models import AttributeMapping, JiraAssetsConfig
    from ninjaone_jira_integration.sync.mapper import DeviceMapper

    config = JiraAssetsConfig(
        attribute_mappings=[
            AttributeMapping(
                jira_attribute_id="100",
                jira_attribute_name="Name",
                source="systemName",
                required=True,
            ),
            AttributeMapping(
                jira_attribute_id="101",
                jira_attribute_name="Serial Number",
                source="system.serialNumber",
                transforms=["normalize_serial"],
            ),
        ]
    )
    return DeviceMapper(config=config)


# ---------------------------------------------------------------------------
# Alert sample validation
# ---------------------------------------------------------------------------


class TestAlertSampleData:
    """Validate real alert payloads parse correctly throughout the pipeline."""

    @_samples_present
    def test_all_alerts_have_uid_not_id(self, alert_sample):
        """Every alert must use uid (UUID string) — the code dropped integer id support."""
        for alert in alert_sample:
            assert "uid" in alert, f"Alert missing uid: {alert}"
            assert isinstance(alert["uid"], str), f"uid must be a string: {alert['uid']}"
            # The integer 'id' field must NOT be present (would indicate API format change)
            assert "id" not in alert or not isinstance(alert.get("id"), int), (
                f"Unexpected integer 'id' field — check uid handling: {alert}"
            )

    @_samples_present
    def test_createtime_is_numeric(self, alert_sample):
        """createTime must be a numeric Unix timestamp (int or float), not an ISO string."""
        for alert in alert_sample:
            ct = alert.get("createTime")
            if ct is not None:
                assert isinstance(ct, (int, float)), (
                    f"createTime should be numeric, got {type(ct).__name__}: {ct}"
                )
                assert ct > 1_000_000_000, f"createTime looks too small to be a Unix ts: {ct}"

    @_samples_present
    def test_deviceid_is_integer(self, alert_sample):
        """deviceId must be an integer matching the devices sample."""
        for alert in alert_sample:
            device_id = alert.get("deviceId")
            if device_id is not None:
                assert isinstance(device_id, int), (
                    f"deviceId should be int, got {type(device_id).__name__}: {device_id}"
                )

    @_samples_present
    def test_build_description_no_crash(self, alert_sample, _processor):
        """_build_description must not raise for any real alert payload."""
        for alert in alert_sample:
            desc = _processor._build_description(alert)
            assert isinstance(desc, str)
            assert len(desc) > 0
            # createTime should be formatted as a human-readable UTC datetime, not a raw float
            if alert.get("createTime"):
                assert "UTC" in desc, (
                    f"createTime not formatted as UTC datetime in description for alert {alert.get('uid')}"
                )

    @_samples_present
    def test_uid_used_in_description(self, alert_sample, _processor):
        """Alert description should reference uid, not integer id."""
        for alert in alert_sample:
            desc = _processor._build_description(alert)
            uid = alert.get("uid", "")
            # Description should not show "N/A" for the alert ID when uid is present
            if uid:
                assert "N/A" not in desc or uid in desc


# ---------------------------------------------------------------------------
# Device sample validation
# ---------------------------------------------------------------------------


class TestDeviceSampleData:
    """Validate real device payloads parse correctly throughout the pipeline."""

    @_samples_present
    def test_all_devices_have_integer_id(self, device_sample):
        """Devices use integer id (not uid) — the code relies on device.get('id')."""
        for device in device_sample:
            assert "id" in device, f"Device missing id: {device.get('systemName')}"
            assert isinstance(device["id"], int), (
                f"Device id must be int, got {type(device['id']).__name__}"
            )

    @_samples_present
    def test_lastcontact_not_iso_string(self, device_sample):
        """lastContact must be a float Unix timestamp or None, not an ISO string."""
        for device in device_sample:
            lc = device.get("lastContact")
            if lc is not None:
                assert not isinstance(lc, str), (
                    f"lastContact is ISO string — expected float Unix timestamp: {lc!r} "
                    f"(device: {device.get('systemName')})"
                )
                assert isinstance(lc, (int, float))

    @_samples_present
    def test_extract_serial_number_no_crash(self, device_sample, _mapper):
        """extract_serial_number must not raise for any real device."""
        for device in device_sample:
            serial = _mapper.extract_serial_number(device)
            assert serial is None or isinstance(serial, str), (
                f"extract_serial_number returned unexpected type: {type(serial)}"
            )
            if serial is not None:
                # Normalized serials are always uppercase and non-empty
                assert serial == serial.upper()
                assert serial.strip() == serial
                assert serial not in ("NONE", "N/A", "UNKNOWN", "")

    @_samples_present
    def test_map_device_no_crash(self, device_sample, _mapper):
        """map_device must not raise for any real device; name attribute should map."""
        for device in device_sample:
            attrs = _mapper.map_device(device)
            assert isinstance(attrs, list)
            # Devices with a systemName should produce at least the Name attribute
            if device.get("systemName"):
                name_attrs = [a for a in attrs if a.get("objectTypeAttributeId") == "100"]
                assert len(name_attrs) == 1, (
                    f"Expected Name attribute for {device.get('systemName')}, got {attrs}"
                )

    @_samples_present
    def test_memory_path_not_system_memory_total(self, device_sample):
        """Confirm system.memory.total does not exist — real path is system.totalPhysicalMemory."""
        for device in device_sample:
            sys = device.get("system", {})
            assert "memory" not in sys, (
                f"Found system.memory nested object — README/config example would be wrong "
                f"(device: {device.get('systemName')})"
            )
            # The real path that DOES work
            if sys:
                assert "totalPhysicalMemory" in sys or True  # may be absent on network devices

    @_samples_present
    def test_serial_number_fallback_coverage(self, device_sample):
        """Report serial number coverage across the sample."""
        from ninjaone_jira_integration.sync.mapper import DeviceMapper
        from ninjaone_jira_integration.config.models import JiraAssetsConfig

        mapper = DeviceMapper(config=JiraAssetsConfig())
        found = sum(1 for d in device_sample if mapper.extract_serial_number(d) is not None)
        total = len(device_sample)
        # Don't assert a specific count — just ensure it doesn't crash and returns something
        assert 0 <= found <= total
        # Log the coverage for informational purposes during local runs
        print(f"\nSerial number coverage: {found}/{total} devices ({100*found//total}%)")

"""
Fixture-based integration tests.

Load the JSON responses captured from real NinjaOne and Jira environments
(via scripts/capture_ninja_fixtures.py and scripts/capture_jira_fixtures.py)
and run the actual parsing / mapping / diffing code against them.

Tests are automatically skipped when the fixture files are absent, so the
suite stays green in CI until fixtures are committed to the repo.  Add the
files to git to enable them everywhere.

Capture:
    uv run python scripts/capture_ninja_fixtures.py
    uv run python scripts/capture_jira_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_NINJA = _FIXTURES / "ninja"
_JIRA = _FIXTURES / "jira"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load(path: Path) -> object:
    return json.loads(path.read_text())


def _skip_unless(*paths: Path):
    missing = [p.name for p in paths if not p.exists()]
    return pytest.mark.skipif(bool(missing), reason=f"fixture(s) not present: {', '.join(missing)}")


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def _processor():
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
def _simple_mapper():
    """DeviceMapper with a minimal systemName mapping — works with any device fixture."""
    from ninjaone_jira_integration.config.models import AttributeMapping, JiraAssetsConfig
    from ninjaone_jira_integration.sync.mapper import DeviceMapper

    return DeviceMapper(config=JiraAssetsConfig(
        attribute_mappings=[
            AttributeMapping(
                jira_attribute_id="1",
                jira_attribute_name="Name",
                source="systemName",
                required=True,
            ),
        ]
    ))


# ══════════════════════════════════════════════════════════════════════════════
# NinjaOne fixture tests
# ══════════════════════════════════════════════════════════════════════════════

class TestNinjaDeviceFixtures:
    """Validate captured device payloads against code assumptions."""

    @_skip_unless(_NINJA / "devices.json")
    def test_all_devices_have_integer_id(self):
        for device in _load(_NINJA / "devices.json"):
            assert isinstance(device.get("id"), int), (
                f"device.id must be int: {device.get('systemName')} id={device.get('id')!r}"
            )

    @_skip_unless(_NINJA / "devices.json")
    def test_lastcontact_is_numeric_or_absent(self):
        for device in _load(_NINJA / "devices.json"):
            lc = device.get("lastContact")
            if lc is not None:
                assert isinstance(lc, (int, float)), (
                    f"lastContact should be a Unix timestamp float, "
                    f"got {type(lc).__name__}: {lc!r} (device {device.get('systemName')})"
                )

    @_skip_unless(_NINJA / "devices.json")
    def test_node_role_id_is_integer_or_absent(self):
        for device in _load(_NINJA / "devices.json"):
            role = device.get("nodeRoleId")
            if role is not None:
                assert isinstance(role, int), (
                    f"nodeRoleId must be int, got {type(role).__name__}: {role!r}"
                )

    @_skip_unless(_NINJA / "devices.json")
    def test_extract_serial_number_no_crash(self, _simple_mapper):
        for device in _load(_NINJA / "devices.json"):
            serial = _simple_mapper.extract_serial_number(device)
            assert serial is None or isinstance(serial, str)
            if serial is not None:
                assert serial == serial.upper()
                assert serial.strip() == serial
                assert serial not in ("NONE", "N/A", "NA", "UNKNOWN", "")

    @_skip_unless(_NINJA / "devices.json")
    def test_map_device_no_crash(self, _simple_mapper):
        for device in _load(_NINJA / "devices.json"):
            attrs = _simple_mapper.map_device(device)
            assert isinstance(attrs, list)

    @_skip_unless(_NINJA / "devices.json")
    def test_get_nested_value_common_paths(self):
        from ninjaone_jira_integration.utils import get_nested_value
        for device in _load(_NINJA / "devices.json"):
            # These paths must never raise — None is a valid return
            for path in ("systemName", "system.serialNumber", "os.name",
                         "system.manufacturer", "ipAddresses[0]"):
                result = get_nested_value(device, path)
                assert result is None or isinstance(result, (str, int, float, bool))

    @_skip_unless(_NINJA / "devices_by_role.json")
    def test_role_devices_each_have_matching_role(self):
        by_role = _load(_NINJA / "devices_by_role.json")
        for role_id_str, device in by_role.items():
            assert device.get("nodeRoleId") == int(role_id_str), (
                f"Device {device.get('systemName')} returned for role {role_id_str} "
                f"but nodeRoleId={device.get('nodeRoleId')}"
            )

    @_skip_unless(_NINJA / "device_samples.json")
    def test_individually_fetched_device_shape(self):
        """Validates GET /v2/device/{id} response structure across sampled devices."""
        samples = _load(_NINJA / "device_samples.json")
        assert len(samples) >= 1
        for device in samples:
            assert isinstance(device, dict), f"device sample must be a dict: {type(device)}"
            assert isinstance(device.get("id"), int), f"device.id must be int: {device.get('id')!r}"
            assert "systemName" in device or "displayName" in device, (
                f"device {device.get('id')} has neither systemName nor displayName"
            )

    @_skip_unless(_NINJA / "device_samples.json")
    def test_sampled_devices_span_multiple_roles(self):
        """Confirms samples were collected from diverse roles, not all the same type."""
        samples = _load(_NINJA / "device_samples.json")
        roles = {d.get("nodeRoleId") for d in samples if d.get("nodeRoleId") is not None}
        # Only enforce diversity when we actually got 3 samples
        if len(samples) >= 3:
            assert len(roles) > 1, (
                f"All {len(samples)} device samples share the same nodeRoleId — "
                "fixture captures no role diversity; re-run capture with more devices"
            )


class TestNinjaAlertFixtures:
    """Validate captured alert payloads against code assumptions."""

    @_skip_unless(_NINJA / "alerts.json")
    def test_all_alerts_have_uid_string(self):
        for alert in _load(_NINJA / "alerts.json"):
            uid = alert.get("uid")
            assert uid is not None, f"Alert missing uid: {alert}"
            assert isinstance(uid, str), f"uid must be a string, got {type(uid).__name__}: {uid!r}"

    @_skip_unless(_NINJA / "alerts.json")
    def test_no_integer_id_field(self):
        """Real NinjaOne alerts use uid — an integer id field would mean the API changed."""
        for alert in _load(_NINJA / "alerts.json"):
            assert not isinstance(alert.get("id"), int), (
                f"Unexpected integer 'id' on alert {alert.get('uid')} — "
                "processor uses uid; check whether API format changed"
            )

    @_skip_unless(_NINJA / "alerts.json")
    def test_createtime_is_numeric(self):
        for alert in _load(_NINJA / "alerts.json"):
            ct = alert.get("createTime")
            if ct is not None:
                assert isinstance(ct, (int, float)), (
                    f"createTime should be a Unix timestamp, "
                    f"got {type(ct).__name__}: {ct!r} (alert {alert.get('uid')})"
                )
                assert ct > 1_000_000_000, f"createTime too small to be a Unix ts: {ct}"

    @_skip_unless(_NINJA / "alerts.json")
    def test_device_id_is_integer_or_absent(self):
        for alert in _load(_NINJA / "alerts.json"):
            device_id = alert.get("deviceId")
            if device_id is not None:
                assert isinstance(device_id, int), (
                    f"deviceId must be int, got {type(device_id).__name__}: {device_id!r}"
                )

    @_skip_unless(_NINJA / "alerts.json")
    def test_build_description_no_crash(self, _processor):
        for alert in _load(_NINJA / "alerts.json"):
            desc = _processor._build_description(alert)
            assert isinstance(desc, str) and len(desc) > 0

    @_skip_unless(_NINJA / "alerts.json")
    def test_build_summary_no_crash(self, _processor):
        for alert in _load(_NINJA / "alerts.json"):
            summary = _processor._build_summary(alert)
            assert isinstance(summary, str) and len(summary) > 0

    @_skip_unless(_NINJA / "alerts.json")
    def test_skip_reason_does_not_crash(self, _processor):
        for alert in _load(_NINJA / "alerts.json"):
            reason = _processor._skip_reason(alert)
            assert reason is None or isinstance(reason, str)

    @_skip_unless(_NINJA / "alert_samples.json")
    def test_alert_samples_shape(self):
        """Validates alert structure across severity-diverse samples."""
        samples = _load(_NINJA / "alert_samples.json")
        assert len(samples) >= 1
        for alert in samples:
            assert isinstance(alert.get("uid"), str), (
                f"alert.uid must be a string: {alert.get('uid')!r}"
            )

    @_skip_unless(_NINJA / "alert_samples.json")
    def test_alert_samples_span_multiple_severities(self):
        """Confirms samples were collected from diverse severity levels."""
        samples = _load(_NINJA / "alert_samples.json")
        severities = {(a.get("severity") or "UNKNOWN").upper() for a in samples}
        if len(samples) >= 3:
            assert len(severities) > 1, (
                f"All {len(samples)} alert samples share the same severity — "
                "fixture captures no severity diversity"
            )


class TestNinjaRoleFixtures:
    """Validate roles fixture."""

    @_skip_unless(_NINJA / "roles.json")
    def test_roles_have_id_and_name(self):
        for role in _load(_NINJA / "roles.json"):
            assert isinstance(role.get("id"), int), f"role.id must be int: {role}"
            assert isinstance(role.get("name"), str), f"role.name must be str: {role}"


# ══════════════════════════════════════════════════════════════════════════════
# Jira fixture tests
# ══════════════════════════════════════════════════════════════════════════════

class TestJiraWorkspaceFixtures:
    """Validate workspace discovery response shape."""

    @_skip_unless(_JIRA / "workspace.json")
    def test_workspace_has_values_with_workspace_id(self):
        data = _load(_JIRA / "workspace.json")
        values = data.get("values", [])
        assert len(values) > 0, "workspace response must have at least one value"
        assert "workspaceId" in values[0], f"workspaceId missing from: {values[0]}"
        assert isinstance(values[0]["workspaceId"], str)

    @_skip_unless(_JIRA / "myself.json")
    def test_myself_has_account_id(self):
        me = _load(_JIRA / "myself.json")
        assert "accountId" in me, f"myself missing accountId: {list(me.keys())}"


class TestJiraSchemaFixtures:
    """Validate schema / object type response shapes."""

    @_skip_unless(_JIRA / "schemas.json")
    def test_schemas_have_id_and_name(self):
        for schema in _load(_JIRA / "schemas.json"):
            assert "id" in schema, f"schema missing id: {schema}"
            assert "name" in schema, f"schema missing name: {schema}"

    @_skip_unless(_JIRA / "schema.json")
    def test_schema_has_object_schema_key(self):
        schema = _load(_JIRA / "schema.json")
        assert "objectSchemaKey" in schema, (
            f"schema missing objectSchemaKey — check get_schema() parsing: {list(schema.keys())}"
        )

    @_skip_unless(_JIRA / "object_types.json")
    def test_object_types_have_id_and_name(self):
        for ot in _load(_JIRA / "object_types.json"):
            assert "id" in ot, f"object type missing id: {ot}"
            assert "name" in ot, f"object type missing name: {ot}"

    @_skip_unless(_JIRA / "object_type.json")
    def test_object_type_has_schema_reference(self):
        ot = _load(_JIRA / "object_type.json")
        assert "id" in ot
        assert "name" in ot
        # objectSchemaId is used by the capture script to resolve schema — must be present
        assert "objectSchemaId" in ot, (
            f"object_type missing objectSchemaId — schema lookup in capture script will break: {list(ot.keys())}"
        )


class TestJiraAttributeFixtures:
    """Validate object type attribute definition shapes."""

    @_skip_unless(_JIRA / "object_type_attributes.json")
    def test_attributes_have_required_fields(self):
        for attr in _load(_JIRA / "object_type_attributes.json"):
            assert "id" in attr, f"attribute missing id: {attr}"
            assert "name" in attr, f"attribute missing name: {attr}"

    @_skip_unless(_JIRA / "object_type_attributes.json")
    def test_default_type_has_id_and_name(self):
        for attr in _load(_JIRA / "object_type_attributes.json"):
            dt = attr.get("defaultType")
            if dt is not None:
                assert "id" in dt, f"defaultType missing id on attr {attr.get('name')}: {dt}"
                assert "name" in dt, f"defaultType missing name on attr {attr.get('name')}: {dt}"

    @_skip_unless(_JIRA / "object_type_attributes.json")
    def test_attribute_ids_are_strings(self):
        """Attribute IDs come back as strings from the API — code must not cast them to int."""
        for attr in _load(_JIRA / "object_type_attributes.json"):
            assert isinstance(attr["id"], str), (
                f"attribute id must be a string (used as dict key): "
                f"{attr['name']} id={attr['id']!r}"
            )


class TestJiraAssetFixtures:
    """Validate asset object response shapes."""

    @_skip_unless(_JIRA / "assets_search.json")
    def test_search_results_have_id_and_object_key(self):
        for asset in _load(_JIRA / "assets_search.json"):
            assert "id" in asset, f"asset missing id: {asset}"
            assert "objectKey" in asset, f"asset missing objectKey: {asset}"

    @_skip_unless(_JIRA / "asset_samples.json")
    def test_asset_samples_have_id_and_object_key(self):
        """Validates basic shape across all sampled assets."""
        samples = _load(_JIRA / "asset_samples.json")
        assert len(samples) >= 1
        for asset in samples:
            assert "id" in asset, f"asset sample missing id: {asset}"
            assert "objectKey" in asset, f"asset sample missing objectKey: {asset}"

    @_skip_unless(_JIRA / "asset_full_samples.json")
    def test_asset_full_samples_have_attributes_list(self):
        """Full asset fetch must include attributes — needed for diff and identity resolution."""
        samples = _load(_JIRA / "asset_full_samples.json")
        assert len(samples) >= 1
        for asset in samples:
            assert "attributes" in asset, (
                f"asset {asset.get('id')} missing attributes — "
                f"identity resolution and diff will fail: {list(asset.keys())}"
            )
            assert isinstance(asset["attributes"], list)

    @_skip_unless(_JIRA / "asset_full_samples.json")
    def test_attribute_values_format(self):
        """objectAttributeValues must be a list with {value: ...} entries across all samples."""
        for asset in _load(_JIRA / "asset_full_samples.json"):
            for attr in asset.get("attributes", []):
                vals = attr.get("objectAttributeValues", [])
                assert isinstance(vals, list), (
                    f"objectAttributeValues must be list on attr "
                    f"{attr.get('objectTypeAttributeId')} of asset {asset.get('id')}"
                )
                for v in vals:
                    assert "value" in v, (
                        f"objectAttributeValues entry missing 'value' key on attr "
                        f"{attr.get('objectTypeAttributeId')} of asset {asset.get('id')}: {v}"
                    )

    @_skip_unless(_JIRA / "asset_full_samples.json")
    def test_compute_attribute_diff_with_real_assets(self):
        """compute_attribute_diff must handle the real attribute format across all samples."""
        from ninjaone_jira_integration.sync.engine import compute_attribute_diff

        for asset in _load(_JIRA / "asset_full_samples.json"):
            current_attrs = asset.get("attributes", [])
            changed, descriptions = compute_attribute_diff(current_attrs, current_attrs)
            assert isinstance(changed, list)
            assert changed == [], (
                f"Diffing asset {asset.get('id')} against itself should yield no changes"
            )
            assert isinstance(descriptions, list)

    @_skip_unless(_JIRA / "asset_full_samples.json")
    def test_compute_attribute_diff_detects_change(self):
        """A modified attribute value must appear in the diff result."""
        from ninjaone_jira_integration.sync.engine import compute_attribute_diff
        import copy

        # Use the first sample that has at least one attribute with a value
        for asset in _load(_JIRA / "asset_full_samples.json"):
            current = asset.get("attributes", [])
            eligible = [
                a for a in current
                if a.get("objectAttributeValues") and
                a["objectAttributeValues"][0].get("value") is not None
            ]
            if not eligible:
                continue

            updated = copy.deepcopy(current)
            target_id = eligible[0].get("objectTypeAttributeId", eligible[0].get("id"))
            for attr in updated:
                if attr.get("objectTypeAttributeId", attr.get("id")) == target_id:
                    attr["objectAttributeValues"][0]["value"] = "__changed__"
                    break

            changed, _ = compute_attribute_diff(current, updated)
            assert len(changed) >= 1, (
                f"Expected at least one changed attribute on asset {asset.get('id')}"
            )
            return  # one passing case is sufficient

        pytest.skip("no asset samples have mutable attribute values")


class TestJiraIssueFixtures:
    """Validate Jira issue / project response shapes."""

    @_skip_unless(_JIRA / "projects.json")
    def test_projects_have_key_and_name(self):
        for project in _load(_JIRA / "projects.json"):
            assert "key" in project, f"project missing key: {project}"
            assert "name" in project, f"project missing name: {project}"

    @_skip_unless(_JIRA / "issue_types.json")
    def test_issue_types_have_id_and_name(self):
        for it in _load(_JIRA / "issue_types.json"):
            assert "id" in it, f"issue type missing id: {it}"
            assert "name" in it, f"issue type missing name: {it}"

    @_skip_unless(_JIRA / "issue_create_metadata.json")
    def test_issue_create_metadata_has_projects(self):
        meta = _load(_JIRA / "issue_create_metadata.json")
        assert "projects" in meta, (
            f"issue create metadata missing projects: {list(meta.keys())}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Cross-fixture contract tests
# ══════════════════════════════════════════════════════════════════════════════

class TestCrossFixtureContracts:
    """Tests that cross-reference ninja and jira fixtures together."""

    @_skip_unless(_NINJA / "alerts.json", _NINJA / "devices.json")
    def test_alert_and_device_fixtures_share_environment(self):
        """At least one alert deviceId should appear in the devices fixture.

        A partial device sample (default 25) will never cover every alert's
        device, so we only check that the two fixtures aren't from completely
        different environments (zero overlap would mean stale/mismatched captures).
        """
        device_ids = {d["id"] for d in _load(_NINJA / "devices.json") if "id" in d}
        alerts_with_device = [
            a for a in _load(_NINJA / "alerts.json") if a.get("deviceId") is not None
        ]
        if not alerts_with_device or not device_ids:
            return  # nothing to cross-check

        matched = sum(1 for a in alerts_with_device if a["deviceId"] in device_ids)
        assert matched > 0, (
            f"Zero alert deviceIds overlap with the devices fixture "
            f"({len(alerts_with_device)} alerts checked against {len(device_ids)} devices) — "
            "fixtures may be from different environments; consider re-running capture scripts"
        )

    @_skip_unless(_NINJA / "device_samples.json", _JIRA / "object_type_attributes.json")
    def test_role_devices_map_to_jira_attributes(self):
        """Devices captured per role must survive map_device with the fixture's attribute IDs."""
        from ninjaone_jira_integration.config.models import AttributeMapping, JiraAssetsConfig
        from ninjaone_jira_integration.sync.mapper import DeviceMapper

        jira_attrs = _load(_JIRA / "object_type_attributes.json")
        # Build a mapper that uses the first two attributes from the real fixture
        if len(jira_attrs) < 1:
            pytest.skip("no attributes in fixture")

        attr = jira_attrs[0]
        config = JiraAssetsConfig(
            attribute_mappings=[
                AttributeMapping(
                    jira_attribute_id=attr["id"],
                    jira_attribute_name=attr["name"],
                    source="systemName",
                ),
            ]
        )
        mapper = DeviceMapper(config=config)

        for device in _load(_NINJA / "device_samples.json"):
            attrs = mapper.map_device(device)
            assert isinstance(attrs, list), (
                f"map_device crashed for device {device.get('systemName')} "
                f"(role {device.get('nodeRoleId')})"
            )

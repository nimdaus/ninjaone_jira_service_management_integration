"""
Capture live NinjaOne API responses as test fixtures.

Run this script while connected to a real NinjaOne environment to produce
fixture files under tests/fixtures/ninja/. Those files are then used by
tests as response mocks so the test suite runs offline.

Usage:
    uv run python scripts/capture_ninja_fixtures.py
    uv run python scripts/capture_ninja_fixtures.py --config path/to/config.yaml
    uv run python scripts/capture_ninja_fixtures.py --output tests/fixtures/ninja
    uv run python scripts/capture_ninja_fixtures.py --devices 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
from ninjaone_jira_integration.config.loader import load_config


async def capture(config_path: str | None, output_dir: Path, max_devices: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading config...")
    cfg = load_config(config_path)

    if not cfg.ninjaone.base_url or not cfg.ninjaone.client_id:
        print("ERROR: NinjaOne credentials not configured (need base_url, client_id, client_secret).")
        sys.exit(1)

    client = NinjaOneClient(
        base_url=cfg.ninjaone.base_url,
        client_id=cfg.ninjaone.client_id,
        client_secret=cfg.ninjaone.client_secret,
    )

    saved: list[str] = []
    skipped: list[str] = []

    def save(name: str, data: object) -> None:
        path = output_dir / name
        path.write_text(json.dumps(data, indent=2, default=str))
        saved.append(name)
        print(f"  saved {path}")

    def skip(name: str, reason: str) -> None:
        skipped.append(name)
        print(f"  skipped {name}: {reason}")

    print("\nAuthenticating...")
    try:
        await client.authenticate()
    except Exception as e:
        print(f"ERROR: Authentication failed: {e}")
        sys.exit(1)

    # ── Roles ──────────────────────────────────────────────────────────────────
    print("\nCapturing roles...")
    try:
        save("roles.json", await client.get_roles())
    except Exception as e:
        skip("roles.json", str(e))

    # ── Organizations ──────────────────────────────────────────────────────────
    print("\nCapturing organizations...")
    try:
        save("organizations.json", await client.get_organizations())
    except Exception as e:
        skip("organizations.json", str(e))

    # ── Devices ────────────────────────────────────────────────────────────────
    print(f"\nCapturing up to {max_devices} devices...")
    devices: list[dict] = []
    try:
        async for device in client.get_devices_detailed(page_size=min(max_devices, 100)):
            devices.append(device)
            if len(devices) >= max_devices:
                break
        save("devices.json", devices)
    except Exception as e:
        skip("devices.json", str(e))

    # Three individually-fetched devices — prefer one per role for type diversity,
    # fall back to the first three from the list when roles aren't configured.
    SAMPLE_COUNT = 3
    if devices:
        # Try to source candidates from different roles first
        role_samples: list[dict] = []
        seen_roles: set[int | None] = set()
        for d in devices:
            role = d.get("nodeRoleId")
            if role not in seen_roles:
                seen_roles.add(role)
                role_samples.append(d)
            if len(role_samples) >= SAMPLE_COUNT:
                break
        # Pad with sequential devices if we didn't get enough distinct roles
        for d in devices:
            if len(role_samples) >= SAMPLE_COUNT:
                break
            if d not in role_samples:
                role_samples.append(d)

        candidate_ids = [d["id"] for d in role_samples if d.get("id")][:SAMPLE_COUNT]
        print(f"\nCapturing {len(candidate_ids)} individual devices (by ID) for structural validation...")
        fetched_devices: list[dict] = []
        for dev_id in candidate_ids:
            try:
                fetched_devices.append(await client.get_device(dev_id))
                print(f"  device {dev_id}: OK")
            except Exception as e:
                print(f"  device {dev_id}: {e}")
        if fetched_devices:
            save("device_samples.json", fetched_devices)
        else:
            skip("device_samples.json", "all individual fetches failed")

        # Device alerts from the first available device
        first_id = candidate_ids[0] if candidate_ids else None
        if first_id:
            print(f"\nCapturing device alerts for device {first_id}...")
            try:
                save("device_alerts.json", await client.get_device_alerts(first_id))
            except Exception as e:
                skip("device_alerts.json", str(e))
        else:
            skip("device_alerts.json", "no device id available")
    else:
        skip("device_samples.json", "no devices returned")
        skip("device_alerts.json", "no devices returned")

    # One device per configured role (for role-mapping coverage)
    roles_data: list[dict] = []
    try:
        roles_data = json.loads((output_dir / "roles.json").read_text())
    except Exception:
        pass

    configured_role_ids = {
        m.ninja_role_id for m in cfg.assets.object_type_mappings
    } if cfg.assets.object_type_mappings else set()

    if configured_role_ids and roles_data:
        print("\nCapturing one sample device per configured role...")
        samples: dict[str, dict] = {}
        for role_id in sorted(configured_role_ids):
            try:
                sample = await client.get_sample_device_by_role(role_id)
                if sample:
                    samples[str(role_id)] = sample
                    print(f"  role {role_id}: {sample.get('systemName', 'unnamed')}")
                else:
                    print(f"  role {role_id}: no devices found")
            except Exception as e:
                print(f"  role {role_id}: error — {e}")
        if samples:
            save("devices_by_role.json", samples)
        else:
            skip("devices_by_role.json", "no sample devices found for any configured role")
    else:
        skip("devices_by_role.json", "no object_type_mappings configured or no roles returned")

    # ── Alerts ─────────────────────────────────────────────────────────────────
    print("\nCapturing alerts...")
    alerts: list[dict] = []
    try:
        async for alert in client.get_alerts(page_size=20):
            alerts.append(alert)
            if len(alerts) >= 20:
                break
        save("alerts.json", alerts)
    except Exception as e:
        skip("alerts.json", str(e))

    # Three alert samples — prefer variety across severity levels
    if alerts:
        seen_severities: set[str] = set()
        alert_samples: list[dict] = []
        for a in alerts:
            sev = (a.get("severity") or "UNKNOWN").upper()
            if sev not in seen_severities:
                seen_severities.add(sev)
                alert_samples.append(a)
            if len(alert_samples) >= SAMPLE_COUNT:
                break
        for a in alerts:
            if len(alert_samples) >= SAMPLE_COUNT:
                break
            if a not in alert_samples:
                alert_samples.append(a)
        save("alert_samples.json", alert_samples[:SAMPLE_COUNT])
    else:
        skip("alert_samples.json", "no alerts returned")

    await client.close()

    print(f"\nDone. {len(saved)} fixtures saved to {output_dir}/")
    if skipped:
        print(f"Skipped ({len(skipped)}): {', '.join(skipped)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument(
        "--output",
        default="tests/fixtures/ninja",
        help="Output directory (default: tests/fixtures/ninja)",
    )
    parser.add_argument(
        "--devices",
        type=int,
        default=25,
        help="Max number of devices to capture (default: 25)",
    )
    args = parser.parse_args()

    asyncio.run(capture(args.config, Path(args.output), args.devices))

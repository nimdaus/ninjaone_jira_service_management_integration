"""
Capture live Jira API responses as test fixtures.

Run this script while connected to a real Jira environment to produce
fixture files under tests/fixtures/jira/. Those files are then used by
tests as response mocks so the test suite runs offline.

Usage:
    uv run python scripts/capture_jira_fixtures.py
    uv run python scripts/capture_jira_fixtures.py --config path/to/config.yaml
    uv run python scripts/capture_jira_fixtures.py --output tests/fixtures/jira
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure the project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
from ninjaone_jira_integration.config.loader import load_config


async def capture(config_path: str | None, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading config...")
    cfg = load_config(config_path)

    if not cfg.jira.is_configured():
        print("ERROR: Jira credentials not configured (need subdomain, email, api_token).")
        sys.exit(1)

    client = JiraAssetsClient(
        subdomain=cfg.jira.subdomain,
        email=cfg.jira.email,
        api_token=cfg.jira.api_token,
        workspace_id=cfg.jira.workspace_id or None,
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

    # ── Workspace ─────────────────────────────────────────────────────────────
    print("\nCapturing workspace...")
    try:
        workspace_id = await client.discover_workspace()
        # The raw response before discover_workspace unwraps it
        from ninjaone_jira_integration.clients.base import APIError  # noqa: F401
        import httpx, base64
        credentials = f"{cfg.jira.email}:{cfg.jira.api_token.get_secret_value()}"
        auth = "Basic " + base64.b64encode(credentials.encode()).decode()
        async with httpx.AsyncClient() as raw:
            r = await raw.get(
                f"https://{cfg.jira.subdomain}.atlassian.net/rest/servicedeskapi/assets/workspace",
                headers={"Authorization": auth},
            )
            save("workspace.json", r.json())
    except Exception as e:
        skip("workspace.json", str(e))

    # ── Current user ──────────────────────────────────────────────────────────
    print("\nCapturing current user...")
    try:
        save("myself.json", await client.get_myself())
    except Exception as e:
        skip("myself.json", str(e))

    # ── Schemas ───────────────────────────────────────────────────────────────
    print("\nCapturing schemas...")
    try:
        schemas = await client.list_schemas()
        save("schemas.json", schemas)
    except Exception as e:
        skip("schemas.json", str(e))

    # ── Object type ───────────────────────────────────────────────────────────
    object_type_id = _first_object_type_id(cfg)
    schema_id = cfg.assets.schema_id  # may be empty if using object_type_mappings

    if object_type_id:
        print(f"\nCapturing object type {object_type_id}...")
        object_type_data: dict | None = None
        try:
            object_type_data = await client.get_object_type(object_type_id)
            save("object_type.json", object_type_data)
            # Fall back to schema_id embedded in the object type response
            if not schema_id and object_type_data:
                schema_id = str(object_type_data.get("objectSchemaId", ""))
        except Exception as e:
            skip("object_type.json", str(e))
        try:
            attrs = await client.get_object_type_attributes(object_type_id)
            save("object_type_attributes.json", attrs)
        except Exception as e:
            skip("object_type_attributes.json", str(e))

        # ── Assets (search + samples) ──────────────────────────────────────────
        SAMPLE_COUNT = 3
        print(f"\nCapturing assets (search + {SAMPLE_COUNT} full fetches)...")
        try:
            # objectTypeId = N (no quotes) filters by ID; objectType = "name" filters by name
            aql = f"objectTypeId = {object_type_id}"
            results = await client.search_objects(aql, max_results=max(5, SAMPLE_COUNT))
            save("assets_search.json", results)
            if results:
                samples = results[:SAMPLE_COUNT]
                save("asset_samples.json", samples)
                full_samples: list[dict] = []
                for asset in samples:
                    try:
                        full_samples.append(await client.get_object(asset["id"]))
                    except Exception as e:
                        print(f"  asset {asset['id']} full fetch failed: {e}")
                if full_samples:
                    save("asset_full_samples.json", full_samples)
                else:
                    skip("asset_full_samples.json", "all full fetches failed")
            else:
                skip("asset_samples.json", "search returned no results")
                skip("asset_full_samples.json", "search returned no results")
        except Exception as e:
            skip("assets_search.json", str(e))
            skip("asset_samples.json", str(e))
    else:
        for name in ("object_type.json", "object_type_attributes.json",
                     "assets_search.json", "asset.json", "asset_full.json"):
            skip(name, "no object_type_id in config")

    # ── Schema (after object type, so we can fall back to its schemaId) ───────
    if schema_id:
        try:
            save("schema.json", await client.get_schema(schema_id))
        except Exception as e:
            skip("schema.json", str(e))
        try:
            object_types = await client.list_object_types(schema_id)
            save("object_types.json", object_types)
        except Exception as e:
            skip("object_types.json", str(e))
    else:
        skip("schema.json", "no schema_id in config or object type response")
        skip("object_types.json", "no schema_id in config or object type response")

    # ── Jira projects / issue types ───────────────────────────────────────────
    print("\nCapturing Jira projects...")
    try:
        projects = await client.get_projects()
        save("projects.json", projects)
    except Exception as e:
        skip("projects.json", str(e))

    project_key = cfg.issues.project_key if cfg.issues else None
    if project_key:
        try:
            save("issue_types.json", await client.get_issue_types(project_key))
        except Exception as e:
            skip("issue_types.json", str(e))
        try:
            meta = await client.get_issue_create_metadata(project_key)
            save("issue_create_metadata.json", meta)
        except Exception as e:
            skip("issue_create_metadata.json", str(e))
    else:
        skip("issue_types.json", "no project_key in config")
        skip("issue_create_metadata.json", "no project_key in config")

    await client.close()

    print(f"\nDone. {len(saved)} fixtures saved to {output_dir}/")
    if skipped:
        print(f"Skipped ({len(skipped)}): {', '.join(skipped)}")


def _first_object_type_id(cfg) -> str | None:
    """Return the first configured object type ID across all mapping strategies."""
    # New role-based mappings
    if cfg.assets.object_type_mappings:
        return cfg.assets.object_type_mappings[0].jira_object_type_id
    # Legacy flat config
    if cfg.assets.object_type_id:
        return cfg.assets.object_type_id
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument(
        "--output",
        default="tests/fixtures/jira",
        help="Output directory (default: tests/fixtures/jira)",
    )
    args = parser.parse_args()

    asyncio.run(capture(args.config, Path(args.output)))

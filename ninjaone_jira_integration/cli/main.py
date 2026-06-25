"""
Main CLI entry point.

Provides commands:
- init: Interactive configuration setup
- mapping-test: Test attribute mappings with sample device
- alert-test: Test alert-to-issue mapping with sample alert
- sync-all: Full sync of all devices
- sync-device: Sync a single device
- run-server: Start the HTTP server
- replay-dead-letter: Requeue dead-letter jobs
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from ninjaone_jira_integration import __version__
from ninjaone_jira_integration.config import AppConfig, load_config, save_config

console = Console()


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure logging.

    Uses structured logging (JSON to file, colorized text to console) when
    a log_file is specified; otherwise falls back to Rich console logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional path to write JSON log file.
    """
    from ninjaone_jira_integration.observability.logging import setup_structured_logging

    setup_structured_logging(
        level=level.upper(),
        json_format=bool(log_file),  # JSON only when writing to a file
        log_file=log_file,
    )

    # When not writing to a file, attach Rich handler for nicer console output
    if not log_file:
        root = logging.getLogger()
        root.handlers = []
        logging.basicConfig(
            level=level.upper(),
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(console=console, rich_tracebacks=True)],
        )


@click.group()
@click.version_option(version=__version__)
@click.option(
    "-c", "--config",
    "config_path",
    type=click.Path(exists=False),
    help="Path to configuration file",
)
@click.option(
    "-v", "--verbose",
    is_flag=True,
    help="Enable verbose output",
)
@click.option(
    "--log-file",
    "log_file",
    envvar="NINJA_JIRA_LOG_FILE",
    default=None,
    help="Write JSON logs to this file path (in addition to console)",
)
@click.pass_context
def cli(ctx: click.Context, config_path: str | None, verbose: bool, log_file: str | None) -> None:
    """NinjaOne to Jira Service Management integration CLI."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["verbose"] = verbose
    ctx.obj["log_file"] = log_file

    setup_logging("DEBUG" if verbose else "INFO", log_file=log_file)


@cli.command()
@click.option(
    "--ui",
    is_flag=True,
    help="Launch interactive configuration UI in browser",
)
@click.option(
    "--port",
    default=8080,
    type=int,
    help="Port for configuration UI server",
)
@click.option(
    "--write-secrets-to-config",
    is_flag=True,
    help="Write secrets to config file (not recommended for production)",
)
@click.pass_context
def init(ctx: click.Context, ui: bool, port: int, write_secrets_to_config: bool) -> None:
    """Initialize configuration interactively."""
    config_path = ctx.obj.get("config_path") or "config.yaml"
    
    if ui:
        from ninjaone_jira_integration.config.ui import run_config_ui
        
        console.print("[bold blue]Starting Configuration UI...[/bold blue]")
        console.print(f"Opening browser at http://127.0.0.1:{port}")
        console.print("The server will auto-shutdown after you save the configuration.")
        console.print()
        
        run_config_ui(config_path=config_path, port=port)
    else:
        asyncio.run(_init_interactive(ctx, write_secrets_to_config))


async def _init_interactive(ctx: click.Context, write_secrets: bool) -> None:
    """Interactive configuration initialization."""
    console.print("[bold blue]NinjaOne-Jira Integration Setup[/bold blue]")
    console.print()
    
    config_path = ctx.obj.get("config_path") or "config.yaml"
    
    # Check for existing config
    if Path(config_path).exists():
        if not click.confirm(f"Config file {config_path} exists. Overwrite?"):
            return
    
    console.print("[bold]Step 1: NinjaOne Configuration[/bold]")
    
    ninja_base_url = click.prompt(
        "NinjaOne API URL",
        default="https://app.ninjarmm.com",
    )
    ninja_client_id = click.prompt("NinjaOne Client ID")
    ninja_client_secret = click.prompt("NinjaOne Client Secret", hide_input=True)
    
    console.print()
    console.print("[bold]Step 2: Jira Configuration[/bold]")
    
    jira_subdomain = click.prompt("Jira subdomain (e.g., 'mycompany' for mycompany.atlassian.net)")
    jira_email = click.prompt("Jira account email")
    jira_api_token = click.prompt("Jira API token", hide_input=True)
    
    console.print()
    console.print("[bold]Testing connections...[/bold]")
    
    # Test NinjaOne connection
    from pydantic import SecretStr
    from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
    from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # Test NinjaOne
        task = progress.add_task("Testing NinjaOne connection...", total=None)
        
        try:
            ninja_client = NinjaOneClient(
                base_url=ninja_base_url,
                client_id=ninja_client_id,
                client_secret=SecretStr(ninja_client_secret),
            )
            await ninja_client.authenticate()
            await ninja_client.close()
            progress.update(task, description="[green]✓ NinjaOne connected[/green]")
        except Exception as e:
            progress.update(task, description=f"[red]✗ NinjaOne failed: {e}[/red]")
            console.print(f"[red]Error: {e}[/red]")
            if not click.confirm("Continue anyway?"):
                return
        
        # Test Jira
        task = progress.add_task("Testing Jira connection...", total=None)
        
        workspace_id = None
        try:
            jira_client = JiraAssetsClient(
                subdomain=jira_subdomain,
                email=jira_email,
                api_token=SecretStr(jira_api_token),
            )
            
            if await jira_client.test_connection():
                workspace_id = await jira_client.discover_workspace()
            
            await jira_client.close()
            progress.update(task, description=f"[green]✓ Jira connected (workspace: {workspace_id})[/green]")
        except Exception as e:
            progress.update(task, description=f"[red]✗ Jira failed: {e}[/red]")
            if not click.confirm("Continue anyway?"):
                return
    
    console.print()
    console.print("[bold]Step 3: Asset Configuration[/bold]")
    
    # We would normally list schemas and types here
    console.print("[yellow]Schema and type selection requires manual configuration.[/yellow]")
    console.print("Edit the config file to set object_schema_id and object_type_id.")
    
    # Build config
    config_data = {
        "ninjaone": {
            "base_url": ninja_base_url,
            "client_id": ninja_client_id,
        },
        "jira": {
            "subdomain": jira_subdomain,
            "email": jira_email,
            "workspace_id": workspace_id or "",
        },
        "assets": {
            "object_schema_id": "",
            "object_type_id": "",
        },
        "database": {
            "path": "data/integration.db",
        },
    }
    
    if write_secrets:
        config_data["ninjaone"]["client_secret"] = ninja_client_secret
        config_data["jira"]["api_token"] = jira_api_token
    else:
        console.print()
        console.print("[bold yellow]Important:[/bold yellow] Set these environment variables:")
        console.print(f"  NINJA_CLIENT_SECRET={ninja_client_secret[:4]}***")
        console.print(f"  JIRA_API_TOKEN={jira_api_token[:4]}***")
    
    # Save config
    save_config(AppConfig.model_validate(config_data), Path(config_path))
    console.print()
    console.print(f"[green]Configuration saved to {config_path}[/green]")
    console.print()
    console.print("Next steps:")
    console.print("1. Set environment variables for secrets")
    console.print("2. Configure asset mappings in the config file")
    console.print("3. Run 'mapping-test' to validate mappings")
    console.print("4. Run 'sync-all --dry-run' to preview changes")


async def _find_device_for_mapping_test(ninja_client: Any, config: Any) -> dict | None:
    """Return a device that matches a configured role mapping, or the first device found.

    Scans up to 50 devices. If role mappings are configured, prefers a device whose
    nodeRoleId has a matching mapping so the preview shows real attribute data.
    """
    configured_roles = {m.ninja_role_id for m in config.assets.object_type_mappings if m.enabled}
    first_device = None
    scanned = 0

    async for device in ninja_client.get_devices_detailed(page_size=50):
        if first_device is None:
            first_device = device
        if not configured_roles or device.get("nodeRoleId") in configured_roles:
            return device
        scanned += 1
        if scanned >= 50:
            break

    # No role match found — fall back to first device and warn
    if first_device and configured_roles:
        console.print(
            "[yellow]No device found matching a configured role in the first 50 results. "
            "Using first available device — pass --device-id to test a specific device.[/yellow]"
        )
        console.print()
    return first_device


@cli.command("mapping-test")
@click.option(
    "--device-id",
    type=int,
    help="Test with specific device ID",
)
@click.pass_context
def mapping_test(ctx: click.Context, device_id: int | None) -> None:
    """Test attribute mappings with a sample device."""
    asyncio.run(_mapping_test(ctx, device_id))


async def _mapping_test(ctx: click.Context, device_id: int | None) -> None:
    """Run mapping test."""
    config = load_config(ctx.obj.get("config_path"))

    has_mappings = config.assets.attribute_mappings or config.assets.has_role_mappings()
    if not has_mappings:
        console.print("[yellow]No attribute mappings configured.[/yellow]")
        console.print("Use 'ninja-jira init --ui' to configure mappings, or add them to config.yaml.")
        return
    
    from pydantic import SecretStr
    from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
    from ninjaone_jira_integration.sync.mapper import DeviceMapper
    
    console.print("[bold]Testing attribute mappings...[/bold]")
    console.print()
    
    # Initialize client
    ninja_client = NinjaOneClient(
        base_url=config.ninjaone.base_url,
        client_id=config.ninjaone.client_id,
        client_secret=config.ninjaone.client_secret,
    )
    
    try:
        await ninja_client.authenticate()
        
        # Get a sample device
        if device_id:
            device = await ninja_client.get_device(device_id)
        else:
            device = await _find_device_for_mapping_test(ninja_client, config)
            if device is None:
                console.print("[red]No devices found in NinjaOne[/red]")
                return

        role_id = device.get("nodeRoleId")
        role_mapping = config.assets.get_mapping_for_role(role_id) if role_id else None

        console.print(f"[bold]Device:[/bold] {device.get('systemName', 'Unknown')} (ID: {device.get('id')})")
        if role_mapping:
            console.print(
                f"[bold]Role mapping:[/bold] role {role_id} → "
                f"{role_mapping.ninja_role_name or role_id} → "
                f"Jira type {role_mapping.jira_object_type_id} ({role_mapping.jira_object_type_name or ''})"
            )
        elif config.assets.has_role_mappings():
            console.print(
                f"[yellow]Warning: device role {role_id} has no configured mapping — "
                f"no attributes will be synced for this device.[/yellow]"
            )
        console.print()

        # Test mappings
        mapper = DeviceMapper(config.assets)
        preview = mapper.get_mapped_preview(device)

        if not preview:
            console.print("[yellow]No attributes mapped for this device's role.[/yellow]")
            console.print(
                "Configure an object_type_mapping for role ID "
                f"{role_id} in config.yaml, or pass --device-id with a device "
                "whose role is already configured."
            )
        else:
            # Display results
            table = Table(title="Attribute Mapping Preview")
            table.add_column("Jira Attribute", style="cyan")
            table.add_column("Source", style="dim")
            table.add_column("Original Value")
            table.add_column("Mapped Value", style="green")
            table.add_column("Transformed", style="yellow")

            for item in preview:
                table.add_row(
                    item.attribute_name,
                    item.source_field,
                    str(item.original_value)[:50] if item.original_value else "-",
                    str(item.value)[:50] if item.value else "-",
                    "✓" if item.transformed else "",
                )

            console.print(table)

        # Validate mappings
        from ninjaone_jira_integration.config.validation import validate_all_mappings

        active_mappings = role_mapping.attribute_mappings if role_mapping else config.assets.attribute_mappings
        errors = validate_all_mappings(active_mappings, device)

        if errors:
            console.print()
            console.print("[bold red]Mapping Validation Errors:[/bold red]")
            for error in errors:
                console.print(f"  • {error.attribute_name}: {error.message}")
        elif preview:
            console.print()
            console.print("[green]✓ All mappings validated successfully[/green]")
        
    finally:
        await ninja_client.close()


@cli.command("alert-test")
@click.option(
    "--alert-uid",
    default=None,
    help="Test with a specific alert UID (uses first active alert if omitted)",
)
@click.pass_context
def alert_test(ctx: click.Context, alert_uid: str | None) -> None:
    """Test alert-to-issue mapping with a sample NinjaOne alert."""
    asyncio.run(_alert_test(ctx, alert_uid))


async def _alert_test(ctx: click.Context, alert_uid: str | None) -> None:
    from rich.panel import Panel
    from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
    from ninjaone_jira_integration.alerts.processor import build_alert_preview

    config = load_config(ctx.obj.get("config_path"))

    if not config.issues.project_key:
        console.print("[yellow]No alert-to-issue config found (issues.project_key is empty).[/yellow]")
        console.print("Use 'ninja-jira init --ui' to configure alert mapping, or edit config.yaml.")
        return

    ninja_client = NinjaOneClient(
        base_url=config.ninjaone.base_url,
        client_id=config.ninjaone.client_id,
        client_secret=config.ninjaone.client_secret,
    )

    try:
        await ninja_client.authenticate()

        alert: dict | None = None
        if alert_uid:
            alert = await ninja_client.get_alert(alert_uid)
        else:
            async for a in ninja_client.get_alerts(page_size=1):
                alert = a
                break

        if alert is None:
            console.print("[yellow]No active alerts found in NinjaOne.[/yellow]")
            console.print("Trigger a condition alert or pass --alert-uid to test with a specific alert.")
            return

        preview = build_alert_preview(alert, config.issues)

        # Alert details panel
        device_name = (
            alert.get("deviceName")
            or (alert.get("device") or {}).get("systemName", "Unknown")
        )
        alert_lines = [
            f"[bold]UID:[/bold]         {alert.get('uid', 'N/A')}",
            f"[bold]Severity:[/bold]    {alert.get('severity', 'N/A')}",
            f"[bold]Message:[/bold]     {alert.get('message', 'N/A')}",
            f"[bold]Source Type:[/bold] {alert.get('sourceType', 'N/A')}",
            f"[bold]Device:[/bold]      {device_name} (ID: {alert.get('deviceId', 'N/A')})",
        ]
        console.print(Panel("\n".join(alert_lines), title="Sample Alert", border_style="blue"))
        console.print()

        if preview["included"]:
            # Issue preview panel
            priority_label = preview["priority_id"] or "(Jira default)"
            labels_label = ", ".join(preview["labels"]) if preview["labels"] else "(none)"
            preview_lines = [
                f"[bold]Summary:[/bold]  {preview['summary']}",
                f"[bold]Priority:[/bold] {priority_label}",
                f"[bold]Labels:[/bold]   {labels_label}",
            ]
            console.print(Panel("\n".join(preview_lines), title="Issue Preview", border_style="green"))
            console.print()
            console.print("[green]✓ This alert WOULD create a Jira issue[/green]")
        else:
            console.print(Panel(
                f"Reason: {preview['filter_reason']}",
                title="Alert Filtered Out",
                border_style="yellow",
            ))
            console.print()
            console.print(f"[yellow]✗ This alert would be FILTERED OUT[/yellow]")

    finally:
        await ninja_client.close()


@cli.command("sync-all")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview changes without making them",
)
@click.pass_context
def sync_all(ctx: click.Context, dry_run: bool) -> None:
    """Sync all devices from NinjaOne to Jira Assets."""
    asyncio.run(_sync_all(ctx, dry_run))


async def _sync_all(ctx: click.Context, dry_run: bool) -> None:
    """Run full sync."""
    config = load_config(ctx.obj.get("config_path"))
    
    if dry_run:
        console.print("[bold yellow]DRY RUN MODE - No changes will be made[/bold yellow]")
        console.print()
    
    from pydantic import SecretStr
    from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
    from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
    from ninjaone_jira_integration.store.db import DatabaseManager
    from ninjaone_jira_integration.sync.engine import SyncEngine
    
    # Initialize clients
    ninja_client = NinjaOneClient(
        base_url=config.ninjaone.base_url,
        client_id=config.ninjaone.client_id,
        client_secret=config.ninjaone.client_secret,
    )
    
    jira_client = JiraAssetsClient(
        subdomain=config.jira.subdomain,
        email=config.jira.email,
        api_token=config.jira.api_token,
        workspace_id=config.jira.workspace_id,
    )
    
    async with DatabaseManager(config.database.path) as db:
        try:
            await ninja_client.authenticate()
            
            if not config.jira.workspace_id:
                await jira_client.discover_workspace()
            
            engine = SyncEngine(config, ninja_client, jira_client, db)
            
            console.print("[bold]Starting full sync...[/bold]")
            console.print()
            
            summary = await engine.sync_all(dry_run=dry_run)
            
            # Display results
            console.print()
            console.print("[bold]Sync Summary[/bold]")
            console.print(f"  Total devices:  {summary.total_devices}")
            console.print(f"  Created:        [green]{summary.created}[/green]")
            console.print(f"  Updated:        [blue]{summary.updated}[/blue]")
            console.print(f"  Skipped:        [dim]{summary.skipped}[/dim]")
            console.print(f"  Failed:         [red]{summary.failed}[/red]")
            console.print(f"  Success rate:   {summary.success_rate:.1f}%")
            
            if summary.errors:
                console.print()
                console.print("[bold red]Errors:[/bold red]")
                for device_id, error in summary.errors[:10]:
                    console.print(f"  Device {device_id}: {error}")
                if len(summary.errors) > 10:
                    console.print(f"  ... and {len(summary.errors) - 10} more")
                    
        finally:
            await ninja_client.close()
            await jira_client.close()


@cli.command("sync-device")
@click.argument("device_id", type=int)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview changes without making them",
)
@click.pass_context
def sync_device(ctx: click.Context, device_id: int, dry_run: bool) -> None:
    """Sync a single device by ID."""
    asyncio.run(_sync_device(ctx, device_id, dry_run))


async def _sync_device(ctx: click.Context, device_id: int, dry_run: bool) -> None:
    """Sync a single device."""
    config = load_config(ctx.obj.get("config_path"))
    
    from pydantic import SecretStr
    from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
    from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
    from ninjaone_jira_integration.store.db import DatabaseManager
    from ninjaone_jira_integration.sync.engine import SyncEngine
    
    ninja_client = NinjaOneClient(
        base_url=config.ninjaone.base_url,
        client_id=config.ninjaone.client_id,
        client_secret=config.ninjaone.client_secret,
    )
    
    jira_client = JiraAssetsClient(
        subdomain=config.jira.subdomain,
        email=config.jira.email,
        api_token=config.jira.api_token,
        workspace_id=config.jira.workspace_id,
    )
    
    async with DatabaseManager(config.database.path) as db:
        try:
            await ninja_client.authenticate()
            
            if not config.jira.workspace_id:
                await jira_client.discover_workspace()
            
            engine = SyncEngine(config, ninja_client, jira_client, db)
            
            console.print(f"[bold]Syncing device {device_id}...[/bold]")
            
            result = await engine.sync_device(device_id, dry_run=dry_run)
            
            console.print()
            console.print(f"Action:  [bold]{result.action.value}[/bold]")
            console.print(f"Device:  {result.device_name or 'Unknown'}")
            
            if result.jira_asset_key:
                console.print(f"Asset:   {result.jira_asset_key}")
            
            if result.changes:
                console.print("Changes:")
                for change in result.changes:
                    console.print(f"  - {change}")
            
            if result.error:
                console.print(f"[red]Error: {result.error}[/red]")
                
        finally:
            await ninja_client.close()
            await jira_client.close()


@cli.command("run")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview changes without making them",
)
@click.option(
    "--once",
    is_flag=True,
    help="Run a single sync then exit (useful for cron jobs)",
)
@click.pass_context
def run(ctx: click.Context, dry_run: bool, once: bool) -> None:
    """Start scheduled device sync and alert polling (no public-facing server required).

    Runs two independent schedulers:
    - Device sync: every schedule.interval_hours (default 6h)
    - Alert polling: every alert_schedule.interval_minutes (default 5m)

    Use --once to run both immediately and exit (useful for cron jobs).
    Use --dry-run to preview changes without writing to Jira.
    """
    asyncio.run(_run(ctx, dry_run, once))


async def _run(ctx: click.Context, dry_run: bool, once: bool) -> None:
    """Run the scheduled sync and alert polling."""
    config = load_config(ctx.obj.get("config_path"))

    from ninjaone_jira_integration.alerts.scheduler import AlertScheduler
    from ninjaone_jira_integration.store.db import DatabaseManager
    from ninjaone_jira_integration.sync.scheduler import SyncScheduler

    if dry_run:
        console.print("[bold yellow]DRY RUN MODE - No changes will be made[/bold yellow]")
        console.print()

    if once:
        console.print("[bold]Running single device sync and alert poll...[/bold]")
    else:
        console.print(
            f"[bold]Starting scheduled sync (devices every {config.schedule.interval_hours:.1f}h,"
            f" alerts every {config.alert_schedule.interval_minutes:.1f}m).[/bold]"
        )
        console.print("Press Ctrl+C to stop.")
        console.print()

    async with DatabaseManager(config.database.path) as db:
        sync_scheduler = SyncScheduler(config, db)
        alert_scheduler = AlertScheduler(config, db)

        if once:
            await sync_scheduler.run_once(dry_run=dry_run)
            await alert_scheduler.run_once(dry_run=dry_run)
            console.print("[green]Sync and alert poll complete.[/green]")
        else:
            await sync_scheduler.start()
            await alert_scheduler.start()

            if config.heartbeat.url:
                from ninjaone_jira_integration.notifications import OutboundNotifier

                _notifier = OutboundNotifier(config.heartbeat)

                async def _heartbeat_loop() -> None:
                    while True:
                        await _notifier.send_heartbeat()
                        await asyncio.sleep(config.heartbeat.interval_seconds)

                asyncio.create_task(_heartbeat_loop())
                logger.info(
                    "Heartbeat enabled: posting to %s every %ds",
                    config.heartbeat.url,
                    config.heartbeat.interval_seconds,
                )

            try:
                # Block until interrupted
                await asyncio.Event().wait()
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            finally:
                await alert_scheduler.stop()
                await sync_scheduler.stop()


@cli.command("run-server")
@click.option(
    "--host",
    default="0.0.0.0",
    help="Host to bind to",
)
@click.option(
    "--port",
    default=8080,
    type=int,
    help="Port to bind to",
)
@click.pass_context
def run_server(ctx: click.Context, host: str, port: int) -> None:
    """Start the HTTP server for webhooks."""
    config = load_config(ctx.obj.get("config_path"))
    
    console.print(f"[bold]Starting server on {host}:{port}[/bold]")
    console.print("Press Ctrl+C to stop")
    console.print()
    
    import uvicorn
    from ninjaone_jira_integration.server.app import create_app
    
    app = create_app(config)
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )


@cli.command("replay-dead-letter")
@click.option(
    "--job-id",
    type=int,
    help="Replay specific job by ID",
)
@click.option(
    "--limit",
    type=int,
    default=100,
    help="Maximum jobs to replay",
)
@click.pass_context
def replay_dead_letter(ctx: click.Context, job_id: int | None, limit: int) -> None:
    """Requeue dead-letter jobs for retry."""
    asyncio.run(_replay_dead_letter(ctx, job_id, limit))


async def _replay_dead_letter(ctx: click.Context, job_id: int | None, limit: int) -> None:
    """Replay dead-letter jobs."""
    config = load_config(ctx.obj.get("config_path"))
    
    from ninjaone_jira_integration.store.db import DatabaseManager
    from ninjaone_jira_integration.store.jobs import JobStore
    
    async with DatabaseManager(config.database.path) as db:
        job_store = JobStore(db)
        
        # Show current dead-letter jobs
        dead_letters = await job_store.get_dead_letter_jobs(limit=limit)
        
        if not dead_letters:
            console.print("[green]No dead-letter jobs found.[/green]")
            return
        
        console.print(f"[bold]Dead-letter jobs: {len(dead_letters)}[/bold]")
        console.print()
        
        table = Table()
        table.add_column("ID")
        table.add_column("Type")
        table.add_column("Key")
        table.add_column("Last Error")
        table.add_column("Updated")
        
        for job in dead_letters:
            table.add_row(
                str(job.id),
                job.job_type.value,
                job.job_key,
                (job.last_error or "")[:50],
                str(job.updated_at)[:19] if job.updated_at else "",
            )
        
        console.print(table)
        console.print()
        
        if job_id:
            if click.confirm(f"Replay job {job_id}?"):
                count = await job_store.replay_dead_letter(job_id=job_id)
                console.print(f"[green]Replayed {count} job(s)[/green]")
        else:
            if click.confirm(f"Replay all {len(dead_letters)} dead-letter jobs?"):
                count = await job_store.replay_dead_letter(limit=limit)
                console.print(f"[green]Replayed {count} job(s)[/green]")


@cli.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show integration status and statistics."""
    asyncio.run(_status(ctx))


async def _status(ctx: click.Context) -> None:
    """Show status."""
    config = load_config(ctx.obj.get("config_path"))
    
    from ninjaone_jira_integration.store.db import DatabaseManager
    from ninjaone_jira_integration.store.jobs import JobStore
    from ninjaone_jira_integration.store.mappings import MappingStore
    
    async with DatabaseManager(config.database.path) as db:
        job_store = JobStore(db)
        mapping_store = MappingStore(db)
        
        job_stats = await job_store.get_stats()
        device_count = await mapping_store.count_device_mappings()
        alert_count = await mapping_store.count_alert_mappings()
        
        console.print("[bold]Integration Status[/bold]")
        console.print()
        
        console.print("[bold cyan]Mappings[/bold cyan]")
        console.print(f"  Devices synced: {device_count}")
        console.print(f"  Alerts processed: {alert_count}")
        console.print()
        
        console.print("[bold cyan]Job Queue[/bold cyan]")
        console.print(f"  Queued: {job_stats.queued}")
        console.print(f"  Processing: {job_stats.processing}")
        console.print(f"  Completed: {job_stats.completed}")
        console.print(f"  Failed: {job_stats.failed}")
        console.print(f"  Dead-letter: {job_stats.dead_letter}")


if __name__ == "__main__":
    cli()

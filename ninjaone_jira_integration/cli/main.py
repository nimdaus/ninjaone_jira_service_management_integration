"""
Main CLI entry point.

Provides commands:
- init: Interactive configuration setup
- mapping-test: Test attribute mappings with sample device
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


def setup_logging(level: str = "INFO") -> None:
    """Configure logging with rich handler.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
    """
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
@click.pass_context
def cli(ctx: click.Context, config_path: str | None, verbose: bool) -> None:
    """NinjaOne to Jira Service Management integration CLI."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["verbose"] = verbose
    
    setup_logging("DEBUG" if verbose else "INFO")


@cli.command()
@click.option(
    "--ui",
    is_flag=True,
    help="Launch interactive configuration UI in browser",
)
@click.option(
    "--port",
    default=5000,
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
    save_config(config_data, config_path)
    console.print()
    console.print(f"[green]Configuration saved to {config_path}[/green]")
    console.print()
    console.print("Next steps:")
    console.print("1. Set environment variables for secrets")
    console.print("2. Configure asset mappings in the config file")
    console.print("3. Run 'mapping-test' to validate mappings")
    console.print("4. Run 'sync-all --dry-run' to preview changes")


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
    
    if not config.assets.attribute_mappings:
        console.print("[yellow]No attribute mappings configured.[/yellow]")
        console.print("Add mappings to the 'assets.attribute_mappings' section of your config.")
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
            # Get first device
            async for device in ninja_client.get_devices_detailed(page_size=1):
                break
            else:
                console.print("[red]No devices found in NinjaOne[/red]")
                return
        
        console.print(f"[bold]Sample Device: {device.get('systemName', 'Unknown')} (ID: {device.get('id')})[/bold]")
        console.print()
        
        # Test mappings
        mapper = DeviceMapper(config.assets)
        preview = mapper.get_mapped_preview(device)
        
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
        
        errors = validate_all_mappings(config.assets.attribute_mappings, device)
        
        if errors:
            console.print()
            console.print("[bold red]Mapping Validation Errors:[/bold red]")
            for error in errors:
                console.print(f"  • {error.attribute_name}: {error.message}")
        else:
            console.print()
            console.print("[green]✓ All mappings validated successfully[/green]")
        
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

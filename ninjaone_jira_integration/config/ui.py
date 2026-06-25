"""
Configuration Web UI server.

Provides a browser-based interface for configuring the integration.
Runs on localhost only with auto-shutdown after save or timeout.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, SecretStr

from ninjaone_jira_integration.config import AppConfig, load_config, save_config
from ninjaone_jira_integration.config.models import AttributeMapping

logger = logging.getLogger(__name__)

# Directory containing static files
STATIC_DIR = Path(__file__).parent / "static"


class CredentialsRequest(BaseModel):
    """Request model for testing credentials."""
    
    # NinjaOne
    ninja_base_url: str | None = None
    ninja_client_id: str | None = None
    ninja_client_secret: str | None = None
    
    # Jira
    jira_subdomain: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None


class ConfigSaveRequest(BaseModel):
    """Request model for saving configuration."""
    
    config: dict[str, Any]
    write_secrets: bool = False


class MappingTestRequest(BaseModel):
    """Request model for testing mappings."""
    
    mappings: list[dict[str, Any]]
    device_id: int | None = None


class CreateAttributeRequest(BaseModel):
    object_type_id: str
    name: str
    description: str = ""



class ConfigUIServer:
    """Configuration UI server.
    
    Runs a FastAPI server on localhost for web-based configuration.
    Auto-shuts down after save or configurable timeout.
    """
    
    def __init__(
        self,
        config_path: str | Path | None = None,
        host: str = "127.0.0.1",
        port: int = 5000,
        timeout_minutes: int = 30,
        open_browser: bool = True,
    ):
        """Initialize UI server.
        
        Args:
            config_path: Path to save configuration.
            host: Host to bind (default localhost only).
            port: Port to bind.
            timeout_minutes: Auto-shutdown timeout.
            open_browser: Whether to open browser on start.
        """
        self.config_path = Path(config_path) if config_path else Path("config.yaml")
        self.host = host
        self.port = port
        self.timeout_minutes = timeout_minutes
        self.open_browser = open_browser
        
        self._shutdown_event = asyncio.Event()
        self._config: AppConfig | None = None
        
        # API clients for testing (created on demand)
        self._ninja_client = None
        self._jira_client = None
        
        self.app = self._create_app()
    
    def _create_app(self) -> FastAPI:
        """Create FastAPI application."""
        app = FastAPI(
            title="NinjaOne-Jira Integration Setup",
            docs_url=None,
            redoc_url=None,
        )
        
        # Mount static files
        if STATIC_DIR.exists():
            app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
        
        @app.on_event("startup")
        async def on_startup():
            # Load existing config if present
            try:
                if self.config_path.exists():
                    self._config = load_config(self.config_path)
                else:
                    self._config = AppConfig()
            except Exception as e:
                logger.warning("Failed to load existing config: %s", e)
                self._config = AppConfig()
            
            # Start timeout timer
            asyncio.create_task(self._timeout_shutdown())
            
            # Open browser
            if self.open_browser:
                url = f"http://{self.host}:{self.port}"
                logger.info("Opening browser: %s", url)
                webbrowser.open(url)
        
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse
        
        @app.exception_handler(RequestValidationError)
        async def validation_exception_handler(request: Request, exc: RequestValidationError):
            logger.error(f"Validation error: {exc.errors()} body: {await request.body()}")
            return JSONResponse(
                status_code=422,
                content={"detail": exc.errors(), "body": str(exc.body)},
            )
        
        @app.get("/", response_class=HTMLResponse)
        async def index():
            """Serve main HTML page."""
            index_path = STATIC_DIR / "index.html"
            if index_path.exists():
                return FileResponse(index_path, media_type="text/html")
            else:
                # Inline fallback if static files not found
                return HTMLResponse(self._get_inline_html())
        
        @app.get("/api/config")
        async def get_config():
            """Get current configuration (secrets redacted)."""
            config_dict = self._config.model_dump()
            
            # Redact secrets
            if "ninjaone" in config_dict and "client_secret" in config_dict["ninjaone"]:
                config_dict["ninjaone"]["client_secret"] = "***"
            if "jira" in config_dict and "api_token" in config_dict["jira"]:
                config_dict["jira"]["api_token"] = "***"
            if "server" in config_dict and "webhook" in config_dict["server"]:
                if "secret" in config_dict["server"]["webhook"]:
                    config_dict["server"]["webhook"]["secret"] = "***"
            
            return config_dict
        
        @app.get("/api/config/secrets")
        async def get_secrets():
            """Get secrets from environment or config for form pre-fill.
            
            This allows users to not have to re-enter credentials when
            they already exist in .env or config file.
            """
            secrets = {}
            
            # Try to get NinjaOne secrets
            ninja_secret = os.environ.get("NINJA_CLIENT_SECRET", "")
            if not ninja_secret and self._config.ninjaone.client_secret:
                ninja_secret = self._config.ninjaone.client_secret.get_secret_value()
            secrets["ninja_client_secret"] = ninja_secret
            
            ninja_id = os.environ.get("NINJA_CLIENT_ID", "")
            if not ninja_id and self._config.ninjaone.client_id:
                ninja_id = self._config.ninjaone.client_id
            secrets["ninja_client_id"] = ninja_id
            
            ninja_region = os.environ.get("NINJA_REGION", "")
            if not ninja_region and self._config.ninjaone.base_url:
                ninja_region = self._config.ninjaone.base_url
            secrets["ninja_base_url"] = ninja_region
            
            # Try to get Jira secrets
            jira_token = os.environ.get("JIRA_API_TOKEN", "")
            if not jira_token and self._config.jira.api_token:
                jira_token = self._config.jira.api_token.get_secret_value()
            secrets["jira_api_token"] = jira_token
            
            jira_email = os.environ.get("JIRA_EMAIL", "")
            if not jira_email and self._config.jira.email:
                jira_email = self._config.jira.email
            secrets["jira_email"] = jira_email
            
            jira_subdomain = os.environ.get("JIRA_SUBDOMAIN", "")
            if not jira_subdomain and self._config.jira.subdomain:
                jira_subdomain = self._config.jira.subdomain
            secrets["jira_subdomain"] = jira_subdomain
            
            return secrets
        
        @app.post("/api/config")
        async def save_config_endpoint(request: ConfigSaveRequest):
            """Save configuration."""
            try:
                # Build AppConfig from dict
                config_obj = AppConfig.model_validate(request.config)
                save_config(config_obj, self.config_path, write_secrets=request.write_secrets)
                logger.info("Configuration saved to %s", self.config_path)
                
                # Schedule shutdown
                asyncio.create_task(self._delayed_shutdown(2.0))
                
                return {"status": "saved", "path": str(self.config_path)}
            except Exception as e:
                logger.error("Failed to save config: %s", e)
                raise HTTPException(status_code=500, detail=str(e))
        
        @app.post("/api/test/ninjaone")
        async def test_ninjaone(creds: CredentialsRequest):
            """Test NinjaOne connection."""
            from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
            
            try:
                client = NinjaOneClient(
                    base_url=creds.ninja_base_url or "https://app.ninjarmm.com",
                    client_id=creds.ninja_client_id or "",
                    client_secret=SecretStr(creds.ninja_client_secret or ""),
                )
                await client.authenticate()
                
                # Get some basic info
                device_count = 0
                async for _ in client.get_devices_detailed(page_size=1):
                    device_count += 1
                    break
                
                await client.close()
                
                return {
                    "status": "success",
                    "message": "Connected successfully",
                    "has_devices": device_count > 0,
                }
            except Exception as e:
                return {
                    "status": "error",
                    "message": str(e),
                }
        
        @app.post("/api/test/jira")
        async def test_jira(creds: CredentialsRequest):
            """Test Jira connection."""
            from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
            
            try:
                client = JiraAssetsClient(
                    subdomain=creds.jira_subdomain or "",
                    email=creds.jira_email or "",
                    api_token=SecretStr(creds.jira_api_token or ""),
                )
                
                connected = await client.test_connection()
                workspace_id = None
                
                if connected:
                    workspace_id = await client.discover_workspace()
                
                await client.close()
                
                return {
                    "status": "success" if connected else "error",
                    "message": "Connected successfully" if connected else "Connection failed",
                    "workspace_id": workspace_id,
                }
            except Exception as e:
                return {
                    "status": "error",
                    "message": str(e),
                }
        
        @app.get("/api/jira/schemas")
        async def get_jira_schemas(
            subdomain: str,
            email: str,
            api_token: str,
        ):
            """Get Jira Assets schemas."""
            from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
            
            try:
                client = JiraAssetsClient(
                    subdomain=subdomain,
                    email=email,
                    api_token=SecretStr(api_token),
                )
                
                await client.discover_workspace()
                schemas = await client.list_schemas()
                await client.close()
                
                return {"schemas": schemas}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @app.get("/api/jira/types/{schema_id}")
        async def get_jira_types(
            schema_id: str,
            subdomain: str,
            email: str,
            api_token: str,
        ):
            """Get object types in a schema."""
            from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
            
            try:
                client = JiraAssetsClient(
                    subdomain=subdomain,
                    email=email,
                    api_token=SecretStr(api_token),
                )
                
                await client.discover_workspace()
                types = await client.list_object_types(schema_id)
                await client.close()
                
                return {"types": types}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @app.get("/api/jira/attributes/{type_id}")
        async def get_jira_attributes(
            type_id: str,
            subdomain: str,
            email: str,
            api_token: str,
        ):
            """Get attributes for an object type."""
            from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
            
            try:
                client = JiraAssetsClient(
                    subdomain=subdomain,
                    email=email,
                    api_token=SecretStr(api_token),
                )
                
                await client.discover_workspace()
                attributes = await client.get_object_type_attributes(type_id)
                await client.close()
                
                return {"attributes": attributes}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @app.get("/api/ninjaone/roles")
        async def get_ninjaone_roles(
            base_url: str,
            client_id: str,
            client_secret: str,
        ):
            """Get all device roles from NinjaOne."""
            from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
            
            try:
                client = NinjaOneClient(
                    base_url=base_url,
                    client_id=client_id,
                    client_secret=SecretStr(client_secret),
                )
                await client.authenticate()
                
                roles = await client.get_roles()
                await client.close()
                
                return {"roles": roles}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @app.get("/api/ninjaone/sample-device")
        async def get_sample_device(
            base_url: str,
            client_id: str,
            client_secret: str,
            role_id: int | None = None,
        ):
            """Get a sample device from NinjaOne, optionally filtered by role."""
            from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
            
            try:
                client = NinjaOneClient(
                    base_url=base_url,
                    client_id=client_id,
                    client_secret=SecretStr(client_secret),
                )
                await client.authenticate()
                
                device = None
                if role_id is not None:
                    # Get sample device with specific role
                    device = await client.get_sample_device_by_role(role_id)
                else:
                    # Get any device
                    async for d in client.get_devices_detailed(page_size=1):
                        device = d
                        break
                
                await client.close()
                
                if device:
                    return {"device": device}
                else:
                    return {"device": None, "message": "No devices found"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        
        @app.post("/api/mapping/test")
        async def test_mapping(request: MappingTestRequest):
            """Test mappings against a device."""
            from ninjaone_jira_integration.config.models import JiraAssetsConfig
            from ninjaone_jira_integration.sync.mapper import DeviceMapper
            
            try:
                # Build config from mappings
                mappings = [AttributeMapping(**m) for m in request.mappings]
                config = JiraAssetsConfig(attribute_mappings=mappings)
                mapper = DeviceMapper(config)
                
                # We need a device to test against
                # For now, return placeholder
                return {
                    "status": "success",
                    "message": "Mappings are valid",
                    "mapping_count": len(mappings),
                }
            except Exception as e:
                return {
                    "status": "error",
                    "message": str(e),
                }
        
        
        @app.post("/api/jira/attributes/create")
        async def create_attribute(
            request: CreateAttributeRequest = Body(...),
            subdomain: str = Query(...),
            email: str = Query(...),
            api_token: str = Query(...),
        ):
            """Create a new attribute on a Jira object type."""
            from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
            from ninjaone_jira_integration.clients.base import APIError
            
            client = None
            try:
                logger.info(
                    "Creating attribute '%s' on object type %s",
                    request.name,
                    request.object_type_id,
                )
                
                client = JiraAssetsClient(
                    subdomain=subdomain,
                    email=email,
                    api_token=SecretStr(api_token),
                )
                
                # Discover workspace first
                await client.discover_workspace()
                
                # Create the attribute
                result = await client.create_object_type_attribute(
                    object_type_id=request.object_type_id,
                    name=request.name,
                    attribute_type="Default",
                    description=request.description,
                )
                
                logger.info("Attribute created successfully: %s", result)
                
                return {
                    "id": result.get("id"),
                    "name": result.get("name"),
                    "status": "created",
                }
            except APIError as e:
                error_msg = f"Jira API error: {e}"
                if hasattr(e, 'response_body') and e.response_body:
                    error_msg += f" - {e.response_body}"
                logger.error(error_msg)
                raise HTTPException(status_code=e.status_code or 500, detail=error_msg)
            except Exception as e:
                error_msg = f"Failed to create attribute: {type(e).__name__}: {e}"
                logger.error(error_msg)
                raise HTTPException(status_code=500, detail=error_msg)
            finally:
                if client:
                    await client.close()

        @app.get("/api/jira/issue-fields")
        async def get_jira_issue_fields(
            subdomain: str,
            email: str,
            api_token: str,
            project_key: str,
            issue_type_id: str,
        ):
            """Get available fields for creating issues in a project/issue-type combination."""
            from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient

            # Fields the user cannot set during issue creation
            _NON_SETTABLE = frozenset({
                "project", "issuetype", "reporter", "created", "updated",
                "creator", "votes", "watches", "worklog", "comment",
                "attachment", "subtasks", "issuelinks", "parent",
            })

            try:
                client = JiraAssetsClient(
                    subdomain=subdomain,
                    email=email,
                    api_token=SecretStr(api_token),
                )
                meta = await client.get_issue_create_metadata(project_key, issue_type_id)
                await client.close()

                fields: list[dict] = []
                projects = meta.get("projects") or []
                if projects:
                    issue_types = projects[0].get("issuetypes") or []
                    if issue_types:
                        raw_fields = issue_types[0].get("fields") or {}
                        for field_id, field_info in raw_fields.items():
                            if field_id in _NON_SETTABLE:
                                continue
                            schema = field_info.get("schema") or {}
                            raw_allowed = field_info.get("allowedValues") or []
                            allowed_values = []
                            for av in raw_allowed:
                                label = av.get("value") or av.get("name") or ""
                                if label:
                                    allowed_values.append(label)
                            fields.append({
                                "id": field_id,
                                "name": field_info.get("name", field_id),
                                "schema_type": schema.get("type", ""),
                                "allowed_values": allowed_values,
                            })
                fields.sort(key=lambda f: f["name"])
                return {"fields": fields}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/jira/project-statuses")
        async def get_jira_project_statuses(
            subdomain: str,
            email: str,
            api_token: str,
            project_key: str,
            issue_type_id: str = "",
        ):
            """Get workflow statuses for a Jira project (optionally filtered to one issue type)."""
            from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient

            try:
                client = JiraAssetsClient(
                    subdomain=subdomain,
                    email=email,
                    api_token=SecretStr(api_token),
                )
                statuses = await client.get_project_statuses(
                    project_key, issue_type_id or None
                )
                await client.close()
                normalized = [
                    {"id": s.get("id", ""), "name": s.get("name", "")}
                    for s in statuses
                ]
                return {"statuses": normalized}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/jira/issue-transitions")
        async def get_jira_issue_transitions(
            subdomain: str,
            email: str,
            api_token: str,
            issue_key: str,
        ):
            """Get available workflow transitions for a specific Jira issue."""
            from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient

            try:
                client = JiraAssetsClient(
                    subdomain=subdomain,
                    email=email,
                    api_token=SecretStr(api_token),
                )
                transitions = await client.get_transitions(issue_key)
                await client.close()

                normalized = [
                    {
                        "id": t.get("id", ""),
                        "name": t.get("name", ""),
                        "to_status": (t.get("to") or {}).get("name", ""),
                    }
                    for t in transitions
                ]
                return {"transitions": normalized}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/jira/projects")
        async def get_jira_projects(subdomain: str, email: str, api_token: str):
            """Get all Jira projects accessible with these credentials."""
            from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient

            try:
                client = JiraAssetsClient(
                    subdomain=subdomain,
                    email=email,
                    api_token=SecretStr(api_token),
                )
                projects = await client.get_projects()
                await client.close()
                normalized = [
                    {"id": p.get("id", ""), "key": p.get("key", ""), "name": p.get("name", "")}
                    for p in projects
                ]
                return {"projects": normalized}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/jira/issue-types")
        async def get_jira_issue_types(subdomain: str, email: str, api_token: str, project_key: str):
            """Get issue types for a Jira project."""
            from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient

            try:
                client = JiraAssetsClient(
                    subdomain=subdomain,
                    email=email,
                    api_token=SecretStr(api_token),
                )
                issue_types = await client.get_issue_types(project_key)
                await client.close()
                normalized = [
                    {"id": it.get("id", ""), "name": it.get("name", ""), "description": it.get("description", "")}
                    for it in issue_types
                ]
                return {"issue_types": normalized}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/shutdown")
        async def shutdown():
            """Graceful shutdown."""
            asyncio.create_task(self._delayed_shutdown(1.0))
            return {"status": "shutting_down"}
        
        return app
    
    async def _timeout_shutdown(self) -> None:
        """Auto-shutdown after timeout."""
        try:
            await asyncio.wait_for(
                self._shutdown_event.wait(),
                timeout=self.timeout_minutes * 60,
            )
        except asyncio.TimeoutError:
            logger.info("UI server timeout, shutting down")
            os.kill(os.getpid(), signal.SIGTERM)
    
    async def _delayed_shutdown(self, delay: float) -> None:
        """Shutdown after a delay."""
        await asyncio.sleep(delay)
        logger.info("Shutting down UI server")
        os.kill(os.getpid(), signal.SIGTERM)
    
    def _get_inline_html(self) -> str:
        """Get inline HTML if static files not available."""
        return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NinjaOne-Jira Integration Setup</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: system-ui, -apple-system, sans-serif; 
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #e8e8e8;
            min-height: 100vh;
            padding: 2rem;
        }
        .container { max-width: 900px; margin: 0 auto; }
        h1 { font-size: 2rem; margin-bottom: 2rem; color: #fff; }
        .card {
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            border: 1px solid rgba(255,255,255,0.1);
        }
        .card h2 { font-size: 1.25rem; margin-bottom: 1rem; color: #4fc3f7; }
        .form-group { margin-bottom: 1rem; }
        label { display: block; margin-bottom: 0.5rem; color: #aaa; font-size: 0.875rem; }
        input, select {
            width: 100%;
            padding: 0.75rem;
            background: rgba(0,0,0,0.3);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 6px;
            color: #fff;
            font-size: 1rem;
        }
        input:focus, select:focus { outline: none; border-color: #4fc3f7; }
        .btn {
            padding: 0.75rem 1.5rem;
            border: none;
            border-radius: 6px;
            font-size: 1rem;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-primary { background: #4fc3f7; color: #000; }
        .btn-primary:hover { background: #81d4fa; }
        .btn-secondary { background: rgba(255,255,255,0.1); color: #fff; }
        .btn-secondary:hover { background: rgba(255,255,255,0.2); }
        .btn-success { background: #66bb6a; color: #000; }
        .status { margin-top: 1rem; padding: 0.75rem; border-radius: 6px; }
        .status.success { background: rgba(102,187,106,0.2); color: #81c784; }
        .status.error { background: rgba(244,67,54,0.2); color: #ef5350; }
        .row { display: flex; gap: 1rem; }
        .row > * { flex: 1; }
        .actions { display: flex; gap: 1rem; justify-content: flex-end; margin-top: 2rem; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔧 NinjaOne-Jira Integration Setup</h1>
        
        <div class="card">
            <h2>NinjaOne Credentials</h2>
            <div class="form-group">
                <label>API Base URL</label>
                <input type="text" id="ninja-url" value="https://app.ninjarmm.com" placeholder="https://app.ninjarmm.com">
            </div>
            <div class="row">
                <div class="form-group">
                    <label>Client ID</label>
                    <input type="text" id="ninja-client-id" placeholder="OAuth2 Client ID">
                </div>
                <div class="form-group">
                    <label>Client Secret</label>
                    <input type="password" id="ninja-client-secret" placeholder="OAuth2 Client Secret">
                </div>
            </div>
            <button class="btn btn-secondary" onclick="testNinjaOne()">Test Connection</button>
            <div id="ninja-status"></div>
        </div>
        
        <div class="card">
            <h2>Jira Credentials</h2>
            <div class="row">
                <div class="form-group">
                    <label>Subdomain (e.g., mycompany)</label>
                    <input type="text" id="jira-subdomain" placeholder="mycompany">
                </div>
                <div class="form-group">
                    <label>Email</label>
                    <input type="email" id="jira-email" placeholder="user@example.com">
                </div>
            </div>
            <div class="form-group">
                <label>API Token</label>
                <input type="password" id="jira-token" placeholder="Jira API Token">
            </div>
            <button class="btn btn-secondary" onclick="testJira()">Test Connection</button>
            <div id="jira-status"></div>
        </div>
        
        <div class="card">
            <h2>Jira Assets Configuration</h2>
            <div class="row">
                <div class="form-group">
                    <label>Schema</label>
                    <select id="jira-schema" onchange="loadObjectTypes()">
                        <option value="">-- Test Jira connection first --</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Object Type</label>
                    <select id="jira-type" onchange="loadAttributes()">
                        <option value="">-- Select schema first --</option>
                    </select>
                </div>
            </div>
            <div id="attributes-container"></div>
        </div>
        
        <div class="actions">
            <button class="btn btn-secondary" onclick="exportConfig()">Export Config</button>
            <button class="btn btn-success" onclick="saveConfig()">Save & Exit</button>
        </div>
    </div>
    
    <script>
        async function testNinjaOne() {
            const status = document.getElementById('ninja-status');
            status.className = 'status';
            status.textContent = 'Testing...';
            
            try {
                const res = await fetch('/api/test/ninjaone', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        ninja_base_url: document.getElementById('ninja-url').value,
                        ninja_client_id: document.getElementById('ninja-client-id').value,
                        ninja_client_secret: document.getElementById('ninja-client-secret').value,
                    })
                });
                const data = await res.json();
                status.className = 'status ' + data.status;
                status.textContent = data.message;
            } catch (e) {
                status.className = 'status error';
                status.textContent = 'Connection failed: ' + e.message;
            }
        }
        
        async function testJira() {
            const status = document.getElementById('jira-status');
            status.className = 'status';
            status.textContent = 'Testing...';
            
            try {
                const res = await fetch('/api/test/jira', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        jira_subdomain: document.getElementById('jira-subdomain').value,
                        jira_email: document.getElementById('jira-email').value,
                        jira_api_token: document.getElementById('jira-token').value,
                    })
                });
                const data = await res.json();
                status.className = 'status ' + data.status;
                status.textContent = data.message + (data.workspace_id ? ' (Workspace: ' + data.workspace_id + ')' : '');
                
                if (data.status === 'success') {
                    loadSchemas();
                }
            } catch (e) {
                status.className = 'status error';
                status.textContent = 'Connection failed: ' + e.message;
            }
        }
        
        async function loadSchemas() {
            const select = document.getElementById('jira-schema');
            try {
                const params = new URLSearchParams({
                    subdomain: document.getElementById('jira-subdomain').value,
                    email: document.getElementById('jira-email').value,
                    api_token: document.getElementById('jira-token').value,
                });
                const res = await fetch('/api/jira/schemas?' + params);
                const data = await res.json();
                
                select.innerHTML = '<option value="">-- Select Schema --</option>';
                for (const schema of data.schemas || []) {
                    select.innerHTML += `<option value="${schema.id}">${schema.name}</option>`;
                }
            } catch (e) {
                console.error('Failed to load schemas:', e);
            }
        }
        
        async function loadObjectTypes() {
            const schemaId = document.getElementById('jira-schema').value;
            const select = document.getElementById('jira-type');
            
            if (!schemaId) {
                select.innerHTML = '<option value="">-- Select schema first --</option>';
                return;
            }
            
            try {
                const params = new URLSearchParams({
                    subdomain: document.getElementById('jira-subdomain').value,
                    email: document.getElementById('jira-email').value,
                    api_token: document.getElementById('jira-token').value,
                });
                const res = await fetch(`/api/jira/types/${schemaId}?` + params);
                const data = await res.json();
                
                select.innerHTML = '<option value="">-- Select Object Type --</option>';
                for (const type of data.types || []) {
                    select.innerHTML += `<option value="${type.id}">${type.name}</option>`;
                }
            } catch (e) {
                console.error('Failed to load types:', e);
            }
        }
        
        async function loadAttributes() {
            const typeId = document.getElementById('jira-type').value;
            const container = document.getElementById('attributes-container');
            
            if (!typeId) {
                container.innerHTML = '';
                return;
            }
            
            try {
                const params = new URLSearchParams({
                    subdomain: document.getElementById('jira-subdomain').value,
                    email: document.getElementById('jira-email').value,
                    api_token: document.getElementById('jira-token').value,
                });
                const res = await fetch(`/api/jira/attributes/${typeId}?` + params);
                const data = await res.json();
                
                let html = '<h3 style="margin: 1rem 0; color: #4fc3f7;">Available Attributes</h3>';
                html += '<table style="width: 100%; border-collapse: collapse;">';
                html += '<tr style="text-align: left; border-bottom: 1px solid rgba(255,255,255,0.1);"><th style="padding: 0.5rem;">Name</th><th>Type</th><th>Required</th></tr>';
                for (const attr of data.attributes || []) {
                    html += `<tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                        <td style="padding: 0.5rem;">${attr.name}</td>
                        <td>${attr.type || 'Default'}</td>
                        <td>${attr.required ? '✓' : ''}</td>
                    </tr>`;
                }
                html += '</table>';
                container.innerHTML = html;
            } catch (e) {
                console.error('Failed to load attributes:', e);
            }
        }
        
        function exportConfig() {
            const config = buildConfig();
            const blob = new Blob([JSON.stringify(config, null, 2)], {type: 'application/json'});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'config.json';
            a.click();
        }
        
        function buildConfig() {
            return {
                ninjaone: {
                    base_url: document.getElementById('ninja-url').value,
                    client_id: document.getElementById('ninja-client-id').value,
                },
                jira: {
                    subdomain: document.getElementById('jira-subdomain').value,
                    email: document.getElementById('jira-email').value,
                },
                assets: {
                    object_schema_id: document.getElementById('jira-schema').value,
                    object_type_id: document.getElementById('jira-type').value,
                },
            };
        }
        
        async function saveConfig() {
            try {
                const res = await fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        config: buildConfig(),
                        write_secrets: false,
                    })
                });
                const data = await res.json();
                alert('Configuration saved to ' + data.path + '\\n\\nSet secrets via environment variables:\\nNINJA_CLIENT_SECRET=...\\nJIRA_API_TOKEN=...');
            } catch (e) {
                alert('Failed to save: ' + e.message);
            }
        }
        
        // Load existing config on page load
        fetch('/api/config').then(r => r.json()).then(config => {
            if (config.ninjaone?.base_url) document.getElementById('ninja-url').value = config.ninjaone.base_url;
            if (config.ninjaone?.client_id) document.getElementById('ninja-client-id').value = config.ninjaone.client_id;
            if (config.jira?.subdomain) document.getElementById('jira-subdomain').value = config.jira.subdomain;
            if (config.jira?.email) document.getElementById('jira-email').value = config.jira.email;
        });
    </script>
</body>
</html>
"""
    
    def run(self) -> None:
        """Run the server."""
        import uvicorn
        
        logger.info("Starting configuration UI at http://%s:%d", self.host, self.port)
        uvicorn.run(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
        )


def run_config_ui(
    config_path: str | Path | None = None,
    port: int = 5000,
) -> None:
    """Run the configuration UI server.
    
    Args:
        config_path: Path to save configuration.
        port: Port to bind.
    """
    server = ConfigUIServer(config_path=config_path, port=port)
    server.run()

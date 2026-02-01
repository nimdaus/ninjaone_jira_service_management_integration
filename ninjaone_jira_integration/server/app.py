"""
FastAPI application factory.

Creates the ASGI application with all routes, middleware,
and lifecycle management.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ninjaone_jira_integration.config import AppConfig, load_config
from ninjaone_jira_integration.store.db import DatabaseManager
from ninjaone_jira_integration.server.webhooks import router as webhook_router
from ninjaone_jira_integration.server.worker import JobWorker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager.
    
    Handles startup and shutdown of:
    - Database connections
    - API clients
    - Background workers
    """
    config: AppConfig = app.state.config
    
    logger.info("Starting integration server")
    
    # Initialize database
    db = DatabaseManager(config.database.path, config.database.wal_mode)
    await db.initialize()
    app.state.db = db
    
    # Start job worker
    worker = JobWorker(config, db)
    await worker.start()
    app.state.worker = worker
    
    logger.info("Server started successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down server")
    
    await worker.stop()
    await db.close()
    
    logger.info("Server shutdown complete")


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI application.
    
    Args:
        config: Optional configuration. If not provided, will be loaded.
        
    Returns:
        Configured FastAPI application.
    """
    if config is None:
        config = load_config()
    
    app = FastAPI(
        title="NinjaOne-Jira Integration",
        description="Integration service for syncing NinjaOne devices to Jira Assets",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if config.logging.level == "DEBUG" else None,
        redoc_url=None,
    )
    
    # Store config in app state
    app.state.config = config
    
    # Add CORS middleware for local UI development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include routers
    app.include_router(webhook_router, prefix="/webhook", tags=["webhooks"])
    
    # Health endpoints
    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        """Basic liveness probe."""
        return {"status": "ok"}
    
    @app.get("/readyz", tags=["health"])
    async def readyz(request: Request) -> dict[str, Any]:
        """Readiness probe with dependency checks."""
        db: DatabaseManager = request.app.state.db
        
        checks = {
            "database": "unknown",
            "worker": "unknown",
        }
        
        # Check database
        try:
            stats = await db.get_stats()
            checks["database"] = "ok"
            checks["db_stats"] = stats
        except Exception as e:
            checks["database"] = f"error: {str(e)}"
        
        # Check worker
        worker: JobWorker = request.app.state.worker
        if worker.is_running:
            checks["worker"] = "ok"
        else:
            checks["worker"] = "stopped"
        
        is_ready = all(
            v == "ok" for v in [checks["database"], checks["worker"]]
        )
        
        return {
            "status": "ready" if is_ready else "not_ready",
            "checks": checks,
        }
    
    @app.get("/status", tags=["health"])
    async def status(request: Request) -> dict[str, Any]:
        """Extended status information."""
        db: DatabaseManager = request.app.state.db
        worker: JobWorker = request.app.state.worker
        
        from ninjaone_jira_integration.store.jobs import JobStore
        from ninjaone_jira_integration.store.mappings import MappingStore
        
        job_store = JobStore(db)
        mapping_store = MappingStore(db)
        
        job_stats = await job_store.get_stats()
        device_count = await mapping_store.count_device_mappings()
        alert_count = await mapping_store.count_alert_mappings()
        
        return {
            "version": "0.1.0",
            "jobs": {
                "queued": job_stats.queued,
                "processing": job_stats.processing,
                "completed": job_stats.completed,
                "failed": job_stats.failed,
                "dead_letter": job_stats.dead_letter,
            },
            "mappings": {
                "devices": device_count,
                "alerts": alert_count,
            },
            "worker": {
                "running": worker.is_running,
            },
        }
    
    # Exception handler
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Handle uncaught exceptions."""
        logger.exception("Unhandled exception: %s", str(exc))
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )
    
    return app

"""
Structured JSON logging with correlation ID support.

Provides:
- JSON formatter for structured logging
- Correlation ID propagation via contextvars
- FastAPI middleware for correlation ID handling
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Context variable for correlation ID
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    """Get the current correlation ID.
    
    Returns:
        Correlation ID or None if not set.
    """
    return _correlation_id.get()


def set_correlation_id(correlation_id: str | None) -> None:
    """Set the correlation ID for the current context.
    
    Args:
        correlation_id: Correlation ID to set.
    """
    _correlation_id.set(correlation_id)


class StructuredLogFormatter(logging.Formatter):
    """JSON log formatter for structured logging.
    
    Output format:
    {
        "timestamp": "2024-01-01T00:00:00.000Z",
        "level": "INFO",
        "logger": "module.name",
        "message": "Log message",
        "correlation_id": "uuid",
        "extra": {...}
    }
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.
        
        Args:
            record: Log record to format.
            
        Returns:
            JSON string.
        """
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add correlation ID if present
        correlation_id = get_correlation_id()
        if correlation_id:
            log_data["correlation_id"] = correlation_id
        
        # Add source location for debug/error
        if record.levelno >= logging.WARNING:
            log_data["location"] = {
                "file": record.filename,
                "line": record.lineno,
                "function": record.funcName,
            }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields
        extra_fields = {
            k: v for k, v in record.__dict__.items()
            if k not in {
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "pathname", "process", "processName", "relativeCreated",
                "stack_info", "exc_info", "exc_text", "thread", "threadName",
                "message",
            }
        }
        
        if extra_fields:
            log_data["extra"] = extra_fields
        
        return json.dumps(log_data, default=str)


class ConsoleLogFormatter(logging.Formatter):
    """Human-readable console formatter with colors.
    
    For development use.
    """
    
    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record for console.
        
        Args:
            record: Log record to format.
            
        Returns:
            Formatted string.
        """
        color = self.COLORS.get(record.levelname, "")
        
        # Timestamp
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Build message
        parts = [
            f"{color}{record.levelname:8s}{self.RESET}",
            timestamp,
        ]
        
        # Add correlation ID if present
        correlation_id = get_correlation_id()
        if correlation_id:
            parts.append(f"[{correlation_id[:8]}]")
        
        parts.append(f"{record.name}: {record.getMessage()}")
        
        message = " ".join(parts)
        
        # Add exception if present
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        
        return message


def setup_structured_logging(
    level: str = "INFO",
    json_format: bool = True,
    log_file: str | None = None,
) -> None:
    """Configure structured logging.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        json_format: If True, use JSON format. If False, use console format.
        log_file: Optional log file path.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())
    
    # Remove existing handlers
    root_logger.handlers = []
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level.upper())
    
    if json_format:
        console_handler.setFormatter(StructuredLogFormatter())
    else:
        console_handler.setFormatter(ConsoleLogFormatter())
    
    root_logger.addHandler(console_handler)
    
    # File handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level.upper())
        file_handler.setFormatter(StructuredLogFormatter())
        root_logger.addHandler(file_handler)
    
    # Reduce noise from libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for correlation ID handling.
    
    Extracts X-Correlation-Id header or generates a new one,
    sets it in context, and adds it to response headers.
    """
    
    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with correlation ID.
        
        Args:
            request: Incoming request.
            call_next: Next middleware/handler.
            
        Returns:
            Response with correlation ID header.
        """
        # Get or generate correlation ID
        correlation_id = request.headers.get("X-Correlation-Id")
        if not correlation_id:
            correlation_id = str(uuid.uuid4())
        
        # Set in context
        set_correlation_id(correlation_id)
        
        try:
            response = await call_next(request)
        finally:
            # Clear context
            set_correlation_id(None)
        
        # Add to response headers
        response.headers["X-Correlation-Id"] = correlation_id
        
        return response

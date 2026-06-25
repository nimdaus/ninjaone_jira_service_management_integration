"""
Base HTTP client with retry logic, rate limiting, and error handling.

All API clients inherit from this base class to ensure consistent
behavior for retries, rate limiting, and error handling.
"""

from __future__ import annotations

import asyncio
import logging
import random
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

import httpx

from ninjaone_jira_integration.utils.concurrency import RateLimiter, RetryAfterTracker

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Base exception for API errors."""
    
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class RateLimitError(APIError):
    """Raised when API rate limit is exceeded."""
    
    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: int | None = None,
        **kwargs: Any,
    ):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class AuthenticationError(APIError):
    """Raised when authentication fails."""
    pass


class NotFoundError(APIError):
    """Raised when a resource is not found."""
    pass


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    
    max_retries: int = 8
    base_delay: float = 1.0
    max_delay: float = 300.0
    jitter: bool = True
    
    # Status codes that should trigger a retry
    retryable_status_codes: set[int] = field(
        default_factory=lambda: {429, 500, 502, 503, 504}
    )


class BaseClient:
    """Base HTTP client with retry logic and rate limiting.
    
    Features:
    - Exponential backoff with jitter for transient errors
    - Respects Retry-After headers
    - Configurable rate limiting via RateLimiter
    - Request/response logging with secret redaction
    - Automatic connection pooling via httpx
    """
    
    def __init__(
        self,
        base_url: str,
        retry_config: RetryConfig | None = None,
        rate_limiter: RateLimiter | None = None,
        timeout: float = 30.0,
    ):
        """Initialize the base client.
        
        Args:
            base_url: Base URL for all requests.
            retry_config: Retry configuration.
            rate_limiter: Optional rate limiter.
            timeout: Request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.retry_config = retry_config or RetryConfig()
        self.rate_limiter = rate_limiter
        self.timeout = timeout
        
        self._client: httpx.AsyncClient | None = None
        self._retry_after_tracker = RetryAfterTracker()
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client.
        
        Returns:
            Configured httpx.AsyncClient instance.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client
    
    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def _prepare_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        """Prepare request headers.
        
        Override in subclasses to add authentication headers.
        
        Args:
            headers: Optional additional headers.
            
        Returns:
            Complete headers dict.
        """
        result = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if headers:
            result.update(headers)
        return result
    
    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate backoff delay for retry attempt.
        
        Uses exponential backoff with optional jitter.
        
        Args:
            attempt: Retry attempt number (0-indexed).
            
        Returns:
            Delay in seconds.
        """
        delay = min(
            self.retry_config.base_delay * (2 ** attempt),
            self.retry_config.max_delay,
        )
        
        if self.retry_config.jitter:
            # Add random jitter (±25%)
            jitter_range = delay * 0.25
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0.1, delay)
    
    def _should_retry(self, status_code: int) -> bool:
        """Check if a status code should trigger a retry.
        
        Args:
            status_code: HTTP status code.
            
        Returns:
            True if the request should be retried.
        """
        return status_code in self.retry_config.retryable_status_codes
    
    def _parse_retry_after(self, response: httpx.Response) -> int | None:
        """Parse Retry-After header from response.
        
        Args:
            response: HTTP response.
            
        Returns:
            Seconds to wait, or None if not present.
        """
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return int(retry_after)
            except ValueError:
                # Could be HTTP date format, just use default
                return 60
        return None
    
    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make an HTTP request with retry logic.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            path: Request path (will be joined with base_url).
            params: Query parameters.
            json: JSON body.
            data: Form data.
            headers: Additional headers.
            
        Returns:
            HTTP response.
            
        Raises:
            APIError: For non-retryable errors.
            RateLimitError: When rate limited and retries exhausted.
        """
        client = await self._get_client()
        full_headers = await self._prepare_headers(headers)
        
        last_exception: Exception | None = None
        
        for attempt in range(self.retry_config.max_retries + 1):
            try:
                # Wait for any retry-after period
                await self._retry_after_tracker.wait_if_needed()
                
                # Apply rate limiting
                async with (self.rate_limiter.acquire() if self.rate_limiter else nullcontext()):
                    logger.debug("Request: %s %s", method.upper(), path)
                    response = await client.request(
                        method=method,
                        url=path,
                        params=params,
                        json=json,
                        data=data,
                        headers=full_headers,
                    )
                    logger.debug(
                        "Response: %d %s (%.0f bytes)",
                        response.status_code,
                        response.reason_phrase,
                        len(response.content),
                    )

                # Check for rate limiting
                if response.status_code == 429:
                    retry_after = self._parse_retry_after(response)
                    if retry_after:
                        await self._retry_after_tracker.set_retry_after(retry_after)
                    
                    if attempt < self.retry_config.max_retries:
                        delay = retry_after or self._calculate_backoff(attempt)
                        logger.warning(
                            "Rate limited, retrying in %.1f seconds (attempt %d/%d)",
                            delay,
                            attempt + 1,
                            self.retry_config.max_retries,
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise RateLimitError(
                            "Rate limit exceeded after max retries",
                            retry_after=retry_after,
                            status_code=429,
                        )
                
                # Check for other retryable errors
                if self._should_retry(response.status_code):
                    if attempt < self.retry_config.max_retries:
                        delay = self._calculate_backoff(attempt)
                        logger.warning(
                            "Request failed with %d, retrying in %.1f seconds (attempt %d/%d)",
                            response.status_code,
                            delay,
                            attempt + 1,
                            self.retry_config.max_retries,
                        )
                        await asyncio.sleep(delay)
                        continue
                
                # Handle specific error codes
                if response.status_code == 401:
                    raise AuthenticationError(
                        "Authentication failed",
                        status_code=401,
                        response_body=response.text,
                    )
                
                if response.status_code == 404:
                    raise NotFoundError(
                        f"Resource not found: {path}",
                        status_code=404,
                        response_body=response.text,
                    )
                
                if response.status_code >= 400:
                    raise APIError(
                        f"API error: {response.status_code}",
                        status_code=response.status_code,
                        response_body=response.text,
                    )
                
                # Success
                return response
                
            except httpx.TimeoutException as e:
                last_exception = e
                if attempt < self.retry_config.max_retries:
                    delay = self._calculate_backoff(attempt)
                    logger.warning(
                        "Request timed out, retrying in %.1f seconds (attempt %d/%d)",
                        delay,
                        attempt + 1,
                        self.retry_config.max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue
                    
            except httpx.NetworkError as e:
                last_exception = e
                if attempt < self.retry_config.max_retries:
                    delay = self._calculate_backoff(attempt)
                    logger.warning(
                        "Network error, retrying in %.1f seconds (attempt %d/%d): %s",
                        delay,
                        attempt + 1,
                        self.retry_config.max_retries,
                        str(e),
                    )
                    await asyncio.sleep(delay)
                    continue
        
        # All retries exhausted
        if last_exception:
            raise APIError(f"Request failed after {self.retry_config.max_retries} retries: {last_exception}")
        raise APIError(f"Request failed after {self.retry_config.max_retries} retries")
    
    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make a GET request.
        
        Args:
            path: Request path.
            params: Query parameters.
            headers: Additional headers.
            
        Returns:
            HTTP response.
        """
        return await self.request("GET", path, params=params, headers=headers)
    
    async def post(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make a POST request.
        
        Args:
            path: Request path.
            json: JSON body.
            data: Form data.
            params: Query parameters.
            headers: Additional headers.
            
        Returns:
            HTTP response.
        """
        return await self.request(
            "POST", path, json=json, data=data, params=params, headers=headers
        )
    
    async def put(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make a PUT request.
        
        Args:
            path: Request path.
            json: JSON body.
            params: Query parameters.
            headers: Additional headers.
            
        Returns:
            HTTP response.
        """
        return await self.request("PUT", path, json=json, params=params, headers=headers)
    
    async def delete(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make a DELETE request.
        
        Args:
            path: Request path.
            params: Query parameters.
            headers: Additional headers.
            
        Returns:
            HTTP response.
        """
        return await self.request("DELETE", path, params=params, headers=headers)
    

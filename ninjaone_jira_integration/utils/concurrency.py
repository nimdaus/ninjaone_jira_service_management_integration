"""
Concurrency control utilities.

Provides rate limiting and request throttling for API calls.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class TokenBucket:
    """Token bucket rate limiter.
    
    Allows bursts up to bucket capacity while maintaining
    a long-term average rate.
    """
    
    capacity: float
    """Maximum number of tokens in the bucket."""
    
    refill_rate: float
    """Tokens added per second."""
    
    _tokens: float = 0.0
    _last_refill: float = 0.0
    _lock: asyncio.Lock | None = None
    
    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
    
    async def acquire(self, tokens: float = 1.0) -> float:
        """Acquire tokens, waiting if necessary.
        
        Args:
            tokens: Number of tokens to acquire.
            
        Returns:
            Time spent waiting in seconds.
        """
        assert self._lock is not None
        
        async with self._lock:
            wait_time = 0.0
            
            while True:
                self._refill()
                
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return wait_time
                
                # Calculate wait time for enough tokens
                tokens_needed = tokens - self._tokens
                delay = tokens_needed / self.refill_rate
                wait_time += delay
                
                await asyncio.sleep(delay)
    
    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        
        self._tokens = min(
            self.capacity,
            self._tokens + elapsed * self.refill_rate,
        )
    
    @classmethod
    def from_rate_per_minute(
        cls,
        requests_per_minute: int,
        burst_size: int | None = None,
    ) -> "TokenBucket":
        """Create a token bucket from a per-minute rate.
        
        Args:
            requests_per_minute: Maximum requests per minute.
            burst_size: Maximum burst size (defaults to rate/10).
            
        Returns:
            Configured TokenBucket instance.
        """
        refill_rate = requests_per_minute / 60.0
        capacity = burst_size if burst_size else max(1, requests_per_minute // 10)
        
        return cls(capacity=capacity, refill_rate=refill_rate)


class RateLimiter:
    """Combined rate limiter with semaphore for concurrency control.
    
    Enforces both:
    - Maximum concurrent requests (semaphore)
    - Maximum requests per time period (token bucket)
    """
    
    def __init__(
        self,
        max_concurrent: int = 2,
        requests_per_minute: int | None = None,
    ):
        """Initialize rate limiter.
        
        Args:
            max_concurrent: Maximum concurrent requests.
            requests_per_minute: Optional rate limit per minute.
        """
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._token_bucket: TokenBucket | None = None
        
        if requests_per_minute:
            self._token_bucket = TokenBucket.from_rate_per_minute(requests_per_minute)
    
    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """Acquire permission for a request.
        
        Usage:
            async with limiter.acquire():
                await make_request()
        """
        # Wait for token bucket if configured
        if self._token_bucket:
            await self._token_bucket.acquire()
        
        # Wait for semaphore
        async with self._semaphore:
            yield


def create_jira_limiter(
    max_concurrent: int = 2,
    requests_per_minute: int | None = None,
) -> RateLimiter:
    """Create a rate limiter configured for Jira API.
    
    Jira Cloud has rate limits that vary by plan and endpoint.
    This creates a conservative limiter to avoid hitting limits.
    
    Args:
        max_concurrent: Maximum concurrent Jira requests.
        requests_per_minute: Optional requests per minute limit.
        
    Returns:
        Configured RateLimiter instance.
    """
    return RateLimiter(
        max_concurrent=max_concurrent,
        requests_per_minute=requests_per_minute,
    )


class RetryAfterTracker:
    """Tracks Retry-After headers from API responses.
    
    When an API returns 429 with Retry-After, this ensures we
    don't make requests until the specified time has passed.
    """
    
    def __init__(self) -> None:
        self._retry_after: float = 0.0
        self._lock = asyncio.Lock()
    
    async def set_retry_after(self, seconds: float) -> None:
        """Set retry-after time.
        
        Args:
            seconds: Seconds to wait before next request.
        """
        async with self._lock:
            new_time = time.monotonic() + seconds
            self._retry_after = max(self._retry_after, new_time)
    
    async def wait_if_needed(self) -> float:
        """Wait if we're in a retry-after period.
        
        Returns:
            Time spent waiting in seconds.
        """
        async with self._lock:
            now = time.monotonic()
            if now < self._retry_after:
                wait_time = self._retry_after - now
                await asyncio.sleep(wait_time)
                return wait_time
            return 0.0

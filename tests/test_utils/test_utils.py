"""Tests for utility modules."""

from __future__ import annotations

import asyncio

import pytest

from ninjaone_jira_integration.utils.secrets import REDACTED, redact_secrets, redact_string


class TestSecretRedaction:
    """Tests for secret redaction utility."""

    def test_redact_dict_with_secrets(self) -> None:
        data = {
            "username": "user@example.com",
            "password": "super-secret",
            "token": "my-api-token",
            "client_secret": "oauth-secret",
            "api_key": "key-123",
        }

        redacted = redact_secrets(data)

        assert redacted["username"] == "user@example.com"
        assert redacted["password"] == REDACTED
        assert redacted["token"] == REDACTED
        assert redacted["client_secret"] == REDACTED
        assert redacted["api_key"] == REDACTED

    def test_redact_nested_dict(self) -> None:
        data = {
            "ninjaone": {
                "client_id": "test-id",
                "client_secret": "test-secret",
            },
            "jira": {
                "api_token": "jira-token",
            },
        }

        redacted = redact_secrets(data)

        assert redacted["ninjaone"]["client_id"] == "test-id"
        assert redacted["ninjaone"]["client_secret"] == REDACTED
        assert redacted["jira"]["api_token"] == REDACTED

    def test_redact_string_with_known_secrets(self) -> None:
        message = "Connecting with token secret123 and password supersecret"
        redacted = redact_string(message, ["secret123", "supersecret"])
        assert "secret123" not in redacted
        assert "supersecret" not in redacted

    def test_preserve_non_secret_values(self) -> None:
        data = {
            "host": "example.com",
            "port": 443,
            "enabled": True,
            "count": 100,
        }

        redacted = redact_secrets(data)
        assert redacted == data

    def test_handle_none_values(self) -> None:
        data = {
            "password": None,
            "token": None,
        }

        redacted = redact_secrets(data)
        # Secret keys with None values are still redacted
        assert redacted["password"] == REDACTED
        assert redacted["token"] == REDACTED

    def test_handle_empty_strings(self) -> None:
        data = {
            "password": "",
            "api_key": "",
        }

        redacted = redact_secrets(data)
        assert redacted["password"] == REDACTED
        assert redacted["api_key"] == REDACTED


class TestConcurrencyUtils:
    """Tests for concurrency utilities."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self) -> None:
        sem = asyncio.Semaphore(2)
        active = 0
        max_active = 0

        async def task():
            nonlocal active, max_active
            async with sem:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.05)
                active -= 1

        await asyncio.gather(*[task() for _ in range(5)])
        assert max_active <= 2

    @pytest.mark.asyncio
    async def test_rate_limiter_acquire_context_manager(self) -> None:
        from ninjaone_jira_integration.utils.concurrency import RateLimiter

        limiter = RateLimiter(max_concurrent=3)

        # acquire() is an async context manager
        async with limiter.acquire():
            pass  # Should not raise

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_concurrent_up_to_limit(self) -> None:
        from ninjaone_jira_integration.utils.concurrency import RateLimiter

        limiter = RateLimiter(max_concurrent=2)
        active = 0
        max_active = 0

        async def task():
            nonlocal active, max_active
            async with limiter.acquire():
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(*[task() for _ in range(4)])
        assert max_active <= 2

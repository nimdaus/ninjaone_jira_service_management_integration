"""Tests for utility modules."""

import pytest

from ninjaone_jira_integration.utils.secrets import redact_secrets


class TestSecretRedaction:
    """Tests for secret redaction utility."""
    
    def test_redact_dict_with_secrets(self):
        """Test redacting secrets from a dictionary."""
        data = {
            "username": "user@example.com",
            "password": "super-secret",
            "token": "my-api-token",
            "client_secret": "oauth-secret",
            "api_key": "key-123",
        }
        
        redacted = redact_secrets(data)
        
        assert redacted["username"] == "user@example.com"  # Not a secret
        assert redacted["password"] == "***"
        assert redacted["token"] == "***"
        assert redacted["client_secret"] == "***"
        assert redacted["api_key"] == "***"
    
    def test_redact_nested_dict(self):
        """Test redacting secrets from nested dictionaries."""
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
        assert redacted["ninjaone"]["client_secret"] == "***"
        assert redacted["jira"]["api_token"] == "***"
    
    def test_redact_in_log_message(self):
        """Test redacting secrets from a log message string."""
        message = 'Connecting with token="secret123" and password=supersecret'
        
        redacted = redact_secrets(message)
        
        assert "secret123" not in redacted
        assert "supersecret" not in redacted
    
    def test_preserve_non_secret_values(self):
        """Test that non-secret values are preserved."""
        data = {
            "host": "example.com",
            "port": 443,
            "enabled": True,
            "count": 100,
        }
        
        redacted = redact_secrets(data)
        
        assert redacted == data
    
    def test_handle_none_values(self):
        """Test handling of None values."""
        data = {
            "password": None,
            "token": None,
        }
        
        redacted = redact_secrets(data)
        
        # None values should be preserved
        assert redacted["password"] is None
        assert redacted["token"] is None
    
    def test_handle_empty_strings(self):
        """Test handling of empty string secrets."""
        data = {
            "password": "",
            "api_key": "",
        }
        
        redacted = redact_secrets(data)
        
        # Empty strings should be redacted to avoid confusion
        assert redacted["password"] == "***"
        assert redacted["api_key"] == "***"


class TestConcurrencyUtils:
    """Tests for concurrency utilities."""
    
    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Test that semaphore-based limiting works."""
        from ninjaone_jira_integration.utils.concurrency import AsyncSemaphore
        import asyncio
        
        sem = AsyncSemaphore(2)  # Allow 2 concurrent
        active = 0
        max_active = 0
        
        async def task():
            nonlocal active, max_active
            async with sem:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.05)
                active -= 1
        
        # Run 5 tasks with limit of 2
        await asyncio.gather(*[task() for _ in range(5)])
        
        assert max_active <= 2
    
    @pytest.mark.asyncio
    async def test_rate_limiter_tokens(self):
        """Test rate limiter token consumption."""
        from ninjaone_jira_integration.utils.concurrency import RateLimiter
        
        limiter = RateLimiter(rate=3, per_seconds=1)
        
        # Should be able to acquire 3 tokens immediately
        for _ in range(3):
            assert await limiter.acquire() is True
        
        # 4th should work but may have waited
        # (depending on implementation, this tests the interface)
        result = await limiter.acquire()
        assert result is True

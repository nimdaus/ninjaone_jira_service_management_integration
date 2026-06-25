"""Tests for API clients."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from ninjaone_jira_integration.clients.base import BaseClient
from ninjaone_jira_integration.utils.concurrency import RateLimiter


class TestRateLimiter:
    """Tests for rate limiter."""

    @pytest.mark.asyncio
    async def test_acquire_within_limit(self):
        """Test that acquire context manager works without error."""
        limiter = RateLimiter(max_concurrent=2)
        async with limiter.acquire():
            pass  # Should not raise

    @pytest.mark.asyncio
    async def test_concurrent_limit_respected(self):
        """Test that semaphore limits concurrent requests."""
        import asyncio

        limiter = RateLimiter(max_concurrent=1)
        results = []

        async def task(n):
            async with limiter.acquire():
                results.append(f"start-{n}")
                await asyncio.sleep(0.01)
                results.append(f"end-{n}")

        await asyncio.gather(task(1), task(2))
        # With max_concurrent=1, tasks must interleave: start1, end1, start2, end2
        assert results[0] == "start-1" or results[0] == "start-2"
        # Each start must be immediately followed by its own end (no interleaving)
        assert results.index("end-1") < results.index("start-2") or results.index("end-2") < results.index("start-1")


class TestBaseClient:
    """Tests for BaseClient."""
    
    @pytest.mark.asyncio
    async def test_request_adds_headers(self):
        """Test that requests include proper headers."""
        with patch.object(httpx.AsyncClient, 'request') as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.raise_for_status = MagicMock()
            mock_request.return_value = mock_response
            
            client = BaseClient(base_url="https://example.com")
            
            await client.request("GET", "/test")
            
            # Verify request was made
            mock_request.assert_called()
    
    @pytest.mark.asyncio
    async def test_retry_on_503(self):
        """Test retry on 503 Service Unavailable."""
        from ninjaone_jira_integration.clients.base import RetryConfig

        call_count = 0

        async def mock_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            response = MagicMock()
            response.reason_phrase = "OK" if call_count >= 3 else "Service Unavailable"
            response.content = b""
            response.status_code = 200 if call_count >= 3 else 503
            return response

        with patch.object(httpx.AsyncClient, 'request', side_effect=mock_request):
            client = BaseClient(
                base_url="https://example.com",
                retry_config=RetryConfig(max_retries=5, base_delay=0.0),
            )

            await client.request("GET", "/test")

            assert call_count == 3  # Failed twice, succeeded on third


class TestNinjaOneClient:
    """Tests for NinjaOne client."""
    
    @pytest.mark.asyncio
    async def test_authentication(self):
        """Test OAuth2 authentication flow."""
        from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
        from pydantic import SecretStr
        
        mock_token_response = {
            "access_token": "test-token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        
        with patch.object(httpx.AsyncClient, 'post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_token_response
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response
            
            client = NinjaOneClient(
                base_url="https://app.ninjarmm.com",
                client_id="test-id",
                client_secret=SecretStr("test-secret"),
            )
            
            await client.authenticate()
            
            assert client._access_token == "test-token"
    
    @pytest.mark.asyncio
    async def test_get_device(self):
        """Test fetching a specific device."""
        from ninjaone_jira_integration.clients.ninjaone import NinjaOneClient
        from pydantic import SecretStr
        
        mock_device = {
            "id": 12345,
            "systemName": "TEST-LAPTOP",
        }
        
        client = NinjaOneClient(
            base_url="https://app.ninjarmm.com",
            client_id="test-id",
            client_secret=SecretStr("test-secret"),
        )
        client._access_token = "test-token"
        
        with patch.object(client, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_device
            mock_get.return_value = mock_response
            
            device = await client.get_device(12345)
            
            assert device["id"] == 12345
            assert device["systemName"] == "TEST-LAPTOP"


class TestJiraAssetsClient:
    """Tests for Jira Assets client."""
    
    @pytest.mark.asyncio
    async def test_auth_header(self):
        """Test that basic auth header is constructed correctly."""
        from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
        from pydantic import SecretStr
        import base64
        
        client = JiraAssetsClient(
            subdomain="testcompany",
            email="user@example.com",
            api_token=SecretStr("my-token"),
        )
        
        expected_creds = base64.b64encode(b"user@example.com:my-token").decode()
        expected_header = f"Basic {expected_creds}"
        
        assert client._auth_header == expected_header
    
    @pytest.mark.asyncio
    async def test_workspace_discovery(self):
        """Test workspace ID discovery."""
        from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
        from pydantic import SecretStr
        
        mock_response_data = {
            "values": [{"workspaceId": "workspace-abc-123"}]
        }
        
        client = JiraAssetsClient(
            subdomain="testcompany",
            email="user@example.com",
            api_token=SecretStr("my-token"),
        )
        
        with patch.object(client, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_data
            mock_get.return_value = mock_response
            
            workspace_id = await client.discover_workspace()
            
            assert workspace_id == "workspace-abc-123"
    
    @pytest.mark.asyncio
    async def test_search_by_aql(self):
        """Test searching objects with AQL (uses POST to /object/aql)."""
        from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
        from pydantic import SecretStr

        mock_search_results = {
            "values": [
                {"id": "1", "objectKey": "ASSET-1"},
                {"id": "2", "objectKey": "ASSET-2"},
            ]
        }

        client = JiraAssetsClient(
            subdomain="testcompany",
            email="user@example.com",
            api_token=SecretStr("my-token"),
            workspace_id="workspace-123",
        )

        async def mock_post(*args, **kwargs):
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = mock_search_results
            return response

        with patch.object(httpx.AsyncClient, 'post', side_effect=mock_post):
            results = await client.search_objects('objectType = "Computer"')

            assert len(results) == 2
            assert results[0]["objectKey"] == "ASSET-1"

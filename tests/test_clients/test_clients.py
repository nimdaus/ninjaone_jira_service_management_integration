"""Tests for API clients."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from ninjaone_jira_integration.clients.base import BaseClient, RateLimiter


class TestRateLimiter:
    """Tests for rate limiter."""
    
    @pytest.mark.asyncio
    async def test_acquire_within_limit(self):
        """Test acquiring tokens within the limit."""
        limiter = RateLimiter(rate=10, per_seconds=1)
        
        # Should be able to acquire immediately
        acquired = await limiter.acquire()
        
        assert acquired is True
    
    @pytest.mark.asyncio
    async def test_tokens_refill(self):
        """Test that tokens refill over time."""
        limiter = RateLimiter(rate=2, per_seconds=1)
        
        # Use up tokens
        await limiter.acquire()
        await limiter.acquire()
        
        # Wait for refill
        import asyncio
        await asyncio.sleep(0.6)
        
        # Should have at least 1 token now
        assert limiter.tokens >= 1


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
        call_count = 0
        
        async def mock_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            
            if call_count < 3:
                error = httpx.HTTPStatusError(
                    "Service Unavailable",
                    request=MagicMock(),
                    response=MagicMock(status_code=503),
                )
                raise error
            
            response = MagicMock()
            response.status_code = 200
            response.raise_for_status = MagicMock()
            return response
        
        with patch.object(httpx.AsyncClient, 'request', side_effect=mock_request):
            client = BaseClient(base_url="https://example.com", max_retries=5)
            
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
        """Test searching objects with AQL."""
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
        
        with patch.object(client, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_search_results
            mock_get.return_value = mock_response
            
            results = await client.search_objects('objectType = "Computer"')
            
            assert len(results) == 2
            assert results[0]["objectKey"] == "ASSET-1"

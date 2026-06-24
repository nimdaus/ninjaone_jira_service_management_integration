"""
NinjaOne API client.

Handles OAuth2 authentication with client credentials and provides
methods for accessing devices, alerts, and other NinjaOne resources.
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

from pydantic import SecretStr

from ninjaone_jira_integration.clients.base import (
    APIError,
    AuthenticationError,
    BaseClient,
    RetryConfig,
)
from ninjaone_jira_integration.utils.concurrency import RateLimiter

logger = logging.getLogger(__name__)


class NinjaOneClient(BaseClient):
    """NinjaOne API client with OAuth2 authentication.
    
    Supports:
    - Client credentials OAuth2 flow
    - Automatic token refresh
    - Paginated device listing
    - Alert retrieval
    """
    
    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: SecretStr,
        scopes: list[str] | None = None,
        retry_config: RetryConfig | None = None,
        rate_limiter: RateLimiter | None = None,
    ):
        """Initialize NinjaOne client.
        
        Args:
            base_url: NinjaOne API base URL (region-specific).
            client_id: OAuth2 client ID.
            client_secret: OAuth2 client secret.
            scopes: OAuth2 scopes to request.
            retry_config: Retry configuration.
            rate_limiter: Optional rate limiter.
        """
        super().__init__(
            base_url=base_url,
            retry_config=retry_config,
            rate_limiter=rate_limiter,
        )
        
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes or ["monitoring", "management"]
        
        # Token storage
        self._access_token: str | None = None
        self._token_expiry: float = 0.0
    
    @property
    def _token_url(self) -> str:
        """Get the OAuth2 token endpoint URL."""
        # NinjaOne token endpoint is at the same base URL
        return f"{self.base_url}/oauth/token"
    
    async def authenticate(self) -> None:
        """Obtain access token using client credentials.
        
        Raises:
            AuthenticationError: If authentication fails.
        """
        logger.info("Authenticating with NinjaOne API")
        
        # Build token request
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret.get_secret_value(),
            "scope": " ".join(self.scopes),
        }
        
        try:
            # Make request directly without auth headers
            client = await self._get_client()
            response = await client.post(
                "/oauth/token",
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            
            if response.status_code != 200:
                raise AuthenticationError(
                    f"Authentication failed: {response.status_code}",
                    status_code=response.status_code,
                    response_body=response.text,
                )
            
            token_data = response.json()
            self._access_token = token_data["access_token"]
            
            # Calculate token expiry (with 60 second buffer)
            expires_in = token_data.get("expires_in", 3600)
            self._token_expiry = time.time() + expires_in - 60
            
            logger.info("Successfully authenticated with NinjaOne")
            
        except Exception as e:
            if isinstance(e, AuthenticationError):
                raise
            raise AuthenticationError(f"Authentication failed: {e}") from e
    
    async def _ensure_authenticated(self) -> None:
        """Ensure we have a valid access token.
        
        Automatically refreshes token if expired.
        """
        if not self._access_token or time.time() >= self._token_expiry:
            await self.authenticate()
    
    async def _prepare_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        """Prepare request headers with authentication.
        
        Args:
            headers: Optional additional headers.
            
        Returns:
            Headers with Authorization bearer token.
        """
        await self._ensure_authenticated()
        
        result = await super()._prepare_headers(headers)
        result["Authorization"] = f"Bearer {self._access_token}"
        return result
    
    async def test_connection(self) -> bool:
        """Test the API connection.
        
        Returns:
            True if connection is successful.
            
        Raises:
            APIError: If connection fails.
        """
        await self._ensure_authenticated()
        
        # Try to get organization info as a simple test
        response = await self.get("/v2/organization")
        return response.status_code == 200
    
    async def get_devices_detailed(
        self,
        page_size: int = 100,
        device_filter: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Get all devices with detailed information.
        
        Uses cursor-based pagination to handle large device counts.
        
        Args:
            page_size: Number of devices per page (max 1000).
            device_filter: Optional device filter string.
            
        Yields:
            Device dictionaries with detailed information.
        """
        params: dict[str, Any] = {
            "pageSize": min(page_size, 1000),
        }
        
        if device_filter:
            params["df"] = device_filter
        
        cursor: str | None = None
        total_yielded = 0
        
        while True:
            if cursor:
                params["after"] = cursor
            
            logger.debug("Fetching devices page (after=%s)", cursor)
            
            response = await self.get("/v2/devices-detailed", params=params)
            data = response.json()
            
            devices = data if isinstance(data, list) else data.get("devices", [])
            
            if not devices:
                break
            
            for device in devices:
                yield device
                total_yielded += 1
            
            # Check for next page cursor
            # NinjaOne uses the last device ID as the cursor
            if len(devices) < page_size:
                break
            
            # Get ID of last device for cursor
            last_device = devices[-1]
            cursor = str(last_device.get("id"))
            
            if not cursor:
                break
        
        logger.info("Retrieved %d devices from NinjaOne", total_yielded)
    
    async def get_device(self, device_id: int) -> dict[str, Any]:
        """Get a single device by ID.
        
        Args:
            device_id: NinjaOne device ID.
            
        Returns:
            Device details dictionary.
            
        Raises:
            NotFoundError: If device not found.
        """
        response = await self.get(f"/v2/device/{device_id}")
        return response.json()
    
    async def get_device_by_id_detailed(self, device_id: int) -> dict[str, Any]:
        """Get detailed device information by ID.
        
        Args:
            device_id: NinjaOne device ID.
            
        Returns:
            Detailed device dictionary.
        """
        # Get basic device info
        device = await self.get_device(device_id)
        
        # Enrich with additional details if needed
        # The /v2/device/{id} endpoint already includes most details
        return device
    
    async def get_alerts(
        self,
        page_size: int = 100,
        source_type: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Get active alerts.
        
        Args:
            page_size: Number of alerts per page.
            source_type: Optional source type filter.
            
        Yields:
            Alert dictionaries.
        """
        params: dict[str, Any] = {
            "pageSize": min(page_size, 1000),
        }
        
        if source_type:
            params["sourceType"] = source_type
        
        cursor: str | None = None
        
        while True:
            if cursor:
                params["after"] = cursor
            
            response = await self.get("/v2/alerts", params=params)
            data = response.json()
            
            alerts = data if isinstance(data, list) else data.get("alerts", [])
            
            if not alerts:
                break
            
            for alert in alerts:
                yield alert
            
            if len(alerts) < page_size:
                break
            
            # Get cursor for next page
            last_alert = alerts[-1]
            cursor = last_alert.get("uid")

            if not cursor:
                break
    
    async def get_alert(self, alert_id: str) -> dict[str, Any]:
        """Get a single alert by UID.

        Args:
            alert_id: NinjaOne alert UID (UUID string).
            
        Returns:
            Alert details dictionary.
        """
        response = await self.get(f"/v2/alert/{alert_id}")
        return response.json()
    
    async def get_device_alerts(self, device_id: int) -> list[dict[str, Any]]:
        """Get alerts for a specific device.
        
        Args:
            device_id: NinjaOne device ID.
            
        Returns:
            List of alerts for the device.
        """
        response = await self.get(f"/v2/device/{device_id}/alerts")
        data = response.json()
        return data if isinstance(data, list) else data.get("alerts", [])
    
    async def get_organizations(self) -> list[dict[str, Any]]:
        """Get all organizations.
        
        Returns:
            List of organization dictionaries.
        """
        response = await self.get("/v2/organizations")
        data = response.json()
        return data if isinstance(data, list) else data.get("organizations", [])
    
    async def get_device_custom_fields(self, device_id: int) -> dict[str, Any]:
        """Get custom fields for a device.
        
        Args:
            device_id: NinjaOne device ID.
            
        Returns:
            Dictionary of custom field values.
        """
        response = await self.get(f"/v2/device/{device_id}/custom-fields")
        return response.json()
    
    async def search_devices(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search for devices by query string.
        
        Args:
            query: Search query.
            limit: Maximum results to return.
            
        Returns:
            List of matching devices.
        """
        params = {
            "q": query,
            "limit": limit,
        }
        
        response = await self.get("/v2/devices/search", params=params)
        data = response.json()
        return data if isinstance(data, list) else data.get("devices", [])
    
    async def get_roles(self) -> list[dict[str, Any]]:
        """Get all device roles.
        
        Returns:
            List of role dictionaries with id, name, description.
        """
        response = await self.get("/v2/roles")
        data = response.json()
        return data if isinstance(data, list) else data.get("roles", [])
    
    async def get_devices_by_role(
        self,
        role_id: int,
        page_size: int = 100,
    ) -> AsyncIterator[dict[str, Any]]:
        """Get all devices with a specific role.
        
        Uses the device filter (df) parameter to filter by role.
        
        Args:
            role_id: NinjaOne device role ID.
            page_size: Number of devices per page.
            
        Yields:
            Device dictionaries matching the role.
        """
        device_filter = f"role = {role_id}"
        
        async for device in self.get_devices_detailed(
            page_size=page_size,
            device_filter=device_filter,
        ):
            yield device
    
    async def get_sample_device_by_role(
        self,
        role_id: int,
    ) -> dict[str, Any] | None:
        """Get a sample device with a specific role.
        
        Useful for testing mappings.
        
        Args:
            role_id: NinjaOne device role ID.
            
        Returns:
            First device matching the role, or None if none found.
        """
        async for device in self.get_devices_by_role(role_id, page_size=1):
            return device
        return None


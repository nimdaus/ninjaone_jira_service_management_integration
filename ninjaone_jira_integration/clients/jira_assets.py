"""
Jira Service Management Assets API client.

Handles basic authentication and provides methods for:
- Workspace discovery
- Schema/object type introspection
- Object CRUD operations
- Issue CRUD operations
- Asset-issue linking
"""

from __future__ import annotations

import base64
import logging
from typing import Any
from urllib.parse import quote

from pydantic import SecretStr

from ninjaone_jira_integration.clients.base import (
    APIError,
    BaseClient,
    NotFoundError,
    RetryConfig,
)
from ninjaone_jira_integration.utils.concurrency import RateLimiter

logger = logging.getLogger(__name__)


class JiraAssetsClient(BaseClient):
    """Jira Service Management Assets API client.
    
    Uses basic authentication (email + API token) for Atlassian Cloud.
    
    Supports:
    - Assets API (object schemas, types, attributes, objects)
    - Jira REST API (issues)
    - AQL queries for asset search
    """
    
    def __init__(
        self,
        subdomain: str,
        email: str,
        api_token: SecretStr,
        workspace_id: str | None = None,
        retry_config: RetryConfig | None = None,
        rate_limiter: RateLimiter | None = None,
    ):
        """Initialize Jira Assets client.
        
        Args:
            subdomain: Jira subdomain (e.g., 'mycompany' for mycompany.atlassian.net).
            email: Jira account email.
            api_token: Jira API token.
            workspace_id: Assets workspace ID (will be discovered if not provided).
            retry_config: Retry configuration.
            rate_limiter: Optional rate limiter.
        """
        # Base URL for Jira Cloud
        base_url = f"https://{subdomain}.atlassian.net"
        
        super().__init__(
            base_url=base_url,
            retry_config=retry_config,
            rate_limiter=rate_limiter,
        )
        
        self.subdomain = subdomain
        self.email = email
        self.api_token = api_token
        self._workspace_id = workspace_id
        
        # Build auth header
        credentials = f"{email}:{api_token.get_secret_value()}"
        encoded = base64.b64encode(credentials.encode()).decode()
        self._auth_header = f"Basic {encoded}"
    
    @property
    def workspace_id(self) -> str | None:
        """Get the Assets workspace ID."""
        return self._workspace_id
    
    @property
    def assets_api_base(self) -> str:
        """Get the Assets API base URL."""
        if not self._workspace_id:
            raise ValueError("Workspace ID not set. Call discover_workspace() first.")
        return f"https://api.atlassian.com/jsm/assets/workspace/{self._workspace_id}/v1"
    
    async def _prepare_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        """Prepare request headers with authentication.
        
        Args:
            headers: Optional additional headers.
            
        Returns:
            Headers with Authorization header.
        """
        result = await super()._prepare_headers(headers)
        result["Authorization"] = self._auth_header
        return result
    
    async def test_connection(self) -> bool:
        """Test the API connection.
        
        Returns:
            True if connection is successful.
        """
        try:
            response = await self.get("/rest/api/3/myself")
            return response.status_code == 200
        except APIError:
            return False
    
    async def discover_workspace(self) -> str:
        """Discover the Assets workspace ID.
        
        Returns:
            Workspace ID.
            
        Raises:
            APIError: If discovery fails.
        """
        logger.info("Discovering Jira Assets workspace ID")
        
        response = await self.get("/rest/servicedeskapi/assets/workspace")
        data = response.json()
        
        # Response contains workspace info
        values = data.get("values", [])
        if not values:
            raise APIError("No Assets workspace found")
        
        workspace = values[0]
        self._workspace_id = workspace.get("workspaceId")
        
        if not self._workspace_id:
            raise APIError("Could not determine workspace ID")
        
        logger.info("Discovered workspace ID: %s", self._workspace_id)
        return self._workspace_id
    
    # =========================================================================
    # Schema and Object Type Introspection
    # =========================================================================
    
    async def list_schemas(self) -> list[dict[str, Any]]:
        """List all object schemas.
        
        Returns:
            List of object schema dictionaries.
        """
        # Use the Assets API endpoint
        url = f"{self.assets_api_base}/objectschema/list"
        
        # Need to use external API base
        client = await self._get_client()
        headers = await self._prepare_headers(None)
        
        response = await client.get(url, headers=headers)
        
        if response.status_code != 200:
            raise APIError(
                f"Failed to list schemas: {response.status_code}",
                status_code=response.status_code,
                response_body=response.text,
            )
        
        data = response.json()
        return data.get("values", data) if isinstance(data, dict) else data
    
    async def get_schema(self, schema_id: str) -> dict[str, Any]:
        """Get a specific object schema.
        
        Args:
            schema_id: Object schema ID.
            
        Returns:
            Schema details dictionary.
        """
        url = f"{self.assets_api_base}/objectschema/{schema_id}"
        
        client = await self._get_client()
        headers = await self._prepare_headers(None)
        
        response = await client.get(url, headers=headers)
        
        if response.status_code == 404:
            raise NotFoundError(f"Schema not found: {schema_id}")
        
        if response.status_code != 200:
            raise APIError(
                f"Failed to get schema: {response.status_code}",
                status_code=response.status_code,
            )
        
        return response.json()
    
    async def list_object_types(self, schema_id: str) -> list[dict[str, Any]]:
        """List object types in a schema.
        
        Args:
            schema_id: Object schema ID.
            
        Returns:
            List of object type dictionaries.
        """
        url = f"{self.assets_api_base}/objectschema/{schema_id}/objecttypes/flat"
        
        client = await self._get_client()
        headers = await self._prepare_headers(None)
        
        response = await client.get(url, headers=headers)
        
        if response.status_code != 200:
            raise APIError(
                f"Failed to list object types: {response.status_code}",
                status_code=response.status_code,
            )
        
        data = response.json()
        return data if isinstance(data, list) else data.get("values", [])
    
    async def get_object_type(self, object_type_id: str) -> dict[str, Any]:
        """Get a specific object type.
        
        Args:
            object_type_id: Object type ID.
            
        Returns:
            Object type details dictionary.
        """
        url = f"{self.assets_api_base}/objecttype/{object_type_id}"
        
        client = await self._get_client()
        headers = await self._prepare_headers(None)
        
        response = await client.get(url, headers=headers)
        
        if response.status_code == 404:
            raise NotFoundError(f"Object type not found: {object_type_id}")
        
        if response.status_code != 200:
            raise APIError(
                f"Failed to get object type: {response.status_code}",
                status_code=response.status_code,
            )
        
        return response.json()
    
    async def get_object_type_attributes(
        self,
        object_type_id: str,
    ) -> list[dict[str, Any]]:
        """Get attributes for an object type.
        
        Returns attribute definitions including:
        - id: Attribute ID
        - name: Attribute name
        - type: Attribute type (Default, Integer, Boolean, etc.)
        - required: Whether the attribute is required
        - defaultType: Default type configuration
        - options: Allowed values for select/enum types
        
        Args:
            object_type_id: Object type ID.
            
        Returns:
            List of attribute definition dictionaries.
        """
        url = f"{self.assets_api_base}/objecttype/{object_type_id}/attributes"
        
        client = await self._get_client()
        headers = await self._prepare_headers(None)
        
        response = await client.get(url, headers=headers)
        
        if response.status_code != 200:
            raise APIError(
                f"Failed to get object type attributes: {response.status_code}",
                status_code=response.status_code,
            )
        
        data = response.json()
        return data if isinstance(data, list) else data.get("values", [])
    
    async def create_object_type_attribute(
        self,
        object_type_id: str,
        name: str,
        attribute_type: str = "Default",
        description: str = "",
        default_value: str | None = None,
    ) -> dict[str, Any]:
        """Create a new attribute on an object type.
        
        Args:
            object_type_id: Object type ID.
            name: Attribute name.
            attribute_type: Attribute type (Default, Integer, Boolean, etc.).
            description: Optional attribute description.
            default_value: Optional default value.
            
        Returns:
            Created attribute dictionary with id and name.
        """
        url = f"{self.assets_api_base}/objecttypeattribute/{object_type_id}"
        
        # Map attribute type to Jira type IDs (as strings per API docs)
        # See: https://developer.atlassian.com/cloud/assets/rest/api-group-objecttypeattribute/
        type_mapping = {
            "Default": "0",  # Text
            "Integer": "1",
            "Boolean": "2",
            "Double": "3",
            "Date": "4",
            "DateTime": "5",
            "URL": "6",
            "Email": "7",
            "Textarea": "8",
            "Select": "9",
            "IP Address": "10",
        }
        
        type_id = type_mapping.get(attribute_type, "0")
        
        # Payload format per Jira API documentation
        # Note: type and defaultTypeId must be strings
        payload: dict[str, Any] = {
            "name": name,
            "type": type_id,
            "defaultTypeId": "0",  # Default to Text type
        }
        
        # Add optional fields only if they have values
        if description:
            payload["description"] = description
        
        logger.debug("Creating attribute with payload: %s at URL: %s", payload, url)
        
        client = await self._get_client()
        headers = await self._prepare_headers(None)
        
        response = await client.post(url, json=payload, headers=headers)
        
        logger.debug("Create attribute response: %s - %s", response.status_code, response.text)
        
        if response.status_code not in (200, 201):
            raise APIError(
                f"Failed to create attribute: {response.status_code}",
                status_code=response.status_code,
                response_body=response.text,
            )
        
        logger.info(
            "Created attribute '%s' on object type %s",
            name,
            object_type_id,
        )
        
        return response.json()
    
    # =========================================================================
    # Object Operations
    # =========================================================================
    
    async def search_objects(
        self,
        aql: str,
        object_type_id: str | None = None,
        max_results: int = 50,
        include_attributes: bool = True,
    ) -> list[dict[str, Any]]:
        """Search for objects using AQL (Assets Query Language).
        
        Args:
            aql: AQL query string.
            object_type_id: Optional object type ID to limit search.
            max_results: Maximum number of results.
            include_attributes: Whether to include attribute values.
            
        Returns:
            List of matching object dictionaries.
        """
        url = f"{self.assets_api_base}/object/aql"
        
        payload = {
            "qlQuery": aql,
            "maxResults": max_results,
            "includeAttributes": include_attributes,
        }
        
        if object_type_id:
            payload["objectTypeId"] = object_type_id
        
        client = await self._get_client()
        headers = await self._prepare_headers(None)
        
        response = await client.post(url, json=payload, headers=headers)
        
        if response.status_code != 200:
            raise APIError(
                f"AQL search failed: {response.status_code}",
                status_code=response.status_code,
                response_body=response.text,
            )
        
        data = response.json()
        return data.get("values", []) if isinstance(data, dict) else data
    
    async def get_object(
        self,
        object_id: str,
        include_attributes: bool = True,
    ) -> dict[str, Any]:
        """Get a single object by ID.
        
        Args:
            object_id: Object ID.
            include_attributes: Whether to include attribute values.
            
        Returns:
            Object dictionary.
        """
        url = f"{self.assets_api_base}/object/{object_id}"
        
        params = {}
        if include_attributes:
            params["includeAttributes"] = "true"
        
        client = await self._get_client()
        headers = await self._prepare_headers(None)
        
        response = await client.get(url, params=params, headers=headers)
        
        if response.status_code == 404:
            raise NotFoundError(f"Object not found: {object_id}")
        
        if response.status_code != 200:
            raise APIError(
                f"Failed to get object: {response.status_code}",
                status_code=response.status_code,
            )
        
        return response.json()
    
    async def create_object(
        self,
        object_type_id: str,
        attributes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create a new object.
        
        Args:
            object_type_id: Object type ID.
            attributes: List of attribute values in format:
                [{"objectTypeAttributeId": "123", "objectAttributeValues": [{"value": "..."}]}]
            
        Returns:
            Created object dictionary.
        """
        url = f"{self.assets_api_base}/object/create"
        
        payload = {
            "objectTypeId": object_type_id,
            "attributes": attributes,
        }
        
        client = await self._get_client()
        headers = await self._prepare_headers(None)
        
        response = await client.post(url, json=payload, headers=headers)
        
        if response.status_code not in (200, 201):
            raise APIError(
                f"Failed to create object: {response.status_code}",
                status_code=response.status_code,
                response_body=response.text,
            )
        
        return response.json()
    
    async def update_object(
        self,
        object_id: str,
        attributes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Update an existing object.
        
        Args:
            object_id: Object ID to update.
            attributes: List of attribute values to update.
            
        Returns:
            Updated object dictionary.
        """
        url = f"{self.assets_api_base}/object/{object_id}"
        
        payload = {
            "attributes": attributes,
        }
        
        client = await self._get_client()
        headers = await self._prepare_headers(None)
        
        response = await client.put(url, json=payload, headers=headers)
        
        if response.status_code == 404:
            raise NotFoundError(f"Object not found: {object_id}")
        
        if response.status_code != 200:
            raise APIError(
                f"Failed to update object: {response.status_code}",
                status_code=response.status_code,
                response_body=response.text,
            )
        
        return response.json()
    
    async def find_object_by_attribute(
        self,
        object_type_id: str,
        attribute_name: str,
        value: str,
    ) -> dict[str, Any] | None:
        """Find an object by a specific attribute value.
        
        Args:
            object_type_id: Object type ID.
            attribute_name: Attribute name to search.
            value: Value to match.
            
        Returns:
            Object dictionary if found, None otherwise.
        """
        # Escape the value for AQL
        escaped_value = value.replace('"', '\\"')
        aql = f'objectType = "{object_type_id}" AND "{attribute_name}" = "{escaped_value}"'
        
        results = await self.search_objects(aql, max_results=1)
        
        return results[0] if results else None
    
    # =========================================================================
    # Issue Operations
    # =========================================================================
    
    async def get_projects(self) -> list[dict[str, Any]]:
        """Get all projects.
        
        Returns:
            List of project dictionaries.
        """
        response = await self.get("/rest/api/3/project")
        return response.json()
    
    async def get_project(self, project_key: str) -> dict[str, Any]:
        """Get a specific project.
        
        Args:
            project_key: Project key (e.g., 'PROJ').
            
        Returns:
            Project dictionary.
        """
        response = await self.get(f"/rest/api/3/project/{project_key}")
        return response.json()
    
    async def get_issue_types(self, project_key: str) -> list[dict[str, Any]]:
        """Get issue types for a project.
        
        Args:
            project_key: Project key.
            
        Returns:
            List of issue type dictionaries.
        """
        response = await self.get(f"/rest/api/3/project/{project_key}")
        project = response.json()
        return project.get("issueTypes", [])
    
    async def get_issue_create_metadata(
        self,
        project_key: str,
        issue_type_id: str | None = None,
    ) -> dict[str, Any]:
        """Get metadata for creating issues.
        
        Returns information about required and available fields.
        
        Args:
            project_key: Project key.
            issue_type_id: Optional issue type ID to get specific metadata.
            
        Returns:
            Create metadata dictionary.
        """
        params: dict[str, str] = {
            "projectKeys": project_key,
            "expand": "projects.issuetypes.fields",
        }
        
        if issue_type_id:
            params["issuetypeIds"] = issue_type_id
        
        response = await self.get("/rest/api/3/issue/createmeta", params=params)
        return response.json()
    
    async def create_issue(
        self,
        project_key: str,
        issue_type_id: str,
        summary: str,
        description: str | dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new issue.
        
        Args:
            project_key: Project key.
            issue_type_id: Issue type ID.
            summary: Issue summary.
            description: Issue description (string or ADF format).
            fields: Additional fields to set.
            
        Returns:
            Created issue dictionary with key and id.
        """
        payload: dict[str, Any] = {
            "fields": {
                "project": {"key": project_key},
                "issuetype": {"id": issue_type_id},
                "summary": summary,
            }
        }
        
        if description:
            if isinstance(description, str):
                # Convert to Atlassian Document Format (ADF)
                payload["fields"]["description"] = {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": description}
                            ]
                        }
                    ]
                }
            else:
                payload["fields"]["description"] = description
        
        if fields:
            payload["fields"].update(fields)
        
        response = await self.post("/rest/api/3/issue", json=payload)
        
        if response.status_code not in (200, 201):
            raise APIError(
                f"Failed to create issue: {response.status_code}",
                status_code=response.status_code,
                response_body=response.text,
            )
        
        return response.json()
    
    async def update_issue(
        self,
        issue_key: str,
        fields: dict[str, Any],
    ) -> None:
        """Update an existing issue.
        
        Args:
            issue_key: Issue key (e.g., 'PROJ-123').
            fields: Fields to update.
        """
        payload = {"fields": fields}
        
        response = await self.put(f"/rest/api/3/issue/{issue_key}", json=payload)
        
        if response.status_code == 404:
            raise NotFoundError(f"Issue not found: {issue_key}")
        
        if response.status_code not in (200, 204):
            raise APIError(
                f"Failed to update issue: {response.status_code}",
                status_code=response.status_code,
                response_body=response.text,
            )
    
    async def get_issue(self, issue_key: str) -> dict[str, Any]:
        """Get an issue by key.
        
        Args:
            issue_key: Issue key (e.g., 'PROJ-123').
            
        Returns:
            Issue dictionary.
        """
        response = await self.get(f"/rest/api/3/issue/{issue_key}")
        
        if response.status_code == 404:
            raise NotFoundError(f"Issue not found: {issue_key}")
        
        return response.json()
    
    async def add_comment(
        self,
        issue_key: str,
        body: str,
    ) -> dict[str, Any]:
        """Add a comment to an issue.
        
        Args:
            issue_key: Issue key.
            body: Comment body text.
            
        Returns:
            Comment dictionary.
        """
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": body}
                        ]
                    }
                ]
            }
        }
        
        response = await self.post(
            f"/rest/api/3/issue/{issue_key}/comment",
            json=payload,
        )
        
        return response.json()
    
    async def link_asset_to_issue(
        self,
        issue_key: str,
        asset_id: str,
        custom_field_id: str,
    ) -> None:
        """Link an asset to an issue via a custom field.
        
        Args:
            issue_key: Issue key.
            asset_id: Asset object ID.
            custom_field_id: Custom field ID for asset reference.
        """
        # The format depends on how the asset field is configured
        # Typically it's an array of object keys/IDs
        fields = {
            custom_field_id: [{"key": asset_id}]
        }
        
        await self.update_issue(issue_key, fields)
    
    # =========================================================================
    # Utility Methods
    # =========================================================================
    
    async def get_myself(self) -> dict[str, Any]:
        """Get the current user information.
        
        Returns:
            User dictionary.
        """
        response = await self.get("/rest/api/3/myself")
        return response.json()
    
    @staticmethod
    def escape_aql_value(value: str) -> str:
        """Escape a value for use in AQL queries.
        
        Args:
            value: Value to escape.
            
        Returns:
            Escaped value.
        """
        # Escape special characters
        return value.replace("\\", "\\\\").replace('"', '\\"')
    
    @staticmethod
    def normalize_serial_number(serial: str | None) -> str | None:
        """Normalize a serial number for consistent matching.
        
        Args:
            serial: Serial number to normalize.
            
        Returns:
            Normalized serial number, or None if input is None/empty.
        """
        if not serial:
            return None
        
        # Strip whitespace and convert to uppercase
        normalized = serial.strip().upper()
        
        # Remove common filler patterns
        if normalized in ("NONE", "N/A", "NA", "UNKNOWN", "NOT SPECIFIED", "TBD"):
            return None
        
        return normalized if normalized else None

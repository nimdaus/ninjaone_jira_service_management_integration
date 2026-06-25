"""
Identity resolution for device-to-asset matching.

Implements the matching strategy:
1. Check persisted mapping (ninja_device_id -> jira_asset_id)
2. Try identity attributes in priority order
3. Create new if not found

Handles edge cases like missing values and duplicates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ninjaone_jira_integration.clients.jira_assets import JiraAssetsClient
from ninjaone_jira_integration.config.models import AttributeMapping, JiraAssetsConfig
from ninjaone_jira_integration.store.mappings import DeviceMapping, MappingStore
from ninjaone_jira_integration.sync.mapper import DeviceMapper

logger = logging.getLogger(__name__)


class MatchMethod(str, Enum):
    """How the asset was matched."""
    
    PERSISTED_MAPPING = "persisted_mapping"
    IDENTITY_ATTRIBUTE = "identity_attribute"  # Matched via identity attribute
    CREATED = "created"
    NOT_FOUND = "not_found"


@dataclass
class MatchResult:
    """Result of identity resolution."""
    
    method: MatchMethod
    jira_asset_id: str | None
    jira_asset_key: str | None
    existing_asset: dict[str, Any] | None = None
    warning: str | None = None
    matched_by: str | None = None  # Name of attribute that matched
    
    @property
    def found(self) -> bool:
        """Whether an existing asset was found."""
        return self.method in (MatchMethod.PERSISTED_MAPPING, MatchMethod.IDENTITY_ATTRIBUTE)
    
    @property
    def needs_create(self) -> bool:
        """Whether a new asset needs to be created."""
        return self.method == MatchMethod.NOT_FOUND



class IdentityResolver:
    """Resolves NinjaOne devices to Jira assets.
    
    Uses a multi-step matching strategy with fallback and
    handles edge cases gracefully.
    """
    
    def __init__(
        self,
        jira_client: JiraAssetsClient,
        mapping_store: MappingStore,
        config: JiraAssetsConfig,
    ):
        """Initialize identity resolver.
        
        Args:
            jira_client: Jira Assets API client.
            mapping_store: Persistent mapping store.
            config: Jira Assets configuration.
        """
        self.jira_client = jira_client
        self.mapping_store = mapping_store
        self.config = config
        self.mapper = DeviceMapper(config)
    
    async def resolve(
        self,
        device_id: int,
        device: dict[str, Any],
    ) -> MatchResult:
        """Resolve a NinjaOne device to a Jira asset.
        
        Matching strategy:
        1. Check if we have a persisted mapping for this device
        2. Get role-specific mapping and try identity attributes in order
        3. Return not found (caller should create)
        
        Args:
            device_id: NinjaOne device ID.
            device: NinjaOne device data.
            
        Returns:
            MatchResult indicating how/if the asset was found.
        """
        # Step 1: Check persisted mapping
        mapping = await self.mapping_store.get_device_mapping(device_id)
        if mapping:
            logger.debug(
                "Found persisted mapping for device %d -> asset %s",
                device_id,
                mapping.jira_asset_id,
            )
            
            # Verify the asset still exists
            try:
                asset = await self.jira_client.get_object(mapping.jira_asset_id)
                return MatchResult(
                    method=MatchMethod.PERSISTED_MAPPING,
                    jira_asset_id=mapping.jira_asset_id,
                    jira_asset_key=asset.get("objectKey"),
                    existing_asset=asset,
                )
            except Exception as e:
                logger.warning(
                    "Persisted asset %s not found, will try identity attributes: %s",
                    mapping.jira_asset_id,
                    str(e),
                )
                # Fall through to identity attribute matching
        
        # Step 2: Get role-specific mapping
        role_id = device.get("nodeRoleId")
        role_mapping = None
        object_type_id = self.config.object_type_id  # Legacy fallback
        
        if role_id is not None:
            role_mapping = self.config.get_mapping_for_role(role_id)
            if role_mapping:
                object_type_id = role_mapping.jira_object_type_id
            elif self.config.has_role_mappings():
                # Role mappings exist but none match this device
                logger.debug("No mapping found for role %d, cannot match", role_id)
                return MatchResult(
                    method=MatchMethod.NOT_FOUND,
                    jira_asset_id=None,
                    jira_asset_key=None,
                    warning=f"No mapping configured for role {role_id}",
                )

        if not object_type_id:
            return MatchResult(
                method=MatchMethod.NOT_FOUND,
                jira_asset_id=None,
                jira_asset_key=None,
                warning="No object type configured",
            )
        
        # Step 3: Try identity attributes in priority order
        if role_mapping:
            identity_attrs = role_mapping.get_identity_attributes()
            
            for identity_attr in identity_attrs:
                result = await self._match_by_attribute(
                    device=device,
                    device_id=device_id,
                    object_type_id=object_type_id,
                    identity_attr=identity_attr,
                )
                if result.found:
                    return result
            
            if not identity_attrs:
                logger.debug(
                    "No identity attributes configured for role %d",
                    role_id,
                )
        else:
            logger.debug("No role mapping, cannot search by identity attributes")
        
        # Step 4: Not found
        return MatchResult(
            method=MatchMethod.NOT_FOUND,
            jira_asset_id=None,
            jira_asset_key=None,
        )
    
    def _extract_device_value(
        self,
        device: dict[str, Any],
        source: str,
    ) -> str | None:
        """Extract a value from device data using a dotted path.
        
        Args:
            device: NinjaOne device data.
            source: Dotted path like 'system.serialNumber' or 'id'.
            
        Returns:
            String value or None if not found/empty.
        """
        parts = source.split(".")
        value = device
        
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
            
            if value is None:
                return None
        
        # Convert to string and strip
        if value is not None:
            str_value = str(value).strip()
            return str_value if str_value else None
        
        return None
    
    async def _match_by_attribute(
        self,
        device: dict[str, Any],
        device_id: int,
        object_type_id: str,
        identity_attr: AttributeMapping,
    ) -> MatchResult:
        """Try to match device to asset using a specific identity attribute.
        
        Args:
            device: NinjaOne device data.
            device_id: NinjaOne device ID for logging.
            object_type_id: Jira object type ID to search in.
            identity_attr: The identity attribute mapping to use.
            
        Returns:
            MatchResult.
        """
        # Extract value from device
        value = self._extract_device_value(device, identity_attr.source)
        
        if not value:
            logger.debug(
                "Device %d has no value for identity attribute '%s' (source: %s)",
                device_id,
                identity_attr.jira_attribute_name,
                identity_attr.source,
            )
            return MatchResult(
                method=MatchMethod.NOT_FOUND,
                jira_asset_id=None,
                jira_asset_key=None,
            )
        
        # Build AQL query using attribute NAME
        attr_name = identity_attr.jira_attribute_name
        if not attr_name:
            logger.warning(
                "Identity attribute has no name, cannot search (id: %s)",
                identity_attr.jira_attribute_id,
            )
            return MatchResult(
                method=MatchMethod.NOT_FOUND,
                jira_asset_id=None,
                jira_asset_key=None,
                warning="Identity attribute has no name configured",
            )
        
        try:
            escaped_value = self.jira_client.escape_aql_value(value)
            aql = f'objectType = "{object_type_id}" AND "{attr_name}" = "{escaped_value}"'
            
            logger.debug("Searching with AQL: %s", aql)
            
            results = await self.jira_client.search_objects(
                aql=aql,
                max_results=5,  # Get a few to detect duplicates
            )
            
            if not results:
                logger.debug(
                    "No asset found for %s='%s' (device %d)",
                    attr_name,
                    value,
                    device_id,
                )
                return MatchResult(
                    method=MatchMethod.NOT_FOUND,
                    jira_asset_id=None,
                    jira_asset_key=None,
                )
            
            if len(results) > 1:
                # Multiple assets - log warning and use first
                logger.warning(
                    "Multiple assets (%d) found with %s='%s', using first match",
                    len(results),
                    attr_name,
                    value,
                )
                return MatchResult(
                    method=MatchMethod.IDENTITY_ATTRIBUTE,
                    jira_asset_id=str(results[0]["id"]),
                    jira_asset_key=results[0].get("objectKey"),
                    existing_asset=results[0],
                    matched_by=attr_name,
                    warning=f"Multiple assets ({len(results)}) with same {attr_name}",
                )
            
            # Single match - success
            asset = results[0]
            logger.info(
                "Matched device %d to asset %s by %s='%s'",
                device_id,
                asset.get("objectKey"),
                attr_name,
                value,
            )
            
            return MatchResult(
                method=MatchMethod.IDENTITY_ATTRIBUTE,
                jira_asset_id=str(asset["id"]),
                jira_asset_key=asset.get("objectKey"),
                existing_asset=asset,
                matched_by=attr_name,
            )
            
        except Exception as e:
            logger.error(
                "Error searching for asset by %s='%s': %s",
                attr_name,
                value,
                str(e),
            )
            return MatchResult(
                method=MatchMethod.NOT_FOUND,
                jira_asset_id=None,
                jira_asset_key=None,
                warning=f"Search error: {str(e)}",
            )
    
    async def create_and_map(
        self,
        device_id: int,
        device: dict[str, Any],
        dry_run: bool = False,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Create a new asset and persist the mapping.
        
        Args:
            device_id: NinjaOne device ID.
            device: NinjaOne device data.
            dry_run: If True, don't actually create.
            
        Returns:
            Tuple of (asset_id, asset_data) or (None, None) on failure.
        """
        # Map device to attributes (role-aware via DeviceMapper)
        attributes = self.mapper.map_device(device)

        if not attributes:
            logger.warning(
                "No attributes mapped for device %d, cannot create asset",
                device_id,
            )
            return None, None

        if dry_run:
            logger.info("[DRY RUN] Would create asset for device %d", device_id)
            return None, None

        # Resolve the correct object type for this device's role
        role_id = device.get("nodeRoleId")
        role_mapping = self.config.get_mapping_for_role(role_id) if role_id is not None else None
        object_type_id = role_mapping.jira_object_type_id if role_mapping else self.config.object_type_id

        try:
            # Create the asset
            asset = await self.jira_client.create_object(
                object_type_id=object_type_id,
                attributes=attributes,
            )
            
            asset_id = str(asset["id"])
            asset_key = asset.get("objectKey")
            
            logger.info(
                "Created asset %s for device %d",
                asset_key,
                device_id,
            )
            
            # Persist the mapping
            serial_number = self.mapper.extract_serial_number(device)
            mapping = DeviceMapping(
                ninja_device_id=device_id,
                jira_asset_id=asset_id,
                jira_asset_key=asset_key,
                serial_number=serial_number,
            )
            await self.mapping_store.upsert_device_mapping(mapping)
            
            return asset_id, asset
            
        except Exception as e:
            detail = getattr(e, "response_body", None)
            logger.error(
                "Failed to create asset for device %d: %s%s",
                device_id,
                str(e),
                f" — {detail}" if detail else "",
            )
            return None, None
    
    async def persist_mapping(
        self,
        device_id: int,
        device: dict[str, Any],
        jira_asset_id: str,
        jira_asset_key: str | None = None,
    ) -> None:
        """Persist a device-to-asset mapping.
        
        Call this after matching by serial number to remember
        the association for future syncs.
        
        Args:
            device_id: NinjaOne device ID.
            device: NinjaOne device data.
            jira_asset_id: Jira asset ID.
            jira_asset_key: Jira asset key.
        """
        serial_number = self.mapper.extract_serial_number(device)
        
        mapping = DeviceMapping(
            ninja_device_id=device_id,
            jira_asset_id=jira_asset_id,
            jira_asset_key=jira_asset_key,
            serial_number=serial_number,
        )
        
        await self.mapping_store.upsert_device_mapping(mapping)
        
        logger.debug(
            "Persisted mapping: device %d -> asset %s",
            device_id,
            jira_asset_id,
        )

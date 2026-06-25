"""
Pydantic configuration models for the integration service.

All configuration is strongly typed with validation. Secrets are stored
as SecretStr to prevent accidental logging.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator


class NinjaOneRegion(str, Enum):
    """Known NinjaOne API regions."""
    
    US = "https://app.ninjarmm.com"
    EU = "https://eu.ninjarmm.com"
    CA = "https://ca.ninjarmm.com"
    OC = "https://oc.ninjarmm.com"
    
    @classmethod
    def from_url(cls, url: str) -> "NinjaOneRegion | None":
        """Get region from URL, returns None if custom URL."""
        url = url.rstrip("/")
        for region in cls:
            if region.value == url:
                return region
        return None


class JiraAttributeType(str, Enum):
    """Jira Assets attribute types."""
    
    DEFAULT = "Default"  # Text
    BOOLEAN = "Boolean"
    INTEGER = "Integer"
    FLOAT = "Float"
    DATE = "Date"
    DATE_TIME = "DateTime"
    URL = "URL"
    EMAIL = "Email"
    TEXTAREA = "Textarea"
    SELECT = "Select"  # Enum/dropdown
    REFERENCE = "Reference"  # Link to another object
    USER = "User"
    GROUP = "Group"
    STATUS = "Status"


class NinjaOneConfig(BaseModel):
    """NinjaOne API configuration."""
    
    base_url: str = Field(
        default="https://app.ninjarmm.com",
        description="NinjaOne API base URL (region-specific)",
    )
    client_id: str = Field(
        default="",
        description="OAuth2 client ID",
    )
    client_secret: SecretStr = Field(
        default=SecretStr(""),
        description="OAuth2 client secret",
    )
    scopes: list[str] = Field(
        default=["monitoring", "management"],
        description="OAuth2 scopes to request",
    )
    
    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, v: str) -> str:
        """Remove trailing slash from base URL."""
        return v.rstrip("/")
    
    def is_configured(self) -> bool:
        """Check if NinjaOne credentials are configured."""
        return bool(self.client_id and self.client_secret.get_secret_value())


class JiraConfig(BaseModel):
    """Jira API configuration."""
    
    email: str = Field(
        default="",
        description="Jira account email address",
    )
    api_token: SecretStr = Field(
        default=SecretStr(""),
        description="Jira API token",
    )
    subdomain: str = Field(
        default="",
        description="Jira subdomain (e.g., 'mycompany' for mycompany.atlassian.net)",
    )
    workspace_id: str = Field(
        default="",
        description="Jira Assets workspace ID",
    )
    
    @property
    def base_url(self) -> str:
        """Get Jira base URL from subdomain."""
        return f"https://{self.subdomain}.atlassian.net"
    
    @property
    def assets_api_url(self) -> str:
        """Get Jira Assets API base URL."""
        return f"https://api.atlassian.com/jsm/assets/workspace/{self.workspace_id}/v1"
    
    def is_configured(self) -> bool:
        """Check if Jira credentials are configured."""
        return bool(
            self.email 
            and self.api_token.get_secret_value() 
            and self.subdomain
        )


class AttributeMapping(BaseModel):
    """Mapping from NinjaOne device field to Jira asset attribute."""
    
    jira_attribute_id: str = Field(
        description="Jira attribute ID",
    )
    jira_attribute_name: str = Field(
        default="",
        description="Jira attribute name (for display and AQL queries)",
    )
    jira_attribute_type: JiraAttributeType = Field(
        default=JiraAttributeType.DEFAULT,
        description="Jira attribute data type",
    )
    source: str = Field(
        description="NinjaOne field path (e.g., 'system.serialNumber', 'system.name')",
    )
    required: bool = Field(
        default=False,
        description="Whether this attribute is required in Jira",
    )
    default_value: Any | None = Field(
        default=None,
        description="Default value if source field is empty",
    )
    transform: str | None = Field(
        default=None,
        description="Optional transform expression (e.g., 'upper', 'lower', 'strip')",
    )
    allowed_values: list[str] | None = Field(
        default=None,
        description="Allowed values for enum/select attributes",
    )
    identity_order: int | None = Field(
        default=None,
        description="If set, this attribute is used for identity matching. "
                    "Lower numbers = higher priority (1 = try first, 2 = try second, etc.). "
                    "If None, attribute is synced but not used for matching.",
    )


class ObjectTypeMapping(BaseModel):
    """Maps a NinjaOne device role to a Jira Assets object type.
    
    Each mapping defines which NinjaOne devices (by role) should sync
    to which Jira object type, with role-specific attribute mappings.
    """
    
    # NinjaOne side
    ninja_role_id: int = Field(
        description="NinjaOne device role ID from /v2/roles",
    )
    ninja_role_name: str = Field(
        default="",
        description="Role name for display (e.g., 'Windows Workstation')",
    )
    
    # Jira side
    jira_object_type_id: str = Field(
        description="Jira Assets object type ID",
    )
    jira_object_type_name: str = Field(
        default="",
        description="Object type name for display (e.g., 'Workstation')",
    )
    
    # Role-specific attribute mappings
    attribute_mappings: list[AttributeMapping] = Field(
        default_factory=list,
        description="Attribute mappings for this role→type pair",
    )
    
    # Optional: enable/disable this mapping
    enabled: bool = Field(
        default=True,
        description="Whether this mapping is active",
    )
    
    def get_identity_attributes(self) -> list[AttributeMapping]:
        """Get identity attributes sorted by priority order.
        
        Returns attribute mappings that have identity_order set,
        sorted by identity_order (lowest first = highest priority).
        """
        identity_attrs = [
            m for m in self.attribute_mappings 
            if m.identity_order is not None
        ]
        return sorted(identity_attrs, key=lambda m: m.identity_order)



class JiraAssetsConfig(BaseModel):
    """Jira Assets object configuration.
    
    Supports multiple role→type mappings via `object_type_mappings`.
    Legacy single-type config (`object_type_id`, `attribute_mappings`)
    is still supported for backwards compatibility.
    """
    
    schema_id: str = Field(
        default="",
        description="Object schema ID",
    )
    schema_name: str = Field(
        default="",
        description="Object schema name (for display)",
    )
    
    # NEW: Multiple role→type mappings
    object_type_mappings: list[ObjectTypeMapping] = Field(
        default_factory=list,
        description="Role-to-object-type mappings with attribute configs",
    )
    
    # LEGACY: Single type config (used if object_type_mappings is empty)
    object_type_id: str = Field(
        default="",
        description="[Legacy] Object type ID (use object_type_mappings instead)",
    )
    object_type_name: str = Field(
        default="",
        description="[Legacy] Object type name (for display)",
    )
    attribute_mappings: list[AttributeMapping] = Field(
        default_factory=list,
        description="[Legacy] Attribute mappings (use object_type_mappings instead)",
    )
    name_attribute_id: str = Field(
        default="",
        description="[Legacy] Name attribute ID",
    )
    serial_number_attribute_id: str = Field(
        default="",
        description="[Legacy] Serial number attribute ID",
    )
    
    def get_mapping_for_role(self, role_id: int) -> ObjectTypeMapping | None:
        """Get the object type mapping for a specific role."""
        for mapping in self.object_type_mappings:
            if mapping.ninja_role_id == role_id and mapping.enabled:
                return mapping
        return None
    
    def get_all_enabled_mappings(self) -> list[ObjectTypeMapping]:
        """Get all enabled object type mappings."""
        return [m for m in self.object_type_mappings if m.enabled]
    
    def has_role_mappings(self) -> bool:
        """Check if role-based mappings are configured."""
        return len(self.object_type_mappings) > 0


class FieldMapping(BaseModel):
    """Mapping for Jira issue fields."""
    
    jira_field_id: str = Field(
        description="Jira field ID (e.g., 'summary', 'description', 'customfield_10001')",
    )
    jira_field_name: str = Field(
        description="Jira field name (for display)",
    )
    source: str | None = Field(
        default=None,
        description="NinjaOne alert field path, or None if using static value",
    )
    static_value: Any | None = Field(
        default=None,
        description="Static value to use if source is None",
    )
    template: str | None = Field(
        default=None,
        description="Template string with {placeholders} for dynamic values",
    )
    required: bool = Field(
        default=False,
        description="Whether this field is required",
    )


class JsmFieldMapping(BaseModel):
    """Maps NinjaOne severity or priority values to a Jira option field (Impact, Urgency, etc.)."""

    jira_field_id: str = Field(description="Jira field ID (e.g. customfield_10040)")
    jira_field_name: str = Field(default="", description="Field name for display")
    ninja_source: Literal["severity", "priority"] = Field(
        default="severity",
        description="Which NinjaOne alert field to map from",
    )
    value_map: dict[str, str] = Field(
        default_factory=dict,
        description="NinjaOne value → Jira option value (e.g. {'CRITICAL': 'Extensive / Widespread'})",
    )


class JiraIssueConfig(BaseModel):
    """Jira issue configuration for alert processing."""
    
    project_key: str = Field(
        default="",
        description="Jira project key",
    )
    issue_type_id: str = Field(
        default="",
        description="Issue type ID",
    )
    issue_type_name: str = Field(
        default="",
        description="Issue type name (for display)",
    )
    field_mappings: list[FieldMapping] = Field(
        default_factory=list,
        description="Field mappings for issue creation",
    )
    asset_field_id: str = Field(
        default="",
        description="Custom field ID for linking to Assets (if applicable)",
    )
    min_severity: str | None = Field(
        default=None,
        description="Minimum alert severity to create issues for (NONE, MINOR, MODERATE, MAJOR, CRITICAL)",
    )
    source_types: list[str] = Field(
        default_factory=list,
        description="Restrict issue creation to these NinjaOne sourceType values (empty = all)",
    )
    jsm_field_mappings: list[JsmFieldMapping] = Field(
        default_factory=list,
        description="Value mappings for JSM option fields (Impact, Urgency, Severity, Priority)",
    )

    # Default templates for issue fields
    summary_template: str = Field(
        default="[NinjaOne Alert] {alert.message} - {device.systemName}",
        description="Template for issue summary",
    )
    description_template: str = Field(
        default="""
*NinjaOne Alert Details*

||Property||Value||
|Alert ID|{alert.id}|
|Severity|{alert.severity}|
|Message|{alert.message}|
|Device|{device.systemName}|
|Triggered At|{alert.createTime}|
""",
        description="Template for issue description",
    )
    resolve_transition_id: str | None = Field(
        default=None,
        description="Jira transition ID to apply when a NinjaOne alert is no longer active (resolved)",
    )
    resolve_comment: str = Field(
        default="NinjaOne alert resolved — this issue was automatically transitioned.",
        description="Comment to post on the Jira issue when the alert resolves",
    )
    retrigger_behavior: Literal["reopen", "new_issue"] = Field(
        default="new_issue",
        description="What to do when a resolved NinjaOne alert reappears: reopen the existing issue or create a new one",
    )
    reopen_transition_id: str | None = Field(
        default=None,
        description="Jira transition ID to apply when reopening an issue for a retriggered alert (retrigger_behavior=reopen)",
    )
    reopen_comment: str = Field(
        default="NinjaOne alert retriggered — condition re-activated.",
        description="Comment to post when an issue is reopened for a retriggered alert",
    )
    resolve_target_status: str | None = Field(
        default=None,
        description="Target Jira status name to walk to when an alert resolves (walks multi-hop workflows)",
    )
    reopen_target_status: str | None = Field(
        default=None,
        description="Target Jira status name to walk to when reopening a retriggered alert",
    )


class ConcurrencyConfig(BaseModel):
    """Concurrency and rate limiting configuration."""
    
    max_workers: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Maximum number of worker threads/tasks",
    )
    max_in_flight_jira_requests: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Maximum concurrent Jira API requests",
    )
    jira_requests_per_minute: int | None = Field(
        default=None,
        ge=1,
        description="Soft limit on Jira requests per minute (token bucket)",
    )
    ninja_requests_per_minute: int | None = Field(
        default=None,
        ge=1,
        description="Soft limit on NinjaOne requests per minute",
    )


class HeartbeatConfig(BaseModel):
    """Push-based heartbeat configuration for external monitoring."""
    
    enabled: bool = Field(
        default=False,
        description="Whether heartbeat is enabled",
    )
    url: str | None = Field(
        default=None,
        description="Heartbeat endpoint URL",
    )
    interval_seconds: int = Field(
        default=60,
        ge=10,
        le=3600,
        description="Heartbeat interval in seconds",
    )
    timeout_seconds: int = Field(
        default=5,
        ge=1,
        le=30,
        description="Heartbeat request timeout",
    )
    token: SecretStr | None = Field(
        default=None,
        description="Optional token sent as X-Heartbeat-Token header",
    )
    notify_on_changes: bool = Field(
        default=True,
        description="Send a webhook notification after each sync/alert-poll run completes",
    )


class WebhookConfig(BaseModel):
    """Webhook security configuration."""
    
    secret: SecretStr = Field(
        default=SecretStr(""),
        description="Shared secret for webhook verification",
    )
    secret_header: str = Field(
        default="X-Webhook-Secret",
        description="Header name for webhook secret",
    )


class ServerConfig(BaseModel):
    """HTTP server configuration."""
    
    host: str = Field(
        default="127.0.0.1",
        description="Server bind address",
    )
    port: int = Field(
        default=8080,
        ge=1,
        le=65535,
        description="Server port",
    )
    webhook: WebhookConfig = Field(
        default_factory=WebhookConfig,
    )


class DatabaseConfig(BaseModel):
    """SQLite database configuration."""
    
    path: str = Field(
        default="data/integration.db",
        description="Path to SQLite database file",
    )
    wal_mode: bool = Field(
        default=True,
        description="Enable WAL mode for better concurrency",
    )


class RetryConfig(BaseModel):
    """Retry configuration for API calls and jobs."""
    
    max_retries: int = Field(
        default=8,
        ge=1,
        le=20,
        description="Maximum retry attempts",
    )
    base_delay_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Base delay for exponential backoff",
    )
    max_delay_seconds: float = Field(
        default=300.0,
        ge=1.0,
        le=3600.0,
        description="Maximum delay between retries",
    )
    jitter: bool = Field(
        default=True,
        description="Add random jitter to delays",
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""
    
    level: str = Field(
        default="INFO",
        description="Log level (DEBUG, INFO, WARNING, ERROR)",
    )
    format: str = Field(
        default="json",
        description="Log format (json or text)",
    )
    include_timestamp: bool = Field(
        default=True,
    )


class AppConfig(BaseModel):
    """Root application configuration."""
    
    ninjaone: NinjaOneConfig = Field(
        default_factory=NinjaOneConfig,
    )
    jira: JiraConfig = Field(
        default_factory=JiraConfig,
    )
    assets: JiraAssetsConfig = Field(
        default_factory=JiraAssetsConfig,
    )
    issues: JiraIssueConfig = Field(
        default_factory=JiraIssueConfig,
    )
    concurrency: ConcurrencyConfig = Field(
        default_factory=ConcurrencyConfig,
    )
    heartbeat: HeartbeatConfig = Field(
        default_factory=HeartbeatConfig,
    )
    server: ServerConfig = Field(
        default_factory=ServerConfig,
    )
    database: DatabaseConfig = Field(
        default_factory=DatabaseConfig,
    )
    retry: RetryConfig = Field(
        default_factory=RetryConfig,
    )
    logging: LoggingConfig = Field(
        default_factory=LoggingConfig,
    )
    
    def is_fully_configured(self) -> bool:
        """Check if all required configuration is present."""
        return (
            self.ninjaone.is_configured()
            and self.jira.is_configured()
            and self.assets.schema_id
            and self.assets.object_type_id
        )

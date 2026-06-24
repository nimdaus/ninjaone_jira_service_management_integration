# NinjaOne to Jira Service Management Assets Integration

A production-ready Python 3.11+ integration service that synchronizes NinjaOne devices into Jira Service Management Assets and creates Jira issues from NinjaOne condition alerts.

## Features

- **Flexible Operation Modes**
  - **`ninja-jira run`** — Scheduled polling (recommended for most users; no public server required)
  - **`ninja-jira run-server`** — HTTP server for webhook-based real-time updates
  - CLI for interactive configuration, one-shot syncs, and diagnostics

- **Device Synchronization**
  - Scheduled polling every N hours (default: 6h, configurable)
  - Full sync of all NinjaOne devices to Jira Assets
  - Single device sync for targeted updates
  - Role-based mapping: each NinjaOne device role → separate Jira object type
  - Smart matching: persisted ID mapping → identity attribute search → create new
  - Diff-based updates: only modifies changed attributes

- **Alert Processing**
  - Polls NinjaOne `/v2/alerts` every 5 minutes (configurable) — no webhook or public server required
  - Also supports real-time processing via NinjaOne webhooks (server mode)
  - Links issues to corresponding device assets; auto-syncs the device first if no asset exists yet
  - Configurable severity filtering, source type filtering, and priority mapping
  - Deduplication via persistent mapping — re-polling never creates duplicate issues

- **Enterprise Ready**
  - Resilient retry logic with exponential backoff and jitter
  - Respects `Retry-After` headers from rate limits
  - SQLite storage with WAL mode for durability
  - Dead-letter queue for failed jobs
  - Structured JSON logging with correlation IDs (console + optional file)
  - Health endpoints for container orchestration

## Installation

### From Source

```bash
# Clone repository
git clone https://github.com/yourorg/ninjaone-jira-integration.git
cd ninjaone-jira-integration

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows

# Install package
pip install -e .
```

### With Docker

```bash
docker build -t ninjaone-jira-integration .
```

## Quick Start

### 1. Configure Credentials

Create a `.env` file (or set environment variables):

```bash
# NinjaOne OAuth2 - Create API credentials in Administration > Apps
NINJA_CLIENT_ID=your-client-id
NINJA_CLIENT_SECRET=your-client-secret

# Jira API - Create token at https://id.atlassian.com/manage-profile/security/api-tokens
JIRA_API_TOKEN=your-api-token

# Optional: Webhook verification secret
WEBHOOK_SECRET=your-secret
```

### 2. Initialize Configuration

```bash
python -m ninjaone_jira_integration init --ui
```

This will:
- Test connections to both APIs
- Discover your Jira Assets workspace
- Create a `config.yaml` with your settings

### 3. Configure Attribute Mappings

Use `ninja-jira init --ui` to configure mappings interactively, or edit `config.yaml` directly. Mappings are role-based — each NinjaOne device role maps to a separate Jira object type:

```yaml
assets:
  schema_id: "1"

  object_type_mappings:
    - ninja_role_id: 101
      ninja_role_name: "Windows Workstation"
      jira_object_type_id: "200"
      jira_object_type_name: "Workstation"
      attribute_mappings:
        - jira_attribute_id: "123"
          jira_attribute_name: "Name"
          source: "systemName"
          required: true
          identity_order: 1

        - jira_attribute_id: "124"
          jira_attribute_name: "Serial Number"
          source: "system.serialNumber"
          transforms:
            - normalize_serial
          identity_order: 2

        - jira_attribute_id: "125"
          jira_attribute_name: "Operating System"
          source: "os.name"
```

### 4. Test Mappings

```bash
ninja-jira mapping-test
```

This fetches a sample device and shows how your mappings would transform the data.

### 5. Run a Dry Sync

```bash
ninja-jira sync-all --dry-run
```

Review the output to see what would be created/updated.

### 6. Start Scheduled Polling (Recommended)

```bash
# Run continuously — syncs every interval_hours (default: 6h)
ninja-jira run

# Run a single sync and exit (great for cron jobs or testing)
ninja-jira run --once

# Preview without making changes
ninja-jira run --once --dry-run
```

No public-facing server required. The integration polls NinjaOne directly on a schedule.

### 7. Start Webhook Server (Advanced / Optional)

For real-time updates via NinjaOne webhooks (requires a publicly accessible URL):

```bash
ninja-jira run-server
```

Configure NinjaOne to send webhooks to `https://your-server:8080/webhook/device` and `/webhook/alert`.
The server also runs the scheduled sync alongside the webhook listener.

## CLI Commands

| Command | Description |
|---------|-------------|
| `init` | Interactive configuration setup |
| `init --ui` | Browser-based configuration wizard |
| `mapping-test` | Test attribute mappings with a sample device |
| `sync-all` | Sync all devices immediately |
| `sync-all --dry-run` | Preview sync without making changes |
| `sync-device <id>` | Sync a specific device by ID |
| `run` | Start device sync (every `interval_hours`, default 6h) + alert polling (every `interval_minutes`, default 5m) |
| `run --once` | Run one device sync and one alert poll, then exit |
| `run --dry-run` | Start both schedulers in dry-run mode |
| `run-server` | Start HTTP server for webhooks + scheduled sync |
| `replay-dead-letter` | Requeue failed jobs for retry |
| `status` | Show statistics and queue status |

### Global Options

```
--config / -c    Path to config file (or set NINJA_JIRA_CONFIG env var)
--verbose / -v   Enable debug logging
--log-file       Write JSON logs to a file (or set NINJA_JIRA_LOG_FILE)
```

## Configuration

Configuration can be provided via:
1. CLI flags (highest priority)
2. Environment variables
3. `.env` file
4. `config.yaml` file (lowest priority)

### Config File Discovery

The CLI searches for `config.yaml` in this order:

1. Explicit `--config <path>` flag
2. `NINJA_JIRA_CONFIG` environment variable (absolute path)
3. Current directory and parent directories (walks up to `.git` / `pyproject.toml`)
4. `~/.config/ninja-jira/config.yaml`

### Environment Variables

| Variable | Description |
|----------|-------------|
| `NINJA_JIRA_CONFIG` | Absolute path to config file |
| `NINJA_CLIENT_ID` | NinjaOne OAuth2 client ID |
| `NINJA_CLIENT_SECRET` | NinjaOne OAuth2 client secret |
| `NINJA_BASE_URL` | NinjaOne API URL (default: `https://app.ninjarmm.com`) |
| `JIRA_SUBDOMAIN` | Jira subdomain (e.g., `mycompany`) |
| `JIRA_EMAIL` | Jira account email |
| `JIRA_API_TOKEN` | Jira API token |
| `JIRA_WORKSPACE_ID` | Jira Assets workspace ID |
| `WEBHOOK_SECRET` | Shared secret for webhook verification |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `NINJA_JIRA_LOG_FILE` | Path to write JSON log file |
| `NINJA_JIRA_SCHEDULE_ENABLED` | Enable/disable scheduled device sync (true/false) |
| `NINJA_JIRA_SCHEDULE_INTERVAL_HOURS` | Hours between device syncs |
| `NINJA_JIRA_ALERT_SCHEDULE_ENABLED` | Enable/disable alert polling (true/false) |
| `NINJA_JIRA_ALERT_SCHEDULE_INTERVAL_MINUTES` | Minutes between alert polls |

### Scheduled Sync

Configure how often the integration polls NinjaOne devices and alerts. The two schedules run independently on their own intervals:

```yaml
# Device sync — full reconciliation of all devices
schedule:
  enabled: true
  interval_hours: 6   # 0.1 to 168 hours

# Alert polling — creates Jira issues for active NinjaOne alerts
alert_schedule:
  enabled: true
  interval_minutes: 5   # 1 to 1440 minutes
```

Run as a daemon with `ninja-jira run` (starts both schedulers), or as a one-shot with `ninja-jira run --once`.

**Alert polling behaviour:**
- Fetches all currently active alerts from NinjaOne `/v2/alerts` every `interval_minutes`
- Creates a Jira issue for each alert that doesn't already have one (idempotent — duplicate issues are never created)
- If the alert's device has no Jira asset yet, a targeted device sync runs automatically before creating the issue, so the issue can be linked to the correct asset
- Resolved alerts (no longer in the active list) are not automatically closed in Jira; manage their lifecycle in Jira directly

### Attribute Mapping

Map NinjaOne device fields to Jira Assets attributes using dot notation:

- Simple fields: `systemName`, `displayName`
- Nested objects: `system.serialNumber`, `os.name`
- Arrays: `ipAddresses[0]`, `disks[0].size`

#### Transforms

Transforms are applied in order (a pipeline) before the value is written to Jira:

```yaml
object_type_mappings:
  - ninja_role_id: 101
    jira_object_type_id: "200"
    attribute_mappings:
      - jira_attribute_id: "124"
        jira_attribute_name: "Serial Number"
        source: "system.serialNumber"
        transforms:
          - strip
          - normalize_serial

      - jira_attribute_id: "130"
        jira_attribute_name: "RAM (GB)"
        source: "system.totalPhysicalMemory"  # also available as "memory.capacity"
        transforms:
          - bytes_to_gb
          - to_string
```

Available transforms:

| Transform | Description |
|-----------|-------------|
| `upper` | Convert to UPPERCASE |
| `lower` | Convert to lowercase |
| `strip` | Remove leading/trailing whitespace |
| `normalize_serial` | Uppercase + strip + filter common non-values |
| `to_string` | Convert to string |
| `to_integer` | Parse as integer (truncates decimals) |
| `to_float` | Parse as floating-point number |
| `to_boolean` | Parse truthy strings (`true`, `yes`, `1`, `on`) |
| `first_ip` | Extract first IPv4 address from a string or list |
| `first_mac` | Extract first MAC address |
| `bytes_to_gb` | Convert bytes to GB (rounded to 2 decimal places) |

### Log Files

Write logs to a file in addition to the console:

```bash
# CLI flag
ninja-jira --log-file logs/integration.log run

# Environment variable
NINJA_JIRA_LOG_FILE=logs/integration.log ninja-jira run
```

Log files are written in JSON format (structured logging). The console continues to use human-readable colored output.

### Alert to Issue Mapping

Configure how alerts become issues:

```yaml
issues:
  project_key: "HELPDESK"
  issue_type_id: "10001"
  summary_template: "[NinjaOne] {severity}: {device_name} - {message}"
  min_severity: "MODERATE"
  default_labels:
    - "ninjaone"
    - "auto-created"
  severity_to_priority_mapping:
    CRITICAL: "1"
    MAJOR: "2"
    MODERATE: "3"
```

## Docker Deployment

### Using Docker Compose

```yaml
# docker-compose.yaml
services:
  integration:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
      - ./config.yaml:/app/config.yaml:ro
    environment:
      - NINJA_CLIENT_ID=${NINJA_CLIENT_ID}
      - NINJA_CLIENT_SECRET=${NINJA_CLIENT_SECRET}
      - JIRA_API_TOKEN=${JIRA_API_TOKEN}
    restart: unless-stopped
```

```bash
docker-compose up -d
```

### Kubernetes

The server exposes health endpoints:
- `/healthz` - Liveness probe (always returns 200 if server is running)
- `/readyz` - Readiness probe (checks database and worker status)

## API Endpoints

When running in server mode:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/healthz` | GET | Liveness check |
| `/readyz` | GET | Readiness check with dependency status |
| `/status` | GET | Detailed statistics and metrics |
| `/webhook/device` | POST | Device update webhook |
| `/webhook/alert` | POST | Alert webhook |

## Architecture

```
┌─────────────────┐    ┌─────────────────┐
│    NinjaOne     │    │   Jira Cloud    │
│      API        │    │     APIs        │
└────────┬────────┘    └────────┬────────┘
         │                      │
         │  OAuth2 + REST       │  Basic Auth + REST
         │                      │
┌────────┴──────────────────────┴────────┐
│         Integration Service            │
├────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌───────┐ │
│  │   CLI    │  │  Server  │  │Worker │ │
│  └────┬─────┘  └────┬─────┘  └───┬───┘ │
│       │             │            │      │
│  ┌────┴─────────────┴────────────┴───┐ │
│  │          Sync Engine              │ │
│  │   • Device Mapper                 │ │
│  │   • Identity Resolver             │ │
│  │   • Diff Computer                 │ │
│  └───────────────┬───────────────────┘ │
│                  │                      │
│  ┌───────────────┴───────────────────┐ │
│  │      SQLite (WAL mode)            │ │
│  │   • Device Mappings               │ │
│  │   • Alert Mappings                │ │
│  │   • Job Queue                     │ │
│  └───────────────────────────────────┘ │
└────────────────────────────────────────┘
```

## Development

### Setup

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run linting
ruff check .

# Run type checking
mypy ninjaone_jira_integration

# Run tests
pytest
```

### Project Structure

```
ninjaone_jira_integration/
├── __init__.py          # Package metadata
├── __main__.py          # Entry point
├── cli/                 # CLI commands
├── clients/             # API clients (NinjaOne, Jira)
├── config/              # Configuration models and loader
├── store/               # SQLite storage (mappings, jobs)
├── sync/                # Sync engine (mapper, matching)
├── alerts/              # Alert processing
├── server/              # FastAPI webhooks and worker
├── observability/       # Logging, heartbeat
└── utils/               # Utilities (secrets, concurrency)
```

## Troubleshooting

### Connection Issues

```bash
# Test NinjaOne connection
python -m ninjaone_jira_integration init  # Will test during setup

# Enable debug logging
python -m ninjaone_jira_integration -v sync-all --dry-run
```

### Mapping Issues

```bash
# Test with specific device
python -m ninjaone_jira_integration mapping-test --device-id 12345
```

### Failed Jobs

```bash
# View dead-letter jobs
python -m ninjaone_jira_integration replay-dead-letter

# Replay specific job
python -m ninjaone_jira_integration replay-dead-letter --job-id 123
```

## License

MIT License - See [LICENSE](LICENSE) for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and linting
5. Submit a pull request

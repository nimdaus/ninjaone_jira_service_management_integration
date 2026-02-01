# NinjaOne to Jira Service Management Assets Integration

A production-ready Python 3.11+ integration service that synchronizes NinjaOne devices into Jira Service Management Assets and creates Jira issues from NinjaOne condition alerts.

## Features

- **Dual Operation Modes**
  - CLI for interactive configuration, testing, and batch operations
  - HTTP server for webhooks and continuous asynchronous processing

- **Device Synchronization**
  - Full sync of all NinjaOne devices to Jira Assets
  - Single device sync for targeted updates
  - Smart matching: persisted ID mapping → serial number search → create new
  - Diff-based updates: only modifies changed attributes

- **Alert Processing**
  - Creates Jira issues from NinjaOne condition alerts
  - Links issues to corresponding device assets
  - Configurable severity filtering and priority mapping
  - Deduplication via persistent mapping

- **Enterprise Ready**  
  - Resilient retry logic with exponential backoff and jitter
  - Respects `Retry-After` headers from rate limits
  - SQLite storage with WAL mode for durability
  - Dead-letter queue for failed jobs
  - Structured JSON logging with correlation IDs
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

Edit `config.yaml` to map NinjaOne device fields to Jira Assets attributes:

```yaml
assets:
  object_schema_id: "1"
  object_type_id: "10"
  serial_number_attribute_id: "124"
  
  attribute_mappings:
    - jira_attribute_id: "123"
      jira_attribute_name: "Name"
      source: "systemName"
      required: true
    
    - jira_attribute_id: "124"
      jira_attribute_name: "Serial Number"
      source: "system.serialNumber"
      transform: "normalize_serial"
    
    - jira_attribute_id: "125"
      jira_attribute_name: "Operating System"
      source: "os.name"
```

### 4. Test Mappings

```bash
python -m ninjaone_jira_integration mapping-test
```

This fetches a sample device and shows how your mappings would transform the data.

### 5. Run a Dry Sync

```bash
python -m ninjaone_jira_integration sync-all --dry-run
```

Review the output to see what would be created/updated.

### 6. Run Full Sync

```bash
python -m ninjaone_jira_integration sync-all
```

### 7. Start Webhook Server (Optional)

For real-time updates via NinjaOne webhooks:

```bash
python -m ninjaone_jira_integration run-server
```

Configure NinjaOne to send webhooks to `https://your-server:8080/webhook/device` and `/webhook/alert`.

## CLI Commands

| Command | Description |
|---------|-------------|
| `init` | Interactive configuration setup |
| `init --ui` | Web-based configuration UI (coming soon) |
| `mapping-test` | Test attribute mappings with sample device |
| `sync-all` | Sync all devices |
| `sync-all --dry-run` | Preview sync without making changes |
| `sync-device <id>` | Sync a specific device by ID |
| `run-server` | Start HTTP server for webhooks |
| `replay-dead-letter` | Requeue failed jobs for retry |
| `status` | Show statistics and queue status |

## Configuration

Configuration can be provided via:
1. CLI flags (highest priority)
2. Environment variables
3. `.env` file
4. `config.yaml` file (lowest priority)

### Environment Variables

| Variable | Description |
|----------|-------------|
| `NINJA_CLIENT_ID` | NinjaOne OAuth2 client ID |
| `NINJA_CLIENT_SECRET` | NinjaOne OAuth2 client secret |
| `NINJA_BASE_URL` | NinjaOne API URL (default: `https://app.ninjarmm.com`) |
| `JIRA_SUBDOMAIN` | Jira subdomain (e.g., `mycompany`) |
| `JIRA_EMAIL` | Jira account email |
| `JIRA_API_TOKEN` | Jira API token |
| `JIRA_WORKSPACE_ID` | Jira Assets workspace ID |
| `WEBHOOK_SECRET` | Shared secret for webhook verification |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) |

### Attribute Mapping

Map NinjaOne device fields to Jira Assets attributes using dot notation:

- Simple fields: `systemName`, `displayName`
- Nested objects: `system.serialNumber`, `os.name`
- Arrays: `ipAddresses[0]`, `disks[0].size`

Available transforms:
- `upper` - Convert to uppercase
- `lower` - Convert to lowercase  
- `strip` - Remove whitespace
- `normalize_serial` - Uppercase, strip, remove common fillers

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

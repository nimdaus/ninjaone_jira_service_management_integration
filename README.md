# NinjaOne → Jira Service Management Assets

Synchronizes your NinjaOne device inventory into Jira Service Management Assets and creates Jira issues from NinjaOne alerts — with no public-facing server required.

## What it does

- **Device sync** — Pulls all NinjaOne devices on a schedule and creates or updates matching objects in Jira Assets. Each device role maps to a separate Jira object type with custom attribute mappings.
- **Alert polling** — Checks for active NinjaOne alerts every few minutes and creates a Jira issue for each one that doesn't already have one. Automatically links the issue to the device's asset.
- **Dry run mode** — Preview exactly what would be created or changed before writing anything.

## Prerequisites

- Python 3.11+ (or Docker)
- [uv](https://docs.astral.sh/uv/) package manager
- A NinjaOne account with API credentials (Administration > Apps)
- A Jira Cloud account with an API token and Assets configured

## Installation

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone https://github.com/yourorg/ninjaone-jira-integration.git
cd ninjaone-jira-integration

uv sync
```

No need to create or activate a virtual environment — prefix commands with `uv run` and uv handles it.

## Quick Start

### 1. Set credentials

Create a `.env` file:

```bash
# NinjaOne: Administration > Apps > Add API application
NINJA_CLIENT_ID=your-client-id
NINJA_CLIENT_SECRET=your-client-secret

# Jira: https://id.atlassian.com/manage-profile/security/api-tokens
JIRA_API_TOKEN=your-api-token
```

### 2. Run the setup wizard

```bash
uv run ninja-jira init --ui
```

This opens a browser-based wizard that:
- Tests your NinjaOne and Jira connections
- Discovers your Jira Assets workspace and schema
- Walks you through mapping device roles to object types
- Configures alert-to-issue settings
- Saves everything to `config.yaml`

You can re-run the wizard any time to update your configuration.

### 3. Test your mappings

Before running a full sync, verify that your attribute mappings look right:

```bash
# Fetch a sample device and show how it maps to Jira attributes
uv run ninja-jira mapping-test

# Test with a specific device
uv run ninja-jira mapping-test --device-id 12345
```

For alerts, preview how an active alert would become a Jira issue:

```bash
# Fetch a real active alert and preview the issue that would be created
uv run ninja-jira alert-test

# Test with a specific alert UID
uv run ninja-jira alert-test --alert-uid abc-123-def
```

Both commands show exactly which filters apply, what values would be written, and why anything would be skipped — without touching Jira.

### 4. Dry run

```bash
uv run ninja-jira run --once --dry-run
```

Shows every device and alert that would be created or updated. Nothing is written to Jira.

### 5. Run

```bash
# Run once and exit (good for a first-time test or cron)
uv run ninja-jira run --once

# Run continuously — syncs devices every 6h, polls alerts every 5m
uv run ninja-jira run
```

Runs entirely from your machine. No public URL or open port needed.

---

## Configuration

Configuration is loaded in this priority order:

1. CLI flags
2. Environment variables / `.env` file
3. `config.yaml`

### Config file location

The CLI searches for `config.yaml` in this order:

1. `--config <path>` flag
2. `NINJA_JIRA_CONFIG` environment variable
3. Current directory and parent directories (up to `.git` / `pyproject.toml`)
4. `~/.config/ninja-jira/config.yaml`

### Sync schedule

```yaml
# Device sync (default: every 6 hours)
schedule:
  enabled: true
  interval_hours: 6

# Alert polling (default: every 5 minutes)
alert_schedule:
  enabled: true
  interval_minutes: 5
```

Both schedules run independently. Disable either one if you only need the other.

### Attribute mappings

Map NinjaOne device fields to Jira Assets attributes using dot notation:

- Top-level fields: `systemName`, `displayName`
- Nested objects: `system.serialNumber`, `os.name`
- Array indexing: `ipAddresses[0]`, `disks[0].size`

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

#### NinjaOne Device ID attribute

The wizard includes a **+ Create NinjaOne Device ID Attribute** button on each object type mapping. Clicking it creates a new text attribute called `NinjaOne Device ID` in your Jira Assets object type and pre-configures a mapping from `id` (the NinjaOne device's integer ID). This attribute is used as a secondary identity key — if a device's serial number changes or is missing, the integration can still find the right asset.

The button disappears after it's used (or when loading a config where the attribute already exists). That's intentional: clicking it a second time would fail because Jira requires attribute names to be unique within an object type.

#### Transforms

Applied in order as a pipeline:

| Transform | Description |
|-----------|-------------|
| `upper` | UPPERCASE |
| `lower` | lowercase |
| `strip` | Trim whitespace |
| `normalize_serial` | Uppercase + strip + remove known-bad values |
| `to_string` | Convert to string |
| `to_integer` | Parse as integer |
| `to_float` | Parse as float |
| `to_boolean` | `true`, `yes`, `1`, `on` → `True` |
| `first_ip` | Extract first IPv4 from string or list |
| `first_mac` | Extract first MAC address |
| `bytes_to_gb` | Bytes → GB, 2 decimal places |

### Alert to issue mapping

```yaml
issues:
  project_key: "HELPDESK"
  issue_type_id: "10001"
  summary_template: "[NinjaOne] {severity}: {device_name} - {message}"
  min_severity: "MODERATE"
  default_labels:
    - ninjaone
    - auto-created
  severity_to_priority_mapping:
    CRITICAL: "1"
    MAJOR: "2"
    MODERATE: "3"
```

A few things worth knowing about alert behavior:

- **Deduplication**: each alert's UID is stored after a Jira issue is created for it. Re-polling never creates duplicate issues.
- **Device linking**: if an alert references a device with no Jira asset yet, the integration syncs that device first so the issue can be linked to the correct asset.
- **Resolved alerts**: alerts that disappear from the NinjaOne active list are not automatically closed in Jira — manage their lifecycle in Jira directly.

### Environment variables

| Variable | Description |
|----------|-------------|
| `NINJA_JIRA_CONFIG` | Absolute path to config file |
| `NINJA_CLIENT_ID` | NinjaOne OAuth2 client ID |
| `NINJA_CLIENT_SECRET` | NinjaOne OAuth2 client secret |
| `NINJA_BASE_URL` | NinjaOne API URL (default: `https://app.ninjarmm.com`) |
| `JIRA_SUBDOMAIN` | Jira subdomain (e.g. `mycompany`) |
| `JIRA_EMAIL` | Jira account email |
| `JIRA_API_TOKEN` | Jira API token |
| `JIRA_WORKSPACE_ID` | Jira Assets workspace ID (auto-discovered if omitted) |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`) |
| `NINJA_JIRA_LOG_FILE` | Path to write a structured JSON log file |
| `NINJA_JIRA_SCHEDULE_ENABLED` | `true`/`false` — enable device sync |
| `NINJA_JIRA_SCHEDULE_INTERVAL_HOURS` | Hours between device syncs |
| `NINJA_JIRA_ALERT_SCHEDULE_ENABLED` | `true`/`false` — enable alert polling |
| `NINJA_JIRA_ALERT_SCHEDULE_INTERVAL_MINUTES` | Minutes between alert polls |

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `init` | Interactive CLI setup wizard |
| `init --ui` | Browser-based setup wizard |
| `mapping-test` | Preview how a sample device maps to Jira attributes |
| `mapping-test --device-id <id>` | Test with a specific device |
| `alert-test` | Preview how an active alert becomes a Jira issue |
| `alert-test --alert-uid <uid>` | Test with a specific alert |
| `sync-all` | Sync all devices to Jira Assets now |
| `sync-all --dry-run` | Preview sync without writing |
| `sync-device <id>` | Sync a single device by NinjaOne ID |
| `run` | Start continuous device sync + alert polling |
| `run --once` | Run one sync cycle and exit |
| `run --dry-run` | Run in preview mode (no writes) |
| `status` | Show sync statistics and queue status |
| `replay-dead-letter` | Retry failed jobs |

**Global flags** (must come before the subcommand):

```
--config / -c    Path to config file
--verbose / -v   Debug logging
--log-file       Write structured JSON logs to a file
```

```bash
# Correct — global flags go before the subcommand
uv run ninja-jira --log-file test.log run --once
uv run ninja-jira --verbose sync-all --dry-run

# Wrong — these will error
uv run ninja-jira run --once --log-file test.log
```

---

## Docker

```bash
docker build -t ninjaone-jira-integration .
```

```yaml
# docker-compose.yaml
services:
  integration:
    build: .
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
docker compose up -d
```

---

## Troubleshooting

**Connection problems:**
```bash
# The setup wizard tests both connections on startup
uv run ninja-jira init --ui

# Or enable debug logging on any command
uv run ninja-jira -v sync-all --dry-run
```

**A device isn't mapping correctly:**
```bash
uv run ninja-jira mapping-test --device-id 12345
```

**An alert isn't creating an issue:**
```bash
uv run ninja-jira alert-test --alert-uid <uid>
```

Shows which filter was applied, what the issue summary and priority would be, and why it might be skipped.

**Failed jobs:**
```bash
uv run ninja-jira status
uv run ninja-jira replay-dead-letter
```

---

## Development

```bash
uv sync --all-extras

uv run ruff check .
uv run mypy ninjaone_jira_integration
uv run pytest
```

```
ninjaone_jira_integration/
├── cli/              # CLI commands
├── clients/          # API clients (NinjaOne, Jira)
├── config/           # Configuration models, loader, setup UI
├── store/            # SQLite storage (mappings, job queue)
├── sync/             # Device sync engine
├── alerts/           # Alert processing
├── server/           # Webhook server (run-server mode)
└── notifications.py  # Outbound webhook notifications
```

---

## Advanced / In Progress

The following features are in the codebase but not yet fully tested or production-ready.

### Webhook server (`run-server`)

An HTTP server for real-time processing via NinjaOne webhooks instead of polling. Requires a publicly accessible URL.

```bash
uv run ninja-jira run-server
```

Configure NinjaOne to POST to:
- `https://your-server:8080/webhook/device` — device updates
- `https://your-server:8080/webhook/alert` — alert events

Health endpoints (for container probes):
- `GET /healthz` — liveness
- `GET /readyz` — readiness (checks DB and worker)

> The polling mode (`run` / `run --once`) is the tested, recommended path for most deployments. The webhook server mode is functional but hasn't been hardened for production.

### Outbound notifications (heartbeat + change summaries)

The integration can optionally POST to a URL after each sync cycle and on a heartbeat timer — useful for [Uptime Kuma](https://uptime.kuma.pet/), Betterstack, Slack, or any HTTP receiver.

Configure via the **Notifications** tab in `ninja-jira init --ui`, or manually:

```yaml
heartbeat:
  enabled: true
  url: https://uptime.example.com/api/push/abc123
  interval_seconds: 60
  notify_on_changes: true
  # token: set via HEARTBEAT_TOKEN env var — never stored in config.yaml
```

Environment variables:

| Variable | Description |
|----------|-------------|
| `HEARTBEAT_URL` | Webhook URL (both event types post here) |
| `HEARTBEAT_TOKEN` | Bearer token sent as `Authorization: Bearer ...` |
| `HEARTBEAT_INTERVAL_SECONDS` | Seconds between heartbeat pings (default: 60) |
| `HEARTBEAT_ENABLED` | `true`/`false` |

If no URL is configured the feature is completely silent — no errors, no warnings.

**Payload examples:**

Heartbeat ping:
```json
{
  "event": "heartbeat",
  "timestamp": "2026-06-24T17:56:32Z",
  "status": "running",
  "version": "1.0.0",
  "runtime": "bare-python",
  "uptime_seconds": 3600
}
```

Change summary (sent after any sync that created or updated records):
```json
{
  "event": "sync_complete",
  "timestamp": "2026-06-24T18:00:00Z",
  "runtime": "bare-python",
  "summary": {
    "type": "device_sync",
    "total": 60,
    "created": 3,
    "updated": 12,
    "skipped": 45,
    "failed": 0,
    "success_rate": 100.0,
    "changes": ["win-server-01: updated Serial Number"]
  }
}
```

> The outbound notification system is newly added and hasn't been fully tested end-to-end. The setup UI for it exists in the wizard but treat this as a preview feature.

---

## License

MIT — see [LICENSE](LICENSE).

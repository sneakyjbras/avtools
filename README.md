
# AV Tools — CERN Audio/Video Monitoring & Telemetry

> **Status:** Production
> **Language/Runtime:** Python **3.11**
> **Packaging/Deploy:** Poetry → internal PyPI (IT‑DCIM) → Puppet/Foreman
> **Infra:** AlmaLinux 9 (AL9) OpenStack VMs
> **Dashboards:** Grafana (central CERN)
>
> **Website link:** https://cern.ch/avtools

AV Tools is CERN’s monitoring and data‑collection stack for **Audio/Video (AV) devices** deployed across meeting rooms and lecture halls. It aggregates authoritative inventory data (EAM & LanDB), augments it with **real‑time probe signals** (ping/SNMP, etc.), and serves consolidated views in **Grafana** so operations can reason about device health, availability, and trends at scale.

---

## Table of Contents

1. [What AV Tools Does](#what-av-tools-does)
2. [What We Replace](#what-we-replace)
3. [Device Scope (What We Monitor)](#device-scope-what-we-monitor)
4. [High‑Level Architecture](#high-level-architecture)
5. [Data Sources & Storage](#data-sources--storage)
6. [Dashboards (Grafana)](#dashboards-grafana)
7. [Runtime & Hosting (OpenStack AL9)](#runtime--hosting-openstack-al9)
8. [Configuration Management & Puppet](#configuration-management--puppet)
9. [Packaging & Release Workflow (Poetry → IT‑DCIM PyPI)](#packaging--release-workflow-poetry--it-dcim-pypi)
10. [Installation from Internal PyPI](#installation-from-internal-pypi)
11. [Configuration & Secrets](#configuration--secrets)
12. [Systemd Services, Timers & Wrapper](#systemd-services-timers--wrapper)
13. [CLI Usage](#cli-usage)
14. [Testing & Coverage](#testing--coverage)
15. [Operations: Day‑2 Tasks](#operations-day-2-tasks)
16. [Troubleshooting Checklist](#troubleshooting-checklist)
17. [Repository Layout](#repository-layout)
18. [Local Development (Python 3.11)](#local-development-python-311)
19. [Docs](#docs)
20. [Security Notes](#security-notes)
21. [Appendix: Links](#appendix-links)

---

## What AV Tools Does

- Integrates authoritative **inventory** from EAM and LanDB into a normalized **PostgreSQL** cache (periodic snapshots).
- Snapshots both **EAM devices (OSOBJA)** and **EAM positions (OSOBJP)** into separate cache tables (`eam_devices`, `eam_positions`), filtered to the AV department.
- Enriches the EAM snapshot with LanDB **SNMP target IPs** + location metadata into `landb_ipaddresses` (only rows with an IPv4/IPv6 target are persisted).
- Provides a fast diff/sync engine with an **EAM text sanitizer** (reduces churn from dirty whitespace) and optional per-row **sync reports**.
- Performs **real‑time telemetry** (e.g., ping/SNMP probe results) written to **InfluxDB** for time‑series analysis.
- Exposes curated metrics to **Grafana** dashboards for operations and reporting.
- Is designed for **scale** (tens of thousands of rows in inventory, many probes), **reliability** (systemd timers with idempotent runs), and **observability** (structured logging).
- Is packaged and versioned with **Poetry** (Python 3.11), published to **IT‑DCIM internal PyPI**, and rolled out with **Puppet** via **Foreman/aiadm**.

## What We Replace

AV Tools replaces the former dashboard at **https://av-dashboard.web.cern.ch/** with a modern, data‑pipeline‑driven approach: reproducible snapshots, real‑time probes, and Grafana‑first observability.

## Device Scope (What We Monitor)

The scope is **AV devices** deployed at CERN (examples include **projectors**, **AV switchers/matrixes**, **room codecs/endpoints**, and related peripherals). Exact model coverage evolves as new device classes are onboarded; discovery and status logic live in this repository’s Python services.

## High‑Level Architecture

```
            +------------------+
            |  EAM (author.)   |
            +------------------+
                     \
                      \  periodic snapshot (ETL)
                       \
                        v
                   +-----------+
                   | Postgres  |  <--- normalized cache of EAM & LanDB
                   +-----------+
                        ^
                       /  periodic snapshot (ETL)
                      /
            +------------------+
            |  LanDB (author.) |
            +------------------+

   realtime probes (ping/SNMP, etc.)
                |
                v
          +-----------+
          | InfluxDB  |   <--- time‑series of probe results
          +-----------+

                |
                v
          +-----------+
          | Grafana   |   <--- operational dashboards
          +-----------+

                ^
                |
        +----------------+
        |  AV Tools Svc  |   <--- Python 3.11 services (systemd timers)
        +----------------+
                   |
                   v
            AlmaLinux 9 on OpenStack
```

## Data Sources & Storage

- **EAM (Enterprise Asset Management)** and **LanDB** are the **sources of truth** for device inventory and network attributes.
  - AV Tools periodically snapshots/normalizes both into **PostgreSQL** (“cache DB”) for fast relational queries.
- **InfluxDB** stores **real‑time probe** outputs (e.g., *ping_check*, *snmp_probe*) for latency/availability and device health.
- **PostgreSQL** → supports relational joins (e.g., room hierarchies, inventory merges).
- **Grafana** → primary consumer of both **PostgreSQL** snapshots (slowly‑changing data) and **InfluxDB** (live signals).



### Cache tables (schema overview)

These Postgres tables are **cache snapshots** owned by AV Tools (safe to rebuild):

- `eam_devices` — subset of EAM `Equipment` rows representing **devices/assets**.
- `eam_positions` — subset of EAM `Equipment` rows representing **positions** (distinct concept; used for hierarchy/location joins).
- `landb_ipaddresses` — EAM‑keyed cache of **LanDB SNMP targets** (IPv4/IPv6) enriched with LanDB serial/name and parsed location (`building`, `floor`, `room`).

Notes:
- Cache tables may be **dropped & recreated automatically** if a legacy schema is detected (to avoid manual migrations).
- LanDB enrichment is designed to be efficient (bulk lookups by serial number/name; a small number of API calls).

> The project includes SQL/Influx examples and service‑specific usage under `./docs`.

## Dashboards (Grafana)

Central CERN Grafana (Org 12) hosts AV Tools panels and composite views:
**https://monit-grafana.cern.ch/dashboards/f/beuar1of5bo5cf/?orgId=12**

Dashboards combine snapshot context (EAM/LanDB via PostgreSQL) with **live health** (InfluxDB) to highlight availability, device status, and outliers.

## Runtime & Hosting (OpenStack AL9)

- **OS:** AlmaLinux 9 (AL9) images.
- **Platform:** CERN OpenStack. Machines are provisioned via the project console:
  **https://openstack.cern.ch/project/**
- **Role:** One or more AL9 VMs run the **AV Tools services** (periodic ETL + real‑time probes).

## Configuration Management & Puppet

- Configuration is managed via **Puppet**, driven by **Foreman/aiadm** (Judy portal):
  **https://judy.cern.ch/**
- The Puppet control repo and hostgroup integration live at:
  **https://gitlab.cern.ch/ai/it-puppet-hostgroup-itdcim/**

> Puppet classes define the AV Tools package source (internal PyPI), environment, systemd units/timers, and service wrappers. Host enrollment, parameters, and hiera are owned in Foreman/aiadm.

## Packaging & Release Workflow (Poetry → IT‑DCIM PyPI)

AV Tools uses **Poetry** for packaging/versioning and publishes to the internal **IT‑DCIM PyPI** index.

1. **Develop** against Python **3.11**.
2. **Version bump** (semantic): `poetry version patch|minor|major`.
3. **Build**: `poetry build`.
4. **Publish** to internal PyPI (IT‑DCIM). The internal simple index is:
   **https://pypi-itdcim.cern.ch/simple**
   (Requires appropriate credentials/certificates.)

> Deployment is **pull‑based**: Puppet installs/updates the package from the internal index on target AL9 nodes and ensures systemd units/timers are enabled.

## Installation from Internal PyPI

### Prerequisites (AL9)

- Python **3.11** and build tools (`python3.11`, `python3.11-devel`, `gcc`, etc.).
- A dedicated venv is recommended (e.g., `/opt/avtools/.venv`).
- Ensure corporate CA trust is available for TLS (e.g., `REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt`).

### Example (manual, for testing)

```bash
# Create dedicated user and folders (if not managed by Puppet)
sudo useradd --system --create-home --home-dir /var/lib/avtools avtools || true
sudo mkdir -p /opt/avtools && sudo chown avtools: /opt/avtools
cd /opt/avtools

# Python 3.11 virtualenv
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

# Install from IT-DCIM PyPI
export REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt   # trust CERN CA
python -m pip install --index-url https://pypi-itdcim.cern.ch/simple avtools
```

> In managed environments, Puppet performs the equivalent installation and pins the desired version/channel.

## Configuration & Secrets

AV Tools services read configuration from environment variables and/or an env‑file. **Never commit secrets.** Example template:

```dotenv
# ---------- Common ----------
REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt
AVTOOLS_LOG_LEVEL=INFO
THREADS=16

# ---------- PostgreSQL (cache DB) ----------
# format: postgresql://USER:PASS@HOST:PORT/DB
DATABASE_URL=postgresql://<USER>:<PASSWORD>@<HOST>:<PORT>/<DB>

# ---------- EAM ----------
MY_USERNAME=avtools
MY_PASSWORD="<SECRET>"

# ---------- LanDB / OAuth ----------
LANDB_CLIENT_ID=av-tools
LANDB_CLIENT_SECRET="<SECRET>"
LANDB_AUDIENCE=production-microservice-landb-rest
# Note: `avtools run-landb` takes OAuth parameters as CLI flags.
# It's still convenient to keep them in env and pass through a wrapper/systemd unit.
# Optional: depending on LanDB REST client configuration, an OAuth token may be cached on disk.
# LANDB_TOKEN_FILE=/var/lib/avtools/token

# ---------- InfluxDB ----------
INFLUX_HOST=<influx-host>
INFLUX_PORT=8086
INFLUX_USER=<user>
INFLUX_PASSWORD=<secret>
INFLUX_DB=<db-name>
```

Puppet/Hiera own the concrete values in production. On developer machines, keep a `.env.local` that is never committed.

## Systemd Services, Timers & Wrapper

AV Tools runs as a set of **systemd template services** with paired **timers**. Timers schedule ETL and probe jobs; services execute through a **wrapper** that loads the env‑file and calls the appropriate Python entrypoint.

### Typical units

- `avtools@run-eam.service` / `avtools@run-eam.timer` – snapshot/ETL from **EAM** → **PostgreSQL**
- `avtools@run-landb.service` / `avtools@run-landb.timer` – snapshot/ETL from **LanDB** → **PostgreSQL**
- `avtools@snmp-influx.service` / `avtools@snmp-influx.timer` – **SNMP/ping** probes → **InfluxDB**

> Service‑specific details are in `./docs` (see [Docs](#docs)).

### Enabling timers

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now avtools@run-eam.timer
sudo systemctl enable --now avtools@run-landb.timer
sudo systemctl enable --now avtools@snmp-influx.timer

# Verify schedule
systemctl list-timers 'avtools@*.timer'
```

### Force‑run a job now (without waiting for the timer)

```bash
# one-shot run of the associated service unit
sudo systemctl start avtools@run-eam.service
sudo systemctl start avtools@run-landb.service
sudo systemctl start avtools@snmp-influx.service
```

### Verify the wrapper is in effect

```bash
# Show fragment and drop-in overrides (wrapper lives in a drop-in like 10-exec-wrapper.conf)
systemctl show -p FragmentPath -p DropInPaths 'avtools@run-eam.service'
systemctl cat 'avtools@run-eam.service'
```

> The wrapper centralizes env loading and uniform logging around each Python entrypoint.

### Logs

```bash
journalctl -u 'avtools@*.service' --since '1h' -n 200 -o cat
```


## CLI Usage

AV Tools ships a Click-based CLI (console script: `avtools`). Systemd units typically invoke these commands through the wrapper.

### Global options

- `--dbod-url` (or env `DATABASE_URL`) — PostgreSQL cache connection string.
- `--logs` — enables extra structured logging, including optional per-row sync reports.

### Commands

#### `run-eam`

Snapshots EAM **devices** and **positions** into Postgres.

- Credentials: `MY_USERNAME`, `MY_PASSWORD` (or pass via options).
- Default grids: `OSOBJA` (devices/assets) and `OSOBJP` (positions), filtered by department code prefix `AV`.

```bash
export DATABASE_URL='postgresql://<USER>:<PASS>@<HOST>:<PORT>/<DB>'
export MY_USERNAME='...'
export MY_PASSWORD='...'

poetry run avtools --logs run-eam
```

#### `run-landb`

Enriches the current EAM snapshot with LanDB network targets and writes `landb_ipaddresses`.

- OAuth parameters are passed as CLI flags (you can still store them in env and reference them).
- Only devices with a usable SNMP target IP (**IPv4 or IPv6**) are persisted.

```bash
export DATABASE_URL='postgresql://<USER>:<PASS>@<HOST>:<PORT>/<DB>'

poetry run avtools run-landb \
  --client-id "$LANDB_CLIENT_ID" \
  --client-secret "$LANDB_CLIENT_SECRET" \
  --audience "$LANDB_AUDIENCE" \
  --threads 16
```

#### `snmp-influx`

Loads cached LanDB targets from Postgres, performs ping + SNMP collection, and writes to InfluxDB 1.x.

Measurements written include:
- `ping_check` (fields: `status`, optional `rtt_ms`)
- `snmp_probe` (field: `status`)
- `snmp_query` (device-specific fields; currently implemented for projectors)

```bash
export DATABASE_URL='postgresql://<USER>:<PASS>@<HOST>:<PORT>/<DB>'
export INFLUX_HOST='...'
export INFLUX_PORT=8086
export INFLUX_USER='...'
export INFLUX_PASSWORD='...'
export INFLUX_DB='...'

poetry run avtools snmp-influx --threads 16
```

#### `snmp-timeseries`

Loads cached LanDB targets from Postgres, performs ping + SNMP collection, and
publishes Prometheus metrics to CERN **MONIT** via OTLP/gRPC (stored in Mimir).
This is the current production path (the InfluxDB sink above is legacy).

```bash
export DATABASE_URL='postgresql://<USER>:<PASS>@<HOST>:<PORT>/<DB>'
export MONIT_TENANT='...'
export MONIT_PASSWORD='...'

poetry run avtools snmp-timeseries --threads 16
```

##### `--priority` (priority tiers)

Metrics are classified into **priority tiers** so different metrics can be
published at different cadences by separate CronJobs. `--priority`
(env `AVTOOLS_PRIORITY`, default `all`) is a **publish-time filter**: collection
is unchanged every cycle — only the emitted subset changes.

| `--priority` | Publishes |
|---|---|
| `all` (default) | Every metric — identical to today's behaviour. |
| `critical` | The CRITICAL tier **plus** the ALWAYS guardrails. |
| `high` | The HIGH tier **plus** the ALWAYS guardrails. |
| `medium` | The MEDIUM tier **plus** the ALWAYS guardrails. |
| `low` | The LOW tier **plus** the ALWAYS guardrails. |

Tiers are **exact, not cumulative**: `--priority critical` publishes only
CRITICAL ∪ ALWAYS — it does **not** also emit HIGH/MEDIUM/LOW. The taxonomy is
the single source of truth in `src/avtools/timeseries/metrics.py`
(`Priority` / `MetricMeta.priority`):

- **CRITICAL** — up/down and threshold-status signals: ping status, SNMP probe
  status, interface oper-status, PDU health/input-status/pq-severity, the PDU
  device-evaluated status codes (active-power/power-factor/balance/load), and PDU
  environmental temperature/humidity.
- **HIGH** — live electrical/actionable readings: ping RTT, PDU active power,
  line current, current-utilized %, voltage, outlet on/off state, projector
  power status.
- **MEDIUM** — trends & counters: energy, frequency, power factor, apparent
  power, out-of-balance %, per-outlet current/power/energy, interface
  octets/errors, and all uptime/lamp-hours gauges.
- **LOW** — all `*_info` string-as-label gauges (firmware, status labels, sensor/
  outlet/interface identity).
- **ALWAYS** — collector self-instrumentation that must emit on **every** run
  regardless of tier: the cycle guardrails (`snmp_devices_targeted`,
  `snmp_devices_polled`, `snmp_coverage_ratio`, `snmp_cycle_duration_seconds`)
  and the per-job heartbeats (`*_last_run_timestamp`).

**Guardrail `tier` label (for Grafana):** when `--priority != all`, the ALWAYS
cycle guardrails carry a `tier` label equal to the `--priority` value (mirroring
the conditional `shard` label). Once collection is split into per-tier CronJobs,
all four jobs emit the same guardrails every cycle; the `tier` label keeps their
series from colliding on one identity. **Grafana SLO/watchdog alerts must
`group by (tier)`** (and `shard` when sharded) so each tier's coverage/freshness
is evaluated independently. The label is omitted under `all` so the current
untiered series identity is unchanged, and it is deliberately **not** added to
device metrics (that would multiply device cardinality).


## Testing & Coverage

Unit tests live under `./tests` and focus on core sync logic, edge cases, and failure handling.

```bash
poetry install
poetry run pytest

# Coverage (Cobertura-compatible XML for CI)
poetry run pytest --cov=avtools --cov-report=term-missing --cov-report=xml:coverage.xml
```

## Operations: Day‑2 Tasks

- **Upgrade to a new AV Tools release** (after `poetry publish` to IT‑DCIM PyPI):
  ```bash
  # If using a venv
  source /opt/avtools/.venv/bin/activate
  export REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt
  python -m pip install --index-url https://pypi-itdcim.cern.ch/simple --upgrade avtools

  # Restart services (or wait for next timer tick)
  sudo systemctl restart 'avtools@run-eam.service' 'avtools@run-landb.service' 'avtools@snmp-influx.service'
  ```
- **Sanity checks**: confirm timers are active; run a forced execution; inspect journald output; validate new metrics in Grafana panels.

## Troubleshooting Checklist

1. **Timers not firing**
   - `systemctl list-timers 'avtools@*.timer'`
   - `systemctl status avtools@run-eam.timer` (look for schedule/next).

2. **Wrapper not used**
   - `systemctl show -p DropInPaths 'avtools@run-eam.service'`
   - `systemctl cat 'avtools@run-eam.service'` (check `ExecStart=` override from the wrapper).

3. **TLS/CA errors** (when talking to internal endpoints)
   - Ensure `REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt` is exported for the service environment.

4. **DB/credentials**
   - Confirm `DATABASE_URL` and secret env vars are present for the service user.
   - Validate network reachability/ports to PostgreSQL and InfluxDB.

5. **Grafana shows stale data**
   - Confirm the ETL timers have run successfully; check **InfluxDB** series for recent points.
   - Verify `OrgId`/datasource in Grafana panels.

## Repository Layout

```
.
├── src/avtools/             # Python 3.11 source tree (packages, CLI entrypoints)
├── tests/                   # Unit tests (pytest)
├── docs/                    # Service-specific docs (ETL, probes, SQL/Influx examples, runbooks)
├── deployment/              # Deployment notes (e.g., systemd/Puppet details)
├── db/                      # DB-related notes or migrations (if applicable)
├── pyproject.toml           # Poetry project definition
├── README.md                # This file
└── ...                      # CI, tooling, etc.
```

> The `./docs` directory contains detailed notes for each service (see next section).

## Local Development (Python 3.11)

```bash
# Clone and set up Poetry
poetry env use python3.11
poetry install

# Run linters/tests (if configured)
poetry run pytest -q
poetry run ruff check .  # or flake8, etc.

# Try a local entrypoint (reads .env.local if supported by your shell/wrapper)
poetry run avtools --help
```

## Docs

Detailed docs for individual services live in **`./docs`**. At a minimum, expect:

- **EAM ETL**: how we snapshot authoritative EAM entities into PostgreSQL.
- **LanDB ETL**: how we snapshot LanDB and reconcile with EAM positions.
- **SNMP/Probe pipeline**: schema/fields in InfluxDB (e.g., `ping_check`, `snmp_probe`).
- **SQL/Influx examples**: common queries used by Grafana panels.
- **Runbooks**: start/stop/force‑run, failure triage, and log locations.

## Security Notes

- **Do not commit secrets** (passwords, tokens, client secrets).
- Environment files must be owned by the **service account** (e.g., `avtools`) with restrictive permissions.
- For internal services, ensure the **corporate CA** is trusted by the runtime (`REQUESTS_CA_BUNDLE`).
- OAuth tokens (e.g., **LanDB**) may be cached on disk depending on client configuration; ensure any token files are readable only by the service user.

## Appendix: Links

- **Legacy dashboard (replaced):** https://av-dashboard.web.cern.ch/
- **Grafana dashboards (Org 12):** https://monit-grafana.cern.ch/dashboards/f/beuar1of5bo5cf/?orgId=12
- **OpenStack (project console):** https://openstack.cern.ch/project/
- **Foreman/aiadm (Judy):** https://judy.cern.ch/
- **Puppet repo (IT‑DCIM hostgroup):** https://gitlab.cern.ch/ai/it-puppet-hostgroup-itdcim/
- **Internal PyPI (IT‑DCIM simple index):** https://pypi-itdcim.cern.ch/simple

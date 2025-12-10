
# AV Tools — CERN Audio/Video Monitoring & Telemetry

> **Status:** Production
> **Language/Runtime:** Python **3.11**
> **Packaging/Deploy:** Poetry → internal PyPI (IT‑DCIM) → Puppet/Foreman
> **Infra:** AlmaLinux 9 (AL9) OpenStack VMs
> **Dashboards:** Grafana (central CERN)
>
> **This project REPLACES the legacy AV dashboard:** https://cern.ch/avtools

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
13. [Operations: Day‑2 Tasks](#operations-day-2-tasks)
14. [Troubleshooting Checklist](#troubleshooting-checklist)
15. [Repository Layout](#repository-layout)
16. [Local Development (Python 3.11)](#local-development-python-311)
17. [Docs](#docs)
18. [Security Notes](#security-notes)
19. [Appendix: Links](#appendix-links)

---

## What AV Tools Does

- Integrates authoritative **inventory** from EAM and LanDB into a normalized **PostgreSQL** cache (periodic snapshots).
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
LANDB_TOKEN_FILE=/var/lib/avtools/token

# ---------- InfluxDB ----------
INFLUX_HOST=<influx-host>
INFLUX_PORT=8090
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
├── avtools/                 # Python 3.11 source tree (packages, entrypoints)
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
- Tokens (e.g., **LanDB**) are written to paths like `/var/lib/avtools/token` and should be readable only by the service user.

## Appendix: Links

- **Legacy dashboard (replaced):** https://av-dashboard.web.cern.ch/
- **Grafana dashboards (Org 12):** https://monit-grafana.cern.ch/dashboards/f/beuar1of5bo5cf/?orgId=12
- **OpenStack (project console):** https://openstack.cern.ch/project/
- **Foreman/aiadm (Judy):** https://judy.cern.ch/
- **Puppet repo (IT‑DCIM hostgroup):** https://gitlab.cern.ch/ai/it-puppet-hostgroup-itdcim/
- **Internal PyPI (IT‑DCIM simple index):** https://pypi-itdcim.cern.ch/simple

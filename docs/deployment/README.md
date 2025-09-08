# AV Tools – Ops Lifecycle (AL9)

This document is a practical, end‑to‑end runbook for shipping and operating **AV Tools** on Alma/Rocky Linux 9 at CERN. It covers:

1) Publishing a new AV Tools release
2) Installing the package from the **itdcim** private PyPI using **pip**
3) Configuring **systemd** services and timers (with a wrapper)
4) Optional **cron ↔ systemd** bridge
5) Running everything under the **avdaemon** service account
6) Sanity checks, upgrades, and troubleshooting
7) Pointer to our Puppet codebase

> All secrets are placeholders. Replace with real values and keep permissions tight.

---

## 0) Prerequisites

- OS: Alma/Rocky Linux 9
- Packages:
  ```bash
  sudo dnf install -y python3 python3-pip python3-venv git curl jq
  ```
- Service account:
  ```bash
  sudo useradd --system --create-home --home-dir /var/lib/avtools --shell /sbin/nologin avdaemon || true
  sudo mkdir -p /etc/avtools /var/log/avtools /opt/avtools
  sudo chown -R avdaemon:avdaemon /etc/avtools /var/log/avtools /var/lib/avtools /opt/avtools
  sudo chmod 0750 /etc/avtools /var/log/avtools /var/lib/avtools
  ```

- Internal PyPI (itdcim) and CA:
  - Index URL: `https://pypi-itdcim.cern.ch/simple`
  - CA bundle: `/etc/pki/tls/certs/ca-bundle.crt`

---

## 1) Publish a new AV Tools release (Poetry)

> Run on your dev machine or CI.

1. Bump version & build:
   ```bash
   poetry version patch          # or minor / major
   poetry build
   ```

2. Configure the **itdcim** repository and credentials (one-time):
   ```bash
   poetry config repositories.itdcim https://pypi-itdcim.cern.ch
   # Either using http-basic:
   poetry config http-basic.itdcim "$PYPI_USER" "$PYPI_PASS"
   # Or via env vars in CI:
   #   POETRY_HTTP_BASIC_ITDCIM_USERNAME
   #   POETRY_HTTP_BASIC_ITDCIM_PASSWORD
   ```

3. Publish:
   ```bash
   poetry publish -r itdcim
   ```

4. Tag (optional but recommended):
   ```bash
   git tag -a v$(poetry version -s) -m "AV Tools $(poetry version -s)"
   git push --tags
   ```

**Verify the artifact** (from a host with access to itdcim):
```bash
pip install --index-url https://pypi-itdcim.cern.ch/simple \
           --cert /etc/pki/tls/certs/ca-bundle.crt \
           --no-cache-dir --dry-run avtools==X.Y.Z
```

---

## 2) Install AV Tools on the machine (pip + venv)

We install into a dedicated virtual environment for isolation:

```bash
sudo -i
python3 -m venv /opt/avtools/.venv
/opt/avtools/.venv/bin/python -m pip install --upgrade pip
/opt/avtools/.venv/bin/pip install \
  --index-url https://pypi-itdcim.cern.ch/simple \
  --cert /etc/pki/tls/certs/ca-bundle.crt \
  --no-cache-dir avtools==X.Y.Z
```

Make sure the **avdaemon** user can execute the venv and read the package:
```bash
chown -R avdaemon:avdaemon /opt/avtools
chmod -R o-rwx /opt/avtools
```

Quick verification:
```bash
sudo -u avdaemon /opt/avtools/.venv/bin/avtools --version
```

> If the CLI is not `avtools`, use `python -m avtools`:
> ```bash
> sudo -u avdaemon /opt/avtools/.venv/bin/python -m avtools --help
> ```

---

## 3) Runtime configuration (environment)

Create `/etc/avtools/avtools.env` (owner `avdaemon`, mode `0640`):

```ini
# ---------- Common ----------
REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt
AVTOOLS_LOG_LEVEL=INFO
THREADS=16

# ---------- Postgres (cache DB) ----------
# format: postgresql://USER:PASS@HOST:PORT/DB
DATABASE_URL=postgresql://avdaemon:********@dbod-avtools-cache.cern.ch:6613/av_cache

# ---------- EAM ----------
MY_USERNAME=avtools
MY_PASSWORD="********"

# ---------- LanDB / OAuth ----------
LANDB_CLIENT_ID=av-tools
LANDB_CLIENT_SECRET="********"
LANDB_AUDIENCE=production-microservice-landb-rest
LANDB_TOKEN_FILE=/var/lib/avtools/token   # writable by avdaemon

# ---------- Influx 1.8 ----------
INFLUX_HOST=dbod-avtools-ts.cern.ch
INFLUX_PORT=8090
INFLUX_USER=avdaemon
INFLUX_PASSWORD="********"
INFLUX_DB=av_ts
```

Permissions:
```bash
sudo chown avdaemon:avdaemon /etc/avtools/avtools.env
sudo chmod 0640 /etc/avtools/avtools.env
```

---

## 4) The exec wrapper

We standardize execution via a thin wrapper so **systemd**, **cron**, and operators all call the same entry point.

Create `/usr/local/bin/avtools-wrapper`:

```bash
sudo tee /usr/local/bin/avtools-wrapper >/dev/null <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

VENV="/opt/avtools/.venv"
ENVFILE="/etc/avtools/avtools.env"
LOGROOT="/var/log/avtools"

# Ensure runtime dirs exist
mkdir -p "$LOGROOT"

# Export environment from file (systemd also loads it, but this keeps CLI consistent)
if [[ -f "$ENVFILE" ]]; then
  set -o allexport
  # shellcheck disable=SC1090
  source "$ENVFILE"
  set +o allexport
fi

cmd="$1"; shift || true

# Prefer the CLI entry point if present; fallback to -m avtools
if [[ -x "$VENV/bin/avtools" ]]; then
  exec "$VENV/bin/avtools" "$cmd" "$@"
else
  exec "$VENV/bin/python" -m avtools "$cmd" "$@"
fi
EOF

sudo chmod 0755 /usr/local/bin/avtools-wrapper
sudo chown root:root /usr/local/bin/avtools-wrapper
```

This wrapper expects commands like:
```bash
sudo -u avdaemon /usr/local/bin/avtools-wrapper run-eam
sudo -u avdaemon /usr/local/bin/avtools-wrapper run-landb
sudo -u avdaemon /usr/local/bin/avtools-wrapper snmp-influx
```

---

## 5) systemd service + timers (template instances)

Create the template service **/etc/systemd/system/avtools@.service**:

```ini
[Unit]
Description=AV Tools daemon (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=avdaemon
Group=avdaemon
EnvironmentFile=/etc/avtools/avtools.env
# Use the wrapper for all instances:
ExecStart=/usr/local/bin/avtools-wrapper %i
# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ProtectKernelTunables=true
ProtectControlGroups=true
ProtectKernelModules=true
LockPersonality=true
RestrictRealtime=true
RestrictNamespaces=true
RuntimeDirectory=avtools
StateDirectory=avtools
WorkingDirectory=/var/lib/avtools
# Restart policy
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

> If a package-provided unit already exists, add a drop-in to enforce the wrapper:
> `/etc/systemd/system/avtools@.service.d/10-exec-wrapper.conf`
> ```ini
> [Service]
> ExecStart=
> ExecStart=/usr/local/bin/avtools-wrapper %i
> ```

### Timers

Create timers for each job you want scheduled, e.g.:

**/etc/systemd/system/avtools@run-eam.timer**
```ini
[Unit]
Description=Run AV Tools: run-eam on schedule

[Timer]
OnCalendar=*:0/15        # every 15 minutes
Persistent=true          # catch-up if the host was down

[Install]
WantedBy=timers.target
```

**/etc/systemd/system/avtools@run-landb.timer**
```ini
[Unit]
Description=Run AV Tools: run-landb on schedule

[Timer]
OnCalendar=*:0/20
Persistent=true

[Install]
WantedBy=timers.target
```

**/etc/systemd/system/avtools@snmp-influx.timer**
```ini
[Unit]
Description=Run AV Tools: snmp-influx on schedule

[Timer]
OnCalendar=*:0/10
Persistent=true

[Install]
WantedBy=timers.target
```

Enable & start:
```bash
sudo systemctl daemon-reload

# One-time enable
sudo systemctl enable avtools@run-eam.service avtools@run-landb.service avtools@snmp-influx.service
sudo systemctl enable avtools@run-eam.timer avtools@run-landb.timer avtools@snmp-influx.timer

# Start timers now
sudo systemctl start avtools@run-eam.timer avtools@run-landb.timer avtools@snmp-influx.timer

# Check status
systemctl list-timers '*avtools*'
```

> **Forcing a run manually** (bypasses the timer schedule):
> ```bash
> sudo systemctl start avtools@run-eam.service
> sudo systemctl start avtools@run-landb.service
> sudo systemctl start avtools@snmp-influx.service
> ```

Logs:
```bash
journalctl -u 'avtools@*.service' -n 200 --no-pager
journalctl -u avtools@run-eam.service -f
```

---

## 6) Optional: cron ↔ systemd bridge

**Systemd timers are preferred.** If legacy infra requires cron, use cron to trigger the systemd instances so behavior stays identical to timers.

Create `/etc/cron.d/avtools` (owner `root`, mode `0644`):
```cron
# m h dom mon dow user  command
*/15 * * * * root /usr/bin/systemctl start avtools@run-eam.service
*/20 * * * * root /usr/bin/systemctl start avtools@run-landb.service
*/10 * * * * root /usr/bin/systemctl start avtools@snmp-influx.service
```

This **does not** run the jobs directly; it delegates to the same systemd units that use the wrapper and the `avdaemon` user.

---

## 7) Operating as avdaemon

- All services run as `User=avdaemon`, `Group=avdaemon` (see the unit above).
- The environment **must** be in `/etc/avtools/avtools.env` (0600/0640 recommended).
- Token/cache files live in `/var/lib/avtools` and logs in `/var/log/avtools`.

Useful commands:
```bash
# Check which binary the service uses (wrapper → venv)
systemctl cat avtools@run-eam.service

# Confirm wrapper is active
systemctl show -p FragmentPath -p DropInPaths avtools@run-eam.service
systemctl cat avtools@run-eam.service

# Run as avdaemon for quick checks
sudo -u avdaemon /usr/local/bin/avtools-wrapper run-eam --help
```

---

## 8) Upgrading AV Tools on the server

1. Stop timers (optional, to avoid concurrent runs):
   ```bash
   sudo systemctl stop avtools@run-eam.timer avtools@run-landb.timer avtools@snmp-influx.timer
   ```

2. Upgrade the package in the venv:
   ```bash
   sudo /opt/avtools/.venv/bin/pip install -U \
     --index-url https://pypi-itdcim.cern.ch/simple \
     --cert /etc/pki/tls/certs/ca-bundle.crt \
     --no-cache-dir avtools==X.Y.Z
   ```

3. Reload systemd units (if unit files changed) and resume timers:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl start avtools@run-eam.timer avtools@run-landb.timer avtools@snmp-influx.timer
   ```

4. Force a run to validate:
   ```bash
   sudo systemctl start avtools@run-eam.service
   journalctl -u avtools@run-eam.service -n 200 --no-pager
   ```

---

## 9) Sanity checks & troubleshooting

- **Wrapper in use?**
  ```bash
  systemctl cat avtools@run-eam.service | sed -n '1,200p'
  ```
- **Environment loaded?** Confirm `EnvironmentFile` and that `/etc/avtools/avtools.env` is readable by `avdaemon`.
- **Permissions:** `/opt/avtools` owned by `avdaemon`, secrets `0640` or tighter.
- **CA bundle:** `REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt` in env.
- **Network:** `After=Wants=network-online.target` present; `systemd-networkd-wait-online` or NM dispatcher available.
- **Manual job run:** `sudo -u avdaemon /usr/local/bin/avtools-wrapper run-eam -v` (if `-v` supported).
- **Timer schedule view:**
  ```bash
  systemctl list-timers | grep avtools
  systemctl status avtools@run-eam.timer
  ```
- **Logs:** `journalctl -u avtools@run-eam.service -b` for current boot.
- **Exit codes:** non‑zero exits trigger restart policy (`on-failure`); see `systemctl status` for last exit.

---

## 10) Security hardening (recommended)

- Keep secrets out of unit files; use `EnvironmentFile` with tight perms.
- Consider `ReadWritePaths=/var/lib/avtools /var/log/avtools` and `ReadOnlyPaths=/` to further sandbox.
- Use `PrivateTmp=true`, `ProtectSystem=full`, `ProtectHome=true` (already in the template).
- Rotate logs via journald defaults or `logrotate` if the app writes to files.
- Ensure **LANDB_TOKEN_FILE** lives under `/var/lib/avtools` with `0600` permissions.

---

## 11) Quick checklist (from zero to running)

1. Create `avdaemon`, dirs, and permissions
2. Create venv in `/opt/avtools/.venv` and `pip install` from **itdcim**
3. Write `/etc/avtools/avtools.env` (mask secrets)
4. Install `/usr/local/bin/avtools-wrapper`
5. Write `avtools@.service` and required `*.timer` units
6. `systemctl daemon-reload`
7. `systemctl enable --now avtools@*.timer`
8. `systemctl start avtools@run-eam.service` (manual test)
9. Verify logs and data landing in Postgres/Influx as expected

---

## 12) Puppet

For managed deployments at CERN, see the Puppet codebase:

- **it-puppet-hostgroup-itdcim**
  https://gitlab.cern.ch/ai/it-puppet-hostgroup-itdcim

> The Puppet profile includes the same primitives as above (env file, wrapper, units, timers). Prefer Puppet for fleet-wide consistency; use this README as the authoritative manual procedure for single-host and break‑glass scenarios.

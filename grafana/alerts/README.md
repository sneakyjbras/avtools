# AV Tools — Grafana Alert Rules (MonIT) via Rule-Group Provisioning API

AV Tools alert rules in CERN MonIT Grafana are managed with the **Alerting
Provisioning HTTP API** "rule-group PUT" endpoint. We do **not** POST individual
rules — we upload an entire **rule group** (evaluation group) in one atomic PUT.

---

## Source of truth & environments

Each rule group has **one PROD payload** in this directory. The **QA** variant is
**derived from PROD at deploy time** by `../scripts/patch_grafana_rulegroup.py`.
There is no committed QA JSON, so prod and QA cannot drift.

```
grafana/alerts/
  avtools-eam-dq-weekly.rulegroup.PUT.json     # weekly DQ checks (14 rules)
  avtools-not-networked.rulegroup.PUT.json     # weekly: devices absent from Prometheus
  avtools-network-state.rulegroup.PUT.json     # 5-min: offline / online-without-SNMP
  README.md
  ALERTS_NOTES.md                              # design notes, per-device + routing
```

| | PROD | QA |
|---|---|---|
| Grafana | `https://monit-grafana.cern.ch` | same |
| Org | `12` | `12` |
| Folder | `beuar1of5bo5cf` (av-dashboard) | `7BZSQJX4z` (**playground**) |
| Group name | `<base>` | `<base>-qa` |
| Receiver | `AV Tools` | `AV Test` |
| Postgres datasource | `ed690575-…-81f47592f349` | `dfaue906qonpcf` |
| PromQL `submitter_environment` | `"prod"` | `"qa"` |
| Prometheus datasource | `afcql1n10tnggc` (shared) | same |

> Folder UID comes from the folder URL:
> `https://monit-grafana.cern.ch/dashboards/f/<FOLDER_UID>/...`

---

## Routing label

Every rule carries `av_alert_type`:

- `inventory` — `avtools-eam-dq-weekly`, `avtools-not-networked`
- `operational` — `avtools-network-state`

Notification-policy `repeat_interval` (operational `1d`, inventory `4w`) and
per-device grouping on `equipmentno` are configured in the Grafana
**Notification policies** UI, not in this JSON. See `ALERTS_NOTES.md`.

---

## Deploy

### CI (recommended) — manual, separate from dashboards

| Job | Stage | Action |
|---|---|---|
| `deploy_grafana_alerts_qa` | `deploy_qa` | publish all groups to QA (playground) |
| `deploy_grafana_alerts_prod` | `deploy_stable` | publish all groups to PROD |

Both are `when: manual`. Trigger from the pipeline ▶ buttons. Requires
`GRAFANA_API_TOKEN_QA` / `GRAFANA_API_TOKEN_PROD` (see the CI file's
Protected-variable note).

### Local

```bash
export GRAFANA_API_TOKEN="<service-account token for the target org>"

# All groups for one environment (QA derived on the fly):
./scripts/sync_grafana_rulegroup.sh qa        # or: prod | both
./scripts/sync_grafana_rulegroup_qa.sh put    # QA convenience wrapper

# Preview what would be sent, without deploying:
python3 scripts/patch_grafana_rulegroup.py qa \
  grafana/alerts/avtools-network-state.rulegroup.PUT.json /tmp/preview-qa.json
```

The sync script publishes **every** `*.rulegroup.PUT.json` in this directory; the
group name and folder in each PUT URL are taken from the patched payload, so they
can never disagree with the body.

---

## Payload requirements

Each file is an **AlertRuleGroup** object:

- `title` — rule group name (e.g. `avtools-network-state`)
- `folderUid` — folder UID (PROD `beuar1of5bo5cf`)
- `interval` — **integer seconds** (NOT `"1w"`): weekly `604800`, 5-min `300`.
  Must be divisible by the scheduler tick (MonIT commonly 10s).

Keep rule `uid`s stable (Grafana identity). Prefer small diffs per commit. Never
embed secrets in `rawSql`.

---

## Troubleshooting

- **"interval (0s) should be non-zero…"** — `interval` must be integer seconds
  (`604800`, `300`), not a duration string.
- **401 / "Invalid API key"** — use a Service Account token, exported correctly,
  not revoked.
- **403 Forbidden** — service-account role too weak (Viewer won't work) or
  missing folder RBAC.
- **"Missing required CI/CD variable GRAFANA_API_TOKEN_*"** — usually the
  variable is marked *Protected* but the branch/tag isn't protected. Uncheck
  Protected, or protect the branch/tag. (Full note in `.gitlab-ci.yml`.)

---

## Security

Never paste tokens into tickets/chats/logs or commit them. Prefer short-lived,
minimally-scoped service-account tokens; rotate periodically.

---

## Contact

AV Tools / IT-DCIM — CERN. Use repo issues or the team channel for review.

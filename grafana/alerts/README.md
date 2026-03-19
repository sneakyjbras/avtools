# AV Tools — Grafana Alert Rules (MonIT) via Rule-Group Provisioning API

This repo manages AV Tools alert rules in CERN MonIT Grafana using the **Alerting Provisioning HTTP API** “rule-group PUT” endpoint.

We do **not** POST individual rules. We upload an entire **rule group** (evaluation group) in one payload.

---

## Why this approach

On `https://monit-grafana.cern.ch`, the most reliable workflow is:

- Maintain a single JSON payload representing a **rule group**
- Upload it with a single **PUT**
- Grafana updates the whole group atomically

This avoids common issues seen with other endpoints (e.g., missing `folderUID`, unsupported formats, `/api/ruler` not exposed, etc.).

---

## Files

Recommended layout:

```
alerting/
  avtools-eam-dq-weekly.rulegroup.PUT.json
  README.md
```

- `avtools-eam-dq-weekly.rulegroup.PUT.json`  
  Payload for `PUT /api/v1/provisioning/folder/<folderUid>/rule-groups/<groupName>`

---

## Target location in Grafana

- Grafana: `https://monit-grafana.cern.ch`
- Org: `12`
- Folder UID: `beuar1of5bo5cf`  (av-dashboard folder)
- Rule group name: `avtools-eam-dq-weekly`

> Folder UID is taken from the folder URL:
> `https://monit-grafana.cern.ch/dashboards/f/<FOLDER_UID>/...`

---

## Prerequisites

### 1) Service Account Token (required)

Use a **Service Account token** with sufficient permissions (typically **Editor** or **Admin**) on the target org/folder.

⚠️ Never commit tokens to git.

Export your token locally:

```bash
export GRAFANA_API_TOKEN="REDACTED"
```

Sanity check (should return the service account user JSON):

```bash
curl -s -H "Authorization: Bearer $GRAFANA_API_TOKEN" https://monit-grafana.cern.ch/api/user
```

---

## Deploy / Update the rule group (one-liner)

From the directory containing the JSON payload:

```bash
FOLDER_UID="beuar1of5bo5cf"; GROUP="avtools-eam-dq-weekly"; FILE="alerting/avtools-eam-dq-weekly.rulegroup.PUT.json"; \
curl -sS -X PUT -H "Authorization: Bearer $GRAFANA_API_TOKEN" -H "Content-Type: application/json" --data-binary @"$FILE" \
"https://monit-grafana.cern.ch/api/v1/provisioning/folder/$FOLDER_UID/rule-groups/$GROUP"
```

If successful, the response should not be an error JSON. Rules will appear under:
**Alerting → Alert rules → Folder: av-dashboard → Group: avtools-eam-dq-weekly**

---

## Payload requirements (important)

The JSON must be an **AlertRuleGroup** object:

- `title`: rule group name (e.g., `avtools-eam-dq-weekly`)
- `folderUid`: folder UID (e.g., `beuar1of5bo5cf`)
- `interval`: **integer seconds** (NOT `"1w"`, NOT `"168h"`)

Example:
- Weekly: `604800` seconds (7 * 24 * 60 * 60)

Scheduler compatibility:
- Interval must be divisible by the scheduler tick (MonIT commonly uses 10s)

---

## Editing rules safely

### Workflow
1. Edit `alerting/avtools-eam-dq-weekly.rulegroup.PUT.json`
2. Keep rule `uid`s stable (Grafana uses them for identity)
3. Re-run the PUT command

### Good practices
- Keep rules ordered by ID (e.g., 01…15) for readability
- Prefer small changes per commit (easier review)
- Keep annotations (`summary`, `description`) consistent
- Avoid embedding secrets in `rawSql` (should not happen, but stay mindful)

---

## Troubleshooting

### “invalid alert rule: interval (0s) should be non-zero…”
Your group `interval` is being interpreted as 0s.

Fix: use integer seconds, e.g.
- Weekly: `604800`
- Daily: `86400`
- Every 5 minutes: `300`

### “Invalid API key” / 401
- Token is wrong type (use **Service Account token**, not legacy API key)
- Token not exported correctly
- Token revoked/expired

### 403 Forbidden
- Service account role too weak (Viewer won’t work)
- Missing folder permissions (RBAC)

---

## Security notes

- Never paste tokens into tickets/chats/logs
- Do not commit tokens to GitLab
- Prefer rotating tokens periodically
- Keep tokens scoped to the minimum role required

---

## Contact

AV Tools / IT-DCIM — CERN  
(Use the repo issues or the team channel for changes/review.)


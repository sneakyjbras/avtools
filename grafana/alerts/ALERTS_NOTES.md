# AV Tools — Alerts update (PROD + QA, per-device)

Three rule groups, one source of truth per group. QA is **derived from PROD at
deploy time** by `scripts/patch_grafana_rulegroup.py` — there are no
hand-maintained QA payloads to drift. The existing DQ group changes in exactly
**one** way: an `av_alert_type` label is added to every rule for
notification-policy routing.

## Rule groups

| Group (PROD) | Source file | Interval | Rules | `av_alert_type` |
|---|---|---|---|---|
| `avtools-eam-dq-weekly` *(existing)* | `grafana/alerts/avtools-eam-dq-weekly.rulegroup.PUT.json` | `604800` (weekly, unchanged) | 14 (unchanged) | **+`inventory`** |
| `avtools-not-networked` *(new)* | `grafana/alerts/avtools-not-networked.rulegroup.PUT.json` | `604800` (weekly) | 1 — Alert 1 | `inventory` |
| `avtools-network-state` *(new)* | `grafana/alerts/avtools-network-state.rulegroup.PUT.json` | `300` (5 min) | 2 — Alerts 2 + 3 | `operational` |

The QA equivalents (`*-qa` groups in the **playground** folder) are produced by
the patcher and previewed under `review/qa/` for inspection.

## The three new alerts

1. **Devices not networked (absent from Prometheus)** — `avtools-not-networked`.
   Pure-Postgres anti-join: active `eam_devices` with no row in
   `landb_ipaddresses` (no LanDB IP ⇒ never scraped ⇒ never in Prometheus).
   Grafana cannot string-key-anti-join Postgres against Prometheus, so this is
   expressed entirely in SQL, which faithfully equals "not networked". Weekly —
   it is a slow inventory condition.
2. **Devices offline (latest ping state)** — `avtools-network-state`.
   `(max by (equipmentno) (last_over_time(avtools_ping_check_status{submitter_environment="prod"}[30d])) == 0) + 1`.
3. **Online devices without SNMP** — `avtools-network-state`.
   `(… ping == 1) and (… snmp_probe_status == 0)` — online **and** SNMP-down.

Join key is **`equipmentno`** throughout (the EAM primary key and the mandatory
Prometheus label). Serial number is not a Prometheus label, so a serial-based
match is impossible; `equipmentno` is the correct, robust key.

## Per-device firing

Every new rule emits **one alert instance per `equipmentno`**, not an aggregate
count:

- **Alert 1 (SQL)** returns one row per device — columns `equipmentno` (becomes
  the series label) + `value = 1`; threshold `gt 0` fires per row.
- **Alert 2 (PromQL)** yields one series per offline device, value 1.
- **Alert 3 (PromQL)** yields one series per device that is online **and**
  SNMP-down; offline devices are excluded by the online-gate (`and`).

`noDataState` is **`OK`** on the three new rules (not `NoData` like the DQ
rules): a per-device filter returns an empty frame when the whole fleet is
healthy, and `OK` makes that read as Normal rather than NoData. The DQ rules are
untouched and keep `NoData`.

## Labels → notification policies (manual UI step)

The cadence you want (daily reminders for operational, weekly→monthly for
inventory) is **`repeat_interval`, which lives in Notification policies, not in
the rule JSON**. Add two child policies under the default policy:

| Matcher | repeat_interval | Group by | Covers |
|---|---|---|---|
| `av_alert_type = operational` | `1d` | add `equipmentno` | offline / SNMP (`avtools-network-state`) |
| `av_alert_type = inventory` | `4w` | add `equipmentno` | DQ + not-networked |

Two reminders from the policy screen you shared:

- The **default policy repeat interval is `52w`** today (effectively
  fire-once). The child policies above override it for these alerts.
- Default grouping is `grafana_folder, alertname`. For per-device alerts you
  must **add `equipmentno` to the grouping** (toggle "Override grouping"), or
  Grafana bundles every firing device for one alertname into a single
  notification and you lose per-device granularity.

## PROD vs QA — the deterministic transform

`patch_grafana_rulegroup.py <env> <src> <out>` renders a PROD source for `prod`
or `qa`. QA = PROD with exactly these substitutions:

| Aspect | PROD | QA |
|---|---|---|
| Folder UID | `beuar1of5bo5cf` (av-dashboard) | `7BZSQJX4z` (**playground**) |
| Group name | `<base>` | `<base>-qa` |
| Rule UID | `<uid>` | `<uid>-qa` |
| Rule title | `AV Tools: …` | `QA - AV Tools: …` |
| Receiver | `AV Tools` | `AV Test` |
| Postgres datasource | `ed690575-…-81f47592f349` | `dfaue906qonpcf` |
| Dashboard link `__dashboardUid__` | `av_devices_dashboard` | `av_devices_dashboard-qa` |
| PromQL `submitter_environment` | `"prod"` | `"qa"` |
| Prometheus datasource | `afcql1n10tnggc` (shared — **not** rewritten) | same |
| Group `interval` | per file | same as PROD |

The transform is bijective and idempotent: `prod(source) == source` (modulo the
server-assigned `id`, which is intentionally stripped), `qa(qa(x)) == qa(x)`, and
`prod(qa(x)) == prod(x)`.

## Existing QA DQ group — update in place, no duplicate

The QA DQ group `avtools-eam-dq-weekly-qa` already exists in **playground**. The
QA render produced here carries the **same rule UIDs** (`afccb41r2fjswd-qa`, …)
and the same group/folder, so a PUT **updates the existing rules in place** and
simply adds `av_alert_type: inventory`. It does not create a second copy. (Grafana
identifies provisioned rules by UID; the "Provisioned" badge you see is the
provisioning-API provenance — the same flow these scripts use.)

## Deploy

CI (recommended) — two new **manual** jobs, separate from the dashboard deploys:

- `deploy_grafana_alerts_qa` (stage `deploy_qa`) → `sync_grafana_rulegroup_qa.sh put`
- `deploy_grafana_alerts_prod` (stage `deploy_stable`) → `sync_grafana_rulegroup.sh prod`

Each publishes **every** `grafana/alerts/*.rulegroup.PUT.json` (3 groups),
deriving QA on the fly. Trigger from the pipeline's manual ▶ buttons.

Local (same scripts):

```bash
export GRAFANA_API_TOKEN="<service-account token for the target org>"
./scripts/sync_grafana_rulegroup.sh qa     # or: prod | both
```

## Validated here vs needs your live stack

Verified in this bundle: all payloads are valid JSON; the PROD DQ file is the
committed file **plus the label, nothing else** (zero other diffs); the QA
renders match the existing playground group exactly (UIDs/titles/receiver/folder)
plus the label; the QA env transform is correct (QA Postgres UID, `qa` env label
in PromQL, `-qa` groups/UIDs, `-qa` dashboard links, no PROD datasource string
leaks into QA); per-device output and `gt 0` condition wiring confirmed; the
not-networked SQL output columns are `[equipmentno, value]`.

Needs your stack: run the two PromQL exprs in Explore against Mimir (prod + QA)
and confirm the per-device series match the dashboards; confirm a few
`equipmentno`s from Alert 1 genuinely have no LanDB IP; after wiring the
policies, fire a test (pause/unpause, or a known-offline device) to confirm one
notification per device at the expected repeat interval.

## Tunables

- `interval`: `604800` (not-networked / DQ), `300` (network-state). Keep
  divisible by the 10s scheduler tick.
- `for`: `0s` (matches existing). Raise on the operational rules to require a
  sustained state if you see flapping.
- `[30d]` lookback: latest-known-state window, consistent with the dashboards.
  Shorter = stricter "recently offline"; longer = "last known". A withdrawn
  device lingers until its sample ages out.
- Per-device cardinality: Alert 1 can fire for many devices at once. The planned
  EAM model change (a "not-networkable / monitoring-exempt" flag) will let you
  exclude devices that legitimately cannot be networked; the SQL's
  `active_devices` CTE is where that exclusion goes.

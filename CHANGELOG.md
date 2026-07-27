# Changelog

## [Unreleased]

### Changed
- **License & Maintainers update**: Updated project license specification to MIT in `pyproject.toml` and updated maintainer/contributor guidelines in `CONTRIBUTING.md` setting José Bras (`jose.bras@cern.ch` / `j.eduardo.bras@outlook.com`, `@jsapinat` / `@sneakyjbras`) as the sole core author and maintainer.

## [1.10.0] — 2026-07-27

### Added
- **`eam_rooms` precompute service**: Precomputes device→room mapping to support simplified room-based querying.

## [1.9.3] — 2026-07-26

### Changed
- Miscellaneous fixes and version bump.

## [1.9.2] — 2026-07-25

### Fixed
- **Stable OTLP `service.instance.id` — the real fix for the k8s-only ~1h metric
  gaps.** The instance id maps to the Prometheus/Mimir `instance` label (part of
  every series' identity) and was `socket.gethostname()` — on Kubernetes that's
  the *ephemeral pod name*, a new value every CronJob cycle × 8 shard pods. Each
  run therefore minted a fresh set of series, exploding MONIT/Mimir's active-series
  count until the tenant limit dropped samples — the ~1h on/off gaps seen only on
  k8s (the Puppet VM's stable hostname never trips it). Cadence changes (`*/2`↔`*/5`)
  never fixed it because the churn is per-cycle, not per-rate. Now a STABLE identity:
  `$AVTOOLS_INSTANCE_ID` if set, else `<service>-shard-<JOB_COMPLETION_INDEX>` on an
  Indexed Job, else the hostname (monolith unchanged). Also exposed as the new
  `instance_id` constructor argument.

## [1.9.1] — 2026-07-24

### Fixed
- **OTLP metric export no longer fails silently.** `OTLPMetricsPublisher.publish()`
  now checks the `MeterProvider.force_flush()` result (which returns `False` on a
  timed-out/rejected export instead of raising), retries with backoff, and returns
  a boolean. An unconfirmed export is logged loudly (`otlp_export_unconfirmed` /
  `otlp_publish_unconfirmed`) instead of being swallowed as `status=ok`. This was
  the silent failure behind the ~1-hour gaps seen only on Kubernetes (8 shard pods
  exporting to MONIT concurrently) and never on the single-stream Puppet VM. A
  failed export is now visible and retried, **not** fatal to the collection cycle
  (the DB write already succeeded).
- **Reduced concurrent export load.** The periodic export interval now defaults to
  well beyond a batch job's lifetime, so `force_flush()` is the sole deterministic
  export — removing the per-second export churn multiplied across 8 concurrent
  shard pods.

## [1.9.0] — 2026-07-19 — codename: boros

### Added

- **Sharded SNMP collection** — the fleet is partitioned across N pods with no
  coordinator: each shard keeps devices where `crc32(equipment_no) % shard_total
  == shard_index` (`device_in_shard`). Driven by `--shard-index` / `--shard-total`
  (`JOB_COMPLETION_INDEX` / `SHARD_TOTAL` env). `shard_total = 1` is a no-op, so
  the single-machine monolith behaviour is unchanged. Only `snmp-timeseries`
  shards; `run-eam` / `run-landb` stay single-node (reconciliation needs the
  global inventory view).
- **Container image built from source** for the Kubernetes (Magnum) deployment: a
  non-root `Dockerfile` + a kaniko `docker_build` CI job publishing
  `registry.cern.ch/avtools/avtools:{qa,prod}`.
- **Sentry error tracking** (`avtools.observability.sentry`): a no-op unless
  `SENTRY_DSN` is set; scrubs device IPs / equipment numbers; shipped as an
  optional `[sentry]` extra so the monolith wheel is unaffected.
- **Kubernetes reliability SLOs** (`grafana/alerts/avtools-k8s-slo.rulegroup`).
- **`.env.example`** for local sharded-sweep testing.

### Changed

- **Deployment split into `itdcim/av-tools-infra`** (Terraform for the Magnum
  cluster, Helm chart, ArgoCD, and the tbag→Secret bridge). This repo owns the
  application and the container image it builds; the running deployment lives in
  that GitOps repo.


## [1.8.10] — 2026-07-11 — codename: boros

### Changed

- **All alert notifications collapsed to a weekly cadence.** Both
  `group_interval` and `repeat_interval` are set to `168h` (1 week) on every
  rule across `grafana/alerts/*.rulegroup.PUT.json`, so no alert group can email
  more than ~once per week regardless of how much its membership churns. This is
  the agreed fallback after the per-family tuning in 1.8.8 (group_interval 1h,
  repeat 6h/1w) did not reduce mail volume enough. The `for: 5m` flap damping
  from 1.8.8 is retained.
- **Note:** these settings live in Grafana and only change when the Grafana
  alert deploy job actually runs (`deploy_grafana_alerts_qa` / `_prod`). Editing,
  merging, or tagging does not itself push anything to Grafana — the manual
  deploy button must be clicked (or `sync_grafana_rulegroup.sh` run with a token).


## [1.8.9] — 2026-07-11 — codename: boros

### Fixed

- **QA Grafana deploys now also appear on tag pipelines.** In 1.8.8 the QA jobs
  (`deploy_grafana_qa`, `deploy_grafana_alerts_qa`) were gated to
  `$CI_COMMIT_BRANCH == "qa"`, which is empty on tag pipelines — so tagging a
  release hid the QA deploy buttons. Added a `$CI_COMMIT_TAG` manual rule to
  both, so a tagged release can be validated in QA before the PROD (tag-only)
  buttons are used. PROD jobs unchanged.


## [1.8.8] — 2026-07-11 — codename: boros

### Changed

- **Alert notification cadence retuned to stop the 5-minute mail flood.** Across
  the `grafana/alerts/*.rulegroup.PUT.json` groups: `group_interval` `5m → 1h`
  (caps re-mails on group-membership churn to hourly), `repeat_interval` split
  by `av_alert_type` — operational/environmental/SLO `→ 6h`, inventory
  (`eam-dq-weekly`, `not-networked`) `→ 168h` (weekly) — and `for` `0s → 5m` on
  the flappy operational rules (`network-state` offline, `pdu-power-quality`,
  `pdu-state`) to damp transient flaps at the source. Real outages still remind
  every 6h; slow inventory conditions go quiet for a week. The QA patcher leaves
  these timing fields untouched, so QA inherits the same cadence.
- **CI: Grafana deploys are environment-per-trigger.** `deploy_grafana_qa` /
  `deploy_grafana_alerts_qa` are offered as optional manual jobs on `qa`-branch
  commits/merges; `deploy_grafana_prod` / `deploy_grafana_alerts_prod` appear
  only on tag pipelines (promote-to-production). All four are `allow_failure:
  true` so the manual buttons are non-blocking. (1.8.9 also surfaces the QA jobs
  on tag pipelines.)


## [1.8.7] — 2026-07-02 — codename: boros

### Changed

- **`cycle_summary` now splits `failed`** into `failed_equipment` (down devices that
  carry an `equipmentno` — the actionable set that also emits `snmp_probe_failure`)
  and `unreachable_targets` (down raw LanDB IP targets never SNMP-managed — structural
  noise). `failed` is retained and always equals their sum, so existing consumers are
  unaffected. Alerting should key on `failed_equipment`, not the raw `failed` total.


## [1.8.6] — 2026-06-30 — codename: boros

### Added

- **Structured-log sink (`avtools.logsink`)** — the producer end of the OpenSearch
  logging contract. structlog now renders to a JSON-lines file (tailed by Fluent
  Bit → OTLP → `otel-logs_<tenant>`) **and** the console/journal at once. Envelope
  fields (`service`, `host`, `submitter_environment`, `hostgroup`, `cycle_id`) are
  bound as contextvars on every event; `--log-file` (env `AVTOOLS_LOG_FILE`,
  default `/var/log/avtools/avtools.jsonl`) sets the path.
- **`snmp_probe_failure` event** — per-device, with `equipmentno` / `reason` / `ip`.
  Routed to a dedicated file-only logger so a mass-down period does not flood the
  journal. SNMP failures are classified (`classify_snmp_failure`) into
  `timeout | host_unreachable | network_unreachable | auth_failure | mib_error | unknown`.
  NOTE: this stack is SNMPv2c, so a rotated community surfaces as **`timeout`**
  (silent drop), not `auth_failure` — the rotated-credential alarm must therefore
  key on a timeout-burst-while-ping-up, not on `auth_failure`.
- **`cycle_summary` event** — one per SNMP cycle: `targeted` / `polled` / `failed` /
  `duration_s` (+ `status`).

### Changed

- `AbstractDeviceHandler.probe()` now delegates to **`probe_with_reason()`**, which
  returns `(ok, reason)`; `probe()` keeps its boolean contract. `ProbeResult` gained
  a `reason` field (defaulted, so existing constructors are unaffected).


## [1.8.4] — 2026-06-26 — codename: boros

### Changed

- **`Online Devices Without SNMP`** operational alert (`avnetsnmpdown`,
  `avtools-network-state` group) hardened. This rule catches the
  reachable-but-not-yielding case ping cannot see — a rotated community string,
  a wedged SNMP agent, a dropped UDP/161 ACL, or a broken MIB all leave ping
  green while SNMP data freezes (a failed probe emits `snmp_probe_status=0`, it
  does not go absent, so the `== 0` test is the correct signal).
  - `for: 0s` → **`for: 10m`**: no longer fires on a single transient SNMP cycle
    miss; the real failure modes are persistent, so the ~10-minute delay is
    immaterial while per-device noise from one-off UDP losses is suppressed.
  - Added **`severity: warning`** (was unset): SNMP-stale-but-pingable is a
    degradation, not an outage, and it now routes/colours accordingly.


## [1.8.2] — 2026-06-26 — codename: boros

### Added

- **Fleet-availability SLO** (`avsloavailwarn` / `avsloavailcrit` in the
  `avtools-slo` group): alarms on the share of the fleet reachable by ping
  (`sum(reachable) / max(targeted)`). **Baseline-relative**, not absolute — it
  fires when reachability drops >10% (warning) / >20% (critical) below its
  trailing-day average. AV devices are routinely powered off, so the baseline
  self-adjusts to the normal off-rate and the alarm flags only *unexpected* mass
  unreachability. Per-device offline detection stays in the operational family
  (`Devices Offline`) and is unchanged.
- **Rooms dashboard**: *Fleet Availability (now)* stat + *Fleet Availability —
  Over Time* timeseries (panels 33/34), so the real reachability distribution is
  visible before any absolute target is considered.

### Changed

- **Details single-value panels** (stats, gauges, ON/OFF) now select the latest
  replica with `topk by (equipmentno) (1, …)` instead of `max by (equipmentno)`.
  `topk` preserves the winning series' labels, which is required for the
  string-`_info` panels (Firmware, Status MIB, PDU status stats) and the
  per-row tables (Interfaces, Outlets) — `max` dropped those display labels.
- **Details timeseries panels** keep all N redundant-replica series (the prior
  `max by (equipmentno)` collapse is reverted); legend collapsed to one entry.
  More replicas → finer time-granularity. Aggregate *count* plots (Active
  Outlets, Interfaces Up) stay replica-collapsed to a single true count.

### Fixed

- **Details freshness panels** (Last Checked, SNMP Last Sample, Minutes Since)
  used `timestamp(last_over_time(…))`, which returns the *evaluation* time, not
  the sample time — so "minutes since" read ≈0 regardless of staleness. Now use
  the bare-selector `timestamp(…)` of the freshest replica (real sample age;
  visible up to the 5-minute lookback, beyond which the freshness SLO fires).


## [1.5.0] — 2026-06-12 — codename: illidan

### Fixed

- **`avtools-service.sh`**: `snmp-timeseries` now explicitly passes
  `--environment "$AVTOOLS_ENVIRONMENT"`, `--hostgroup "$AVTOOLS_HOSTGROUP"`,
  and `--availability-zone "$AVTOOLS_AVAILABILITY_ZONE"` to the CLI instead of
  relying on Click's implicit `envvar` fallback.  A `:?` guard on
  `AVTOOLS_ENVIRONMENT` causes the service to fail loudly at startup if the
  variable is absent, preventing metrics from being silently mislabelled with
  the Click default (`"prod"`) in QA, or vice-versa.  `AVTOOLS_HOSTGROUP` and
  `AVTOOLS_AVAILABILITY_ZONE` receive safe `:=` defaults that match the CLI
  defaults so existing deployments without those Puppet variables are unaffected.

## [1.4.1] — 2026-05-29 — codename: arthas

### Added

**Layer 1 — OTel resource attributes** (on every `ResourceMetrics` envelope)

| Attribute | Value | Notes |
|---|---|---|
| `service.name` | `"avtools"` (or `--service-name`) | Pre-existing |
| `service.instance.id` | hostname | Pre-existing |
| `service.version` | `avtools.__version__` | **New** |
| `service.namespace` | `"itdcim"` | **New** |

`service.version` is sourced from `avtools.__version__` (now defined in
`src/avtools/__init__.py`) so every metric export is stamped with the
running package version. Incident correlation with deployments becomes
instant in Grafana.

**Layer 2 — global metric labels** (merged into every `MetricSample` before export)

| Label | Default | CLI flag | Env var |
|---|---|---|---|
| `job` | `service_name` | — | — |
| `submitter_environment` | `"prod"` | `--environment` | `AVTOOLS_ENVIRONMENT` |
| `toplevel_hostgroup` | `"itdcim"` | — | — |
| `submitter_hostgroup` | `"itdcim/av"` | `--hostgroup` | `AVTOOLS_HOSTGROUP` |
| `region` | `"cern"` | — | — |
| `availability_zone` | `"cern-geneva-b"` | `--availability-zone` | `AVTOOLS_AVAILABILITY_ZONE` |

These labels mirror the `metric_labels` block in timeseries-DIP's Puppet config
and allow MONIT/Mimir to route and filter by environment, hostgroup, and job
without table joins.

A **collision guard** raises `OTLPPublishError` if a global label key would
silently shadow a per-device sample label key (the same guard timeseries-DIP
applies in `_apply_metric_labels`).

**Per-device label enrichment** (on each `MetricSample`, sourced from `CachedIPAddress`)

| Label | Source |
|---|---|
| `equipmentno` | EAM primary key — pre-existing, mandatory |
| `building` | LanDB `Device.location.building` |
| `room` | LanDB `Device.location.room` |
| `eq_class` | EAM `class_code` |
| `model` | EAM `model` |
| `category` | EAM `category_code` |
| `hostname` | LanDB `IPAddress.name` (DNS hostname) |

Fields that are `None` or empty are omitted from the label set (no empty
string labels). The enrichment is computed once in `run_snmp_timeseries`
as a `dict[str, dict[str, str]]` lookup keyed by `equipment_no` and passed
through `SNMPObserverRouter.process()` to `encode_all()`. The `encoder`
module has no direct dependency on the Postgres ORM.

### Changed

- `OTLPMetricsPublisher.__init__` accepts a new optional `metric_labels`
  keyword argument (`Mapping[str, str]`).
- `SNMPObserverRouter.process` accepts a new optional `device_lookup`
  keyword argument (`dict[str, dict[str, str]] | None`).
- `encode_ping`, `encode_probe`, `encode_queries`, and `encode_all` each
  accept an optional `device_lookup` keyword argument (default `None`,
  fully backward-compatible).
- `AVTools.run_snmp_timeseries` accepts three new keyword arguments:
  `submitter_environment`, `submitter_hostgroup`, `availability_zone`.
- `avtools snmp-timeseries` CLI gains three new options:
  `--environment`, `--hostgroup`, `--availability-zone`.

### Fixed

- `src/avtools/__init__.py` now exports `__version__ = "1.3.2"` so
  `service.version` is available at runtime without parsing `pyproject.toml`.

### Tests

New and updated unit tests in:
- `tests/unit/timeseries/encoder_test.py` — device label enrichment,
  partial lookup fallback, empty-equipmentno skip.
- `tests/unit/timeseries/otlp_publisher_test.py` — Layer 1 resource
  attribute assertions, Layer 2 collision guard, label merge into observations.
- `tests/unit/timeseries/otlp_publisher_additional_test.py` — double-publish
  gauge non-re-registration guard.
- `tests/unit/pipeline/snmp_router_test.py` — device_lookup forwarding,
  enriched label assertions, stat counters.
- `tests/unit/core/avtools_run_snmp_timeseries_test.py` — metric_labels
  content, device_lookup construction, null equipment_no exclusion.

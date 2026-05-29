# Changelog

## [Unreleased] — Prometheus metadata enrichment

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

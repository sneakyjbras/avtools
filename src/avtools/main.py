from __future__ import annotations

import click
import requests
import structlog

from avtools.core.av_tools import AVTools
from avtools.exception.errors import NoRecordsFound
from avtools.logsink import DEFAULT_LOG_FILE, bind_envelope, configure_logging
from avtools.observability import init_sentry
from avtools.timeseries import metrics as m
from avtools.timeseries.heartbeat import publish_heartbeat


def _otlp_heartbeat_options(f):
    """Attach the OTLP/deployment options needed to publish a heartbeat.

    Mirrors the snmp-timeseries OTLP block but reads everything from the shared
    avtools.env (EnvironmentFile), and makes tenant/password NON-required so the
    heartbeat is best-effort: a sync without MONIT creds still succeeds, just
    without a heartbeat. The MONIT password is exposed as --monit-password to avoid
    colliding with the EAM/LanDB credential options on these commands.
    """
    opts = [
        click.option(
            "--otlp-endpoint",
            envvar="MONIT_OTLP_ENDPOINT",
            default="monit-otlp.cern.ch:4316",
            show_default=True,
            help="MONIT OTLP gRPC endpoint (host:port).",
        ),
        click.option("--tenant", envvar="MONIT_TENANT", default=None, help="MONIT tenant name."),
        click.option(
            "--monit-password",
            envvar="MONIT_PASSWORD",
            default=None,
            hide_input=True,
            help="MONIT tenant password (heartbeat is skipped if unset).",
        ),
        click.option(
            "--service-name",
            envvar="OTEL_SERVICE_NAME",
            default="avtools",
            show_default=True,
            help="OpenTelemetry resource service.name.",
        ),
        click.option(
            "--otlp-ca-file",
            envvar="OTLP_CA_FILE",
            default=None,
            help="Optional CA bundle path for OTLP gRPC.",
        ),
        click.option(
            "--otlp-insecure/--otlp-tls",
            envvar="MONIT_OTLP_INSECURE",
            default=False,
            show_default=True,
            help="Use plaintext OTLP/gRPC (no TLS).",
        ),
        click.option(
            "--environment",
            envvar="AVTOOLS_ENVIRONMENT",
            default="prod",
            show_default=True,
            help="Deployment environment label (prod/qa).",
        ),
        click.option(
            "--hostgroup",
            envvar="AVTOOLS_HOSTGROUP",
            default="itdcim/av",
            show_default=True,
            help="submitter_hostgroup label.",
        ),
        click.option(
            "--availability-zone",
            envvar="AVTOOLS_AVAILABILITY_ZONE",
            default="cern-geneva-b",
            show_default=True,
            help="availability_zone label.",
        ),
    ]
    for opt in reversed(opts):
        f = opt(f)
    return f


def _emit_heartbeat(metric_name: str, **otlp) -> None:
    """Publish a heartbeat iff MONIT creds are available; otherwise skip quietly."""
    if not otlp.get("tenant") or not otlp.get("monit_password"):
        structlog.get_logger(__name__).info("heartbeat_skipped_no_creds", metric=metric_name)
        return
    publish_heartbeat(
        metric_name,
        otlp_endpoint=otlp["otlp_endpoint"],
        tenant=otlp["tenant"],
        password=otlp["monit_password"],
        service_name=otlp["service_name"],
        otlp_ca_file=otlp["otlp_ca_file"],
        otlp_insecure=otlp["otlp_insecure"],
        environment=otlp["environment"],
        hostgroup=otlp["hostgroup"],
        availability_zone=otlp["availability_zone"],
    )


logger = structlog.get_logger(__name__)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


@click.group(
    context_settings={"show_default": True},
    help="CLI to interact with AVTools equipment and data stores.",
)
@click.option(
    "--logs/--no-logs",
    default=False,
    help="Enable verbose logging.",
)
@click.option(
    "--dbod-url",
    envvar="DATABASE_URL",
    required=True,
    help="DBoD PostgreSQL URL (env DATABASE_URL).",
)
@click.option(
    "--log-file",
    envvar="AVTOOLS_LOG_FILE",
    default=DEFAULT_LOG_FILE,
    show_default=True,
    help="JSON-lines log file tailed by Fluent Bit (env AVTOOLS_LOG_FILE).",
)
@click.pass_context
def cli(ctx: click.Context, logs: bool, dbod_url: str, log_file: str) -> None:
    ctx.obj = {"logs": logs, "dbod_url": dbod_url}
    configure_logging(logs, log_file)
    # No-op unless SENTRY_DSN is set (and sentry-sdk installed); safe on the monolith.
    init_sentry()


@cli.command("run-eam", help="Run EAM CRUD operations.")
@click.option(
    "--username",
    envvar="MY_USERNAME",
    required=True,
    help="EAM username.",
)
@click.option(
    "--password",
    envvar="MY_PASSWORD",
    required=True,
    hide_input=True,
    help="EAM password.",
)
@_otlp_heartbeat_options
@click.pass_context
def run_eam(ctx: click.Context, username: str, password: str, **otlp) -> None:
    bind_envelope("run-eam", otlp["environment"], otlp["hostgroup"])
    dbod_url = ctx.obj["dbod_url"]
    try:
        tools = AVTools(dbod_url)
        tools.run_eam(username=username, password=password)
    except NoRecordsFound as exc:
        raise click.ClickException(str(exc))
    # Heartbeat only after a successful sync (best-effort).
    _emit_heartbeat(m.EAM_LAST_RUN_TIMESTAMP, **otlp)


@cli.command("run-landb", help="Run LanDB CRUD operations.")
@click.option(
    "--client-id",
    envvar="LANDB_CLIENT_ID",
    required=True,
    help="OAuth2 client id.",
)
@click.option(
    "--client-secret",
    envvar="LANDB_CLIENT_SECRET",
    required=True,
    hide_input=True,
    help="OAuth2 client secret.",
)
@click.option(
    "--audience",
    envvar="LANDB_AUDIENCE",
    required=True,
    help="OAuth2 audience.",
)
@_otlp_heartbeat_options
@click.pass_context
def run_landb(
    ctx: click.Context,
    client_id: str,
    client_secret: str,
    audience: str,
    **otlp,
) -> None:
    bind_envelope("run-landb", otlp["environment"], otlp["hostgroup"])
    dbod_url = ctx.obj["dbod_url"]
    try:
        tools = AVTools(dbod_url)
        tools.run_landb(
            client_id=client_id,
            client_secret=client_secret,
            audience=audience,
        )
    except NoRecordsFound as exc:
        raise click.ClickException(str(exc))
    # Heartbeat only after a successful sync (best-effort).
    _emit_heartbeat(m.LANDB_LAST_RUN_TIMESTAMP, **otlp)


@cli.command(
    "sync-rooms",
    help=(
        "Recompute the device->room mapping from the EAM inventory cache and "
        "persist it into the eam_rooms cache table (reads Postgres only; no EAM/"
        "LanDB credentials required)."
    ),
)
@_otlp_heartbeat_options
@click.pass_context
def sync_rooms(ctx: click.Context, **otlp) -> None:
    bind_envelope("sync-rooms", otlp["environment"], otlp["hostgroup"])
    dbod_url = ctx.obj["dbod_url"]
    tools = AVTools(dbod_url)
    tools.sync_rooms()
    # Heartbeat only after a successful sync (best-effort).
    _emit_heartbeat(m.ROOMS_LAST_RUN_TIMESTAMP, **otlp)


@cli.command(
    "snmp-timeseries",
    help=(
        "Collect Ping/SNMP timeseries from devices in landb_ipaddresses and "
        "publish metrics to Prometheus via MONIT OTLP (Mimir)."
    ),
)
@click.option(
    "--threads",
    envvar="THREADS",
    default=8,
    show_default=True,
    type=int,
    help="Number of parallel workers.",
)
@click.option(
    "--otlp-endpoint",
    envvar="MONIT_OTLP_ENDPOINT",
    default="monit-otlp.cern.ch:4316",
    show_default=True,
    help="MONIT OTLP gRPC endpoint (host:port).",
)
@click.option(
    "--tenant",
    envvar="MONIT_TENANT",
    required=True,
    help="MONIT tenant name.",
)
@click.option(
    "--password",
    envvar="MONIT_PASSWORD",
    required=True,
    hide_input=True,
    help="MONIT tenant password.",
)
@click.option(
    "--service-name",
    envvar="OTEL_SERVICE_NAME",
    default="avtools",
    show_default=True,
    help="OpenTelemetry resource service.name.",
)
@click.option(
    "--otlp-ca-file",
    envvar="OTLP_CA_FILE",
    default=None,
    help=(
        "Optional CA bundle path for OTLP gRPC. "
        "Usually not needed on CERN hosts with system CAs."
    ),
)
@click.option(
    "--otlp-insecure/--otlp-tls",
    envvar="MONIT_OTLP_INSECURE",
    default=False,
    show_default=True,
    help=(
        "Use plaintext OTLP/gRPC (no TLS). "
        "Required for endpoints that do not speak TLS (e.g. monit-otlp.cern.ch:4316)."
    ),
)
@click.option(
    "--environment",
    envvar="AVTOOLS_ENVIRONMENT",
    default="prod",
    show_default=True,
    help=(
        "Deployment environment label attached to every Prometheus metric. "
        "Use 'qa' for the QA pipeline and 'prod' for production."
    ),
)
@click.option(
    "--hostgroup",
    envvar="AVTOOLS_HOSTGROUP",
    default="itdcim/av",
    show_default=True,
    help="Full Puppet hostgroup path (submitter_hostgroup label, e.g. 'itdcim/av').",
)
@click.option(
    "--availability-zone",
    envvar="AVTOOLS_AVAILABILITY_ZONE",
    default="cern-geneva-b",
    show_default=True,
    help="CERN compute availability zone (availability_zone label).",
)
@click.option(
    "--shard-index",
    envvar="JOB_COMPLETION_INDEX",
    default=0,
    show_default=True,
    type=int,
    help=(
        "This pod's shard number (0-based). On Kubernetes Indexed Jobs this is "
        "set automatically from the JOB_COMPLETION_INDEX env var."
    ),
)
@click.option(
    "--shard-total",
    envvar="SHARD_TOTAL",
    default=1,
    show_default=True,
    type=int,
    help=(
        "Total number of shards N. MUST equal the Job's `completions`, or devices "
        "whose index is not covered are silently never polled. Default 1 = no "
        "sharding (unchanged single-process behaviour)."
    ),
)
@click.option(
    "--priority",
    envvar="AVTOOLS_PRIORITY",
    type=click.Choice(["all", "critical", "high", "medium", "low"]),
    default="all",
    show_default=True,
    help=(
        "Publish-time priority tier filter. 'all' (default) publishes every metric "
        "(today's behaviour). A tier (critical/high/medium/low) publishes only that "
        "tier's device metrics plus the ALWAYS cycle guardrails; tiers are EXACT, "
        "not cumulative. Collection is unchanged — this filters at publish time so "
        "separate CronJobs can emit different tiers at different cadences."
    ),
)
@click.pass_context
def snmp_timeseries(
    ctx: click.Context,
    threads: int,
    otlp_endpoint: str,
    tenant: str,
    password: str,
    service_name: str,
    otlp_ca_file: str | None,
    otlp_insecure: bool,
    environment: str,
    hostgroup: str,
    availability_zone: str,
    shard_index: int,
    shard_total: int,
    priority: str,
) -> None:
    # Fail fast on a misconfigured partition rather than silently leaving a gap:
    # a wrong N (shard_total) or an out-of-range index means some devices are
    # never polled with no error anywhere. Refuse to start instead.
    if shard_total < 1:
        raise click.BadParameter("--shard-total must be >= 1", param_hint="--shard-total")
    if not (0 <= shard_index < shard_total):
        raise click.BadParameter(
            f"--shard-index {shard_index} is out of range [0, {shard_total})",
            param_hint="--shard-index",
        )

    bind_envelope("avtools", environment, hostgroup)
    dbod_url = ctx.obj["dbod_url"]
    tools = AVTools(dbod_url)
    tools.run_snmp_timeseries(
        otlp_endpoint=otlp_endpoint,
        monit_tenant=tenant,
        monit_password=password,
        max_workers=threads,
        service_name=service_name,
        otlp_ca_file=otlp_ca_file,
        otlp_insecure=otlp_insecure,
        submitter_environment=environment,
        submitter_hostgroup=hostgroup,
        availability_zone=availability_zone,
        shard_index=shard_index,
        shard_total=shard_total,
        priority=priority,
    )


# -----------------------------------------------------------------------------
# get-token (standalone entry point)
# -----------------------------------------------------------------------------


@click.command(
    "get-token",
    help="Fetch an OAuth2 access token via client_credentials and print it.",
)
@click.option(
    "--token-url",
    envvar="OAUTH_TOKEN_URL",
    required=True,
    help="OAuth2 token endpoint URL.",
)
@click.option(
    "--client-id",
    envvar="LANDB_CLIENT_ID",
    required=True,
    help="OAuth2 client id.",
)
@click.option(
    "--client-secret",
    envvar="LANDB_CLIENT_SECRET",
    required=True,
    help="OAuth2 client secret.",
)
@click.option(
    "--audience",
    envvar="LANDB_AUDIENCE",
    default=None,
    help="Optional Auth0 audience (if required by your provider).",
)
@click.option(
    "--scope",
    envvar="OAUTH_SCOPE",
    default=None,
    help="Optional OAuth2 scope.",
)
def get_token(
    token_url: str,
    client_id: str,
    client_secret: str,
    audience: str | None,
    scope: str | None,
) -> None:
    data: dict[str, str] = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if audience:
        data["audience"] = audience
    if scope:
        data["scope"] = scope

    try:
        resp = requests.post(token_url, data=data, timeout=30)
    except requests.RequestException as exc:
        raise click.ClickException(f"Token request failed: {exc}")

    if resp.status_code >= 400:
        raise click.ClickException(
            f"Token request failed ({resp.status_code}): {resp.text.strip()}"
        )

    try:
        payload = resp.json()
    except ValueError:
        raise click.ClickException(f"Token endpoint did not return JSON: {resp.text}")

    token = payload.get("access_token")
    if not token:
        raise click.ClickException(f"Missing access_token in response: {payload}")

    click.echo(token)


if __name__ == "__main__":
    cli()

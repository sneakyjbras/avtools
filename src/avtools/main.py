from __future__ import annotations

import logging

import click
import requests
import structlog

from avtools.core.av_tools import AVTools
from avtools.exception.errors import NoRecordsFound

logger = structlog.get_logger(__name__)


def _configure_logging(logs: bool) -> None:
    log_level = logging.DEBUG if logs else logging.INFO

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
    )
    logging.basicConfig(level=log_level)
    logger.info("Logging configured", log_level=log_level)


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
@click.pass_context
def cli(ctx: click.Context, logs: bool, dbod_url: str) -> None:
    ctx.obj = {"logs": logs, "dbod_url": dbod_url}
    _configure_logging(logs)


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
@click.pass_context
def run_eam(ctx: click.Context, username: str, password: str) -> None:
    dbod_url = ctx.obj["dbod_url"]
    try:
        tools = AVTools(dbod_url)
        tools.run_eam(username=username, password=password)
    except NoRecordsFound as exc:
        raise click.ClickException(str(exc))


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
@click.pass_context
def run_landb(
    ctx: click.Context,
    client_id: str,
    client_secret: str,
    audience: str,
) -> None:
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
) -> None:
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

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from avtools.av_tools import AVTools
from avtools.errors import NoRecordsFound
from avtools.logger import system_logger
from avtools.snmp_helper import SNMPHelper
from avtools.ts_helper import TimeSeriesHelper


def _load_landb_token(ctx: click.Context, param: click.Parameter, value: str) -> str:
    """
    Click callback to load the LanDB API token from a file path.

    Reads and returns the token string, raising a ClickException on errors.

    Args:
        ctx (click.Context): Click context.
        param (click.Parameter): Parameter metadata.
        value (str): File path to the token file.

    Returns:
        str: The trimmed token string.
    """
    token_path = Path(value)
    if not token_path.exists():
        raise click.ClickException(f"LanDB token file not found: {value}")
    token = token_path.read_text().strip()
    if not token:
        raise click.ClickException(f"LanDB token file is empty: {value}")
    return token


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
def cli(
    ctx: click.Context,
    logs: bool,
    dbod_url: str,
) -> None:
    """
    Root entry point that configures logging and passes context.

    Args:
        ctx (click.Context): Context object for passing parameters.
        logs (bool): Flag to enable debug logging.
        dbod_url (str): Database URL for DBoD operations.
    """
    ctx.obj = {"logs": logs, "dbod_url": dbod_url}
    system_logger.configure(log_level="DEBUG" if logs else "INFO")


# ---------------------------------------------------------------------------- #
# run-eam                                                                     #
# ---------------------------------------------------------------------------- #
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
def run_eam(
    ctx: click.Context,
    username: str,
    password: str,
) -> None:
    """
    CLI command to synchronize EAM devices with the database.

    Args:
        ctx (click.Context): Context with shared options.
        username (str): EAM API username.
        password (str): EAM API password.
    """
    av = AVTools(dbod_url=ctx.obj["dbod_url"], logs=ctx.obj["logs"])
    try:
        av.run_eam(username, password)
        click.echo("EAM CRUD operation completed successfully.")
    except NoRecordsFound as exc:
        click.echo(f"Error: {exc}")


# ---------------------------------------------------------------------------- #
# run-landb                                                                   #
# ---------------------------------------------------------------------------- #
@cli.command("run-landb", help="Run LanDB CRUD operations.")
@click.option(
    "--client-id",
    "client_id",
    required=True,
    help="Auth0 Client ID.",
)
@click.option(
    "--client-secret",
    "client_secret",
    required=True,
    help="Auth0 Client Secret.",
)
@click.option(
    "--audience",
    "audience",
    required=True,
    help="Auth0 audience (API identifier).",
)
@click.option(
    "--dbod-url",
    envvar="DATABASE_URL",
    required=True,
    help="DBoD PostgreSQL URL (env DATABASE_URL).",
)
@click.option(
    "--threads",
    "-t",
    envvar="THREADS",
    default=8,
    type=int,
    show_default=True,
    help="Number of threads for concurrent LanDB API requests.",
)
@click.pass_context
def run_landb(
    ctx: click.Context,
    client_id: str,
    client_secret: str,
    audience: str,
    dbod_url: str,
    threads: int,
) -> None:
    """
    CLI command to synchronize LanDB devices with the database.

    Args:
        ctx (click.Context): Context with shared options.
        client_id (str): Auth0 Client ID.
        client_secret (str): Auth0 Client Secret.
        audience (str): Auth0 audience.
        dbod_url (str): Database URL for DBoD operations.
    """
    av = AVTools(dbod_url=dbod_url, logs=ctx.obj["logs"])
    try:
        av.run_landb(
            client_id=client_id,
            client_secret=client_secret,
            audience=audience,
            concurrency=threads,
        )
        click.echo("LanDB CRUD operation completed successfully.")
    except NoRecordsFound as exc:
        click.echo(f"Error: {exc}")


# ---------------------------------------------------------------------------- #
# snmp-influx                                                                  #
# ---------------------------------------------------------------------------- #
@cli.command("snmp-influx", help="Query SNMP data and write it to InfluxDB 1.8.")
@click.option(
    "--influx-host",
    envvar="INFLUX_HOST",
    required=True,
    help="InfluxDB 1.8 host URL.",
)
@click.option(
    "--influx-port",
    envvar="INFLUX_PORT",
    default=8086,
    type=int,
    show_default=True,
    help="InfluxDB 1.8 port.",
)
@click.option(
    "--influx-user",
    envvar="INFLUX_USER",
    required=True,
    help="InfluxDB username.",
)
@click.option(
    "--influx-password",
    envvar="INFLUX_PASSWORD",
    required=True,
    hide_input=True,
    help="InfluxDB password.",
)
@click.option(
    "--influx-db",
    envvar="INFLUX_DB",
    required=True,
    help="InfluxDB database name.",
)
@click.option(
    "--dbod-url",
    envvar="DATABASE_URL",
    required=True,
    help="DBoD PostgreSQL URL (env DATABASE_URL).",
)
@click.option(
    "--threads",
    "-t",
    envvar="THREADS",
    default=8,
    type=int,
    show_default=True,
    help="Number of concurrent ping/SNMP worker threads.",
)
@click.pass_context
def snmp_influx(
    ctx: click.Context,
    influx_host: str,
    influx_port: int,
    influx_user: str,
    influx_password: str,
    influx_db: str,
    dbod_url: str,
    threads: int,
) -> None:
    """
    Synchronize SNMP and ping metrics with InfluxDB.

    Fetches cached LanDBDevice entries, runs up to `threads` parallel
    ICMP ping checks and SNMP queries, then writes all collected points
    to InfluxDB.
    """
    av = AVTools(dbod_url=dbod_url, logs=ctx.obj["logs"])
    av.run_influx_snmp(
        influx_host=influx_host,
        influx_port=influx_port,
        influx_user=influx_user,
        influx_password=influx_password,
        influx_db=influx_db,
        concurrency=threads,
    )


# ---------------------------------------------------------------------------- #
# Entry point                                                                 #
# ---------------------------------------------------------------------------- #
if __name__ == "__main__":
    cli()

"""OTLP endpoint forms and the gRPC ``host:port`` -> OTLP/HTTP URL migration.

The OTLP/gRPC exporter takes a bare ``host:port`` target; the OTLP/HTTP exporter
takes a full signal URL (``https://host:port/v1/metrics``).  Every deployed chart
still sets ``MONIT_OTLP_ENDPOINT=monit-otlp.cern.ch:4316`` (the gRPC form), and
the charts live in a different repository, so this module accepts the OLD form
and DERIVES the new one instead of hard-failing on it.  A hard failure here
would take the production metrics pipeline down the moment the image is rolled,
which is exactly the regression we must not ship.

Port mapping used when migrating a bare ``host:port`` target, measured live
against ``monit-otlp.cern.ch``:

    ==========  ================  ==================================
    Given port  Protocol          Migrated to
    ==========  ================  ==================================
    4316, 4317  OTLP/gRPC         ``https://host:4319/v1/<signal>``
    4318        OTLP/HTTP plain   ``http://host:4318/v1/<signal>``
    4319        OTLP/HTTP TLS     ``https://host:4319/v1/<signal>``
    other       unknown           ``https://host:<port>/v1/<signal>``
    ==========  ================  ==================================

4318 keeps its ``http://`` scheme because that port is plaintext-only; silently
promoting it to ``https://`` would break a deliberate local-collector setup with
a confusing TLS error rather than an honest one.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

# MONIT's OTLP/HTTP ports. 4319 terminates TLS (TLSv1.3); 4318 is plaintext.
OTLP_HTTPS_PORT = 4319
OTLP_HTTP_PLAINTEXT_PORT = 4318

# The OTLP/gRPC ports. These do NOT speak TLS at all — the server closes the
# connection during the TLS handshake — which is why anything pointed at them
# had to run in cleartext.
OTLP_GRPC_PORTS = frozenset({4316, 4317})

MONIT_OTLP_HOST = "monit-otlp.cern.ch"

DEFAULT_OTLP_HTTP_METRICS_ENDPOINT = f"https://{MONIT_OTLP_HOST}:{OTLP_HTTPS_PORT}/v1/metrics"
DEFAULT_OTLP_HTTP_LOGS_ENDPOINT = f"https://{MONIT_OTLP_HOST}:{OTLP_HTTPS_PORT}/v1/logs"

# The legacy default that the deployed charts still pass in.
DEFAULT_OTLP_GRPC_ENDPOINT = f"{MONIT_OTLP_HOST}:4316"

_SIGNAL_PATHS = {"metrics": "/v1/metrics", "logs": "/v1/logs", "traces": "/v1/traces"}


class OTLPEndpointError(ValueError):
    """Raised when an OTLP endpoint cannot be understood at all."""


def normalize_metrics_endpoint(endpoint: str) -> str:
    """Return a full OTLP/HTTP **metrics** URL for any accepted endpoint form."""
    return normalize_http_endpoint(endpoint, signal="metrics")


def normalize_logs_endpoint(endpoint: str) -> str:
    """Return a full OTLP/HTTP **logs** URL for any accepted endpoint form."""
    return normalize_http_endpoint(endpoint, signal="logs")


def normalize_http_endpoint(endpoint: str, *, signal: str) -> str:
    """Coerce any accepted endpoint form into a full OTLP/HTTP signal URL.

    Accepted inputs, in the order they are recognised:

    1. A full URL that already names the signal
       (``https://host:4319/v1/metrics``) — returned unchanged.
    2. A base URL with no signal path (``https://host:4319``) — the
       ``/v1/<signal>`` suffix is appended.
    3. The legacy OTLP/gRPC target (``monit-otlp.cern.ch:4316``) — migrated per
       the port table in the module docstring.
    4. A bare host with no port — assumed to be MONIT's TLS OTLP/HTTP port.

    Args:
        endpoint: Endpoint in any of the forms above.
        signal: OTLP signal name; one of ``metrics``, ``logs``, ``traces``.

    Returns:
        A full ``http(s)://host:port/v1/<signal>`` URL.

    Raises:
        OTLPEndpointError: If ``endpoint`` is empty, the signal is unknown, or
            the value cannot be parsed as a host (with an actionable message —
            operators read this straight out of a CronJob log).
    """
    signal_path = _SIGNAL_PATHS.get(signal)
    if signal_path is None:
        raise OTLPEndpointError(
            f"Unknown OTLP signal {signal!r}; expected one of {sorted(_SIGNAL_PATHS)}."
        )

    raw = (endpoint or "").strip()
    if not raw:
        raise OTLPEndpointError(
            "OTLP endpoint is empty. Set MONIT_OTLP_ENDPOINT (or --otlp-endpoint) to "
            f"{DEFAULT_OTLP_HTTP_METRICS_ENDPOINT!r} for OTLP/HTTP, or to "
            f"{DEFAULT_OTLP_GRPC_ENDPOINT!r} for the deprecated plaintext OTLP/gRPC path."
        )

    if raw.startswith(("http://", "https://")):
        return _with_signal_path(raw, signal_path)

    if "://" in raw:
        scheme = raw.split("://", 1)[0]
        raise OTLPEndpointError(
            f"OTLP endpoint {raw!r} uses an unsupported scheme {scheme!r}. "
            "Use an http:// or https:// URL, or the bare 'host:port' gRPC form."
        )

    host, port = _split_host_port(raw)
    scheme, port = _migrate_grpc_port(port)
    return urlunsplit((scheme, f"{host}:{port}", signal_path, "", ""))


def is_http_url(endpoint: str) -> bool:
    """Whether ``endpoint`` is already a full OTLP/HTTP URL."""
    return (endpoint or "").strip().startswith(("http://", "https://"))


def grpc_target(endpoint: str) -> str:
    """Return the bare ``host:port`` target the OTLP/gRPC exporter expects.

    The gRPC escape hatch must keep working even if an operator has already
    migrated ``MONIT_OTLP_ENDPOINT`` to the URL form, so a URL is reduced back to
    its authority. A URL that names an OTLP/HTTP port is left alone rather than
    guessed at: pointing gRPC at 4319 fails loudly, which beats silently
    rewriting an operator's explicit endpoint.

    Args:
        endpoint: Endpoint in either the URL or the ``host:port`` form.

    Returns:
        A ``host:port`` string.

    Raises:
        OTLPEndpointError: If ``endpoint`` is empty or has no host.
    """
    raw = (endpoint or "").strip()
    if not raw:
        raise OTLPEndpointError(
            "OTLP endpoint is empty. Set MONIT_OTLP_ENDPOINT (or --otlp-endpoint) to "
            f"{DEFAULT_OTLP_GRPC_ENDPOINT!r} for the deprecated plaintext OTLP/gRPC path."
        )
    if is_http_url(raw):
        parts = urlsplit(raw)
        if not parts.netloc:
            raise OTLPEndpointError(f"OTLP endpoint {raw!r} has no host.")
        return parts.netloc
    host, port = _split_host_port(raw)
    return f"{host}:{port}"


def _with_signal_path(url: str, signal_path: str) -> str:
    """Append ``/v1/<signal>`` to a URL that does not already carry a path."""
    parts = urlsplit(url)
    if not parts.hostname:
        raise OTLPEndpointError(
            f"OTLP endpoint {url!r} has no host. Expected e.g. "
            f"{DEFAULT_OTLP_HTTP_METRICS_ENDPOINT!r}."
        )
    path = parts.path.rstrip("/")
    if not path:
        path = signal_path
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _split_host_port(raw: str) -> tuple[str, int]:
    """Split a bare ``host:port`` target, defaulting to MONIT's TLS OTLP/HTTP port."""
    target = raw.rstrip("/")
    if ":" not in target:
        if not target:
            raise OTLPEndpointError(f"OTLP endpoint {raw!r} has no host.")
        return target, OTLP_HTTPS_PORT

    host, _, port_text = target.rpartition(":")
    if not host:
        raise OTLPEndpointError(
            f"OTLP endpoint {raw!r} has no host. Expected 'host:port', e.g. "
            f"{DEFAULT_OTLP_GRPC_ENDPOINT!r}."
        )
    try:
        port = int(port_text)
    except ValueError:
        raise OTLPEndpointError(
            f"OTLP endpoint {raw!r} has a non-numeric port {port_text!r}. Expected "
            f"'host:port' (e.g. {DEFAULT_OTLP_GRPC_ENDPOINT!r}) or a full URL "
            f"(e.g. {DEFAULT_OTLP_HTTP_METRICS_ENDPOINT!r})."
        ) from None
    return host, port


def _migrate_grpc_port(port: int) -> tuple[str, int]:
    """Map a bare target's port onto an OTLP/HTTP (scheme, port) pair."""
    if port in OTLP_GRPC_PORTS:
        # The gRPC ports have no TLS listener at all, so there is nothing to
        # preserve: move to the TLS OTLP/HTTP port.
        return "https", OTLP_HTTPS_PORT
    if port == OTLP_HTTP_PLAINTEXT_PORT:
        return "http", OTLP_HTTP_PLAINTEXT_PORT
    return "https", port

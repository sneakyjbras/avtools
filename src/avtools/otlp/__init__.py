"""Shared OTLP/HTTP transport for AV Tools (metrics today, logs next).

Everything AV Tools sends to MONIT goes over OTLP.  Until now that meant
OTLP/**gRPC** on ``monit-otlp.cern.ch:4316`` with ``insecure=True`` — i.e. CERN
equipment numbers and device IPs for ~1375 devices travelling in **cleartext**.
That was not a misconfiguration: the MONIT gRPC ports do not offer TLS at all
(the server closes the connection on ClientHello), so plaintext was the only
thing that worked there.

MONIT *does* offer TLS on the OTLP/**HTTP** port ``4319``, so this package moves
the wire onto ``https://monit-otlp.cern.ch:4319/v1/{metrics,logs}`` with HTTP
Basic auth and a pinned CA chain.  See :mod:`avtools.otlp.http_transport` for the
CA story and :mod:`avtools.otlp.endpoints` for the endpoint migration rules.
"""

from __future__ import annotations

from avtools.otlp.endpoints import (
    DEFAULT_OTLP_HTTP_LOGS_ENDPOINT,
    DEFAULT_OTLP_HTTP_METRICS_ENDPOINT,
    OTLP_HTTPS_PORT,
    normalize_logs_endpoint,
    normalize_metrics_endpoint,
)
from avtools.otlp.http_transport import (
    OTLPHttpTransport,
    OTLPResponse,
    OTLPTransportError,
    default_ca_file,
    resolve_ca_file,
)

__all__ = [
    "DEFAULT_OTLP_HTTP_LOGS_ENDPOINT",
    "DEFAULT_OTLP_HTTP_METRICS_ENDPOINT",
    "OTLPHttpTransport",
    "OTLPResponse",
    "OTLPTransportError",
    "OTLP_HTTPS_PORT",
    "default_ca_file",
    "normalize_logs_endpoint",
    "normalize_metrics_endpoint",
    "resolve_ca_file",
]

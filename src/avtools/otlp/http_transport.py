"""HTTP transport shared by every OTLP signal AV Tools exports.

Why this exists
---------------
AV Tools used to ship metrics over OTLP/gRPC to ``monit-otlp.cern.ch:4316`` with
``MONIT_OTLP_INSECURE=true``. That is cleartext on the wire, carrying CERN
equipment numbers and device IPs for ~1375 devices. The gRPC ports offer no TLS
listener at all, so the only way to encrypt the export is to move to OTLP/HTTP on
port 4319, which does terminate TLS (TLSv1.3, verified live).

The CA story (this is the part that bites)
------------------------------------------
``monit-otlp.cern.ch:4319`` presents its leaf certificate but does **not** send
the ``CERN Grid Certification Authority`` intermediate. A client therefore cannot
build a chain from the leaf to a trusted root on its own, and verification fails
with every "obvious" trust store:

* the Debian default bundle in ``python:3.11-slim`` — FAILS,
* ``CERN Root Certification Authority 2`` alone — FAILS,
* ``/etc/pki/tls/certs/ca-bundle.crt`` — does not exist in that image.

The client must supply the **full chain** itself. ``avtools/certs/cern-otlp-chain.pem``
holds exactly the two certificates needed (``CERN Grid Certification Authority``
followed by ``CERN Root Certification Authority 2``) and is shipped inside the
wheel, so it resolves identically in the container image, in the Puppet monolith
install, and in a dev checkout. Operators can still override it with
``--otlp-ca-file`` / ``OTLP_CA_FILE`` (e.g. to mount a rotated chain from a
ConfigMap) — but there is deliberately **no** way to disable verification.

Confirming the export
---------------------
``MeterProvider.force_flush()`` cannot be trusted: it has been observed returning
``True`` while the exporter logged ``Failed to export ... UNAVAILABLE``. This
transport therefore derives success from nothing but the **HTTP response status**
returned by MONIT.
"""

from __future__ import annotations

import base64
import math
import os
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

import structlog

_log = structlog.get_logger(__name__)

# OTLP/HTTP payload encodings MONIT accepts. Both were verified against
# :4319/v1/metrics and :4319/v1/logs; see CONTENT_TYPE_PROTOBUF for the default.
CONTENT_TYPE_PROTOBUF = "application/x-protobuf"
CONTENT_TYPE_JSON = "application/json"

# Retry budget defaults. These mirror the metrics publisher's historical flush
# budget (3 attempts, 0.75s * attempt backoff, 10s per call) so migrating the
# publisher onto this transport does not change its timing envelope.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_S = 0.75
DEFAULT_TIMEOUT_S = 10.0

# The CA chain shipped inside the wheel. See the module docstring.
_BUNDLED_CA_RELPATH = ("certs", "cern-otlp-chain.pem")

# Statuses worth another attempt. Anything else (401 bad credentials, 400 bad
# payload, 404 wrong signal path) can never succeed on retry, so we fail fast and
# surface the real error instead of burning the backoff budget on it.
_RETRYABLE_STATUSES = frozenset({408, 425, 429})

_MAX_BODY_SNIPPET = 512


class OTLPTransportError(RuntimeError):
    """Raised when an OTLP/HTTP export does not come back with a 2xx status."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = True) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class OTLPResponse:
    """The outcome of one OTLP/HTTP POST, as reported by MONIT itself."""

    status: int
    body: bytes

    @property
    def ok(self) -> bool:
        """Whether MONIT accepted the payload (2xx)."""
        return 200 <= self.status < 300


def default_ca_file() -> str | None:
    """Return the path of the CA chain bundled with the package, if present.

    Returns:
        Absolute path to ``avtools/certs/cern-otlp-chain.pem``, or ``None`` when
        the package was installed without its data files.
    """
    candidate = Path(__file__).resolve().parent.parent.joinpath(*_BUNDLED_CA_RELPATH)
    return str(candidate) if candidate.is_file() else None


def resolve_ca_file(ca_file: str | None) -> str | None:
    """Pick the CA bundle to verify MONIT's OTLP/HTTP certificate with.

    Resolution order:

    1. an explicit ``ca_file`` (``--otlp-ca-file`` / ``OTLP_CA_FILE``),
    2. the chain bundled in the wheel,
    3. ``None`` — the system trust store, which is known to FAIL against MONIT
       because the server omits its intermediate. That failure is left visible on
       purpose; it is never downgraded to an unverified connection.

    Args:
        ca_file: Operator-supplied CA bundle path, if any.

    Returns:
        A CA bundle path, or ``None`` to fall back to the system trust store.
    """
    explicit = (ca_file or "").strip()
    if explicit:
        return explicit
    return default_ca_file()


def basic_auth_header(tenant: str, password: str) -> str:
    """Build the ``Authorization: Basic ...`` value from MONIT credentials."""
    token = base64.b64encode(f"{tenant}:{password}".encode()).decode()
    return f"Basic {token}"


class OTLPHttpTransport:
    """POST serialized OTLP payloads to a MONIT OTLP/HTTP signal endpoint.

    One instance targets one signal URL (``.../v1/metrics`` or ``.../v1/logs``);
    the class itself is signal-agnostic, which is the point — the logs publisher
    lands on top of it next.

    Args:
        endpoint: Full OTLP/HTTP signal URL.
        tenant: MONIT tenant (HTTP Basic username).
        password: MONIT tenant password (HTTP Basic password).
        ca_file: CA bundle path; see :func:`resolve_ca_file` for the fallbacks.
        timeout_s: Per-request timeout in seconds.
        max_attempts: Attempts used by :meth:`post_with_retry` (>= 1).
        backoff_s: Base backoff; attempt *n* sleeps ``backoff_s * n``.
        tenant_id_header: Optional ``x-scope-orgid``-style tenant header value.
            MONIT accepts Basic auth alone, so this stays unset by default.
        sleep: Injectable sleep, so tests do not pay the real backoff.

    Raises:
        OTLPTransportError: If any constructor argument is unusable, or the CA
            bundle cannot be loaded into an SSL context.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        tenant: str,
        password: str,
        ca_file: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_s: float = DEFAULT_BACKOFF_S,
        tenant_id_header: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.endpoint = _require_non_empty("endpoint", endpoint)
        if not self.endpoint.startswith(("http://", "https://")):
            raise OTLPTransportError(
                f"OTLP endpoint {self.endpoint!r} must be an http:// or https:// URL. "
                "Bare 'host:port' targets are the OTLP/gRPC form — run them through "
                "avtools.otlp.endpoints.normalize_http_endpoint() first."
            )
        if not _is_positive_finite(timeout_s):
            raise OTLPTransportError("OTLP timeout_s must be a finite number > 0.")
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise OTLPTransportError("OTLP max_attempts must be an integer >= 1.")
        if not isinstance(backoff_s, (int, float)) or backoff_s < 0 or not math.isfinite(backoff_s):
            raise OTLPTransportError("OTLP backoff_s must be a finite number >= 0.")

        self.timeout_s = float(timeout_s)
        self.max_attempts = int(max_attempts)
        self.backoff_s = float(backoff_s)
        self._sleep = sleep

        tenant_value = _require_non_empty("tenant", tenant)
        if not isinstance(password, str) or not password:
            raise OTLPTransportError("OTLP password is required.")

        self._base_headers: dict[str, str] = {
            "Authorization": basic_auth_header(tenant_value, password),
        }
        if tenant_id_header:
            self._base_headers["x-scope-orgid"] = tenant_id_header

        self.ca_file = resolve_ca_file(ca_file)
        self._ssl_context = _build_ssl_context(self.ca_file) if self.is_tls else None

    @property
    def is_tls(self) -> bool:
        """Whether this transport talks TLS (``https://``)."""
        return self.endpoint.startswith("https://")

    def headers(self, content_type: str) -> dict[str, str]:
        """Return the full header set for one request."""
        return {**self._base_headers, "Content-Type": content_type}

    def post(self, payload: bytes, *, content_type: str = CONTENT_TYPE_PROTOBUF) -> OTLPResponse:
        """POST one serialized OTLP payload and validate MONIT's response.

        Success is decided by the HTTP status code and nothing else.

        Args:
            payload: Serialized OTLP request body.
            content_type: ``application/x-protobuf`` or ``application/json``.

        Returns:
            The 2xx :class:`OTLPResponse` MONIT returned.

        Raises:
            OTLPTransportError: On any non-2xx status, timeout, TLS failure or
                connection error. ``retryable`` says whether another attempt
                could plausibly succeed.
        """
        request = urllib_request.Request(
            self.endpoint,
            data=payload,
            headers=self.headers(content_type),
            method="POST",
        )
        try:
            with _urlopen(request, timeout=self.timeout_s, context=self._ssl_context) as response:
                status = _response_status(response)
                body = _safe_read(response, _MAX_BODY_SNIPPET)
        except urllib_error.HTTPError as exc:
            status = int(exc.code)
            raise OTLPTransportError(
                f"OTLP/HTTP export to {self.endpoint} failed with status {status}: "
                f"{_body_snippet(_safe_read(exc, _MAX_BODY_SNIPPET))}",
                status=status,
                retryable=_status_is_retryable(status),
            ) from exc
        except ssl.SSLError as exc:
            raise OTLPTransportError(
                f"OTLP/HTTP export to {self.endpoint} failed TLS verification: {exc}. "
                f"CA bundle in use: {self.ca_file or '<system trust store>'}. MONIT does "
                "not send its intermediate, so the client must supply the full CERN "
                "chain — point --otlp-ca-file / OTLP_CA_FILE at a PEM containing "
                "'CERN Grid Certification Authority' AND 'CERN Root Certification "
                "Authority 2'.",
                retryable=False,
            ) from exc
        except urllib_error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLError):
                raise OTLPTransportError(
                    f"OTLP/HTTP export to {self.endpoint} failed TLS verification: {reason}. "
                    f"CA bundle in use: {self.ca_file or '<system trust store>'}. MONIT does "
                    "not send its intermediate, so the client must supply the full CERN "
                    "chain — point --otlp-ca-file / OTLP_CA_FILE at a PEM containing "
                    "'CERN Grid Certification Authority' AND 'CERN Root Certification "
                    "Authority 2'.",
                    retryable=False,
                ) from exc
            raise OTLPTransportError(
                f"OTLP/HTTP export to {self.endpoint} failed: {reason}"
            ) from exc
        except TimeoutError as exc:
            raise OTLPTransportError(
                f"OTLP/HTTP export to {self.endpoint} timed out after {self.timeout_s}s."
            ) from exc

        response_obj = OTLPResponse(status=status, body=body)
        if not response_obj.ok:
            raise OTLPTransportError(
                f"OTLP/HTTP export to {self.endpoint} failed with status {status}: "
                f"{_body_snippet(body)}",
                status=status,
                retryable=_status_is_retryable(status),
            )
        return response_obj

    def post_with_retry(
        self, payload: bytes, *, content_type: str = CONTENT_TYPE_PROTOBUF
    ) -> tuple[bool, Optional[Exception]]:
        """POST with a bounded retry budget, reporting the outcome instead of raising.

        Mirrors the metrics publisher's historical flush loop: up to
        ``max_attempts`` tries with ``backoff_s * attempt`` between them. A
        non-retryable failure (bad credentials, bad payload, TLS misconfiguration)
        stops immediately — retrying it can only delay the job while producing the
        same error.

        Args:
            payload: Serialized OTLP request body.
            content_type: ``application/x-protobuf`` or ``application/json``.

        Returns:
            ``(confirmed, last_error)``. ``confirmed`` is ``True`` only when MONIT
            answered 2xx; it is never inferred from anything else.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                self.post(payload, content_type=content_type)
                return True, None
            except OTLPTransportError as exc:
                last_exc = exc
                if not exc.retryable:
                    _log.warning(
                        "otlp_http_export_not_retryable",
                        endpoint=self.endpoint,
                        status=exc.status,
                        attempt=attempt,
                        error=str(exc),
                    )
                    return False, exc
            except Exception as exc:  # defensive: never let a transport bug crash a cycle
                last_exc = exc
            if attempt < self.max_attempts:
                self._sleep(self.backoff_s * attempt)
        return False, last_exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_ssl_context(ca_file: str | None) -> ssl.SSLContext:
    """Build a verifying SSL context, optionally anchored on a custom CA bundle.

    Verification is always on: there is no ``verify=False`` path here, by design.

    Args:
        ca_file: CA bundle path, or ``None`` for the system trust store.

    Returns:
        A verifying :class:`ssl.SSLContext`.

    Raises:
        OTLPTransportError: If the CA bundle is missing or unreadable — a silent
            fall back to the system store would look like a working TLS setup
            while trusting the wrong roots.
    """
    if ca_file is None:
        return ssl.create_default_context()
    if not os.path.isfile(ca_file):
        raise OTLPTransportError(
            f"OTLP CA bundle {ca_file!r} does not exist. Point --otlp-ca-file / "
            "OTLP_CA_FILE at a readable PEM, or unset it to use the bundled CERN chain."
        )
    try:
        return ssl.create_default_context(cafile=ca_file)
    except (OSError, ssl.SSLError) as exc:
        raise OTLPTransportError(f"OTLP CA bundle {ca_file!r} could not be loaded: {exc}") from exc


def _urlopen(
    request: urllib_request.Request,
    *,
    timeout: float,
    context: Optional[ssl.SSLContext],
):
    """Open an HTTP request, keeping a single injectable seam for tests."""
    if context is None:
        return urllib_request.urlopen(request, timeout=timeout)  # noqa: S310 - scheme validated
    return urllib_request.urlopen(request, timeout=timeout, context=context)  # noqa: S310


def _response_status(response: object) -> int:
    """Extract an integer HTTP status from urllib-like response objects."""
    status = getattr(response, "status", None)
    if status is not None:
        return int(status)
    getcode = getattr(response, "getcode", None)
    if callable(getcode):
        return int(getcode())
    code = getattr(response, "code", None)
    if code is not None:
        return int(code)
    raise OTLPTransportError("OTLP/HTTP response did not expose a status code.")


def _safe_read(response: object, limit: int) -> bytes:
    """Read a bounded body without letting a read failure mask the real error."""
    try:
        return response.read(limit)
    except Exception:
        return b""


def _body_snippet(body: bytes) -> str:
    """Render a bounded response body for an operator-facing error message."""
    if not body:
        return "<empty response body>"
    return body.decode("utf-8", errors="replace").strip() or "<empty response body>"


def _status_is_retryable(status: int) -> bool:
    """Whether another attempt at this status could plausibly succeed."""
    return status >= 500 or status in _RETRYABLE_STATUSES


def _require_non_empty(field_name: str, value: str) -> str:
    """Validate and strip a required transport string setting."""
    if not isinstance(value, str) or not value.strip():
        raise OTLPTransportError(f"OTLP {field_name} is required.")
    return value.strip()


def _is_positive_finite(value: float) -> bool:
    """Return whether a numeric setting is finite and strictly positive."""
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0.0

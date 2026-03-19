from __future__ import annotations

from abc import ABC

from avtools.snmp.handlers.abstract_device_handler import AbstractDeviceHandler


# Common OID available on essentially all SNMP agents.
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"  # sysUpTime.0 (Timeticks)


def _as_int(value) -> int | None:
    try:
        return int(value)
    except Exception:
        try:
            s = str(value)
            digits = "".join(ch for ch in s if ch.isdigit())
            return int(digits) if digits else None
        except Exception:
            return None


class AbstractProjector(AbstractDeviceHandler, ABC):
    """Base class for SNMP projector handlers.

    A projector is still an SNMP device handler, but with a shared notion of
    what "projector stats" mean (uptime, firmware, lamp hours, power status).
    Concrete vendors/models only need to provide the relevant OIDs.
    """

    # Vendor-specific OIDs to be provided by subclasses.
    OID_FIRMWARE_VERSION: str
    OID_LAMP_HOURS: str
    OID_POWER_STATUS: str

    # Reasonable defaults for projectors.
    DEFAULT_TIMEOUT: float = 1.5
    DEFAULT_RETRIES: int = 1

    def fetch_stats(self) -> dict[str, object]:
        error_indication, error_status, _error_index, var_binds = self._snmp_get(
            [
                OID_SYS_UPTIME,
                self.OID_FIRMWARE_VERSION,
                self.OID_LAMP_HOURS,
                self.OID_POWER_STATUS,
            ],
            timeout=self.DEFAULT_TIMEOUT,
            retries=self.DEFAULT_RETRIES,
        )

        if error_indication or error_status:
            return {}

        values = [var_bind[1] for var_bind in var_binds]

        # sysUpTime is Timeticks (1/100s).
        ticks = _as_int(values[0])
        uptime_seconds = float(ticks) / 100.0 if ticks is not None else None

        firmware = str(values[1]).strip()
        lamp_hours = _as_int(values[2])
        power_status = str(values[3]).strip()

        out: dict[str, object] = {}
        if uptime_seconds is not None:
            out["uptime_seconds"] = uptime_seconds
        if firmware:
            out["firmware"] = firmware
        if lamp_hours is not None:
            out["lamp_hours"] = lamp_hours
        if power_status:
            out["power_status"] = power_status
        return out


class EpsonProjector(AbstractProjector):
    """Epson projector handler."""

    # Epson enterprise MIB OIDs.
    OID_FIRMWARE_VERSION = "1.3.6.1.4.1.1248.4.1.1.1.8.0"
    OID_LAMP_HOURS = "1.3.6.1.4.1.1248.4.1.1.1.1.0"
    OID_POWER_STATUS = "1.3.6.1.4.1.1248.4.1.1.1.9.0"


class SonyProjector(AbstractProjector):
    """Sony projector handler (placeholder).

    Sony OIDs vary by model/MIB. Fill these in once the target models and MIBs
    are known.
    """

    # NOTE: Intentionally blank until we confirm the correct Sony MIB.
    OID_FIRMWARE_VERSION = ""
    OID_LAMP_HOURS = ""
    OID_POWER_STATUS = ""

    def fetch_stats(self) -> dict[str, object]:  # type: ignore[override]
        raise NotImplementedError("Sony projector OIDs are not defined yet")

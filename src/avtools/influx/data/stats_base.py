"""Common SNMP stats structures used for Influx points."""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Tuple

from avtools.influx.parsable import InfluxParsable
from avtools.influx.serializable import InfluxSerializable


class StatsBase(InfluxSerializable, InfluxParsable):
    """
    Base class providing default InfluxDB and human-readable implementations.

    Subclasses must define:
      - ClassVar "measurement": name of the Influx measurement
      - ClassVar "tags": tuple of attribute names to treat as tags
    """

    measurement: ClassVar[str]
    tags: ClassVar[tuple[str, ...]] = ()

    def to_influx(self) -> dict[str, Any]:
        """Serialize the stats object into an InfluxDB point dict.

        Returns:
            Influx point dict with ``measurement``, ``tags``, and ``fields``.

        Notes:
            Attributes listed in the class-level ``tags`` tuple are emitted as tags.
            All other attributes are emitted as fields.
        """
        raw = vars(self)
        tag_dict = {k: str(raw[k]) for k in self.tags if k in raw}
        field_dict = {k: raw[k] for k in raw if k not in self.tags}
        return {
            "measurement": self.measurement,
            "tags": tag_dict,
            "fields": field_dict,
        }

    def to_human(self) -> dict[str, str]:
        """Render the stats object as a human-friendly mapping.

        Returns:
            Mapping where keys are title-cased field names and values are strings.
        """
        return {k.replace("_", " ").title(): str(v) for k, v in vars(self).items()}

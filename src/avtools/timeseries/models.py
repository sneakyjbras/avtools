from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class MetricSample:
    """A single metric sample to publish via OTLP.

    Notes:
      - Prometheus labels are represented as `labels`.
      - Values must be numeric (int/float).
      - For human-readable strings (e.g. firmware), publish an *_info metric
        with value=1 and the string as an extra label.
    """

    name: str
    value: float | int
    labels: Mapping[str, str]

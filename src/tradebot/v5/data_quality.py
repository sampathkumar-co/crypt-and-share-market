from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Iterable, Mapping


@dataclass(frozen=True)
class SourceObservation:
    source: str
    timestamp: datetime
    available_at: datetime
    value: float

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("source is required")
        if self.timestamp.tzinfo is None or self.available_at.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        if self.available_at < self.timestamp:
            raise ValueError("available_at cannot precede observation timestamp")
        if self.value <= 0:
            raise ValueError("value must be positive")


@dataclass(frozen=True)
class ReconciledPoint:
    timestamp: datetime
    value: float
    sources: tuple[str, ...]
    maximum_relative_deviation: float


def reconcile_sources(
    observations: Iterable[SourceObservation],
    *,
    decision_time: datetime,
    max_relative_deviation: float = 0.01,
    minimum_sources: int = 2,
) -> ReconciledPoint:
    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    if minimum_sources < 1:
        raise ValueError("minimum_sources must be positive")
    eligible = [item for item in observations if item.available_at <= decision_time]
    if len(eligible) < minimum_sources:
        raise ValueError("insufficient point-in-time sources")
    timestamps = {item.timestamp.astimezone(timezone.utc) for item in eligible}
    if len(timestamps) != 1:
        raise ValueError("source timestamps do not align")
    if len({item.source for item in eligible}) != len(eligible):
        raise ValueError("duplicate source")
    center = median(item.value for item in eligible)
    deviation = max(abs(item.value - center) / center for item in eligible)
    if deviation > max_relative_deviation:
        raise ValueError("source disagreement exceeds tolerance")
    return ReconciledPoint(
        timestamp=next(iter(timestamps)),
        value=center,
        sources=tuple(sorted(item.source for item in eligible)),
        maximum_relative_deviation=deviation,
    )


def validate_panel(panel: Mapping[str, Iterable[tuple[datetime, float]]]) -> None:
    if len(panel) < 2:
        raise ValueError("at least two assets are required")
    expected: tuple[datetime, ...] | None = None
    for asset, rows in panel.items():
        values = list(rows)
        if not values:
            raise ValueError(f"empty asset series: {asset}")
        timestamps = tuple(timestamp for timestamp, _ in values)
        if any(timestamp.tzinfo is None for timestamp in timestamps):
            raise ValueError("panel timestamps must be timezone-aware")
        if list(timestamps) != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
            raise ValueError(f"unordered or duplicate timestamps: {asset}")
        if any(value <= 0 for _, value in values):
            raise ValueError(f"non-positive price: {asset}")
        if expected is None:
            expected = timestamps
        elif timestamps != expected:
            raise ValueError("asset panel is not aligned")

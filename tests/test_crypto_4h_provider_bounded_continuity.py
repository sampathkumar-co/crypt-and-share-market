from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradebot.data import crypto_4h_provider_bounded_continuity as continuity
from tradebot.models import Candle


def hourly_grid(start: datetime, count: int) -> list[Candle]:
    return [
        Candle(
            timestamp=start + timedelta(hours=index),
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.5 + index,
            volume=10.0,
        )
        for index in range(count)
    ]


def test_isolated_gaps_use_previous_close_and_zero_volume() -> None:
    start = datetime(2025, 1, 1)
    raw = hourly_grid(start, 12)
    raw = [item for item in raw if item.timestamp not in {start + timedelta(hours=4), start + timedelta(hours=5)}]
    completed, synthetic, longest = continuity.apply_bounded_continuity(
        raw, start, start + timedelta(hours=12)
    )
    assert synthetic == [start + timedelta(hours=4), start + timedelta(hours=5)]
    assert longest == 2
    assert len(completed) == 12
    previous_close = completed[3].close
    for index in (4, 5):
        candle = completed[index]
        assert candle.open == previous_close
        assert candle.high == previous_close
        assert candle.low == previous_close
        assert candle.close == previous_close
        assert candle.volume == 0.0


def test_more_than_six_missing_hours_is_rejected() -> None:
    start = datetime(2025, 1, 1)
    raw = hourly_grid(start, 20)
    missing = {start + timedelta(hours=index) for index in range(5, 12)}
    raw = [item for item in raw if item.timestamp not in missing]
    with pytest.raises(continuity.BoundedContinuityError, match="exceed"):
        continuity.apply_bounded_continuity(raw, start, start + timedelta(hours=20))


def test_six_consecutive_missing_hours_is_the_frozen_maximum() -> None:
    start = datetime(2025, 1, 1)
    raw = hourly_grid(start, 20)
    missing = {start + timedelta(hours=index) for index in range(5, 11)}
    raw = [item for item in raw if item.timestamp not in missing]
    completed, synthetic, longest = continuity.apply_bounded_continuity(
        raw, start, start + timedelta(hours=20)
    )
    assert len(completed) == 20
    assert len(synthetic) == 6
    assert longest == 6


def test_missing_boundary_hour_is_rejected() -> None:
    start = datetime(2025, 1, 1)
    raw = hourly_grid(start, 12)[1:]
    with pytest.raises(continuity.BoundedContinuityError, match="first or final"):
        continuity.apply_bounded_continuity(raw, start, start + timedelta(hours=12))


def test_future_candle_cannot_change_synthetic_value() -> None:
    start = datetime(2025, 1, 1)
    raw = hourly_grid(start, 12)
    raw = [item for item in raw if item.timestamp != start + timedelta(hours=4)]
    first, _, _ = continuity.apply_bounded_continuity(raw, start, start + timedelta(hours=12))
    altered = list(raw)
    later = altered[4]
    altered[4] = Candle(
        timestamp=later.timestamp,
        open=1_000_000.0,
        high=1_000_001.0,
        low=999_999.0,
        close=1_000_000.5,
        volume=later.volume,
    )
    second, _, _ = continuity.apply_bounded_continuity(altered, start, start + timedelta(hours=12))
    assert second[4] == first[4]
